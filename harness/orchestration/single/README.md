# single_agent/ — 单 Agent 模式（`--mode single`）

主入口：`agent.py` → `streaming_agent`。CLI 由根目录 `main.py` 调度。

| 文件 | 职责 |
|------|------|
| `agent.py` | ReAct tool loop：streaming、prompt caching、temperature=0、重试 |
| `tools_bundle.py` | single 模式挂载的全量 TOOLS + TOOL_HANDLERS |

共用能力在 `common/`（tools / memory / rag / utils）。  
多 Agent 编排在 `multi_agent/`。

阅读顺序：`main.py --mode single` → `streaming_agent` → `tools_bundle` → `common/tools/query.py`。
