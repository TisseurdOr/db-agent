# 数据库助手 Agent

自然语言查询数据库的 AI Agent。基于 Claude API 兼容协议，用 Agent Loop 自主探索表结构、生成 SQL、分析结果。支持三层记忆系统（工作记忆 / 短期记忆 / 长期向量记忆）和 RAG 检索增强。

## 架构

```
用户 (CLI)
    │
    ▼
┌──────────────┐   System Prompt + 记忆上下文   ┌──────────────────┐
│   main.py    │ ────────────────────────────► │  LLM API         │
│ 注册 9 Tools │                               │  (DeepSeek 等)   │
│ 记忆编排     │                               └────────▲─────────┘
└──────┬───────┘                                        │
       │                                                │ Think
       ▼                                                │
┌──────────────┐  tools=TOOLS                           │
│  agent.py    │ ──────────────────────────────────────►│
│ Agent Loop   │◄───────────────────────────────────────┘
│ Observe→     │        tool_use / text
│ Think→Act    │
└──────┬───────┘
       │ Act: handlers[name]
       ▼
┌──────────────────────────────────────────────────────────────┐
│ tools/                                                       │
│  schema.py    list_tables / describe_table / get_schema_summary│
│  query.py     run_query (只读 SELECT)                         │
│  analysis.py  analyze_results / compare_periods               │
│  knowledge.py search_knowledge_base / save_to_memory / read_memory│
└──────────────────────────┬───────────────────────────────────┘
                           ▼
              ┌─────────────────────┐
              │ SQLite DB + 向量库   │
              │ db/demo.db          │
              │ memory/chroma_db/   │
              └─────────────────────┘

── 记忆系统（每轮对话自动运行）──
       ┌────────────────────┐
       │ ConversationManager │  短期：最近 N 轮原文 + 早期压缩摘要
       │ (short_term_memory) │
       └────────┬───────────┘
                │ recall(user_query) → 注入 System Prompt
       ┌────────▼───────────┐
       │   VectorMemory     │  长期：ChromaDB 向量检索 + 语义匹配
       │   (vector_store)   │
       └────────────────────┘
```

## 技术栈

- Python 3.12, asyncio
- Claude API 兼容协议（Anthropic SDK → DeepSeek endpoint）
- SQLite — 结构化数据存储
- ChromaDB — 向量存储 + 语义检索
- DashScope (qwen3.7-text-embedding) — embedding 模型
- Prompt Caching（ephemeral cache，节省 ~99.5% system prompt 成本）
- @tool 装饰器自动生成 JSON Schema（type hints + docstring → schema）

## 快速开始

```bash
cp .env.example .env   # 填入 ANTHROPIC_API_KEY + EMBEDDING_API_KEY
uv sync
uv run python main.py   # 一键启动（自动 init_db）
```

可选参数：
```bash
uv run python main.py --model deepseek-chat      # 默认，便宜快速
uv run python main.py --model deepseek-reasoner  # 复杂查询用推理模型
```

## 模型选型

| 模型 | 适用场景 | 单次查询估算 |
|------|---------|-------------|
| `deepseek-chat`（默认） | 日常查询、Tool 调用 | ~¥0.01-0.03 |
| `deepseek-reasoner` | 复杂多步推理 | ~¥0.05-0.15 |

**选型依据：**
- DeepSeek API 兼容 Anthropic SDK，零代码改动切换
- `deepseek-chat` Tool Use 能力足够处理绝大多数 DB 查询场景（list → describe → query → analyze）
- 比 Claude Haiku 便宜 ~90%，适合开发阶段高频测试
- 每轮 Agent Loop 消耗 ~2500 tokens（含 System Prompt + Tool Defs + 对话历史），cache hit 后 ~1450 tokens
- 按每天 50 次查询算，月成本 < ¥15

