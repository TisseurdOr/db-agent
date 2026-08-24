# fin-agent 文档合集

> 合并自 2026-06-24 的改进日志、Harness 改进、LangGraph 解读与面试准备。
> 评测原始数据：`2026-06-24_rag-vs-no-rag-评测结果.json`
> 最后更新：2026-08-11
> 相关：[[resume-projects]] · [[fraud-agent-面试]] · [[面试准备]]

---

## 开发改进日志（RAG / 评测）

## 问题 1：RAG 召回率低

**日期**：2026-06-22 (D2)

**发现**：8 题测试，top-1 召回率仅 25%，top-3 为 38%，top-5 为 62%。

**诊断**：
- chunk_size=500（≈250 中文字）太短，概念被切断（如 DuPont 公式跨两个 chunk）
- 无 reranker，纯 embedding 相似度噪声大
- 跨语言检索混乱：中文查询匹配英文文档而非中文知识点文档
- 纯语义检索，缺 BM25 关键词互补（"网信办""5.8万亿"搜不准）
- 字符级切分截断金融表格和公式

**改进方案**：
1. chunk_size 500→800，overlap 50→150
2. 加 LLM reranker 做第二道精排
3. Hybrid Search：BM25 + Dense RRF 融合
4. SemanticChunker 语义切分
5. LLM Query 改写

**落地**：
- ① chunk_size 500→800, overlap 50→150 ✅
- ② LLM reranker (DeepSeek) ✅
- ③-⑤ 按需后续迭代

**结果**：
| 指标 | 改进前 | 改进后 |
|------|--------|--------|
| chunks | 783 | 542（更完整） |
| top-1 | 25% | 88% |
| top-3 | 38% | 88% |

Reranker 效果显著：中文"Dupont"查询，无 rerank 时 top-1 命中英文公式 PDF，有 rerank 后 top-1 精准定位中文"利润率×周转率×杠杆"。

---

## 原理深度解析：为什么这个方案有效

### 一、chunk_size 500→800（chunk_overlap 50→150）

**问题本质：中文与英文的信息密度差异**

英文 RAG 的常见最佳实践 chunk_size=500，但对于中文这是一个错误的迁移：
- 500 字符 ≈ 250 个中文字。中文一个词通常 1-2 个字，而英文一个词平均 5 个字母
- 同样的信息量，中文只需要英文 1/3 的字符数
- 所以中文场景 chunk_size 应该是英文的 1.5-2 倍，即 800-1000

**为什么 chunk 太小会直接拉低召回率？**

embedding 模型的工作原理是把一段文本压缩成一个固定维度的向量（如 1024 维），然后通过余弦相似度匹配。但这里有个关键约束：一个向量只能表达**一个核心语义**。

如果 chunk 太小，比如只包含"ROE = (NI/Sales) × (Sales/Assets) × (Assets/Equity)"这一行公式，向量只编码了"这是一个公式"，丢失了"这是杜邦分析法的三因子分解"这个上下文。用户问"杜邦分析法"时，系统无法匹配这个孤立的公式 chunk。

如果 chunk 完整包含"杜邦分析：ROE 分解公式 + 解释 + 用途"，向量就能编码"财务分析、杜邦、ROE、盈利能力"等多维语义，召回精度大幅提升。

**为什么 overlap 也很关键？**

金融内容的公式、表格、法规引用往往横跨相邻段落。overlap=50 中文只有 25 个字，连一个完整的句子都覆盖不了。overlap=150 中文约 75 字，能兜住一个完整句子，确保边界处的信息不被截断。

**补充：为什么 chunk 从 783 减少到 542？**

因为每个 chunk 变大了，所以总数减少。这意味着：
- 检索时向量库需要比对的候选更少 → 粗排更快
- 但每个候选携带的信息更完整 → 精排更容易命中
- 最终送给 LLM 的上下文质量更高 → 生成答案更准确

### 二、LLM Reranker：两阶段检索的核心思想

**单阶段检索的致命缺陷**

纯 embedding 检索只有一次向量匹配，而向量匹配本质上是"大概相似"而非"精确相关"。举个实际例子：

用户问"杜邦分析法将 ROE 分解为哪三个部分？"

embedding 的 top-1 返回了一段 CFA 英文公式 PDF 的文本，内容包含"DuPont Analysis method of decomposing Return on Equity"。从向量角度看，这确实是"相似"的——都提到了 DuPont 和 ROE。但它没有回答"哪三个部分"这个问题。

而中文 CFA 知识点 PDF 里明确写了"利润率×周转率×杠杆"，这才是真正相关的。embedding 把它排到了第 2 位。

**两阶段检索的架构原理**

```
用户查询
    │
    ▼
阶段一（粗排）：Embedding 向量检索
    └→ 从 542 个 chunk 中召回 top-12（2.2% 的候选）
    └→ 速度快，覆盖面广，但精度有限
    │
    ▼
阶段二（精排）：LLM Reranker
    └→ 从 12 个候选中精选 top-4
    └→ LLM 逐条阅读内容，理解查询意图，判断真实相关性
    └→ 速度较慢，但精度极高
    │
    ▼
生成：基于 top-4 精确上下文生成答案
```

**为什么两阶段比一阶段好？**

这借鉴了搜索引擎和信息检索领域的经典架构（Google 也是粗排+精排）。核心洞察是：

1. **分工明确**：embedding 擅长"快速排除 98% 不相关的内容"，LLM 擅长"从 2% 候选里挑出真正有用的 4 条"
2. **互补优势**：embedding 速度快但理解弱，LLM 理解强但速度慢。两者结合，在速度和质量之间取得平衡
3. **语义理解层次不同**：embedding 理解"词面相似"，LLM 理解"意图匹配"。比如"杜邦分析法"和"ROE decomposition"在向量空间可能没那么近，但 LLM 知道这是同一个概念

**DeepSeek 做 reranker 的优势**

- 不需要额外部署 cross-encoder 模型（如 bge-reranker），零额外基础设施成本
- prompt 可控，可以根据金融场景定制评分标准
- 如果后续换 embedding 模型，reranker 不需要改动

### 三、效率分析：这次改进如何影响工作流

**检索质量→开发信任感**

之前 25% 的 top-1 召回率意味着：你问 4 个问题，只有 1 个能在第一个结果里找到答案。开发时你会不断遇到"明明文档里有，为什么搜不出来"的挫败感。

88% 的召回率意味着：几乎每次检索都能在首位命中。这个信任感对开发效率很重要——你不会因为怀疑检索有问题而反复手动翻文档验证。

**端到端链路的质量门槛**

RAG 回答的质量取决于上下文质量。如果 top-4 里有 3 条噪声，LLM 生成的答案自然是错的。这就像给一个聪明人错误的参考资料让他答题——人再聪明也没用。

reranker 保证了送给 LLM 的上下文是高精度、低噪声的，LLM 只需做它擅长的事：基于准确资料生成答案。而不是代替检索系统去"猜"哪个片段有用。

**迭代效率**

有了 reranker 之后，如果想进一步提升检索质量，只需要调整两个独立环节：
- 粗排不够好 → 换更强的 embedding 模型或加 BM25 混合检索
- 精排不够好 → 调 reranker prompt 或换更强的 LLM

两部分互不干扰，可以独立迭代。这是好的架构设计的标志。

---

## 实验 1：RAG vs 纯 LLM 系统性对比评测

**日期**：2026-06-23 (D2 补充)

**方法**：设计 8 道有明确标准答案的金融问题，分别走 RAG（检索增强）和纯 LLM 两路生成回答，由 DeepSeek 作为独立裁判根据标准答案评分（LLM-as-Judge）。

**评测数据**：

| # | 问题 | RAG | 纯LLM | 胜者 |
|---|------|-----|-------|------|
| 1 | GDP增速预期 | 10 | 2 | RAG |
| 2 | 杜邦分析三因子 | 10 | 10 | 平局 |
| 3 | 金融科技市场规模 | 10 | 2 | RAG |
| 4 | WACC/CAPM | 10 | 9 | RAG |
| 5 | AI模型备案部门 | 10 | 4 | RAG |
| 6 | 银行客服解决率 | 10 | 3 | RAG |
| 7 | MCP试点银行 | 10 | 0 | RAG |
| 8 | 区块链交易额 | 10 | 2 | RAG |
| **平均** | | **10.0** | **4.0** | RAG 7胜0负 |

