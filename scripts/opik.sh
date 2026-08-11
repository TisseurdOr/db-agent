#!/usr/bin/env bash
# Start/stop local Opik for db-agent observability.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/opik-platform"
case "${1:-up}" in
  up|start)
    docker compose --profile opik up -d
    echo "Opik UI: http://localhost:5173  (project: db-agent)"
    echo "Ensure .env has OPIK_ENABLED=1"
    ;;
  down|stop)
    docker compose --profile opik down
    ;;
  status|ps)
    docker compose --profile opik ps
    ;;
  *)
    echo "usage: $0 {up|down|status}"
    exit 1
    ;;
esac