**Embedding 选型：**
- 选用 DashScope `qwen3.7-text-embedding`（OpenAI 兼容接口）
- 512 维向量，中文语义匹配效果好，¥0.0007/1K tokens
- 日均 300 条 embedding 成本 < ¥0.01

## Memory 系统（三层记忆模型）

```
┌─────────────────────────────────────────────────────┐
│                 Working Memory                       │
│          当前这一轮的"草稿纸"                           │
│   变量、中间推理、Tool 返回结果                          │
│   实现: CoT 指令 + Tool 结果摘要                       │
├─────────────────────────────────────────────────────┤
│                Short-term Memory                     │
│          当前会话的"聊天记录"                            │
│   混合策略: 最近 10 轮保留原文 + 更早的压缩为摘要         │
│   压缩: DeepSeek 将旧消息总结为 ≤200 字中文摘要          │
│   注入: build_context() → System Prompt 上下文块       │
├─────────────────────────────────────────────────────┤
│                 Long-term Memory                     │
│          跨会话的"知识库 + 用户档案"                     │
│   结构化: SQLite user_memory 表（偏好/备注/洞察）       │
│   非结构化: ChromaDB 向量检索（语义匹配历史对话）         │
│   检索: 每轮对话前 recall(user_query) → 注入 Prompt     │
└─────────────────────────────────────────────────────┘
```

**Short-term Memory 细节：**
- 最近 10 条消息保留原文，超过窗口 → 压缩最旧的一半
- 压缩用 `compress_history()` 调 LLM 生成摘要，token 成本极低
- 摘要合并策略：新摘要追加到已有摘要后面
- `token_estimate()` 实时追踪 recent/summary/total 分项 token 占用
- 日志格式：`[memory] 本轮 tokens≈1234 (原文 800/5条, 摘要 434/12条已压缩)`

**Long-term Memory 细节：**
- 结构化路径（SQLite）：用户偏好、实体关系 → `save_to_memory` / `read_memory`
- 向量路径（ChromaDB）：对话片段 → embedding → `remember` / `recall`
- 选型依据：结构化数据不需要向量检索（精确匹配更快更准），向量检索只用在语义模糊的自然语言回忆场景
- 元数据过滤：`recall(query, memory_type="preference")` 可按类型精确筛选
- 兼容性：旧数据无 `user_id` 字段时自动退回不过滤

## RAG 检索增强

解决两个核心问题：(1) 知识时效——LLM 训练数据有截止日期；(2) 幻觉——没资料时 LLM 会编，有资料时能引用。

实现链路：
```
用户 query → recall(query) → ChromaDB 向量检索 → top-K 相关记忆
→ 注入 System Prompt (extra_context) → LLM 基于记忆回答
```

支持的能力（`memory/long_term_memory.py` 的 `RAGPipeline`）：
- **HyDE**：短查询（< 20 字）先生成假设答案再 embedding，提高检索精度
- **Rerank**：粗排取 top_k×3 候选 → LLM 精排取 top_k，提高准确率
- **元数据过滤**：向量检索 + metadata where 条件，支持按 user_id、memory_type 过滤
- **元数据过滤**：支持按 memory_type 等维度筛选，API 层预留了 user_id 参数

## 项目结构

```
db-agent/
├── pyproject.toml
├── .env / .env.example
├── .gitignore
├── README.md
├── main.py                    # CLI 入口（支持 --model，记忆编排）
├── agent.py                   # streaming agent loop + prompt caching
├── prompts/
│   └── system_prompt.py       # Prompt 工厂函数（五层结构，可注入变量）
├── tools/
│   ├── __init__.py            # @tool 装饰器（自动生成 JSON Schema）
│   ├── schema.py              # list_tables, describe_table, get_schema_summary
│   ├── query.py               # run_query (只读 SELECT + 白名单校验)
│   ├── analysis.py            # analyze_results, compare_periods（同比/环比）
│   └── knowledge.py           # search_knowledge_base, save_to_memory, read_memory
├── memory/
│   ├── short_term_memory.py   # ConversationManager（混合策略对话管理）
│   ├── vector_store.py        # VectorMemory（ChromaDB 向量记忆）
│   └── long_term_memory.py    # RAGPipeline（HyDE + Rerank + 检索编排）
├── utils/
│   └── llm.py                 # extract_text + logger（安全取文字、共享日志）
├── db/
│   └── seed.py                # SQLite 初始化 + 150+ 行示例数据
└── tests/
    ├── test_agent.py           # 17 个测试（12 单元 + 5 集成）
    └── test_memory.py          # 11 个测试（向量记忆 + 对话管理）
```

