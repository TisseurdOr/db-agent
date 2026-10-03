# db-agent HTTP API

交互式文档（服务启动后）：

| 入口 | URL |
|------|-----|
| **Swagger UI** | [http://localhost:8000/docs](http://localhost:8000/docs) |
| **ReDoc** | [http://localhost:8000/redoc](http://localhost:8000/redoc) |
| **OpenAPI JSON** | [http://localhost:8000/openapi.json](http://localhost:8000/openapi.json) |

```bash
cd /path/to/db-agent
.venv/bin/python -m uvicorn server.main:app --port 8000
# 浏览器打开 http://localhost:8000/docs
```

---

## 鉴权

| 条件 | 行为 |
|------|------|
| 未设置 `WEB_API_TOKEN` | 全部放行（本地 demo 默认） |
| 已设置 `WEB_API_TOKEN` | 除下表「开放」接口外，需带 Token |

```http
Authorization: Bearer <WEB_API_TOKEN>
# 或
X-API-Key: <WEB_API_TOKEN>
```

**开放（无需 Token）**：`GET /api/health`、`GET /api/metrics`。

其余 `/api/*` 业务接口均走 `require_auth`。

---

## 接口一览

### 健康与运维

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `/api/health` | 开放 | `{"status","model","mode"}` |
| GET | `/api/metrics` | 开放 | Prometheus 文本（查询数 / 成功率 / token / 耗时） |

### 问答（SSE）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/query` | 自然语言问答，**SSE** 流式推送节点进度 |
| POST | `/api/query/resume` | HITL（敏感列 / 写操作 / 澄清）批准或拒绝后继续 |

**`POST /api/query` body**

```json
{
  "query": "华东销售额最高的部门？",
  "session_id": "default",
  "datasource": "sqlite",
  "model": null,
  "user_id": "viewer",
  "enable_dq": false
}
```

| 字段 | 默认 | 说明 |
|------|------|------|
| `query` | 必填 | 用户问题 |
| `session_id` | `"default"` | 会话 / Checkpointer thread |
| `datasource` | `"sqlite"` | 数据源 |
| `user_id` | `"viewer"` | RBAC 用户（`agent_users`） |
| `enable_dq` | `false` | 是否注入 DataQuality |
| `model` | `null` | 覆盖默认 `ANTHROPIC_MODEL` |

**SSE 事件（节选）**

| event | 含义 |
|-------|------|
| `step_start` / `step_end` | 节点开始 / 结束（router、sql、analysis…） |
| `text_delta` | 增量文本 |
| `interrupt` | HITL 暂停（含 SQL / 澄清问题） |
| `done` | 完成（answer、plan、trace_id、charts…） |
| `error` | 错误 |
| `heartbeat` | 保活（约 15s 无事件时） |

**`POST /api/query/resume` body**

```json
{
  "session_id": "default",
  "approved": true,
  "query_id": ""
}
```

按 `session_id` 定位 runner，不依赖全局 active。

### 反馈 · 会话 · 数据源

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/feedback` | 👍/👎；点赞回流 few-shot + Opik score |
| GET | `/api/sessions` | 会话列表 |
| GET | `/api/sessions/{session_id}/messages` | 某会话消息 |
| POST | `/api/datasource/upload` | 上传 CSV → 独立 SQLite |
| POST | `/api/datasource/connect` | 连接外部 SQLite |

**`POST /api/feedback` body**

```json
{
  "query": "...",
  "answer": "...",
  "rating": "up",
  "trace_id": "",
  "opik_trace_id": "",
  "session_id": "",
  "comment": "",
  "sql": ""
}
```

`rating`：`"up"` \| `"down"`。

### Overview · Memory · Database · RBAC · Dashboard

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/overview` | Architecture 驾驶舱（今日 / 累计 / hero / 节点） |
| GET | `/api/overview/node-errors/{node_id}` | 节点错误列表（含 trace_id / reason） |
| GET | `/api/memory` | 短/语义/向量/程序记忆概览 |
| GET | `/api/memory/checkpoints` | Checkpointer 快照（短时会话） |
| GET | `/api/database?store=demo` | 库浏览器：demo / uploads / traces / hbase |
| GET | `/api/database/table/{table_name}` | 表预览 |
| POST | `/api/database/query` | 只读 SELECT（库浏览器） |
| GET | `/api/rbac` | 角色与权限矩阵 |
| GET | `/api/rbac/users/{user_id}` | 单用户权限 |
| GET | `/api/dashboard/latest` | 最新图表大屏 |

### 静态资源

| 路径 | 说明 |
|------|------|
| `/charts/*` | `render_chart` 生成的 HTML |
| `/` | React 前端（需先 `cd frontend && npm run build`） |

---

## curl 示例

```bash
# 健康检查
curl -s http://127.0.0.1:8000/api/health

# Prometheus
curl -s http://127.0.0.1:8000/api/metrics | head

# 问答（SSE；未配 WEB_API_TOKEN 时）
curl -N -X POST http://127.0.0.1:8000/api/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"有哪些表","session_id":"demo","user_id":"analyst"}'

# 已配 Token
curl -N -X POST http://127.0.0.1:8000/api/query \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $WEB_API_TOKEN" \
  -d '{"query":"华东销售额","session_id":"demo","user_id":"analyst"}'
```

---

## 相关代码

| 模块 | 路径 |
|------|------|
| App 入口 | `server/main.py` |
| 鉴权 | `server/auth.py` |
| SSE | `server/sse.py` · `server/runner_wrapper.py` |
| 路由 | `server/endpoints/*.py` |

实现细节以运行中的 **Swagger / OpenAPI** 为准；本文档与代码同步维护。
