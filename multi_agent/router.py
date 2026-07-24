"""Router: 意图分类 + 任务分派。

硬规则优先，兜底 LLM——route_override() 返回 plan 或 None。
"""

import re


# ── Router System Prompt（LLM 兜底用）──

ROUTER_PROMPT = """你是路由 Agent。分析用户 query 并输出执行计划的 JSON。

你支持的专业 Agent:
- sql: 查数据库（订单、员工、部门、产品、客户等结构化数据）
- strategy: 查公司制度/政策文档（提成、年假、考勤、定价政策、公司战略等）
- hbase: 生成 HBase Shell 命令（scan/get/count/put 等）。只生成，不执行
- hive: 生成 Hive/Impala (Hue) SQL 查询。只生成，不执行
- analysis: 综合分析与建议，或回答对话历史相关问题（不会自己查库）

【优先级从高到低，必须严格遵守】
1. 闲聊/能力介绍（"你好"、"你能做什么"、"你是谁"）→ plan 必须为 []，禁止 sql/strategy
2. 对话历史/元问题（"刚才问了什么"、"上一个问题是"、"之前查了什么"）→ 只用 analysis，禁止 sql/strategy
   task 写: "用户询问对话历史，请根据上下文回答"
3. 制度/政策（"提成比例"、"年假多少天"、"考勤规则"）→ 只用 strategy，禁止 sql
4. HBase 查询（"HBase scan"、"帮我写个HBase命令"、"scan orders表"）→ 只用 hbase
   注意："scanner"/"scanning" 不是 HBase scan，禁止路由到 hbase
5. Hive/Impala/Hue 查询（"生成Hive建表语句"、"Impala分区查询"、"hue上写查询"）→ 只用 hive
6. 纯数据查询（"销售额多少"、"有多少员工"、拼音错别字如"销shou额"）→ 只用 sql
7. 需要可视化/图表（"图表展示"、"画图"、"可视化"、"生成图表"、"用图来看"）→ 必须 sql + analysis
8. 需要数据+建议（"分析趋势并给建议"）→ sql + analysis；若还要对照制度再加 strategy
9. 对比/变化/环比/同比（"对比本月和上月"、"订单量变化"）→ 必须 sql + analysis：
   - sql: 分别查询两个时期的数据
   - analysis: 对比变化幅度并解读

反例（禁止）:
- "销售人员的提成比例是多少" → 不要 sql，只要 strategy
- "你好，你能做什么" → 不要 sql，plan=[]
- "上一个问题是什么" → 不要 sql，只要 analysis

输出格式（只输出 JSON，不要其他文字）:
{"plan": [{"agent": "sql", "task": "具体任务描述"}], "combine": true}

每个 task 要具体、完整。不确定时宁可少派 agent，也不要默认加 sql。"""


# ── Router 硬规则：LLM 不可靠时兜底（与 ROUTER_PROMPT 优先级一致）──

_CHITCHAT_MARKERS = (
    "你好", "您好", "hi", "hello", "介绍下", "介绍一下",
    "自我介绍", "在吗", "谢谢", "再见", "你能做什么", "你会什么",
)
# 「你是谁 / 你现在是谁 / 你到底是谁」——子串 "你是谁" 匹配不到中间插字的情况
_CHITCHAT_WHO_RE = re.compile(r"你.{0,4}是谁")
_META_QUESTION_RE = re.compile(
    r"(刚才|上次|上条|上轮|之前|上一个|上一条|上一轮).{0,8}(问了|查了|问题|查询|语句|问了什么)|"
    r"(问了什么|查了什么|聊了什么|做过什么|查过什么|问过什么|还记得)|"
    r"(第一句|最初的?问题|最开始|最初一句)|"
    r"这[次轮场]对话|"
    r"上一个问题"
)
_STRATEGY_MARKERS = ("提成", "年假", "考勤", "定价政策", "公司战略", "休假", "制度", "政策")
_DATA_MARKERS = (
    "销售额", "订单", "员工", "部门", "客户", "产品销量", "多少人",
    "趋势", "对比", "分析", "统计", "数据库",
)
# 拼音/错别字容错：「销shou额」「销 额」≈ 销售额；「查询」+「销」也当数据查询
_FUZZY_SALES_RE = re.compile(r"销\S{0,8}额|查询.{0,6}销")
_COMPARE_MARKERS = ("对比", "环比", "同比", "变化", "增减")
_HBASE_MARKERS = (
    "hbase", "hbaseshell", "hbase查询", "hbase语句", "hbase命令",
)
# "scan" 是 HBase 独有操作动词（SQL 用 SELECT），做词边界匹配防误匹配 scanner/scanning
_HBASE_OP_RE = re.compile(r'\bscan\b')
# scanner/scanning 不是 HBase —— 硬拦，避免 LLM 看到 scan 子串误路由到 hbase/hive
_SCANNER_FALSE_RE = re.compile(r"scann(?:er|ing)", re.IGNORECASE)
_HIVE_MARKERS = (
    "hive", "hue", "impala", "大数据",
    "hql", "hiveql", "hive查询", "hive语句",
)
_COMPARE_DATA_MARKERS = ("订单", "销售", "员工", "数据", "部门", "产品")