## 设计决策

- **为什么不用 LangChain？** 先理解底层 Agent Loop（Observe → Think → Act）。Phase 4 引入 LangGraph 做多 Agent 编排。
- **为什么只允许 SELECT？** 安全考量。SQL 白名单校验 + 只读限制，防止 LLM 生成 DROP/UPDATE。
- **为什么用 SQLite？** 零配置。生产环境换 PostgreSQL 改连接串即可。
- **为什么用 ChromaDB 而不是 Pinecone？** pip install 零配置，跟 SQLite 一样轻量。Pinecone 适合生产高并发，但 ChromaDB 换 Pinecone 只需改 client 初始化（5 行代码）。
- **为什么用 @tool 装饰器而不是手写 JSON Schema？** 手写 schema 每个 Tool ~30 行，改参数名要同步改 3 处。装饰器从 type hints + docstring 自动生成，~8 行一个 Tool，零重复。
- **为什么 System Prompt 用 Python 函数而不是 .md 文件？** 可注入 db_type、user_role、extra_context（memory 检索结果注入点），MD 文件做不到变量替换。
- **为什么 embedding 用 DashScope 而不是 OpenAI？** qwen3.7-text-embedding 中文效果更好、更便宜（¥0.0007 vs $0.02/1K tokens），国内访问延迟更低。

## 示例

### 基础查询
```
你: 上周哪个部门销售额最高？
Agent: 让我先看看有哪些表...
      🔧 list_tables → departments, employees, products, orders
      🔧 describe_table(orders) → 有 dept_id, total, created_at
      🔧 run_query(SELECT d.name, SUM(o.total) as sales FROM orders o
                   JOIN departments d ON o.dept_id = d.id
                   WHERE o.created_at >= date('now', '-7 days')
                   GROUP BY d.name ORDER BY sales DESC)
      → 销售部 ¥383,000（第1名，领先第2名 ¥180,000）
```

### 跨轮记忆（同一个会话内）
```
你: 查一下各部门的销售额排名
Agent: 🔧 run_query(...) → 销售部第1，市场部第2，研发部第3

你: 其中销售部卖得最好的是哪个产品？
Agent: 从刚才的结果已知 dept_id=1 是销售部，直接查...
      🔧 run_query(SELECT p.name, SUM(o.total) ...
                   WHERE o.dept_id=1 GROUP BY p.name)
      → 企业版 SaaS ¥280,000（占销售部 73%）
```

### 跨会话记忆（向量检索）
```
[会话 1]
你: 帮我做一下 Q2 各地区的订单分析
Agent: 🔧 run_query(...) → 北京 120 万，上海 98 万，深圳 76 万

[会话 2——第二天]
你: 上次那个地区分析，深圳排第几来着？
Agent: [recall 从向量库检索到上次的对话]
      → 根据之前的分析，深圳以 76 万排名第三。
```

## 测试

```bash
uv run pytest tests/ -v               # 全部 28 个测试
uv run pytest tests/ -v -k "unit"     # 只跑单元测试（秒级，不需要 API）
uv run pytest tests/ -v -k "memory"   # 只跑记忆系统测试
uv run pytest tests/ -v -k "agent"    # 只跑集成测试（需要 API key）
```
