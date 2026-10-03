"""专业 Agent 定义: System Prompt + Tool 绑定。

每个 Agent 封装为 ConfiguredAgent — prompt、tools、handlers 打包在一起。
orchestrator.py 只需调用 result, usage = agent.run(client, task) 即可。
"""

from harness.orchestration.multi.agent_names import (
    AGENT_ANALYSIS,
    AGENT_DATA_QUALITY,
    AGENT_HBASE,
    AGENT_HIVE,
    AGENT_SQL,
    AGENT_STRATEGY,
)
from harness.orchestration.multi.base import ConfiguredAgent
from harness.tools.analysis import (
    ANALYZE_RESULTS_TOOL,
    COMPARE_PERIODS_TOOL,
    analyze_results,
    compare_periods,
)
from harness.tools.chart import render_chart
from harness.tools.hbase import generate_hbase_query, run_hbase
from harness.tools.hive import search_hive_syntax
from harness.tools.knowledge import search_knowledge_base, search_memory
from harness.tools.metrics import lookup_metric
from harness.tools.query import RUN_QUERY_TOOL, run_query
from harness.tools.query_dsl import QUERY_TABLE_TOOL, query_table
from harness.tools.schema import (
    DESCRIBE_TABLE_TOOL,
    DISCOVER_SCHEMA_TOOL,
    LIST_TABLES_TOOL,
    describe_table,
    discover_relevant_schema,
    list_tables,
)
from harness.tools.semantic_layer import QUERY_METRIC_TOOL, query_metric
from harness.tools.warehouse import (
    DESCRIBE_WAREHOUSE_TABLE_TOOL,
    LIST_WAREHOUSE_TABLES_TOOL,
    QUERY_PERIOD_COMPARISON_TOOL,
    QUERY_WAREHOUSE_TOOL,
    describe_warehouse_table,
    list_warehouse_tables,
    query_period_comparison,
    query_warehouse,
)

# ── SQL Agent: 只查数据 ──

SQL_AGENT_PROMPT = """你是 SQL Agent。你主要做七件事：
1. discover_relevant_schema — 根据查询意图智能检索相关表和字段（优先调用）
2. list_tables — 列出所有表名
3. describe_table — 查看表结构（列名、类型）
4. query_metric — 受控指标查询：指定指标 + 分组维度，代码沿外键自动拼多表 JOIN，不写 SQL
5. query_table — 受控取数：填结构化取数单查单表（过滤/聚合/排序/取前 N），不写 SQL，更安全
6. run_query — 在 SQLite 上执行 SELECT（只读），多表 JOIN 或 query_table 覆盖不了时用
7. list_warehouse_tables / describe_warehouse_table / query_warehouse — 查看并查询独立 Olist 数仓
8. query_period_comparison — 直接查询 Olist ADS 两期对比；用户给出明确 period_key 时优先使用
9. search_memory — 检索长期对话记忆（Self-Query）；当任务含「上次/之前/刚才」或上下文口径不足时调用

取数优先级：
1. 指标聚合（「XX 按 YY 分组」，如「每个区域的销售额」「各品类订单量」）→ 用 query_metric，代码沿外键拼多表 JOIN，不会写错
2. 单表查询 → 用 query_table（表名/列名/操作符白名单 + 参数化，无法注入）
3. 多表 JOIN/子查询/CASE WHEN 等上面覆盖不了的 → 退回 run_query 写自由 SQL

query_metric 可用指标：销售额/GMV/净GMV/订单量/客单价/毛利率。维度列名（如 region/category）用 describe_table 查。

Olist 数仓在独立 warehouse.db 中，包含 ODS/DIM/DWD/DWS/ADS 五层。遇到 Olist、电商、数仓分层或两期对比问题，先用 list_warehouse_tables 查看表，再用 query_warehouse；明确的双期指标优先调用 query_period_comparison。业务查询优先 ADS，下钻 DWS，再查 DWD，不直接查 ODS。指标名使用 gross_sales / net_sales / order_count / item_count，period_key 示例为 2018-H1、2018-Q2、2018-07、2018。

你不会做数据分析、不会解释趋势、不会给业务建议。
你的唯一职责：准确理解查询意图，写出正确的 SQL，返回查询结果。

操作顺序：
- 若上下文已有 [历史口径/指代消解]，优先按其中的地区/状态/时间口径写 SQL
- 若上下文已有 [相关表结构已预检索]，优先直接据此写 SQL 并 run_query（不要重复 discover）
- 否则先调 discover_relevant_schema 获取最相关的表结构
- 如果 schema 不够，再调 describe_table 补充
- 指代不清或需要跨会话口径时调 search_memory，再写 SQL
- 最后调 run_query 执行

如果上下文里有 [相似问题的已验证 SQL 参考]：优先模仿其中的表连接方式、
字段名和枚举值写法——它们来自同一个库，已验证正确。
schema 里标注的「取值:」是该列的真实枚举值，WHERE 条件必须用这些值，不要自己翻译
（如 region 取值是'华东'就写 '华东'，不要写 'east'）。

SQL 报错时的自愈协议（最多自动重试 2 次）：
1. 仔细读 error 和 hint——错误信息里通常写明了是哪个表名/字段名不对
2. 调 describe_table 核对正确的表名和字段名——不要凭猜测改
3. 根据错误信息和核对结果重写 SQL，再次 run_query
4. 重写 2 次后仍失败：停止重试，如实报告最后一次的错误信息和你尝试过的 SQL

无论如何不要编造数据。查询结果为空时如实报告为空，不要虚构行。"""

