"""HBase 工具集：内存模拟引擎 + 命令生成 + 执行。

用嵌套 dict 模拟 HBase 的 KV 存储模型：
  _HBASE_STORE[table][row_key][cf:col] = value

两个 Tool：
  run_hbase — 在模拟器上执行 scan/get/count/put/delete/list/create/desc 等
  generate_hbase_query — 生成 HBase Shell 命令（给用户手动执行）
种子数据在 main.py 启动时调用 _seed_hbase_store() 填充。
"""

import re
from collections.abc import Callable

from harness.tools import tool

# ═══════════════════════════════════════════════════════════════════
# 内存存储
# ═══════════════════════════════════════════════════════════════════

# table → row_key → column → value
_HBASE_STORE: dict[str, dict[str, dict[str, str]]] = {}
# table → [列族列表]
_HBASE_META: dict[str, list[str]] = {}
# table → [行键列表]（保持插入顺序）
_HBASE_ROW_ORDER: dict[str, list[str]] = {}
# 被 disable 的表名集合
_HBASE_DISABLED: set[str] = set()


def _seed_hbase_store():
    """填充 HBase 模拟数据。幂等——重复调不会重复插。"""
    if _HBASE_STORE:
        return

    # ── orders 表 ──
    _HBASE_META["orders"] = ["cf"]
    _HBASE_STORE["orders"] = {}
    _HBASE_ROW_ORDER["orders"] = []

    import random
    random.seed(42)

    statuses = ["pending", "completed", "cancelled", "shipped"]
    regions = ["华东", "华南", "华北", "西南"]

    for i in range(1, 31):
        rk = f"order_{i:03d}"
        _HBASE_ROW_ORDER["orders"].append(rk)
        _HBASE_STORE["orders"][rk] = {
            "cf:order_id": str(i),
            "cf:customer_id": str(random.randint(1, 12)),
            "cf:total": str(round(random.uniform(100, 50000), 2)),
            "cf:quantity": str(random.randint(1, 20)),
            "cf:status": random.choice(statuses),
            "cf:region": random.choice(regions),
            "cf:created_at": f"2025-{random.randint(1,12):02d}-{random.randint(1,28):02d}",
        }

    # ── user_profile 表 ──
    _HBASE_META["user_profile"] = ["info", "behavior"]
    _HBASE_STORE["user_profile"] = {}
    _HBASE_ROW_ORDER["user_profile"] = []

    names = ["张三", "李四", "王五", "赵六", "孙七", "周八", "吴九", "郑十",
             "陈一一", "林二二", "黄三三", "刘四四", "杨五五", "吕六六", "马七七"]
    for i in range(1, 16):
        rk = f"user_{i:03d}"
        _HBASE_ROW_ORDER["user_profile"].append(rk)
        _HBASE_STORE["user_profile"][rk] = {
            "info:name": names[i-1],
            "info:email": f"user{i}@example.com",
            "info:age": str(random.randint(22, 55)),
            "info:region": random.choice(regions),
            "behavior:last_login": f"2025-{random.randint(1,12):02d}-{random.randint(1,28):02d}",
            "behavior:pv": str(random.randint(10, 5000)),
            "behavior:purchases": str(random.randint(0, 30)),
        }

    # ── product_catalog 表 ──
    _HBASE_META["product_catalog"] = ["meta", "stock"]
    _HBASE_STORE["product_catalog"] = {}
    _HBASE_ROW_ORDER["product_catalog"] = []

    products = [
        ("企业版SaaS", "软件", "50000"), ("专业版SaaS", "软件", "20000"),
        ("基础版SaaS", "软件", "5000"), ("数据中台", "软件", "120000"),
        ("服务器X1", "硬件", "35000"), ("交换机S500", "硬件", "8000"),
        ("路由器R200", "硬件", "2500"), ("技术咨询", "服务", "30000"),
        ("定制开发", "服务", "4000"), ("运维支持", "服务", "15000"),
    ]
    for i, (name, cat, price) in enumerate(products, 1):
        rk = f"prod_{i:03d}"
        _HBASE_ROW_ORDER["product_catalog"].append(rk)
        _HBASE_STORE["product_catalog"][rk] = {
            "meta:name": name,
            "meta:category": cat,
            "meta:price": price,
            "stock:qty": str(random.randint(0, 200)),
            "stock:warehouse": random.choice(["北京仓", "上海仓", "广州仓"]),
        }


