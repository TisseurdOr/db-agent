"""语义层：指标编译 + 外键 join 图。

LLM 不写自由 SQL，只填结构化取数单 {metric, group_by}，
代码沿外键 join 图确定性拼出多表 SQL 并执行。对标 dbt MetricFlow 的
「指标定义 + 实体(外键) → 编译 SQL」思路。

与现有工具的分工：
- run_query：LLM 写自由 SQL，guard 事后拦截（黑名单）
- query_table：LLM 填单表结构化单，代码拼单表 SELECT（白名单）
- query_metric（本模块）：LLM 指定指标 + 维度，代码沿 FK 拼多表 JOIN（白名单 + join 图）

ponytail: 支持 simple（单聚合）、ratio（分子/分母相除）、derived（度量表达式组合）三类指标。
derived 的度量支持跨表列运算（如成本 = SUM(quantity × products.cost)，沿外键 JOIN products）。
"""

import sqlite3
from collections import deque

from db.seed import DB_PATH
from harness.tools.query import run_query

# 结构化指标定义——对应 metrics.py 里的 formula 字符串，但代码能直接编译。
# table/agg/column/filters 全部来自真实表结构，编译时白名单校验。
_METRIC_DEFS = {
    "销售额": {"type": "simple", "table": "orders", "agg": "SUM", "column": "total",
              "filters": [("status", "=", "completed")]},
    "GMV": {"type": "simple", "table": "orders", "agg": "SUM", "column": "total",
            "filters": [("status", "!=", "cancelled")]},
    "净GMV": {"type": "simple", "table": "orders", "agg": "SUM", "column": "total",
             "filters": [("status", "=", "completed")]},
    "订单量": {"type": "simple", "table": "orders", "agg": "COUNT", "column": "*",
              "filters": [("status", "!=", "cancelled")]},
    # ratio：分子/分母两个度量相除。客单价(订单均) = SUM(total)/COUNT(*)
    "客单价": {"type": "ratio",
             "numerator": {"table": "orders", "agg": "SUM", "column": "total"},
             "denominator": {"table": "orders", "agg": "COUNT", "column": "*"},
             "filters": [("status", "=", "completed")]},
    # derived：度量表达式组合。毛利率 = (收入-成本)/收入，成本跨 orders→products 算 SUM(quantity×cost)
    "毛利率": {"type": "derived", "table": "orders",
             "expr": "({revenue} - {cost}) / NULLIF({revenue}, 0)",
             "measures": {
                 "revenue": {"table": "orders", "agg": "SUM", "column": "total"},
                 "cost": {"table": "orders", "agg": "SUM",
                          "expr": [("orders", "quantity"), "*", ("products", "cost")]},
             },
             "filters": [("status", "=", "completed")]},
}

_SYSTEM_TABLES = {"agent_roles", "agent_users", "user_memory", "user_feedback",
                  "sqlite_sequence", "sqlite_stat1"}

# 度量表达式允许的算术运算符（白名单，拒绝一切其他 token）
_OP_WHITELIST = {"+", "-", "*", "/"}


def _q(ident: str) -> str:
    """双引号包裹标识符（表名/列名/别名）。"""
    return '"' + ident.replace('"', '""') + '"'


