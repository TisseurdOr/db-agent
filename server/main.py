"""FastAPI server for db-agent — wraps MultiAgentRunner with SSE streaming."""

import os
import sys
from pathlib import Path

# Ensure project root is on path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

# Opik SDK 在缺 URL 时会交互式询问；本地默认写上，避免卡死启动
os.environ.setdefault("OPIK_URL_OVERRIDE", "http://localhost:5173/api")
os.environ.setdefault("OPIK_PROJECT_NAME", "db-agent")

from anthropic import Anthropic
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from harness.bootstrap import bootstrap_data
from harness.config import DEFAULT_MODEL
from harness.llm_client import get_anthropic_client
from server.storage import init_feedback_db

# ── Bootstrap ──────────────────────────────────────────────────────────

bootstrap_data()
init_feedback_db()

# Anthropic client 惰性创建：import server.main 不应依赖 API key，
# 否则 CI / 无 .env 环境连 test_server 都无法收集。首次真正处理查询时才创建。
_client: Anthropic | None = None


def get_client() -> Anthropic:
    """按需创建 Anthropic client（支持 DeepSeek 兼容 endpoint）。"""
    global _client
    if _client is None:
        _client = get_anthropic_client()
    return _client


# ── FastAPI app ────────────────────────────────────────────────────────

from contextlib import asynccontextmanager


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Trace 按日文件 retention（默认 30 天）
    try:
        from harness.observation.tracer import cleanup_expired_traces
        cleanup_expired_traces()
    except Exception:
        pass
    # 预热向量记忆栈（search_memory / pre-turn recall）+ 默认会话 runner
    try:
        from harness.memory.preturn_recall import ensure_memory_stack
        ensure_memory_stack(get_client())
    except Exception:
        pass
    try:
        from server.runner_wrapper import runner_registry
        await runner_registry.get_or_create(
            session_id="default",
            client=get_client(),
            model=DEFAULT_MODEL,
        )
    except Exception:
        pass
    yield
    try:
        from server.runner_wrapper import runner_registry
        await runner_registry.close_all()
    except Exception:
        pass


app = FastAPI(title="db-agent API", version="0.1.0", lifespan=_lifespan)

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


@app.get("/api/metrics")
async def metrics():
    """Prometheus 文本格式运维指标（开放，供抓取）。"""
    from fastapi.responses import PlainTextResponse

    from harness.observation.ops_metrics import prometheus_text
    return PlainTextResponse(prometheus_text(), media_type="text/plain; version=0.0.4")


# ── Router registration (deferred to avoid circular imports) ───────────

# 业务接口统一加可选鉴权（WEB_API_TOKEN；/api/health 保持开放供探针）
from server.auth import require_auth  # noqa: E402
from server.endpoints.dashboard import router as dashboard_router
from server.endpoints.database import router as database_router
from server.endpoints.datasource import router as datasource_router
from server.endpoints.eval import router as eval_router
from server.endpoints.feedback import router as feedback_router
from server.endpoints.memory import router as memory_router
from server.endpoints.ops import router as ops_router
from server.endpoints.overview import router as overview_router
from server.endpoints.query import router as query_router
from server.endpoints.rbac import router as rbac_router
from server.endpoints.sessions import router as sessions_router

app.include_router(dashboard_router, prefix="/api", dependencies=[Depends(require_auth)])
app.include_router(query_router, prefix="/api", dependencies=[Depends(require_auth)])
app.include_router(overview_router, prefix="/api", dependencies=[Depends(require_auth)])
app.include_router(ops_router, prefix="/api", dependencies=[Depends(require_auth)])
app.include_router(eval_router, prefix="/api", dependencies=[Depends(require_auth)])
app.include_router(rbac_router, prefix="/api", dependencies=[Depends(require_auth)])
app.include_router(feedback_router, prefix="/api", dependencies=[Depends(require_auth)])
app.include_router(sessions_router, prefix="/api", dependencies=[Depends(require_auth)])
app.include_router(datasource_router, prefix="/api", dependencies=[Depends(require_auth)])
app.include_router(database_router, prefix="/api", dependencies=[Depends(require_auth)])
app.include_router(memory_router, prefix="/api", dependencies=[Depends(require_auth)])

# ── Chart dashboard HTML (render_chart 产物) ───────────────────────────

from harness.tools.chart import CHART_DIR

os.makedirs(CHART_DIR, exist_ok=True)
app.mount("/charts", StaticFiles(directory=CHART_DIR), name="charts")

# ── Static file serving (React build output) ───────────────────────────

frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="static")
