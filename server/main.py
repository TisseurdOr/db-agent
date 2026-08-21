"""FastAPI server for db-agent — wraps MultiAgentRunner with SSE streaming."""

import asyncio
import os
import sys
from pathlib import Path

# Ensure project root is on path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from anthropic import Anthropic

from db.seed import init_db
from harness.tools.hbase import _seed_hbase_store
from harness.context.template_matcher import init_metric_registry
from harness.context.schema_discovery import get_schema_discovery
from server.storage import init_feedback_db

# ── Bootstrap ──────────────────────────────────────────────────────────

init_db()
_seed_hbase_store()
init_metric_registry()
init_feedback_db()
try:
    get_schema_discovery().build_index()
except Exception:
    pass

# Anthropic client 惰性创建：import server.main 不应依赖 API key，
# 否则 CI / 无 .env 环境连 test_server 都无法收集。首次真正处理查询时才创建。
_client: Anthropic | None = None


def get_client() -> Anthropic:
    """按需创建 Anthropic client（支持 DeepSeek 兼容 endpoint）。"""
    global _client
    if _client is None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "缺少 ANTHROPIC_API_KEY（请在 .env 或环境变量中配置）"
            )
        from harness.observation.opik_tracing import wrap_anthropic_client
        _client = wrap_anthropic_client(Anthropic(
            api_key=api_key,
            base_url=os.getenv("ANTHROPIC_BASE_URL"),
        ))
    return _client


DEFAULT_MODEL = os.getenv("ANTHROPIC_MODEL", "deepseek-chat")

# ── FastAPI app ────────────────────────────────────────────────────────

app = FastAPI(title="db-agent API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    return {"status": "ok", "model": DEFAULT_MODEL, "mode": "multi"}


# ── Router registration (deferred to avoid circular imports) ───────────

from server.endpoints.query import router as query_router
from server.endpoints.feedback import router as feedback_router
from server.endpoints.sessions import router as sessions_router
from server.endpoints.datasource import router as datasource_router

app.include_router(query_router, prefix="/api")
app.include_router(feedback_router, prefix="/api")
app.include_router(sessions_router, prefix="/api")
app.include_router(datasource_router, prefix="/api")

# ── Static file serving (React build output) ───────────────────────────

frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="static")