# ═══════════════════════════════════════════════════════════════════
# 内部辅助
# ═══════════════════════════════════════════════════════════════════

def _apply_prefix_filter(rows: list[tuple[str, dict]], prefix: str) -> list[tuple[str, dict]]:
    return [(rk, cols) for rk, cols in rows if rk.startswith(prefix)]


def _parse_simple_filter(filter_str: str) -> Callable | None:
    """解析简单 filter: cf:col op value。返回 lambda 或 None。"""
    if not filter_str:
        return None
    # 尝试: "cf:status = 'completed'"
    m = re.match(r"(\S+)\s*(=|!=|>=|<=|>|<)\s*(.+)", filter_str.strip())
    if not m:
        return None
    col, op, val = m.group(1), m.group(2), m.group(3).strip().strip("'\"")
    ops = {
        "=": lambda a, b: a == b,
        "!=": lambda a, b: a != b,
        ">": lambda a, b: float(a) > float(b) if b.replace(".", "").isdigit() else a > b,
        "<": lambda a, b: float(a) < float(b) if b.replace(".", "").isdigit() else a < b,
    }
    op_fn = ops.get(op)
    if not op_fn:
        return None
    return lambda cols: col in cols and op_fn(cols[col], val)


# ═══════════════════════════════════════════════════════════════════
# Tool 定义
# ═══════════════════════════════════════════════════════════════════

