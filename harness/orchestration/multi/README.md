# multi_agent/ — 多 Agent 编排（`--mode multi`）

主入口：`orchestrator.py` → `MultiAgentRunner`。CLI（`main.py`）、Streamlit（`app.py`）和 Web（`server/`）共用这一套。

| 文件 | 职责 |
|------|------|
| `orchestrator.py` | LangGraph 图：router → sql/strategy/hbase/hive/dq → analysis → reflection；HITL resume；`wrap_langgraph` |
| `agents.py` | 各专业 Agent 的 prompt + tools；SQL 优先 `discover_relevant_schema`；Strategy 挂 `lookup_metric` |
| `base.py` | 轻量 tool loop（`ConfiguredAgent`） |
| `router.py` | 硬规则 → 上下文继承 → LRU 缓存 → LLM 兜底 |
| `entitlement.py` | 权限：工具/表/行 + `deny_payload` + HITL 判定 |
| `guardrails.py` | 输入 / SQL / 输出护栏 |
| `cache.py` | Router plan 缓存 |
| `state.py` | `MultiAgentState` |
| `schema_discovery.py` | Schema Linking + 值级索引 |
| `task_system.py` | Task board（成功 ✓ / 失败 ✗） |
| `confidence.py` | SQL 自评分门（低分可 HITL） |

阅读顺序：`orchestrator.run` → `node_router` → `agents.route_override` → `common/tools/query.py`（`check_entitlement`）。

Web 侧由 `server/runner_wrapper.py` 包一层 SSE 事件（`step_start` / `text_delta` / `interrupt` / `done`）。
