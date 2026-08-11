# HARNESS.md — db-agent Harness 工程文档

**Agent = 模型(LLM) + Harness(操作环境)。模型提供智能，Harness 提供双手、双眼和工作空间。**

本仓库是一个自然语言数据库分析 Agent 的 harness 实现——不训练模型，只为模型构建一个能在数据库领域高效工作的环境。

参考框架：[learn-claude-code](https://github.com/shareAI-lab/learn-claude-code) — "造好 Harness，Agent 会完成剩下的。"

---

## 架构全景

```
                          ┌──────────────────────────────────────────┐
                          │           PERMISSION LAYER               │
                          │  entitlement.py + guardrails.py          │
                          │  ┌────────────┐  ┌────────────────────┐  │
                          │  │ 5 角色权限  │  │ 三层护栏           │  │
                          │  │ table/row级 │  │ L1 输入(注入检测)  │  │
                          │  │ HITL 审批   │  │ L2 SQL(写操作拦截) │  │
                          │  └────────────┘  │ L3 输出(PII检测)   │  │
                          │                  └────────────────────┘  │
                          └──────────────────────────────────────────┘
                                    │
                                    ▼
  ┌──────────┐    ┌──────────────────────────────────────────────────┐
  │  USER    │───▶│              AGENT LOOP                          │
  │  QUERY   │    │                                                  │
  └──────────┘    │  ┌────────────────────────────────────────────┐  │
                  │  │       streaming_agent()  单Agent模式        │  │
                  │  │  15 tools 全挂一个agent, while True loop   │  │
                  │  │  cache_control 温度=0, 结构化错误处理      │  │
                  │  └────────────────────────────────────────────┘  │
                  │                                                  │
                  │  ┌────────────────────────────────────────────┐  │
                  │  │     MultiAgentRunner 多Agent编排模式        │  │
                  │  │  LangGraph StateGraph + Checkpointer        │  │
                  │  │                                            │  │
                  │  │  ┌──────────────────────────────────────┐  │  │
                  │  │  │         ROUTER (router.py)            │  │  │
                  │  │  │  硬规则覆盖(闲聊/元问题/关键词)        │  │  │
                  │  │  │  LLM意图分类(JSON plan输出)           │  │  │
                  │  │  │  上下文继承(prev_agents carry-forward) │  │  │
                  │  │  │  LRU缓存(同样query零重复LLM调用)      │  │  │
                  │  │  └──────────────────────────────────────┘  │  │
                  │  │           │                                 │  │
                  │  │           ▼                                 │  │
                  │  │  ┌──────────────────────────────────────┐  │  │
                  │  │  │          AGENT TEAM                   │  │  │
                  │  │  │                                      │  │  │
                  │  │  │  ┌──────────┐ ┌──────────────────┐   │  │  │
                  │  │  │  │ sql      │ │ analysis         │   │  │  │
                  │  │  │  │ 4 tools  │ │ 3 tools           │   │  │  │
                  │  │  │  │ 只查数据  │ │ 趋势/对比/图表     │   │  │  │
                  │  │  │  └────┬─────┘ └────────┬─────────┘   │  │  │
                  │  │  │       │                 │             │  │  │
                  │  │  │  ┌────┴─────┐ ┌────────┴─────────┐   │  │  │
                  │  │  │  │ strategy │ │ data_quality     │   │  │  │
                  │  │  │  │ 2 tools  │ │ 3 tools           │   │  │  │
                  │  │  │  │ 制度文档  │ │ NULL/异常/日期扫描 │   │  │  │
                  │  │  │  └──────────┘ └──────────────────┘   │  │  │
                  │  │  │                                      │  │  │
                  │  │  │  ┌──────────┐ ┌──────────────────┐   │  │  │
                  │  │  │  │ hbase    │ │ hive             │   │  │  │
                  │  │  │  │ 3 tools  │ │ 5 tools           │   │  │  │
                  │  │  │  │ KV型NoSQL│ │ HiveQL方言        │   │  │  │
                  │  │  │  └──────────┘ └──────────────────┘   │  │  │
                  │  │  └──────────────────────────────────────┘  │  │
                  │  │           │                                 │  │
                  │  │           ▼                                 │  │
                  │  │  ┌──────────────────────────────────────┐  │  │
                  │  │  │         15 TOOLS                      │  │  │
                  │  │  │  ┌─────────────────────────────────┐  │  │  │
                  │  │  │  │ 数据: list_tables describe_table │  │  │  │
                  │  │  │  │       run_query get_schema_summary│  │  │  │
                  │  │  │  │       match_sql_template          │  │  │  │
                  │  │  │  │ 分析: analyze_results             │  │  │  │
                  │  │  │  │       compare_periods render_chart│  │  │  │
                  │  │  │  │ 知识: search_knowledge_base       │  │  │  │
                  │  │  │  │ 记忆: save_to_memory read_memory  │  │  │  │
                  │  │  │  │       search_memory               │  │  │  │
                  │  │  │  │ 大数据: run_hbase                  │  │  │  │
                  │  │  │  │        generate_hbase_query        │  │  │  │
                  │  │  │  │        search_hive_syntax           │  │  │  │
                  │  │  │  └─────────────────────────────────┘  │  │  │
                  │  │  └──────────────────────────────────────┘  │  │
                  │  └────────────────────────────────────────────┘  │
                  └──────────────────────────────────────────────────┘
                                    │
                                    ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │                   MEMORY SYSTEM                                  │
  │                                                                  │
  │  ┌─────────────────────┐  ┌──────────────────────────────────┐  │
  │  │ ConversationManager │  │ VectorMemory (ChromaDB)          │  │
  │  │ 滑动窗口 + LLM 压缩  │  │ embedding → 语义召回 top_k       │  │
  │  │ build_context()注入  │  │ search_memory → Agentic RAG     │  │
  │  └─────────────────────┘  └──────────────────────────────────┘  │
  │                                                                  │
  │  ┌─────────────────────┐  ┌──────────────────────────────────┐  │
  │  │ memory_controller   │  │ HybridWindowManager              │  │
  │  │ 闲聊/Meta过滤        │  │ 四层压缩: layer0/middle/old      │  │
  │  │ 向量召回策略          │  │ TokenBudget 预算控制             │  │
  │  └─────────────────────┘  └──────────────────────────────────┘  │
  └──────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │                  OBSERVATION LAYER                               │
  │                                                                  │
  │  TraceContext   → logs/traces/*.jsonl (节点耗时+token+错误)       │
  │  Opik           → LangGraph 树 + LLM span + Feedback（可选）      │
  │  RouterCache    → LRU 缓存 (同样 query 零 LLM 重复调用)           │
  │  Checkpointer   → agent_state.db (LangGraph state 跨轮持久化)     │
  │  Token 日志     → 每轮 [memory] 统计: 原文/摘要/压缩/checkpoint   │
  └──────────────────────────────────────────────────────────────────┘
```

---

## 6 个专业 Agent

每个 Agent 封装为 `ConfiguredAgent(name, system_prompt, tools, handlers)`。Agent 不知道其他 Agent 的存在——只接收 task，返回 result。编排由 `orchestrator.py` 的 LangGraph graph 负责。

### 1. SQL Agent — 数据查询专家

```
职责: 只查数据，不做分析
Tools: discover_relevant_schema, list_tables, describe_table, run_query
约束: 不分析趋势、不给业务建议、不编造数据
       SQL 报错如实报告，不猜测原因
       run_query 只允许 SELECT 语句
```

- 先 `discover_relevant_schema` 按问题检索相关表/字段
- 不够再 `list_tables` / `describe_table`
- 最后 `run_query` 执行 SELECT，返回 `{rows, count, truncated}`
- 自动截断超大结果集（>50 行），生成统计摘要

### 2. Analysis Agent — 数据分析专家

```
职责: 分析数据，不做查询
Tools: analyze_results, compare_periods, render_chart
约束: 不会写 SQL、不会查数据库
       上游 SQL/Strategy 结果作为 context 注入
       直接基于结果分析，不说"我无法查询数据库"
```

- `analyze_results` — 趋势、排名、分布、异常检测
- `compare_periods` — 同环比、多维度对比（地区/时间/部门/产品）
- `render_chart` — 暗色主题 HTML 数据大屏（多面板布局）
- 输出风格：先给结论和关键数字，再补简短依据

### 3. Strategy Agent — 制度知识检索专家

```
职责: 查公司制度文档，关联战略分析
Tools: search_knowledge_base
约束: 不会查数据库、不会写 SQL
       不确定时标注推测，不编造制度内容
```

- 把分析结果和公司战略/制度关联（"华东下降可能是因为 Q2 战略重心在华南"）
- 用公司政策解释现象（"按提成制度，软件类 8% 佣金可能激励了软件销售"）
- 给出符合公司方向和制度的可执行建议

### 4. HBase Agent — KV 型 NoSQL 专家

```
职责: 生成 HBase Shell 命令 + 在本地模拟器上执行查询
Tools: run_hbase, generate_hbase_query, search_knowledge_base
约束: HBase 不是 SQL——确保命令符合 HBase Shell 语法
       scan 全表务必加 FILTER 或 STARTROW/STOPROW
       写操作（put/delete/drop/truncate）触发 HITL 审批
```

**本地模拟 HBase（内存 KV 引擎）：**

| 表名 | 列族 | 行数 | 说明 |
|------|------|------|------|
| `orders` | cf | 30 行 | 行键 `order_NNN`，total/status/customer_id/region/created_at |
| `user_profile` | info, behavior | 15 行 | info:name/email/age/region, behavior:last_login/pv/purchases |
| `product_catalog` | meta, stock | 10 行 | meta:name/category/price, stock:qty/warehouse |

**支持 12 种操作：** scan / get / count / put / delete / list / create / desc / disable / enable / drop / truncate

**DDL 安全规则：** drop 和 truncate 前必须先 disable（模拟真实 HBase 行为）。

### 5. Hive Agent — Hive/Impala 数仓专家

```
职责: 生成 HiveQL/Impala SQL + 在本地模拟数仓上执行查询
Tools: list_tables, describe_table, run_query, search_hive_syntax, search_knowledge_base
约束: 确保查询符合 HiveQL 方言（非标准 SQL）
       标注 Hive vs Impala 差异
       给出性能建议（分区裁剪、MAPJOIN 提示）
```

**本地模拟 Hive 数仓（SQLite 模拟，表名和结构保持 Hive 风格）：**

| 表名 | 分区列 | 行数 | 说明 |
|------|--------|------|------|
| `ods_orders_hive` | dt, region | 60 行 | 订单贴源层，PARQUET 格式 |
| `dwd_user_events` | dt | 50 行 | 用户行为埋点明细，event_props 为 JSON (模拟 MAP) |
| `dim_products_hive` | — | 10 行 | 产品维度表，tags 为 JSON 数组 (模拟 ARRAY) |

**语法模板覆盖 9 种查询类型：** select / create_table / insert / window_function / lateral_view / compute_stats / show_partitions / explain / join

**方言差异：** Hive 用 `ANALYZE TABLE ... COMPUTE STATISTICS`，Impala 用 `COMPUTE STATS`。

### 6. DataQuality Agent — 数据质量扫描专家

```
职责: 首次查询时扫一遍数据质量，只读不写
Tools: list_tables, describe_table, run_query
约束: 只报告事实，不推荐业务决策
       不确定时标注"推测"
       检查不超过 5 条 SQL
```

**检查内容（按优先级）：**
- 日期连续性：`orders.created_at` 是否有明显缺失的日期段
- NULL 比例：关键字段（total、customer_id、dept_id）的 NULL 占比
- 异常值：金额远超同表均值的记录（total > 均值 × 5）
- 状态分布：cancelled/pending/completed 各占多少

**时效控制：** `--dq` 参数启用，首次扫描后 1 小时内跳过。`MultiAgentRunner._should_inject_dq()` 控制注入时机。

---

## Router — 意图分类中枢

```
route_override(query, prev_agents) → plan | None

优先级:
  1. 硬规则覆盖
     ├─ 闲聊 ("你好"/"hi"/"你是谁")      → plan = [] (不查数据)
     ├─ 元问题 ("刚才问了什么")           → analysis
     ├─ HBase 关键词 (hbase/regionserver) → hbase
     ├─ HBase 操作词 (\bscan\b/\bget\b)   → hbase
     ├─ Hive 关键词 (hive/impala/hue)     → hive
     ├─ 制度关键词 (提成/报销/战略)        → strategy
     ├─ 对比关键词 (vs/对比/环比)          → sql + analysis
     └─ 数据关键词 (销售额/订单/员工)       → sql

  2. 上下文继承
     └─ prev_agents 只有 hbase 或 hive，本轮无冲突信号 → 继承

  3. LLM 意图分类
     └─ router_prompt → client.messages.create → JSON plan

  4. LRU 缓存
     └─ RouterCache(100) 缓存 LLM 结果，同样 query 零重复调用
```

---

## 15 个 Tool — 给 Agent 的双手

| # | Tool | Agent | 作用 | HITL |
|---|------|-------|------|------|
| 1 | `list_tables` | sql, hive, dq | 列出所有表名 | — |
| 2 | `describe_table` | sql, hive, dq | 查看表结构（列名/类型） | — |
| 3 | `run_query` | sql, hive, dq | 执行 SELECT（只读） | 敏感列审批 |
| 4 | `get_schema_summary` | (single) | 一键获取全部表结构摘要 | — |
| 5 | `analyze_results` | analysis | 统计摘要（趋势/排名/分布） | — |
| 6 | `compare_periods` | analysis | 周期对比（同环比） | — |
| 7 | `render_chart` | analysis | 暗色 HTML 数据大屏 | — |
| 8 | `search_knowledge_base` | strategy, hbase, hive | 公司制度/政策文档检索 | 文档级过滤 |
| 9 | `save_to_memory` | (single) | 写入长期记忆 | — |
| 10 | `read_memory` | (single) | 读取指定记忆 | — |
| 11 | `search_memory` | (single) | 语义搜索记忆 (Agentic RAG) | Self-Query 过滤 |
| 12 | `run_hbase` | hbase | HBase 模拟器执行 (12 种操作) | 写操作审批 |
| 13 | `generate_hbase_query` | hbase | 生成 HBase Shell 命令文本 | — |
| 14 | `search_hive_syntax` | hive | Hive/Impala 语法模板检索 | — |
| 15 | `match_sql_template` | (single) | 高频指标模板填槽，未命中回退 LLM | — |

另：multi 模式 SQL Agent 优先调 `discover_relevant_schema`（Schema Linking）；Strategy Agent 另挂 `lookup_metric`。

每个 tool 通过 `@tool` 装饰器自动生成 JSON Schema（name/description/input_schema），注册进 `TOOLS` + `TOOL_HANDLERS` dispatch map。

---

## Permissions — 权限边界

### 5 种角色 (entitlement.py)

| 角色 | 可用工具 | 数据范围 | 敏感检查 | HITL |
|------|---------|---------|---------|------|
| **dba** | 全部 (含 write_query, run_hbase) | 全部表 | 关 | 无 |
| **manager** | run_query, list, describe, search | 全部表, 行级隔离 (WHERE dept_id=X) | 开 | salary/cost/budget |
| **analyst** | run_query, list, describe, search, hbase | 5 表 (departments~orders) | 开 | salary/cost/budget |
| **viewer** | list, describe, search | 4 表 (不含 employees) | 关 | — |
| **support** | run_query, list, describe, search | 3 表 (products~orders) | 关 | — |

权限数据从 `db/demo.db` 的 `agent_roles` / `agent_users` 表加载，DB 为空时 fallback 到内置默认值。生产环境改表即生效。

### 三层护栏 (guardrails.py)

```
Layer 1 输入护栏 (guard_input)
  拦截: prompt injection / 指令覆盖 / 角色扮演越狱 / 空输入
  特点: 正则匹配，零 token 成本，秒级跑完
  覆盖: 6 条 eval (guard-001 ~ guard-006)

Layer 2 SQL 护栏 (guard_sql)
  拦截: 非 SELECT 语句 (INSERT/UPDATE/DELETE/DROP/ALTER)
  位置: run_query() 工具内部，执行前硬拦截

Layer 3 输出护栏 (guard_output)
  检测: PII (身份证/手机号/邮箱)、system prompt 泄露、异常输出
  位置: node_analysis 输出后，写入 final_answer 前
```

### HITL 双层审批

```
SQL HITL:
  needs_approval(user, sql) → sensitive_check=True 角色
  查询 salary/cost/budget 列 → interrupt() 暂停 graph
  main.py 展示 SQL → 用户 y/n → resume()

HBase HITL:
  needs_approval_hbase(operation) → put/delete/drop/truncate
  run_hbase() 执行前 → interrupt() 暂停 graph
  main.py 展示操作详情 → 用户 y/n → resume()
```

---

## Memory — 多层记忆系统

```
┌─────────────────────────────────────────────────────────┐
│ Layer 0: 原文保留                                       │
│   ConversationManager.messages → 最近 N 轮原文不动       │
│   max_recent=6, 超出 → 触发压缩                          │
├─────────────────────────────────────────────────────────┤
│ Layer 1: 短期压缩                                       │
│   ConversationManager._compress() → LLM 摘要             │
│   build_context() 注入下一轮 system prompt               │
│   HybridWindowManager → 四层策略 (layer0/middle/old)     │
├─────────────────────────────────────────────────────────┤
│ Layer 2: 长期向量记忆                                    │
│   VectorMemory (ChromaDB)                                │
│   remember(Q&A pair) → embedding → 写入向量库            │
│   recall(query) → 语义召回 top_k=3                       │
│   分数阈值 0.3 → 低分结果不注入                          │
│   search_memory tool → Agent 自主检索 (Agentic RAG)      │
├─────────────────────────────────────────────────────────┤
│ Layer 3: 记忆策略控制 (memory_controller.py)             │
│   is_chitchat() → 闲聊跳过向量召回                       │
│   is_meta_question() → 元问题走时间倒序 list_recent()    │
│   is_meta_memory() → 元问答记忆标记跳过，防止污染        │
│   should_vector_recall() → 正常查询才走向量召回          │
│   should_remember() → 元问题不写入向量库                 │
├─────────────────────────────────────────────────────────┤
│ Layer 4: Token 预算 (TokenBudget)                        │
│   固定成本 (system_prompt + tools) + 动态成本 (messages)  │
│   available() → 剩余 token 预算                          │
│   should_compress() → 超阈值 → 触发压缩                  │
└─────────────────────────────────────────────────────────┘
```

**两套 Agent 模式共用同一套记忆系统：**
- Single mode: `streaming_agent()` → `conversation.add_message()` + `vector_memory.remember()`
- Multi mode: `MultiAgentRunner.run()` → Checkpointer 持久化 + `conversation.build_context()` 注入 → `vector_memory.remember()`

---

## Observation — 可观测性

| 组件 | 文件 | 机制 |
|------|------|------|
| **TraceContext** | `utils/tracer.py` | 每个节点记录: node名/耗时/token/错误。落盘 `logs/traces/*.jsonl`。`trace.summary()` 输出一行摘要 |
| **Opik** | `utils/opik_tracing.py` | `OPIK_ENABLED=1` 时 wrap LangGraph + Anthropic；annotate 路由/few-shot/HITL/成本；点赞写 Feedback Score |
| **RouterCache** | `multi_agent/cache.py` | LRU 缓存 (max_size=100)。`cache.get(query)` → hit 则跳过 LLM。显示命中率 `hit_rate` |
| **Checkpointer** | `db/agent_state.db` | LangGraph `AsyncSqliteSaver`。graph 每步 auto-save state。同一 thread_id 跨轮恢复 messages + plan + results |
| **Token 日志** | `main.py` | 每轮 `[memory]` 统计: `total≈N (原文 X/Y条, 摘要 Z/W条已压缩, checkpoint=db)` |
| **Eval tracer** | `eval_runner.py` | `_parse_agents_from_trace()` 从最新 trace 解析 agent 列表。`_parse_tokens_from_trace()` 读取总 token 数；`--opik` 上传 Experiment |

---

## 验证体系

### 冒烟测试 (零成本，每次 commit)

```sh
uv run pytest tests/test_harness_smoke.py -v   # 零 API
```

| 验证维度 | 条数 | 说明 |
|---------|------|------|
| 模块编译 | 21 | 所有 harness 模块能正常 import |
| Tool schema | 1 | 15 个 tool 的 name/description/input_schema 格式合法，无重复名 |
| Handler 配线 | 1 | 15 个 tool ↔ 15 个 handler 一一对应，无孤立 |
| Agent 配置 | 5 | 6 个 agent 的 name/prompt 非空，tools/handlers 一致，核心 tool 到位 |
| Router 标记 | 2 | 5 组标记常量非空，scan 正则能匹配且不误匹配 |
| Guardrails | 4 | guard_input/guard_output/guard_sql 可调用，prompt injection 被拦截 |
| HITL | 3 | needs_approval 存在，HBase 4 种破坏性操作全覆盖，敏感列 >=3 个 |
| Memory | 1 | 5 个 controller 函数齐全可调用 |
| Graph | 1 | LangGraph 图能编译，节点含 router/sql/analysis |
| Entitlement | 3 | 5 角色 + 9 用户已加载，get_user 返回完整对象，build_permission_context 非空 |
| Handler | 1 | 15 个 handler 均可调用 |
| Memory 组件 | 3 | ConversationManager/VectorMemory/TokenBudget 可实例化 |

### 单元测试

```sh
uv run pytest tests/ -v                          # 零/低 API 用例为主
```

| 文件 | 条数 | 覆盖 |
|------|------|------|
| `test_harness_smoke.py` | 46 | Harness 线路完整性 |
| `test_hbase.py` | 34 | HBase 模拟器 (scan/get/count/put/delete/DDL/filter/HITL) |
| `test_memory.py` | 31 | VectorMemory + ConversationManager + TokenBudget + memory_controller |
| `test_bigdata.py` | 22 | HBase/Hive SQL 生成 + Router 路由 + 上下文继承 |
| `test_agent.py` | 10 | Agent 集成 (tool 调用 / 未知表 / 写操作被拒) |
| `test_self_query.py` | 7 | Self-Query 检索 (过滤/降级/memory_type) |
| `test_recovery.py` | 12 | 错误恢复 (API 重试退避 / SQL 自愈信号 / 失败重规划 / Task失败态 / trace耗时) |
| `test_context_engineering.py` | 6 | 检索式 few-shot + 值级索引（种子 SQL 可执行 / 降级） |
| `test_feedback.py` | 14 | 自学习回流 (SQL 提取 / 质量门 / HITL / 降级) |
| `test_template_matcher.py` | 23 | 模板填槽 / 未命中回退 / Tool 接口 |
| `test_orchestration.py` | 16 | 编排边、Strategy lookup_metric 等 |
| `test_task_system.py` | 5 | 任务板状态机 |
| `test_eval_hitl.py` | 4 | Eval 与 HITL 边界 |
| `eval_cases.py` | 36 | 结构化 eval 用例库 (非 pytest，eval_runner 驱动) |

### Eval 评估

```sh
python -m tests.eval_runner --fast               # 6 条 guardrail (零 LLM, 秒级)
python -m tests.eval_runner --full               # 36 条全类别 (需 API Key)
python -m tests.eval_runner --full --judge       # LLM-as-Judge 打分 (Kimi 独立评估)
python -m tests.eval_runner --full --judge --suggest  # +修改建议
```

| 类别 | 条数 | 说明 |
|------|------|------|
| guardrail | 6 | prompt injection / 空输入 / 写操作意图 / 正常放行 |
| routing | 12 | sql / strategy / analysis / hbase / hive / 元问题 / 上下文继承 / 误匹配防护 |
| output_quality | 6 | 表名/字段名/部门名/产品趋势/写操作拒绝/推诿话术拦截 |
| edge | 12 | 不存在实体 / 极短query / 纯数字 / SQL注入 / 超长输入(500字+) / 乱码 / 拼音 / HBase HITL / 元问题 / 闲聊 |

---

## 双模式架构

### Single 模式 (`--mode single`)

```
user_input → memory_controller (闲聊过滤)
          → vector_memory.recall() (语义召回 top_k=3)
          → build_system_prompt(extra_context=memories)
          → streaming_agent(client, system_prompt, tools=15, handlers=15)
             → while True:
                 response = client.messages.create(tools=15)
                 if stop_reason != "tool_use": break
                 for tool_call: TOOL_HANDLERS[name](**input)
                 append tool_result → loop
          → conversation.add_message(Q&A)
          → vector_memory.remember(Q&A pair)
```

优势：15 个 tool 全挂一个 agent，灵活。适合简单查询和探索性对话。

### Multi 模式 (`--mode multi`)

```
user_input → guard_input (L1 护栏)
          → memory_controller → vector_memory.recall()
          → MultiAgentRunner.run(query, memories, conversation_summary)
             → LangGraph graph.ainvoke(state):
                 node_router → plan (硬规则/缓存/LLM)
                 node_data_quality  (可选, --dq)
                 node_sql → sql_agent.run()
                 node_hbase → hbase_agent.run()
                 node_hive → hive_agent.run()
                 node_strategy → strategy_agent.run()
                 node_analysis → analysis_agent.run(context=所有上游结果+记忆+历史)
                 → final_answer
          → conversation.add_message()
          → vector_memory.remember()
```

优势：Agent 职责单一，上下文隔离，并行执行（同层 Agent 互不依赖），LangGraph Checkpointer 持久化 state。

---

## 项目结构

```
db-agent/
  main.py                     CLI 入口，15 TOOLS/TOOL_HANDLERS 注册，双模式调度
  app.py                      Streamlit 聊天界面
  server/                     FastAPI + SSE（query / resume / feedback / sessions / datasource）
  frontend/                   React + Vite 聊天 UI
  agent.py                    单 Agent loop (streaming_agent, cache_control)
  multi_agent/
    orchestrator.py           多 Agent 编排 (LangGraph StateGraph + Checkpointer + HITL resume + Opik wrap)
    agents.py                 6 个 ConfiguredAgent 定义 (prompt + tools + handlers)
    router.py                 意图路由 (5组标记 + 正则 + 上下文继承 + route_override)
    state.py                  MultiAgentState TypedDict
    base.py                   ConfiguredAgent + agent.run(client, task) 封装
    entitlement.py            权限网关 (5角色/表级/行级/HITL/db动态加载)
    guardrails.py             三层护栏 (L1输入/L2 SQL/L3输出)
    cache.py                  Router LRU 缓存 (max_size=100)
    schema_discovery.py       Schema Linking + 值级索引
    task_system.py            Task board（成功/失败态）
    confidence.py             SQL 置信度门
  tools/
    __init__.py               @tool 装饰器 (自动生成 JSON Schema)
    schema.py                 list_tables / describe_table / get_schema_summary / discover_relevant_schema
    query.py                  run_query (只读 SELECT + HITL interrupt + 结果截断)
    analysis.py               analyze_results / compare_periods
    chart.py                  render_chart (暗色 HTML 多面板数据大屏)
    knowledge.py              search_knowledge_base / save_to_memory / read_memory / search_memory
    metrics.py                lookup_metric（指标口径）
    template_matcher.py       高频 SQL 模板优先匹配
    hbase.py                  HBase 内存模拟引擎 + 12种操作 + HITL + Shell命令生成
    hive.py                   Hive/Impala 语法模板 (9类型, 双方言)
  memory/
    short_term_memory.py      ConversationManager (滑动窗口 + LLM 压缩)
    vector_store.py           VectorMemory (ChromaDB 语义召回 + list_recent + forget)
    memory_controller.py      记忆策略控制 (闲聊/Meta过滤/召回策略)
    token_budget.py           TokenBudget (token 预算计算 + 压缩阈值判断)
    hybrid_window_manager.py  四层压缩 (layer0原文/middle压缩对/old全局摘要)
    long_term_memory.py       长期记忆数据模型
  db/
    seed.py                   SQLite 种子数据 (5业务表 + 3 Hive风格表 + 权限表, ~250行)
    demo.db                   SQLite 数据库文件
    agent_state.db            LangGraph Checkpointer 持久化文件
  utils/
    llm.py                    extract_text (多格式兼容)
    tracer.py                 TraceContext (节点trace + JSONL落盘 + summary)
    opik_tracing.py           Opik wrap + annotate + 用户反馈打分
    retry.py                  LLM API 指数退避
    cost.py                   token 成本估算
  opik-platform/              本地 Opik Docker Compose
  tests/
    test_harness_smoke.py     ★ Harness 冒烟测试 (零API)
    test_hbase.py             HBase 模拟器
    test_bigdata.py           HBase/Hive/Router
    test_memory.py            Memory 系统 + memory_controller
    test_agent.py             Agent 集成
    test_self_query.py        Self-Query 检索
    test_recovery.py          错误恢复
    test_feedback.py          自学习质量门
    test_context_engineering.py  few-shot + 值级索引
    test_template_matcher.py  SQL 模板匹配
    test_orchestration.py     编排与 Strategy 工具
    test_task_system.py       任务板状态机
    test_eval_hitl.py         Eval / HITL 边界
    eval_cases.py             评估用例库 (36条, 4类别)
    eval_runner.py            评估执行器 (fast/full/judge/suggest/--opik)
```

---

## 数据层

### SQLite 业务库 (5 表, ~200 行)

| 表名 | 行数 | 说明 |
|------|------|------|
| `departments` | 6 | 部门表 (dept_id, dept_name, manager, budget) |
| `employees` | 20 | 员工表 (emp_id, name, dept_id, salary, hire_date) |
| `products` | 10 | 产品表 (prod_id, name, category, unit_price) |
| `customers` | 12 | 客户表 (cust_id, name, region, industry) |
| `orders` | 80 | 订单表 (order_id, dept_id, cust_id, total, status, created_at) |

### Hive 模拟数仓 (3 表, ~120 行)

| 表名 | 分区列 | 行数 | 层级 |
|------|--------|------|------|
| `ods_orders_hive` | dt, region | 60 | ODS 贴源层 |
| `dwd_user_events` | dt | 50 | DWD 明细层 |
| `dim_products_hive` | — | 10 | DIM 维度层 |

### HBase 内存模拟 (3 表, ~55 行)

| 表名 | 列族 | 行数 |
|------|------|------|
| `orders` | cf | 30 |
| `user_profile` | info, behavior | 15 |
| `product_catalog` | meta, stock | 10 |

### 权限数据

| 表名 | 行数 | 说明 |
|------|------|------|
| `agent_roles` | 5 | 角色定义 (allowed_tools, db_tables, db_row_filter, sensitive_check) |
| `agent_users` | 9 | 用户定义 (user_id, name, role, dept_id) |

---

## Harness 覆盖对照 (learn-claude-code 20 章)

| 课程 | 机制 | db-agent 实现 |
|------|------|-------------|
| s01 Agent Loop | 循环 + Bash | `streaming_agent()` while True + LangGraph graph |
| s02 Tool Use | dispatch map | 15 tools + TOOL_HANDLERS dict, `@tool` 自动Schema |
| s03 Permission | 审批管线 | entitlement.py (5角色, 表级, 行级改写, HITL interrupt) |
| s04 Hooks | 工具前后插口 | guardrails L1(输入)/L2(SQL)/L3(输出) 三层拦截点 |
| s05 TodoWrite | 先计划后执行 | TaskCreate/TaskUpdate + blockedBy 依赖图 |
| s06 Subagent | 上下文隔离 | Agent 工具 + 6 subagent_type 派生 |
| s07 Skill Loading | 按需注入 | `build_system_prompt()` 动态组装 (db_type/user_role/extra_context) |
| s08 Context Compact | 上下文压缩 | HybridWindowManager 四层 (layer0原文/middle压缩对/old全局摘要) |
| s09 Memory | 筛选/提取/整理 | VectorMemory (ChromaDB) + memory_controller (闲聊/Meta过滤) |
| s10 System Prompt | 运行时组装 | `build_system_prompt()` 工厂函数 (分段拼接) |
| s11 Error Recovery | 重试策略 | 三层自愈：API 指数退避重试 (utils/retry.py) + SQL 错误回喂重写 (自愈协议, 最多2次) + Agent 失败回 Router 重规划 (_maybe_replan, 上限1次) |
| — | 动态上下文 | Schema Linking + 值级索引 (schema_discovery) + 检索式 few-shot (sql_examples，Vanna 模式) |
| — | 自学习闭环 | 成功 SQL（run_query 工具层捕获）/ HITL 批准 → 质量门 dry-run → 回流样例库 (rag/feedback.py)；AUTO_LEARN_SQL 可关 |
| s12 Task System | 磁盘持久化 | LangGraph Checkpointer (agent_state.db, cross-turn state恢复) |
| s13 Background Tasks | 后台执行 | Agent tool run_in_background + Monitor stream |
| s15 Agent Teams | 多Agent协作 | 6 agent LangGraph 编排 (Router→SQL/HBase/Hive/Strategy→Analysis) |
| — | 可观测性 | TraceContext JSONL + Opik（LangGraph/LLM span/Feedback/Experiment） |
| — | HITL | interrupt() 双层审批 (SQL敏感列 + HBase写操作) |
| — | 缓存 | RouterCache LRU (同样 query 零重复 LLM 调用) |
| — | 数据模拟 | HBase 内存 KV 引擎 (3表12操作) + Hive SQLite 模拟 (3表) |
| — | 评估体系 | 36条 eval (4类别) + LLM-as-Judge (Kimi) + suggest (自动修方案) |

---

**造好 Harness。Agent 会完成剩下的。**
