"""db-agent Streamlit 聊天界面。

用法:
    uv run streamlit run app.py

布局:
    主区 — 聊天对话
    侧边栏 — Router 分派 / 执行计划 / SQL / Reflection / Token 统计
"""

import asyncio
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv

load_dotenv()

from anthropic import Anthropic

from db.seed import init_db
from harness.context.schema_discovery import get_schema_discovery
from harness.context.template_matcher import init_metric_registry
from harness.orchestration.multi.orchestrator import MultiAgentRunner
from harness.tools.hbase import _seed_hbase_store

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
    init_db()
    _seed_hbase_store()
    init_metric_registry()
    try:
        get_schema_discovery().build_index()
    except Exception:
        pass  # embedding 不可用时跳过，Agent 降级用 list_tables/describe_table
    client = Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        base_url=os.environ.get("ANTHROPIC_BASE_URL"),
    )
    from harness.observation.opik_tracing import wrap_anthropic_client
    return wrap_anthropic_client(client)

client = init_system()
DEFAULT_MODEL = os.getenv("ANTHROPIC_MODEL", "deepseek-chat")


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
                "sql": "🗄️", "strategy": "📋", "analysis": "📊",
                "hbase": "🗃️", "hive": "🐝", "data_quality": "🔬",
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