**关键发现**：

1. **RAG 的回答几乎满分**（10.0 vs 4.0）。因为 LLM 只需做它擅长的事——把检索到的准确资料转述出来，不需要"猜测"。

2. **纯 LLM 对时效性数据完全无知**。纯 LLM 回答 GDP 时说"尚无权威机构发布预测"，问 MCP 试点银行时直接给 0 分——模型训练数据截止后，这些 2026 年的信息它不可能知道。这就是 RAG 解决的核心问题：让模型能回答"训练数据之外"的问题。

3. **基础知识是唯一的平局**。杜邦分析法打了平手（10 vs 10），因为这是经典教材内容，LLM 训练数据里早已覆盖。这说明：RAG 并不需要替代 LLM 的通用知识，而是补齐它的盲区——时效性信息、专有数据、垂直领域细节。

4. **纯 LLM 的典型失败模式**：
   - "尚无官方数据"（其实文档里有） → 缺乏信心，过度保守
   - "大概在 X-Y 范围"（其实文档写的是精确值 5.0%）→ 模糊化精确信息
   - "建议关注后续报告" → 把不确定性归咎于外部，而非承认自己不知道

**评测方法**：LLM-as-Judge（DeepSeek 独立评分），prompt + 标准答案 + 逐题打分 + 理由。完整原始数据见 `~/Desktop/rag-vs-no-rag-评测结果.json`。

---

## 方法 1：如何制定 RAG 系统评测标准

RAG 系统评测分三层：检索层、生成层、端到端。每层的评测目标和方法不同。

### 一、三层评测框架

```
┌─────────────────────────────────────────┐
│  ③ 端到端评测                            │
│  RAG ON vs RAG OFF，LLM-as-Judge 打分    │
│  回答: "GDP增速5.0%" → 对比标准答案 → 10分  │
├─────────────────────────────────────────┤
│  ② 生成层评测                            │
│  忠实度（是否忠于检索到的上下文）             │
│  上下文: "GDP增速5.0%" → 回答: "约5%" → 扣分 │
├─────────────────────────────────────────┤
│  ① 检索层评测                            │
│  Recall@k / MRR / NDCG                  │
│  542 chunks → top-5 → 62% 命中            │
└─────────────────────────────────────────┘
```

### 二、每一层的具体做法

#### ① 检索层：测"搜得准不准"

**核心指标**：

| 指标 | 含义 | 适用场景 |
|------|------|---------|
| Recall@k | 标准答案是否出现在 top-k 结果中 | 最重要，测"有没有漏" |
| MRR | 第一个相关结果排在第几位 | 测"第一个对不对" |
| NDCG | 加权排名质量（相关度×位置） | 有多级相关度时用 |

**测试集构造方法**：
```python
# 从文档中提取 20-30 组 (问题, 关键词) 对
tests = [
    ("2026年GDP增速预期多少？", "5.0%"),
    ("MCP在哪几家银行试点？", "工行、招行、平安"),
    # ...
]
# 跑检索 → 检查关键词是否在 top-k 的 content 中
```

**评判标准**：
- 只做二元判断（命中/未命中），不做多级评分
- 关键词要选文档中**唯一或高度特异的**表述，避免歧义
- ≥20 题才有统计意义，<10 题只是粗略参考

#### ② 生成层：测"答得忠实不忠实"

检索对了 ≠ 答案对了。LLM 可能：
- 忽略检索到的内容，用自己的知识回答（幻觉）
- 曲解检索内容的含义
- 只用了部分信息，遗漏关键数据

**评测方法**：忠实度检查（Faithfulness）

```
检索到的上下文: "GDP增速预计维持在5.0%左右"
LLM 回答:       "GDP增速约4.5%-5%"
                ↑ 把精确值模糊化了 → 不忠实
```

具体做法：用另一个 LLM 逐句检查回答中的每个事实声明是否能在上下文中找到依据。如果找不到，标记为幻觉。

#### ③ 端到端：测"最终答案对不对"

这就是刚做的 RAG vs 纯 LLM 对比。核心要素：

**a) 标准答案从哪来？**

| 来源 | 可靠性 | 成本 |
|------|--------|------|
| 从文档中直接摘取 | 最高 | 低（自己写） |
| 请领域专家写 | 高 | 高（人力） |
| LLM 生成后人审 | 中 | 中 |
| 纯 LLM 生成 | 低（可能自证自评） | 最低 |

本项目用的是第一种——从已入库的文档中直接提取关键事实作为标准答案。

**b) 裁判怎么打分？**

```
你是一个严格的评测专家。根据标准答案评判两个回答。

评分维度：
- 准确性(0-6)：数据、名称、百分比是否与标准答案一致
- 完整性(0-3)：是否覆盖标准答案的所有关键要素
- 简洁性(0-1)：有无冗余或编造内容

标准答案：GDP增速预计维持在5.0%左右
回答A：5.0%左右  → 准确性6 + 完整性3 + 简洁性1 = 10
回答B：约4.5-5%  → 准确性2 + 完整性1 + 简洁性1 = 4
```

**c) 为什么不用自动化指标（ROUGE/BLEU）？**

ROUGE 和 BLEU 测的是字面重叠，适合翻译、摘要等任务。但 QA 场景中：
- "GDP 增速 5.0%" 和 "国内生产总值增长约 5 个百分点" 语义完全相同，字面重叠为 0
- 所以必须用 LLM 做语义级评判

### 三、一个可复用的评测脚本模板

```python
# 三步走，每次改进后跑一遍
def evaluate():
    # 1. 检索层：构造 (问题, 关键词) 对 → 跑 recall@k
    retrieval_scores = []
    for q, keyword in retrieval_tests:
        _, sources = query(q, k=5)
        hit = any(keyword in s['content'] for s in sources)
        retrieval_scores.append(hit)

    # 2. 端到端：构造 (问题, 标准答案) 对 → LLM 打分
    gen_scores = []
    for t in gen_tests:
        rag_ans = ask(t['question'], use_rag=True)
        score = judge(rag_ans, t['ground_truth'])
        gen_scores.append(score)

    # 3. 输出：recall@k + 平均分 + 胜率
    print(f"Recall@5: {sum(retrieval_scores)/len(retrieval_scores):.0%}")
    print(f"Gen Score: {sum(gen_scores)/len(gen_scores):.1f}")
```

### 四、评测设计的关键原则

1. **标准答案必须独立于系统**。不能用系统自己生成的内容当标准答案，那是循环验证。本项目标准答案来自人工从 PDF 中直接摘取的事实。

2. **评测集 ≠ 开发集**。如果每次改进都用同一套题，系统会"过拟合"到这套题上。理想做法是留 30% 的题作为 hold-out，只在最终评测时用。

3. **指标要对齐业务目标**。本项目目标是"金融研报问答准确"，所以评测题聚焦于数据类（GDP、市场规模、增速百分比）和事实类（备案部门、试点银行），而非开放式论述题。

4. **评测频率**：每次改 embedding 模型、切分策略、reranker 后跑一遍。改前端/UI 不需要重跑。

5. **保存原始数据**：不要只记最终分数。把每次评测的逐题得分、回答原文、失败案例全保存下来。下周回头看时，你会发现哪些改进是伪提升（涨了一个题但降了三个题）。

---

## 战略分析：基于 2026 JD 市场的深度方向选择

**日期**：2026-06-24

**数据来源**：200+ 份 2026 年校招/社招 JD（阿里、字节、小米、塔斯汀、联蔚数科等）

### 一、市场信号：三个确定性趋势

| 趋势 | 数据 | 含义 |
|------|------|------|
| LangGraph + MCP 成标配 | 89% 团队要求 | 不是加分项，是门槛 |
| "Demo-to-production gap" | 社招 JD 高频原句 | 面试官最怕只会跑 notebook 的候选人 |
| 可观测性/评测 | 每个 senior JD 都提 | 生产级 Agent 的第一优先级 |

### 二、fin-agent 当前状态 vs JD 要求

