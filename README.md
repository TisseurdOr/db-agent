<div align="center">


# db-agent

<p>
  <a href="README.md"><img src="https://img.shields.io/badge/lang-%E4%B8%AD%E6%96%87-c0392b?style=for-the-badge" alt="中文"></a>
  <a href="README.en.md"><img src="https://img.shields.io/badge/lang-English-2e86de?style=for-the-badge" alt="English"></a>
</p>

### 自然语言数据库分析 Harness

问一句中文，查出 SQLite / HBase / Hive 的数，带权限、自愈和评测。
**Agent = 模型 + Harness。** 本仓库做的是后者。

[![Test](https://github.com/TisseurdOr/db-agent/actions/workflows/test.yml/badge.svg)](https://github.com/TisseurdOr/db-agent/actions/workflows/test.yml)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![LangGraph](https://img.shields.io/badge/LangGraph-StateGraph-1C3C3C)](https://github.com/langchain-ai/langgraph)
[![DeepSeek](https://img.shields.io/badge/LLM-DeepSeek-4D6BFE)](https://www.deepseek.com)
[![FastAPI](https://img.shields.io/badge/Web-FastAPI%20%2B%20SSE-009688)](https://fastapi.tiangolo.com)
[![Stars](https://img.shields.io/github/stars/TisseurdOr/db-agent?style=social)](https://github.com/TisseurdOr/db-agent/stargazers)

[功能](#一功能做什么) · [架构](#二架构怎么拆) · [生命周期](#三生命周期一条-query-怎么走) · [演进](#四演进怎么一步步长出来) · [快速开始](#快速开始) · [API](docs/新手手册/API.md)

</div>

---

这不是一个「让模型写 SQL」的玩具，而是一套 **让模型写 SQL 不出事** 的工程系统：工具真执行、权限硬拦截、失败可自愈、结果可评测。换 DeepSeek / Claude 只改 API，不改这套骨架。对照见 [`HARNESS.md`](HARNESS.md)。

> **范围说明**：工程机制按生产思路实现（权限 / HITL / 自愈 / 观测 / 可切换后端），已上线。
> SQLite 为真实本地库；**HBase 是本地内存模拟器**（API 对齐，无真实集群）。
> **Hive/数仓 agent 查的是真实的 Olist 五层数仓**（`db/warehouse.db`，由 `scripts/build_olist_warehouse.py` 从公开数据集构建），另提供 HiveQL 语法模板；接真实 Hive 集群只需换连接器。
> 测试全部离线可跑：LLM / Embedding 在测试里用脚本化 fake（`pytest tests/` 直接全绿，当前 **531** 条）。
> Web 可演示完整链路：可选 `WEB_API_TOKEN` 鉴权；会话默认内存、配置 `REDIS_URL` 后存 Redis。
> 向量库支持 ChromaDB / Milvus 双后端（`VECTOR_DB` 切换），Redis / Milvus 均「可选后端 + 自动降级」。

---

## 一、功能：做什么

业务痛点很具体：要背 SQL / Hive / HBase 三套语法、改字段要等数据组排期、市面 Text-to-SQL 往往只生成不执行、不鉴权、不改错。本项目要做的是 **说人话 → 查数 → 分析**，并把权限、可靠性、评测一起做完。

<p align="center">
  <img src="docs/diagrams/functional_layers.png" alt="功能分层" width="50%">
</p>
<p align="center"><sub>图 1 · 功能分层：问数能力叠权限、自愈、记忆、评测</sub></p>

| 能力 | 做什么 | 关键落点 |
|------|--------|----------|
| **多引擎问数** | 自然语言查 SQLite / 模拟 HBase / 模拟 Hive | `harness/tools/` + 6 个专职 Agent |
| **双模式编排** | Single：手写 ReAct；Multi：LangGraph 10 节点 | `orchestration/single` · `orchestration/multi` |
| **确定性优先** | 高频指标模板填槽；Router 硬规则 → LRU → LLM | `template_matcher` · `router.py` |
| **权限与 HITL** | 5 角色 RBAC（工具/表/行级）+ 敏感列 / 写操作人工审批 | `entitlement.py` · `interrupt()` |
| **三层护栏** | 输入注入检测 → 仅 SELECT → 输出 PII 过滤 | `guardrails.py` |
| **自愈与容错** | API 重试 → SQL 自愈 → 失败重规划 → 熔断 / 幂等 / 告警 | `retry` · `circuit_breaker` · `idempotency` |
| **记忆与自学习** | 短/长期记忆 + 混合检索（BM25 + 向量 RRF 融合）+ HyDE + LLM Rerank 精排 + Schema Linking + 成功 SQL 回流 few-shot | `memory/` · `long_term_memory.py` · `sql_examples.py` |
| **观测与评测** | Trace JSONL + Opik；47 条 Eval（Kimi Judge / DeepSeek 被测） | `observation/` · `tests/eval_*` |
| **三种入口** | CLI `db-agent` · Streamlit · FastAPI + React SSE | `main.py` · `app.py` · `server/` |

**问一句会怎样（示意）：**

```
用户      ❯ 销售额最高的部门是哪个？

db-agent  ❯ Router → sql
            模板未命中 → discover_relevant_schema → run_query
            SELECT d.name, SUM(o.amount) ...
            市场部  ¥ 1,284,300

用户      ❯ 再看一下市场部员工的薪资分布

db-agent  ❯ Entitlement：仅 dba/经理可查库
            HITL interrupt() → 等你 y/n
            批准后出分布，并写入 sql_examples 供下次 few-shot
```

---

## 二、架构：怎么拆

设计主线：**确定性优先** —— 模板 > LLM，正则 > LLM，硬规则 > 语义理解。运行时代码在 `harness/`，按六维物理拆分（面试与排障都按这条线读）：

| 维度 | 路径 | 职责 |
|------|------|------|
| 上下文 | `harness/context/` | Prompt 组装、Schema Linking、few-shot、SQL 模板、Token/窗口压缩 |
| 记忆 | `harness/memory/` | 短/长期记忆、controller、自学习回流；Chroma / Milvus |
| 工具 | `harness/tools/` | schema / query / analysis / chart / hbase / hive / knowledge |
| 编排 | `harness/orchestration/` | `single/` ReAct；`multi/` LangGraph 6 Agent + Router |
| 观测 | `harness/observation/` | Trace、Opik、cost、告警、ops metrics |
| 约束 | `harness/constraints/` | RBAC、护栏、confidence/HITL、retry、熔断、幂等 |

<p align="center">
  <img src="docs/diagrams/mechanism-overview.png" alt="工程机制全景" width="50%">
</p>
<p align="center"><sub>图 2 · 工程机制全景：入口 → Harness 六维 → 可靠性横切</sub></p>

<p align="center">
  <img src="docs/diagrams/orchestration_flow.png" alt="多 Agent 编排" width="100%">
</p>
<p align="center"><sub>图 3 · Multi 编排：Router → 专职 Agent → Analysis / Reflection</sub></p>

<details>
<summary>六维细节图（点击展开）</summary>

<p align="center"><img src="docs/diagrams/tools.png" alt="工具系统" width="100%"></p>
<p align="center"><sub>工具系统</sub></p>

<p align="center"><img src="docs/diagrams/context_management.png" alt="上下文管理" width="100%"></p>
<p align="center"><sub>上下文管理</sub></p>

<p align="center"><img src="docs/diagrams/memory.png" alt="记忆管理" width="100%"></p>
<p align="center"><sub>记忆管理</sub></p>

<p align="center"><img src="docs/diagrams/constraints.png" alt="约束与权限" width="100%"></p>
<p align="center"><sub>约束与权限</sub></p>

<p align="center"><img src="docs/diagrams/observability.png" alt="观测与评测" width="100%"></p>
<p align="center"><sub>观测与评测</sub></p>

</details>

```text
入口
  ├─ CLI        main.py / db-agent --mode single|multi
  ├─ Streamlit  uv run streamlit run app.py
  └─ Web        uvicorn server.main:app + frontend (Vite)
                    │
                    ▼
              MultiAgentRunner (LangGraph)
                ├─ Router (硬规则 + 上下文继承 + LRU + LLM)
                ├─ sql / strategy / hbase / hive / data_quality
                │     └─ sql 失败可回 Router 重规划（≤1）
                ├─ confidence_gate / clarify（可选 HITL）
                └─ analysis → reflection（≤2）→ final_answer
```

**6 个专职 Agent**（互不看见对方 tool 轨迹，只收 task、回 result）：

| Agent | 职责 | 典型工具 |
|-------|------|----------|
| SQL | 只查数据 | `discover_relevant_schema` / `run_query` |
| Analysis | 只分析、不写 SQL | `analyze_results` / `render_chart` |
| Strategy | 制度 / 指标口径 | `search_knowledge_base` / `lookup_metric` |
| HBase | KV 操作（写操作 HITL） | `run_hbase` |
| Hive（数仓） | 查 Olist 数仓 warehouse.db + HiveQL 语法 | `query_warehouse` / `list_warehouse_tables` / `search_hive_syntax` |
| DataQuality | 质量扫描（可选） | 行数 / NULL / 日期连续性 |

学习材料、简历、旧实验在 `sidecar/`，不参与运行。更细的机制说明见 [`HARNESS.md`](HARNESS.md) · [`docs/项目介绍/engineering-mechanisms.md`](docs/项目介绍/engineering-mechanisms.md)。

---

## 三、生命周期：一条 Query 怎么走

从入口到落盘，一条自然语言问数大致经过下面这条链（Multi 模式）：

<p align="center">
  <img src="docs/diagrams/request_lifecycle.png" alt="请求生命周期" width="100%">
</p>
<p align="center"><sub>图 4 · 请求生命周期（入口 → 落盘）</sub></p>

```text
入口预处理
  → guard_input（注入 / 空 / 超长，零 token）
  → 记忆召回（HyDE + LLM Rerank 精排）+（可选）模板匹配
  → Router：硬规则 > 上下文继承 > LRU > LLM
       ├─ confidence=low → clarify（interrupt 澄清）
       └─ plan 落 Task board；可选插入 data_quality
  → 专职 Agent（sql / hbase / hive / strategy …）
       · Schema Linking + few-shot
       · run_query：仅 SELECT → Entitlement → 敏感列 HITL
       · 成功 SQL 捕获（供自学习）
       · 超时 → 带反馈回 Router 重规划（≤1）
  → confidence_gate（自评分 < 0.7 则 interrupt）
  → Analysis → Reflection（不通过退回 ≤2）
  → guard_output（PII）
  → Trace JSONL + Opik + 运维指标
  → 短/长期记忆写入；Web 推 SSE done
```

<p align="center">
  <img src="docs/diagrams/query-flow.png" alt="Query 流转" width="100%">
</p>
<p align="center"><sub>图 5 · Query 流转细节</sub></p>

**横切能力（不单独成节点，但贯穿全程）：**

| 层 | 行为 |
|----|------|
| API 重试 | 429 / 5xx / 超时 → 指数退避 + 抖动 |
| SQL 自愈 | 错误回喂 → 对表结构 → 重写（prompt 约束约 2 次） |
| 熔断降级 | 连续失败达阈值 → 快速失败；冷却后半开试探 |
| 工具幂等 | 写工具同参数在 TTL 内不重复执行 |
| 观测 | 本地 JSONL 审计 + Opik 执行树；可选 Webhook 告警 |

**安全链：**

<p align="center">
  <img src="docs/diagrams/security-chain.png" alt="安全链路" width="100%">
</p>
<p align="center"><sub>图 6 · 输入护栏 → RBAC → HITL → 输出护栏</sub></p>

**自愈：**

<p align="center">
  <img src="docs/diagrams/self-healing.png" alt="三层自愈" width="50%">
</p>
<p align="center"><sub>图 7 · 重试 → SQL 自愈 / 重规划 → 熔断降级</sub></p>

**记忆 / 观测闭环：**

<p align="center">
  <img src="docs/diagrams/memory-obs-loop.png" alt="记忆与观测闭环" width="50%">
</p>
<p align="center"><sub>图 8 · 召回 → 执行 → 自学习回流 → Trace / Opik</sub></p>

Single 模式差异：不经 Router / DQ / Confidence Gate / Analysis / Reflection，入口后直接进 ReAct loop（约 15 个 tools）；HITL 退化为「需要审批」标记，没有原生 interrupt 暂停/恢复。

---

## 四、演进：怎么一步步长出来

**每层都是被真实问题逼出来的，不是堆功能。**

<p align="center">
  <img src="docs/diagrams/five_stage_evolution.png" alt="五阶段演进" width="150%">
</p>
<p align="center"><sub>图 9 · 五阶段演进：单 Agent → 多 Agent → 权限 → 可靠性 → 工程化</sub></p>

| 阶段 | 核心问题 | 关键动作 | 产出 |
|------|----------|----------|------|
| **一 · 单 Agent** | 模型能干活 | 手写 ReAct（弃 AgentExecutor）；工具真执行；结构化错误；prompt caching | 能稳定执行的 CLI Agent |
| **二 · 多 Agent** | 多引擎协同 | LangGraph 10 节点 / 6 Agent；Router 四层短路；失败重规划 | 多引擎协同编排 |
| **三 · 权限安全** | Prompt 拦不住越权 | 5 角色 RBAC；行级 WHERE 改写；三层护栏；HITL `interrupt()` | 权限下沉工具层 |
| **四 · 可靠性** | 挂了也不崩 | 重试 → SQL 自愈 → 重规划 → **熔断 / 幂等 / 告警**；SSE 断流 | 可靠性闭环 |
| **五 · 工程化** | demo → 产品 | `db-agent` CLI；531 离线测试；47 Eval + Golden Set；Redis / Milvus 可切换；质量门禁 | 可演示、可 CI 的产品形态 |


### 设计取舍（为什么这样）

- **不用 AgentExecutor**：先把手写 Loop 写明白，再用 LangGraph 做显式 State Graph。
- **Run Query 只 SELECT**：写操作在 Tool 层拦掉。
- **HBase / Hive 用模拟器**：API 对齐可替换；POC 证明编排，不假装接了集群。
- **HITL 用原生 `interrupt()`**：暂停点进 checkpointer，`Command(resume=...)` 接着跑。
- **Judge 和被测不是同一模型**：Eval 用 Kimi，被测用 DeepSeek。
- **自学习抓 `run_query` 成功 SQL**：不从模型口头描述里抽；含敏感列的不回流。
- **熔断 / 幂等**：连续失败时重试=烧钱；重试/重规划会让写工具跑两次——这两层都是踩坑后补上的。

---

## 从哪读起

按运行路径读，不要按文件夹扫：

| 优先级 | 路径 | 看什么 |
|--------|------|--------|
| 1 | `main.py` | CLI：`--mode` / `--user`、记忆注入、HITL |
| 1 | `server/main.py` + `frontend/` | Web：FastAPI + SSE + React |
| 2 | `harness/orchestration/single/agent.py` | single：ReAct loop |
| 2 | `harness/orchestration/multi/` | multi：graph / runner / nodes / router |
| 3 | `harness/tools/` · `harness/constraints/entitlement.py` | 能力与权限 |
| 4 | `harness/memory/` · `harness/context/` | 记忆、few-shot、Schema Linking |
| 5 | `HARNESS.md` · `tests/` · `docs/操作与排障/troubleshooting.md` · `docs/新手手册/用户手册.md` | 架构、评测、排障、入门 |

---

## 快速开始

```bash
git clone https://github.com/TisseurdOr/db-agent.git
cd db-agent
uv sync && source .venv/bin/activate        # 安装依赖 + 生成 db-agent 命令
cp .env.example .env                        # 至少填 ANTHROPIC_API_KEY
db-agent --mode multi                       # 进入 CLI（等价于 python main.py --mode multi）
```

> 缺 key 启动会友好提示（`cp .env.example .env`），不会吐 traceback。

```bash
db-agent                                   # single（默认）
db-agent --mode multi
db-agent --mode multi --user dba           # 全库可查
db-agent --mode multi --user zhoufang     # 经理：行级隔离 + 敏感列 HITL
db-agent --mode multi --user analyst     # 不能访问数据库（仅知识库）
db-agent --mode multi --user viewer      # 不能访问数据库
db-agent --mode multi --user xiaoyiming    # 行级 dept_id=2
```

| 入口 | 命令 |
|------|------|
| CLI | `db-agent --mode multi` |
| Streamlit | `uv run streamlit run app.py` |
| Web | 见下方；思考步骤 / 图表 / HITL 弹窗 / 点赞回流 |

```bash
.venv/bin/python scripts/demo_recovery.py          # retry / heal / replan
.venv/bin/python scripts/demo_feedback.py quality  # 自学习质量门（零 API）
./scripts/opik.sh up                               # 本地 Opik（需 Docker）
```

<details>
<summary>环境变量</summary>

```bash
ANTHROPIC_API_KEY=sk-your-key
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
ANTHROPIC_MODEL=deepseek-chat    # 或 deepseek-v4-flash / deepseek-v4-pro

KIMI_API_KEY=sk-your-kimi-key    # Eval Judge，建议与被测模型分开
KIMI_BASE_URL=https://api.moonshot.cn/anthropic
KIMI_MODEL=kimi-k2.5

EMBEDDING_API_KEY=sk-your-dashscope-key
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

LLM_MAX_RETRIES=3                # API 重试次数
CIRCUIT_BREAKER_THRESHOLD=5      # 熔断：连续失败多少次快速失败
TOOL_IDEMPOTENCY_TTL=300         # 写工具幂等窗口（秒）
ALERT_WEBHOOK_URL=               # 告警推送（Slack/钉钉/飞书），空=仅日志
REDIS_URL=                       # 状态外置：会话 + checkpoint 存 Redis
VECTOR_DB=chroma                 # 向量库后端：chroma / milvus
MILVUS_URI=                      # Milvus 集群地址（http://...），本地 Lite 免配
AUTO_LEARN_SQL=1                 # 成功 SQL 回流样例库；0 = 关
WEB_API_TOKEN=                   # Web 可选鉴权；空=不鉴权

OPIK_ENABLED=0
OPIK_URL_OVERRIDE=http://localhost:5173/api
OPIK_PROJECT_NAME=db-agent
```

</details>

---

## Web UI（FastAPI + React）

SSE 推节点进度、HITL 弹窗、ECharts 出图、👍/👎 回流 few-shot 并写 Opik Feedback Score。

```bash
# 终端 1
uv run uvicorn server.main:app --reload --port 8000

# 终端 2（Opik 占用 5173 时改 3000）——开发联调用这个
cd frontend && npm install && npm run dev -- --port 3000
# 浏览器打开 http://127.0.0.1:3000
```

只开后端时访问 **http://127.0.0.1:8000** 用的是打包产物 `frontend/dist`：**改了前端源码必须** `cd frontend && npm run build` 再硬刷新，否则会一直是旧 UI（典型坑：Database 请求不带 `user_id` → 全员 403）。细节见 **[`docs/新手手册/Web操作手册.md`](docs/新手手册/Web操作手册.md)** §1.1 / §7.5–7.6。

### API 文档

完整说明见 **[`docs/新手手册/API.md`](docs/新手手册/API.md)**。服务起来后也可直接打开交互式文档：

| | URL |
|--|-----|
| Swagger UI | http://localhost:8000/docs |
| ReDoc | http://localhost:8000/redoc |
| OpenAPI JSON | http://localhost:8000/openapi.json |

常用接口速查：

| 方法 | 路径 | 作用 |
|------|------|------|
| POST | `/api/query` | SSE 流式问答 |
| POST | `/api/query/resume` | HITL 批准 / 拒绝后继续 |
| POST | `/api/feedback` | 点赞回流 + Opik 打分 |
| GET | `/api/sessions` | 会话列表（Redis / 内存） |
| GET | `/api/overview` | Architecture 驾驶舱 |
| GET | `/api/memory` | 记忆浏览 |
| GET | `/api/database` | 库浏览器 |
| POST | `/api/datasource/upload` | CSV → 独立 SQLite |
| POST | `/api/datasource/connect` | 连接外部 SQLite |
| GET | `/api/health` | 健康检查（开放） |
| GET | `/api/metrics` | Prometheus 文本指标（开放） |

`docker compose run --rm db-agent` 跑的是 **CLI**，不是 Web。

Web 鉴权三档：**每用户 token**（`WEB_API_TOKENS="dba:tokA,zhoufang:tokB"`，身份由 token 推导，客户端传入的 `user_id` 一律忽略——RBAC 是真边界）；**单 token**（`WEB_API_TOKEN`，身份取服务端配置）；**都不配 = 演示模式**（不鉴权，前端可切角色，RBAC 仅演示、不保护真实数据）。除 `/api/health`、`/api/metrics` 外所有接口要求 `Authorization: Bearer <token>` 或 `X-API-Key: <token>`。

Web Runner：按 `session_id` 管理并带空闲 TTL 回收（默认 30 分钟）；HITL resume 按 session 精确定位，不再依赖全局 active。

> **状态外置（可选）**：配置 `REDIS_URL` 后，Web 会话与 Agent checkpoint 走 Redis（需 redis-stack）；不可用时自动回落内存 / SQLite。
> **向量后端可切换**：`VECTOR_DB=chroma`（默认）或 `milvus`。

---

## 两种运行模式 · 安全 · Eval（速查）

### Single vs Multi

```
Single:  用户 → Observe → Think → Act（全量 tools）
Multi:   用户 → Router → [DQ?] → sql|hbase|hive|strategy
              → confidence_gate → analysis → reflection → done
```

### 角色权限（摘要）

| 角色 | 数据库访问 | 可查表 | 行级 | HITL |
|------|------------|--------|------|------|
| dba | yes（含 HBase） | 全部 | 无 | 无 |
| manager | yes | 全部 | employees.dept_id | salary/cost/budget |
| analyst | yes | 11 张白名单（8 业务表 + 3 Hive 表） | 无 | salary/cost/budget |
| viewer | no（仅知识库） | — | — | — |
| support | no（仅知识库） | — | — | — |

权限在 `agent_roles` / `agent_users` 表里，改表即生效。

### Eval

```bash
pytest tests/ -v                               # 全量离线用例（531，默认不跑 live）
pytest tests/ -m live                          # 真实 LLM 端到端 smoke（需 API key，默认排除）
pytest tests/ --cov --cov-fail-under=60        # 带覆盖率门禁（CI 已接入）
python tests/eval_runner.py --fast             # 护栏用例（零 API，已接入 CI）
python tests/eval_runner.py --full             # 全量评测（需 API key）
```

被测：DeepSeek。Judge：Kimi。`tests/eval_cases.py`：47 条 Eval + `tests/golden_set.json` 30 条 Golden Set（20 正例事实断言 + 10 负例拦截）。

### 代码质量

```bash
ruff check harness server db main.py app.py scripts tests   # 全绿
pyright                                                      # 0 errors（basic 模式）
pre-commit run --all-files
```

> `tests/test_e2e_live.py` 是唯一会真调模型/真查库的用例，标记 `live`，默认被 `addopts = -m 'not live'` 排除，CI 不跑、不烧 token。

---

<details>
<summary>技术栈与测试说明</summary>

| 层次 | 技术 |
|------|------|
| LLM | DeepSeek（Anthropic 兼容 SDK） |
| 编排 | LangGraph + SQLite / Redis Checkpointer |
| 向量 | ChromaDB / Milvus；Embedding 用 DashScope |
| Web | FastAPI + SSE + React/Vite |
| Eval | Kimi 做 Judge |

全量测试离线可跑（LLM / Embedding 均 fake）。Redis / Milvus 专项用例在无对应服务时自动跳过。Push 到 `main` / `master` 会跑 GitHub Actions（pytest 全量 + 护栏 eval）。

</details>

<div align="center">

**Agent = 模型 + Harness。** 模型负责想，这套代码负责查、拦、记、改、评。

<sub>
图：<a href="docs/diagrams/">docs/diagrams/</a> ·
机制：<a href="HARNESS.md">HARNESS.md</a> ·
案例：<a href="docs/项目介绍/项目案例.md">项目案例</a> ·
工程图解：<a href="docs/项目介绍/engineering-mechanisms.md">engineering-mechanisms</a> ·
复盘文章：<a href="docs/项目介绍/项目复盘-可复用模块库.md">可复用模块库</a>
</sub>

</div>

### Olist 公共数仓

仓库包含一套可复现的 Olist 公共电商五层数仓：ODS → DIM → DWD → DWS → ADS。

```bash
.venv/bin/python scripts/build_olist_warehouse.py
.venv/bin/python scripts/build_olist_warehouse.py --validate-only
```

原始数据会下载到 `data/raw/olist/`，数仓落在独立的 `db/warehouse.db`。建模和查询说明见 [`docs/warehouse/olist.md`](docs/warehouse/olist.md)。
这是公共测试数据，不代表真实用户反馈或商业增长。