@tool(description=(
    "在本地 HBase 模拟器上执行操作。HBase 是 KV 型 NoSQL，不是 SQL。\n"
    "支持 scan（扫描）、get（读单行）、count（计数）、put（写入）、"
    "delete（删除）、list（列所有表）、create（建表）、desc（表描述）、"
    "disable（禁用表）、enable（启用表）、drop（删表）、truncate（清空表）。\n"
    "返回 JSON: {rows: [{row_key, columns}], count, operation, scanned_rows}。\n"
    "scan 默认最多返回 50 行。写操作返回确认信息。"
))
def run_hbase(
    operation: str,
    table_name: str,
    row_key: str = "",
    start_row: str = "",
    stop_row: str = "",
    filter_expr: str = "",
    column: str = "",
    value: str = "",
    column_families: str = "",
    limit: int = 50,
) -> dict:
    """operation: 操作类型 scan/get/count/put/delete/list/create/desc/disable/enable/drop/truncate
    table_name: HBase 表名
    row_key: 行键（get/put/delete 必填）
    start_row: scan 起始行键（含）
    stop_row: scan 结束行键（不含）
    filter_expr: 简单过滤表达式，如 "cf:status = 'completed'"
    column: 写入/删除的列名（put/delete 使用），格式 cf:qualifier
    value: 写入的值（仅 put 使用）
    column_families: 列族列表，逗号分隔（仅 create 使用），如 "cf" 或 "info,behavior"
    limit: scan 最大返回行数，默认 50"""
    op = (operation or "").strip().lower()
    tbl = (table_name or "").strip()

    if op == "list":
        return {
            "tables": list(_HBASE_META.keys()),
            "operation": "list",
        }

    if op not in ("scan", "get", "count", "put", "delete", "create", "desc", "disable", "enable", "drop", "truncate"):
        return {"error": True, "message": f"不支持的操作: '{operation}'",
                "suggestion": "可用: scan, get, count, put, delete, list, create, desc, disable, enable, drop, truncate"}

    # ── HITL: 破坏性操作需人工审批 ──
    from harness.constraints.entitlement import needs_approval_hbase
    if needs_approval_hbase(op):
        try:
            from langgraph.types import interrupt
            decision = interrupt({
                "type": "hitl_approval",
                "tool": "run_hbase",
                "operation": op,
                "table_name": tbl,
                "row_key": row_key or None,
                "column": column or None,
                "value": value or None,
                "message": (
                    f"HBase 写操作需要审批。\n"
                    f"操作: {op}\n"
                    f"表: {tbl}\n"
                    f"行键: {row_key or '—'}\n"
                    f"列: {column or '—'}\n"
                    f"值: {value or '—'}"
                ),
            })
            if isinstance(decision, dict) and not decision.get("approved"):
                return {"error": True, "message": "用户拒绝了该操作", "operation": op, "table_name": tbl}
        except (RuntimeError, ImportError):
            return {"error": True, "message": "HBase 写操作需要管理员审批",
                    "operation": op, "table_name": tbl,
                    "hint": "put/delete/drop/truncate 操作需在 graph 上下文中执行以触发审批流程"}

    # ── DDL 操作：不需要表已存在 ──
    if op == "create":
        if not tbl:
            return {"error": True, "message": "create 操作需要 table_name"}
        if not column_families:
            return {"error": True, "message": "create 操作需要 column_families，如 'cf' 或 'info,behavior'"}
        if tbl in _HBASE_STORE:
            return {"error": True, "message": f"表 '{tbl}' 已存在"}
        cfs = [cf.strip() for cf in column_families.split(",") if cf.strip()]
        if not cfs:
            return {"error": True, "message": "column_families 不能为空"}
        _HBASE_META[tbl] = cfs
        _HBASE_STORE[tbl] = {}
        _HBASE_ROW_ORDER[tbl] = []
        return {"rows": [], "count": 0, "operation": "create",
                "hint": f"表 '{tbl}' 创建成功，列族: {cfs}"}

    if op == "desc":
        if not tbl:
            return {"error": True, "message": "desc 操作需要 table_name"}
        if tbl not in _HBASE_META:
            return {"error": True, "message": f"表 '{tbl}' 不存在",
                    "suggestion": f"可用表: {', '.join(sorted(_HBASE_META.keys()))}"}
        return {
            "table": tbl,
            "column_families": _HBASE_META[tbl],
            "row_count": len(_HBASE_STORE.get(tbl, {})),
            "disabled": tbl in _HBASE_DISABLED,
            "operation": "desc",
        }

    # ── 以下操作需要表存在 ──
    if not tbl:
        return {"error": True, "message": "缺少 table_name 参数"}

    if op == "disable":
        if tbl not in _HBASE_META:
            return {"error": True, "message": f"表 '{tbl}' 不存在",
                    "suggestion": f"可用表: {', '.join(sorted(_HBASE_META.keys()))}"}
        _HBASE_DISABLED.add(tbl)
        return {"rows": [], "count": 0, "operation": "disable",
                "hint": f"表 '{tbl}' 已禁用。drop 或 truncate 前需先 disable。"}

    if op == "enable":
        if tbl not in _HBASE_META:
            return {"error": True, "message": f"表 '{tbl}' 不存在",
                    "suggestion": f"可用表: {', '.join(sorted(_HBASE_META.keys()))}"}
        _HBASE_DISABLED.discard(tbl)
        return {"rows": [], "count": 0, "operation": "enable",
                "hint": f"表 '{tbl}' 已启用。"}

    if op == "drop":
        if tbl not in _HBASE_META:
            return {"error": True, "message": f"表 '{tbl}' 不存在",
                    "suggestion": f"可用表: {', '.join(sorted(_HBASE_META.keys()))}"}
        if tbl not in _HBASE_DISABLED:
            return {"error": True, "message": f"表 '{tbl}' 未禁用。drop 前必须先 disable。",
                    "suggestion": f"请先执行: disable '{tbl}'"}
        del _HBASE_STORE[tbl]
        del _HBASE_META[tbl]
        del _HBASE_ROW_ORDER[tbl]
        _HBASE_DISABLED.discard(tbl)
        return {"rows": [], "count": 0, "operation": "drop",
                "hint": f"表 '{tbl}' 已删除（不可恢复）。"}

    if op == "truncate":
        if tbl not in _HBASE_META:
            return {"error": True, "message": f"表 '{tbl}' 不存在",
                    "suggestion": f"可用表: {', '.join(sorted(_HBASE_META.keys()))}"}
        if tbl not in _HBASE_DISABLED:
            return {"error": True, "message": f"表 '{tbl}' 未禁用。truncate 前必须先 disable。",
                    "suggestion": f"请先执行: disable '{tbl}'"}
        _HBASE_STORE[tbl] = {}
        _HBASE_ROW_ORDER[tbl] = []
        return {"rows": [], "count": 0, "operation": "truncate",
                "hint": f"表 '{tbl}' 已清空，表结构和列族保留。"}

    # ── 以下操作需要表在 _HBASE_STORE 中存在 ──
    if tbl not in _HBASE_STORE:
        return {"error": True, "message": f"表 '{tbl}' 不存在",
                "suggestion": f"可用表: {', '.join(sorted(_HBASE_STORE.keys()))}"}

    store = _HBASE_STORE[tbl]
    row_order = _HBASE_ROW_ORDER[tbl]

    if op == "get":
        if not row_key:
            return {"error": True, "message": "get 操作需要 row_key"}
        if row_key not in store:
            return {"rows": [], "count": 0, "operation": "get",
                    "hint": f"行键 '{row_key}' 不存在"}
        return {"rows": [{"row_key": row_key, "columns": dict(store[row_key])}],
                "count": 1, "operation": "get"}

    if op == "put":
        if not row_key or not column:
            return {"error": True, "message": "put 操作需要 row_key 和 column"}
        if not value:
            return {"error": True, "message": "put 操作需要 value"}
        if ":" not in column:
            return {"error": True, "message": "column 格式应为 '列族:限定符'，如 'cf:name'"}
        if tbl not in _HBASE_STORE:
            _HBASE_STORE[tbl] = {}
            _HBASE_ROW_ORDER[tbl] = []
        if row_key not in _HBASE_STORE[tbl]:
            _HBASE_STORE[tbl][row_key] = {}
            _HBASE_ROW_ORDER[tbl].append(row_key)
        _HBASE_STORE[tbl][row_key][column] = value
        return {"rows": [{"row_key": row_key, "columns": {column: value}}],
                "count": 1, "operation": "put", "hint": "写入成功。"}

    if op == "delete":
        if not row_key:
            return {"error": True, "message": "delete 操作需要 row_key"}
        if row_key not in store:
            return {"error": True, "message": f"行键 '{row_key}' 不存在"}
        if column:
            if column in store[row_key]:
                del store[row_key][column]
                return {"rows": [], "count": 0, "operation": "delete",
                        "hint": f"已删除 {row_key} 的 {column}"}
            return {"error": True, "message": f"列 '{column}' 不存在于行 '{row_key}'"}
        del store[row_key]
        row_order.remove(row_key)
        return {"rows": [], "count": 0, "operation": "delete",
                "hint": f"已删除行 '{row_key}'（整行）"}

    if op == "count":
        total = len(store)
        if filter_expr:
            fn = _parse_simple_filter(filter_expr)
            if fn:
                total = sum(1 for cols in store.values() if fn(cols))
        return {"rows": [], "count": total, "operation": "count"}

    # scan
    rows = [(rk, dict(store[rk])) for rk in row_order if rk in store]

    # 范围过滤
    if start_row:
        rows = [(rk, cols) for rk, cols in rows if rk >= start_row]
    if stop_row:
        rows = [(rk, cols) for rk, cols in rows if rk < stop_row]

    # 前缀过滤（通过 start/stop 模拟或用 filter_expr）
    if filter_expr:
        # 先试前缀: PrefixFilter("xxx") 格式
        pf = re.match(r"PrefixFilter\('([^']+)'\)", filter_expr)
        if pf:
            rows = _apply_prefix_filter(rows, pf.group(1))
        else:
            fn = _parse_simple_filter(filter_expr)
            if fn:
                rows = [(rk, cols) for rk, cols in rows if fn(cols)]

    scanned = len(rows)
    rows = rows[:limit]

    return {
        "rows": [{"row_key": rk, "columns": cols} for rk, cols in rows],
        "count": len(rows),
        "scanned_rows": scanned,
        "truncated": scanned > limit,
        "operation": "scan",
        "hint": f"扫描 {scanned} 行，返回前 {len(rows)} 行" if scanned > limit else None,
    }


