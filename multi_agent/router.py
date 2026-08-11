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
   用户直接粘贴 Hive 方言语句（INSERT OVERWRITE、PARTITION (dt=...)、STORED AS 等）→ 只用 hive，禁止当 SQLite sql
6. 同时点名多个引擎（"SQL和hive有什么表"、"hbase和hive"）→ 每个引擎各派一个 Agent，禁止只派其中一个
7. 纯数据查询（"销售额多少"、"有多少员工"、拼音错别字如"销shou额"）→ 只用 sql
8. 需要可视化/图表（"图表展示"、"画图"、"可视化"、"生成图表"、"用图来看"）→ 必须 sql + analysis
9. 需要数据+建议（"分析趋势并给建议"）→ sql + analysis；若还要对照制度再加 strategy
10. 对比/变化/环比/同比（"对比本月和上月"、"订单量变化"）→ 必须 sql + analysis：
   - sql: 分别查询两个时期的数据
   - analysis: 对比变化幅度并解读

反例（禁止）:
- "销售人员的提成比例是多少" → 不要 sql，只要 strategy
- "你好，你能做什么" → 不要 sql，plan=[]
- "上一个问题是什么" → 不要 sql，只要 analysis
- "SQL和hive有什么表" → 不要只派 hive，必须 sql + hive
- 长背景提到「口径/指标」但末尾是「帮我查询…是多少」→ 只用 sql，不要 strategy

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
# 指标口径查询：「GMV怎么算」「销售额包含什么」「转化率口径」
_METRIC_LOOKUP_RE = re.compile(
    r"(怎么算|口径|定义|包含|含不含|是什么|什么意思|啥意思|指什么|算不算|包括)",
)
_METRIC_ALIASES = (
    "gmv", "dau", "mau", "ltv", "cac", "cvr", "arpu",
    "销售额", "客单价", "转化率", "留存率", "复购率", "退货率", "毛利率", "周转",
    "活跃用户", "订单量", "指标",
)
# 明确在「查数」而非「问口径」——长文背景里常夹带「口径/指标」，不能误派 strategy
_DATA_QUERY_ASK_RE = re.compile(
    r"(帮我查询|查询一下|查一下|请查询|是多少|有多少|总金额|总额|合计|"
    r"笔数|多少钱|统计一下|查一查|帮我查)"
)
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
# 用户把 CLI 当编辑器，直接粘贴 HiveQL 方言（无 "hive" 字样也会命中）
_HIVEQL_DIALECT_RE = re.compile(
    r"insert\s+overwrite|"
    r"\bpartition\s*\(|"
    r"stored\s+as\b|"
    r"lateral\s+view|"
    r"msck\s+repair|"
    r"show\s+partitions|"
    r"row\s+format|"
    r"\blocation\s+'hdfs|"
    r"\b(?:cluster|distribute)\s+by\b",
    re.IGNORECASE | re.DOTALL,
)
# 整段看起来像一条 SQL/Hive 语句（以动词开头）
_RAW_STMT_RE = re.compile(
    r"^\s*(select|with|insert|update|delete|create|alter|drop|truncate|explain)\b",
    re.IGNORECASE,
)
# 显式点名 SQL/关系库引擎（区别于业务数据词如「销售额」）
_SQL_ENGINE_RE = re.compile(r"(?<![a-z])sql(?![a-z])|sqlite|关系(?:数据)?库", re.IGNORECASE)
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

    # HBase/Hive/SQL 引擎优先于闲聊检查——"hi" 在 chitchat 里会误匹配 "hive"
    has_hbase = any(m in q_lower for m in _HBASE_MARKERS) or bool(_HBASE_OP_RE.search(q_lower))
    looks_like_hiveql = bool(_HIVEQL_DIALECT_RE.search(q))
    has_hive = any(m in q_lower for m in _HIVE_MARKERS) or looks_like_hiveql
    has_sql_engine = bool(_SQL_ENGINE_RE.search(q))
    has_sql_kw = any(m in q for m in _DATA_MARKERS) or bool(_FUZZY_SALES_RE.search(q))
    looks_like_stmt = bool(_RAW_STMT_RE.search(q))

    # scanner/scanning ≠ HBase scan：无显式 hbase 标记时硬返回 analysis，不交给 LLM
    if _SCANNER_FALSE_RE.search(q_lower) and not any(m in q_lower for m in _HBASE_MARKERS):
        return [{"agent": "analysis", "task": q}]

    # 粘贴的 Hive 方言语句（如 INSERT OVERWRITE ... PARTITION）→ 必须 hive，别当 SQLite
    if looks_like_hiveql and not has_hbase and not has_sql_engine:
        return [{
            "agent": "hive",
            "task": (
                "用户直接粘贴了 HiveQL/Hive 方言语句（不是自然语言问题）。"
                "请按 Hive 语义解释、指出与本地模拟表的差异，必要时改写成可执行的 Hive/Impala 查询。"
                f"\n\n语句：\n{q}"
            ),
        }]

    # 显式点名的引擎可组合：SQL + Hive / HBase + Hive / 三者都要
    engine_plan = []
    if has_sql_engine:
        engine_plan.append({
            "agent": "sql",
            "task": f"针对关系库/SQL 侧回答：{q}",
        })
    if has_hbase:
        engine_plan.append({
            "agent": "hbase",
            "task": f"针对 HBase 侧回答：{q}" if (has_hive or has_sql_engine) else q,
        })
    if has_hive:
        engine_plan.append({
            "agent": "hive",
            "task": f"针对 Hive 侧回答：{q}" if (has_hbase or has_sql_engine) else q,
        })
    if engine_plan:
        return engine_plan

    # 粘贴的普通 SQL 语句（无 Hive 方言）→ sql，按「解释/改写粘贴语句」处理
    if looks_like_stmt and not has_hbase:
        return [{
            "agent": "sql",
            "task": (
                "用户直接粘贴了 SQL 语句（不是自然语言问题）。"
                "请检查表名/语法是否适配本地 SQLite，解释问题并给出可执行改写。"
                f"\n\n语句：\n{q}"
            ),
        }]

    is_chitchat = (
        any(m in q_lower for m in _CHITCHAT_MARKERS) or bool(_CHITCHAT_WHO_RE.search(q))
    )
    if is_chitchat:
        if not has_sql_kw and not any(m in q for m in _STRATEGY_MARKERS):
            return []

    if _META_QUESTION_RE.search(q):
        return [{"agent": "analysis", "task": "用户询问对话历史，请根据上下文回答"}]

    # 指标口径查询：「GMV怎么算」「销售额包含退款吗」→ strategy（lookup_metric）
    # 但若明确在查数（「帮我查询…总金额是多少」），即使背景提到「口径/指标」也不走 strategy。
    # 反例：edge-007 超长复盘背景含「统计口径」「某个指标」，末尾才是查销售部 Q1 订单额。
    has_metric_alias = any(m in q_lower for m in _METRIC_ALIASES)
    has_metric_pattern = bool(_METRIC_LOOKUP_RE.search(q))
    is_concrete_data_ask = bool(_DATA_QUERY_ASK_RE.search(q))
    if has_metric_alias and has_metric_pattern and not is_concrete_data_ask:
        return [{"agent": "strategy", "task": q}]

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
