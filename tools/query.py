import sqlite3
from db.seed import DB_PATH

RUN_QUERY_TOOL = {
    "name": "run_query",
    "description": (
        "在 SQLite 数据库上执行一条 SELECT 查询。"
    "当你需要从数据库获取数据时使用此工具。"
    "调用前必须先通过 describe_table 了解字段名——不要猜测。"
    "只支持 SELECT 语句。"
    "返回 JSON: {rows: [...], count: N, truncated: bool}"
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
            "detail": "检测到非 SELECT 语句",
            "hint": "请把语句改写为 SELECT；写操作（INSERT/UPDATE/DELETE 等）不被允许。",
            "sql": sql,
        }

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # 让结果可以用列名访问
    try:
        cursor = conn.execute(sql)
        rows = [dict(row) for row in cursor.fetchmany(50)]
        return {
            "rows": rows,
            "count": len(rows),
            "truncated": len(rows) >= 50
        }
    except Exception as e:
        return {
            "error": str(e),
            "error_type": type(e).__name__,
            "sql": sql,
            "hint": "检查表名/字段名是否正确，可先调 describe_table 确认字段后重试。",
        }
    finally:
        conn.close()