"""受控取数 DSL —— LLM 不写自由 SQL，只填结构化取数单，代码确定性拼 SELECT。

与 run_query 的区别：
- run_query：LLM 生成自由 SQL → guard_sql 事后拦截（黑名单）
- query_table：LLM 填 {table, select, where, group_by, order_by, limit}
  → build_select_from_spec 白名单校验 + 参数化拼 SQL（白名单，事前不可能出错）

安全边界：
1. 表名/列名来自 sqlite_master + PRAGMA 白名单，系统表直接拒绝
2. 聚合函数 / 过滤操作符 / 排序方向都是封闭白名单
3. where 的 value 走 sqlite3 参数化（? 占位符），永不拼进 SQL 文本
4. 仅支持单表——JOIN 仍走 run_query（受控 DSL 的已知边界）
"""

import sqlite3

from db.seed import DB_PATH
from harness.tools.query import run_query

_AGG_WHITELIST = {"SUM", "AVG", "COUNT", "MIN", "MAX"}
_OP_WHITELIST = {"=", "!=", "<", ">", "<=", ">=", "IN", "NOT IN", "LIKE", "IS NULL", "IS NOT NULL"}
_DIR_WHITELIST = {"ASC", "DESC"}
# 与 lineage._SYSTEM_TABLES 对齐：权限/记忆/元数据表不开放给取数 DSL
_SYSTEM_TABLES = {"agent_roles", "agent_users", "user_memory", "user_feedback", "sqlite_sequence"}

QUERY_TABLE_TOOL = {
    "name": "query_table",
    "description": (
        "受控取数：用结构化参数查询单表，不写 SQL。代码会校验白名单并确定性地拼成 SELECT。"
        "比 run_query 更安全——无法通过它写 DROP/DELETE/多语句/子查询注入。"
        "适合单表过滤、分组聚合、排序、取前 N 条；多表 JOIN 请用 run_query。"
        "返回 JSON: {rows, count, truncated}。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "table": {"type": "string", "description": "表名（先 list_tables/describe_table 确认）"},
            "select": {
                "type": "array",
                "description": "要选的列。元素是字符串（列名）或对象 {column, agg, alias}；"
                               "agg 限 SUM/AVG/COUNT/MIN/MAX；COUNT 可用 column='*' 表示 COUNT(*)",
                "items": {"type": ["string", "object"]},
            },
            "where": {
                "type": "array",
                "description": "过滤条件。元素 {column, op, value}；op 限 = != < > <= >= IN NOT IN LIKE "
                               "IS NULL IS NOT NULL；IN/NOT IN 的 value 是数组；IS NULL 系不需要 value",
                "items": {"type": "object"},
            },
            "group_by": {"type": "array", "items": {"type": "string"}, "description": "分组列名"},
            "order_by": {
                "type": "array",
                "description": "排序。元素 {column, dir} 或字符串列名；dir 限 ASC/DESC；只支持原表列名（不支持别名）",
                "items": {"type": ["string", "object"]},
            },
            "limit": {"type": "integer", "minimum": 1, "description": "最多返回行数"},
        },
        "required": ["table", "select"],
    },
}


def _q(ident: str) -> str:
    """双引号包裹标识符（表名/列名/别名），内部双引号转义。"""
    return '"' + ident.replace('"', '""') + '"'


