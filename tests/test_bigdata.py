"""测试 bigdata 工具: generate_hbase_query + search_hive_syntax + Router 路由。"""

from harness.tools.hbase import generate_hbase_query
from harness.tools.hive import search_hive_syntax
from harness.orchestration.multi.router import route_override


# ═══════════════════════════════════════════════════════════════════════════════
# generate_hbase_query
# ═══════════════════════════════════════════════════════════════════════════════

def test_hbase_scan():
    """HBase scan 基础命令。"""
    result = generate_hbase_query(
        operation="scan", table_name="orders",
        filter_description="行键以 2025 开头", limit=10,
    )
    assert "command" in result
    assert "scan" in result["command"]
    assert "orders" in result["command"]
    assert result["operation"] == "scan"
    assert "filter_explanation" in result or "warning" in result


def test_hbase_get():
    """HBase get 按行键精确读。"""
    result = generate_hbase_query(
        operation="get", table_name="orders", row_key="row_001",
    )
    assert "get" in result["command"]
    assert "row_001" in result["command"]
    assert result["operation"] == "get"


def test_hbase_count():
    """HBase count 统计行数。"""
    result = generate_hbase_query(operation="count", table_name="orders")
    assert "count" in result["command"]
    assert result["operation"] == "count"


def test_hbase_invalid_operation():
    """不支持的操作返回 error + 可用操作列表。"""
    result = generate_hbase_query(operation="invalid_op", table_name="orders")
    assert result.get("error") is True
    assert "suggestion" in result
    assert "scan" in result["suggestion"]


def test_hbase_empty_operation():
    """空操作返回 error。"""
    result = generate_hbase_query(operation="", table_name="orders")
    assert result.get("error") is True


def test_hbase_filter_matching():
    """filter_description 模糊匹配 filter 类型。"""
    result = generate_hbase_query(
        operation="scan", table_name="orders",
        filter_description="status 列值等于 completed",
    )
    assert "filter_explanation" in result
    assert "SingleColumnValueFilter" in result["filter_explanation"]


def test_hbase_columns_validation():
    """columns 参数正常处理。"""
    result = generate_hbase_query(
        operation="get", table_name="orders",
        row_key="r1", columns=["cf:total", "cf:status"],
    )
    assert "get" in result["command"]


# ═══════════════════════════════════════════════════════════════════════════════
# search_hive_syntax
# ═══════════════════════════════════════════════════════════════════════════════

def test_hive_select():
    """Hive select 语法检索。"""
    result = search_hive_syntax(query_type="select", dialect="hive")
    assert result["count"] >= 1
    assert "SELECT" in result["results"][0]["syntax"]


def test_impala_create_table():
    """Impala create_table 语法检索。"""
    result = search_hive_syntax(query_type="create_table", dialect="impala")
    assert result["count"] >= 1
    assert result["dialect"] == "impala"
    assert "CREATE" in result["results"][0]["syntax"]


def test_hive_invalid_dialect():
    """不支持的 dialect 返回 error。"""
    result = search_hive_syntax(query_type="select", dialect="mysql")
    assert result.get("error") is True


def test_hive_unknown_query_type():
    """不支持的 query_type 返回 hint。"""
    result = search_hive_syntax(query_type="unknown_syntax", dialect="hive")
    assert result["count"] == 0
    assert "hint" in result


def test_hive_compute_stats_diff():
    """Hive 和 Impala 的 compute_stats 语法不同。"""
    hive_r = search_hive_syntax(query_type="compute_stats", dialect="hive")
    impala_r = search_hive_syntax(query_type="compute_stats", dialect="impala")
    assert "ANALYZE" in hive_r["results"][0]["syntax"]
    assert "COMPUTE STATS" in impala_r["results"][0]["syntax"]


# ═══════════════════════════════════════════════════════════════════════════════
# Router 路由
# ═══════════════════════════════════════════════════════════════════════════════

def test_router_hbase_keyword():
    """含 HBase 关键词 → 路由到 hbase。"""
    plan = route_override("帮我写个HBase scan命令扫描orders表")
    assert plan is not None
    agents = [s["agent"] for s in plan]
    assert "hbase" in agents
    assert "hive" not in agents


def test_router_hive_keyword():
    """含 Hive 关键词 → 路由到 hive。"""
    plan = route_override("生成一个Hive查询统计销售额")
    assert plan is not None
    assert plan[0]["agent"] == "hive"


def test_router_impala_keyword():
    """含 Impala 关键词 → 路由到 hive（Impala 属于 Hive 方言）。"""
    plan = route_override("Impala怎么查分区表")
    assert plan is not None
    assert plan[0]["agent"] == "hive"


def test_router_hue_keyword():
    """含 Hue 关键词 → 路由到 hive。"""
    plan = route_override("hue上怎么写查询")
    assert plan is not None
    assert plan[0]["agent"] == "hive"


def test_router_sql_not_bigdata():
    """纯 SQL 查询不应误路由到 hbase 或 hive。"""
    plan = route_override("销售额最高的部门是哪个")
    if plan is not None:
        agents = [s["agent"] for s in plan]
        assert "hbase" not in agents
        assert "hive" not in agents


def test_router_both_hbase_and_hive():
    """同时提 HBase 和 Hive → 两个都路由。"""
    plan = route_override("hbase和hive的区别是什么")
    assert plan is not None
    agents = [s["agent"] for s in plan]
    assert "hbase" in agents
    assert "hive" in agents


