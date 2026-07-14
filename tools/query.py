import sqlite3
from db.seed import DB_PATH

RUN_QUERY_TOOL = {
    "name": "run_query",
    "description": (
        "在 SQLite 数据库上执行一条只读 SELECT 查询。"
        "只允许 SELECT 语句。返回最多 50 行结果。"
        "复杂查询优先用 CTE (WITH 子句)。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": "要执行的 SELECT 查询语句。只允许 SELECT。"
            }
        },
        "required": ["sql"]
    }
}

def run_query(sql: str) -> dict:
    # 安全：只允许 SELECT（防 SQL 注入 + 防误删数据）
    cleaned = sql.strip().upper()
    if not cleaned.startswith("SELECT"):
        return {
            "error": "只允许 SELECT 查询",
            "detail": f"检测到非 SELECT 语句"
        }

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # 让结果可以用列名访问
    try:
        cursor = conn.execute(sql)
        rows = [dict(row) for row in cursor.fetchmany(50)]
        conn.close()
        return {
            "rows": rows,
            "count": len(rows),
            "truncated": len(rows) >= 50
        }
    except Exception as e:
        conn.close()
        return {"error": str(e), "sql": sql}