| 能力维度 | 当前 | JD 要求 | 差距 |
|---------|------|---------|------|
| LangGraph 多 Agent | ✅ 线性流水线 | Conditional edge、并行、工具调用 | 中等 |
| RAG | ✅ v4 + rerank + rewrite + HyDE | GraphRAG、多轮对话、RAGAS 评测 | 较大 |
| Tool Calling | ❌ 未实现 | Function Calling + MCP 双模 | 大 |
| 可观测性 | ✅ 基础 logging + request_id | Tracing、cost、latency dashboard | 中等 |
| 评测体系 | ⚠️ 有 recall test | RAGAS generation eval、regression suite | 大 |
| 异步/生产化 | ❌ 同步 invoke | Celery/任务队列、超时控制 | 大 |
| Guardrails | ❌ 无 | 输出校验、幻觉检测、prompt injection 防护 | 大 |

### 三、推荐的深度方向（按优先级排序）

#### 🥇 P0：D4 Tool Calling + MCP（必做，已在计划中）

**为什么**：2026 基线要求。阿里 JD 写"深入理解 MCP 协议，有源码级定制经验优先"。

**具体做法**：
- D4a：analyst 节点 bind 2 个 tool（get_stock_quote / get_industry_pe），让 Agent 查实时数据
- D4b：同一套 tool 通过 MCP 协议暴露，可被 Cursor/Claude Code 调用
- **面试话术**："同一套金融 tool 既服务 LangGraph 内部节点，也通过 MCP 对外暴露"

#### 🥇 P0：评测体系建设（建议 D3.5 补充）

**为什么**：每个 senior JD 都提"端到端评估与可观测体系"。这是区分 demo 和 production 的分水岭。

**具体做法**：
- 引入 RAGAS 做 generation 层评测（faithfulness / answer relevancy / context precision）
- CI 里跑 regression 评测（每次 push 自动跑 18 题，召回率低于阈值报警）
- **面试话术**："检索召回率衡量搜得准不准，RAGAS 衡量答得对不对，两套指标跑 CI，每次改动都有数据"

#### 🥈 P1：LangGraph 深度化 — Conditional Edge + 并行

**为什么**：当前是线性流水线，JD 要的是"循环图架构""条件边""并行执行"。

**具体做法**：
- analyst 解析失败 → 跳过 risk/report，直接返回错误（conditional edge）
- risk_level="low" → 走简化报告；risk_level="high" → 走详细报告（条件分支）
- analyst 和 risk 可并行（独立节点）→ merge 到 report

**面试话术**："基础版是线性流水线，我设计了 3 个演进阶段：条件边 → 工具调用 → 并行执行，每个阶段解决一个真实问题"

#### 🥈 P1：异步化 analyze（挂 Java/Kafka 差异化）

**为什么**：JD 要"微服务 + 异步编程"。你的 Java 背景在这可以自然衔接。

**具体做法**：
- `POST /api/analyze` → 提交 Celery 任务 → 返回 task_id
- `GET /api/analyze/{task_id}` → 轮询结果
- 面试主动提："analyze 耗时长，用 Celery 异步——这和我在花旗用 Kafka 做异步消费的经验是同一套模式"

#### 🥉 P2：Guardrails + 安全（小众但稀缺）

**为什么**：AI Safety 方向极度稀缺，竞争小。EU AI Act 生效后合规需求爆发。

**具体做法**：
- 输出校验：报告节点后加 guardrail 检查（是否含虚假数据、是否违反合规要求）
- Prompt injection 防护：用户输入过滤

#### 🥉 P2：GraphRAG（加分但不必须）

**为什么**：JD 开始提。但实现成本高（需 Neo4j），性价比不如上面几个。

**判断**：D5 之后有余力再做。

### 四、修正后的 D4-D7 建议

| 原计划 | 建议调整 |
|--------|---------|
| D4a Tool Calling | 保留，加 tool 调用验证链路 |
| D4b MCP Server | 保留 |
| D5 Docker + README | 保留，加 `/docs` Swagger |
| **新增 D4.5** | **评测体系（RAGAS + CI regression）** |
| **新增 D4.5** | **Conditional Edge（analyst 失败短路）** |
| D6 简历 | 保留，但加 **面试逐字稿** |
| D7 投递 | 保留 |

### 五、风险判断

- **不要做的事**：开第二个 demo（电商客服 Agent）。JD 要的是深度，一个项目讲 30 分钟 > 两个项目各讲 10 分钟
- **不要做的事**：React 前端、GraphRAG（投入产出比低，面试不太会深问）
- **必须做的事**：MCP、评测体系、conditional edge（这三个是面试分水岭）


---

## Harness 层改进报告

## 一、总体变化

为 fin-agent 添加了 **LLM 执行 Harness 层**——一个薄的基础设施层，位于业务代码与 LLM API 之间，负责**可靠性、可观测性、可维护性**。

```
改进前：  业务代码 ──裸调──→ DeepSeek/通义千问 API
                         └── 网络故障 → 500 崩溃

改进后：  业务代码 ──call_llm()──→ 重试/日志/降级 ──→ LLM API
                              └── 网络故障 → 3次重试 → 仍失败 → 503 + 降级消息
```

---

## 二、新增文件

| 文件 | 职责 | 行数 |
|------|------|------|
| `app/config.py` | 集中化 LLM 工厂——3 个模型的注册表 + 延迟实例化 + 缓存复用 | 67 |
| `app/llm_harness.py` | 核心执行层——`call_llm()` 包装所有 LLM 调用 | 88 |
| `app/observability.py` | 日志配置 + RequestID 中间件 | 44 |

---

## 三、具体改进项

### 1. 可靠性：所有 LLM 调用增加重试机制

**改进前**：7 个 `llm.invoke()` 调用点全部裸调，网络抖动 / API 限流 / 服务暂时不可用 → 未处理异常 → FastAPI 500 Internal Server Error。

**改进后**：全部替换为 `call_llm()`，每次调用最多 4 次尝试（3 次重试），指数退避（1s → 2s → 4s）+ 随机抖动，避免惊群效应。

**影响范围**：
- `graph.py`：analyst、risk、report 三个 LangGraph 节点
- `rag.py`：reranker、query rewrite、HyDE 生成
- `main.py`：/api/chat、/api/askRAG 端点

### 2. 可观测性：从零到有

**改进前**：640 行代码，零个 `logging` 调用。LLM 调用失败时完全不知道哪个节点、什么原因、耗时多少。

**改进后**：
- **每条 LLM 调用**：成功时 INFO 日志（调用方、模型、耗时 ms、内容长度、第几次尝试）
- **重试**：WARNING 日志（错误原因、退避秒数）
- **耗尽重试次数**：ERROR 日志（所有尝试失败后的最终错误）
- **分析任务**：analyze_pdf 开始/结束时 INFO 日志（文件名、文本长度、风险等级、报告长度）
- **HyDE 失败**：之前静默 `except Exception: pass`，现在 WARNING 日志后降级

### 3. 错误处理统一化

**改进前**：三种错误处理风格混用：
- 裸 `llm.invoke()` 无保护 → 未处理异常（7 处）
- `{"error": "..."}` 字符串键判断（graph.py analyze_pdf）
- `except Exception: pass` 静默吞错（rag.py _hyde_generate）

**改进后**：
- `LLMResult` 数据类统一 LLM 调用的成功/失败返回，`call_llm()` 永不抛异常
- `/api/chat` 和 `/api/askRAG` 失败时返回 **503 Service Unavailable** + 错误描述（而非 500 堆栈）
- `_hyde_generate` 失败时 WARNING 日志 → 返回 `""`（行为不变，但可观测）
- 各节点有明确的降级路径（analyst 失败 → 错误 dict → risk 检测到 → 提前返回高危标记）

### 4. 配置集中化：5 个分散的 ChatOpenAI 实例 → 1 个工厂

**改进前**：
- `graph.py`：1 个模块级 `ChatOpenAI`（import 时即构建）
- `rag.py`：1 个 `ChatOpenAI` + 1 个 `OpenAI`（import 时即构建）
- `main.py`：3 个 `ChatOpenAI` 在一个 `MODELS` 字典里（import 时即构建）
- 共 5 个独立实例，API key / base_url / temperature 字符串字面量重复出现

**改进后**：
- `app/config.py` 的 `get_llm(model_name)` 单一入口
- 延迟实例化 + 字典缓存——只有被调用时才构建，同一模型复用同一实例
- 新增模型只需在 `_MODEL_REGISTRY` 加一条，所有模块自动可用
- `max_retries=0`（重试交给 harness 层，不在 HTTP 客户端层重复）

### 5. 健康检查：从空操作变为真正的依赖检测