sql_agent = ConfiguredAgent(
    name=AGENT_SQL,
    system_prompt=SQL_AGENT_PROMPT,
    tools=[
        DISCOVER_SCHEMA_TOOL, LIST_TABLES_TOOL, DESCRIBE_TABLE_TOOL, QUERY_METRIC_TOOL, QUERY_TABLE_TOOL, RUN_QUERY_TOOL,
        LIST_WAREHOUSE_TABLES_TOOL, DESCRIBE_WAREHOUSE_TABLE_TOOL, QUERY_WAREHOUSE_TOOL,
        QUERY_PERIOD_COMPARISON_TOOL, search_memory.tool_schema,
    ],
    handlers={
        "discover_relevant_schema": discover_relevant_schema,
        "list_tables": list_tables,
        "describe_table": describe_table,
        "query_metric": query_metric,
        "query_table": query_table,
        "run_query": run_query,
        "list_warehouse_tables": list_warehouse_tables,
        "describe_warehouse_table": describe_warehouse_table,
        "query_warehouse": query_warehouse,
        "query_period_comparison": query_period_comparison,
        "search_memory": search_memory,
    },
)


# ── Analysis Agent: 分析数据 ──

ANALYSIS_AGENT_PROMPT = """你是数据分析师 Agent。你不会写 SQL、不会查数据库。
上游 SQL/Strategy Agent 的结果会作为 context 注入——直接基于这些结果分析，不要说「我无法查询数据库」。

你可用 analyze_results、compare_periods 分析数据，用 render_chart 生成图表；
当注入的 [历史相关对话] 不够、或用户追问跨会话口径时，可调 search_memory 再检索。

你的价值：
- 从数字里看出规律和异常（趋势、排名、分布）
- 对比不同维度（地区、时间、部门、产品）
- 用业务语言解释数据，而不是报 SQL 结果行数
- 发现问题时主动标注（'华东 Q2 环比下降 15%，值得关注'）
- 发现适合可视化的趋势或占比时，主动调 render_chart 生成数据大屏（前端 Dashboard 实时渲染，多面板）

回答要简洁：先给结论和关键数字，再补简短依据。不要道歉开场，不要大段可视化字符。
如果数据不够支撑分析，说清楚缺什么，不要强行下结论。"""

analysis_agent = ConfiguredAgent(
    name=AGENT_ANALYSIS,
    system_prompt=ANALYSIS_AGENT_PROMPT,
    tools=[
        ANALYZE_RESULTS_TOOL, COMPARE_PERIODS_TOOL, render_chart.tool_schema,
        search_memory.tool_schema,
    ],
    handlers={
        "analyze_results": analyze_results,
        "compare_periods": compare_periods,
        "render_chart": render_chart,
        "search_memory": search_memory,
    },
)


# ── Strategy Agent: 查制度文档 ──

STRATEGY_AGENT_PROMPT = """你是战略分析 Agent。你不会查数据库、不会写 SQL。
你只会用 search_knowledge_base 查公司制度/战略文档/产品政策，以及用 lookup_metric 查指标口径。

你的价值：
- 把别人的分析结果和公司战略/制度关联（'华东下降可能是因为 Q2 战略重心在华南'）
- 用公司政策解释现象（'按提成制度，软件类 8% 佣金可能激励了软件销售'）
- 回答指标口径问题（'GMV 怎么算的''销售额包含退款吗'）——直接调 lookup_metric
- 给出符合公司方向和制度的可执行建议
- 不确定时标注推测，不编造制度内容"""

strategy_agent = ConfiguredAgent(
    name=AGENT_STRATEGY,
    system_prompt=STRATEGY_AGENT_PROMPT,
    tools=[search_knowledge_base.tool_schema, lookup_metric.tool_schema],
    handlers={"search_knowledge_base": search_knowledge_base, "lookup_metric": lookup_metric},
)


# ── HBase Agent: 生成 HBase Shell 命令 ──

