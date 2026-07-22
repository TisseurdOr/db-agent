# 课程-代码对照表

17 节课教学内容与 `db-agent` 项目的对应关系。

## 基础思维（无直接代码产出）

### 0001: Spring Boot 到 Agent — 思维模型切换

无代码 — 认知转变：从"写业务逻辑"到"编排 LLM 行为"。

### 0002: Agent 项目的七块积木

无代码 — 但项目结构对应该框架：

| 积木 | 项目落点 |
|------|---------|
| LLM | Anthropic SDK（DeepSeek endpoint） |
| Tools | `tools/` 目录 |
| Memory | `memory/` 目录 |
| Orchestrator | `agent.py` |
| Prompt | `prompts/system_prompt.py` |
| Safety | SQL 白名单 + 只读限制 |
| Evaluation | `tests/` |

### 0003: Python 速通 — Java 开发者的 Agent 开发工具箱

全项目 — `async/await`、type hints、`@tool` 装饰器用到 `inspect` 模块。

---

## Phase 2: 核心 Agent Loop（0004-0011）

### 0004: Claude API + Tool Use 实战 — 裸写 Agent Loop

| 教学内容 | 代码位置 |
|---------|---------|
| Agent Loop (Observe→Think→Act) | `agent.py:78-252` — `streaming_agent()` 主循环 |
| Tool 执行 + 结构化错误返回 | `agent.py:35-55` — `_execute_tool()` |
| Anthropic SDK messages.create | `agent.py:161-168` — `client.messages.stream()` |
| max_turns 防止死循环 | `agent.py:32` — `MAX_TURNS = 10` |

### 0005: 第一个 Agent 项目

| 教学内容 | 代码位置 |
|---------|---------|
| CLI 入口 + 对话循环 | `main.py:76-180` — `main()` |
| Tool 注册与 handler 绑定 | `main.py:45-73` — `TOOLS` + `TOOL_HANDLERS` |
| Demo 数据库初始化 | `db/seed.py` — `init_db()` |
| 基础 Tool（list/describe/query） | `tools/schema.py` + `tools/query.py` |

### 0006: LLM 工作原理 — 每个 Agent 开发者必须懂的四件事

| 教学内容 | 代码位置 |
|---------|---------|
| Token 概念 | `memory/token_budget.py:2-9` — `estimate_tokens()`（中 ~1.5 字/token, 英 ~4 chars/token） |
| Temperature = 0 的选择 | `agent.py:9` — "Agent 选 Tool 必须确定，不能随机" |
| Prompt Caching（ephemeral） | `agent.py:58-75` — `_build_cacheable_system()` |
| Context Window 限制 | `agent.py:233-238` — Tool 结果截断 |

### 0007: Prompt Engineering 即系统设计

| 教学内容 | 代码位置 |
|---------|---------|
| 五层 Prompt 结构 | `prompts/system_prompt.py:37-107` — Role / Tools / Workflow / Constraints / Output Format |
| Few-shot Examples | `prompts/system_prompt.py:67-81` — 字段名拼错 + 非查询闲聊 |
| 工厂函数（变量注入） | `prompts/system_prompt.py:18-24` — `build_system_prompt(db_type, user_role, extra_context)` |
| extra_context 注入点 | `prompts/system_prompt.py:111-112` — Phase 3 memory block 注入处 |

### 0008: Tool 设计最佳实践 — Agent 的 API 设计

| 教学内容 | 代码位置 |
|---------|---------|
| @tool 装饰器自动生成 Schema | `tools/__init__.py` — type hints + docstring → JSON Schema |
| 每个 Tool ~8 行（vs 手写 ~30 行） | `tools/knowledge.py` — `search_knowledge_base` 等 |
| SQL 只读限制 + 白名单 | `tools/query.py:27-35` — 只允许 SELECT |
| 结构化错误返回 | `tools/query.py:54-60` — error + error_type + hint |
| get_schema_summary（避免逐表 describe） | `tools/schema.py:93-133` |
| 分析 Tool（排名/对比） | `tools/analysis.py` — `analyze_results` / `compare_periods` |

