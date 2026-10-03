"""Read-only tools for the separate Olist warehouse database."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from db.olist_warehouse import (
    OLIST_WAREHOUSE_TABLES,
    WAREHOUSE_DB_PATH,
    compare_olist_periods,
)
from harness.constraints.entitlement import guard, resolve_user_id
from harness.tools import tool

_ALLOWED_TABLE_SET = set(OLIST_WAREHOUSE_TABLES)
_SELECT_RE = re.compile(r"^\s*SELECT\b", re.IGNORECASE)
_TABLE_RE = re.compile(r"\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)


def _warehouse_connection(db_path: str | Path = WAREHOUSE_DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(
            "warehouse.db 不存在，请先运行 scripts/build_olist_warehouse.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


@tool(description=(
    "列出独立 Olist 数仓中的 ODS/DIM/DWD/DWS/ADS 表。"
    "当用户问 Olist、电商、数仓分层或 ADS 层有哪些表时使用。"
))
def list_warehouse_tables() -> dict:
    ent = guard(resolve_user_id(None), "list_tables")
    if isinstance(ent, dict):
        return ent
    try:
        conn = _warehouse_connection()
        try:
            existing = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        finally:
            conn.close()
    except Exception as exc:
        return {"error": True, "message": str(exc)}
    tables = [table for table in OLIST_WAREHOUSE_TABLES if table in existing]
    return {"tables": tables, "count": len(tables), "catalog": "olist_warehouse"}


@tool(description=(
    "查看独立 Olist 数仓中某张分层表的字段。"
    "只能查询 ODS/DIM/DWD/DWS/ADS 中已登记的表。"
))
def describe_warehouse_table(table_name: str) -> dict:
    """table_name: Olist 数仓表名，例如 ads_olist_period_metrics"""
    ent = guard(resolve_user_id(None), "list_tables")
    if isinstance(ent, dict):
        return ent
    if table_name not in _ALLOWED_TABLE_SET:
        return {"error": True, "message": f"不是 Olist 数仓表: {table_name}"}
    try:
        conn = _warehouse_connection()
        try:
            rows = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
        finally:
            conn.close()
    except Exception as exc:
        return {"error": True, "message": str(exc)}
    return {
        "table": table_name,
        "columns": [
            {"name": row["name"], "type": row["type"], "nullable": not row["notnull"]}
            for row in rows
        ],
    }


@tool(description=(
    "在独立 Olist 数仓上执行只读 SELECT。"
    "只允许访问 ODS/DIM/DWD/DWS/ADS 中已登记的表。"
    "业务查询优先 ADS，下钻 DWS，再查 DWD；不要直接查 ODS。"
))
def query_warehouse(sql: str, max_rows: int = 50) -> dict:
    """sql: 只读 SELECT 语句，表名必须来自 Olist 数仓
    max_rows: 最大返回行数，默认 50"""
    ent = guard(resolve_user_id(None), "list_tables")
    if isinstance(ent, dict):
        return ent
    if not _SELECT_RE.match(sql or ""):
        return {"error": "只允许 SELECT 查询"}
    referenced = {match.group(1).lower() for match in _TABLE_RE.finditer(sql)}
    unknown = sorted(table for table in referenced if table not in _ALLOWED_TABLE_SET)
    if unknown:
        return {
            "error": True,
            "message": f"包含非 Olist 数仓表: {', '.join(unknown)}",
            "hint": "先用 list_warehouse_tables 查看允许的表。",
        }
    try:
        conn = _warehouse_connection()
        try:
            cursor = conn.execute(sql)
            rows = [dict(row) for row in cursor.fetchmany(max_rows + 1)]
        finally:
            conn.close()
    except Exception as exc:
        return {"error": True, "message": str(exc), "sql": sql}
    truncated = len(rows) > max_rows
    return {
        "rows": rows[:max_rows],
        "count": min(len(rows), max_rows),
        "truncated": truncated,
    }


@tool(description=(
    "查询 Olist 数仓 ADS 层的两期对比。"
    "适合用户明确给出两个 period_key、指标和维度时使用。"
    "period_key 示例: 2018-H1、2018-H2、2018-Q1、2018-07、2018。"
    "返回两期数值、绝对变化、变化率和数据完整性警告。"
))
def query_period_comparison(
    metric_name: str,
    period_a: str,
    period_b: str,
    dimension_type: str = "overall",
    dimension_value: str = "ALL",
) -> dict:
    """metric_name: 指标名，可选 gross_sales / net_sales / order_count / item_count
    period_a: 第一期 period_key，例如 2018-H1
    period_b: 第二期 period_key，例如 2018-H2
    dimension_type: overall / customer_state / seller_state / product_category
    dimension_value: 维度值；overall 使用 ALL"""
    ent = guard(resolve_user_id(None), "list_tables")
    if isinstance(ent, dict):
        return ent
    return compare_olist_periods(
        metric_name=metric_name,
        period_a=period_a,
        period_b=period_b,
        dimension_type=dimension_type,
        dimension_value=dimension_value,
    )


LIST_WAREHOUSE_TABLES_TOOL = list_warehouse_tables.tool_schema
DESCRIBE_WAREHOUSE_TABLE_TOOL = describe_warehouse_table.tool_schema
QUERY_WAREHOUSE_TOOL = query_warehouse.tool_schema
QUERY_PERIOD_COMPARISON_TOOL = query_period_comparison.tool_schema
