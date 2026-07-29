import os
import sqlite3

from db.seed import DB_PATH
from multi_agent.entitlement import get_user, check_entitlement, resolve_user_id

LIST_TABLES_TOOL = {
    "name": "list_tables",
    "description": (
        "列出数据库中的所有表名。"
        "当你还不知道有哪些表时，在写任何 SQL 之前先调用它。"
        "返回 JSON: {tables: [表名, ...]}。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


def list_tables(user_id: str | None = None) -> dict:
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = [row[0] for row in cursor.fetchall()]
        user = get_user(resolve_user_id(user_id))
        ent = check_entitlement(user, tool_name="list_tables", tables=tables)
        if not ent.passed:
            return {"error": True, "message": ent.reason}
        return {"tables": ent.tables or []}
    except Exception as e:
        return {
            "error": str(e),
            "error_type": type(e).__name__,
            "hint": "数据库可能未初始化，请先运行 db/seed.py。",
        }
    finally:
        conn.close()


DESCRIBE_TABLE_TOOL = {
    "name": "describe_table",
    "description": (
        "查看指定表的所有字段名、类型以及是否可空(nullable)。"
        "写涉及某张表的 SQL 前必须先调它，不要猜字段名。"
        "返回 JSON: {table: 表名, columns: [{name, type, nullable}, ...]}；"
        "表不存在时返回 error 及可用表列表。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "table_name": {
                "type": "string",
                "description": "要查看的表名。若不确定，先调 list_tables 获取。",
            }
        },
        "required": ["table_name"],
    },
}


def describe_table(table_name: str, user_id: str | None = None) -> dict:
    user = get_user(resolve_user_id(user_id))
    ent = check_entitlement(user, tool_name="describe_table", table=table_name)
    if not ent.passed:
        return {"error": True, "message": ent.reason}

    conn = sqlite3.connect(DB_PATH)
    try:
        valid_tables = [
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        if table_name not in valid_tables:
            return {
                "error": True,
                "message": f"表 '{table_name}' 不存在",
                "suggestion": f"可用的表: {', '.join(valid_tables)}",
                "hint": "请从可用表中选一个重试",
                "columns": [],
            }

        cursor = conn.execute(f"PRAGMA table_info('{table_name}')")
        columns = [
            {"name": row[1], "type": row[2], "nullable": not row[3]}
            for row in cursor.fetchall()
        ]
        return {"table": table_name, "columns": columns}
    finally:
        conn.close()


GET_SCHEMA_SUMMARY_TOOL = {
    "name": "get_schema_summary",
    "description": (
        "一次性获取数据库中所有表和字段的摘要。"
        "当用户问涉及哪些数据、或需要了解整体数据库结构时优先调用，"
        "避免逐表调用 describe_table。"
        "返回 JSON: {tables: [{name, columns: [{name, type, nullable}]}], table_count: N}"
    ),
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


def get_schema_summary(user_id: str | None = None) -> dict:
    """一次性返回所有表 + 字段的摘要（按用户权限过滤表）。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        table_names = [
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        user = get_user(resolve_user_id(user_id))
        ent = check_entitlement(user, tool_name="list_tables", tables=table_names)
        if not ent.passed:
            return {"error": True, "message": ent.reason}
        allowed = ent.tables or []

        tables_result = []
        for table_name in allowed:
            cursor = conn.execute(f"PRAGMA table_info('{table_name}')")
            columns = [
                {"name": row[1], "type": row[2], "nullable": not row[3]}
                for row in cursor.fetchall()
            ]
            tables_result.append({"name": table_name, "columns": columns})

        return {
            "tables": tables_result,
            "table_count": len(tables_result),
            "total_columns": sum(len(t["columns"]) for t in tables_result),
        }
    finally:
        conn.close()
