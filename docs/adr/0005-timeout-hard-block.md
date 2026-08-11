# Agent 超时不应依赖软约束（提示词），应走硬阻断

当前 Agent 超时检测 `is_agent_timeout()` 仅在上游结果中插入一段提示词，告知下游 Analysis Agent "请忽略该结果"。这是软约束——Analysis Agent 可能仍将垃圾文本当作有效上下文，污染最终回答。

实际案例：SQL Agent 轮次跑满 10 轮，未完成查询，但超时结果仍传入 node_analysis。Analysis 读到了"查询出错，请重试"的文本，未能过滤。

**Considered Options**

- **当前方案（软约束）**：在 context 中追加 `[警告] 上游 Agent 超过最大轮数` 字符串。依赖 Analysis Agent 自行判断忽略——不可靠。
- **硬阻断方案（重来）**：Agent 返回结构化结果——`{status: "fail"|"partial"|"ok", data: ..., last_sql: ...}`。`fail` 直接 `next_step` 返回 `done`，不调用 Analysis，给用户明确错误信息。`partial` 保留有效数据，注入 Analysis 或直接输出。

**Consequences**

- 当前软约束已导致至少一次生产问题（SQL 错误循环 10 轮后污染 Analysis）。
- 重来需要 Agent 返回结构化状态而非纯文本——与现有的 `_execute_tool()` 结构化错误回传思路一致，但需要在 Agent 层面做。
- 核心原则：禁止用字符串匹配（"忽略""超时"等）作为调度策略。调度必须走状态机，不走自然语言。
