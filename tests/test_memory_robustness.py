"""记忆的两条回归：① 启动不清空用户记忆 ② 无 embedding 时优雅降级。

背景：
- init_db() 曾在每次启动 DELETE user_memory / user_feedback，导致记忆与反馈
  一重启就丢（记忆最该有的持久性恰恰没有）。
- ensure_memory_stack() 在缺 EMBEDDING_API_KEY 时曾抛 KeyError，把整条记忆栈
  搞挂；应改为优雅降级（返回 None，不抛）。
"""

import sqlite3

from db.seed import DB_PATH
from harness.bootstrap import bootstrap_data


def _count(table: str) -> int:
    return sqlite3.connect(DB_PATH).execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_bootstrap_does_not_wipe_user_memory():
    bootstrap_data()
    from harness.tools.knowledge import save_to_memory

    save_to_memory("回归测试：记忆必须在重启后保留", memory_type="note")
    before = _count("user_memory")
    assert before >= 1

    bootstrap_data()  # 模拟服务重启（Web lifespan / CLI 都会走这一步）
    after = _count("user_memory")
    assert after >= before, "user_memory 不应在启动时被清空"


def test_ensure_memory_stack_degrades_without_embedding(monkeypatch):
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_BASE_URL", raising=False)
    from harness.memory import preturn_recall

    preturn_recall._ready = False
    preturn_recall._vm = None
    preturn_recall._rag = None

    vm, rag = preturn_recall.ensure_memory_stack(None)  # 不应抛异常
    assert vm is None and rag is None
