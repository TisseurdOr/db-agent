"""专业 Agent 定义: System Prompt + Tool 绑定。

每个 Agent 封装为 ConfiguredAgent — prompt、tools、handlers 打包在一起。
orchestrator.py 只需调用 result, usage = agent.run(client, task) 即可。
"""

import re

from tools.schema import (
    LIST_TABLES_TOOL, list_tables,
    DESCRIBE_TABLE_TOOL, describe_table,
)
from tools.query import RUN_QUERY_TOOL, run_query
from tools.analysis import (
    ANALYZE_RESULTS_TOOL, analyze_results,
    COMPARE_PERIODS_TOOL, compare_periods,
)
from tools.chart import render_chart
from tools.knowledge import search_knowledge_base
from multi_agent.base import ConfiguredAgent


# ── Router Agent: 意图分类 + 任务分派 ──
# Router 没有 Tool，纯推理——分析 query 后输出 JSON 执行计划。
# 和 SQL/Strategy/Analysis 不同，它只被 orchestrator 调一次（不是 agent loop）。

ROUTER_PROMPT = """你是路由 Agent。分析用户 query 并输出执行计划的 JSON。

你支持的专业 Agent:
- sql: 查数据库（订单、员工、部门、产品、客户等结构化数据）
- strategy: 查公司制度/政策文档（提成、年假、考勤、定价政策、公司战略等）
- analysis: 综合分析与建议，或回答对话历史相关问题（不会自己查库）

【优先级从高到低，必须严格遵守】
1. 闲聊/能力介绍（"你好"、"你能做什么"、"你是谁"）→ plan 必须为 []，禁止 sql/strategy
2. 对话历史/元问题（"刚才问了什么"、"上一个问题是"、"之前查了什么"）→ 只用 analysis，禁止 sql/strategy
   task 写: "用户询问对话历史，请根据上下文回答"
3. 制度/政策（"提成比例"、"年假多少天"、"考勤规则"）→ 只用 strategy，禁止 sql
4. 纯数据查询（"销售额多少"、"有多少员工"）→ 只用 sql
5. 需要数据+建议（"分析趋势并给建议"）→ sql + analysis；若还要对照制度再加 strategy
6. 对比/变化/环比/同比（"对比本月和上月"、"订单量变化"）→ 必须 sql + analysis：
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
    "你好", "您好", "hi", "hello", "你是谁", "介绍下", "介绍一下",
    "自我介绍", "在吗", "谢谢", "再见", "你能做什么", "你会什么",
)
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
    "趋势", "对比", "分析", "统计", "表", "数据库",
)
_COMPARE_MARKERS = ("对比", "环比", "同比", "变化", "增减")
_COMPARE_DATA_MARKERS = ("订单", "销售", "员工", "数据", "部门", "产品")


def route_override(query: str) -> list[dict] | None:
    """明确意图时返回硬编码 plan；否则返回 None，交给 LLM。

    用来兜住 route-002/004/007 这类「模型爱乱加 sql」的 case。
    """
    q = (query or "").strip()
    if not q:
        return []

    q_lower = q.lower()
    if any(m in q_lower for m in _CHITCHAT_MARKERS):
        if not any(m in q for m in _DATA_MARKERS) and not any(m in q for m in _STRATEGY_MARKERS):
            return []

    if _META_QUESTION_RE.search(q):
        return [{"agent": "analysis", "task": "用户询问对话历史，请根据上下文回答"}]

    has_strategy = any(m in q for m in _STRATEGY_MARKERS)
    has_data = any(m in q for m in _DATA_MARKERS)
    if has_strategy and not has_data:
        return [{"agent": "strategy", "task": q}]

    if any(m in q for m in _COMPARE_MARKERS) and any(m in q for m in _COMPARE_DATA_MARKERS):
        return [
            {"agent": "sql", "task": q},
            {"agent": "analysis", "task": f"对比分析：{q}"},
        ]

    return None


# ── SQL Agent: 只查数据 ──

SQL_AGENT_PROMPT = """你是 SQL Agent。你只能做三件事：
1. list_tables — 列出所有表名
2. describe_table — 查看表结构（列名、类型）
3. run_query — 在 SQLite 上执行 SELECT（只读）

