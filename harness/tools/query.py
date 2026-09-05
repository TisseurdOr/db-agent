import contextvars
import sqlite3

from db.seed import DB_PATH
from harness.constraints.entitlement import (
    get_user,
    guard,
    resolve_user_id,
)

# 自学习回流用：记录最近一次成功执行的 SELECT。
# Agent 最终回答经常不带完整 SQL，从工具层捕获比从自然语言抽更可靠。
# ponytail: ContextVar 而非全局 dict——每请求独立上下文，避免并发串扰。
_last_successful_sql: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "last_successful_sql", default=None
)


def pop_last_successful_sql() -> str | None:
    """取出并清空最近一次成功 SQL；没有则返回 None。"""
    sql = _last_successful_sql.get()
    _last_successful_sql.set(None)
    return sql


def peek_last_successful_sql() -> str | None:
    return _last_successful_sql.get()


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

    ent = guard(user_id, "run_query", sql=sql)
    if isinstance(ent, dict):
        return ent
    if ent.needs_approval:
        user = get_user(resolve_user_id(user_id))
        try:
            from langgraph.types import interrupt
            decision = interrupt({
                "type": "hitl_approval",
                "tool": "run_query",
                "sql": ent.sql,
                "user": user["name"],
                "role": user["role"],
                "message": (
                    f"敏感查询需要审批。\n"
                    f"用户: {user['name']} ({user['role']})\n"
                    f"SQL: {ent.sql}"
                ),
            })
            if isinstance(decision, dict) and not decision.get("approved"):
                return {"error": "用户拒绝了该查询", "sql": sql}
            # 用户批准，继续执行
        except (RuntimeError, ImportError):
            # 不在 graph 上下文（测试/单 Agent 模式），返回审批标记
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
        _last_successful_sql.set(sql)  # 仅成功路径写入，供自学习回流
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
        # retryable=True 是给 Agent 的自愈信号：结合 error + hint 重写 SQL 重试
        # （重试预算由 Agent prompt 约束为 2 次，max_turns 兜底防死循环）。
        return {
            "error": str(e),
            "error_type": type(e).__name__,
            "sql": sql,
            "retryable": True,
            "hint": (
                "先调 describe_table 核对正确的表名/字段名，"
                "再根据本错误信息重写 SQL 重试（最多重试 2 次，仍失败则如实报告）。"
            ),
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
