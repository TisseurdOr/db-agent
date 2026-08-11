"""GET /api/sessions — list and retrieve chat sessions."""

from fastapi import APIRouter

router = APIRouter()

# In-memory session store (production would use DB)
_sessions: dict[str, list[dict]] = {}


def record_message(session_id: str, role: str, content: str, trace_id: str = ""):
    if session_id not in _sessions:
        _sessions[session_id] = []
    _sessions[session_id].append({
        "role": role,
        "content": content[:500],
        "trace_id": trace_id,
    })


@router.get("/sessions")
async def list_sessions():
    return {
        "sessions": [
            {"id": sid, "message_count": len(msgs)}
            for sid, msgs in _sessions.items()
        ]
    }


@router.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str):
    messages = _sessions.get(session_id, [])
    return {"messages": messages}
