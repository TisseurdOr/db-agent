# scripts/demo_recovery.py — 错误恢复三层机制的现场演示
#
# 用法（在仓库根目录）:
#   .venv/bin/python scripts/demo_recovery.py           # 跑全部三幕
#   .venv/bin/python scripts/demo_recovery.py retry     # 只跑第 1 幕（零 API 成本）
#   .venv/bin/python scripts/demo_recovery.py heal      # 只跑第 2 幕（需 API key）
#   .venv/bin/python scripts/demo_recovery.py replan    # 只跑第 3 幕（需 API key）
#
# 三幕对应 HARNESS s11 的三层自愈：
#   1. API 重试     — 模拟连续 429，观察指数退避后成功
#   2. SQL 自愈     — 让 SQL Agent 执行一条字段名错误的 SQL，观察报错→核对→重写
#   3. 失败重规划   — 给 SQL Agent 注入一次假超时，观察 Router 收到反馈后重排计划

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("AGENT_USER", "dba")  # dba 可 run_query，演示不被权限拦截

from harness.constraints.retry import acall_with_retry


def banner(title: str):
    print(f"\n{'═' * 60}\n  {title}\n{'═' * 60}")


# ── 第 1 幕: API 重试（零 API 成本，纯模拟）──

class Fake429(Exception):
    """模拟 DeepSeek 限流——is_retriable 靠 status_code 识别。"""
    status_code = 429


async def demo_retry():
    banner("第 1 幕 · API 重试：前 2 次调用返回 429，第 3 次成功")
    calls = {"n": 0}

    def flaky_llm_call():
        calls["n"] += 1
        print(f"   → 第 {calls['n']} 次调用 LLM API...", end=" ")
        if calls["n"] < 3:
            print("💥 HTTP 429 (rate limited)")
            raise Fake429()
        print("✅ 200 OK")
        return "这是 LLM 的回复"

    result = await acall_with_retry(flaky_llm_call, max_retries=3, base_delay=1.0)
    print(f"   最终结果: {result}（共调用 {calls['n']} 次，退避等待自动完成）")
    print("   ↳ 对照实验：改动前的代码在第 1 次 429 时就会让整轮对话直接崩掉")


# ── 第 2 幕: SQL 自愈（真实 API）──

async def demo_heal():
    banner("第 2 幕 · SQL 自愈：故意执行字段名错误的 SQL")
    from anthropic import Anthropic
    from harness.orchestration.multi.agents import sql_agent

    client = Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        base_url=os.environ.get("ANTHROPIC_BASE_URL"),
    )
    # total_amount 不存在（正确字段是 total）——强制先执行错的，观察自愈协议
    task = (
        "先原样执行这条 SQL（第一步不要检查表结构，直接执行）："
        "SELECT SUM(total_amount) FROM orders; "
        "之后按你的流程处理，最终告诉我订单总金额。"
    )
    print(f"   任务: {task}\n   —— 观察下方 tool 调用链：run_query 报错 → describe_table 核对 → 重写 SQL ——\n")
    result, usage = await sql_agent.run(client, task, verbose=True)
    print(f"\n   最终回答: {result[:300]}")
    print(f"   消耗: {usage['turns']} 轮 / {usage['input_tokens']}+{usage['output_tokens']} tokens")
    print("   ↳ 对照实验：改动前 prompt 写的是「SQL 报错如实报告」——第一轮报错就直接放弃")


# ── 第 3 幕: 失败重规划（真实 API 路由）──

TIMEOUT_MARK = "(Agent 在 8 轮内未完成)"


async def demo_replan():
    banner("第 3 幕 · 失败重规划：给 SQL Agent 注入一次假超时")
    from anthropic import Anthropic
    from harness.orchestration.multi.agents import sql_agent
    from harness.orchestration.multi.orchestrator import MultiAgentRunner

    client = Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        base_url=os.environ.get("ANTHROPIC_BASE_URL"),
    )

    # monkeypatch：第 1 次调用返回超时标记，之后恢复真实执行
    orig_run = sql_agent.run
    calls = {"n": 0}

    async def flaky_run(client, task, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            print("   💥 [注入] SQL Agent 第 1 次执行 → 假装超过最大轮数失败")
            return TIMEOUT_MARK, {"input_tokens": 0, "output_tokens": 0, "turns": 8}
        print(f"   ✅ [注入] SQL Agent 第 {calls['n']} 次执行（重规划后）→ 恢复真实执行")
        return await orig_run(client, task, **kwargs)

    sql_agent.run = flaky_run
    try:
        runner = await MultiAgentRunner.create(
            client,
            model=os.getenv("ANTHROPIC_MODEL", "deepseek-chat"),
            enable_data_quality=False,
            checkpoint_db="/tmp/demo_recovery_state.db",
            thread_id=f"demo-replan-{os.getpid()}",
        )
        print("   问题: 各部门的订单总金额是多少\n   —— 观察: sql 失败 → 🔄 回 Router 重规划 → 新 plan 再执行 ——\n")
        answer = await runner.run("各部门的订单总金额是多少")
        print(f"\n   最终回答: {str(answer)[:300]}")
        print(f"   SQL Agent 实际被调用 {calls['n']} 次（第 1 次失败，重规划后成功）")
        print("   ↳ 对照实验：改动前失败结果会原样流进 Analysis，用户只能得到一句「请重试」")
        await runner.aclose()
    finally:
        sql_agent.run = orig_run


async def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("retry", "all"):
        await demo_retry()
    if which in ("heal", "all"):
        await demo_heal()
    if which in ("replan", "all"):
        await demo_replan()
    print()


if __name__ == "__main__":
    asyncio.run(main())