**改进前**：
```python
@app.get("/api/health")
async def health():
    return {"status": "ok", "models": list(MODELS.keys())}
# 永远返回 ok，即使 ChromaDB 损坏、API key 缺失
```

**改进后**：
- 检测 ChromaDB 连通性（调用 `get_stats()` 验证向量库可读）
- 检测每个模型的 API key 是否配置（非 placeholder）
- 有异常时返回 `{"status": "degraded", "issues": [...]}`
- 可用于 Kubernetes liveness/readiness probe

### 6. 请求追踪

- `RequestIDMiddleware` 从 `X-Request-ID` header 读取或生成 UUID
- 存入 `ContextVar`，任意调用点可通过 `get_request_id()` 获取
- 注入响应 header，实现端到端请求关联
- 第三方库日志（httpx/openai/chromadb）抑制到 WARNING，减少噪声

### 7. 延迟实例化：import 不再触发 HTTP 客户端构建

**改进前**：`import app.graph` 或 `import app.rag` 就会执行 `ChatOpenAI(...)` 构造，即使只是用到 `AnalysisState` 类型也会初始化 HTTP 客户端。API key 缺失时静默替换为 `"sk-placeholder"`。

**改进后**：`get_llm()` 只在第一次调用时构建实例。import 安全。

---

## 四、没有做的（当前阶段属于过度工程化）

| 不做的 | 原因 |
|--------|------|
| tenacity 重试库 | 10 行手动循环完全等价，零新增依赖 |
| OpenTelemetry 分布式追踪 | 640 行项目，结构化日志已足够 |
| `asyncio.to_thread` 异步化 LLM 调用 | 需要重构 pipeline，留到 v0.2.0 |
| Pydantic Settings 配置类 | 只有 4 个 env var |
| 抽象 BaseLLMHarness 基类 | 无第二个实现需要多态 |
| 熔断器/限流器 | 单用户原型，无并发压力 |

---

## 五、修改文件清单

| 文件 | 改动类型 | 改动量 |
|------|---------|--------|
| `app/config.py` | **新增** | 67 行 |
| `app/llm_harness.py` | **新增** | 88 行 |
| `app/observability.py` | **新增** | 44 行 |
| `app/graph.py` | 修改 | +20 / -10 行 |
| `app/rag.py` | 修改 | +25 / -15 行 |
| `app/main.py` | 修改 | +30 / -20 行 |
| **总计** | | **新代码 ~140 行，修改 ~60 行** |

---

## 六、验证结果

```
✅ 所有新模块 import 正常（含缓存复用验证）
✅ /api/health    → {"status": "ok"}（真实检测 ChromaDB + API key）
✅ /api/chat      → LLM 调用通过 harness，返回正常回复
✅ /api/graph     → 三节点 LangGraph 流水线完整
✅ 无效模型名     → 400 + 可用模型列表
✅ LLM 调用失败   → 3 次重试 → 503 + 降级错误消息（不再 500 堆栈）
✅ 结构化日志输出 → 每次 LLM 调用的耗时、状态、调用方可追踪
```


---

## LangGraph 在项目中的用法

## 什么是 LangGraph

LangGraph 是 LangChain 团队出的一个**有状态、多步骤 Agent 编排框架**。如果把 LLM 单次调用比作"一个函数"，LangGraph 就是把多个 LLM 调用**编排成流水线（或更复杂的图）**，让它们按顺序协作，状态在节点间传递。

核心概念就三个：

| 概念 | 类比 | 项目中的对应 |
|------|------|-------------|
| **State**（状态） | 流水线上传递的工单 | `AnalysisState` — 装着 PDF 文本、提取数据、风险标注、最终报告 |
| **Node**（节点） | 流水线上的一个工位 | `analyst_node`、`risk_node`、`report_node` — 三个 Agent |
| **Edge**（边） | 工位之间的传送带 | `analyst → risk → report` — 定义执行顺序 |

---

## 项目中的用法

### 架构图

```
START
  │
  ▼
┌──────────┐    ┌──────────┐    ┌──────────┐
│ 分析师    │───→│ 风控     │───→│ 报告     │───→ END
│ Agent    │    │ Agent    │    │ Agent    │
└──────────┘    └──────────┘    └──────────┘
     │               │               │
  提取关键数据    标注风险等级    生成结构化摘要
  (JSON)         (risk_flags)    (Markdown)
```

三个 Agent 的角色分工：

1. **分析师 Agent**（`app/graph.py:64`）— 从研报原始文本中提取关键数据（营收、增速、估值、行业数据），输出结构化 JSON
2. **风控 Agent**（`app/graph.py:110`）— 检查分析师数据的合理性，从 5 个维度标注风险（数据合理性、一致性、缺失、行业风险、集中度），输出风险等级
3. **报告 Agent**（`app/graph.py:161`）— 汇总前三者的输出，生成 Markdown 格式的结构化分析摘要

### State 如何流转

```python
class AnalysisState(TypedDict):
    pdf_filename: str              # 入参
    raw_text: str                  # PDF 原文 → 分析师读取
    extracted_data: Optional[dict] # 分析师写入 → 风控读取 → 报告读取
    risk_flags: Optional[list]     # 风控写入 → 报告读取
    risk_level: Optional[str]      # 风控写入 → 报告读取
    final_report: Optional[str]    # 报告写入 → 最终输出
```

State 就像一份随着流水线逐渐填满的工单：
- 开始时只有 `pdf_filename` 和 `raw_text`
- 分析师节点往 State 里写入 `extracted_data`
- 风控节点读取 `extracted_data`，写入 `risk_flags` 和 `risk_level`
- 报告节点汇总前三者，写入 `final_report`

### 构建图的代码

```python
from langgraph.graph import StateGraph, START, END

builder = StateGraph(AnalysisState)       # 定义 State 结构

builder.add_node("analyst", analyst_node) # 注册 3 个节点
builder.add_node("risk", risk_node)
builder.add_node("report", report_node)

builder.add_edge(START, "analyst")        # 定义边：线性流水线
builder.add_edge("analyst", "risk")
builder.add_edge("risk", "report")
builder.add_edge("report", END)

graph = builder.compile()                 # 编译成可执行图
```

### API 入口

```python
# app/main.py
@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest):
    result = analyze_pdf(req.filename)  # → graph.invoke(state)
    return result

@app.get("/api/graph")
async def show_graph():
    return {"graph_ascii": print_graph_ascii()}  # 查看图结构
```

调用示例：

```bash
curl -X POST http://localhost:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"filename":"2026A股中期策略.pdf"}'
```

返回结构：

```json
{
  "pdf_filename": "2026A股中期策略.pdf",
  "extracted_data": { "key_metrics": {...}, "industry_data": {...}, ... },
  "risk_flags": [{ "维度": "数据合理性", "等级": "中", "描述": "..." }],
  "risk_level": "medium",
  "final_report": "## 研报分析摘要\n### 核心数据\n..."
}
```

---

## 解决了什么问题

### 之前：单次 LLM 调用

```
PDF 原文 → 一个 prompt 让它做所有事 → 输出
```

问题：
- 一个超长 prompt 同时要求提取数据、检查风险、写报告，LLM 容易顾此失彼
- 没有中间质量检查——如果数据提取错了，后面的分析全错
- 无法对每个环节单独调优（分析师要 temperature=0，写报告要 0.3）

### 之后：LangGraph 多 Agent 流水线

```
PDF 原文 → 分析师（只做提取）→ 风控（只做审查）→ 报告（只做汇总）
              ↓                    ↓                ↓
        extracted_data        risk_flags       final_report
```

好处：
- **职责分离**：每个 Agent 只做一件事，prompt 更聚焦，输出更可靠
- **质量关卡**：风控 Agent 在数据进入报告之前拦截问题（如数据缺失、增速异常）
- **可观测**：每一步的中间结果都留在 State 里，出问题时知道是哪一环节的问题
- **可扩展**：加一个"合规审查 Agent"只需 `add_node + add_edge`，不改现有代码

---

## 改进了什么

| 维度 | 单次 LLM 调用 | LangGraph 多 Agent |
|------|-------------|-------------------|
| **输出可靠性** | 一问全包，容易遗漏 | 三关审核，层层校验 |
| **风险感知** | 无 | 风控 Agent 独立标注 risk_flags + risk_level |
| **可维护性** | 一个 prompt 几百行 | 3 个小 prompt，各管各的 |
| **可观测性** | 黑盒 | State 每步可见，`/api/graph` 还能打印图结构 |
| **扩展性** | 改 prompt = 影响全部 | 加 Agent = 加边，不影响现有节点 |
| **成本** | 1 次 LLM 调用 | 3 次 LLM 调用（但每步 prompt 更短，总 token 近似） |