### 0009: 模型选型 & 成本模型

| 教学内容 | 代码位置 |
|---------|---------|
| 模型对比表 | `README.md:106-122` — deepseek-chat vs deepseek-reasoner |
| 单次查询成本估算 | `README.md:117-118` — ~¥0.01-0.03/次, 月成本 < ¥15 |
| Embedding 选型 | `README.md:120-123` — qwen3.7-text-embedding, ¥0.0007/1K tokens |
| 环境变量配置 | `.env.example` — `ANTHROPIC_MODEL`, `ANTHROPIC_BASE_URL` |
| 模型名统一走 env | `agent.py:31` — `DEFAULT_MODEL = os.getenv("ANTHROPIC_MODEL", "deepseek-chat")` |

### 0010: Streaming & 实时交互

| 教学内容 | 代码位置 |
|---------|---------|
| client.messages.stream() | `agent.py:161-168` — streaming context manager |
| content_block_start 事件 | `agent.py:170-179` — Tool 调用实时通知 |
| content_block_delta 事件 | `agent.py:181-195` — 逐字输出 + input_json_delta 累积 |
| content_block_stop 事件 | `agent.py:197-198` |
| get_final_message() | `agent.py:201` — SDK 自动 parse Tool input |
| streaming vs 非 streaming 两个版本 | `agent.py:78-252` (streaming) + `agent.py:258-314` (`agent_loop`, 非 streaming) |

### 0011: Phase 2 集成 — 把所有东西焊在一起

| 教学内容 | 代码位置 |
|---------|---------|
| 完整 Tool 注册（9 个） | `main.py:45-73` |
| 集成测试 | `tests/test_agent.py` — 17 个测试（12 单元 + 5 集成） |
| 架构文档 | `README.md:7-80` — 完整 ASCII 架构图 |
| 项目结构 | `README.md:178-207` — 目录树 |

---

## Phase 3: 记忆系统（0012-0017）

### 0012: Memory 三层模型 — Agent 的记忆系统

| 教学内容 | 代码位置 |
|---------|---------|
| Working Memory（草稿纸） | CoT 指令 + Tool 结果摘要（Agent Loop 内） |
| Short-term Memory（聊天记录） | `memory/short_term_memory.py` — `ConversationManager` |
| Long-term Memory（知识库+档案） | `memory/vector_store.py` — `VectorMemory` (ChromaDB) |
| 三层架构图 | `README.md:127-146` |

### 0013: RAG 深入 — Embedding → Chunking → 检索 → 生成

| 教学内容 | 代码位置 |
|---------|---------|
| RAG 完整链路 | `memory/long_term_memory.py:36-61` — `retrieve()` |
| HyDE（短查询生成假设答案） | `memory/long_term_memory.py:63-79` — `_generate_hypothesis()` |
| Rerank（粗排→精排） | `memory/long_term_memory.py:81-104` — `_rerank()` |
| recall→注入→生成 | `main.py:125-143` — memories_text → extra_context → build_system_prompt |

### 0014: 向量数据库实操 — 把记忆存进向量库

| 教学内容 | 代码位置 |
|---------|---------|
| ChromaDB PersistentClient | `memory/vector_store.py:19` |
| 余弦相似度（hnsw:space=cosine） | `memory/vector_store.py:30-31` |
| distance → similarity 转换 | `memory/vector_store.py:179` — `score = 1 - distance` |
| remember / recall / forget API | `memory/vector_store.py:80-182` |
| 元数据过滤 | `memory/vector_store.py:135-143` — `_where()` with `$and` |
| 旧数据兼容（user_id 退避） | `memory/vector_store.py:155-167` |
| 单元测试 | `tests/test_memory.py` — 6 个 VectorMemory 测试 |

