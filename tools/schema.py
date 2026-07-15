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

def describe_table(table_name: str) -> dict:
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.execute(f"PRAGMA table_info('{table_name}')")
        columns = [
            {"name": row[1], "type": row[2], "nullable": not row[3]}
            for row in cursor.fetchall()
        ]
        if not columns:
            # 结构化错误：给出可用表 + 修复建议，方便模型自纠
            available = [
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            ]
            return {
                "error": f"表 '{table_name}' 不存在",
                "available_tables": available,
                "hint": "请从 available_tables 中选一个表名重试。",
                "columns": [],
            }
        return {"table": table_name, "columns": columns}
    finally:
        conn.close()
