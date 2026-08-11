# Agent 按数据源边界拆分，不按功能合并

HBase 和 Hive 最初尝试合并为一个"大数据查询 Agent"，但两套语法（Shell vs SQL-like）、存储模型（rowkey+列族 vs 分区表）、安全策略（写操作需审批 vs 纯查询）塞进同一个 system prompt 导致串扰。最终按数据源边界拆出 6 个 Agent，而非按功能合并或按输出类型拆分。

**Considered Options**

- **合并 HBase+Hive 为一个 Agent**：减少 Agent 数量，但 prompt 臃肿、tool 子集混乱、安全策略冲突。
- **chart 独立为 Agent**：图表是 analysis 输出的一部分，独立 Agent 需要额外的上下文传递和结果合并——增加复杂度但无信息增益。
- **6 Agent 按数据源边界拆分**：每个 Agent 只掌握自己数据源的语法、表结构和安全策略。Router 按意图分配，Agent 之间不共享 prompt。

**Consequences**

- 拆分粒度 = 数据源边界。Agent 内部可以共用同一套 tool+安全策略，Agent 之间无状态共享。
- 新增数据源只需新增一个 Agent + 对应 tools，不影响已有 Agent。
- 小查询（纯 SQL）的 Router→SQL→Analysis 链路 3 步走完，不会被其他 Agent 干扰。
