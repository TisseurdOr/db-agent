# Harness 工程第一原则：软约束提示词不可靠，硬护栏才是防线

在 db-agent 的开发和迭代中反复验证了一条原则：用自然语言（prompt / system prompt / 上下文中插入的提示文本）表达的任何约束，最终都不可靠。真正兜底的必须是代码层面的硬控制——护栏正则、状态机跳转、权限字典查找、结构化错误返回。

**真实案例**

- Agent 超时 → 在 context 中插入"[警告] 上游 Agent 超时，请忽略" → Analysis 仍将垃圾文本当有效输入（ADR-0005）
- 权限控制 → 仅靠 system prompt 告知 Agent "你只能查 5 张表" → Agent 仍尝试查第 6 张 → 必须 tool handler 层硬拦截（entitlement.py Layer 2）
- 记忆污染 → 无脑写入所有 Q&A → 元问答污染向量库 → 必须 `should_remember()` 硬过滤（ADR-0002）
- 护栏检测 → 14 条正则可能被同义词绕过 → 后续还有 L2 SQL 关键字 + RBAC 权限 + HITL 审批，多层硬拦截兜底

**设计推论**

1. 调度必须走状态机，不走自然语言——禁止用"忽略""超时"等字符串作为控制流
2. 权限的最终生效点一定是 tool handler 里的 `authorize_tool()`，不是 system prompt
3. 每一层防线都可能被绕过，所以必须多层——护栏 → 权限 → HITL，纵深防御
4. 可测试性 = 可靠性——正则可测、dict 可 mock、状态机可枚举路径。LLM 语义判断不可枚举。
