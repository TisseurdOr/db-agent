"""模板匹配器测试。

覆盖:
- 精确名称匹配（模板命中）
- 别名匹配（模板命中）
- 模糊匹配（关键词重叠命中）
- 无匹配（回退 LLM）
- 填槽功能（日期、实体、limit）
- 模板命中但填槽失败（回退 LLM）
- match_sql_template Tool 接口
"""

import os
import sys

import pytest

# 确保项目根在 path 里
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.context.template_matcher import (
    MetricTemplate,
    TemplateMatcher,
    _extract_dept,
    _extract_limit,
    _extract_product,
    _extract_status,
    _parse_date_range,
    init_metric_registry,
    match_sql_template,
)


@pytest.fixture
def matcher():
    """创建带种子数据的 TemplateMatcher。"""
    m = TemplateMatcher()
    m._templates = [
        MetricTemplate(
            metric_name="月销售额",
            sql_template="SELECT SUM(total) FROM orders WHERE created_at BETWEEN '{start_date}' AND '{end_date}'",
            aliases=["月度销售额", "月营收"],
            related_tables=["orders"],
            domain="sales",
            caveats="不含已取消订单",
        ),
        MetricTemplate(
            metric_name="部门销售额",
            sql_template="SELECT d.name, SUM(o.total) AS sales FROM orders o JOIN departments d ON o.dept_id = d.id WHERE o.created_at BETWEEN '{start_date}' AND '{end_date}' GROUP BY d.name ORDER BY sales DESC",
            aliases=["各部门销售", "销售排名"],
            related_tables=["orders", "departments"],
            domain="sales",
        ),
        MetricTemplate(
            metric_name="某部门员工",
            sql_template="SELECT e.name, e.position, e.salary FROM employees e JOIN departments d ON e.dept_id = d.id WHERE d.name = '{dept_name}'",
            aliases=["部门员工列表", "部门有哪些人"],
            related_tables=["employees", "departments"],
            domain="hr",
        ),
        MetricTemplate(
            metric_name="产品销售排名",
            sql_template="SELECT p.name, SUM(oi.quantity) AS sold FROM order_items oi JOIN products p ON oi.product_id = p.id JOIN orders o ON oi.order_id = o.id WHERE o.created_at BETWEEN '{start_date}' AND '{end_date}' GROUP BY p.name ORDER BY sold DESC LIMIT {limit}",
            aliases=["产品销量", "哪个产品卖得好"],
            related_tables=["products", "order_items", "orders"],
            domain="sales",
        ),
    ]
    m._loaded = True
    return m


# ── 日期解析测试 ──

def test_date_range_recent_days():
    start, end = _parse_date_range("最近7天的销售额")
    assert start and end
    assert start < end


def test_date_range_last_month():
    start, end = _parse_date_range("上个月的销售数据")
    assert start and end
    assert start < end


def test_date_range_today():
    start, end = _parse_date_range("今天的订单")
    assert start == end


# ── 实体提取测试 ──

def test_extract_dept():
    assert _extract_dept("销售部有哪些员工") == "销售部"
    assert _extract_dept("研发部的平均工资") == "研发部"
    assert _extract_dept("产品销量排名") is None


def test_extract_status():
    assert _extract_status("已付款的订单") == "paid"
    assert _extract_status("已取消的订单") == "cancelled"
    assert _extract_status("所有订单") is None


def test_extract_limit():
    assert _extract_limit("排名前5的产品") == 5
    assert _extract_limit("top 10 客户") == 10
    assert _extract_limit("最近订单") == 10  # default


def test_extract_product():
    assert _extract_product("企业版的销量") == "企业版"
    assert _extract_product("SaaS 客户") == "SaaS"


# ── 模板匹配测试 ──

def test_exact_name_match(matcher):
    """精确名称匹配：模板名直接出现在 query 里。"""
    result = matcher.match("查一下月销售额")
    assert result.matched
    assert "SUM(total)" in result.sql
    assert result.metric_name == "月销售额"


def test_alias_match(matcher):
    """别名匹配。"月营收" 是 "月销售额" 的别名。"""
    result = matcher.match("月营收多少")
    assert result.matched
    assert result.metric_name == "月销售额"


def test_keyword_overlap_match(matcher):
    """关键词+领域词重叠匹配。"""
    result = matcher.match("各部门销售排名")
    assert result.matched
    assert result.metric_name == "部门销售额"


def test_no_match(matcher):
    """无匹配：query 不与任何模板相似 → 回退 LLM。"""
    result = matcher.match("帮我写一段 Python 代码")
    assert not result.matched


