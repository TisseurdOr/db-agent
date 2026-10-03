"""真实 LLM 端到端 smoke 测试——无 API key 时自动跳过。

与其余 500+ 条离线测试（全 fake）不同：这里真的调用模型 + 真实 SQLite，
走通「自然语言 → 模型写 SQL → 工具执行 → 回答」这条完整链路。

为什么需要它：离线单测证明胶水能跑，但证明不了「模型真能把 SQL 写对」。
这一条补上那个缺口。默认在 CI / 无 key 环境 skip，只在配好 key 时执行：

    ANTHROPIC_API_KEY=... uv run pytest tests/test_e2e_live.py -v -s

注意会消耗真实 token（一次约一问一答）。
"""

import os

import pytest
from dotenv import load_dotenv

load_dotenv()

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.getenv("ANTHROPIC_API_KEY"),
        reason="需要真实 ANTHROPIC_API_KEY（live 端到端测试）",
    ),
]


@pytest.mark.asyncio
async def test_live_single_agent_answers_with_sql():
    """问一个必须查库才能答的问题，断言拿到了含数字的答案。"""
    from harness.bootstrap import bootstrap_data
    from harness.context.system_prompt import build_system_prompt
    from harness.llm_client import get_anthropic_client
    from harness.orchestration.single.agent import agent_loop
    from harness.orchestration.single.tools_bundle import TOOL_HANDLERS, TOOLS

    bootstrap_data()
    client = get_anthropic_client()

    answer = await agent_loop(
        client,
        "orders 表里一共有多少条订单记录？只给数字和一句话结论。",
        build_system_prompt(user_role="DBA"),
        TOOLS,
        TOOL_HANDLERS,
    )

    assert answer and answer.strip(), "Agent 必须给出非空回答"
    assert any(ch.isdigit() for ch in answer), f"答案应含查询得到的数字，实际: {answer!r}"