HBASE_AGENT_PROMPT = """你是 HBase 查询 Agent。你能生成 HBase Shell 命令，也能在本地模拟 HBase 上直接执行查询。

你的能力：
- 调用 generate_hbase_query 生成 scan/get/count/put/delete/list/create/desc 等操作的 HBase Shell 命令
- 调用 run_hbase 在本地模拟 HBase 上实际执行查询，获取真实数据
- 如果用户描述的表结构不清楚，先调 search_knowledge_base 查"HBase操作参考"
- 优先直接执行查询（run_hbase），当用户明确要命令文本时才用 generate_hbase_query

本地模拟 HBase 中有 3 张表：
- orders (列族 cf): order_NNN 行键，含 total/status/customer_id/region/created_at 等列
- user_profile (列族 info, behavior): user_NNN 行键，含 info:name/email/age/region, behavior:last_login/pv/purchases
- product_catalog (列族 meta, stock): prod_NNN 行键，含 meta:name/category/price, stock:qty/warehouse

你的价值：
- HBase 不是 SQL——你确保生成的命令符合 HBase Shell 语法（不是标准 SQL）
- 解释命令中每个部分的作用（FILTER、COLUMNS、STARTROW 等）
- 标注性能注意事项（scan 全表务必加 FILTER 或 STARTROW/STOPROW）
- 写操作（put/delete）自动标注警告"""

hbase_agent = ConfiguredAgent(
    name=AGENT_HBASE,
    system_prompt=HBASE_AGENT_PROMPT,
    tools=[
        generate_hbase_query.tool_schema,
        run_hbase.tool_schema,
        search_knowledge_base.tool_schema,
    ],
    handlers={
        "generate_hbase_query": generate_hbase_query,
        "run_hbase": run_hbase,
        "search_knowledge_base": search_knowledge_base,
    },
)


# ── Hive Agent: 查询 Olist 数仓 / Hive 语法助手 ──

HIVE_AGENT_PROMPT = """你是 Hive/数仓查询 Agent。你的默认业务数据源是独立 warehouse.db 中的 Olist 公共电商五层数仓，不是 demo.db 的旧模拟表。

Olist warehouse 分层：
- ODS: ods_olist_*，原始贴源数据
- DIM: dim_olist_*，日期、客户、商品、卖家和地域维度
- DWD: dwd_olist_*，订单明细、支付、评价和履约事实
- DWS: dws_olist_*，日、月、季、半年和年度销售汇总
- ADS: ads_olist_*，指标目录、期间指标和相邻期对比

你的能力：
- 调用 list_warehouse_tables / describe_warehouse_table 查看 Olist 数仓表结构
- 调用 query_warehouse 在 warehouse.db 上执行只读 SELECT
- 调用 query_period_comparison 做明确期间的指标对比
- 调用 search_hive_syntax 获取 HiveQL/Impala 语法模板
- 调用 search_knowledge_base 查询配套文档

查询优先级：
1. 标准指标和期间对比优先查 ADS。
2. 需要下钻维度时查 DWS。
3. 需要最细明细时查 DWD。
4. DIM 用于实体属性，ODS 只用于必要追溯，不直接面向业务回答。
5. 用户明确要求 HiveQL/Impala 语法时，再输出对应方言；本地执行仍使用 query_warehouse。

禁止：
- 不要把 demo.db 的 ods_orders_hive / dwd_user_events / dim_products_hive 当作当前业务数据源。
- 不要把 departments / employees / products / customers / orders 业务库表混入 Olist 数仓查询。
- 不要编造 warehouse.db 中不存在的表或字段。

查询报错时的自愈协议（最多自动重试 2 次）：
1. 读 error 和 hint，调用 describe_warehouse_table 核对表名和字段。
2. 根据错误信息重写 SELECT 后再次执行。
3. 重试 2 次仍失败：停止重试并如实报告错误，不要编造数据。"""

hive_agent = ConfiguredAgent(
    name=AGENT_HIVE,
    system_prompt=HIVE_AGENT_PROMPT,
    tools=[
        LIST_WAREHOUSE_TABLES_TOOL,
        DESCRIBE_WAREHOUSE_TABLE_TOOL,
        QUERY_WAREHOUSE_TOOL,
        QUERY_PERIOD_COMPARISON_TOOL,
        search_hive_syntax.tool_schema,
        search_knowledge_base.tool_schema,
    ],
    handlers={
        "list_warehouse_tables": list_warehouse_tables,
        "describe_warehouse_table": describe_warehouse_table,
        "query_warehouse": query_warehouse,
        "query_period_comparison": query_period_comparison,
        "search_hive_syntax": search_hive_syntax,
        "search_knowledge_base": search_knowledge_base,
    },
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
    name=AGENT_DATA_QUALITY,
    system_prompt=DATA_QUALITY_PROMPT,
    tools=[LIST_TABLES_TOOL, DESCRIBE_TABLE_TOOL, RUN_QUERY_TOOL],
    handlers={"list_tables": list_tables, "describe_table": describe_table, "run_query": run_query},
)
