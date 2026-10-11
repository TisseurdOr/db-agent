"""GET /api/sessions —— 会话列表、历史消息、删除。

存储：默认 **SQLite 持久化**（`db/chat_sessions.db`，进程重启不丢）；
配置 ``REDIS_URL`` 后切到 Redis（多实例共享，带 24h TTL）。

和早期版本的区别：**存完整消息**，不只存文本。
每条 assistant 消息额外带 sql / charts / plan / tokens / elapsed，
所以点回一个旧会话，前端能恢复成当时的样子（步骤、图表、SQL 都在），
而不是只剩一段干巴巴的文字。

测试隔离：用 ``CHAT_DB_PATH`` 指向临时文件即可（conftest 已设）。
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()

_REDIS_KEY_PREFIX = "dbagent:session:"
_REDIS_TTL_SECONDS = 60 * 60 * 24  # 会话保留 24h

_DEFAULT_DB = Path(__file__).resolve().parents[2] / "db" / "chat_sessions.db"

# 进程内兜底（Redis 模式下的"不该写内存"断言仍引用它）
_memory: dict[str, list[dict]] = {}

_redis_client: Any = None
# 已建过表的库路径集合。不能只用一个 bool —— 测试里每个用例的
# CHAT_DB_PATH 不同，用 bool 会导致后续库跳过建表而报 no such table。
_schema_ready: set[str] = set()


def _db_path() -> str:
    return os.getenv("CHAT_DB_PATH") or str(_DEFAULT_DB)


def _conn() -> sqlite3.Connection:
    """打开连接并按需建表。SQLite 单机够用，且和项目其他部分一致。"""
    path = _db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    if path not in _schema_ready:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id         TEXT PRIMARY KEY,
                title      TEXT,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS chat_messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role       TEXT NOT NULL,
                content    TEXT NOT NULL,
                trace_id   TEXT,
                sql        TEXT,
                charts     TEXT,
                plan       TEXT,
                tokens     INTEGER DEFAULT 0,
                elapsed    REAL    DEFAULT 0,
                created_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_chat_messages_session
                ON chat_messages(session_id, id);
            """
        )
        conn.commit()
        _schema_ready.add(path)
    return conn


def _json_or(default: str, value: Any) -> str:
    if value is None:
        return default
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return default