# ═══════════════════════════════════════════════════════════════════
# HBase Shell 命令生成（generate_hbase_query）
# ═══════════════════════════════════════════════════════════════════

_HBASE_OPERATIONS = {
    "scan": {
        "syntax": "scan '{table_name}'{columns}{filter}{limit}",
        "required_params": [],
        "optional_params": ["columns", "filter_description", "limit"],
        "description": "扫描表，返回多行。可加列族过滤、行键范围、FILTER、LIMIT。",
        "examples": [
            "scan 'orders'",
            "scan 'orders', {COLUMNS => 'cf:total', LIMIT => 10}",
            "scan 'orders', {FILTER => \"SingleColumnValueFilter('cf', 'status', =, 'binary:completed')\"}",
        ],
        "warning": "scan 全表可能非常慢。生产环境务必加 FILTER 或 STARTROW/STOPROW 限制扫描范围。",
    },
    "get": {
        "syntax": "get '{table_name}', '{row_key}'{columns}",
        "required_params": ["row_key"],
        "optional_params": ["columns"],
        "description": "按行键精确读取一行。",
        "examples": [
            "get 'orders', 'row_001'",
            "get 'orders', 'row_001', {COLUMNS => 'cf:total'}",
        ],
    },
    "count": {
        "syntax": "count '{table_name}'{filter}",
        "required_params": [],
        "optional_params": ["filter_description"],
        "description": "统计表的行数。可加 FILTER 只统计符合条件的行。",
        "examples": [
            "count 'orders'",
            "count 'orders', FILTER => \"PrefixFilter('2025-01')\"",
        ],
    },
    "put": {
        "syntax": "put '{table_name}', '{row_key}', '{column}', '{value}'",
        "required_params": ["row_key", "columns", "value"],
        "optional_params": [],
        "description": "插入或更新一个单元格的值。列格式: '列族:限定符'。",
        "examples": [
            "put 'orders', 'row_001', 'cf:status', 'completed'",
        ],
        "warning": "⚠️ 写操作。生成后请人工确认再执行。",
    },
    "delete": {
        "syntax": "delete '{table_name}', '{row_key}', '{column}'",
        "required_params": ["row_key", "columns"],
        "optional_params": [],
        "description": "删除指定行的一个单元格。不指定列则删除整行（用 deleteall）。",
        "examples": [
            "delete 'orders', 'row_001', 'cf:status'",
            "deleteall 'orders', 'row_001'",
        ],
        "warning": "⚠️ 删除操作。生成后请人工确认再执行。",
    },
    "list": {
        "syntax": "list{pattern}",
        "required_params": [],
        "optional_params": [],
        "description": "列出所有表。可用正则过滤：list 'orders.*'。",
        "examples": [
            "list",
            "list 'orders_.*'",
        ],
    },
    "create": {
        "syntax": "create '{table_name}', '{column_families}'",
        "required_params": ["column_families"],
        "optional_params": [],
        "description": "创建新表并指定列族。多个列族用逗号分隔。可选 VERSIONS、TTL 等属性。",
        "examples": [
            "create 'orders', 'cf'",
            "create 'users', 'info', 'behavior', {VERSIONS => 3}",
        ],
    },
    "desc": {
        "syntax": "describe '{table_name}'",
        "required_params": [],
        "optional_params": [],
        "description": "查看表结构（列族、VERSIONS、TTL 等属性）。",
        "examples": [
            "describe 'orders'",
        ],
    },
    "disable": {
        "syntax": "disable '{table_name}'",
        "required_params": [],
        "optional_params": [],
        "description": "禁用表（drop 前必须先 disable）。",
        "examples": ["disable 'orders'"],
        "warning": "⚠️ 禁用后表不可读写。",
    },
    "enable": {
        "syntax": "enable '{table_name}'",
        "required_params": [],
        "optional_params": [],
        "description": "启用之前 disable 的表。",
        "examples": ["enable 'orders'"],
    },
    "drop": {
        "syntax": "disable '{table_name}'\ndrop '{table_name}'",
        "required_params": [],
        "optional_params": [],
        "description": "删除表（需先 disable）。不可恢复。",
        "examples": ["先 disable 'orders' 再 drop 'orders'"],
        "warning": "⚠️ 不可恢复操作。生成后请人工确认再执行。",
    },
    "truncate": {
        "syntax": "truncate '{table_name}'",
        "required_params": [],
        "optional_params": [],
        "description": "清空表数据但保留表结构（需先 disable）。",
        "examples": ["先 disable 'orders' 再 truncate 'orders'"],
        "warning": "⚠️ 清空全部数据，不可恢复。",
    },
}

