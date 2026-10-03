"""回归：模板 SQL 必须"真能执行"，L2 SQL 护栏必须接在 run_query 上。

背景：此前模板测试只验证"匹配到没匹配到、槽位填没填对"，从不执行生成的
SQL —— 于是 `e.position`（真实列是 title）、`order_items`（表不存在）、
`information_schema`（SQLite 没有）、`NOT LIKE '_%'`（_ 是通配符，恒空）
这些错误长期潜伏。这里逐条执行，堵住这个盲区。
"""

import re

from harness.bootstrap import bootstrap_data
from harness.context.template_matcher import init_metric_registry
from harness.tools.query import run_query

# 常见占位符的样例值：能把模板填成可执行 SQL 即可
_FILLS = {
    "start_date": "2025-06-01",
    "end_date": "2026-09-01",
    "date_range_start": "2026-08-01",
    "dept_name": "销售部",
    "limit": "5",
    "month": "6",
    "year": "2026",
    "status": "completed",
    "product_name": "x",
    "table_name": "orders",
}


def test_every_template_sql_executes():
    bootstrap_data()
    rows = init_metric_registry().execute(
        "SELECT metric_name, sql_template FROM metric_registry"
    ).fetchall()
    assert rows, "模板库为空"

    failures = []
    checked = 0
    for name, sql in rows:
        filled = sql
        for key, val in _FILLS.items():
            filled = filled.replace("{" + key + "}", val)
        if re.findall(r"\{(\w+)\}", filled):
            continue  # 还有未覆盖占位符，跳过
        checked += 1
        result = run_query(filled)
        if result.get("error"):
            failures.append(f"{name}: {result['error']}")

    assert checked >= 10, f"实际只验证了 {checked} 个模板"
    assert not failures, "以下模板 SQL 执行失败:\n" + "\n".join(failures)


def test_run_query_applies_sql_guardrail():
    """L2 护栏必须接在 agent 的 SQL 路径上（此前漏接）。"""
    bootstrap_data()
    # 内部系统表应被拦
    assert run_query("SELECT * FROM sqlite_sequence").get("error")
    # 多语句应被拦（DROP）
    assert run_query("SELECT 1; DROP TABLE orders").get("error")
    # sqlite_master 是列举表的正当途径，放行（表级 RBAC 另管）
    assert not run_query("SELECT name FROM sqlite_master WHERE type='table'").get("error")
