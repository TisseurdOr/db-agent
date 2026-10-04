"""run_insert：写库必须过 RBAC + 表/列校验 + HITL，未批准不落库。"""

from __future__ import annotations

import asyncio
import sqlite3
from typing import TypedDict

import pytest

from harness.tools import write


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db = tmp_path / "write.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT UNIQUE)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(write, "DB_PATH", str(db))
    return db


@pytest.fixture(autouse=True)
def _allow_dba_run_insert(monkeypatch):
    """测试环境不重新 seed 全库，直接把 run_insert 加进当前 dba 角色。"""
    from harness.constraints import entitlement

    monkeypatch.setenv("AGENT_USER", "dba")
    dba = entitlement.ROLES["dba"]
    monkeypatch.setitem(dba, "allowed_tools", [*dba["allowed_tools"], "run_insert"])


def _count(db) -> int:
    conn = sqlite3.connect(db)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM items").fetchone()[0])
    finally:
        conn.close()


def test_dba_default_role_uses_run_insert_not_ghost_write_query():
    from harness.constraints.entitlement import _DEFAULT_ROLES

    tools = _DEFAULT_ROLES["dba"]["allowed_tools"]
    assert "run_insert" in tools
    assert "write_query" not in tools


def test_run_insert_outside_graph_waits_for_approval(temp_db):
    """不在图里执行时，只返回待审批，绝不落库。"""
    result = write.run_insert("items", {"id": 1, "name": "alice"}, key_columns=["id"])
    assert result["error"] == "需要人工审批"
    assert result["pending_sql"].startswith('INSERT INTO "items"')
    assert _count(temp_db) == 0


def test_run_insert_rejects_unknown_column(temp_db):
    result = write.run_insert("items", {"id": 1, "nope": "x"})
    assert "列不存在" in result["error"]
    assert _count(temp_db) == 0


def test_run_insert_rejects_bad_table_identifier(temp_db):
    result = write.run_insert('items"; DROP TABLE items; --', {"id": 1})
    assert "不合法" in result["error"]
    assert _count(temp_db) == 0


def test_run_insert_executes_after_approval(temp_db, monkeypatch):
    monkeypatch.setattr("langgraph.types.interrupt", lambda _payload: {"approved": True})
    result = write.run_insert("items", {"id": 1, "name": "alice"}, key_columns=["id"])
    assert result["ok"] is True
    assert result["inserted"] is True
    assert _count(temp_db) == 1


def test_run_insert_rejected_does_not_write(temp_db, monkeypatch):
    monkeypatch.setattr("langgraph.types.interrupt", lambda _payload: {"approved": False})
    result = write.run_insert("items", {"id": 1, "name": "alice"})
    assert result["ok"] is False
    assert "拒绝" in result["error"]
    assert _count(temp_db) == 0


def test_run_insert_is_idempotent_with_key_columns(temp_db, monkeypatch):
    monkeypatch.setattr("langgraph.types.interrupt", lambda _payload: {"approved": True})
    first = write.run_insert("items", {"id": 1, "name": "alice"}, key_columns=["id"])
    second = write.run_insert("items", {"id": 1, "name": "alice"}, key_columns=["id"])
    assert first["inserted"] is True
    assert second["inserted"] is False
    assert _count(temp_db) == 1


def test_run_insert_in_graph_with_hitl_resume(temp_db):
    """真实 LangGraph 链路：interrupt 暂停 → resume 批准 → 落库。"""
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, StateGraph
    from langgraph.types import Command

    class InsertState(TypedDict):
        done: bool

    def insert_node(state: InsertState):
        result = write.run_insert("items", {"id": 1, "name": "alice"}, key_columns=["id"])
        return {"done": bool(result.get("ok"))}

    async def run_cycle():
        builder = StateGraph(InsertState)
        builder.add_node("insert", insert_node)
        builder.set_entry_point("insert")
        builder.add_edge("insert", END)
        graph = builder.compile(checkpointer=InMemorySaver())

        config = {"configurable": {"thread_id": "test-run-insert-hitl-001"}}
        await graph.ainvoke({"done": False}, config)
        assert _count(temp_db) == 0

        snapshot = await graph.aget_state(config)
        assert snapshot.interrupts, "run_insert 应触发 HITL interrupt"
        payload = snapshot.interrupts[0].value
        assert payload["type"] == "hitl_write"
        assert payload["table"] == "items"

        await graph.ainvoke(Command(resume={"approved": True}), config)
        assert _count(temp_db) == 1

    asyncio.run(run_cycle())
