"""MCP 入口测试：工具注册 + 缺 key 的友好降级（不真调 LLM）。"""

from __future__ import annotations

import asyncio
import json


def test_mcp_exposes_agent_tools():
    from mcp_server.db_agent_mcp import mcp

    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {"ask_db_agent", "resume_db_agent", "db_agent_sessions", "db_agent_tools"}

    ask = next(t for t in tools if t.name == "ask_db_agent")
    schema = ask.inputSchema
    assert "question" in schema["properties"]
    assert schema["required"] == ["question"]
    # session_id / user_id / enable_data_quality 都是可选参数
    assert {"session_id", "user_id", "enable_data_quality"} <= set(schema["properties"])


def test_ask_reports_missing_api_key(monkeypatch):
    import mcp_server.db_agent_mcp as mod

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(mod, "_client", None)
    monkeypatch.setattr(mod, "_bootstrapped", True)  # 跳过 seed，专注 key 缺失路径

    out = asyncio.run(mod.ask_db_agent("有哪些表？", session_id="missing-key"))
    data = json.loads(out)
    assert data["status"] == "error"
    assert "ANTHROPIC_API_KEY" in data["message"]


def test_resume_without_pending_query_is_friendly(monkeypatch):
    import mcp_server.db_agent_mcp as mod
    from server.runner_wrapper import runner_registry

    runner_registry._runners.clear()
    out = asyncio.run(mod.resume_db_agent(session_id="nothing-pending"))
    data = json.loads(out)
    assert data["status"] == "error"
    assert "没有待审批" in data["message"]


def test_sessions_lists_active_runners():
    from server.runner_wrapper import RunnerRegistry

    registry = RunnerRegistry()
    assert registry.list_sessions() == []


def test_drive_passes_charts_and_stats_through(monkeypatch):
    """done 事件里的 charts / stats 必须透传到 MCP 返回值。

    回归背景：早先 MCP 路径只挑 answer/sql/plan/tokens，图表数据被静默丢掉，
    表现为「在 MCP 里看不到图，也拿不到画图所需的数据」。
    """
    import mcp_server.db_agent_mcp as mod
    import server.runner_wrapper as rw

    chart = {"type": "bar", "title": "各部门人数", "series": [{"name": "人数", "data": [3, 5]}]}

    class _FakeStreaming:
        def __init__(self, runner):
            self.runner = runner

        async def run_streaming(self, query, queue):
            await queue.put(("step_start", {"type": "step_start", "node": "sql", "task": "查数"}))
            await queue.put((
                "done",
                {
                    "type": "done",
                    "answer": "共 7763 行",
                    "sql": "SELECT COUNT(*) FROM orders",
                    "plan": [{"agent": "sql", "task": "查数"}],
                    "charts": [chart],
                    "stats": {"rows": 1, "elapsed": 0.4},
                    "tokens": 1116,
                    "total_elapsed": 13.78,
                    "trace_id": "trace-1",
                },
            ))

    class _FakeRunner:
        def get_execution_info(self):
            return {"sql": "", "plan": [], "tokens": 0, "stats": {}}

    monkeypatch.setattr(rw, "StreamingRunner", _FakeStreaming)

    result = asyncio.run(mod._drive(_FakeRunner(), mode="ask", question="orders 有多少行"))

    assert result["status"] == "ok"
    assert result["charts"] == [chart], "图表配置不能丢"
    assert result["stats"] == {"rows": 1, "elapsed": 0.4}
    assert result["answer"] == "共 7763 行"
    assert result["sql"] == "SELECT COUNT(*) FROM orders"
    assert result["tokens"] == 1116


def test_drive_defaults_charts_to_empty_when_absent(monkeypatch):
    """没有图表的普通回答，charts 应是空列表而不是缺字段——前端解析更省事。"""
    import mcp_server.db_agent_mcp as mod
    import server.runner_wrapper as rw

    class _FakeStreaming:
        def __init__(self, runner):
            self.runner = runner

        async def run_streaming(self, query, queue):
            await queue.put(("done", {"type": "done", "answer": "纯文字回答", "sql": "", "plan": []}))

    class _FakeRunner:
        def get_execution_info(self):
            return {"sql": "", "plan": [], "tokens": 0, "stats": {}}

    monkeypatch.setattr(rw, "StreamingRunner", _FakeStreaming)

    result = asyncio.run(mod._drive(_FakeRunner(), mode="ask", question="你好"))
    assert result["status"] == "ok"
    assert result["charts"] == []
    assert result["answer"] == "纯文字回答"