def test_router_both_sql_and_hive():
    """同时提 SQL 和 Hive → 两个都路由，不能只派 hive。"""
    plan = route_override("SQL和hive有什么表")
    assert plan is not None
    agents = [s["agent"] for s in plan]
    assert "sql" in agents
    assert "hive" in agents


def test_router_pasted_hiveql_insert_overwrite():
    """CLI 误粘贴 INSERT OVERWRITE ... PARTITION → 必须 hive，不能当 SQLite sql。"""
    q = (
        "INSERT OVERWRITE TABLE orders PARTITION (dt='2025-01-01')\n"
        "SELECT ... FROM source_table;"
    )
    plan = route_override(q)
    assert plan is not None
    agents = [s["agent"] for s in plan]
    assert agents == ["hive"]


def test_router_pasted_plain_select():
    """粘贴普通 SELECT → sql（解释/改写），不是 hive。"""
    plan = route_override("SELECT * FROM orders WHERE status = 'completed';")
    assert plan is not None
    agents = [s["agent"] for s in plan]
    assert agents == ["sql"]


def test_list_hive_tables_only_hive_sim():
    """Hive list_tables 只返回模拟数仓表，不含业务表。"""
    import os
    from harness.tools.schema import list_hive_tables, HIVE_SIM_TABLES

    os.environ["AGENT_USER"] = "analyst"
    result = list_hive_tables()
    assert "tables" in result
    assert set(result["tables"]) == set(HIVE_SIM_TABLES)
    assert "orders" not in result["tables"]
    assert "departments" not in result["tables"]
    assert result.get("catalog") == "hive_sim"


def test_router_sql_engine_alone():
    """只点名 SQL 引擎（无 hive/hbase）→ 路由到 sql。"""
    plan = route_override("SQL里有哪些表")
    assert plan is not None
    agents = [s["agent"] for s in plan]
    assert agents == ["sql"]


def test_router_scan_verb_without_hbase_keyword():
    """"scan" 动词（不含 hbase 关键词）→ 路由到 hbase，不路由到 sql。"""
    plan = route_override("scan orders 表，限制 10 行")
    assert plan is not None
    agents = [s["agent"] for s in plan]
    assert "hbase" in agents
    assert "sql" not in agents


def test_router_scan_not_false_match():
    """"scanner"、"scanning" 不应误匹配 scan 词边界。"""
    plan = route_override("用scanner扫描文档")
    # 硬规则直接返回 analysis，禁止交给 LLM 误加 hbase/hive
    assert plan is not None
    agents = [s["agent"] for s in plan]
    assert "hbase" not in agents
    assert "hive" not in agents
    assert "analysis" in agents


def test_router_chitchat_who_are_you_now():
    """「你现在是谁」应识别为闲聊（空 plan）。"""
    plan = route_override("你现在是谁")
    assert plan == []


def test_router_fuzzy_sales_typo():
    """拼音混输入「查询销shou 额」应硬路由到 sql。"""
    plan = route_override("查询销shou 额")
    assert plan is not None
    agents = [s["agent"] for s in plan]
    assert "sql" in agents


def test_router_context_inherit_hbase():
    """上一轮是 hbase，本轮无关键词 → 继承 hbase 上下文。"""
    plan = route_override("查一下 user_profile 表", prev_agents=["hbase"])
    assert plan is not None
    agents = [s["agent"] for s in plan]
    assert "hbase" in agents


def test_router_context_inherit_hive():
    """上一轮是 hive，本轮无关键词 → 继承 hive 上下文。"""
    plan = route_override("查一下华东地区的数据", prev_agents=["hive"])
    assert plan is not None
    agents = [s["agent"] for s in plan]
    assert "hive" in agents


def test_router_context_no_inherit_with_sql_keyword():
    """本轮含 sql 数据关键词 → 不继承，走 LLM。"""
    plan = route_override("销售额最高的部门是哪个", prev_agents=["hbase"])
    # "销售额" 命中 _DATA_MARKERS → 不应继承 hbase
    if plan is not None:
        agents = [s["agent"] for s in plan]
        assert "hbase" not in agents


def test_router_context_no_inherit_mixed_prev():
    """上一轮同时有 hbase+hive → 无法确定，不继承。"""
    plan = route_override("帮我详细解释一下", prev_agents=["hbase", "hive"])
    # 两个 context 冲突 → 不继承
    if plan is not None:
        agents = [s["agent"] for s in plan]
        assert not ("hbase" in agents and "hive" not in agents)  # 不会单独继承一个

def test_long_briefing_with_口径_still_routes_sql():
    """edge-007 超长复盘含「口径/指标」，末尾查数 → sql，不误派 strategy。"""
    from tests.eval_cases import _EDGE_007_LONG_QUERY
    plan = route_override(_EDGE_007_LONG_QUERY)
    assert plan is not None
    assert any(s["agent"] == "sql" for s in plan)
    assert not any(s["agent"] == "strategy" for s in plan)


def test_metric_gmv_how_calculated_routes_strategy():
    plan = route_override("GMV怎么算")
    assert plan is not None
    assert any(s["agent"] == "strategy" for s in plan)


def test_metric_sales_含退款_routes_strategy():
    plan = route_override("销售额包含退款吗")
    assert plan is not None
    assert any(s["agent"] == "strategy" for s in plan)

