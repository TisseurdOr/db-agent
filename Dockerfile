# db-agent Dockerfile
# Build: docker build -t db-agent .
# Run:   docker compose run --rm db-agent

FROM python:3.12-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 依赖只认锁文件（uv.lock），不手写清单——pyproject 加新依赖不会在这里漂移
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy source
COPY . .

# 安装项目本体（生成 db-agent 命令）
RUN uv sync --frozen --no-dev

# 让 .venv 里的可执行文件进 PATH（db-agent 命令在这里）
ENV PATH="/app/.venv/bin:$PATH"

# Create data directories
RUN mkdir -p /app/db /app/harness/memory/chroma_db

ENTRYPOINT ["db-agent"]
CMD ["--mode", "single", "--user", "viewer"]
