# single_agent/tools_bundle.py — single 模式挂载的全量 Tool 列表
#
# 与 multi_agent/agents.py 对应：multi 按角色拆分 tools，single 一次挂全量。
# tools 实现在 common/tools/，这里只做注册与 dispatch map。

from harness.tools.schema import (
    LIST_TABLES_TOOL, list_tables,
    DESCRIBE_TABLE_TOOL, describe_table,
    GET_SCHEMA_SUMMARY_TOOL, get_schema_summary,
)
from harness.tools.query import RUN_QUERY_TOOL, run_query
from harness.tools.analysis import (
    ANALYZE_RESULTS_TOOL, analyze_results,
    COMPARE_PERIODS_TOOL, compare_periods,
)
from harness.tools.chart import render_chart
from harness.tools.knowledge import (
    search_knowledge_base, save_to_memory, read_memory, search_memory,
)
from harness.tools.hive import search_hive_syntax
from harness.tools.hbase import run_hbase, generate_hbase_query
from harness.context.template_matcher import match_sql_template

TOOLS = [
    LIST_TABLES_TOOL,
    DESCRIBE_TABLE_TOOL,
    GET_SCHEMA_SUMMARY_TOOL,
    RUN_QUERY_TOOL,
    ANALYZE_RESULTS_TOOL,
    COMPARE_PERIODS_TOOL,
    render_chart.tool_schema,
    search_knowledge_base.tool_schema,
    save_to_memory.tool_schema,
    read_memory.tool_schema,
    search_memory.tool_schema,
    generate_hbase_query.tool_schema,
    search_hive_syntax.tool_schema,
    run_hbase.tool_schema,
    match_sql_template.tool_schema,
]

TOOL_HANDLERS = {
    "list_tables": list_tables,
    "describe_table": describe_table,
    "get_schema_summary": get_schema_summary,
    "run_query": run_query,
    "analyze_results": analyze_results,
    "compare_periods": compare_periods,
    "render_chart": render_chart,
    "search_knowledge_base": search_knowledge_base,
    "save_to_memory": save_to_memory,
    "read_memory": read_memory,
    "search_memory": search_memory,
    "generate_hbase_query": generate_hbase_query,
    "search_hive_syntax": search_hive_syntax,
    "run_hbase": run_hbase,
    "match_sql_template": match_sql_template,
}