---

## 和 RAG 的关系

现在项目有两个独立管道，互补：

| 功能 | 接口 | 用途 |
|------|------|------|
| **RAG 问答** | `POST /api/askRAG` | 针对所有文档的即兴提问："下半年A股怎么看？" |
| **多 Agent 分析** | `POST /api/analyze` | 对一份 PDF 的结构化深度分析：提取数据 → 风控 → 报告 |

它们可以串联使用——比如先跑 LangGraph 对某份研报做深度分析，把分析结果存入知识库，再用 RAG 检索时就能命中这些结构化数据。

---

## 当前项目完整管道总览

```
                         fin-agent
                            │
              ┌─────────────┼─────────────┐
              │             │             │
         POST /api/    POST /api/     POST /api/
           upload        askRAG        analyze
              │             │             │
              ▼             ▼             ▼
         PDF 入库      RAG 问答      LangGraph
              │             │        多 Agent 分析
              ▼             │             │
         ┌────────┐         │      ┌──────┼──────┐
         │ChromaDB │◄───────┘      │      │      │
         │(v4 emb) │               ▼      ▼      ▼
         └────────┘            分析师  风控  报告
                              (提取) (审查)(汇总)

公共基础：
  - DeepSeek V4 Flash（LLM）
  - text-embedding-v4（向量化）
  - DashScope API（Embedding / Qwen 备选）
```


---

## 面试问答手册

## 一、项目概述（30秒电梯演讲）

> "这是一个基于 LangGraph 多 Agent 编排的金融研报分析系统。核心能力是：上传一份 PDF 研报后，三个 LLM Agent（分析师、风控、报告）流水线式协作，自动提取关键数据、标注风险、生成结构化报告。另外还有一个 RAG 检索链路，支持多查询改写、HyDE、LLM Reranker 三种增强策略，在自建 Benchmark 上召回率达到 100%。"

面试官追问的每个点 ↓

---

## 二、架构设计类

### Q1：为什么用 LangGraph 而不是自己写 if-else 串起来？

**考点**：是否理解 agent 编排框架的价值。

**参考答案**：
- 当前是线性流水线（analyst → risk → report），if-else 也能写。但 LangGraph 提供的是**可扩展的图结构**——下一步加条件分支（比如 data_quality=low 时跳过报告直接返回警告）只需要加 `add_conditional_edges`，if-else 会越来越失控。
- **状态管理**：`AnalysisState(TypedDict)` 在节点间自动传递、合并，不用手动管理中间结果的线程安全。
- **可观测性**：`graph.get_graph().draw_ascii()` 一行代码出拓扑图，自己写的状态机做不到。
- **未来扩展**：人工审核节点（human-in-the-loop）、并行 fan-out、流式输出，LangGraph 都是原生支持。

### Q2：为什么只有 3 个 Agent，没有做更复杂的编排？

**考点**：是否过度设计。

**参考答案**：
- 3 个 Agent 已经覆盖了金融分析的完整链路：**数据提取 → 风险评估 → 报告生成**。再加 Agent（比如独立的估值 Agent、行业分析 Agent）会增加延迟和成本，但当前场景下收益有限。
- 这是**刻意保持简单**——三个 Agent 之间的数据依赖是线性的，复杂编排（如 Map-Reduce、并行投票）留给真正需要的场景。
- 但架构上已经准备好了：`StateGraph` 加一个节点只需 3 行代码。

### Q3：你的 Graph 里，如果 analyst 节点失败了，后续节点怎么办？

**考点**：错误传播和容错设计。

**参考答案**：
- **第一层防护**：harness 层的 `call_llm()` 自动重试 3 次（指数退避），大部分瞬时故障在这一层就消化了。
- **第二层防护**：重试耗尽后 `LLMResult.ok=False`，analyst 返回 `{"extracted_data": {"error": "LLM调用失败: ..."}}`。
- **第三层防护**：risk 节点检测到 `"error" in data`，跳过 LLM 风控分析，直接返回高风险标记 `{"risk_level": "high", "risk_flags": [{"描述": "上游分析师未能提取有效数据"}]}`。
- 所以**不会崩溃，而是优雅降级**——分析师失败 → 整体风险标记为 high → 报告节点收到后生成"数据不可用"的声明。

### Q4：三个 Agent 之间怎么传递数据？状态管理怎么做的？

**考点**：LangGraph State 机制。

**参考答案**：
- 用 `AnalysisState(TypedDict)` 定义共享状态，每个节点返回一个 dict，LangGraph 自动 merge 到全局 state。
- 关键字段：`raw_text → extracted_data → risk_flags/risk_level → final_report`，每个节点只写自己产出的字段，读上游字段。
- 这种模式的好处是**节点之间解耦**——换掉 risk 节点的 prompt 或模型，analyst 和 report 完全不受影响。

---

## 三、RAG 深度问题

### Q5：你的 RAG 为什么需要 Query Rewriting + HyDE + Reranker 三个策略？是不是过度设计？

**考点**：是否真正理解每个策略解决的问题。

**参考答案**：
- 这三个策略解决的是**不同维度**的召回失败：

| 策略 | 解决的问题 | 场景 |
|------|-----------|------|
| Query Rewriting | **术语鸿沟**——用户问"这公司估值贵不贵"，文档里写的是"市盈率处于历史高分位" | 口语化查询、专业术语不对齐 |
| HyDE | **短查询/模糊查询**——"新能源怎么样？"6个字，向量和文档语义差异大 | 极短问题、缺乏上下文的提问 |
| LLM Reranker | **粗排精度不足**——embedding 召回 top-20，但 embedding 不是为相关性设计的，需要 LLM 精排 | 多文档混合检索的精确排序 |

- 三者可以叠加——rewrite 生成多个查询 + HyDE 生成假答案，合并去重后 reranker 精选 top-4。Benchmark 上从 25% → 88.9% → 100% 是真实数据（commit 记录可查）。

### Q6：HyDE 的原理是什么？为什么"假答案"比"真问题"更好检索？

**考点**：对向量检索和 HyDE 论文的理解。

**参考答案**：
- **核心洞察**：embedding 模型是把文本映射到语义空间——问题的语义向量和答案文档的语义向量往往不在同一区域。但**假答案的向量**和真实文档的向量更接近，因为它们都是"陈述/分析"风格而非"提问"风格。
- 流程：用户问"新能源行业增长前景" → LLM 生成一段 200-300 字的假分析报告 → 用假报告的向量去 ChromaDB 检索 → 召回的真文档和假报告语义相近。
- 代价是 +1 次 LLM 调用（~2秒），收益是短查询场景的召回率大幅提升。

### Q7：为什么 Embedding 选 text-embedding-v4 而不是通用的 text-embedding-ada-002？

**考点**：技术选型的 trade-off。

**参考答案**：
- v4 是 DashScope（阿里云）的模型，中文金融文本场景下比 OpenAI 的 ada-002 表现更好。
- 1024 维向量，平衡了精度和存储成本。
- 走 OpenAI 兼容接口（`/compatible-mode/v1`），代码层面不需要特殊适配。
- 潜在问题：供应商锁定。如果要切到其他 embedding 服务，需要重建整个向量库。但 `DashScopeV4Embeddings` 实现了 LangChain 的 `Embeddings` 接口，换模型只需要实现一个新类。

### Q8：Chunker 参数为什么选 chunk_size=800, overlap=150？

**考点**：对 RAG chunking 策略的理解。

**参考答案**：
- `chunk_size=800`：中文约 400 字。金融研报一个自然段通常在这个范围，过大则检索粒度粗、包含噪声；过小则缺少上下文。
- `chunk_overlap=150`：约 75 个中文字。关键数据（如营收数字）如果在 chunk 边界被切断，overlap 确保相邻 chunk 包含这个数据。
- `separators` 按优先级从粗到细：`\n\n → \n → 。→ ！→ ？→ ；→ . → ! → ? → ; → 空格`。优先在段落/句子边界切分，而不是硬性按字符数。
- 这是从 `500/50` 调整过来的（commit 记录：c4a5d97），调整后 top-1 召回率从 25% 提升到 88%。

