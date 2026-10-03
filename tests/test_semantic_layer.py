"""语义层（指标编译 + 外键 join 图）的最小自检。"""

import pytest

from harness.tools.semantic_layer import compile_metric_sql, get_join_graph, query_metric


def test_join_graph_has_orders_fk():
    edges = get_join_graph()
    assert ("orders", "customer_id", "customers", "id") in edges
    assert ("orders", "product_id", "products", "id") in edges


def test_compile_metric_joins_dimension_table():
    sql, params = compile_metric_sql("销售额", ["region"])
    # region 在 customers 表，应沿 orders.customer_id → customers.id 拼 JOIN
    assert 'JOIN "customers"' in sql
    assert 'ON "orders"."customer_id" = "customers"."id"' in sql
    assert params == ["completed"]


def test_compile_unknown_metric_raises():
    with pytest.raises(ValueError):
        compile_metric_sql("不存在的指标")


def test_compile_unreachable_dimension_raises():
    # "email" 列不在任何能沿外键到达 orders 的表（demo 库无此列），应报错
    with pytest.raises(ValueError):
        compile_metric_sql("销售额", ["nonexistent_col"])


def test_compile_ratio_metric_divides():
    sql, params = compile_metric_sql("客单价", ["region"])
    assert "SUM" in sql
    assert "NULLIF(COUNT(*), 0)" in sql
    assert 'JOIN "customers"' in sql
    assert params == ["completed"]


def test_compile_derived_metric_cross_table():
    # 毛利率 = (SUM(total) - SUM(quantity*cost)) / SUM(total)，成本跨 orders→products
    sql, params = compile_metric_sql("毛利率", ["category"])
    assert 'SUM("orders"."quantity" * "products"."cost")' in sql
    assert 'JOIN "products"' in sql
    assert 'NULLIF(SUM("orders"."total"), 0)' in sql
    assert params == ["completed"]


def test_query_metric_returns_rows():
    r = query_metric("销售额", ["region"], user_id="dba")
    assert "rows" in r
    assert r["rows"], "应该返回非空结果"
