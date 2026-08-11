# db-agent — 企业级自然语言数据库分析 Agent

[![Test](https://github.com/TisseurdOr/db-agent/actions/workflows/test.yml/badge.svg)](https://github.com/TisseurdOr/db-agent/actions/workflows/test.yml)

自然语言查询 SQLite + HBase + Hive 的多 Agent 系统。支持 CLI / Streamlit / **React Web（FastAPI + SSE）** 三种入口；多 Agent 编排为 Router → SQL/Strategy/HBase/Hive/DataQuality → Analysis → Reflection。内置 Entitlement 权限网关、双层 HITL、三层记忆、**三层错误自愈**、**Schema Linking + SQL 模板优先 + 检索式 few-shot**、**自学习样例回流**、**Opik 可观测**和 Eval 评估体系。

> **Agent = 模型 + Harness。** 模型提供智能；Harness 提供工具、权限、记忆、编排、护栏、自愈与评测——本仓库做的是后者。详见下方「什么是 Harness」与 [`HARNESS.md`](HARNESS.md)。

## 从哪读起（导航）

按**运行路径**读，不要按文件夹扫：

| 优先级 | 路径 | 看什么 |
|--------|------|--------|
| 1 | `main.py` | CLI 入口：`--mode` / `--user`、记忆注入、HITL 交互 |
| 1 | `server/main.py` + `frontend/` | Web：FastAPI + SSE + React 聊天界面 |
| 2 | `agent.py` | single 模式：ReAct tool loop |
| 2 | `multi_agent/orchestrator.py` | multi 模式：LangGraph 编排（详见 `multi_agent/README.md`） |
| 3 | `tools/` | Agent 实际调用的能力（`query` 含权限与成功 SQL 捕获） |
| 3 | `tools/template_matcher.py` | 高频指标 SQL 模板优先匹配（零 LLM） |
| 3 | `multi_agent/entitlement.py` | 工具/表/行权限 + HITL（SQL 敏感列 + HBase 破坏性 op） |
| 4 | `memory/` | 短期压缩 + 向量长期记忆 |
| 4 | `rag/` | 检索式 few-shot、自学习回流、Self-Query |
| 4 | `multi_agent/schema_discovery.py` | Schema Linking + 值级索引 |
| 5 | `utils/opik_tracing.py` | Opik 上报（LangGraph / Anthropic / 入口 span） |
| 5 | `utils/retry.py` | API 指数退避重试 |
| 5 | `HARNESS.md` | Harness 工程架构全貌 |
| 5 | `opik-platform/README.md` | 本地 Opik 启停与评测回传 |
| 5 | `docs/2026-08-05_错误恢复操作手册.md` | 三层自愈操作说明 |
| 5 | `docs/2026-08-06_自学习闭环操作手册.md` | 样例回流操作说明 |
| 5 | `tests/`、`docs/troubleshooting.md` | 评测与排障 |
| — | `archive/` | **不在主路径**；旧实现 / WIP，见 `archive/README.md` |

```text
入口
  ├─ CLI        main.py --mode single|multi
  ├─ Streamlit  uv run streamlit run app.py
  └─ Web        uvicorn server.main:app + frontend (Vite)
                    │
                    ▼
              MultiAgentRunner (LangGraph)
                ├─ Router (硬规则 + 上下文继承 + LRU + LLM)
                ├─ sql / strategy / hbase / hive / data_quality
                │     └─ sql 失败可回 Router 重规划
                ├─ confidence_gate / clarify（可选 HITL）
                └─ analysis → reflection → final_answer
                     ↑
              tools/query.py ← check_entitlement + 成功 SQL 捕获
              tools/template_matcher.py ← 高频指标预填 SQL
              tools/hbase.py ← needs_approval_hbase
              rag/sql_examples + feedback ← few-shot 读 / 自学习写
              utils/tracer.py + utils/opik_tracing.py ← JSONL 双写 + Opik
```

## 什么是 Harness

业界常说：**Agent = 模型（LLM）+ Harness（操作环境）**。

| | 模型 | Harness（本仓库） |
|--|------|-------------------|
| 提供什么 | 推理、规划、语言 | 双手、双眼、规矩、记忆、评测 |
| 例子 | DeepSeek / Claude | 工具调用、权限、HITL、Router、自愈、few-shot、模板、Trace/Opik、Eval |
| 改什么 | 换 API / 换模型名 | 改代码与配置，不训练权重 |

本项目**不训练模型**，只为模型搭一套能在数据库领域稳定工作的环境——这就是 Harness。完整对照见 [`HARNESS.md`](HARNESS.md)（对齐 learn-claude-code 的 Agent Loop / Tool / Permission / Memory / Error Recovery 等机制）。

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

# Embedding（向量记忆 + Schema Linking + few-shot / 自学习）
EMBEDDING_API_KEY=sk-your-dashscope-key
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

# 错误恢复 / 自学习（可选）
LLM_MAX_RETRIES=3                # API 重试次数；0 = 关闭重试
LLM_RETRY_BASE_DELAY=1.0         # 退避基数（秒）
AUTO_LEARN_SQL=1                 # 成功 SQL 自动回流样例库；0 = 关闭

# Opik（默认关；本地 UI http://localhost:5173）
OPIK_ENABLED=0
OPIK_URL_OVERRIDE=http://localhost:5173/api
OPIK_PROJECT_NAME=db-agent
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

### 三种入口

| 入口 | 命令 | 说明 |
|------|------|------|
| CLI | `python main.py --mode multi` | 终端交互，HITL 用 y/n |
| Streamlit | `uv run streamlit run app.py` | 本机聊天页 + 侧边栏执行信息 |
| Web | 见下方「Web UI」 | React + FastAPI SSE，思考步骤 / 图表 / HITL 弹窗 / 点赞回流 |

### 现场演示

```bash
.venv/bin/python scripts/demo_recovery.py          # 三层自愈：retry / heal / replan
.venv/bin/python scripts/demo_feedback.py quality  # 自学习质量门（零 API）
.venv/bin/python scripts/demo_feedback.py loop     # 回流写入 → few-shot 召回
.venv/bin/python scripts/demo_feedback.py live     # 真跑 SQL Agent，观察 📥 自学习
./scripts/opik.sh up                               # 本地 Opik（需 Docker）
```

## Web UI（FastAPI + React）

浏览器里跑完整 multi 链路：SSE 推送节点进度、HITL 弹窗审批、ECharts 出图、👍/👎 回流样例库并写 Opik Feedback Score。

```bash
# 终端 1：API（默认 http://localhost:8000）
uv run uvicorn server.main:app --reload --port 8000

# 终端 2：前端（Vite 默认 5173；若本机已起 Opik，改用 3000）
cd frontend && npm install && npm run dev -- --port 3000
```

生产可先 `cd frontend && npm run build`，`server/main.py` 会挂载 `frontend/dist`。

| 方法 | 路径 | 作用 |
|------|------|------|
| POST | `/api/query` | SSE 流式问答（`session_id` / `datasource`） |
| POST | `/api/query/resume` | HITL 批准 / 拒绝后继续 |
| POST | `/api/feedback` | 点赞回流 few-shot + Opik 打分 |
| GET | `/api/sessions` | 会话列表（内存） |
| POST | `/api/datasource/upload` | CSV 导入独立 SQLite |
| POST | `/api/datasource/connect` | 连接外部 SQLite 文件 |
| GET | `/api/health` | 健康检查 |

侧边栏可切换 Demo SQLite /「上传 CSV」选项；CSV 上传与外部库连接走上述 API。当前查询主链路默认仍打 Demo 库。

**端口**：Opik UI 占用 `5173`。Web 前端请用 `3000`（CORS 已放行），或先 `./scripts/opik.sh down`。

### Docker（CLI）

根目录 `docker-compose.yml` 跑的是 **CLI 多 Agent**，不是 Web：

```bash
docker compose run --rm db-agent
```

镜像入口是 `python main.py`（默认 `--mode single`）。Web / Streamlit / Opik 需在本机另起，见上文与 [`opik-platform/README.md`](opik-platform/README.md)。

## 两种运行模式

### Single Agent 模式

单 Agent + Tool Loop（ReAct 模式）。Agent 自主决定何时查表、写 SQL、分析结果。

```
用户 query → Agent Loop（Observe → Think → Act）
              ├── list_tables / describe_table / discover_relevant_schema
              ├── match_sql_template（高频指标预填）→ run_query（SELECT）
              └── analyze_results / render_chart（分析可视化）
```

### Multi Agent 模式

LangGraph 多 Agent 编排。Router 分析意图后分派给专业 Agent，结果汇总给 Analysis，再经 Reflection 质检。

```
用户 query
    │
    ▼
┌──────────┐
│  Router  │  意图分类 + 任务分派（硬规则 > LLM > LRU 缓存）
└────┬─────┘
     │  plan: [{agent, task}, ...]
     ├────► DataQuality  扫一遍数据质量（NULL、日期连续性、异常值）
     ├────► SQL Agent    查 SQLite（discover_relevant_schema + list/describe/run_query）
     ├────► HBase Agent  查 NoSQL KV（scan/get/count，写操作需 HITL）
     ├────► Hive Agent   查数仓分层表（ods/dwd/dim，HiveQL 方言）
     ├────► Strategy     查制度文档 / 指标口径（search_knowledge_base、lookup_metric）
     │         │
     │         ▼（SQL 超轮数失败时）
     │    回 Router 重规划（上限 1 次）
     ▼
┌──────────┐
│ Analysis │  综合中间结果 + 记忆，生成自然语言回答
└────┬─────┘
     ▼
┌──────────┐
│Reflection│  完整性/真实性/可用性自审；不通过则退回 Analysis 重写（上限 2 次）
└──────────┘
```

可选节点：
- **clarify**：Router 置信度 low 时主动澄清（HITL）
- **confidence_gate**：SQL 自评分低时暂停请人确认
- **Task board**：Router plan 落盘为任务图（成功 ✓ / 失败 ✗）

**路由逻辑**（Router 硬规则 > LLM）：
| 用户意图 | 路由 | 示例 |
|---------|------|------|
| SQL 数据查询 | sql | "销售额最高的部门" |
| HBase NoSQL 查询 | hbase | "scan orders 表前 10 行" |
| Hive 数仓查询 | hive | "查 ods_orders_hive 华东地区订单" |
| 制度/政策 | strategy | "销售提成比例是多少" |
| 对比/趋势 | sql + analysis | "对比华东和华南的销售趋势" |
| 元问题 | analysis | "刚才问了什么" |
| 闲聊 | 空 plan | "你好" |

Router 支持上下文继承：如果上一轮路由到 hbase，本轮"继续查"会优先复用同一 Agent。

## 高频 SQL 模板优先

高频确定性问数不必每次都让模型从零写 SQL。`tools/template_matcher.py` 用关键词 + 实体槽位（部门 / 日期范围）匹配指标注册表（`db/metric_registry.db`）：

1. **命中** → 填槽得到 SQL（不调 LLM）
2. **未命中或填槽失败** → 返回 `None`，回退 LLM 自由生成

CLI single 模式命中后会把预填 SQL 注入上下文，模型可直接 `run_query` 或改写后再执行。种子模板覆盖部门销售额、月销售额、订单状态分布、产品销售排名等常见指标。复杂 JOIN / 多条件不强行模板化。

## 错误恢复：三层自愈

稳定性不靠「祈模型别错」，而靠明确预算的重试闭环：

| 层 | 触发 | 行为 | 上限 |
|----|------|------|------|
| API 重试 | 429 / 5xx / 连接超时 | 指数退避 + 抖动（`utils/retry.py`） | `LLM_MAX_RETRIES`（默认 3） |
| SQL 自愈 | `run_query` 报错 | 错误回喂 → `describe_table` 核对 → 重写 SQL | prompt 约束 2 次，`max_turns` 兜底 |
| 失败重规划 | Agent 超轮数（`is_agent_timeout`） | 带反馈回 Router 重排 plan；跳过硬规则与脏缓存 | 1 次 |

400/401 等不可恢复错误不重试。streaming 仅在尚未向终端输出内容时重试。

操作手册：[`docs/2026-08-05_错误恢复操作手册.md`](docs/2026-08-05_错误恢复操作手册.md)  
演示：`scripts/demo_recovery.py`

## 动态上下文：Schema Linking + few-shot

Text-to-SQL 准确率瓶颈常在上下文，不在换模型：

- **Schema Linking**（`multi_agent/schema_discovery.py`）：按问题语义检索相关表/字段，经 `discover_relevant_schema` 工具注入，避免整库塞进 prompt。
- **值级索引**：低基数 TEXT 列自动 `SELECT DISTINCT`，把真实取值（如 `region=华东/华南`）写进字段描述，专名题少靠猜。
- **检索式 few-shot**（`rag/sql_examples.py`，Vanna 模式）：提问时召回最相似的已验证 Q→SQL 注入 SQL Agent 上下文；无 embedding 时静默降级为空，不挡主流程。

## 自学习闭环

系统不只「出答案」，还会把验证过的 SQL 写回样例库，供下次 few-shot 使用：

```
成功 run_query / HITL 批准
  → 质量门（非超时、非纯错误 JSON、真实库 dry-run 跑通）
  → record_sql_example → Chroma sql_examples
  → 下次相似问题 → get_sql_fewshot 命中
```

- SQL **优先从 `run_query` 成功路径捕获**（`pop_last_successful_sql`），不赌模型把 SELECT 写进最终回答。
- `source`：`auto`（自动）/ `hitl`（人工批准）。
- `AUTO_LEARN_SQL=0` 可关写路径；读路径 few-shot 不受影响。
- Web 点赞（`POST /api/feedback`，`rating=up`）走同一套 `learn_from_success(..., source="user")`，并写 Opik Feedback Score。

操作手册：[`docs/2026-08-06_自学习闭环操作手册.md`](docs/2026-08-06_自学习闭环操作手册.md)  
演示：`scripts/demo_feedback.py`

## 安全模型：Entitlement + 双层 HITL

六层防御链，从 Prompt 软约束到 Tool 硬拦截。

```
用户 query
    │
    ▼
[1] guard_input()      输入护栏（SQL 注入 / prompt injection 检测）
    │
    ▼
[2] Router             意图分类（SQL/HBase/Hive/制度/闲聊分流）
    │
    ▼
[3] System Prompt      Layer 1 软约束（声明用户能做什么、不能做什么）
    │
    ▼
[4] check_entitlement()  Layer 2 硬拦截（工具授权 + 表白名单 + 行级改写 + 文档过滤）
    │
    ▼
[5] HITL (interrupt)   双层审批：
    │                   - SQL：salary/cost/budget 敏感列 → 弹出 y/n 确认
    │                   - HBase：put/delete/drop/truncate → 弹出 y/n 确认
    ▼
[6] guard_output()     输出护栏（PII 泄露检测）
```

### HITL 双层审批

| 层 | 数据源 | 触发条件 | 实现 |
|----|--------|---------|------|
| SQL HITL | SQLite 业务表 | SELECT 含 salary/cost/budget 列 | `needs_approval()` 检查列名 |
| HBase HITL | 内存 KV 模拟器 | put/delete/drop/truncate | `needs_approval_hbase()` 检查 op |

HITL 在 LangGraph graph 上下文内通过原生 `interrupt()` 暂停执行，graph 外调用写操作直接返回错误。

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
# 跑全部测试（冒烟 48 + 单元 91 + 集成 17）
pytest tests/ -v

# 只跑冒烟测试（1.3s，零 API 成本）
pytest tests/test_harness_smoke.py -v

# 跑 Eval 评测（含 LLM judge）
python tests/eval_runner.py

# 同步到 Opik Dataset / Experiment（需 OPIK_ENABLED=1）
.venv/bin/python -m tests.eval_runner --fast --opik
```

评估维度：

| 维度 | 描述 |
|------|------|
| correctness | 数据是否准确（查错表、写错 SQL、算错数） |
| completeness | 是否回答了用户问的所有部分 |
| safety | 是否拒绝 DROP/INSERT/UPDATE / HBase 破坏性操作 |
| routing | 多 Agent 模式下 Router 是否选了正确的 Agent（含 hbase/hive） |

详见 `tests/eval_cases.py` — 35 条测试覆盖单 Agent 基础查询、SQL 安全、权限边界、多 Agent 路由（含 HBase/Hive）。

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

## 可观测性

两套并存，互不替代：

| 层 | 实现 | 用途 |
|----|------|------|
| 本地审计 | `TraceContext` → `logs/traces/*.jsonl` | 节点耗时 / token / 错误；SQL 参数脱敏 |
| 平台看板 | Opik（`utils/opik_tracing.py`） | LangGraph 执行树、LLM span、Feedback、Dataset/Experiment |
| 路由缓存 | `RouterCache` LRU | 同样 query 跳过 LLM 路由 |
| 断点 | LangGraph Checkpointer → `db/agent_state.db` | HITL resume 不丢进度 |
| 任务板 | `task_system.py` → `.tasks/` | plan 成功 ✓ / 失败 ✗ |

```bash
python -m utils.tracer --today     # 本地 JSONL
./scripts/opik.sh up               # 本地 Opik UI http://localhost:5173
```

`.env` 设 `OPIK_ENABLED=1` 后，图编译走 `track_langgraph`，Anthropic 客户端走 `track_anthropic`。路由来源、few-shot 命中、脱敏 SQL、HITL、Reflection、Token 成本等写入当前 span metadata。关闭开关即不上报，本地 JSONL 仍写。

详见 [`opik-platform/README.md`](opik-platform/README.md)。

## 技术栈

| 层次 | 技术 | 选型理由 |
|------|------|---------|
| LLM | DeepSeek (兼容 Anthropic SDK) | 比 Claude Haiku 便宜 ~90%，Tool Use 能力足够 |
| 编排 | LangGraph + AsyncSqliteSaver | 图状态自动持久化，HITL 用原生 interrupt() |
| 向量库 | ChromaDB | pip install 零配置，生产换 Pinecone 改 5 行 |
| Embedding | DashScope qwen3.7-text-embedding | 中文更好，¥0.0007/1K tokens |
| 结构化存储 | SQLite | 零配置，权限数据 + 业务数据同库 |
| Eval | Kimi kimi-k2.5 | 独立模型做 Judge，避免自评偏差 |
| 可视化 | Matplotlib + ECharts | Agent 出 HTML 大屏；Web 用 ECharts 渲染 SSE 图表 |
| Web | FastAPI + SSE + React/Vite | 流式步骤、HITL 弹窗、点赞回流 |
| 可观测 | Opik（自建 Docker） | Trace / Feedback / Experiment；与 JSONL 双写 |
| 容器 | Docker Compose | CLI 镜像；Opik 平台另见 `opik-platform/` |

## 架构

```
入口
    ├── main.py              CLI + 记忆编排
    ├── app.py               Streamlit 聊天页
    └── server/              FastAPI（SSE / HITL resume / feedback / sessions / datasource）
         └── frontend/       React + Vite（思考步骤、图表、点赞、HITL 弹窗）

main / server 共用 MultiAgentRunner
    ├── 每轮前: recall(query) → 向量检索；模板匹配预填 SQL
    ├── 每轮后: remember(问答) + ConversationManager
    │
    ├─ single ──► agent.py（ReAct + prompt caching，15 tools）
    │                ├── schema / query / analysis / chart / knowledge
    │                ├── metrics.lookup_metric、template_matcher.match_sql_template
    │                └── hbase / hive
    │
    └─ multi ──► multi_agent/orchestrator.py（LangGraph + Opik wrap）
                     ├── agents / base / state（6 Agent）
                     ├── router（硬规则 > 继承 > LRU > LLM）
                     ├── entitlement + guardrails + confidence + task_system
                     └── schema_discovery（Schema Linking + 值级索引）

rag/          sql_examples（读）+ feedback（写 / 质量门）+ self_query
memory/       三层记忆 + TokenBudget + HybridWindow
utils/        tracer（JSONL）+ opik_tracing（平台）+ retry + cost + llm
opik-platform/  本地 Opik Docker Compose
pipeline_monitor/  数据管道健康检查演示（与对话链路独立）

scripts/      demo_recovery / demo_feedback / opik.sh / opik_setup_feedback
tests/        冒烟 + 编排/模板/任务板/HITL + Eval（可 --opik）
```

## 设计决策

**为什么不用 LangChain？** 先理解底层 Agent Loop，再用 LangGraph 做多 Agent 编排。LangGraph 的 State Graph 显式控制流比 LangChain 的 AgentExecutor 黑盒更可调试。

**为什么 Run Query 只允许 SELECT？** 安全考量。Tool 层硬拦截非 SELECT 语句，权限网关做表/行/列三级控制。

**为什么 HBase 用内存 KV 模拟？** 没有真实 HBase 集群。嵌套 dict 模拟 table→row_key→cf:col→value，API 与真实 HBase Shell 一致，可无缝切换。

**为什么 Entitlement 不解析 SQL？** 正则提取 `FROM/JOIN` 表名做表级检查 + 字符串拼接做行级改写。不做完整 SQL 解析（正则不可靠），也不做列级过滤（颗粒度太细，生产无意义）。

**为什么 HITL 用 LangGraph 原生 interrupt()？** 相比自建审批队列，原生 interrupt() 自动持久化暂停点，Command(resume=...) 恢复执行，checkpointer 保证状态不丢。HITL 覆盖两层：SQL 敏感列（salary/cost/budget）和 HBase 破坏性操作（put/delete/drop/truncate）。

**为什么 Eval 用不同模型？** 裁判不能是选手。DeepSeek 做被测 Agent，Kimi 做 Judge，避免模型自评偏差。

**为什么 Permission 数据存 DB 而不是代码？** 生产环境改权限 = 改一行 SQL 表数据，无需重新部署。代码里保留 fallback 默认值，DB 为空时自动降级。

**为什么 System Prompt 用 Python 函数而不是 .md 文件？** 可注入 db_type、user_role、extra_context（memory 检索结果注入点），MD 文件做不到动态变量替换。

**为什么自学习从 run_query 捕获 SQL，而不是从 Agent 回答里抽？** 模型常口头描述关联逻辑却不写完整 SELECT；工具成功路径才是执行事实。见 `docs/troubleshooting.md` §25。

**为什么 few-shot / 自学习失败不能挡主流程？** 增强能力可降级：无 embedding key 或 Chroma 异常时返回空 / False，SQL Agent 退回纯 schema 模式。

**为什么可观测要 JSONL + Opik 双写？** 本地 JSONL 保审计与离线排障（含 SQL 脱敏）；Opik 提供跨请求对比、Feedback、Dataset/Experiment。`OPIK_ENABLED=0` 时平台上报整段跳过。

**为什么高频问数先走模板？** 单表聚合 / 固定 JOIN 用关键词填槽即可，省一轮 SQL 生成；未命中再交给 LLM，避免把复杂题硬塞进模板。

## 测试

每次 push 自动跑 CI（GitHub Actions），覆盖冒烟 + HBase + 记忆 + self_query + 错误恢复相关测试。

```bash
# 全部测试
pytest tests/ -v

# Harness 冒烟测试（零 API 成本，每次 commit 必跑）
pytest tests/test_harness_smoke.py -v

# 错误恢复 / 自学习 / 上下文工程 / 模板 / 编排（零 API）
pytest tests/test_recovery.py tests/test_feedback.py tests/test_context_engineering.py \
      tests/test_template_matcher.py tests/test_orchestration.py tests/test_task_system.py -v

# HBase 模拟器测试（含 graph 内 HITL 集成测试）
pytest tests/test_hbase.py -v

# 只跑单元测试（不需要 API key，秒级）
pytest tests/ -v -k "test_memory"

# 只跑 Agent 集成测试
pytest tests/ -v -k "test_agent"

# Eval 评测（LLM-as-Judge，需要 Kimi API key）
python tests/eval_runner.py
```

| 测试层 | 文件 | 说明 |
|--------|------|------|
| 冒烟 | test_harness_smoke.py | 模块导入、tool schema、handler 配线、agent 一致性 |
| 单元 | test_hbase.py / test_memory.py | HBase 模拟器、记忆、token 预算 |
| 单元 | test_recovery.py | API 重试、SQL 自愈信号、重规划边、Task 失败态、trace 耗时 |
| 单元 | test_feedback.py / test_context_engineering.py | 自学习质量门、工具层 SQL 捕获、few-shot 种子、值级索引 |
| 单元 | test_template_matcher.py / test_orchestration.py / test_task_system.py | 模板填槽、编排与 Strategy 工具、任务板状态机 |
| 单元 | test_eval_hitl.py | Eval 与 HITL 相关边界 |
| 集成 | test_bigdata.py / test_agent.py | 路由权限、Agent loop（需 API） |
| Eval | eval_runner.py | LLM-as-Judge；加 `--opik` 上传 Dataset/Experiment |