def route_override(query: str, prev_agents: list[str] | None = None) -> list[dict] | None:
    """明确意图时返回硬编码 plan；否则返回 None，交给 LLM。

    用来兜住 route-002/004/007 这类「模型爱乱加 sql」的 case。
    prev_agents: 上一轮 plan 里用过的 agent 名（用于上下文继承）。
    """
    q = (query or "").strip()
    if not q:
        return []

    q_lower = q.lower()

    # HBase/Hive 优先于闲聊检查——"hi" 在 chitchat 里会误匹配 "hive"
    has_hbase = any(m in q_lower for m in _HBASE_MARKERS) or bool(_HBASE_OP_RE.search(q_lower))
    has_hive = any(m in q_lower for m in _HIVE_MARKERS)
    has_sql_kw = any(m in q for m in _DATA_MARKERS) or bool(_FUZZY_SALES_RE.search(q))

    # scanner/scanning ≠ HBase scan：无显式 hbase 标记时硬返回 analysis，不交给 LLM
    if _SCANNER_FALSE_RE.search(q_lower) and not any(m in q_lower for m in _HBASE_MARKERS):
        return [{"agent": "analysis", "task": q}]

    if has_hbase and not has_hive:
        return [{"agent": "hbase", "task": q}]
    if has_hive and not has_hbase:
        return [{"agent": "hive", "task": q}]
    if has_hbase and has_hive:
        return [
            {"agent": "hbase", "task": q},
            {"agent": "hive", "task": q},
        ]

    is_chitchat = (
        any(m in q_lower for m in _CHITCHAT_MARKERS) or bool(_CHITCHAT_WHO_RE.search(q))
    )
    if is_chitchat:
        if not has_sql_kw and not any(m in q for m in _STRATEGY_MARKERS):
            return []

    if _META_QUESTION_RE.search(q):
        return [{"agent": "analysis", "task": "用户询问对话历史，请根据上下文回答"}]

    has_strategy = any(m in q for m in _STRATEGY_MARKERS)
    if has_strategy and not has_sql_kw:
        return [{"agent": "strategy", "task": q}]

    if any(m in q for m in _COMPARE_MARKERS) and any(m in q for m in _COMPARE_DATA_MARKERS):
        return [
            {"agent": "sql", "task": q},
            {"agent": "analysis", "task": f"对比分析：{q}"},
        ]

    # 拼音/错别字数据查询：「查询销shou 额」→ sql（策略类已在上面拦截）
    if _FUZZY_SALES_RE.search(q) and not has_strategy:
        return [{"agent": "sql", "task": q}]

    # ── 上下文继承：上一轮只有 hbase/hive，本轮无冲突信号 → 继承 ──
    if prev_agents and not has_hbase and not has_hive and not has_sql_kw and not has_strategy:
        prev_set = {a for a in prev_agents if a in ("hbase", "hive")}
        if len(prev_set) == 1:
            return [{"agent": prev_set.pop(), "task": q}]

    return None