### Q9：多查询融合检索的去重逻辑是怎么做的？

**考点**：细节实现能力。

**参考答案**：
- 当 use_rewrite 或 use_hyde 开启时，`search_queries` 包含原始问题 + 改写版本 + HyDE 假答案。
- 每个查询独立检索 `per_query_k` 个结果，合并后按**前 200 字符去重**——用内容前缀而非完整内容（因为不同查询可能召回同一文档的不同 chunk，前缀相同说明是同一段落）。
- 去重后再交给 reranker 精选 top-k。
- 这个设计的 trade-off：前 200 字符去重可能误杀内容不同但前缀巧合相同的 chunk，但概率极低（需要两个不同文档的前 200 字符完全相同），实际测试中未出现。

---

## 四、Harness 工程问题

### Q10：你给项目加了一个 Harness 层，为什么？解决了什么实际问题？

**考点**：工程化意识——知道什么时候该从"能跑"升级到"可靠"。

**参考答案**：
- **改前痛点**：7 个裸 `llm.invoke()` 调用，任何一个网络抖动 → 未处理异常 → 500。640 行代码零条日志，出了问题完全不可见。
- **改后效果**：
  - 所有 LLM 调用有**指数退避重试**（最多 4 次尝试），瞬时故障自动恢复
  - **结构化日志**：每次调用的耗时、状态、调用方可追踪
  - **优雅降级**：LLM 失败不崩溃，各节点有明确的降级路径
  - **健康检查**：从永返 ok → 真正检测 ChromaDB 连通性和 API key 有效性
- **设计原则**：只用 140 行新代码，不引入任何新依赖，不改变 API 接口。Harness 是薄层，不是框架。

### Q11：你的 Harness 为什么不直接用 tenacity 库？

**考点**：依赖管理意识。

**参考答案**：
- tenacity 的核心功能（装饰器 + 指数退避 + 异常过滤）在这个场景下只需要 10 行手动循环。引入一个依赖意味着：版本管理、安全审计、与项目其他依赖的兼容性。
- 当重试逻辑变复杂（如针对不同异常类型有不同退避策略、异步重试、回调钩子），tenacity 才有性价比。当前 3 次均匀退避完全不需要。
- **原则**：能用标准库就先用标准库，等复杂度逼迫你再引入依赖。

### Q12：LLMResult 为什么是 dataclass 而不是抛异常？

**考点**：错误处理哲学。

**参考答案**：
- 这是**"错误作为值"**模式（Rust 的 `Result<T, E>`、Go 的 `(value, error)`）。
- 抛异常的问题：调用方容易忘记 try/except，导致异常一路传播到最外层变成 500。返回 `LLMResult` 强制调用方检查 `.ok`——不检查就拿不到 `.content`。
- 在 LangGraph 节点里尤其重要：节点函数应该返回 dict 而不是抛异常，这样图的其他节点可以基于错误状态做降级决策。

---

## 五、Prompt Engineering 问题

### Q13：三个 Agent 的 Prompt 是怎么设计的？有什么技巧？

**考点**：Prompt Engineering 实践。

**参考答案**：
- **分析师 Prompt**：明确 JSON schema 约束输出格式；"如果字段没有数据填 null，不要编造"防止幻觉；`data_quality` 自评字段让下游做决策。
- **风控 Prompt**：5 个具体检查维度（合理性、一致性、缺失、行业、集中度），比模糊的"检查风险"更可靠；要求输出 `risk_flags` 数组 + 整体 `risk_level`，结构化便于报告节点消费。
- **报告 Prompt**：要求 Markdown 格式输出（表格、emoji 标记 ⚠️）；"数据质量低时在开头声明局限性"利用上游的 `data_quality` 字段。
- **共性技巧**：都要求输出 JSON/Markdown（结构化输出减少解析失败）；都有"不要编造"约束；都要求当输入不足时明确指出而非硬编。

### Q14：LLM 输出的 JSON 解析失败了怎么办？

**考点**：防御性编程。

**参考答案**：
- 每个节点的 JSON 解析都有 `try/except json.JSONDecodeError`。
- 解析失败时提供**降级值**：analyst 返回 `{"error": "解析失败", "raw_output": resp.content[:500]}`（保留原始输出方便调试），risk 返回空风险标记 + "unknown"级别，rewrite 回退到原始问题。
- 此外，LLM 输出经常被包裹在 ` ```json ... ``` ` 中——会有预处理：`split("```")[1]` 提取内容再解析。
- Harness 层的日志也会记录每次调用的输出长度，如果频繁出现解析失败，可以从日志里找到原始输出优化 prompt。

---

## 六、系统设计与 Trade-off

### Q15：为什么 LLM 调用是同步的？FastAPI 里同步调用会阻塞事件循环，你怎么看？

**考点**：对异步/同步的理解和诚实度。

**参考答案**：
- 坦诚说：**当前是一个已知的 trade-off，留到 v0.2.0 解决**。
- 当前是单用户原型，同步调用在 FastAPI 的线程池里执行，不会完全阻塞。但如果并发上来（比如 10 个用户同时 analyze），线程池会耗尽。
- 解决方案明确：`llm.ainvoke()` + `asyncio.to_thread()` 或在 LangGraph 的 `ainvoke()` 上构建异步 pipeline。但 async 化会传导——config、harness、所有调用方都要改。
- 面试官更看重的是**你知道这个问题存在，有明确的改进计划**，而不是藏着不说。

### Q16：如果要支持流式输出（SSE），你会怎么改？

**考点**：对 streaming 的理解。

**参考答案**：
- LangGraph 支持 streaming：`graph.astream()` 可以在每个节点完成后 yield 状态更新。
- FastAPI 端用 `StreamingResponse` + `text/event-stream`。
- 具体方案：`/api/analyze/stream` 端点，每完成一个节点就推送 `{"node": "analyst", "status": "done", "data": {...}}`。
- 报告节点更细粒度：`llm.astream()` token 级别的流式输出，前端逐字渲染。
- **但**：当前 DeepSeek 的 streaming 稳定性不如非 streaming，实际落地需要 fallback 到非 streaming 模式。

### Q17：RAG 的向量库为什么选 ChromaDB 而不是 Milvus/Pinecone/Weaviate？

**考点**：技术选型有据。

**参考答案**：
- **ChromaDB 优势**：零配置（pip install 即可，不需 Docker/云服务），嵌入式运行，适合原型和小规模部署。
- **局限**：不支持分布式，数据量超过 100 万向量时性能下降，没有内置的权限管理。
- **迁移路径**：LangChain 的 VectorStore 接口统一了 API——`Chroma(...)` 换成 `Milvus(...)` 或 `Pinecone(...)` 只需要改两行代码。
- 选 ChromaDB 是因为当前阶段**开发体验优先于扩展性**。面试官问"为什么不用 X"的正确回答是"我知道 X 的优势，但当前阶段 Y 更合适，且迁移成本可控"。

### Q18：你的测试只有 test_recall.py 的端到端召回率测试，为什么没有单元测试？

**考点**：对测试策略的思考。

**参考答案**：
- `test_recall.py` 覆盖了 18 个测试用例、5 类场景（直接查询 / 口语化 / 术语鸿沟 / 短模糊 / 跨文档），是**最有价值的测试**——它测的是用户真正关心的"搜不搜得到"。
- 单元测试的难点：3 个节点的核心逻辑是 LLM 调用，mock LLM 的返回值需要比业务代码更多的 mock 代码（要模拟 JSON 解析、各种失败模式），性价比低。
- **改进方向**：把节点的纯逻辑（JSON 解析、错误检测、截断）抽成独立函数做单元测试；LLM 调用用 harness 层的 mock 做集成测试。

---

## 七、开放性问题

### Q19：如果要上线给 100 个用户用，你还缺什么？

**考点**：生产环境意识。

**参考答案**（按优先级排）：
1. **认证/鉴权**（API key 或 OAuth，当前所有端点裸奔）
2. **速率限制**（防止单个用户打爆 LLM 预算）
3. **Token 预算管理**（每个用户/每次调用的 token 上限）
4. **异步化**（`llm.ainvoke()` + LangGraph `ainvoke()`）
5. **持久化任务队列**（长时间分析任务放后台，Celery/Redis）
6. **监控告警**（LLM 调用失败率、P99 延迟、日均花费）
7. **CORS 收紧**（当前 `allow_origins=["*"]`）
8. **PDF 上传安全**（文件大小限制、病毒扫描、类型校验）
9. **数据隔离**（不同用户上传的 PDF 索引隔离）