def test_below_threshold(matcher):
    """低于阈值：模板存在但在 query 关键词重叠太少。"""
    result = matcher.match("xyz 完全无关的查询")
    assert not result.matched


# ── 填槽测试 ──

def test_fill_date_slots(matcher):
    """日期槽位填充：start_date 和 end_date 应被实际日期替换。"""
    result = matcher.match("最近 7 天的月销售额")
    assert result.matched
    assert "{start_date}" not in result.sql
    assert "{end_date}" not in result.sql
    assert "BETWEEN" in result.sql


def test_fill_entity_slots(matcher):
    """实体槽位填充：dept_name 应被提取替换。"""
    result = matcher.match("销售部的员工列表")
    assert result.matched
    assert result.metric_name == "某部门员工"
    assert "销售部" in result.sql
    assert "{dept_name}" not in result.sql


def test_fill_limit_slot(matcher):
    """数值槽位填充：limit 应被提取替换。"""
    result = matcher.match("产品销量排名前 5")
    assert result.matched
    assert result.metric_name == "产品销售排名"
    assert "LIMIT 5" in result.sql


def test_fill_fails_gracefully(matcher):
    """填槽失败：模板需要 dept_name 但 query 不包含部门名 → 回退 LLM。

    注意：'各部门总销售额' 不包含具体部门名，无法填 '{dept_name}' 槽位，
    但部门销售额模板匹配的应该是这个 query。实际上这个模板有两个槽位：
    start_date 和 end_date，不包含 dept_name。
    所以它不会失败。

    换一个需要 dept_name 的模板：某部门员工。
    """
    # "有哪些人" 不包含具体部门名 → 某部门员工模板无法填槽 → 回退
    result = matcher.match("有哪些人")
    assert not result.matched


# ── match_sql_template Tool 测试 ──

def test_tool_matched(matcher):
    """Tool 命中返回正确结构。"""
    # 注入模板到全局单例
    import harness.context.template_matcher as tm
    old = tm._matcher
    tm._matcher = matcher
    try:
        result = match_sql_template("月销售额")
        assert result["matched"] is True
        assert "sql" in result
        assert "SUM" in result["sql"]
        assert result["metric"] == "月销售额"
    finally:
        tm._matcher = old


def test_tool_not_matched(matcher):
    """Tool 未命中返回正确结构。"""
    import harness.context.template_matcher as tm
    old = tm._matcher
    tm._matcher = matcher
    try:
        result = match_sql_template("写一段代码")
        assert result["matched"] is False
        assert "模板未命中" in result["hint"]
    finally:
        tm._matcher = old


# ── MatchResult 测试 ──

def test_match_result_caveats(matcher):
    """匹配结果包含注意事项。"""
    result = matcher.match("月销售额")
    assert result.matched
    assert "不含已取消订单" in result.caveats


def test_match_result_from_template_flag(matcher):
    """匹配结果标记 from_template。"""
    result = matcher.match("月销售额")
    assert result.from_template is True


# ── 模板列表测试 ──

def test_list_templates(matcher):
    """列出所有模板。"""
    templates = matcher.list_templates()
    assert len(templates) == 4
    names = [t["name"] for t in templates]
    assert "月销售额" in names
    assert "部门销售额" in names


# ── 动态添加模板测试 ──

def test_add_template():
    """动态添加模板到内存后能被匹配到（不涉及 DB）。"""
    m = TemplateMatcher()
    m._loaded = True
    m._templates = []
    t = MetricTemplate(
        metric_name="测试指标",
        sql_template="SELECT 1",
        aliases=["测试"],
        domain="test",
    )
    m._templates.append(t)
    result = m.match("测试指标")
    assert result.matched
    assert result.sql == "SELECT 1"


# ── 集成测试：init_metric_registry ──

def test_init_metric_registry():
    """初始化后模板库非空。"""
    import tempfile
    from pathlib import Path as P

    import harness.context.template_matcher as tm

    old_db = tm.METRIC_DB
    with tempfile.TemporaryDirectory() as tmpdir:
        tm.METRIC_DB = P(tmpdir) / "metric_registry.db"
        try:
            init_metric_registry()
            m = TemplateMatcher()
            templates = m.list_templates()
            assert len(templates) >= 5  # 至少 5 个种子模板
            assert any(t["name"] == "月销售额" for t in templates)
            assert any(t["name"] == "部门销售额" for t in templates)
            assert any(t["name"] == "数据概览" for t in templates)
        finally:
            tm.METRIC_DB = old_db
