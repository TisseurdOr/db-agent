"""POST /api/feedback — store user ratings and wire to self-learning + Opik."""

from fastapi import APIRouter
from pydantic import BaseModel

from server.storage import save_feedback

router = APIRouter()


class FeedbackRequest(BaseModel):
    trace_id: str = ""
    opik_trace_id: str = ""
    session_id: str = ""
    query: str
    answer: str
    rating: str  # "up" | "down"
    comment: str = ""
    sql: str = ""


@router.post("/feedback")
async def submit_feedback(req: FeedbackRequest):
    fid = save_feedback(
        trace_id=req.trace_id,
        session_id=req.session_id,
        query=req.query,
        answer=req.answer,
        rating=req.rating,
        comment=req.comment,
        sql=req.sql,
    )

    # Wire thumbs-up to self-learning feedback loop
    if req.rating == "up":
        try:
            from rag.feedback import learn_from_success
            learn_from_success(req.query, result_text=req.answer, sql=req.sql or None, source="user")
        except Exception:
            pass  # self-learning is best-effort

    from utils.opik_tracing import log_user_feedback
    opik_result = log_user_feedback(
        trace_ref=req.opik_trace_id or req.trace_id,
        rating=req.rating,
        comment=req.comment,
    )

    return {"ok": True, "id": fid, "opik": opik_result}