### Q20：这个项目最大的技术挑战是什么？

**考点**：自我认知和叙事能力。

**建议叙事框架**：
- **挑战**：RAG 召回率从 25% 提升到 100% 的过程——不是某个单一技巧解决的，而是**组合策略**（chunk 参数优化 + LLM Reranker + Query Rewriting + HyDE）的系统工程。
- **难点**：每个策略解决不同维度的失败 case（术语鸿沟 / 短查询 / 粗排精度不足），需要先诊断失败根因再对症下药，而不是无脑堆策略。
- **量化结果**：top-1 召回率 25% → 88% → 100%，每一步都有 commit 记录可追溯。

### Q21：如果让你重新设计这个项目，你会改什么？

**考点**：反思能力。

**参考答案**（体现成长思维）：
1. **一开始就加 Harness 层**——裸调 LLM 在原型阶段很快，但第一个网络故障就会让你意识到需要重试和日志。早加成本低。
2. **Prompt 版本管理**——现在 prompt 是 Python 字符串常量，改 prompt 要改代码。应该存 YAML/数据库，支持 A/B 测试。
3. **评估先行**——test_recall.py 是后来补的。应该先定义 Benchmark，再优化 RAG，避免"我觉得变好了"的主观判断。
4. **API 设计**——analyze 是同步接口，对于大 PDF 可能超时。应该设计成异步任务模式（提交任务 → 轮询状态 → 获取结果）。

---

## 八、面试前的最后检查

- [ ] 能独立画出系统架构图（FastAPI → Graph/RAG → LLM + ChromaDB）
- [ ] 能解释每个 commit 的动机和效果（git log 里的 7 个 commit）
- [ ] 能跑通 `python test_recall.py` 并解释每个 case
- [ ] 能说出 3 个 LangGraph 的替代方案及优劣（CrewAI、AutoGen、自己写）
- [ ] 能说出 ChromaDB 的 3 个替代方案（Milvus、Pinecone、Weaviate、Qdrant 中选 3 个）
- [ ] 能解释 Harness 层每个文件的职责
- [ ] 准备一个"你最自豪的 technical decision"的故事（推荐：RAG 召回率三阶段提升）


---

## LangGraph 面试 15 题

## 一、基础必问（必中 3-4 题）

### Q1：StateGraph 和 MessageGraph 有什么区别？你为什么用 StateGraph？

**考点**：是否理解 LangGraph 的两种图类型。

**参考答案**：
- `MessageGraph`：State 是 `list[BaseMessage]`，适合对话型 agent——每个节点追加消息到列表，类似聊天记录。
- `StateGraph`：State 是自定义 schema（我的是 `AnalysisState(TypedDict)`），适合**结构化工作流**——每个节点读写特定字段，不是简单堆消息。
- 我选 StateGraph 因为三个节点之间的数据是**结构化的业务对象**（extracted_data dict、risk_flags list、risk_level string），不是对话消息。MessageGraph 的消息列表模式在这里反而需要额外解析，增加复杂度。

### Q2：你的 AnalysisState 为什么用 TypedDict 而不是 Pydantic BaseModel？

```python
class AnalysisState(TypedDict):
    pdf_filename: str
    raw_text: str
    extracted_data: Optional[dict]
    risk_flags: Optional[list]
    risk_level: Optional[str]
    final_report: Optional[str]
```

**参考答案**：
- **TypedDict**：轻量，只做类型标注，不参与运行时验证。LangGraph 原生支持，每个节点返回 partial dict 自动 merge。
- **Pydantic BaseModel**：有运行时验证、默认值、序列化能力。适合复杂状态需要数据校验的场景。
- 我选 TypedDict 因为字段简单（6 个字段，都是基本类型），不需要额外校验开销。如果 State 有嵌套对象、需要 `field_validator`、需要 `.model_dump()`，再升级到 Pydantic 也不迟——LangGraph 两种都支持。

**追问**：那你每个字段都是 Optional，为什么不给默认值？
- 因为 `graph.invoke()` 时需要显式传入所有字段（包括 `None`），这样调用方**不会漏传字段**。如果给了默认值，漏传字段静默成功，后面节点读到 None 时不好定位是谁的锅。

### Q3：你的图是怎么 build 出来的？compile() 做了什么？

```python
builder = StateGraph(AnalysisState)
builder.add_node("analyst", analyst_node)
builder.add_node("risk", risk_node)
builder.add_node("report", report_node)
builder.add_edge(START, "analyst")
builder.add_edge("analyst", "risk")
builder.add_edge("risk", "report")
builder.add_edge("report", END)
graph = builder.compile()
```

**参考答案**：
- `StateGraph(AnalysisState)`：声明图的 state schema。
- `add_node()`：注册三个节点函数，每个函数签名是 `(state) -> partial_dict`。
- `add_edge()`：声明节点间的**强制路由**（无条件分支）。
- `START` / `END`：LangGraph 的两个 sentinel 节点——`START` 是图入口，`END` 是图出口。
- `compile()`：**验证图结构完整性**（每个节点是否可达、是否有死循环、state schema 是否一致），然后生成执行计划。编译失败会在启动时报错，而不是运行时才发现。

### Q4：invoke() 和 stream() 和 astream() 有什么区别？

**参考答案**：

| 方法 | 同步/异步 | 返回值 | 适用场景 |
|------|----------|--------|---------|
| `graph.invoke(state)` | 同步 | 最终 state dict | 当前项目：完整分析一次性返回 |
| `graph.stream(state)` | 同步生成器 | 每个节点完成后的 state 更新 | 前端展示进度："正在分析..."→"正在评估风险..." |
| `graph.astream(state)` | 异步生成器 | 同上，非阻塞 | 高并发场景 + SSE 推送到前端 |

- 当前项目用 `invoke()` 因为分析任务是同步的、单用户的。如果要做流式报告（逐字展示 final_report），需要 `astream()` + FastAPI `StreamingResponse`。

### Q5：add_edge 和 add_conditional_edges 有什么区别？你的项目为什么全用 add_edge？

**参考答案**：
- `add_edge("A", "B")`：A 执行完后**无条件**走向 B。
- `add_conditional_edges("A", router_fn, {"high": "B", "low": "C"})`：A 执行完后调用 `router_fn(state)` 根据返回值动态选择下一个节点。
- 当前项目全用 `add_edge` 因为流程是确定性的：分析师 → 风控 → 报告。但**我已经设计好了条件分支的插入点**——比如 risk 节点检测到 data_quality=low 时，可以跳过报告直接返回：

```python
# 未来改进（一行边改成条件边）
def route_after_analyst(state):
    data = state.get("extracted_data", {})
    if data.get("data_quality") == "low":
        return "report_with_warning"  # 走降级报告
    return "risk"  # 正常流程

builder.add_conditional_edges("analyst", route_after_analyst, {
    "risk": "risk",
    "report_with_warning": "report_low_quality",
})
```

---

## 二、进阶深挖（面试官区分水平的题目）

### Q6：LangGraph 的节点函数返回 dict 后，State 是怎么合并的？如果两个节点同时写同一个字段会怎样？

**考点**：State reducer 机制。

**参考答案**：
- 默认行为是**浅合并**：节点返回的 dict 和全局 state 做 `{**state, **node_return}`，同名 key 被覆盖。
- 如果两个节点**并行**写同一个字段（如 `extracted_data`），后完成的覆盖先完成的——这是默认行为，可能导致数据丢失。
- LangGraph 支持**自定义 reducer**：在 TypedDict 上用 `Annotated[type, reducer_fn]` 定义合并策略。比如 `risk_flags: Annotated[list, operator.add]` 可以实现多个节点的风险标记**追加而非覆盖**。

```python
# 如果未来 risk 节点拆成多个并行子节点，需要自定义 reducer
class AnalysisState(TypedDict):
    risk_flags: Annotated[list, operator.add]  # 多节点追加而非覆盖
```

### Q7：如果 analyst 节点执行 30 秒后超时了，LangGraph 能自动重试单个节点吗？

**考点**：容错机制的深度理解。

