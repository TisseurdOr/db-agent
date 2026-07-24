## AI Agent 开发岗 面试面经分类总结（2025-2026）

基于公开平台 30+ 篇面经整理，覆盖字节/阿里/腾讯/快手/淘天/小米。


### 一、各厂面试风格速查

| 公司 | 风格 | 偏爱追问 |
|------|------|---------|
| **字节** | 技术密度最高 | ReAct 死循环处理、上下文压缩、Tool Calling 异常、手写 MHA |
| **阿里/淘天** | 最全面，算法+工程 | Agent/Workflow/工具区别、画三层架构、落地瓶颈、GRPO/奖励函数 |
| **腾讯** | 生态+协议+产品 | MCP vs A2A、记忆系统设计、SSE 流式、Workflow vs Agent 边界 |
| **快手** | 结合技术栈 | Spring AI + ES + Agent 记忆架构、向量 vs 关键字检索 |
| **小米** | 极深挖、著名"压力三连" | Embedding 结构+维度、LayerNorm 归一化维度、注意力除√d_k |

**统一趋势**：2026 面试已从背概念 → 考工程落地。真正拉开差距的是**项目决策过程**和**故障处理经验**。


### 二、高频考点分类（按出现频率排序）

#### A. Agent 基础与架构（几乎每场必问）

1. Agent vs Chatbot vs Workflow 的本质区别
2. 画出 Agentic Loop 流程图（Thought → Action → Observation 循环）
3. ReAct 框架细节：何时停止、错误处理、上下文溢出
4. Plan-and-Execute vs ReAct 的适用场景和代价对比
5. 什么情况不该用 Agent？什么情况用 Workflow 就够？

#### B. 框架选型与 LangGraph（高频）

1. LangGraph vs LangChain 核心区别
2. LangGraph 三要素：State / Node / Edge 的职责与边界
3. State 的不可变性约束 + Reducer 机制
4. 条件边（Conditional Edge）的实现原理
5. Checkpoint 持久化机制
6. 多 Agent 协作模式（Supervisor / Sequential / Hierarchical）
7. **必考题**："为什么选 LangGraph 而不是 AutoGen/CrewAI/手写？"

#### C. 记忆系统（最容易翻车）

1. 三层/四层记忆架构设计（工作→短期→长期→情节）
2. 短期记忆：为什么不能只用滑动窗口 FIFO？Session 级状态机怎么做？
3. 长期记忆：为什么不能只靠向量库？强事实（手机号、禁忌）必须结构化存储
4. 记忆写入策略：同步 vs 异步事件驱动
5. 记忆冲突更新：时间戳+置信度+来源（用户 > 模型推断）
6. 记忆与 RAG 知识库必须分开存、分开检索（数据安全红线）

#### D. RAG 全链路

1. Chunking 策略（大小、重叠、结构感知）
2. 混合检索：稀疏(BM25) + 稠密(向量) + RRF 融合
3. Rerank 为什么必要（双塔缺交互→Cross-Encoder 精排）
4. 增量索引：hash 检测变更 + 先删后插
5. 文档冲突处理
6. Query Rewriting（指代消解）
7. 评测体系：Recall@K、Precision@K、MRR

#### E. 工具调用（Tool Calling）

1. Function Calling 原理与实现流程
2. Tool Schema 定义规范（name/version/schema/timeout/retry/side_effect）
3. 工具调用失败的三层处理：重试→替换→重规划
4. 如何防止 Agent 选错工具
5. MCP 协议原理及与 Function Calling 的区别
6. A2A 协议（Agent Card、能力名片）

#### F. 安全与护栏

1. HITL 设计：风险等级矩阵（高危/中危/低危 × 明确拒绝/暂时搁置）
2. Guardrails 纵深防御：Input Guardrail → 推理 → Output Guardrail
3. Agent 自主性边界：什么操作绝不能让 Agent 自己决定？
4. Prompt 注入防护
5. PII 检测与脱敏

#### G. 系统设计题

1. 设计一个生产级智能客服（四层架构：接入→规划→执行→治理）
2. 双十一高并发场景设计（削峰/缓存/分层模型/熔断/降级）
3. 成本控制：Cost-Aware Routing、ReWOO 模式、Plan Caching

#### H. 模型与训练（阿里偏重）

1. LoRA 原理（哪些参数更新？为什么低秩矩阵有效？）
2. DPO vs GRPO 的核心区别
3. 奖励函数设计 + 奖励作弊（reward hacking）
4. 幻觉原因与缓解


### 三、项目"死亡追问"清单

面试官的追问模式：**逐层深入直到你答不出**。以下是基于面经整理的高频追问链：

