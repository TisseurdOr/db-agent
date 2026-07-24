## 简历项目描述（中文 · STAR 格式 · 投 AI Agent 应用开发）

---

### 项目一：db-agent — 企业级自然语言数据库分析 Agent

**时间**：2026.06 – 2026.07 | **角色**：独立开发

**技术栈**：Python, LangGraph, ChromaDB, SQLite, DeepSeek API, Anthropic SDK, Matplotlib

**一句话**：从零构建了一个支持自然语言查询多种数据库（SQL + NoSQL + 数仓）的多 Agent 系统，内置权限网关、人工审批流程和记忆系统。

**工作内容**：

- 设计并实现了 **6 个专业 Agent**（SQL、Analysis、Strategy、HBase、Hive、DataQuality），基于 LangGraph StateGraph 编排，Router 采用"硬规则 > LLM 意图分类 > LRU 缓存"三层路由策略，支持上下文继承
- 实现了 **双层 HITL 人工审批**：SQL 敏感列（salary/cost/budget）和 HBase 破坏性操作（put/delete/drop/truncate）均通过 LangGraph 原生 interrupt() 暂停执行，Command(resume=...) 恢复，checkpointer 保证状态不丢
- 构建了 **14 个 Tool**，覆盖 SQLite 查询、HBase KV 内存模拟引擎（scan/get/count/put/delete）、Hive 数仓分层表（ods/dwd/dim）、数据分析、图表渲染、知识库检索
- 实现了 **5 角色 RBAC 权限模型**：工具级白名单 + 表级过滤 + 行级 SQL 改写（WHERE dept_id=X）+ 文档级过滤，权限数据存 DB 支持热更新
- 设计了 **三层记忆系统**：短期滑动窗口 + LLM 压缩摘要 + ChromaDB 向量长期记忆，配合 TokenBudget 自动压缩阈值管理
- 建立了 **三层安全护栏**：输入护栏（SQL 注入/prompt injection 检测）、SQL 护栏（非 SELECT 拦截）、输出护栏（PII 泄露检测）
- 编写了 **164 个测试用例**（48 冒烟 + 91 单元 + 25 集成），含 LLM-as-Judge 双模型评测体系（Kimi 独立评测 DeepSeek），冒烟测试 1.3s 跑完、零 API 成本

**技术亮点**：

- LangGraph 图编排 + async checkpointer 实现多 Agent 状态自动持久化与断点恢复
- 自研 HBase 内存 KV 模拟器，嵌套 dict 模拟 table→row_key→cf:col→value，API 与真实 HBase Shell 一致，可无缝切换
- @tool 装饰器自动生成 JSON Schema，Tool ↔ Handler 自动配线校验，无孤立 tool 或 handler
- RequestTracer 请求级调用链追踪，Span 粒度耗时/token 统计，落盘 JSONL


### 项目二：fin-agent — AI 研报分析 Agent

**时间**：2026.06 | **角色**：独立开发

**技术栈**：Python, FastAPI, LangGraph, RAG, MCP, ChromaDB

**一句话**：基于 FastAPI + LangGraph 的金融研报智能分析系统，支持多源研报检索、结构化提取和对比分析。

**工作内容**：

- [待补充：具体实现了哪些功能？用了哪些数据源？]
- [待补充：LangGraph 编排了哪些 Agent？]
- [待补充：RAG pipeline 怎么设计的？检索效果如何？]
- [待补充：MCP 暴露了哪些能力？]


### 项目三：fraud-agent — 反欺诈智能分析 Agent

**时间**：2026.06 | **角色**：独立开发

**技术栈**：Python, LangGraph, RAG, Streamlit, 10+ Tool

**一句话**：双轨编排的反欺诈分析系统，结合知识库检索与规则引擎，通过 Streamlit 提供交互式分析界面。

**工作内容**：

- [待补充：双轨编排是指什么？正常/异常双轨？]
- [待补充：10 个 Tool 分别是什么？]
- [待补充：Streamlit 界面有哪些交互功能？]
- [待补充：用的是什么数据？检测效果如何？]


### 简历 placement 建议

1. **技术栈行**放在项目标题下方，方便 HR/面试官快速判断匹配度
2. **工作内容**每条 1-2 行，前面 3 条是最重要的（架构设计 > 核心功能 > 质量保障）
3. 如果投递的 JD 强调**安全/权限**，把 HITL + Entitlement 提到第一条
4. 如果投递的 JD 强调**多 Agent 编排**，把 LangGraph + Router 提到第一条
5. fin-agent 和 fraud-agent 把细节补上后，三个项目按"最相关"排序放简历上
