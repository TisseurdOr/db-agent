"""POST /api/query — SSE streaming endpoint for natural language queries."""

import asyncio
import json
import time
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from server.main import client, DEFAULT_MODEL
from server.runner_wrapper import StreamingRunner, runner_registry
from server.sse import SSEEvent, format_sse

router = APIRouter()


class QueryRequest(BaseModel):
    query: str
    session_id: str = "default"
    datasource: str = "sqlite"
    model: str | None = None


class ResumeRequest(BaseModel):
    query_id: str
    approved: bool = True


@router.post("/query")
async def run_query(req: QueryRequest):
    """SSE streaming endpoint. Returns text/event-stream."""
    query_id = uuid.uuid4().hex[:8]

    async def event_stream():
        queue: asyncio.Queue = asyncio.Queue()
        model = req.model or DEFAULT_MODEL

        # Get or create runner for this session
        runner = await runner_registry.get_or_create(
            session_id=req.session_id,
            client=client,
            model=model,
        )

        # Emit preamble
        yield format_sse("connected", {"query_id": query_id, "session_id": req.session_id})

        # Run query in background task, feeding events to queue
        async def run_and_collect():
            try:
                await runner.run_streaming(req.query, queue)
            except Exception as e:
                await queue.put(("error", SSEEvent.error(str(e), type(e).__name__)))
            finally:
                await queue.put(("done_sentinel", None))

        bg_task = asyncio.create_task(run_and_collect())

        # Stream events from queue to client
        while True:
            item = await queue.get()
            if item[0] == "done_sentinel":
                break
            event_type, data = item
            yield format_sse(event_type, data)

        await bg_task

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/query/resume")
async def resume_query(req: ResumeRequest):
    """Resume a HITL-paused query. Returns SSE stream continuation."""

    async def event_stream():
        queue: asyncio.Queue = asyncio.Queue()
        runner = runner_registry.get_active()

        if runner is None:
            yield format_sse("error", SSEEvent.error("No active query to resume"))
            return

        async def resume_and_collect():
            try:
                await runner.resume_streaming(req.approved, queue)
            except Exception as e:
                await queue.put(("error", SSEEvent.error(str(e), type(e).__name__)))
            finally:
                await queue.put(("done_sentinel", None))

        bg_task = asyncio.create_task(resume_and_collect())

        while True:
            item = await queue.get()
            if item[0] == "done_sentinel":
                break
            event_type, data = item
            yield format_sse(event_type, data)

        await bg_task

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
