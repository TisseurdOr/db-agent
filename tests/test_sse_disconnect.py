"""SSE 断流处理测试。

覆盖：
- _drain_and_stream：心跳保活 + 断连立即停止 + 正常完成标记
- _stream_query：客户端断连 → 后台 Agent 任务被取消（不白烧 token）
- _stream_resume：按 session_id 恢复；无 runner 返回可读错误
- RunnerRegistry：TTL 空闲回收、按 session 查询、remove 关闭
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

# 先导入 server.main 建立正确加载顺序（query 与 main 存在循环引用，须经 main 入口加载）
import server.main  # noqa: F401

from server.endpoints.query import (
    QueryRequest, ResumeRequest,
    _drain_and_stream, _stream_query, _stream_resume,
    HEARTBEAT_INTERVAL,
)
from server.runner_wrapper import RunnerRegistry


class _FakeRequest:
    def __init__(self, disconnected: bool = False, delay: float = 0.0):
        self._disconnected = disconnected
        self._delay = delay

    async def is_disconnected(self):
        if self._delay:
            await asyncio.sleep(self._delay)  # 让出控制权，确保后台任务已启动
        return self._disconnected


def _make_fake_runner():
    runner = MagicMock()
    runner.aclose = AsyncMock()
    return runner


async def _collect(agen):
    chunks = []
    async for c in agen:
        chunks.append(c)
    return chunks


# ═══ 1. _drain_and_stream ═══════════════════════════════════════════


@pytest.mark.asyncio
async def test_drain_emits_heartbeat_when_idle(monkeypatch):
    monkeypatch.setattr("server.endpoints.query.HEARTBEAT_INTERVAL", 0.02)
    queue: asyncio.Queue = asyncio.Queue()
    state: dict = {}

    async def feed():
        await asyncio.sleep(0.06)  # 超过心跳间隔
        await queue.put(("text_delta", {"text": "hi"}))
        await queue.put(("done_sentinel", None))

    feed_task = asyncio.create_task(feed())
    chunks = await _collect(_drain_and_stream(queue, _FakeRequest(disconnected=False), state))
    await feed_task

    assert any("heartbeat" in c for c in chunks), "应该有心跳"
    assert any("text_delta" in c for c in chunks)
    assert state["completed"] is True


@pytest.mark.asyncio
async def test_drain_stops_on_disconnect():
    queue: asyncio.Queue = asyncio.Queue()
    state: dict = {}
    chunks = await _collect(_drain_and_stream(queue, _FakeRequest(disconnected=True), state))
    assert chunks == []
    assert state["completed"] is False


# ═══ 2. _stream_query：断连 → 取消后台任务 ═══════════════════════════


@pytest.mark.asyncio
async def test_stream_query_cancels_bg_task_on_disconnect(monkeypatch):
    registry = RunnerRegistry()
    runner = _make_fake_runner()
    cancelled = asyncio.Event()

    async def run_streaming(query, queue):
        await asyncio.sleep(0)  # 确保任务已启动
        try:
            await asyncio.Event().wait()  # 模拟长任务
        except asyncio.CancelledError:
            cancelled.set()
            raise

    runner.run_streaming = run_streaming
    async def fake_create(client, model, enable_data_quality, thread_id):
        return runner
    monkeypatch.setattr("server.runner_wrapper.MultiAgentRunner.create", fake_create)

    req = QueryRequest(query="查一下销售额", session_id="s1")
    # delay 确保后台任务先启动，再断连——模拟"长任务进行中客户端断开"
    chunks = await _collect(_stream_query(_FakeRequest(disconnected=True, delay=0.05), req, "qid1", registry, MagicMock()))

    assert any("connected" in c for c in chunks)
    assert cancelled.is_set(), "断连后后台任务应被取消"
    # runner 保留在注册表（供 HITL resume），且活跃时间被 touch
    assert registry.get("s1") is runner


@pytest.mark.asyncio
async def test_stream_query_normal_completion(monkeypatch):
    registry = RunnerRegistry()
    runner = _make_fake_runner()

    async def run_streaming(query, queue):
        await queue.put(("text_delta", {"text": "答案"}))
        await queue.put(("done_sentinel", None))

    runner.run_streaming = run_streaming
    async def fake_create(client, model, enable_data_quality, thread_id):
        return runner
    monkeypatch.setattr("server.runner_wrapper.MultiAgentRunner.create", fake_create)

    req = QueryRequest(query="q", session_id="s1")
    chunks = await _collect(_stream_query(_FakeRequest(disconnected=False), req, "qid1", registry, MagicMock()))

    assert any("connected" in c for c in chunks)
    assert any("text_delta" in c and "答案" in c for c in chunks)
    assert registry.get("s1") is runner  # 正常结束也不回收（等待 TTL）


# ═══ 3. _stream_resume：按 session 恢复 ═══════════════════════════════


@pytest.mark.asyncio
async def test_resume_without_runner_returns_error():
    registry = RunnerRegistry()
    req = ResumeRequest(query_id="q1", approved=True, session_id="ghost")
    chunks = await _collect(_stream_resume(_FakeRequest(disconnected=False), req, registry))
    assert any("error" in c for c in chunks)
    assert any("没有可恢复" in c for c in chunks)


@pytest.mark.asyncio
async def test_resume_uses_session_runner(monkeypatch):
    registry = RunnerRegistry()
    runner = _make_fake_runner()

    async def resume_streaming(approved, queue):
        await queue.put(("text_delta", {"text": "已批准继续"}))
        await queue.put(("done_sentinel", None))

    runner.resume_streaming = resume_streaming
    async def fake_create(client, model, enable_data_quality, thread_id):
        return runner
    monkeypatch.setattr("server.runner_wrapper.MultiAgentRunner.create", fake_create)
    await registry.get_or_create("s1", MagicMock(), "m")

    req = ResumeRequest(query_id="q1", approved=True, session_id="s1")
    chunks = await _collect(_stream_resume(_FakeRequest(disconnected=False), req, registry))
    assert any("已批准继续" in c for c in chunks)


# ═══ 4. RunnerRegistry：TTL 回收 / 按会话查询 ════════════════════════


@pytest.mark.asyncio
async def test_registry_sweeps_expired_runners():
    registry = RunnerRegistry()
    old_runner = _make_fake_runner()
    fresh_runner = _make_fake_runner()
    now = time.monotonic()
    registry._runners = {
        "old": (old_runner, now - registry.RUNNER_TTL_SECONDS - 10),  # 已过期
        "fresh": (fresh_runner, now),
    }
    await registry._sweep(now)

    assert "old" not in registry._runners
    old_runner.aclose.assert_awaited_once()
    assert registry.get("fresh") is fresh_runner


@pytest.mark.asyncio
async def test_registry_remove_closes_runner():
    registry = RunnerRegistry()
    runner = _make_fake_runner()
    registry._runners["s1"] = (runner, time.monotonic())

    await registry.remove("s1")
    assert registry.get("s1") is None
    runner.aclose.assert_awaited_once()


def test_registry_get_returns_none_for_unknown():
    assert RunnerRegistry().get("nope") is None
