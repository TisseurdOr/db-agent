import os
import sqlite3

from db.seed import DB_PATH
from multi_agent.entitlement import get_user, check_entitlement, resolve_user_id

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
                "description": "要执行的 SELECT 查询语句。只允许 SELECT。",
            }
        },
        "required": ["sql"],
    },
}


def run_query(sql: str, max_rows: int = 50, user_id: str | None = None) -> dict:
    """执行 SELECT 查询，自动截断大结果集。"""
    cleaned = sql.strip().upper()
    if not cleaned.startswith("SELECT"):
        return {
            "error": "只允许 SELECT 查询",
            "detail": "检测到非 SELECT 语句",
            "hint": "请把语句改写为 SELECT；写操作（INSERT/UPDATE/DELETE 等）不被允许。",
            "sql": sql,
        }

    user = get_user(resolve_user_id(user_id))
    ent = check_entitlement(user, tool_name="run_query", sql=sql)
    if not ent.passed:
        return {
            "error": ent.reason,
            "sql": sql,
            "message": ent.reason,
        }
    if ent.needs_approval:
        return {
            "error": "需要管理员审批",
            "sql": sql,
            "message": "需要管理员审批",
            "pending_sql": ent.sql,
        }

    sql = ent.sql or sql
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(sql)
        rows = [dict(row) for row in cursor.fetchmany(max_rows + 1)]
        truncated = len(rows) > max_rows
        rows = rows[:max_rows] if truncated else rows
        return {
            "rows": rows,
            "count": len(rows),
            "truncated": truncated,
            "hint": (
                f"结果已截断，仅显示前 {max_rows} 行。如需更多数据，请用 WHERE 或 LIMIT 缩小范围。"
                if truncated
                else None
            ),
            "summary": generate_summary(rows) if truncated else None,
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


def generate_summary(rows: list) -> str:
    """对查询结果做统计摘要——零 API 成本。"""
    if not rows:
        return "空结果"
    cols = list(rows[0].keys())
    numeric_cols = [
        c for c in cols
        if all(isinstance(r.get(c), (int, float)) for r in rows if r.get(c) is not None)
    ]
    parts = [f"共 {len(rows)} 行, {len(cols)} 列"]
    for c in numeric_cols[:3]:
        vals = [r[c] for r in rows if r.get(c) is not None]
        if vals:
            parts.append(f"{c}: avg={sum(vals)/len(vals):.1f} min={min(vals)} max={max(vals)}")
    return " | ".join(parts)