_HBASE_FILTERS = {
    "RowFilter": (
        "按行键过滤。",
        "RowFilter(<operator>, '<comparator>:<pattern>')",
        "RowFilter(=, 'regexstring:^2025')  ← 行键以 2025 开头的行",
    ),
    "SingleColumnValueFilter": (
        "按列值过滤——最常用的 filter。",
        "SingleColumnValueFilter('<family>', '<qualifier>', <op>, '<comparator>:<value>')",
        "SingleColumnValueFilter('cf', 'status', =, 'binary:completed')  ← status=completed 的行",
    ),
    "PrefixFilter": (
        "按行键前缀过滤。",
        "PrefixFilter('<prefix>')",
        "PrefixFilter('user_')  ← 行键以 user_ 开头的所有行",
    ),
    "ColumnPrefixFilter": (
        "按列名（qualifier）前缀过滤。",
        "ColumnPrefixFilter('<prefix>')",
        "ColumnPrefixFilter('event_')  ← 列名以 event_ 开头的列",
    ),
    "KeyOnlyFilter": (
        "只返回行键，不返回列值——适合快速扫行键列表。",
        "KeyOnlyFilter()",
        "scan 'orders', {FILTER => \"KeyOnlyFilter()\"}  ← 只返回行键",
    ),
    "ValueFilter": (
        "按值过滤（不限定列，全表扫描值）。",
        "ValueFilter(<op>, '<comparator>:<value>')",
        "ValueFilter(=, 'substring:error')  ← 任何列的值包含 error 的行",
    ),
    "FamilyFilter": (
        "按列族过滤。",
        "FamilyFilter(<op>, '<comparator>:<family>')",
        "FamilyFilter(=, 'binary:cf')  ← 只返回 cf 列族",
    ),
    "QualifierFilter": (
        "按列限定符过滤。",
        "QualifierFilter(<op>, '<comparator>:<qualifier>')",
        "QualifierFilter(=, 'binary:total')  ← 只返回列名为 total 的列",
    ),
    "FirstKeyOnlyFilter": (
        "只返回每行的第一个 cell——适合统计行数的场景。",
        "FirstKeyOnlyFilter()",
        "scan 'orders', {FILTER => \"FirstKeyOnlyFilter()\"}  ← 快速行计数",
    ),
    "PageFilter": (
        "限制返回行数。",
        "PageFilter(<n>)",
        "PageFilter(100)  ← 最多返回 100 行",
    ),
    "TimestampsFilter": (
        "按时间戳范围过滤。",
        "TimestampsFilter([<ts1>, <ts2>, ...])",
        "TimestampsFilter([1620000000000, 1620086400000])",
    ),
}

