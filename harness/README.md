# harness/

Agent = Model + **Harness**. 本目录是操作环境，按六维拆分：

```
harness/
  context/         # Prompt、Schema Linking、few-shot、模板、Token/窗口
  memory/          # 短/长期记忆、controller、自学习回流
  tools/           # @tool 与业务工具
  orchestration/   # single ReAct + multi LangGraph
  observation/     # Trace、Opik、cost、pipeline_monitor
  constraints/     # RBAC、护栏、HITL 置信度、retry
```

入口仍是仓库根目录 `main.py` / `app.py` / `server/`。
