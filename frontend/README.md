# frontend — db-agent Web UI

React + TypeScript + Vite。通过 SSE 消费 FastAPI（`server/main.py`）的 multi-agent 执行流。

## 功能

- 对话区流式输出（`useSSE` + `useChat`）
- 思考步骤（Router / SQL / Analysis 等节点进度）
- HITL 审批弹窗（敏感 SQL / 置信度门）
- ECharts 图表（后端 `charts` 字段）
- 👍/👎 反馈 → `POST /api/feedback`（点赞回流 few-shot + Opik 打分）
- 侧边栏数据源选择（Demo SQLite / 上传 CSV 选项）

## 本地开发

先起 API，再起前端。Opik UI 默认占 `5173`，前端请用 `3000`（后端 CORS 已放行）。

```bash
# 仓库根目录
uv run uvicorn server.main:app --reload --port 8000

cd frontend
npm install
npm run dev -- --port 3000
```

`vite.config.ts` 把 `/api` 代理到 `http://localhost:8000`。

## 构建（由 FastAPI 托管静态文件）

```bash
npm run build   # 产出 dist/
```

`server/main.py` 在 `frontend/dist` 存在时挂载为 `/`。

## 目录

```
src/
  App.tsx
  hooks/useChat.ts useSSE.ts
  components/
    ChatArea / ChatInput / MessageBubble / ThinkingSteps
    HITLDialog / FeedbackButtons / ChartPanel
    Sidebar / DataSourceSelector
  types/index.ts
```