| 你的回答 | 面试官追问 |
|---------|----------|
| "用了 LangGraph" | State/Node/Edge 分别解决什么问题？你做项目时 State 设计失控过吗？ |
| "做了记忆系统" | 用户纠正了手机号，你是 Append 还是 Upsert？向量库召回错了怎么办？ |
| "做了 RAG" | Embedding 模型结构？输出维度？为什么 chunk_size 选这个值？怎么做增量索引？ |
| "有评测体系" | Recall@K 的 baseline 是什么？评测集 100 条够吗？分布如何？ |
| "做了 HITL" | 当用户说"不"时，是抛异常还是返回友好提示？为什么？ |
| "有错误处理" | 工具连续返回相同结果 3 次怎么办？模型死循环怎么检测？ |
| "做了多 Agent" | Agent 之间怎么通信？并发时状态竞争怎么解决？ |

**面试官最看重三种回答方式**：
- "报菜名"（✗）：我们用了 RAG、用了 Tool Calling
- "讲决策"（✓）：最开始想用单 Agent，后来发现规划和执行塞一起链路太长、出错难定位，所以拆开了
- "讲动作"（✓）：把当前任务状态单独存出来，不然 Tool 超时后很难从中间恢复


### 四、db-agent 项目与高频考点的映射

| 考点 | db-agent 对应的点 | 怎么说 |
|------|------------------|--------|
| 多 Agent 编排 | 6 Agent + LangGraph StateGraph | "Router 三层路由：硬规则 > LLM > 缓存" |
| 框架选型 | LangGraph vs LangChain | "LangChain AgentExecutor 黑盒难调试，LangGraph 每个节点可独立追踪" |
| 记忆系统 | 三层记忆 + TokenBudget | "短期滑动窗口 + LLM 压缩摘要 + ChromaDB 向量，记忆控制器判断召回策略" |
| HITL | 双层审批 | "SQL 敏感列 + HBase 破坏性 op，LangGraph 原生 interrupt()，checkpointer 不丢状态" |
| 安全护栏 | 三层 Guardrails | "输入护栏 → SQL 护栏 → 输出护栏，零 token 成本拦截 SQL 注入/prompt injection" |
| 权限模型 | 5 角色 RBAC | "工具白名单 + 表级过滤 + 行级 SQL 改写 + 文档过滤，权限数据存 DB 热更新" |
| 工具设计 | 14 Tool + @tool 装饰器 | "自动生成 JSON Schema，Tool-Handler 自动配线校验，零孤立 tool" |
| 评测 | LLM-as-Judge 35 条 | "独立模型 Kimi 评 DeepSeek，避免裁判偏袒" |
| HBase 模拟 | 内存 KV 引擎 | "嵌套 dict 模拟 table→row→cf:col→value，API 与真实 HBase 一致" |

**面试时主动提到的 db-agent 决策**：
1. "为什么不用 LangChain 的 AgentExecutor？"→ 黑盒，状态不可见。LangGraph 每个节点可 trace、可中断、可恢复
2. "为什么 HITL 不自己写审批队列？"→ LangGraph 原生 interrupt() 自动持久化暂停点，Command(resume=...) 恢复，checkpointer 保证不丢
3. "为什么 eval 用不同模型？"→ 裁判不能是选手。DeepSeek 做被测 Agent，Kimi 做 Judge


### 五、回答项目问题的标准模板

每个技术点准备 3 层回答：

```
第1层（概念）：我们做了什么，用的什么技术
第2层（决策）：为什么选这个方案而不是那个
第3层（教训）：踩过什么坑，怎么定位和解决的
```

**示例（db-agent 的 HITL）：**

| 层 | 内容 |
|----|------|
| 概念 | 实现了双层 HITL：SQL 敏感列（salary/cost/budget）和 HBase 破坏性操作（put/delete/drop/truncate）。LangGraph 原生 interrupt() 暂停，Command(resume=...) 恢复 |
| 决策 | 没自建审批队列。LangGraph interrupt() 自动写 checkpointer 持久化暂停点，graph 外调写操作直接返回错误，避免绕过审批 |
| 教训 | 早期只做了 SQL HITL，后来加了 HBase 模拟器后写操作也能在 graph 外调。补了一层 graph 外拦截：catch RuntimeError 返回审批提示。eval runner 踩过坑：HITL 中断后 state 是 dict 不是 string，crash 了 |


### 六、图片面经识别

小红书面经多为截图，我可以识别 PNG/JPG/WebP 格式图片。

**下一步**：把你觉得有价值的小红书面经截图保存到 `docs/interview-screenshots/` 目录，我逐张读取 OCR 并补充到这个文档里。


### 七、2 周冲刺建议

| 天数 | 任务 |
|------|------|
| Day 1-2 | 过上面经文档，对每个高频考点口述一遍答案（录下来听一遍） |
| Day 3-4 | 准备 db-agent 项目 10 个决策点的"为什么"→ 写到逐字稿 |
| Day 5-6 | 过"死亡追问清单"，每个问题准备到第 3 层，练到脱口能答 |
| Day 7-10 | 模拟面试（找朋友或对着镜子）+ 海投 10-20 家 |
| Day 11-14 | 持续投递 + 面试 → 每次面完立刻记录被问到的新问题 |

面经不是教科书——面试官闻得出背诵。练到能"用自己的话自然地讲技术决策"才算 Ready。