### 0015: 对话管理 — 别让 Agent 在长对话中失忆

| 教学内容 | 代码位置 |
|---------|---------|
| TokenBudget（主动预算管理） | `memory/token_budget.py` — `estimate_tokens()`, `should_compress()`, `available()` |
| 70% 预警阈值 | `agent.py:118-119` — `TOKEN_BUDGET_WARN=0.7` |
| HybridWindowManager 3 层压缩 | `memory/hybrid_window_manager.py` — L0 最近6条原文 / L1 7-14条轻摘要 / L2 全局摘要 |
| ConversationManager 混合策略 | `memory/short_term_memory.py:32-89` — 最近10条原文 + 更早压缩摘要 |
| 压缩用便宜模型 | `memory/hybrid_window_manager.py:7` — `deepseek-v4-flash` |
| 每轮 token 日志 | `main.py:171-176` — `[memory] 本轮 tokens≈...` |
| 双压缩互斥 | `agent.py:147` — TokenBudget 触发后跳过 ConversationManager 摘要 |

### 0016: 高级 RAG 模式 — 从"能用"到"好用"

| 教学模式 | 代码位置 |
|---------|---------|
| **Self-Query**: LLM 拆解查询 → filters | `rag/self_query.py` — `parse_self_query()` → `self_query_retrieve()` |
| Self-Query 降级重试 | `rag/self_query.py:151-164` — 结果 < 2 时去掉业务 filters |
| **Agentic RAG**: 记忆 Tool 平权 | `tools/knowledge.py:170-215` — `search_memory` Tool |
| search_knowledge_base Tool | `tools/knowledge.py:67-97` |
| save_to_memory / read_memory Tool | `tools/knowledge.py:100-167` |
| 三路检索平权 | `search_memory`（向量记忆）+ `run_query`（数据库）+ `search_knowledge_base`（知识库） |
| ~~Multi-hop RAG~~ | 课程明确："面试能说就行，项目里先不堆复杂度" |
| ~~Corrective RAG~~ | HyDE 做了简化版（短查询→假设答案），全量 Corrective 未实现 |

### 0017: Phase 3 集成 — 给 Agent 装上记忆系统

| 教学内容 | 代码位置 |
|---------|---------|
| 完整记忆编排（每轮） | `main.py:132-176` — recall → 注入 → agent → add_message ×2 → remember |
| 闲聊过滤 | `main.py:118-123` — `is_chitchat()` 跳过无意义检索 |
| 错误手册 | `docs/troubleshooting.md` — 18 个踩坑记录 + 解决方案 |
| 综合测试 | `tests/test_memory.py` — 19 个测试 |

---

## 总结

| 课程 | 状态 | 关键产出 |
|------|------|---------|
| 0001-0003 | 基础 | 思维转变 + Python 技能 |
| 0004 | Agent Loop | `agent.py` |
| 0005 | 项目骨架 | `main.py` + `db/seed.py` |
| 0006 | LLM 原理 | cache_control + token 估算 |
| 0007 | Prompt 工程 | 五层 factory 函数 |
| 0008 | Tool 设计 | @tool 装饰器 + 9 个 Tool |
| 0009 | 模型选型 | DeepSeek + DashScope |
| 0010 | Streaming | streaming_agent |
| 0011 | Phase 2 集成 | 17 个测试 + README |
| 0012 | 三层记忆 | ConversationManager + VectorMemory |
| 0013 | RAG | HyDE + Rerank |
| 0014 | 向量库 | ChromaDB + 19 个测试 |
| 0015 | 对话管理 | TokenBudget + HybridWindow |
| 0016 | 高级 RAG | Self-Query + Agentic RAG |
| 0017 | Phase 3 集成 | 完整记忆编排 + 错误手册 |

未实现的课程内容：
- 0016 Multi-hop RAG — 课程建议"面试能说就行"
- 0016 Corrective RAG — HyDE 做了简化版，全量未实现