_FILTER_OPERATORS = {"=": "=", "!=": "!=", ">": ">", ">=": ">=", "<": "<", "<=": "<="}
_FILTER_COMPARATORS = {
    "binary": "binary:<value>  ——  精确匹配字节",
    "binaryprefix": "binaryprefix:<prefix>  ——  前缀匹配",
    "substring": "substring:<substr>  ——  包含子串（不区分大小写）",
    "regexstring": "regexstring:<regex>  ——  正则匹配",
}


def _match_filter(filter_desc: str) -> str:
    """自然语言 filter 描述 → HBase filter 语法提示。"""
    desc_lower = (filter_desc or "").lower()
    matches = []
    for name, (explanation, syntax, example) in _HBASE_FILTERS.items():
        if name.lower() in desc_lower:
            matches.append(f"→ {name}: {syntax}\n  例: {example}")
            break
        keywords = name.replace("Filter", "").lower()
        if keywords in desc_lower:
            matches.append(f"→ {name}: {syntax}\n  例: {example}")
            break

    if not matches:
        if any(w in desc_lower for w in ("行键", "rowkey", "row key", "起始行", "开头")):
            matches.append(f"→ RowFilter / PrefixFilter\n  PrefixFilter: {_HBASE_FILTERS['PrefixFilter'][1]}")
        if any(w in desc_lower for w in ("列值", "column value", "等于", "status", "状态")):
            matches.append(f"→ SingleColumnValueFilter\n  {_HBASE_FILTERS['SingleColumnValueFilter'][1]}")
        if any(w in desc_lower for w in ("包含", "contain", "substring", "like")):
            matches.append("→ ValueFilter 或 SingleColumnValueFilter + substring")

    if not matches:
        return "未匹配到具体 filter 类型。"

    op_ref = " | ".join(_FILTER_OPERATORS.keys())
    comp_ref = "; ".join(_FILTER_COMPARATORS.values())
    return "\n".join(matches) + f"\n\n可用操作符: {op_ref}\n可用比较器: {comp_ref}"


