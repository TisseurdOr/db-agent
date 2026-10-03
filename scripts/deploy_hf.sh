#!/usr/bin/env bash
# 干净部署到 Hugging Face Spaces：只带代码，不带个人文档 / git 历史 / 真实 key
#
# 用法:
#   1) huggingface-cli login              # 首次登录 HF（或用 token 配 git 凭据）
#   2) 在 HF 上先建好 Space（Docker → Blank）
#   3) HF_USER=你的用户名 ./scripts/deploy_hf.sh <space名>
#
# 说明: 建一个全新的临时 git 仓库（无历史），rsync 只同步部署必需内容，
#       force push 到 HF Space 的 main。个人/课程/运行产物一律不带。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SPACE="${1:-}"
HF_USER="${HF_USER:-}"

if [[ -z "$SPACE" || -z "$HF_USER" ]]; then
  echo "用法: HF_USER=你的用户名 $0 <space名>" >&2
  exit 1
fi

URL="https://huggingface.co/spaces/${HF_USER}/${SPACE}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# 只同步部署必需；排除个人/课程/运行产物（隐私 + 体积）
rsync -a \
  --exclude '.git' --exclude '.venv' --exclude 'node_modules' --exclude '.env' \
  --exclude 'docs' --exclude 'tests' \
  --exclude 'Asu' --exclude 'blog-draft' --exclude 'learn' --exclude 'skills' \
  --exclude 'templates' --exclude '.cursor' \
  --exclude '.dev-logs' --exclude '.dev-pids' \
  --exclude 'db/turns' --exclude 'db/conversation_*.json' --exclude '*.db' \
  "$ROOT"/ "$TMP"/

# HF Space 页面需要的 metadata（app_port 与 Dockerfile 里的 7860 对齐）
cat > "$TMP/README.md" <<'EOF'
---
title: db-agent
sdk: docker
app_port: 7860
pinned: false
---

# db-agent

多 Agent 数据分析平台（NL2SQL + 血缘可视化）。详见仓库 README。
EOF

cd "$TMP"
git init -q
git add -A
git commit -q -m "deploy db-agent to HF Spaces"

git remote add space "$URL"
git push -f space main:main

echo "✓ 已推送: $URL"
echo "  下一步: HF Space → Settings → Secrets 配 ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL"
