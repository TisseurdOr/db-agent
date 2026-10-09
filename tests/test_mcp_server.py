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
