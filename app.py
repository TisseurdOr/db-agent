"""db-agent Streamlit 聊天界面。

用法:
    uv run streamlit run app.py

布局:
    主区 — 聊天对话
    侧边栏 — Router 分派 / 执行计划 / SQL / Reflection / Token 统计
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv

load_dotenv()

from harness.bootstrap import bootstrap_data
from harness.config import DEFAULT_MODEL
from harness.llm_client import get_anthropic_client
from harness.orchestration.multi.agent_names import (
    AGENT_ANALYSIS,
    AGENT_DATA_QUALITY,
    AGENT_HBASE,
    AGENT_HIVE,
    AGENT_SQL,
    AGENT_STRATEGY,
)
from harness.orchestration.multi.orchestrator import MultiAgentRunner

# ═══════════════════════════════════════════════════════════════════════
# 页面配置
# ═══════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="db-agent · AI 数据分析助手",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .stChatMessage { padding: 0.4rem 1rem; }
    .exec-panel small { color: #888; }
    .exec-panel code { font-size: 0.72rem; }
</style>
""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════
# 一次性初始化
# ═══════════════════════════════════════════════════════════════════════

@st.cache_resource
def init_system():
    bootstrap_data()
    return get_anthropic_client()

client = init_system()


# ═══════════════════════════════════════════════════════════════════════
# 会话状态
# ═══════════════════════════════════════════════════════════════════════

def _init_session():
    defaults = {
        "messages": [],
        "runner": None,
        "model": DEFAULT_MODEL,
        "total_tokens": {"input": 0, "output": 0},
        "total_latency": 0.0,
        "turn_count": 0,
        "last_exec_info": None,
        "pending_interrupt": None,  # HITL 等待审批状态
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_session()


def _get_loop():
    """获取持久化 event loop（aiosqlite 连接绑定到一个 loop）。"""
    if "event_loop" not in st.session_state:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        st.session_state.event_loop = loop
    return st.session_state.event_loop


async def _get_runner() -> MultiAgentRunner:
    if st.session_state.runner is None:
        runner = await MultiAgentRunner.create(
            client,
            model=st.session_state.model,
            enable_data_quality=False,  # 首次交互关 DQ，避免多一轮拖慢首答
            thread_id=f"ui-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        )
        st.session_state.runner = runner
    return st.session_state.runner


def _extract_exec_info(runner) -> dict:
    """从 Runner 的 _last_state 提取侧边栏展示信息。"""
    info = runner.get_execution_info()
    plan = info.get("plan", [])
    sql = info.get("sql", "")
    stats = info.get("stats", {})
    reflection = info.get("reflection", {})

    return {
        "plan": plan,
        "sql": sql,
        "reflection_attempts": reflection.get("attempts", 0),
        "total_tokens": stats.get("input_tokens", 0) + stats.get("output_tokens", 0),
        "nodes": stats.get("nodes", []),
    }


# ═══════════════════════════════════════════════════════════════════════
# 侧边栏
# ═══════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.title("🤖 db-agent")
    st.caption("AI + 数据治理 · 面试演示系统")

    st.divider()
    st.subheader("⚙️ 设置")

    new_model = st.selectbox(
        "模型", ["deepseek-chat", "deepseek-v4-flash", "deepseek-v4-pro"],
        index=0 if st.session_state.model == "deepseek-chat" else (
            1 if "flash" in st.session_state.model else 2
        ),
    )
    if new_model != st.session_state.model:
        st.session_state.model = new_model
        st.session_state.runner = None  # 模型变了，重建 runner

    user_role = st.selectbox("用户角色", ["viewer", "analyst", "manager", "dba"], index=0)
    if user_role:
        os.environ["AGENT_USER"] = user_role

    if st.button("🔄 新会话", use_container_width=True):
        st.session_state.messages = []
        st.session_state.runner = None
        st.session_state.total_tokens = {"input": 0, "output": 0}
        st.session_state.total_latency = 0.0
        st.session_state.turn_count = 0
        st.session_state.last_exec_info = None
        st.rerun()

    st.divider()
    st.subheader("📊 统计")

    col_a, col_b = st.columns(2)
    with col_a:
        st.metric("轮次", st.session_state.turn_count)
    tok = st.session_state.total_tokens
    with col_b:
        st.metric("Tokens", f"{tok['input'] + tok['output']:,}")

    if st.session_state.total_latency > 0:
        st.caption(f"总延迟: {st.session_state.total_latency:.1f}s")

    st.divider()
    st.subheader("🔍 最近执行")

    exec_info = st.session_state.last_exec_info
    if exec_info:
        plan = exec_info.get("plan", [])
        if plan:
            st.markdown("**执行计划:**")
            agent_emoji = {
                AGENT_SQL: "🗄️", AGENT_STRATEGY: "📋", AGENT_ANALYSIS: "📊",
                AGENT_HBASE: "🗃️", AGENT_HIVE: "🐝", AGENT_DATA_QUALITY: "🔬",
            }
            for step in plan:
                agent = step.get("agent", "?")
                emoji = agent_emoji.get(agent, "➡️")
                task = step.get("task", "")[:60]
                st.markdown(f"  {emoji} `{agent}` {task}")

        sql_text = exec_info.get("sql", "")
        if sql_text:
            with st.expander("📝 生成的 SQL", expanded=False):
                st.code(sql_text, language="sql")

        ref_attempts = exec_info.get("reflection_attempts", 0)
        if ref_attempts > 0:
            st.markdown(f"**Reflection:** 🔄 重试 {ref_attempts} 次")

        t = exec_info.get("total_tokens", 0) or 0
        if t:
            st.caption(f"本轮 {t:,} tokens")
    else:
        st.caption("发送消息后显示执行详情")

    st.divider()
    st.subheader("🧪 RAG 召回率（消融）")

    ablation_path = Path(__file__).resolve().parent / "logs" / "ablation_results.json"

    if st.button("🔄 运行消融实验", use_container_width=True):
        import subprocess
        with st.spinner("跑消融中（含 LLM HyDE/rerank，约几分钟）..."):
            proc = subprocess.run(
                [sys.executable, "-m", "tests.eval_retrieval_ablation",
                 "--json", "logs/ablation_results.json"],
                cwd=str(Path(__file__).resolve().parent),
                capture_output=True, text=True,
            )
        if proc.returncode == 0:
            st.success("消融完成")
        else:
            st.error(proc.stderr[-400:] or proc.stdout[-400:])

    if ablation_path.exists():
        data = json.loads(ablation_path.read_text(encoding="utf-8"))
        st.caption(f"更新 {data['generated_at']} · 正例 {data['n_pos']} / 负例 {data['n_neg']}")
        lines = ["| 配置 | hit@1 | hit@3 | hit@5 | hit@10 | MRR |",
                 "|---|---|---|---|---|---|"]
        for c in data["configs"]:
            lines.append(
                f"| {c['name']} | {c['hit@1']:.0%} | {c['hit@3']:.0%} "
                f"| {c['hit@5']:.0%} | {c['hit@10']:.0%} | {c['mrr']:.3f} |"
            )
        st.markdown("\n".join(lines))
    else:
        st.caption("暂无消融数据，点上方按钮生成")


# ═══════════════════════════════════════════════════════════════════════
# 主聊天区
# ═══════════════════════════════════════════════════════════════════════

st.title("db-agent")
st.caption("自然语言 → SQL / Hive / HBase / 指标口径 / 趋势分析")

# 渲染历史
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# 输入
if prompt := st.chat_input("输入你的问题，例如：华东区上个月 GMV 多少？"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    loop = _get_loop()
    start_time = time.time()

    with st.chat_message("assistant"):
        status = st.status("思考中...", expanded=False)

        try:
            runner = loop.run_until_complete(_get_runner())
            result = loop.run_until_complete(runner.run(prompt))
        except Exception as e:
            result = f"❌ 执行出错: {e}"
            status.update(label="出错", state="error")

        elapsed = time.time() - start_time

        # HITL 中断处理
        if isinstance(result, dict) and result.get("__interrupt__"):
            interrupt_data = result["data"]
            itype = interrupt_data.get("type", "")

            status.update(label="需要确认", state="complete")

            if itype == "clarify":
                questions = interrupt_data.get("questions", "")
                st.warning(f"**请确认：**\n\n{questions}")
                clarified = st.text_input("补充说明：", key=f"clarify_{st.session_state.turn_count}")
                if clarified:
                    result = loop.run_until_complete(runner.resume(clarified_query=clarified))
                    result = result if isinstance(result, str) else result.get("final_answer", str(result))
                    st.markdown(result)
                    st.session_state.last_exec_info = _extract_exec_info(runner)
            elif itype == "confidence_gate":
                sql = interrupt_data.get("sql", "")
                conf = interrupt_data.get("confidence", 0)
                st.warning(f"**SQL 置信度 {conf:.2f}**，低于阈值 0.7")
                st.code(sql, language="sql")
                col_ok, col_no = st.columns(2)
                key = f"conf_{st.session_state.turn_count}"
                with col_ok:
                    if st.button("✅ 继续执行", key=f"{key}_yes"):
                        result = loop.run_until_complete(runner.resume(approved=True))
                        result = result if isinstance(result, str) else result.get("final_answer", str(result))
                        st.markdown(result)
                        st.session_state.last_exec_info = _extract_exec_info(runner)
                with col_no:
                    if st.button("❌ 取消", key=f"{key}_no"):
                        result = "已取消。"
                        st.markdown(result)
            else:
                msg = interrupt_data.get("message", "需要审批")
                st.warning(f"⚠️ {msg}")
                key = f"hitl_{st.session_state.turn_count}"
                if st.button("✅ 批准", key=key):
                    result = loop.run_until_complete(runner.resume(approved=True))
                    result = result if isinstance(result, str) else result.get("final_answer", str(result))
                    st.markdown(result)
                    st.session_state.last_exec_info = _extract_exec_info(runner)
        else:
            status.update(label="完成", state="complete")
            if isinstance(result, dict):
                result = result.get("final_answer", str(result))
            st.markdown(result)
            st.session_state.last_exec_info = _extract_exec_info(runner)

    st.session_state.messages.append({"role": "assistant", "content": result})
    st.session_state.turn_count += 1
    st.session_state.total_latency += elapsed
    st.rerun()