def _loads(raw: Any, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _get_redis_client() -> Any:
    """惰性创建 Redis 异步客户端；未配置 REDIS_URL 返回 None。"""
    global _redis_client
    if _redis_client is None:
        url = os.getenv("REDIS_URL", "").strip()
        if not url:
            return None
        import redis.asyncio as aioredis
        _redis_client = aioredis.from_url(url, decode_responses=True)
    return _redis_client


# Redis 可用性缓存：None=未探测 / True=可用 / False=不可用（后续直接走 SQLite）
_redis_available: bool | None = None


async def _usable_redis() -> Any:
    """返回**可用**的 Redis 客户端；未配置或连不上 → None（调用方走 SQLite）。

    项目里其他组件（checkpointer / 向量库）都是"Redis 挂了自动降级"，
    会话存储也照这个来 —— 否则 .env 里配了 REDIS_URL 但 Redis 没起，
    会话列表会直接 500，而不是优雅退回本地。
    """
    global _redis_available
    if _redis_available is False:
        return None
    client = _get_redis_client()
    if client is None:
        _redis_available = False
        return None
    if _redis_available is True:
        return client
    try:
        await client.ping()
        _redis_available = True
        return client
    except Exception:
        _redis_available = False
        return None


def clear_sessions() -> None:
    """清空本地会话（测试隔离用；Redis 模式不影响）。"""
    _memory.clear()
    try:
        conn = _conn()
        try:
            conn.execute("DELETE FROM chat_messages")
            conn.execute("DELETE FROM chat_sessions")
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        pass


async def record_message(
    session_id: str,
    role: str,
    content: str,
    trace_id: str = "",
    *,
    sql: str = "",
    charts: list | None = None,
    plan: list | None = None,
    tokens: int = 0,
    elapsed: float = 0.0,
) -> None:
    """记录一条会话消息。Redis 模式走 Redis；否则写 SQLite。"""
    entry = {
        "role": role,
        "content": content or "",
        "trace_id": trace_id,
        "sql": sql or "",
        "charts": charts or [],
        "plan": plan or [],
        "tokens": int(tokens or 0),
        "elapsed": float(elapsed or 0),
    }

    client = await _usable_redis()
    if client is not None:
        try:
            key = _REDIS_KEY_PREFIX + session_id
            await client.rpush(key, json.dumps(entry, ensure_ascii=False))
            await client.expire(key, _REDIS_TTL_SECONDS)
            return
        except Exception:
            pass  # Redis 中途挂了 → 落到下面的 SQLite

    from datetime import UTC, datetime

    now = datetime.now(UTC).isoformat(timespec="seconds")
    conn = _conn()
    try:
        conn.execute(
            """INSERT INTO chat_messages
               (session_id, role, content, trace_id, sql, charts, plan, tokens, elapsed, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                role,
                entry["content"],
                trace_id,
                entry["sql"],
                _json_or("[]", charts),
                _json_or("[]", plan),
                entry["tokens"],
                entry["elapsed"],
                now,
            ),
        )
        # 会话不存在则建，标题取第一条用户消息（列表里得能认出来）
        title = (entry["content"][:24] if role == "user" else None)
        conn.execute(
            """INSERT INTO chat_sessions (id, title, created_at, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 title = COALESCE(chat_sessions.title, excluded.title),
                 updated_at = excluded.updated_at""",
            (session_id, title, now, now),
        )
        conn.commit()
    finally:
        conn.close()


async def list_sessions() -> list[dict]:
    """列出所有会话。按最近更新倒序 —— 最新问的排最上面。"""
    client = await _usable_redis()
    if client is not None:
        sessions = []
        async for key in client.scan_iter(match=_REDIS_KEY_PREFIX + "*", count=100):
            sid = key[len(_REDIS_KEY_PREFIX):]
            count = await client.llen(key)
            sessions.append({"id": sid, "title": sid, "message_count": count, "updated_at": None})
        return sessions

    conn = _conn()
    try:
        rows = conn.execute(
            """SELECT s.id, s.title, s.updated_at,
                      (SELECT COUNT(*) FROM chat_messages m WHERE m.session_id = s.id) AS message_count
               FROM chat_sessions s
               ORDER BY s.updated_at DESC"""
        ).fetchall()
        return [
            {
                "id": r["id"],
                "title": r["title"] or r["id"],
                "message_count": r["message_count"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]
    finally:
        conn.close()


async def get_messages(session_id: str) -> list[dict]:
    """取回一个会话的全部消息（含完整字段）。"""
    client = await _usable_redis()
    if client is not None:
        raw = await client.lrange(_REDIS_KEY_PREFIX + session_id, 0, -1)
        return [json.loads(x) for x in raw]

    conn = _conn()
    try:
        rows = conn.execute(
            """SELECT role, content, trace_id, sql, charts, plan, tokens, elapsed, created_at
               FROM chat_messages WHERE session_id = ? ORDER BY id ASC""",
            (session_id,),
        ).fetchall()
        return [
            {
                "role": r["role"],
                "content": r["content"],
                "trace_id": r["trace_id"] or "",
                "sql": r["sql"] or "",
                "charts": _loads(r["charts"], []),
                "plan": _loads(r["plan"], []),
                "tokens": r["tokens"] or 0,
                "elapsed": r["elapsed"] or 0.0,
                "created_at": r["created_at"],
            }
            for r in rows
        ]
    finally:
        conn.close()


async def delete_session(session_id: str) -> bool:
    """删除一个会话及其消息。返回是否删到了东西。"""
    client = await _usable_redis()
    if client is not None:
        try:
            key = _REDIS_KEY_PREFIX + session_id
            existed = await client.exists(key)
            await client.delete(key)
            return bool(existed)
        except Exception:
            pass

    conn = _conn()
    try:
        cur = conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ── HTTP ────────────────────────────────────────────────────────────────

@router.get("/sessions")
async def list_sessions_endpoint():
    return {"sessions": await list_sessions()}


@router.get("/sessions/{session_id}/messages")
async def get_messages_endpoint(session_id: str):
    return {"messages": await get_messages(session_id)}


@router.delete("/sessions/{session_id}")
async def delete_session_endpoint(session_id: str):
    if not await delete_session(session_id):
        raise HTTPException(status_code=404, detail=f"session '{session_id}' not found")
    return {"ok": True, "deleted": session_id}
