"""P0/P1: summary bound, checkpoint trim, list_recent index, episode flush."""

import os

import pytest

from harness.memory.episode_memory import (
    _buffers,
    expand_episode_hit,
    load_turns,
    record_and_maybe_flush,
)
from harness.memory.recent_index import append_recent, drop_index, list_recent_ids
from harness.memory.short_term_memory import ConversationManager
from harness.orchestration.multi.state import CHECKPOINT_MESSAGE_LIMIT, add_messages_trim


@pytest.mark.asyncio
async def test_summary_stays_bounded_without_llm():
    mgr = ConversationManager(client=None, max_recent=4, max_summary_tokens=40)
    # Simulate unbounded merge then bound
    mgr.summary = "\n".join([f"摘要段落下文很长_{i}_" + ("字" * 30) for i in range(20)])
    assert mgr._estimate(mgr.summary) > mgr.max_summary_tokens
    await mgr._bound_summary()
    assert mgr._estimate(mgr.summary) <= mgr.max_summary_tokens + 5  # hard truncate approx


def test_checkpoint_messages_trim():
    left = [{"type": "human", "content": f"u{i}"} for i in range(5)]
    # add_messages expects LangChain messages or dicts — use simple dicts carefully
    from langchain_core.messages import AIMessage, HumanMessage

    left = [HumanMessage(content=f"u{i}") for i in range(15)]
    right = [AIMessage(content="a"), HumanMessage(content="u_new")]
    merged = add_messages_trim(left, right)
    assert len(merged) <= CHECKPOINT_MESSAGE_LIMIT
    assert merged[-1].content == "u_new"


def test_recent_index_avoids_needing_full_scan(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_RECENT_INDEX_SCAN", "1000")
    # redirect index dir via monkeypatch of module path
    import harness.memory.recent_index as ri

    monkeypatch.setattr(ri, "_INDEX_DIR", tmp_path)
    drop_index("test_col")
    for i in range(10):
        append_recent("test_col", f"id-{i}", user_id="default", timestamp=f"t{i}", memory_type="note")
    ids = list_recent_ids("test_col", limit=3)
    assert ids == ["id-9", "id-8", "id-7"]


@pytest.mark.asyncio
async def test_episode_flush_every_n(tmp_path, monkeypatch):
    import harness.memory.episode_memory as em
    from harness.memory.vector_store import VectorMemory
    from tests.fake_embedding import fake_embedding

    monkeypatch.setattr(em, "_TURN_DIR", tmp_path / "turns")
    monkeypatch.setattr(em, "EPISODE_EVERY_N", 3)
    # also patch module constant used in record
    em.EPISODE_EVERY_N = 3
    _buffers.clear()

    col = f"ep_test_{os.getpid()}"
    mem = VectorMemory(collection_name=col, embed_fn=fake_embedding, persist_dir=str(tmp_path / "chroma"))
    sid = "sess-ep"
    for i in range(3):
        await record_and_maybe_flush(sid, f"问{i}", f"答{i}", mem, client=None)
    # one episode written
    recent = mem.list_recent(limit=5, memory_type="episode")
    assert len(recent) >= 1
    meta = recent[0]["metadata"]
    assert meta.get("memory_type") == "episode" or meta.get("kind") == "episode"
    assert int(meta.get("turn_start")) == 1
    assert int(meta.get("turn_end")) == 3
    children = load_turns(sid, 1, 3)
    assert len(children) == 3
    expanded = expand_episode_hit(recent[0])
    assert "情节展开" in expanded or "问0" in expanded
    mem.drop()