**参考答案**：
- LangGraph **本身不提供节点级自动重试**。它只负责编排，重试逻辑需要在节点函数内部实现。
- 这就是为什么我的 harness 层的 `call_llm()` 自带 3 次重试——**把重试逻辑放在节点内部而非图层面**，更精确（只重试 LLM 调用，PDF 解析失败不重试）。
- 如果确实需要节点级重试，LangGraph 的 `Checkpoint` 机制可以实现：失败后从 checkpoint 恢复，重新执行失败节点。但这需要在 `invoke()` 时配置 `checkpointer`。

### Q8：Checkpoint 是什么？你的项目用了吗？

**考点**：对 LangGraph 持久化机制的理解。

**参考答案**：
- **Checkpoint**：LangGraph 在每个 super-step（节点执行后）自动保存 state 快照。支持：
  - **故障恢复**：从最后一个成功的 checkpoint 继续执行
  - **Human-in-the-loop**：暂停等人工审批，审批后从 checkpoint 继续
  - **Time travel**：回退到历史 state 重新执行
- **存储后端**：`MemorySaver`（开发用）、`SqliteSaver`（单机）、`PostgresSaver`（生产）。
- **当前项目没用 Checkpoint**——3 个节点的线性流水线，每个节点执行时间 < 10 秒，checkpoint 的收益不大。但如果加了人工审核节点（"风控认为高风险，请确认是否继续"），就必须用 `SqliteSaver` + `interrupt()`。

### Q9：LangGraph 怎么支持并行执行？你的项目能并行化吗？

**考点**：对 LangGraph 高级特性的理解。

**参考答案**：
- **Send API**：从一个节点发送多个并行任务到同一个目标节点。

```python
# 例如：对 analyst 提取的每个指标并行做风控检查
def fanout_to_parallel_risk(state):
    metrics = state["extracted_data"].get("key_metrics", {})
    for metric_name, value in metrics.items():
        yield Send("risk_checker", {"metric": metric_name, "value": value})

builder.add_conditional_edges("analyst", fanout_to_parallel_risk, ["risk_checker"])
```

- **当前项目不适合并行化**：3 个节点是**数据依赖的线性链**——risk 必须等 analyst 产出数据，report 必须等 risk 产出标记。硬要并行只会增加复杂度。
- **适合并行的场景**：如果 analyst 拆成"营收分析""利润分析""行业分析"三个独立子节点，它们之间没有依赖，可以并行。

### Q10：Tool Calling 在你的 LangGraph 里怎么实现？和 ReAct 模式什么关系？

**考点**：Agent 模式的理解。

**参考答案**：
- 当前项目**没有 Tool Calling**——三个 Agent 都是纯 LLM 推理（读文本 → 输出 JSON），不需要调用外部工具。
- **ReAct 模式**（Reasoning + Acting）是 Tool Calling 的标准实现：LLM 在循环中思考 → 调用工具 → 观察结果 → 继续思考，直到给出最终答案。LangGraph 原生支持这种模式：用一个 `tool_node` + `conditional_edges` 实现"调用工具 → 回到 agent 节点"的循环。
- **如果需要加工具**（比如 analyst 需要调用"查询股票实时价格"的 API），做法是：

```python
from langgraph.prebuilt import ToolNode

tools = [get_stock_price, search_news]
tool_node = ToolNode(tools)

builder.add_node("analyst", analyst_node)
builder.add_node("tools", tool_node)
builder.add_conditional_edges("analyst", should_use_tool, {
    "use_tool": "tools",
    "done": "risk",
})
builder.add_edge("tools", "analyst")  # 工具结果回传给 analyst 继续推理
```

---

## 三、框架对比 & 选型论证（面试官最爱追问的）

### Q11：为什么选 LangGraph 而不是 CrewAI？

**考点**：框架对比能力，避免"只会用一个"的印象。

**参考答案**：

| 维度 | LangGraph | CrewAI |
|------|-----------|--------|
| **角色定义** | 节点函数 + SystemMessage | `Agent(role="...", goal="...")` 高抽象 |
| **编排方式** | 显式图（add_node/edge） | `Task(agent=..., expected_output=...)` 隐式编排 |
| **State 管理** | 自定义 TypedDict/Pydantic，精确控制 | 框架内部管理，黑盒 |
| **灵活性** | 极高——你想怎么连就怎么连 | 中等——适合预定义的角色协作模式 |
| **学习曲线** | 需要理解图、state、reducer | 更平缓，概念少 |

- **我选 LangGraph 的原因**：金融分析流程需要**精确控制数据流**（analyst 的 extracted_data 必须完整传递给 risk），LangGraph 的显式 state 管理满足这个需求。CrewAI 更适合"给几个角色一个目标，让他们自己商量"的场景，可控性不如 LangGraph。
- 但 CrewAI 的优势是**开发速度快**——3 行代码定义一个 Agent。如果需求简单且不需要精确的 state 传递，我会选 CrewAI。

### Q12：为什么选 LangGraph 而不是 AutoGen？

**参考答案**：
- AutoGen 的核心是**对话驱动**——Agent 之间通过消息通信，适合多 Agent 对话、辩论、协商场景。
- AutoGen 有 GroupChat、RoundRobin 等管理模式，做复杂对话流很强。
- 我的场景是**数据流水线**而非对话——analyst 产出数据后不需要和 risk "讨论"，直接传递即可。对话模式在这里是开销而非价值。
- **选型原则**：对话型协作选 AutoGen，确定性工作流选 LangGraph，角色扮演选 CrewAI。

---

## 四、实战 & 事故应对

### Q13：你怎么调试 LangGraph 的图？节点里的 bug 怎么定位？

**参考答案**：
- **图结构调试**：`graph.get_graph().draw_ascii()` 一行出拓扑图。我的 `/api/graph` 端点直接暴露这个图。
- **State 调试**：`graph.stream()` 可以在每个节点完成后看到 state 变化，定位是哪个节点写错了字段。
- **节点内部调试**：harness 层的 `call_llm()` 日志标注了 `caller=`（analyst/risk/report），可以精确知道是哪个节点、哪次 LLM 调用出问题。
- **LangSmith/LangFuse**：LangGraph 原生集成，每个节点执行都有 trace。但当前项目规模小，harness 层日志已足够。

### Q14：如果要在 analyst 和 risk 之间加一个人工审核步骤，你怎么改？

```python
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt

def human_review_node(state):
    # interrupt() 暂停图执行，等待外部输入
    approval = interrupt({
        "message": "请审核分析师的提取结果",
        "data": state["extracted_data"],
    })
    if approval.get("action") == "reject":
        return {"extracted_data": {"error": "人工审核驳回"}}
    return {}  # 通过，继续执行

# 需要在 compile 时传入 checkpointer
graph = builder.compile(checkpointer=MemorySaver())

# invoke 时传入 thread_id 用于恢复
graph.invoke(state, config={"configurable": {"thread_id": "task-123"}})
# ... 用户审核后 ...
graph.invoke(None, config={"configurable": {"thread_id": "task-123"}})
# 传入 Command(resume=...) 恢复执行
```

### Q15：你的图如果从 3 个节点扩展到 30 个节点，会遇到什么问题？

**考点**：架构扩展思维。

**参考答案**：
1. **State 膨胀**：30 个节点共享同一个 State dict，会变成几十个字段的巨型字典。解决方案：用 **Subgraph**——把相关节点打包成子图，子图有独立的 state schema。
2. **边管理混乱**：手动管理 30 个节点的边关系容易出错。解决方案：用 `add_sequence` 声明式定义线性段 + 清晰的模块边界。
3. **调试困难**：需要在图中加入更多可观测点（LangSmith/LangFuse 的分布式 tracing）。
4. **LLM 调用成本**：30 个节点假设每个调一次 LLM，单次分析的成本和延迟会很高。需要**智能路由**（data_quality=high 跳过冗余检查节点）和**缓存**（相同输入的节点结果复用）。

---

## 五、面试现场 check

**如果能流畅回答这 5 个问题，LangGraph 这关就稳了**：

1. ✅ "StateGraph 和 MessageGraph 有什么区别？你项目里为什么用前者？"
2. ✅ "add_edge 和 add_conditional_edges 的区别？你的条件分支插在哪里？"
3. ✅ "LangGraph 的 state 是怎么在节点间传递和合并的？"
4. ✅ "如果中间节点失败了，怎么重试？怎么人工介入？"
5. ✅ "LangGraph vs CrewAI vs AutoGen，分别适合什么场景？"
