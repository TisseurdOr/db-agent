"""回归：multi 多 Agent 图能在离线（脚本化 fake LLM）下端到端跑完。

此前离线测试只覆盖 graph 的辅助函数 + 一个 HBase 小图；router→sql→analysis
这条主链路没有离线端到端验证（只在需要真实 API 的 eval_* 里跑过）。这里用
fake LLM 驱动整张图，确保节点 / 边 / state 流转正确。
"""

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from harness.bootstrap import bootstrap_data


def _initial_state(query: str) -> dict:
    return {
        "query": query,
        "messages": [],
        "_recalled_memories": "",
        "_conversation_summary": "",
        "plan": [],
        "results": {},
        "final_answer": "",
        "next": "",
        "_stats": {"input_tokens": 0, "output_tokens": 0, "turns": 0, "elapsed": 0.0, "nodes": []},
        "_inject_dq": False,
        "_reflection_attempts": 0,
        "_replan_attempts": 0,
        "_replan_feedback": "",
        "_skip_confidence": True,
        "_skip_reflection": True,
    }


@pytest.mark.asyncio
async def test_multi_graph_router_sql_analysis_offline(monkeypatch):
    bootstrap_data()

    import harness.orchestration.multi.nodes as nodes_mod

    # 固定路由，跳过 router 的 LLM 调用，直接下发 sql 计划
    monkeypatch.setattr(
        nodes_mod,
        "route_override",
        lambda q, prev_agents=None: [{"agent": nodes_mod.AGENT_SQL, "task": q}],
    )

    from harness.observation.tracer import TraceContext
    from harness.orchestration.multi.graph import build_multi_agent_graph
    from tests.test_agent import _FakeClient, _FakeTextBlock, _FakeToolUseBlock

    # sql agent：先调 run_query（真实执行），再给结论；analysis：给最终回答
    client = _FakeClient([
        [_FakeToolUseBlock("run_query", {"sql": "SELECT COUNT(*) AS n FROM employees"})],
        [_FakeTextBlock("employees 表共 39 行。")],
        [_FakeTextBlock("最终回答：employees 表共 39 行。")],
    ])

    graph = build_multi_agent_graph(checkpointer=InMemorySaver())
    config = {
        "configurable": {
            "thread_id": "e2e-multi-001",
            "_client": client,
            "_model": "fake-model",
            "_trace": TraceContext("employees 有多少行"),
        }
    }

    out = await graph.ainvoke(_initial_state("employees 表有多少行数据？"), config)
    assert out.get("final_answer"), "多 Agent 图必须产出最终回答"
    assert out.get("plan"), "应有执行计划"
