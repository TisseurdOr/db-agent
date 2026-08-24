"""Feedback storage — SQLite table for user ratings."""

import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "db" / "demo.db"

CREATE_FEEDBACK_TABLE = """
CREATE TABLE IF NOT EXISTS user_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    query TEXT NOT NULL,
    answer TEXT NOT NULL,
    rating TEXT NOT NULL CHECK(rating IN ('up', 'down')),
    comment TEXT DEFAULT '',
    sql TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_FEEDBACK_INDEX1 = """
CREATE INDEX IF NOT EXISTS idx_feedback_trace ON user_feedback(trace_id);
"""

CREATE_FEEDBACK_INDEX2 = """
CREATE INDEX IF NOT EXISTS idx_feedback_created ON user_feedback(created_at);
"""


def init_feedback_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(CREATE_FEEDBACK_TABLE)
    conn.execute(CREATE_FEEDBACK_INDEX1)
    conn.execute(CREATE_FEEDBACK_INDEX2)
    conn.commit()
    conn.close()


def save_feedback(
    trace_id: str = "",
    session_id: str = "",
    query: str = "",
    answer: str = "",
    rating: str = "up",
    comment: str = "",
    sql: str = "",
) -> int:
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.execute(
        """INSERT INTO user_feedback (trace_id, session_id, query, answer, rating, comment, sql, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (trace_id, session_id, query, answer, rating, comment, sql, datetime.now().isoformat()),
    )
    fid = cursor.lastrowid
    conn.commit()
    conn.close()
    return fid
