"""multi 记忆补齐：Router/SQL 注入、mid-flight、Web pre-turn、search_memory 挂载。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from harness.memory.preturn_recall import (
    RecallBundle,
    format_memory_for_router,
    format_memory_for_sql,
    should_rerecall,
)
from harness.orchestration.multi import agents as multi_agents
from server.endpoints import sessions as sessions_mod
from server.runner_wrapper import StreamingRunner


def test_format_memory_blocks_include_guidance():
    block = format_memory_for_sql("问: 华东销售额\n答: 120万")
    assert "历史口径" in block
    assert "华东" in block
    assert "search_memory" in block
    r = format_memory_for_router("问: 华东\n答: ok")
    assert "指代消解" in r
    assert "禁止" in r


def test_should_rerecall_on_expanded_task():
    assert should_rerecall("改成华东", "查询华东地区部门销售额，状态=completed") is True
    assert should_rerecall("华东销售额", "华东销售额") is False
    assert should_rerecall("a", "") is False


def test_sql_and_analysis_mount_search_memory():
    sql_names = {t["name"] if isinstance(t, dict) else t.get("name") for t in multi_agents.sql_agent.tools}
    # tool schemas are dicts with name
    def _name(t):
        if isinstance(t, dict):
            return t.get("name")
        return getattr(t, "name", None) or (t.get("name") if hasattr(t, "get") else None)

    sql_names = set()
    for t in multi_agents.sql_agent.tools:
        if isinstance(t, dict):
            sql_names.add(t.get("name") or t.get("function", {}).get("name"))
        else:
            sql_names.add(getattr(t, "get", lambda *_: None)("name"))
    # Anthropic format: {"name": "...", "input_schema": ...} or {"name":...}
    assert "search_memory" in multi_agents.sql_agent.handlers
    assert "search_memory" in multi_agents.analysis_agent.handlers
    tool_names_sql = []
    for t in multi_agents.sql_agent.tools:
        if isinstance(t, dict):
            tool_names_sql.append(t.get("name"))
    assert "search_memory" in tool_names_sql


@pytest.mark.asyncio
async def test_web_injects_recalled_memories(monkeypatch, tmp_path):
    sessions_mod.clear_sessions()
    monkeypatch.setattr("server.runner_wrapper.guard_input", lambda q: (True, ""))
    monkeypatch.setattr("server.runner_wrapper.flush_opik", lambda: None)
    monkeypatch.setattr("server.runner_wrapper.get_current_opik_trace_id", lambda: None)
    monkeypatch.setattr("server.runner_wrapper.capture_opik_trace_id_for_graph", lambda g: None)

    async def fake_recall(query, client=None, **kwargs):
        return RecallBundle(
            text="问: 上次华东销售额\n答: 100",
            meta_hint="",
            memories=[{"text": "问: 上次华东销售额\n答: 100", "score": 0.9}],
            source="self_query",
        )

    monkeypatch.setattr(
        "harness.memory.preturn_recall.recall_for_turn",
        fake_recall,
    )
    monkeypatch.setattr(
        "harness.memory.preturn_recall.ensure_memory_stack",
        lambda client=None: (None, None),
    )
    monkeypatch.setattr(
        "harness.memory.preturn_recall.remember_turn",
        lambda q, a: None,
    )

    from harness.memory.short_term_memory import ConversationManager

    class _Tmp(ConversationManager):
        def __init__(self, *a, state_dir=None, **k):
            self._state_dir = state_dir
            super().__init__(*a, **k)

        @property
        def _state_path(self):
            return self._state_dir / f"conversation_{self.session_id}.json"

    conv = _Tmp(client=None, max_recent=10, session_id="s-mem", state_dir=tmp_path)
    runner = MagicMock()
    runner.thread_id = "web-s-mem"
    runner.client = MagicMock()
    runner.model = "test"
    runner.router_cache = MagicMock()
    runner.task_manager = MagicMock()
    runner._current_config = None
    runner._should_inject_dq.return_value = False
    runner._last_state = None
    runner._session_id = "s-mem"
    runner._conversation = conv
    captured = {}

    async def capture_ainvoke(state, config=None):
        captured["mem"] = state.get("_recalled_memories", "MISSING")
        return {"final_answer": "ok", "results": {}}

    runner.graph = AsyncMock()
    runner.graph.ainvoke = capture_ainvoke
    runner.graph.aget_state = AsyncMock(return_value=SimpleNamespace(interrupts=[]))
    runner.get_execution_info.return_value = {
        "sql": "", "plan": [], "stats": {}, "tokens": 0,
    }
    runner._annotate_turn_cost = MagicMock()

    sr = StreamingRunner(runner)
    q = asyncio.Queue()
    await sr.run_streaming("改成华东", q)
    assert "华东销售额" in captured.get("mem", "")