def _table_columns(table: str) -> list[str] | None:
    """返回表的列名白名单；表不存在返回 None。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        names = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if table not in names:
            return None
        return [r[1] for r in conn.execute(f"PRAGMA table_info('{table}')").fetchall()]
    finally:
        conn.close()


def build_select_from_spec(spec: dict) -> tuple[str, list]:
    """把受控取数单确定性拼成 (sql, params)。

    value 全部参数化，params 顺序 = where 里 value 出现的顺序。
    校验失败抛 ValueError（query_table 捕获后转成 Agent 可自愈的 error）。
    """
    table = spec.get("table")
    if not table or not isinstance(table, str):
        raise ValueError("缺少合法的 table")
    if table in _SYSTEM_TABLES:
        raise ValueError(f"表 '{table}' 是系统表，不允许通过取数 DSL 访问")

    cols = _table_columns(table)
    if cols is None:
        raise ValueError(f"表 '{table}' 不存在，先 list_tables/describe_table 核对")
    colset = set(cols)

    # ── SELECT ──
    select_specs = spec.get("select") or []
    if not select_specs:
        raise ValueError("select 不能为空")
    select_parts: list[str] = []
    for s in select_specs:
        if isinstance(s, str):
            col, agg, alias = s, None, None
        elif isinstance(s, dict):
            col, agg, alias = s.get("column"), (s.get("agg") or "").upper() or None, s.get("alias")
        else:
            raise ValueError("select 元素必须是字符串或对象")

        if not col:
            raise ValueError("select 元素缺少 column")
        if col != "*" and col not in colset:
            raise ValueError(f"列 '{col}' 不在表 '{table}' 中")

        if agg:
            if agg not in _AGG_WHITELIST:
                raise ValueError(f"聚合函数 '{agg}' 不允许，可选 {sorted(_AGG_WHITELIST)}")
            if col == "*" and agg != "COUNT":
                raise ValueError("只有 COUNT 支持 *")
            expr = "COUNT(*)" if col == "*" else f"{agg}({_q(col)})"
        else:
            if col == "*":
                raise ValueError("select 的 '*' 只允许出现在 COUNT(*) 中")
            expr = _q(col)
        if alias:
            expr += f" AS {_q(alias)}"
        select_parts.append(expr)

    sql = "SELECT " + ", ".join(select_parts) + f" FROM {_q(table)}"

    # ── WHERE（参数化）──
    params: list = []
    where_specs = spec.get("where") or []
    if where_specs:
        clauses: list[str] = []
        for w in where_specs:
            if not isinstance(w, dict):
                raise ValueError("where 元素必须是对象")
            col = w.get("column")
            op = (w.get("op") or "=").upper()
            if col not in colset:
                raise ValueError(f"列 '{col}' 不在表 '{table}' 中")
            if op not in _OP_WHITELIST:
                raise ValueError(f"操作符 '{op}' 不允许，可选 {sorted(_OP_WHITELIST)}")
            if op in ("IS NULL", "IS NOT NULL"):
                clauses.append(f"{_q(col)} {op}")
            elif op in ("IN", "NOT IN"):
                vals = w.get("value")
                if not isinstance(vals, list) or not vals:
                    raise ValueError(f"{op} 的 value 必须是非空数组")
                clauses.append(f"{_q(col)} {op} ({', '.join('?' for _ in vals)})")
                params.extend(vals)
            else:
                clauses.append(f"{_q(col)} {op} ?")
                params.append(w.get("value"))
        sql += " WHERE " + " AND ".join(clauses)

    # ── GROUP BY ──
    group_by = spec.get("group_by") or []
    if group_by:
        for g in group_by:
            if g not in colset:
                raise ValueError(f"分组列 '{g}' 不在表 '{table}' 中")
        sql += " GROUP BY " + ", ".join(_q(g) for g in group_by)

    # ── ORDER BY（只支持原表列，不支持别名）──
    order_by = spec.get("order_by") or []
    if order_by:
        parts: list[str] = []
        for o in order_by:
            if isinstance(o, str):
                col, direction = o, "ASC"
            elif isinstance(o, dict):
                col, direction = o.get("column"), (o.get("dir") or "ASC").upper()
            else:
                raise ValueError("order_by 元素必须是字符串或对象")
            if col not in colset:
                raise ValueError(f"排序列 '{col}' 不在表 '{table}' 中（order_by 只支持原表列名）")
            if direction not in _DIR_WHITELIST:
                raise ValueError(f"排序方向 '{direction}' 不允许，可选 ASC/DESC")
            parts.append(f"{_q(col)} {direction}")
        sql += " ORDER BY " + ", ".join(parts)

    # ── LIMIT ──
    limit = spec.get("limit")
    if limit is not None:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit 必须是正整数")
        sql += f" LIMIT {int(limit)}"

    return sql, params


def query_table(spec: dict, user_id: str | None = None, max_rows: int = 50) -> dict:
    """受控取数入口：build → run_query（复用完整鉴权/HITL/截断/摘要链路）。"""
    try:
        sql, params = build_select_from_spec(spec)
    except ValueError as e:
        return {
            "error": str(e),
            "error_type": "ValueError",
            "retryable": True,
            "hint": "先 describe_table 核对正确的表名/列名，再重填取数单重试。",
        }
    return run_query(sql, max_rows=max_rows, user_id=user_id, params=params)
