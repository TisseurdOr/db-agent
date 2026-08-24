"""GET /api/sessions — list and retrieve chat sessions.

会话存储抽象：默认内存（单机演示，重启即清）；
配置 REDIS_URL 后自动切到 Redis（多实例共享，带 TTL 自动过期）。
"""

import json
import os

from fastapi import APIRouter

router = APIRouter()

_REDIS_KEY_PREFIX = "dbagent:session:"
_REDIS_TTL_SECONDS = 60 * 60 * 24  # 会话保留 24h

# 内存兜底（未配置 REDIS_URL 时使用）
_memory: dict[str, list[dict]] = {}

_redis_client = None


def _get_redis_client():
    """惰性创建 Redis 异步客户端；未配置 REDIS_URL 返回 None。"""
    global _redis_client
    if _redis_client is None:
        url = os.getenv("REDIS_URL", "").strip()
        if not url:
            return None
        import redis.asyncio as aioredis
        _redis_client = aioredis.from_url(url, decode_responses=True)
    return _redis_client


def clear_sessions() -> None:
    """清空内存会话（测试隔离用；Redis 模式不影响）。"""
    _memory.clear()


async def record_message(session_id: str, role: str, content: str, trace_id: str = "") -> None:
    """记录一条会话消息（Redis 列表追加，或写入内存）。"""
    entry = {"role": role, "content": content[:500], "trace_id": trace_id}
    client = _get_redis_client()
    if client is not None:
        key = _REDIS_KEY_PREFIX + session_id
        await client.rpush(key, json.dumps(entry, ensure_ascii=False))
        await client.expire(key, _REDIS_TTL_SECONDS)
        return
    _memory.setdefault(session_id, []).append(entry)


async def list_sessions() -> list[dict]:
    """列出所有会话（Redis：scan 前缀；内存：遍历 dict）。"""
    client = _get_redis_client()
    if client is not None:
        sessions = []
        async for key in client.scan_iter(match=_REDIS_KEY_PREFIX + "*", count=100):
            sid = key[len(_REDIS_KEY_PREFIX):]
            count = await client.llen(key)
            sessions.append({"id": sid, "message_count": count})
        return sessions
    return [
        {"id": sid, "message_count": len(msgs)}
        for sid, msgs in _memory.items()
    ]


async def get_messages(session_id: str) -> list[dict]:
    """取回一个会话的全部消息。"""
    client = _get_redis_client()
    if client is not None:
        raw = await client.lrange(_REDIS_KEY_PREFIX + session_id, 0, -1)
        return [json.loads(x) for x in raw]
    return _memory.get(session_id, [])


@router.get("/sessions")
async def list_sessions_endpoint():
    return {"sessions": await list_sessions()}


@router.get("/sessions/{session_id}/messages")
async def get_messages_endpoint(session_id: str):
    return {"messages": await get_messages(session_id)}