def _build_columns_clause(columns: list[str] | None) -> str:
    """columns → HBase COLUMNS 子句。"""
    if not columns:
        return ""
    valid = [c for c in columns if ":" in c]
    if not valid:
        return ""
    cols = "', '".join(valid)
    return f", {{COLUMNS => ['{cols}']}}"


@tool(description=(
    "生成 HBase Shell 命令。HBase 不是 SQL 数据库——它有自己的 Shell 语法。\n"
    "支持 scan（扫描表）、get（读单行）、count（统计行数）、put（写入）、"
    "delete（删除）、list（列所有表）、create（建表）、desc（表描述）等。\n"
    "返回 JSON: {command, operation, explanation, notes, filter_explanation}。\n"
    "operation 不合法时返回 error 及可用操作列表。\n"
    "write 操作（put/delete/create/drop/truncate）返回 warning 提示。"
))
def generate_hbase_query(
    operation: str,
    table_name: str,
    row_key: str = "",
    filter_description: str = "",
    columns: list[str] = None,
    value: str = "",
    column_families: str = "",
    limit: int = 0,
) -> dict:
    """operation: HBase 操作类型（scan/get/count/put/delete/list/create/desc/disable/enable/drop/truncate）
    table_name: HBase 表名
    row_key: 行键（get/put/delete 操作必填）
    filter_description: 自然语言描述过滤条件，如 "行键以 user_ 开头" 或 "status 列值等于 completed"
    columns: 列族:列名 列表，如 ['cf:name', 'cf:total']
    value: 写入的值（仅 put 操作使用）
    column_families: 列族名，逗号分隔（仅 create 操作使用）
    limit: 返回行数限制（仅 scan 操作）"""
    op = (operation or "").strip().lower()
    if not op:
        return {"error": True, "message": "缺少 operation 参数",
                "suggestion": f"可用的操作: {', '.join(sorted(_HBASE_OPERATIONS.keys()))}"}

    if op not in _HBASE_OPERATIONS:
        return {
            "error": True,
            "message": f"不支持的操作: '{operation}'",
            "suggestion": f"可用的操作: {', '.join(sorted(_HBASE_OPERATIONS.keys()))}",
            "hint": "HBase Shell 大小写敏感——操作名用小写。",
        }

    if not (table_name or "").strip():
        if op not in ("list",):
            return {"error": True, "message": "缺少 table_name 参数"}

    tmpl = _HBASE_OPERATIONS[op]

    missing = []
    if "row_key" in tmpl["required_params"] and not row_key:
        missing.append("row_key")
    if "columns" in tmpl["required_params"] and not columns:
        missing.append("columns (列族:限定符)" if op == "put" else "columns")
    if "column_families" in tmpl["required_params"] and not column_families:
        missing.append("column_families")
    if missing:
        return {"error": True, "message": f"缺少必填参数: {', '.join(missing)}",
                "suggestion": f"示例: {tmpl['examples'][0]}"}

    t = table_name.strip()
    cols_clause = _build_columns_clause(columns) if columns else ""
    filter_info = ""
    filter_section = ""

    if filter_description and filter_description.strip():
        filter_info = _match_filter(filter_description.strip())
        filter_section = ", {FILTER => \"<见 filter_explanation>\"}"

    limit_clause = f", {{LIMIT => {limit}}}" if limit and limit > 0 else ""

    if op in ("get",):
        command = f"get '{t}', '{row_key}'{cols_clause}"
    elif op in ("put",):
        col_str = "', '".join(columns) if columns else "?:?"
        command = f"put '{t}', '{row_key}', '{col_str}', '{value}'"
    elif op in ("delete",):
        if columns and len(columns) == 1:
            command = f"delete '{t}', '{row_key}', '{columns[0]}'"
        else:
            command = f"deleteall '{t}', '{row_key}'"
    elif op == "create":
        command = f"create '{t}', '{column_families.strip()}'"
    elif op == "drop":
        command = f"disable '{t}'\ndrop '{t}'"
    elif op == "truncate":
        command = f"disable '{t}'\ntruncate '{t}'"
    elif op == "disable":
        command = f"disable '{t}'"
    elif op == "enable":
        command = f"enable '{t}'"
    elif op == "list":
        command = "list" if not t else f"list '{t}'"
    elif op == "desc":
        command = f"describe '{t}'"
    elif op == "count":
        filter_suffix = ", FILTER => \"<见 filter_explanation>\"" if filter_description and filter_description.strip() else ""
        command = f"count '{t}'{filter_suffix}"
    else:
        command = f"scan '{t}'{limit_clause}{filter_section}"

    result = {
        "command": command,
        "operation": op,
        "explanation": tmpl["description"],
        "notes": f"执行环境: HBase Shell ({'写入' if op in ('put','delete','deleteall','create','drop','truncate','disable') else '只读'})",
    }
    if filter_info:
        result["filter_explanation"] = filter_info
    if op in ("scan",) and not filter_description:
        result["warning"] = tmpl.get("warning", "")
    if op in tmpl["optional_params"] and "warning" in tmpl:
        result["warning"] = tmpl["warning"]
    if limit and limit > 0:
        result["notes"] += f"\n已限制返回行数: LIMIT {limit}"

    return result
