import sqlite3
from db.seed import DB_PATH

LIST_TABLES_TOOL = {
    "name": "list_tables",
    "description": "列出数据库中的所有表名。在写 SQL 之前先调这个了解有哪些表。",
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": []
    }
}

def list_tables() -> dict:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )
    tables = [row[0] for row in cursor.fetchall()]
    conn.close()
    return {"tables": tables}


DESCRIBE_TABLE_TOOL = {
    "name": "describe_table",
    "description": "查看指定表的所有字段名和类型。写 SQL 前必须调这个了解字段。",
    "input_schema": {
        "type": "object",
        "properties": {
            "table_name": {
                "type": "string",
                "description": "要查看的表名"
            }
        },
        "required": ["table_name"]
    }
}

def describe_table(table_name: str) -> dict:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute(f"PRAGMA table_info('{table_name}')")
    columns = [
        {"name": row[1], "type": row[2], "nullable": not row[3]}
        for row in cursor.fetchall()
    ]
    conn.close()
    if not columns:
        return {"error": f"表 '{table_name}' 不存在", "columns": []}
    return {"table": table_name, "columns": columns}
