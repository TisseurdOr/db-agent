# archive/ — 不在主运行路径上的代码

这里的文件**不会被 `main.py` 调用**。保留是为了对照课程旧实现或未完成作业，避免根目录噪音。

| 路径 | 说明 | 主路径替代 |
|------|------|------------|
| `streaming_agent.py` | 早期 streaming 实现（无 cache_control） | `agent.py` 的 `streaming_agent()` |
| `legacy_single_agent_graph/` | 课程 0020 单 Agent LangGraph（think → tool） | `multi_agent/orchestrator.py` |
| `wip_sql_subgraph/subagents/` | SQL Agent 子图草稿（未接通） | `multi_agent/agents.py` 的 `sql_agent` |
| `mcp_server_duplicate/` | 与 `mcp_servers/db_server.py` 重复的旧副本 | `mcp_servers/db_server.py` |

恢复某段代码时：复制回项目根或 `multi_agent/`，修好 import 后再接入。不要直接 `import archive...` 进主流程。
