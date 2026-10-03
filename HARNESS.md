# HARNESS.md — db-agent Harness 工程文档

**Agent = 模型(LLM) + Harness(操作环境)。模型提供智能，Harness 提供双手、双眼和工作空间。**

本仓库是一个自然语言数据库分析 Agent 的 harness 实现——不训练模型，只为模型构建一个能在数据库领域高效工作的环境。

参考框架：[learn-claude-code](https://github.com/shareAI-lab/learn-claude-code) — "造好 Harness，Agent 会完成剩下的。"

设计主线：**确定性优先** —— 模板 > LLM，正则 > LLM，硬规则 > 语义理解。

---

## 六维目录（= 代码地图）

运行时代码在 `harness/`，按面试六维物理拆分：

| 维度 | 路径 | 职责 |
|------|------|------|
| 上下文管理 | `harness/context/` | Prompt 组装、Schema Linking、few-shot、SQL 模板、Token/窗口压缩 |
| 记忆管理 | `harness/memory/` | 短/长期记忆、controller、自学习回流 |
| 工具系统 | `harness/tools/` | `@tool` + 业务工具（schema/query/analysis/…） |
| 系统编排 | `harness/orchestration/` | `single/` ReAct Loop；`multi/` LangGraph 6 Agent |
| 评估观测 | `harness/observation/` | Trace、Opik、cost、pipeline_monitor |
| 约束与修复 | `harness/constraints/` | RBAC、三层护栏、confidence/HITL、API retry |

学习材料 / 简历 / 旧实验在 `sidecar/`，不参与运行。

---

## 架构全景

```
                          ┌──────────────────────────────────────────┐
                          │     CONSTRAINTS  harness/constraints/    │
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
  │  USER    │───▶│         ORCHESTRATION  harness/orchestration/    │
  │  QUERY   │    │                                                  │
  └──────────┘    │  ┌────────────────────────────────────────────┐  │
                  │  │  single/agent.py  streaming_agent()         │  │
                  │  │  15+ tools 全挂, while tool_use loop         │  │
                  │  │  HybridWindow + TokenBudget (context/)      │  │
                  │  └────────────────────────────────────────────┘  │
                  │                                                  │
                  │  ┌────────────────────────────────────────────┐  │
                  │  │  multi/  MultiAgentRunner + LangGraph       │  │
                  │  │  Checkpointer: db/agent_state.db            │  │
                  │  │                                            │  │
                  │  │  router → clarify? → [dq?] → sql/hbase/…   │  │
                  │  │       → confidence_gate → analysis         │  │
                  │  │       → reflection(≤2) → done              │  │
                  │  │  超时 → _maybe_replan(≤1) 回 router        │  │
                  │  └────────────────────────────────────────────┘  │
                  └──────────────────────────────────────────────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              ▼                     ▼                     ▼
     CONTEXT / TOOLS            MEMORY              OBSERVATION
     harness/context/        harness/memory/     harness/observation/
     harness/tools/          Chroma + SQLite     Trace JSONL + Opik
```

---

## 6 个专业 Agent

定义在 `harness/orchestration/multi/agents.py`。每个是 `ConfiguredAgent(name, system_prompt, tools, handlers)`——只收 task、回 result，互不看见对方 tool 轨迹。编排由 `harness/orchestration/multi/orchestrator.py`。

### 1. SQL Agent — 数据查询专家

```
职责: 只查数据，不做分析
Tools: discover_relevant_schema, list_tables, describe_table, run_query
约束: 不分析趋势、不给业务建议、不编造数据
       SQL 报错如实报告；run_query 只允许 SELECT
```

- Schema Linking：`harness/context/schema_discovery.py` → `discover_relevant_schema`
- few-shot：节点内注入 `harness/context/sql_examples.py`（Vanna 模式，相似度阈值 0.35）
- 结果截断：>50 行 + 统计摘要

### 2. Analysis Agent — 数据分析专家

```
Tools: analyze_results, compare_periods, render_chart
约束: 不写 SQL；上游 results + 记忆 + 对话摘要作为 context
```

### 3. Strategy Agent — 制度 + 指标口径

```
Tools: search_knowledge_base, lookup_metric
约束: 不查库、不写 SQL；不确定标注推测
```

### 4. HBase Agent — KV NoSQL

```
Tools: run_hbase, generate_hbase_query, search_knowledge_base
写操作 put/delete/drop/truncate → HITL
本地内存模拟 3 表 × ~12 种操作
```

### 5. Hive Agent — 数仓方言

```
Tools: list_tables, describe_table, run_query, search_hive_syntax, search_knowledge_base
本地 SQLite 模拟 ods/dwd/dim；语法模板 9 类
```

### 6. DataQuality Agent — 质量扫描

```
Tools: list_tables, describe_table, run_query
--dq 启用；首次扫描后 1h 内跳过
检查：日期连续性 / NULL 占比 / 异常值 / 状态分布
```

---

## Router — 意图分类中枢

文件：`harness/orchestration/multi/router.py` + `cache.py`；调度在 `orchestrator.node_router`。

```
优先级（与代码一致）:
  1. 硬规则 route_override（含上下文继承 prev_agents）
     闲聊→[] / 元问题→analysis / HBase·Hive·制度·对比·数据关键词 …
  2. LRU RouterCache(max_size=100)  — 同 query 跳过 LLM
  3. LLM 意图分类 → JSON plan（可带 confidence=low → clarify）

失败重规划：跳过硬规则与缓存，把失败反馈喂给 LLM（上限 1 次）
```

配套质量闸（均在 multi 图上）：

| 节点 | 作用 | 上限/阈值 |
|------|------|-----------|
| `clarify` | 低置信度主动澄清（interrupt） | — |
| `confidence_gate` | SQL 自评分，低则人工确认 | **0.7** |
| `reflection` | 完整性/真实性自审，失败退回 Analysis | **≤2** |
| `_maybe_replan` | Agent 超时带反馈回 Router | **≤1** |
| Task board | plan 落盘 `.tasks/*.task`（✓/✗/failed） | — |

---

## 工具系统 — `harness/tools/`

| # | Tool | 主要 Agent | HITL |
|---|------|------------|------|
| 1 | `list_tables` | sql, hive, dq | — |
| 2 | `describe_table` | sql, hive, dq | — |
| 3 | `run_query` | sql, hive, dq | 敏感列 |
| 4 | `get_schema_summary` | single | — |
| 5 | `discover_relevant_schema` | sql | — |
| 6 | `analyze_results` | analysis | — |
| 7 | `compare_periods` | analysis | — |
| 8 | `render_chart` | analysis | — |
| 9 | `search_knowledge_base` | strategy, hbase, hive | 文档过滤 |
| 10 | `lookup_metric` | strategy | — |
| 11 | `save_to_memory` / `read_memory` / `search_memory` | single | Self-Query |
| 12 | `run_hbase` / `generate_hbase_query` | hbase | 写操作 |
| 13 | `search_hive_syntax` | hive | — |
| 14 | `match_sql_template` | single（实现在 context/） | — |

注册：`@tool` → JSON Schema；single 全量在 `harness/orchestration/single/tools_bundle.py`；multi 按 Agent 白名单挂载。

---

## 约束与修复 — `harness/constraints/`

### 5 角色 RBAC (`entitlement.py`)

| 角色 | 要点 |
|------|------|
| dba | 全工具全表 |
| manager | 行级 `dept_id`；敏感列 HITL |
| analyst | 业务表 + HBase；敏感列 HITL |
| viewer | 只读元数据/知识，无 employees |
| support | 窄表白名单 |

权限存 `db/demo.db` 的 `agent_roles` / `agent_users`，改表即生效。

### 三层护栏 (`guardrails.py`)

- L1 `guard_input`：注入 / 超长 / 空（零 token）
- L2 `guard_sql`：仅 SELECT（在 `run_query` 内）
- L3 `guard_output`：PII / prompt 泄露（analysis 输出前）

### HITL

- SQL：`salary` / `cost` / `budget` → `interrupt()`
- HBase：put/delete/drop/truncate → `interrupt()`
- 另：clarify、confidence_gate 也会 pause；`/api/query/resume` 或 CLI y/n 续跑

### 三层自愈

1. API：`retry.py` — 429/5xx 指数退避（默认最多 3 次）
2. SQL：报错结构化 `retryable` + hint → Agent 重写（prompt 约定 ≤2）
3. 编排：超时 → `_maybe_replan`（≤1）

---

## 可靠性 / 状态外置

生产化加固层：**不让单点故障变成不可用，也不让状态锁死在单机进程里**。

### 可靠性机制

| 机制 | 文件 | 行为 | 配置 |
|------|------|------|------|
| 熔断器 | `constraints/circuit_breaker.py` | 连续失败 ≥ 阈值 → open 快速失败（返回降级文案，不再白烧 token）；冷却后半开试探 1 次，成功关闭 / 失败重开 | `CIRCUIT_BREAKER_ENABLED/THRESHOLD/COOLDOWN`（默认 5 次 / 30s） |
| 工具幂等 | `constraints/idempotency.py` | 写类工具（save_to_memory / run_hbase）按 (工具, 规范化参数) 去重，TTL 窗口内命中直接返回缓存结果；**失败不缓存**，允许重试真执行 | `TOOL_IDEMPOTENCY_ENABLED/TTL`（默认 300s） |
| 告警 | `observation/alerts.py` | 日志必写；配 Webhook 后后台线程推 Slack/钉钉/飞书，绝不阻塞事件循环 | `ALERT_ENABLED`、`ALERT_WEBHOOK_URL` |
| 回归基线 | `observation/regression.py` | eval 结束对比上次 pass_rate，下降 >5pp 自动告警；基线存 `logs/eval_baseline.json` | — |

告警触发点：熔断打开、Agent 超时（`orchestrator.py`）、评测退化（`eval_runner.py`）。

### 状态外置（Redis / 双后端）

统一原则：**Redis 不可用自动降级本地，绝不阻断启动。**

| 状态 | 默认 | 配置 `REDIS_URL` 后 | 降级 |
|------|------|-------------------|------|
| Web 会话（`server/endpoints/sessions.py`） | 内存（重启即清） | Redis 列表 + TTL 24h，多实例共享 | 回内存 |
| LangGraph checkpoint（`orchestrator.py`） | SQLite `db/agent_state.db` | `AsyncRedisSaver`，TTL 默认 24h 自动清理 | 初始化失败回 SQLite |
| 向量库（`memory/vector_store.py`） | Chroma 本地嵌入式 | `VECTOR_DB=milvus`：Milvus Lite（本地文件）或集群（`MILVUS_URI` 仅 http(s)） | — |

其他加固：

- Runner 注册表（`server/runner_wrapper.py`）：空闲 30min 自动回收，防并发内存泄漏；HITL resume 按 `session_id` 定位，不再依赖全局 active。
- SSE 断流（`server/endpoints/query.py` + `frontend/useSSE.ts`）：15s 心跳保活；`is_disconnected()` 感知断连并取消后台 Agent 任务（不烧 token）；前端指数退避重连（1s→2s→4s，默认 3 次）。

---

## 上下文管理 — `harness/context/`

| 组件 | 作用 |
|------|------|
| `system_prompt.py` | 运行时拼 Role/Tools/Workflow/Constraints/Output |
| `schema_discovery.py` | Schema Linking + 低基数列值级索引（≤15 distinct） |
| `sql_examples.py` | 检索式 Q→SQL few-shot（阈值 0.35） |
| `template_matcher.py` | 高频指标模板填槽（未命中回退 LLM） |
| `self_query.py` | 语义 + 元数据过滤（供 search_memory） |
| `token_budget.py` | 默认预算约 32k，warn 水位 0.7（single） |
| `hybrid_window_manager.py` | 近 6 条原文 / 7–14 成对摘要 / 更早全局摘要 |

---

## 记忆管理 — `harness/memory/`

```
Working     单轮 tool / graph state.results
Short-term  ConversationManager：max_recent=10 + 溢出 LLM 摘要
Long-term   VectorMemory(ChromaDB @ harness/memory/chroma_db) + user_memory SQL
Controller  闲聊跳过召回；元问题 list_recent；防污染不写库
回流        feedback.py：成功 SQL / HITL / 点赞 → 质量门 → sql_examples
```

召回默认 `top_k=3`、分数 ≥ 0.3。`AUTO_LEARN_SQL` 可关。

---

## 可观测性 — `harness/observation/`

| 组件 | 文件 | 机制 |
|------|------|------|
| TraceContext | `tracer.py` | 节点耗时/token/错误 → `logs/traces/*.jsonl` |
| Opik | `opik_tracing.py` / `opik_eval.py` | LangGraph 树、LLM span、Feedback、Experiment（`OPIK_ENABLED`） |
| cost | `cost.py` | token 人民币成本估算 |
| RouterCache hit_rate | multi/cache.py | 缓存命中率打到 Opik |
| pipeline_monitor | `pipeline_monitor/` | 演示用管道健康看板 |

---

## 双模式

### Single（`--mode single`）

```
memory_controller → vector recall
  → build_system_prompt(+模板 hint)
  → streaming_agent (max_turns=10, temperature=0)
       while tool_use: 执行 handlers → 截断结果 → loop
  → conversation + vector remember
```

适合探索、低延迟。HybridWindow 挂在 single loop。

### Multi（`--mode multi`）

```
guard_input → memory_controller → recall
  → MultiAgentRunner.run:
       router → clarify?
            → [data_quality?]
            → sql | strategy | hbase | hive
            → confidence_gate (≥0.7?)
            → analysis → reflection(≤2) → final_answer
       超时 → replan≤1 回 router
  → conversation + vector remember
```

适合复杂分析、HITL、专职 prompt、可观测流水线。子 Agent 内部 loop `max_turns=8`。

---

## 验证体系

```sh
uv run pytest tests/test_harness_smoke.py -q   # 冒烟，零 API
uv run pytest tests/ -q                        # 全量（当前 270 passed）
python -m tests.eval_runner --fast             # 护栏
python -m tests.eval_runner --full --judge     # + LLM-as-Judge (Kimi)
```

| 套件 | 规模 | 说明 |
|------|------|------|
| smoke | **62** | 六维模块 import、tool schema、agent 配线、护栏/HITL/图编译 |
| 单元+集成 | 见 `tests/test_*.py` | memory / hbase / recovery / feedback / orchestration … |
| eval_cases | 36+ | guardrail / routing / output_quality / edge |

---

## 项目结构

```
db-agent/
  main.py / app.py            CLI + Streamlit
  server/ / frontend/         FastAPI SSE + React
  harness/                    ★ 六维运行时（见上表）
  db/                         demo.db / agent_state.db / seed.py / user_memory.sql
  tests/                      smoke + 单元 + eval_runner
  docs/                       产品/项目文档（新手手册 / 操作与排障 / 项目介绍 / adr）
  scripts/                    demo / opik 脚本
  opik-platform/              本地 Opik Compose
  sidecar/                    learning / resume / archive / xmind / 旧笔记
```

---

## 数据层（Demo）

| 源 | 内容 |
|----|------|
| SQLite 业务 | departments / employees / products / customers / orders |
| Hive 模拟 | `ods_orders_hive` / `dwd_user_events` / `dim_products_hive` |
| HBase 内存 | orders / user_profile / product_catalog |
| 权限 | `agent_roles`(5) / `agent_users`(9) |
| 指标 | `db/metric_registry.db`（模板填槽） |

---

## Harness 覆盖对照 (learn-claude-code)

| 课程 | 机制 | db-agent 落点 |
|------|------|----------------|
| s01 Agent Loop | while + stop_reason | `orchestration/single/agent.py` + multi graph |
| s02 Tool Use | dispatch map | `tools/` + `@tool` |
| s03 Permission | 审批 | `constraints/entitlement.py` |
| s04 Hooks | 前后拦截 | `constraints/guardrails.py` L1/L2/L3 |
| s05 TodoWrite | 计划后执行 | `orchestration/multi/task_system.py` |
| s06 Subagent | 上下文隔离 | 6× `ConfiguredAgent` |
| s07 Skill / Prompt | 按需组装 | `context/system_prompt.py` |
| s08 Compact | 压缩 | `context/hybrid_window_manager.py` + `token_budget.py` |
| s09 Memory | 筛选整理 | `memory/` + controller |
| s11 Recovery | 重试换路 | `constraints/retry.py` + SQL 自愈 + replan≤1 |
| s12 Task / Checkpoint | 持久化 | Task board + `db/agent_state.db` |
| s15 Teams | 多 Agent | Router → 专业 Agent → Analysis → Reflection |
| — | 动态上下文 | schema_discovery + sql_examples + template_matcher |
| — | 自学习 | `memory/feedback.py` → sql_examples |
| — | 观测/评估 | observation/* + tests/eval_runner |

---

**造好 Harness。Agent 会完成剩下的。**
