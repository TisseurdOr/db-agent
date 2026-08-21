# db-agent Dockerfile
# Build: docker build -t db-agent .
# Run:   docker compose run --rm db-agent

FROM python:3.12-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install dependencies with Tsinghua mirror for China network
COPY pyproject.toml uv.lock ./
RUN uv pip install --system --no-cache \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    anthropic \
    aiosqlite \
    chromadb \
    langgraph \
    langgraph-checkpoint-sqlite \
    matplotlib \
    mcp \
    numpy \
    openai \
    python-dotenv \
    fpdf2

# Copy source
COPY . .

# Create data directories
RUN mkdir -p /app/db /app/harness/memory/chroma_db

ENTRYPOINT ["python", "main.py"]
CMD ["--mode", "single", "--user", "viewer"]
