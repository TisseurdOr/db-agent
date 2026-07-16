import sqlite3
from db.seed import DB_PATH

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
        "required": []
    }
}

def list_tables() -> dict:
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = [row[0] for row in cursor.fetchall()]
        return {"tables": tables}
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
                "description": "要查看的表名。若不确定，先调 list_tables 获取。"
            }
        },
        "required": ["table_name"]
    }
}

# 安全：白名单校验表名——防止通过 LLM 输出注入恶意 SQL。
# PRAGMA table_info 虽然不接受多语句，但 table_name 来自 LLM 输出，
# 可能包含引号或特殊字符。用 sqlite_master 先查白名单，
# 确保表名是真实存在的标识符，而不是注入 payload。
def describe_table(table_name: str) -> dict:
    conn = sqlite3.connect(DB_PATH)
    try:
        # 白名单校验
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

        # 表名已验证在白名单内，可以安全拼接
        cursor = conn.execute(f"PRAGMA table_info('{table_name}')")
        columns = [
            {"name": row[1], "type": row[2], "nullable": not row[3]}
            for row in cursor.fetchall()
        ]
        return {"table": table_name, "columns": columns}
    finally:
        conn.close()


# get_db_schema_summary: 一次返回所有表和字段的摘要。
# 为什么要这个 Tool：按 lesson 0008 的建议，如果没有这个 Tool，
# Agent 在每次查询时要调 list_tables → 对每张表调 describe_table，
# 浪费 3-4 轮 tool call。有了 schema_summary，一轮就拿全貌。
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


def get_schema_summary() -> dict:
    """一次性返回所有表 + 字段的摘要。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        tables_result = []
        table_names = [
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        for table_name in table_names:
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
