"""Redis 状态外置测试。

- 无 Redis 服务时自动跳过（CI 无 Redis 也能全绿）
- 会话存储：record/list/get 往返 + 存到 Redis 而非内存
- checkpoint：MultiAgentRunner 配置 REDIS_URL 时用 RedisSaver，缺省回落 SQLite

测试用独立的 Redis DB（index 15），测试结束 flush，绝不碰业务数据。
"""

import os

import pytest

# 测试专用 Redis：独立 DB 15，避免污染本机其他数据
REDIS_TEST_URL = os.getenv("REDIS_TEST_URL", "redis://localhost:6379/15")


def _redis_available() -> bool:
    try:
        import redis
        client = redis.Redis.from_url(REDIS_TEST_URL, socket_connect_timeout=1)
        client.ping()
        client.close()
        return True
    except Exception:
        return False


def _redis_search_available() -> bool:
    """探测 Redis 是否加载了 RediSearch 模块（checkpoint 索引需要）。"""
    try:
        import redis
        client = redis.Redis.from_url(REDIS_TEST_URL, socket_connect_timeout=1)
        modules = client.execute_command("MODULE", "LIST")
        client.close()
        return any("search" in str(m).lower() for m in modules)
    except Exception:
        return False


requires_redis = pytest.mark.skipif(
    not _redis_available(),
    reason="需要本地 Redis 服务才能跑 Redis 状态外置测试",
)

# checkpoint 用官方 RedisSaver，需要 RediSearch（redis-stack）；普通 Redis 自动降级 SQLite
requires_redis_search = pytest.mark.skipif(
    not _redis_search_available(),
    reason="checkpoint 的 RedisSaver 需要 RediSearch 模块（redis-stack）；"
           "普通 Redis 会自动降级到 SQLite",
)


# ═══ 会话存储 → Redis ═══════════════════════════════════════════════


@requires_redis
@pytest.mark.asyncio
async def test_session_store_redis_roundtrip(monkeypatch):
    from server.endpoints import sessions as sessions_mod

    monkeypatch.setenv("REDIS_URL", REDIS_TEST_URL)
    sessions_mod._redis_client = None  # 强制按新 env 重建客户端

    client = sessions_mod._get_redis_client()
    try:
        await client.flushdb()  # 测试库清空
        await sessions_mod.record_message("s_redis", "user", "你好")
        await sessions_mod.record_message("s_redis", "assistant", "回复内容", trace_id="t1")

        msgs = await sessions_mod.get_messages("s_redis")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["trace_id"] == "t1"

        sessions = await sessions_mod.list_sessions()
        match = [s for s in sessions if s["id"] == "s_redis"]
        assert len(match) == 1 and match[0]["message_count"] == 2
    finally:
        await client.flushdb()
        await client.aclose()
        sessions_mod._redis_client = None


@requires_redis
@pytest.mark.asyncio
async def test_session_store_not_in_memory_when_redis(monkeypatch):
    """Redis 模式下消息不写内存 dict。"""
    from server.endpoints import sessions as sessions_mod

    monkeypatch.setenv("REDIS_URL", REDIS_TEST_URL)
    sessions_mod._redis_client = None
    client = sessions_mod._get_redis_client()
    try:
        await client.flushdb()
        await sessions_mod.record_message("s_redis_mem", "user", "x")
        assert sessions_mod._memory.get("s_redis_mem") is None  # 没进内存
    finally:
        await client.flushdb()
        await client.aclose()
        sessions_mod._redis_client = None


# ═══ checkpoint → Redis ═════════════════════════════════════════════


# RediSearch 只允许在 DB 0 建索引，且测试要用独立 key 前缀，避免污染应用数据
CHECKPOINT_TEST_URL = os.getenv("REDIS_CHECKPOINT_TEST_URL", "redis://localhost:6380/0")
CHECKPOINT_TEST_PREFIX = "test_ckpt_"


def _cleanup_checkpoint_keys():
    """清理测试 checkpoint 留下的 key 和 RediSearch 索引。"""
    try:
        import redis
        client = redis.Redis.from_url(CHECKPOINT_TEST_URL, socket_connect_timeout=1)
        for key in client.scan_iter(match=CHECKPOINT_TEST_PREFIX + "*", count=100):
            client.delete(key)
        try:
            client.execute_command("FT.DROPINDEX", "checkpoint")
        except Exception:
            pass
        client.close()
    except Exception:
        pass


@requires_redis_search
@pytest.mark.asyncio
async def test_checkpoint_uses_redis_saver(monkeypatch):
    from unittest.mock import MagicMock

    from langgraph.checkpoint.redis.aio import AsyncRedisSaver

    from harness.orchestration.multi.orchestrator import MultiAgentRunner

    monkeypatch.setenv("REDIS_URL", "redis://localhost:6380/0")
    _cleanup_checkpoint_keys()
    runner = await MultiAgentRunner.create(
        MagicMock(), model="test", thread_id="redis-checkpoint-test",
        checkpoint_redis_url=CHECKPOINT_TEST_URL,
        checkpoint_prefix=CHECKPOINT_TEST_PREFIX,
    )
    try:
        assert isinstance(runner.checkpointer, AsyncRedisSaver)
        assert runner.checkpoint_db == CHECKPOINT_TEST_URL
    finally:
        await runner.aclose()
        _cleanup_checkpoint_keys()


@pytest.mark.asyncio
async def test_checkpoint_falls_back_to_sqlite_without_redis(monkeypatch):
    """未配置 REDIS_URL → 保持 SQLite checkpoint（默认行为）。"""
    from unittest.mock import MagicMock

    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from harness.orchestration.multi.orchestrator import MultiAgentRunner

    monkeypatch.delenv("REDIS_URL", raising=False)
    runner = await MultiAgentRunner.create(
        MagicMock(), model="test", thread_id="sqlite-checkpoint-test",
    )
    try:
        assert isinstance(runner.checkpointer, AsyncSqliteSaver)
    finally:
        await runner.aclose()
