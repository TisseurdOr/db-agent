# db-agent — 企业级自然语言数据库分析 Agent

自然语言查询 SQLite 数据库的多 Agent 系统。支持单 Agent 快速问答和多 Agent 编排（Router → SQL/Strategy/DataQuality → Analysis），内置 Entitlement 权限网关、HITL 人工审批、三层记忆系统和 Eval 评估体系。

## 快速开始

```bash
git clone https://github.com/TisseurdOr/db-agent.git
cd db-agent
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env             # 填 API key（见下方配置说明）
python main.py                   # 自动初始化 DB，进入 CLI
```

### 环境变量

```bash
ANTHROPIC_API_KEY=sk-your-key    # DeepSeek API key（兼容 Anthropic SDK）
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
ANTHROPIC_MODEL=deepseek-chat    # 可选 deepseek-v4-flash / deepseek-v4-pro

# Eval 独立评测模型（建议用不同模型避免偏差）
KIMI_API_KEY=sk-your-kimi-key
KIMI_BASE_URL=https://api.moonshot.cn/anthropic
KIMI_MODEL=kimi-k2.5

# Embedding
EMBEDDING_API_KEY=sk-your-dashscope-key
```

### CLI 用法

```bash
# 单 Agent 模式（默认）
python main.py

# 多 Agent 编排模式
python main.py --mode multi

# 指定用户角色（测试权限）
python main.py --mode multi --user analyst     # 数据分析师（可查 salary，触发审批）
python main.py --mode multi --user viewer      # 访客（只读，不能 run_query）
python main.py --mode multi --user xiaoyiming  # 市场部经理（行级过滤 dept_id=2）

# 多 Agent + 跳过首次数据质量扫描
python main.py --mode multi --no-dq
```

## 两种运行模式

### Single Agent 模式

单 Agent + Tool Loop（ReAct 模式）。Agent 自主决定何时查表、写 SQL、分析结果。

```
用户 query → Agent Loop（Observe → Think → Act）
              ├── list_tables / describe_table（探索结构）
              ├── run_query（执行 SELECT）
              └── analyze_results / render_chart（分析可视化）
```

### Multi Agent 模式

LangGraph 多 Agent 编排。Router 分析意图后分派给专业 Agent，结果汇总给 Analysis Agent 综合回答。

```
用户 query
    │
    ▼
┌──────────┐
│  Router  │  意图分类 + 任务分派（输出 JSON 执行计划）
└────┬─────┘
     │  plan: [{agent, task}, ...]
     ├────► DataQuality  扫一遍数据质量（NULL、日期连续性、异常值）
     ├────► SQL Agent    查数据库（只能 list/describe/run_query）
     ├────► Strategy     查公司制度文档（search_knowledge_base）
     ▼
┌──────────┐
│ Analysis │  综合中间结果 + 记忆，生成自然语言回答
└──────────┘
```

**路由逻辑**（Router 硬规则 > LLM）：
| 用户意图 | 路由 | 示例 |
|---------|------|------|
| 数据查询 | sql | "销售额最高的部门" |
| 制度/政策 | strategy | "销售提成比例是多少" |
| 对比/趋势 | sql + analysis | "对比华东和华南的销售趋势" |
| 元问题 | analysis | "刚才问了什么" |
| 闲聊 | 空 plan | "你好" |

## 安全模型：Entitlement + HITL

六层防御链，从 Prompt 软约束到 Tool 硬拦截。

```
用户 query
    │
    ▼
[1] guard_input()      输入护栏（SQL 注入 / prompt injection 检测）
    │
    ▼
[2] Router             意图分类（制度/数据/闲聊分流）
    │
    ▼
[3] System Prompt      Layer 1 软约束（声明用户能做什么、不能做什么）
    │
    ▼
[4] check_entitlement()  Layer 2 硬拦截（工具授权 + 表白名单 + 行级改写 + 文档过滤）
    │
    ▼
[5] HITL (interrupt)   Layer 3 人工审批（salary/cost/budget 敏感列触发）
    │
    ▼
[6] guard_output()     输出护栏（PII 泄露检测）
```

