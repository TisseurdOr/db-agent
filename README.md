# 数据库助手 Agent

自然语言查询数据库的 AI Agent。基于 Claude API 兼容协议，用 Agent Loop 自主探索表结构、生成 SQL、分析结果。

## 架构

```
用户 (CLI)
    │
    ▼
┌─────────────┐     System Prompt      ┌──────────────────┐
│   main.py   │ ─────────────────────► │  LLM API         │
│ 注册 9 Tools│                        │  (DeepSeek 等)   │
└──────┬──────┘                        └────────▲─────────┘
       │                                        │
       ▼                                        │ Think
┌─────────────┐   tools=TOOLS                   │
│  agent.py   │ ───────────────────────────────►│
│ Agent Loop  │◄────────────────────────────────┘
│ Observe→    │         tool_use / text
│ Think→Act   │
└──────┬──────┘
       │ Act: handlers[name]
       ▼
┌──────────────────────────────────────────────────┐
│ tools/                                           │
│  schema.py    list_tables / describe_table       │
│               get_schema_summary                 │
│  query.py     run_query (只读 SELECT)            │
│  analysis.py  analyze_results / compare_periods  │
│  knowledge.py search_knowledge_base              │
│               save_to_memory / read_memory       │
└──────────────────┬───────────────────────────────┘
                   ▼
            ┌─────────────┐
            │ SQLite DB   │
            │ db/demo.db  │
            └─────────────┘
```

## 技术栈

- Python 3.12, asyncio
- Claude API 兼容协议（Anthropic SDK → DeepSeek endpoint）
- SQLite
- 自研 Agent Loop（不依赖 LangChain）
- Prompt Caching（ephemeral cache，节省 ~99.5% system prompt 成本）
- @tool 装饰器自动生成 JSON Schema（type hints + docstring → schema）

## 快速开始

```bash
cp .env.example .env   # 填入 API key
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

## 项目结构

```
db-agent/
├── pyproject.toml
├── .env / .env.example
├── .gitignore
├── README.md
├── main.py               # CLI 入口（支持 --model 参数）
├── agent.py              # streaming agent loop + prompt caching
├── prompts/
│   └── system_prompt.py  # Prompt 工厂函数（五层结构，可注入变量）
├── tools/
│   ├── __init__.py       # @tool 装饰器（自动生成 JSON Schema）
│   ├── schema.py         # list_tables, describe_table, get_schema_summary
│   ├── query.py          # run_query (只读 SELECT + 白名单校验)
│   ├── analysis.py       # analyze_results, compare_periods（同比/环比）
│   └── knowledge.py      # search_knowledge_base, save_to_memory, read_memory
├── db/
│   └── seed.py           # SQLite 初始化 + 150+ 行示例数据
└── tests/
    └── test_agent.py      # 17 个测试（12 单元 + 5 集成）
```

## 设计决策

- **为什么不用 LangChain？** 先理解底层 Agent Loop（Observe → Think → Act）。Phase 4 引入 LangGraph 做多 Agent 编排。
- **为什么只允许 SELECT？** 安全考量。SQL 白名单校验 + 只读限制，防止 LLM 生成 DROP/UPDATE。
- **为什么用 SQLite？** 零配置。生产环境换 PostgreSQL 改连接串即可。
- **为什么不用 Haiku 而用 DeepSeek？** DeepSeek API 兼容 Anthropic SDK，价格是 Haiku 的 ~1/10，Tool Use 能力足够。国内访问延迟更低。
- **为什么用 @tool 装饰器而不是手写 JSON Schema？** 手写 schema 每个 Tool ~30 行，改参数名要同步改 3 处。装饰器从 type hints + docstring 自动生成，~8 行一个 Tool，零重复。
- **为什么 System Prompt 用 Python 函数而不是 .md 文件？** 可注入 db_type、user_role、extra_context（Phase 3 memory block 注入点），MD 文件做不到变量替换。

## 示例

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

## 测试

```bash
uv run pytest tests/ -v              # 全部 17 个测试
uv run pytest tests/ -v -k "unit"    # 只跑单元测试（秒级，不需要 API）
uv run pytest tests/ -v -k "agent"   # 只跑集成测试（需要 API key）
```
