"""Web short-term memory wiring: sessions log + ConversationManager inject/commit."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from harness.memory.short_term_memory import ConversationManager
from server.endpoints import sessions as sessions_mod
from server.runner_wrapper import StreamingRunner


class _TmpConversation(ConversationManager):
    def __init__(self, *args, state_dir=None, **kwargs):
        self._state_dir = state_dir
        super().__init__(*args, **kwargs)

    @property
    def _state_path(self):
        return self._state_dir / f"conversation_{self.session_id}.json"


def _make_fake_runner(session_id, conv):
    runner = MagicMock()
    runner.thread_id = f"web-{session_id}"
    runner.client = MagicMock()
    runner.model = "test-model"
    runner.router_cache = MagicMock()
    runner.task_manager = MagicMock()
    runner._current_config = None
    runner._should_inject_dq.return_value = False
    runner._last_state = None
    runner._session_id = session_id
    runner._conversation = conv

    graph = AsyncMock()
    graph.ainvoke = AsyncMock(return_value={
        "final_answer": "最终答案",
        "results": {"sql": "SELECT 1"},
    })
    graph.aget_state = AsyncMock(return_value=SimpleNamespace(interrupts=[]))
    runner.graph = graph
    runner.get_execution_info.return_value = {
        "sql": "SELECT 1",
        "plan": [{"agent": "sql", "task": "查询"}],
        "stats": {},
        "tokens": 0,
    }
    runner._annotate_turn_cost = MagicMock()
    return runner


@pytest.mark.asyncio
async def test_streaming_commits_short_term(monkeypatch, tmp_path):
    sessions_mod.clear_sessions()
    monkeypatch.setattr("server.runner_wrapper.guard_input", lambda q: (True, ""))
    monkeypatch.setattr("server.runner_wrapper.flush_opik", lambda: None)
    monkeypatch.setattr("server.runner_wrapper.get_current_opik_trace_id", lambda: None)
    monkeypatch.setattr("server.runner_wrapper.capture_opik_trace_id_for_graph", lambda g: None)

    async def _empty_recall(*a, **k):
        from harness.memory.preturn_recall import RecallBundle
        return RecallBundle(source="empty")

    monkeypatch.setattr("harness.memory.preturn_recall.recall_for_turn", _empty_recall)
    monkeypatch.setattr("harness.memory.preturn_recall.ensure_memory_stack", lambda client=None: (None, None))
    monkeypatch.setattr("harness.memory.preturn_recall.remember_turn", lambda q, a: None)

    conv = _TmpConversation(client=None, max_recent=10, session_id="s-stm", state_dir=tmp_path)
    runner = _make_fake_runner("s-stm", conv)
    sr = StreamingRunner(runner)

    q = asyncio.Queue()
    await sr.run_streaming("华东销售额", q)
    while not q.empty():
        await q.get()

    msgs = await sessions_mod.get_messages("s-stm")
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert "华东" in msgs[0]["content"]
    assert msgs[1]["role"] == "assistant"
    assert conv.messages[-1]["content"] == "最终答案"
    assert (tmp_path / "conversation_s-stm.json").exists()

    captured = {}

    async def capture_ainvoke(state, config=None):
        captured["summary"] = state.get("_conversation_summary", "MISSING")
        return {"final_answer": "第二轮答案", "results": {}}

    runner.graph.ainvoke = capture_ainvoke
    q2 = asyncio.Queue()
    await sr.run_streaming("再问一次", q2)
    assert captured.get("summary") == ""
    assert len(await sessions_mod.get_messages("s-stm")) == 4
    assert len(conv.messages) == 4
