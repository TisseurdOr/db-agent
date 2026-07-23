# multi_agent/ — 多 Agent 编排（`--mode multi`）

主入口：`orchestrator.py` → `MultiAgentRunner`。

| 文件 | 职责 |
|------|------|
| `orchestrator.py` | LangGraph 图：router → sql/strategy/dq → analysis；HITL resume |
| `agents.py` | 各专业 Agent 的 prompt + tools + `route_override` |
| `base.py` | 轻量 tool loop（`ConfiguredAgent`） |
| `entitlement.py` | 权限：工具/表/行 + `deny_payload` + HITL 判定 |
| `guardrails.py` | 输入 / SQL / 输出护栏 |
| `cache.py` | Router plan 缓存 |
| `state.py` | `MultiAgentState`（及旧课用的 `DBAgentState`） |

阅读顺序：`orchestrator.run` → `node_router` → `agents.route_override` → `tools/query.py`（`check_entitlement`）。
