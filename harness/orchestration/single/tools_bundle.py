# single_agent/tools_bundle.py — single 模式挂载的全量 Tool 列表
#
# 与 multi_agent/agents.py 对应：multi 按角色拆分 tools，single 一次挂全量。
# tools 实现在 common/tools/，这里只做注册与 dispatch map。

from harness.context.template_matcher import match_sql_template
from harness.tools.analysis import (
    ANALYZE_RESULTS_TOOL,
    COMPARE_PERIODS_TOOL,
    analyze_results,
    compare_periods,
)
from harness.tools.chart import render_chart
from harness.tools.hbase import generate_hbase_query, run_hbase
from harness.tools.hive import search_hive_syntax
from harness.tools.knowledge import (
    read_memory,
    save_to_memory,
    search_knowledge_base,
    search_memory,
)
from harness.tools.query import RUN_QUERY_TOOL, run_query
from harness.tools.schema import (
    DESCRIBE_TABLE_TOOL,
    DISCOVER_SCHEMA_TOOL,
    GET_SCHEMA_SUMMARY_TOOL,
    LIST_TABLES_TOOL,
    describe_table,
    discover_relevant_schema,
    get_schema_summary,
    list_tables,
)
from harness.tools.script_gen import (
    generate_insert_script,
    generate_python_script,
    generate_sql_script,
)
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
from harness.tools.write import run_insert

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
    generate_sql_script.tool_schema,
    generate_python_script.tool_schema,
    generate_insert_script.tool_schema,
    run_insert.tool_schema,
    DISCOVER_SCHEMA_TOOL,
    LIST_WAREHOUSE_TABLES_TOOL,
    DESCRIBE_WAREHOUSE_TABLE_TOOL,
    QUERY_WAREHOUSE_TOOL,
    QUERY_PERIOD_COMPARISON_TOOL,
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
    "generate_sql_script": generate_sql_script,
    "generate_python_script": generate_python_script,
    "generate_insert_script": generate_insert_script,
    "run_insert": run_insert,
    "discover_relevant_schema": discover_relevant_schema,
    "list_warehouse_tables": list_warehouse_tables,
    "describe_warehouse_table": describe_warehouse_table,
    "query_warehouse": query_warehouse,
    "query_period_comparison": query_period_comparison,
}
