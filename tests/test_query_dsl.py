"""受控取数 DSL 的自检：白名单 + 参数化，注入进不来、写操作造不出。"""
import sqlite3

import pytest

from db.seed import DB_PATH
from harness.tools.query_dsl import build_select_from_spec, query_table


def test_build_select_basic():
    sql, params = build_select_from_spec({
        "table": "orders",
        "select": ["status", {"agg": "SUM", "column": "total", "alias": "total_sales"}],
        "where": [{"column": "status", "op": "=", "value": "completed"}],
        "group_by": ["status"],
        "order_by": [{"column": "total", "dir": "DESC"}],
        "limit": 5,
    })
    assert sql.startswith("SELECT")
    assert '"orders"' in sql
    assert "SUM" in sql
    assert "GROUP BY" in sql
    assert params == ["completed"]


def test_build_parameterizes_injection_value():
    # 恶意 value 走 ? 占位符，绝不拼进 SQL 文本
    sql, params = build_select_from_spec({
        "table": "orders",
        "select": ["status"],
        "where": [{"column": "status", "op": "=", "value": "'; DROP TABLE orders;--"}],
    })
    assert "DROP" not in sql
    assert params == ["'; DROP TABLE orders;--"]


def test_build_rejects_unknown_column_and_op():
    with pytest.raises(ValueError):
        build_select_from_spec({"table": "orders", "select": ["nonexistent_col"]})
    with pytest.raises(ValueError):
        build_select_from_spec({
            "table": "orders",
            "select": ["status"],
            "where": [{"column": "status", "op": "DROP", "value": "x"}],
        })
    with pytest.raises(ValueError):
        build_select_from_spec({
            "table": "orders",
            "select": [{"agg": "SUM", "column": "*"}],  # 只有 COUNT 支持 *
        })


def test_build_rejects_system_table():
    with pytest.raises(ValueError):
        build_select_from_spec({"table": "agent_users", "select": ["*"]})


def test_query_table_executes_without_injecting():
    before = sqlite3.connect(DB_PATH)
    n_before = before.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    before.close()

    r = query_table({
        "table": "orders",
        "select": ["status"],
        "where": [{"column": "status", "op": "=", "value": "'; DROP TABLE orders;--"}],
    }, user_id="dba")

    # 恶意字符串不可能匹配任何 status → 空结果；且表仍在、行数不变
    assert r.get("count") == 0
    after = sqlite3.connect(DB_PATH)
    assert after.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='orders'").fetchone()[0] == 1
    assert after.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == n_before
    after.close()
