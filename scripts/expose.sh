#!/usr/bin/env bash
# 用 ngrok 把 db-agent 临时暴露到公网（面试 demo 用）
#
# 用法:
#   ./scripts/expose.sh
#   → 打印一个 https://xxx.ngrok-free.dev 公网链接，面试时甩给面试官
#   → Ctrl+C 停止隧道；容器继续跑，可 docker stop db-agent 清理
#
# 固定域名（可选，去 ngrok dashboard 领免费 dev domain 后）:
#   FIXED_DOMAIN=你的名字.ngrok-free.app ./scripts/expose.sh
#
# 依赖: Docker 镜像 db-agent:latest（先 docker build -t db-agent .）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST_PORT="${HOST_PORT:-7860}"
FIXED_DOMAIN="${FIXED_DOMAIN:-}"
NAME="db-agent"

# 1. 检查 ngrok
if ! command -v ngrok >/dev/null 2>&1; then
  echo "✗ 未装 ngrok：brew install ngrok && ngrok config add-authtoken <token>" >&2
  exit 1
fi

# 2. 启动容器（已存在就复用）
if docker ps -a --format '{{.Names}}' | grep -q "^${NAME}$"; then
  docker start "$NAME" >/dev/null
  echo "▶ 复用已有容器 ${NAME}"
else
  echo "▶ 启动容器 ${NAME} (127.0.0.1:${HOST_PORT} -> 7860)..."
  docker run -d --name "$NAME" -p "127.0.0.1:${HOST_PORT}:7860" \
    --env-file "$ROOT/.env" db-agent
fi

# 3. 等健康
echo "▶ 等后端就绪..."
for i in $(seq 1 60); do
  if curl -sf "http://127.0.0.1:${HOST_PORT}/api/health" >/dev/null 2>&1; then
    echo "✓ 后端就绪"
    break
  fi
  sleep 0.5
done

# 4. 起 ngrok（后台），从本地 API 拿公网链接
LOG="$(mktemp)"
NGROK_PID=""
cleanup() { [[ -n "$NGROK_PID" ]] && kill "$NGROK_PID" 2>/dev/null; rm -f "$LOG"; }
trap cleanup EXIT

echo "▶ 起 ngrok..."
if [[ -n "$FIXED_DOMAIN" ]]; then
  ngrok http --url="$FIXED_DOMAIN" "$HOST_PORT" >"$LOG" 2>&1 &
else
  ngrok http "$HOST_PORT" >"$LOG" 2>&1 &
fi
NGROK_PID=$!

# 5. 等公网链接
URL=""
for i in $(seq 1 30); do
  URL="$(curl -sf http://127.0.0.1:4040/api/tunnels 2>/dev/null \
    | python3 -c 'import sys,json; print(json.load(sys.stdin)["tunnels"][0]["public_url"])' 2>/dev/null || true)"
  [[ -n "$URL" ]] && break
  sleep 0.5
done

if [[ -z "$URL" ]]; then
  echo "✗ 没拿到公网链接，日志在 $LOG" >&2
  exit 1
fi

echo ""
echo "   ✅ 公网链接: $URL"
echo "   面试官打开此链接（首次会弹 ngrok 提示页，点 Visit Site）"
echo "   Ctrl+C 停止隧道"
echo ""

wait "$NGROK_PID"
