#!/usr/bin/env bash
# 一键启停本地前后端（后端 :8000 + Vite :3000）
# 用法:
#   ./scripts/dev.sh restart   # 最常用
#   ./scripts/dev.sh start | stop | status
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
LOG_DIR="$ROOT/.dev-logs"
PID_DIR="$ROOT/.dev-pids"
BACKEND_PID_FILE="$PID_DIR/backend.pid"
FRONTEND_PID_FILE="$PID_DIR/frontend.pid"
BACKEND_LOG="$LOG_DIR/backend.log"
FRONTEND_LOG="$LOG_DIR/frontend.log"

mkdir -p "$LOG_DIR" "$PID_DIR"

_load_env() {
  if [[ -f "$ROOT/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$ROOT/.env"
    set +a
  fi
  # 避免 Opik SDK 在无 TTY / 无 URL 时卡在 “Please enter your Opik instance URL”
  export CI="${CI:-1}"
  export OPIK_URL_OVERRIDE="${OPIK_URL_OVERRIDE:-http://localhost:5173/api}"
  export OPIK_PROJECT_NAME="${OPIK_PROJECT_NAME:-db-agent}"
}

_pids_on_port() {
  local port="$1"
  lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | awk 'NR>1{print $2}' | sort -u
}

_all_pids_on_port() {
  # 含 CLOSED 僵尸（只 LISTEN 杀不掉时会 Address already in use）
  local port="$1"
  lsof -nP -iTCP:"$port" 2>/dev/null | awk 'NR>1{print $2}' | sort -u
}

_kill_port() {
  local port="$1"
  local p
  for p in $(_all_pids_on_port "$port"); do
    kill -9 "$p" 2>/dev/null || true
  done
}

_is_running() {
  local pid_file="$1"
  [[ -f "$pid_file" ]] || return 1
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  [[ -n "${pid:-}" ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

cmd_stop() {
  echo "▶ 停止前后端…"
  if _is_running "$BACKEND_PID_FILE"; then
    kill -9 "$(cat "$BACKEND_PID_FILE")" 2>/dev/null || true
  fi
  if _is_running "$FRONTEND_PID_FILE"; then
    # vite 常有子进程；再清端口兜底
    kill -9 "$(cat "$FRONTEND_PID_FILE")" 2>/dev/null || true
  fi
  _kill_port "$BACKEND_PORT"
  _kill_port "$FRONTEND_PORT"
  rm -f "$BACKEND_PID_FILE" "$FRONTEND_PID_FILE"
  echo "✓ 已停止 (:${BACKEND_PORT} / :${FRONTEND_PORT})"
}

cmd_start() {
  _load_env

  if [[ -n "$(_pids_on_port "$BACKEND_PORT")" ]]; then
    echo "⚠ 端口 $BACKEND_PORT 已被占用，先 stop 再 start，或直接 restart"
    exit 1
  fi
  if [[ -n "$(_pids_on_port "$FRONTEND_PORT")" ]]; then
    echo "⚠ 端口 $FRONTEND_PORT 已被占用，先 stop 再 start，或直接 restart"
    exit 1
  fi

  if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
    echo "✗ 找不到 .venv/bin/python，请先在项目根执行: uv sync 或 python -m venv .venv"
    exit 1
  fi

  echo "▶ 启动后端 :$BACKEND_PORT …"
  nohup "$ROOT/.venv/bin/python" -m uvicorn server.main:app \
    --host 127.0.0.1 --port "$BACKEND_PORT" --reload \
    --reload-dir "$ROOT/harness" \
    --reload-dir "$ROOT/server" \
    --reload-dir "$ROOT/db" \
    >"$BACKEND_LOG" 2>&1 &
  echo $! >"$BACKEND_PID_FILE"

  echo "▶ 启动前端 :$FRONTEND_PORT …"
  if [[ ! -d "$ROOT/frontend/node_modules" ]]; then
    echo "  （首次：npm install）"
    (cd "$ROOT/frontend" && npm install) >>"$FRONTEND_LOG" 2>&1
  fi
  nohup npm --prefix "$ROOT/frontend" run dev -- --port "$FRONTEND_PORT" --host 127.0.0.1 \
    >"$FRONTEND_LOG" 2>&1 &
  echo $! >"$FRONTEND_PID_FILE"

  # 等健康检查
  local i
  for i in $(seq 1 40); do
    if curl -sf "http://127.0.0.1:${BACKEND_PORT}/api/health" >/dev/null 2>&1; then
      break
    fi
    sleep 0.5
  done

  cmd_status
  echo
  echo "日志: ${BACKEND_LOG}"
  echo "      ${FRONTEND_LOG}"
  echo "页面: http://127.0.0.1:${FRONTEND_PORT}  （开发；API 代理到 :${BACKEND_PORT}）"
}

cmd_restart() {
  cmd_stop
  sleep 1
  cmd_start
}

cmd_status() {
  local be fe
  if curl -sf "http://127.0.0.1:${BACKEND_PORT}/api/health" >/dev/null 2>&1; then
    be="OK  $(curl -sf "http://127.0.0.1:${BACKEND_PORT}/api/health")"
  else
    be="DOWN"
  fi
  if curl -sf -o /dev/null "http://127.0.0.1:${FRONTEND_PORT}/" 2>/dev/null; then
    fe="OK"
  else
    fe="DOWN"
  fi
  echo "后端 :$BACKEND_PORT  → $be"
  echo "前端 :$FRONTEND_PORT  → $fe"
}

usage() {
  cat <<USAGE
用法: ./scripts/dev.sh <restart|start|stop|status>

  restart  停掉旧进程（含端口僵尸）再拉起前后端   ← 日常用这个
  start    仅启动（端口被占会失败）
  stop     仅停止
  status   看健康状态

环境变量可选: BACKEND_PORT=8000 FRONTEND_PORT=3000
USAGE
}

case "${1:-}" in
  restart) cmd_restart ;;
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  status)  cmd_status ;;
  -h|--help|help|"") usage; [[ -n "${1:-}" ]] || exit 1 ;;
  *) echo "未知命令: $1"; usage; exit 1 ;;
esac