你不会做数据分析、不会解释趋势、不会给业务建议。
你的唯一职责：准确理解查询意图，写出正确的 SQL，返回查询结果。
如果查询结果为空或 SQL 报错，如实报告，不要编造数据。"""

sql_agent = ConfiguredAgent(
    name="sql",
    system_prompt=SQL_AGENT_PROMPT,
    tools=[LIST_TABLES_TOOL, DESCRIBE_TABLE_TOOL, RUN_QUERY_TOOL],
    handlers={"list_tables": list_tables, "describe_table": describe_table, "run_query": run_query},
)


# ── Analysis Agent: 分析数据 ──

ANALYSIS_AGENT_PROMPT = """你是数据分析师 Agent。你不会写 SQL、不会查数据库。
上游 SQL/Strategy Agent 的结果会作为 context 注入——直接基于这些结果分析，不要说「我无法查询数据库」。

你只会用 analyze_results、compare_periods 分析数据，以及用 render_chart 生成图表。

你的价值：
- 从数字里看出规律和异常（趋势、排名、分布）
- 对比不同维度（地区、时间、部门、产品）
- 用业务语言解释数据，而不是报 SQL 结果行数
- 发现问题时主动标注（'华东 Q2 环比下降 15%，值得关注'）
- 发现适合可视化的趋势或占比时，主动调 render_chart 生成图表（折线看趋势、饼图看占比、柱状图看对比）

回答要简洁：先给结论和关键数字，再补简短依据。不要道歉开场，不要大段可视化字符。
如果数据不够支撑分析，说清楚缺什么，不要强行下结论。"""

analysis_agent = ConfiguredAgent(
    name="analysis",
    system_prompt=ANALYSIS_AGENT_PROMPT,
    tools=[ANALYZE_RESULTS_TOOL, COMPARE_PERIODS_TOOL, render_chart.tool_schema],
    handlers={"analyze_results": analyze_results, "compare_periods": compare_periods, "render_chart": render_chart},
)


# ── Strategy Agent: 查制度文档 ──

STRATEGY_AGENT_PROMPT = """你是战略分析 Agent。你不会查数据库、不会写 SQL。
你只会用 search_knowledge_base 查公司制度、战略文档、产品政策。

你的价值：
- 把别人的分析结果和公司战略/制度关联（'华东下降可能是因为 Q2 战略重心在华南'）
- 用公司政策解释现象（'按提成制度，软件类 8% 佣金可能激励了软件销售'）
- 给出符合公司方向和制度的可执行建议
- 不确定时标注推测，不编造制度内容"""

strategy_agent = ConfiguredAgent(
    name="strategy",
    system_prompt=STRATEGY_AGENT_PROMPT,
    tools=[search_knowledge_base.tool_schema],
    handlers={"search_knowledge_base": search_knowledge_base},
)


# ── DataQuality Agent: 首次查询时扫一遍数据质量 ──
# 只读检查：NULL 比例、日期连续性、数值异常值。
# 不算清洗——不修改数据，只报告事实。

DATA_QUALITY_PROMPT = """你是数据质量 Agent。你不会修改数据、不会分析业务趋势。

你能做的事：
1. list_tables / describe_table — 了解表结构
2. run_query — 执行 SELECT 做质量检查

你需要检查的内容（按优先级）：
- 日期连续性：orders.created_at 是否有明显缺失的日期段
- NULL 比例：关键字段（total、customer_id、dept_id）的 NULL 占比
- 异常值：金额远超同表均值的记录（如 total > 均值 × 5）
- 状态分布：cancelled/pending/completed 各占多少

输出格式：
1. 数据概览（总行数、时间范围、表行数）
2. 发现的质量问题（按严重程度排列）
3. 对后续分析的建议（比如"华东分析时注意6月数据缺失"）

规则：
- 只报告事实，不推荐业务决策
- 不确定时标注"推测"
- 检查不超过 5 条 SQL，避免过度扫描"""

data_quality_agent = ConfiguredAgent(
    name="data_quality",
    system_prompt=DATA_QUALITY_PROMPT,
    tools=[LIST_TABLES_TOOL, DESCRIBE_TABLE_TOOL, RUN_QUERY_TOOL],
    handlers={"list_tables": list_tables, "describe_table": describe_table, "run_query": run_query},
)