def _table_columns(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """返回 {表名: [列名...]}，跳过系统表。"""
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    out = {}
    for t in tables:
        if t in _SYSTEM_TABLES or t.startswith("sqlite_"):
            continue
        out[t] = [r[1] for r in conn.execute(f"PRAGMA table_info('{t}')").fetchall()]
    return out


def get_join_graph() -> list[tuple[str, str, str, str]]:
    """从真实外键提取 join 图，返回 [(from_table, from_col, to_table, to_col), ...]。

    例: orders.customer_id → customers.id 记作 ("orders", "customer_id", "customers", "id")。
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        edges = []
        for t in tables:
            if t in _SYSTEM_TABLES or t.startswith("sqlite_"):
                continue
            for row in conn.execute(f"PRAGMA foreign_key_list('{t}')").fetchall():
                to_table, from_col, to_col = row[2], row[3], row[4]
                edges.append((t, from_col, to_table, to_col))
        return edges
    finally:
        conn.close()


def _find_join_path(edges, start: str, target: str) -> list[tuple[str, str, str, str]] | None:
    """无向 BFS 找 start→target 的外键路径，返回定向边列表（可拼 JOIN），无路径返回 None。"""
    adj: dict[str, list] = {}
    for (a, ac, b, bc) in edges:
        adj.setdefault(a, []).append((a, ac, b, bc))  # a → b
        adj.setdefault(b, []).append((b, bc, a, ac))  # b → a（反向）
    if start == target:
        return []

    q = deque([start])
    parent = {start: None}
    while q:
        cur = q.popleft()
        if cur == target:
            break
        for (src, sc, dst, dc) in adj.get(cur, []):
            if dst not in parent:
                parent[dst] = (cur, (src, sc, dst, dc))
                q.append(dst)
    if target not in parent:
        return None

    path = []
    node = target
    while parent[node] is not None:
        prev, edge = parent[node]
        path.append(edge)
        node = prev
    path.reverse()
    return path


def compile_metric_sql(metric_key: str, group_by: list[str] | None = None,
                       filters: list | None = None) -> tuple[str, list]:
    """把「指标 + 维度 + 过滤」确定性编译成 (sql, params)。失败抛 ValueError。"""
    m = _METRIC_DEFS.get(metric_key)
    if m is None:
        raise ValueError(f"指标 '{metric_key}' 未定义，可用: {sorted(_METRIC_DEFS)}")

    conn = sqlite3.connect(DB_PATH)
    try:
        tables = _table_columns(conn)
    finally:
        conn.close()

    # base_table：simple 用自己的 table，ratio 用分子度量的表
    base_table = m.get("table") or m["numerator"]["table"]
    if base_table not in tables:
        raise ValueError(f"指标表 '{base_table}' 不存在")

    # 列名 → 所在表（供维度解析）
    col_to_tables: dict[str, list[str]] = {}
    for t, cols in tables.items():
        for c in cols:
            col_to_tables.setdefault(c, []).append(t)

    edges = get_join_graph()
    joins: list[str] = []
    seen_joins: set = set()

    def ensure_join(to_table: str) -> None:
        """若 to_table != base_table，沿外键加 JOIN（按 (src,dst) 去重）。"""
        if to_table == base_table:
            return
        path = _find_join_path(edges, base_table, to_table)
        if path is None:
            raise ValueError(f"表 '{to_table}' 无法从 '{base_table}' 沿外键到达")
        for (src, sc, dst, dc) in path:
            key = (src, dst)
            if key not in seen_joins:
                seen_joins.add(key)
                joins.append(f"JOIN {_q(dst)} ON {_q(src)}.{_q(sc)} = {_q(dst)}.{_q(dc)}")

    def measure_expr(meas: dict) -> str:
        """度量 → SQL 聚合表达式（必要时加 JOIN）。

        两种形态：单列聚合 {table,agg,column}；跨表列运算 {table,agg,expr}，
        expr 是中缀列表 [(表,列), 运算符, (表,列), ...]，运算符走 _OP_WHITELIST。
        """
        if "expr" in meas:
            parts = []
            for tok in meas["expr"]:
                if isinstance(tok, tuple):
                    t, c = tok
                    ensure_join(t)
                    parts.append(f"{_q(t)}.{_q(c)}")
                elif tok in _OP_WHITELIST:
                    parts.append(f" {tok} ")
                else:
                    raise ValueError(f"非法度量表达式元素: {tok!r}")
            return f"{meas['agg']}({''.join(parts)})"
        t, col, agg = meas["table"], meas["column"], meas["agg"]
        ensure_join(t)
        if col == "*":
            return "COUNT(*)"
        return f"{agg}({_q(t)}.{_q(col)})"

    # 聚合表达式：simple = 单聚合；ratio = 分子/分母相除；derived = 度量表达式模板（NULLIF 防除零）
    mtype = m.get("type", "simple")
    if mtype == "ratio":
        num_expr = measure_expr(m["numerator"])
        den_expr = measure_expr(m["denominator"])
        agg_expr = f"({num_expr}) / NULLIF({den_expr}, 0)"
    elif mtype == "derived":
        agg_expr = m["expr"]
        for key, meas in m["measures"].items():
            agg_expr = agg_expr.replace("{" + key + "}", measure_expr(meas))
    else:
        agg_expr = measure_expr(m)
    select_parts = [f"{agg_expr} AS {_q(metric_key)}"]

    params: list = []
    where_parts: list = []
    for (fcol, fop, fval) in m["filters"]:
        where_parts.append(f"{_q(base_table)}.{_q(fcol)} {fop} ?")
        params.append(fval)
    for (fcol, fop, fval) in (filters or []):
        where_parts.append(f"{_q(fcol)} {fop} ?")
        params.append(fval)

    group_cols: list[str] = []
    for gcol in (group_by or []):
        cands = col_to_tables.get(gcol)
        if not cands:
            raise ValueError(f"维度列 '{gcol}' 不存在")
        gtable = base_table if base_table in cands else None
        if gtable is None:
            for cand in cands:
                if _find_join_path(edges, base_table, cand) is not None:
                    gtable = cand
                    break
        if gtable is None:
            raise ValueError(f"维度 '{gcol}' 无法从 '{base_table}' 沿外键到达")
        ensure_join(gtable)
        select_parts.append(f"{_q(gtable)}.{_q(gcol)} AS {_q(gcol)}")
        group_cols.append(f"{_q(gtable)}.{_q(gcol)}")

    sql = "SELECT " + ", ".join(select_parts) + f" FROM {_q(base_table)}"
    for join_sql in joins:
        sql += " " + join_sql
    if where_parts:
        sql += " WHERE " + " AND ".join(where_parts)
    if group_cols:
        sql += " GROUP BY " + ", ".join(group_cols)

    return sql, params


QUERY_METRIC_TOOL = {
    "name": "query_metric",
    "description": (
        "受控指标查询：指定指标名 + 分组维度，代码沿外键自动拼多表 JOIN 并执行，LLM 不写 SQL。"
        "适合「XX 按 YY 分组」这类指标聚合，如「每个区域的销售额」。"
        "可用指标：销售额/GMV/净GMV/订单量/客单价/毛利率。维度列名可用 describe_table 查。"
        "返回 JSON: {rows, count, sql}。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "metric": {"type": "string", "description": "指标名，如 销售额"},
            "group_by": {"type": "array", "items": {"type": "string"},
                         "description": "分组维度列名，如 ['region']"},
            "filters": {"type": "array",
                        "description": "额外过滤，元素 {column, op, value}；column 用 表.列 形式"},
        },
        "required": ["metric"],
    },
}


def query_metric(metric: str, group_by: list[str] | None = None,
                 filters: list | None = None, user_id: str | None = None) -> dict:
    """受控指标取数入口：编译 → run_query（复用鉴权/HITL/截断链路）。"""
    try:
        # filters 兼容 {column,op,value} 或 (col,op,val) 两种写法
        f = None
        if filters:
            f = [(x["column"], x["op"], x["value"]) if isinstance(x, dict) else tuple(x)
                 for x in filters]
        sql, params = compile_metric_sql(metric, group_by, f)
    except ValueError as e:
        return {"error": str(e), "error_type": "ValueError", "retryable": True,
                "hint": "先 describe_table 核对指标名和维度列名，再重试。"}
    result = run_query(sql, user_id=user_id, params=params)
    result["sql"] = sql
    return result


def demo() -> None:
    """自检：打印几条指标查询编译出的 SQL + 真实执行结果。"""
    for metric, group_by in [("销售额", ["region"]), ("订单量", ["category"]),
                             ("销售额", ["name"]), ("客单价", ["region"]),
                             ("毛利率", ["category"])]:
        try:
            sql, params = compile_metric_sql(metric, group_by)
            print(f"\n=== {metric} 按 {group_by} ===")
            print("SQL:", sql)
            print("params:", params)
            r = query_metric(metric, group_by, user_id="dba")
            print("结果:", r.get("rows", r.get("error")))
        except ValueError as e:
            print(f"\n=== {metric} 按 {group_by} === 编译失败: {e}")


if __name__ == "__main__":
    demo()
