# db-agent Web 部署镜像（本地验证 + Hugging Face Spaces Docker SDK 通用）
#
# 本地验证:
#   docker build -t db-agent .
#   docker run --rm -p 8100:7860 -e ANTHROPIC_API_KEY=... -e ANTHROPIC_BASE_URL=... db-agent
#   open http://127.0.0.1:8100
#
# 说明: 前端 build 成静态文件，由 FastAPI 同源 serve（前端用相对 /api 调用，无需 CORS）。

# ── 阶段 1：构建前端（React + Vite → 静态文件）────────────────────
FROM node:24-alpine AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ── 阶段 2：后端运行（FastAPI 同源 serve 前端 dist）─────────────────
FROM python:3.12-slim
WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 依赖层：只 COPY 锁文件，源码变更不重装依赖（缓存友好）
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 源码 + 前端构建产物
COPY . .
COPY --from=frontend /fe/dist /app/frontend/dist

RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"

RUN mkdir -p /app/db /app/harness/memory/chroma_db

# 观测默认关（生产用环境变量打开）；Redis checkpointer 连不上会自动降级 SQLite
ENV OPIK_ENABLED=0 LANGFUSE_ENABLED=0

EXPOSE 7860

# HF Spaces 注入 PORT（默认 7860）；本地 docker run 可用 -e PORT=8100 覆盖
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${PORT:-7860}/api/health')" || exit 1

CMD ["sh", "-c", "exec uvicorn server.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