### 权限模型（资源级，非 SQL 解析）

| 维度 | 实现 | 示例 |
|------|------|------|
| 工具授权 | `allowed_tools` 白名单 | viewer 不能 run_query |
| 表级过滤 | `db_tables` 白名单 | support 只能看 products/customers/orders |
| 行级安全 | SQL 自动改写 `WHERE dept_id=X` | xiaoyiming 查员工只能看到市场部 |
| 文档过滤 | `docs_filter` 白名单 | support 只能搜技术文档和产品手册 |
| HITL 审批 | LangGraph `interrupt()` | analyst 查 salary → 弹出 y/n 确认 |

### 角色矩阵

| 角色 | run_query | 可查表 | 行级过滤 | HITL |
|------|-----------|--------|----------|------|
| dba | yes | 全部 | 无 | 无 |
| analyst | yes | 5 张业务表 | 无 | salary/cost/budget |
| manager | yes | 全部 | employees WHERE dept_id=X | salary/cost/budget |
| viewer | no | 4 张（无 employees） | 无 | 无 |
| support | yes | 3 张 | 无 | 无 |

权限数据存 DB 表（`agent_roles` + `agent_users`），生产环境改表即可生效，无需重新部署。

## Eval 评估体系

LLM-as-Judge 模式：用独立模型（Kimi kimi-k2.5）评测被测 Agent（DeepSeek），避免裁判偏袒自己。

```bash
# 跑全部 44 条测试
pytest tests/ -v

# 跑 Eval 评测（含 LLM judge）
python tests/eval_runner.py
```

评估维度：

| 维度 | 描述 |
|------|------|
| correctness | 数据是否准确（查错表、写错 SQL、算错数） |
| completeness | 是否回答了用户问的所有部分 |
| safety | 是否拒绝 DROP/INSERT/UPDATE |
| routing | 多 Agent 模式下 Router 是否选了正确的 Agent |

详见 `tests/eval_cases.py` — 44 条测试覆盖单 Agent 基础查询、SQL 安全、权限边界、多 Agent 路由。

## 记忆系统

三层记忆 + Token 预算管理。

```
Working Memory       当前轮 Tool 调用链的中间结果（单轮内可见）
Short-term Memory    最近 N 轮原文 + 超窗口消息的 LLM 压缩摘要（同会话内可见）
Long-term Memory     ChromaDB 向量检索 + user_memory SQLite 表（跨会话持久化）
```

- **向量记忆**：`main.py` 每轮前 `recall(user_query)` 语义检索，注入 System Prompt 做上下文 priming
- **对话压缩**：`ConversationManager` 滑动窗口 + LLM 摘要，避免上下文溢出
- **自指过滤**：元问题（"刚才问了什么"）走时间倒序检索，不依赖语义匹配

## 技术栈

| 层次 | 技术 | 选型理由 |
|------|------|---------|
| LLM | DeepSeek (兼容 Anthropic SDK) | 比 Claude Haiku 便宜 ~90%，Tool Use 能力足够 |
| 编排 | LangGraph + AsyncSqliteSaver | 图状态自动持久化，HITL 用原生 interrupt() |
| 向量库 | ChromaDB | pip install 零配置，生产换 Pinecone 改 5 行 |
| Embedding | DashScope qwen3.7-text-embedding | 中文更好，¥0.0007/1K tokens |
| 结构化存储 | SQLite | 零配置，权限数据 + 业务数据同库 |
| Eval | Kimi kimi-k2.5 | 独立模型做 Judge，避免自评偏差 |
| 可视化 | Matplotlib | 折线/柱状/饼图，render_chart Tool |

## 架构

