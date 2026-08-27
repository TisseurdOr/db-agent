"""SSE event types and formatting for server-sent events."""

import json
import time


def format_sse(event: str, data: dict) -> str:
    """Format a dict as an SSE message string."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class SSEEvent:
    """Factory for SSE event dicts. Each classmethod returns a dict ready for format_sse()."""

    @staticmethod
    def step_start(node: str, task: str) -> dict:
        return {"type": "step_start", "node": node, "task": task, "timestamp": time.time()}

    @staticmethod
    def step_end(node: str, task: str, elapsed: float, tokens: int = 0) -> dict:
        return {"type": "step_end", "node": node, "task": task, "elapsed": round(elapsed, 3), "tokens": tokens}

    @staticmethod
    def text_delta(text: str, node: str = "") -> dict:
        return {"type": "text_delta", "text": text, "node": node}

    @staticmethod
    def interrupt(data: dict) -> dict:
        return {"type": "interrupt", "data": data}

    @staticmethod
    def done(
        total_elapsed: float,
        trace_id: str = "",
        sql: str = "",
        answer: str = "",
        plan: list | None = None,
        charts: list | None = None,
        stats: dict | None = None,
        opik_trace_id: str = "",
        tokens: int = 0,
    ) -> dict:
        return {
            "type": "done",
            "total_elapsed": round(total_elapsed, 3),
            "trace_id": trace_id,
            "opik_trace_id": opik_trace_id,
            "sql": sql,
            "answer": answer,
            "plan": plan or [],
            "charts": charts or [],
            "stats": stats or {},
            "tokens": int(tokens or 0),
        }

    @staticmethod
    def error(message: str, detail: str = "") -> dict:
        return {"type": "error", "message": message, "detail": detail}
