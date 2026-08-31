"""POST /api/query — SSE streaming endpoint for natural language queries.

SSE 断流处理：
- 每轮循环检测 `request.is_disconnected()`，客户端断线立即停止推送；
- 断线时取消后台 Agent 任务（不再白烧 token），runner 保留在注册表供 HITL resume；
- 无事件时每 HEARTBEAT_INTERVAL 秒发心跳，防止代理/网络设备掐断长连接。
"""

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from harness.constraints.entitlement import reset_request_user, set_request_user
from server.main import DEFAULT_MODEL, get_client
from server.runner_wrapper import StreamingRunner, runner_registry
from server.sse import SSEEvent, format_sse

router = APIRouter()

# 无事件时的心跳间隔（秒）：既保活，也让断连能被及时发现
HEARTBEAT_INTERVAL = 15.0

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class QueryRequest(BaseModel):
    query: str
    session_id: str = "default"
    datasource: str = "sqlite"
    model: str | None = None
    user_id: str = "viewer"  # RBAC 用户（对应 agent_users）
    enable_dq: bool = False  # 是否注入 DataQuality Agent


class ResumeRequest(BaseModel):
    query_id: str = ""  # 前端兼容字段；实际按 session_id 定位 runner
    approved: bool = True
    session_id: str = "default"  # 按会话定位 runner，避免全局 active 串线


def _heartbeat_chunk() -> str:
    return format_sse("heartbeat", {"ts": time.time()})


async def _drain_and_stream(queue: asyncio.Queue, request: Request, state: dict):
    """从 queue 转发事件到 SSE；带断连检测与心跳。

    通过 state["completed"] 反馈是否正常完成（收到 done_sentinel），
    供调用方决定是等后台任务收尾还是直接取消。
    """
    state["completed"] = False
    while True:
        if await request.is_disconnected():
            return
        try:
            item = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_INTERVAL)
        except TimeoutError:
            yield _heartbeat_chunk()  # 没有新事件也发心跳保活
            continue
        if item[0] == "done_sentinel":
            state["completed"] = True
            return
        yield format_sse(item[0], item[1])


async def _run_and_collect(coro_factory, queue: asyncio.Queue):
    """后台执行 Agent 任务，把事件喂进 queue；异常转 error 事件。"""
    try:
        await coro_factory()
    except Exception as e:
        name = type(e).__name__
        msg = str(e) or name
        if name in ("APIConnectionError", "APITimeoutError") or "Connection error" in msg:
            msg = (
                "无法连接大模型 API（网络/代理/密钥或 DeepSeek 服务异常）。"
                "请检查 ANTHROPIC_BASE_URL / API Key，以及本机能否访问 api.deepseek.com。"
            )
        await queue.put(("error", SSEEvent.error(msg, name)))
    finally:
        await queue.put(("done_sentinel", None))


async def _stream_query(request: Request, req: QueryRequest, query_id: str,
                      registry, client) -> AsyncIterator[str]:
    """单次查询的完整 SSE 事件流：先推 connected → 创建 runner → 后台执行 → 断连感知转发。"""
    queue: asyncio.Queue = asyncio.Queue()
    model = req.model or DEFAULT_MODEL
    user_token = set_request_user(req.user_id)

    # 先推首包，避免冷启动 create runner 堵住 TTFF
    yield format_sse("connected", {
        "query_id": query_id,
        "session_id": req.session_id,
        "user_id": req.user_id,
        "enable_dq": req.enable_dq,
    })
    t_init = time.time()
    yield format_sse("step_start", SSEEvent.step_start("init", "初始化会话"))

    try:
        runner = await registry.get_or_create(
            session_id=req.session_id,
            client=client,
            model=model,
            enable_data_quality=req.enable_dq,
            user_id=req.user_id,
        )

        yield format_sse(
            "step_end",
            SSEEvent.step_end("init", "就绪", time.time() - t_init),
        )

        # run_streaming / resume_streaming 在 StreamingRunner 上（包装 MultiAgentRunner 发 SSE 事件），
        # 不能直接在 MultiAgentRunner 上调——回归：此前漏掉包装直接调 runner.run_streaming 必崩。
        streaming = StreamingRunner(runner)
        bg_task = asyncio.create_task(
            _run_and_collect(lambda: streaming.run_streaming(req.query, queue), queue)
        )
        state: dict = {}
        try:
            async for chunk in _drain_and_stream(queue, request, state):
                yield chunk
            # 正常结束：等后台任务收尾；断连则跳过（finally 里会取消）
            if state.get("completed") and not bg_task.done():
                await bg_task
        finally:
            # 断连/异常：取消后台 Agent 任务，避免孤儿任务继续烧 token
            if not bg_task.done():
                bg_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await bg_task
            registry.touch(req.session_id)  # 保留 runner 供 HITL resume
    finally:
        reset_request_user(user_token)


async def _stream_resume(request: Request, req: ResumeRequest, registry) -> AsyncIterator[str]:
    """HITL 恢复的事件流：按 session_id 定位 runner，断连感知转发。"""
    queue: asyncio.Queue = asyncio.Queue()
    runner = registry.get(req.session_id)

    if runner is None:
        yield format_sse("error", SSEEvent.error("没有可恢复的查询，请重新发起"))
        return

    bg_task = asyncio.create_task(
        _run_and_collect(
            lambda: StreamingRunner(runner).resume_streaming(req.approved, queue), queue
        )
    )
    state: dict = {}
    try:
        async for chunk in _drain_and_stream(queue, request, state):
            yield chunk
        if state.get("completed") and not bg_task.done():
            await bg_task
    finally:
        if not bg_task.done():
            bg_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await bg_task
        registry.touch(req.session_id)


@router.post("/query")
async def run_query(request: Request, req: QueryRequest):
    """SSE streaming endpoint. Returns text/event-stream."""
    query_id = uuid.uuid4().hex[:8]

    async def event_stream():
        async for chunk in _stream_query(request, req, query_id, runner_registry, get_client()):
            yield chunk

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.post("/query/resume")
async def resume_query(request: Request, req: ResumeRequest):
    """Resume a HITL-paused query. Returns SSE stream continuation."""

    async def event_stream():
        async for chunk in _stream_resume(request, req, runner_registry):
            yield chunk

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=_SSE_HEADERS)