```
main.py（CLI 入口 + 记忆编排层）
    │
    ├── 每轮前: recall(query) → 向量检索历史记忆
    ├── 每轮前: build_system_prompt(extra_context=记忆) → 注入 Prompt
    ├── 每轮后: remember(问答) → 写入向量库
    └── 每轮后: add_message → ConversationManager 对话管理
    │
    ├─ single 模式 ──► agent.py（ReAct Agent Loop + prompt caching）
    │                    ├── tools/schema.py   list_tables, describe_table
    │                    ├── tools/query.py    run_query（SELECT only + 权限）
    │                    ├── tools/analysis.py analyze_results, compare_periods
    │                    ├── tools/chart.py    render_chart（matplotlib）
    │                    └── tools/knowledge.py search_knowledge_base, save/read/search_memory
    │
    └─ multi 模式 ──► multi_agent/orchestrator.py（LangGraph 图编排）
                         ├── multi_agent/agents.py     4 个专业 Agent 定义
                         ├── multi_agent/base.py       轻量 Agent Loop
                         ├── multi_agent/entitlement.py 权限网关（工具/表/行/文档 + HITL）
                         ├── multi_agent/guardrails.py  输入/输出护栏
                         ├── multi_agent/cache.py      Router 缓存（同 query 复用 plan）
                         ├── multi_agent/state.py      MultiAgentState 定义
                         └── multi_agent/subagents/    （子图重构预留）
    │
    └── memory/  三层记忆系统
        ├── vector_store.py       ChromaDB 向量存储（remember/recall）
        ├── short_term_memory.py  ConversationManager（滑动窗口 + LLM 压缩）
        ├── token_budget.py       Token 估算 + 压缩阈值预警
        ├── hybrid_window_manager.py  3 层滑动窗口（L0 原文 / L1 轻摘要 / L2 全局摘要）
        └── long_term_memory.py   RAGPipeline（HyDE + Rerank）

utils/
    ├── llm.py      extract_text（兼容 ThinkingBlock）
    ├── cost.py     token 成本估算
    └── tracer.py   TraceContext（请求级调用链追踪，落盘 JSONL）

db/
    ├── seed.py            表结构 + 示例数据（6 部门、40 员工、15 产品、12 客户、338 订单）
    └── user_memory.sql   用户记忆表

tests/
    ├── test_agent.py      44 条 Agent 集成测试
    ├── test_memory.py     记忆系统单元测试
    ├── eval_runner.py     LLM-as-Judge 评测（Kimi 独立评测）
    └── eval_cases.py      评测用例定义
```

## 设计决策

**为什么不用 LangChain？** 先理解底层 Agent Loop，再用 LangGraph 做多 Agent 编排。LangGraph 的 State Graph 显式控制流比 LangChain 的 AgentExecutor 黑盒更可调试。

**为什么 Run Query 只允许 SELECT？** 安全考量。Tool 层硬拦截非 SELECT 语句，权限网关做表/行/列三级控制。

**为什么 Entitlement 不解析 SQL？** 正则提取 `FROM/JOIN` 表名做表级检查 + 字符串拼接做行级改写。不做完整 SQL 解析（正则不可靠），也不做列级过滤（颗粒度太细，生产无意义）。

**为什么 HITL 用 LangGraph 原生 interrupt()？** 相比自建审批队列，原生 interrupt() 自动持久化暂停点，Command(resume=...) 恢复执行，checkpointer 保证状态不丢。

**为什么 Eval 用不同模型？** 裁判不能是选手。DeepSeek 做被测 Agent，Kimi 做 Judge，避免模型自评偏差。

**为什么 Permission 数据存 DB 而不是代码？** 生产环境改权限 = 改一行 SQL 表数据，无需重新部署。代码里保留 fallback 默认值，DB 为空时自动降级。

**为什么 System Prompt 用 Python 函数而不是 .md 文件？** 可注入 db_type、user_role、extra_context（memory 检索结果注入点），MD 文件做不到动态变量替换。

## 测试

```bash
# 全部测试
pytest tests/ -v

# 只跑单元测试（不需要 API key，秒级）
pytest tests/ -v -k "test_memory"

# 只跑 Agent 集成测试
pytest tests/ -v -k "test_agent"

# Eval 评测（LLM-as-Judge，需要 Kimi API key）
python tests/eval_runner.py
```
