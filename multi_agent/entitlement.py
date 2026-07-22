"""企业级 Agent 权限网关 — 统一控制数据库、文档、工具的访问权限。

设计原则:
    1. 不解析 SQL — 正则不可靠，交给数据库原生权限或 SQLite 限制
    2. 资源级控制 — 表/列/文档/工具，每种资源一个过滤规则
    3. 两层生效 — system prompt 软约束 + tool 层硬拦截
    4. 行级改写保留 — 唯一需要动 SQL 的场景是自动追加 WHERE dept_id=X

权限模型:
    role → {
        allowed_tools:   能调哪些工具
        db_tables:       能查哪些表 (null = 全部, ["employees", "products"] = 白名单)
        db_row_filter:   行级过滤 ({"employees": "dept_id"}) → 自动加 WHERE
        docs_filter:     能搜哪些文档 (null = 全部, ["sales_policy"] = 白名单)
        sensitive_check: 是否触发敏感列 HITL 审批
    }

用户模型:
    dba       — 研发部 DBA，全权限
    manager   — 部门经理，本部门数据 + 全文档
    analyst   — 数据分析师，无 salary/cost/budget 列，全文档
    viewer    — 访客，受限表 + 受限文档
    support   — 技术支持，只能看 products 表 + 技术文档

用法:
    from multi_agent.entitlement import get_user, authorize_tool, filter_tables, rewrite_sql

    user = get_user("xiaoyiming")
    passed, reason = authorize_tool(user, "run_query")     # 能不能调这个工具
    tables = filter_tables(user, all_tables)                # 能看哪些表
    sql = rewrite_sql(user, sql)                            # 行级过滤
"""

from typing import Optional

# ═══════════════════════════════════════════════════════════════════════════════
# 角色定义
# ═══════════════════════════════════════════════════════════════════════════════

ROLES: dict[str, dict] = {
    "dba": {
        "name": "研发DBA",
        "allowed_tools": ["run_query", "list_tables", "describe_table",
                          "search_knowledge_base", "read_document", "write_query"],
        "db_tables": None,              # null = 全部
        "db_row_filter": None,          # 不行级过滤
        "docs_filter": None,
        "sensitive_check": False,       # 不触发 HITL
    },
    "manager": {
        "name": "部门经理",
        "allowed_tools": ["run_query", "list_tables", "describe_table",
                          "search_knowledge_base", "read_document"],
        "db_tables": None,              # 所有表
        "db_row_filter": {"employees": "dept_id"},   # 自动追加 WHERE dept_id=X
        "docs_filter": None,
        "sensitive_check": True,        # 查 salary 触发 HITL
    },
    "analyst": {
        "name": "数据分析师",
        "allowed_tools": ["run_query", "list_tables", "describe_table",
                          "search_knowledge_base", "read_document"],
        "db_tables": ["departments", "employees", "products", "customers", "orders"],
        "db_row_filter": None,
        "docs_filter": None,
        "sensitive_check": True,
    },
    "viewer": {
        "name": "访客",
        "allowed_tools": ["list_tables", "describe_table",
                          "search_knowledge_base", "read_document"],
        "db_tables": ["departments", "products", "customers", "orders"],
        "docs_filter": ["产品手册", "部门介绍", "销售制度"],
        "sensitive_check": False,
    },
    "support": {
        "name": "技术支持",
        "allowed_tools": ["run_query", "list_tables", "describe_table",
                          "search_knowledge_base", "read_document"],
        "db_tables": ["products", "customers", "orders"],
        "db_row_filter": None,
        "docs_filter": ["技术文档", "产品手册"],
        "sensitive_check": False,
    },
}


# ═══════════════════════════════════════════════════════════════════════════════
# 用户定义（每个用户绑定一个角色 + 部门）
# ═══════════════════════════════════════════════════════════════════════════════

USERS: dict[str, dict] = {
    "dba":            {"name": "研发DBA",   "role": "dba",     "dept_id": 3},
    "zhoufang":       {"name": "周芳",      "role": "manager", "dept_id": 1},   # 销售部
    "xiaoyiming":     {"name": "萧一鸣",    "role": "manager", "dept_id": 2},   # 市场部
    "gaoyong":        {"name": "高勇",      "role": "manager", "dept_id": 3},   # 研发部
    "linyi":          {"name": "林怡",      "role": "manager", "dept_id": 4},   # 财务部
    "liangming":      {"name": "梁明",      "role": "manager", "dept_id": 5},   # 人事部
    "lujie":          {"name": "卢杰",      "role": "manager", "dept_id": 6},   # 产品部
    "analyst":        {"name": "数据分析师","role": "analyst",  "dept_id": None},
    "viewer":         {"name": "访客",      "role": "viewer",   "dept_id": None},
    "support":        {"name": "技术支持",  "role": "support",  "dept_id": None},
}

_DEFAULT_USER = "analyst"


# ═══════════════════════════════════════════════════════════════════════════════
# 公共 API
# ═══════════════════════════════════════════════════════════════════════════════

def get_user(user_id: Optional[str] = None) -> dict:
    """获取用户完整权限信息。未指定时默认 analyst。"""
    user = USERS.get(user_id) if user_id else None
    if not user:
        user = USERS[_DEFAULT_USER]
    role = ROLES[user["role"]]
    return {**user, "permissions": role}


def list_users() -> list[dict]:
    """列出所有用户。"""
    return [{"id": uid, "name": u["name"], "role": u["role"]} for uid, u in USERS.items()]


def list_roles() -> list[dict]:
    """列出所有角色及权限。"""
    return [{"role": rid, "name": r["name"], "tools": r["allowed_tools"],
             "table_count": len(r["db_tables"]) if r["db_tables"] else "全部",
             "row_filter": r["db_row_filter"] is not None}
            for rid, r in ROLES.items()]


# ═══════════════════════════════════════════════════════════════════════════════
# 工具授权
# ═══════════════════════════════════════════════════════════════════════════════

def authorize_tool(user: dict, tool_name: str) -> tuple[bool, str]:
    """检查用户能否调用此工具。Layer 2 硬拦截入口。"""
    perms = user.get("permissions", {})
    allowed = perms.get("allowed_tools", [])
    if tool_name in allowed:
        return True, ""
    return False, f"您的角色（{user.get('name')}）无权使用 {tool_name} 工具。"


# ═══════════════════════════════════════════════════════════════════════════════
# 数据库权限
# ═══════════════════════════════════════════════════════════════════════════════

def filter_tables(user: dict, tables: list[str]) -> list[str]:
    """表级过滤：返回用户能看到的表列表。list_tables / describe_table 调用前过滤。"""
    perms = user.get("permissions", {})
    allowed = perms.get("db_tables")
    if allowed is None:
        return tables
    return [t for t in tables if t in allowed]


def check_table_access(user: dict, table: str) -> tuple[bool, str]:
    """表级权限：单表检查。run_query 调用前检查。"""
    perms = user.get("permissions", {})
    allowed = perms.get("db_tables")
    if allowed is None:
        return True, ""
    if table in allowed:
        return True, ""
    return False, f"您无权访问 {table} 表。（角色: {user['name']}）"


def rewrite_sql(user: dict, sql: str) -> str:
    """行级安全：自动追加 WHERE dept_id=X。

    例: 萧一鸣（市场部经理 dept_id=2）:
      SELECT name, salary FROM employees
      → SELECT name, salary FROM employees WHERE dept_id = 2

    只处理简单情况——生产环境应使用数据库 RLS Policy。
    """
    perms = user.get("permissions", {})
    rules = perms.get("db_row_filter") or {}

    tables = _extract_table_names(sql)
    if not tables:
        return sql

    for table in tables:
        if table not in rules:
            continue
        column = rules[table]
        value = user.get("dept_id")
        if value is None:
            continue

        clause = f"{column} = {value}"
        if "WHERE" in sql.upper():
            # 在 GROUP BY / ORDER BY / LIMIT 之前插入 AND
            for kw in ["GROUP BY", "ORDER BY", "LIMIT", "HAVING"]:
                if kw in sql.upper():
                    pos = sql.upper().index(kw)
                    return sql[:pos] + f"AND {clause} " + sql[pos:]
            return sql + f" AND {clause}"
        else:
            for kw in ["GROUP BY", "ORDER BY", "LIMIT", "HAVING"]:
                if kw in sql.upper():
                    pos = sql.upper().index(kw)
                    return sql[:pos] + f"WHERE {clause} " + sql[pos:]
            return sql + f" WHERE {clause}"

    return sql


def _extract_table_names(sql: str) -> list[str]:
    """从 SQL 提取表名——只处理 FROM/JOIN，不做完整解析。"""
    import re
    return re.findall(r'(?:FROM|JOIN)\s+(\w+)', sql, re.IGNORECASE)


# ═══════════════════════════════════════════════════════════════════════════════
# 文档权限
# ═══════════════════════════════════════════════════════════════════════════════

def filter_docs(user: dict, docs: list[dict]) -> list[dict]:
    """文档过滤：返回用户能搜到的文档。search_knowledge_base 调用前过滤。"""
    perms = user.get("permissions", {})
    allowed = perms.get("docs_filter")
    if allowed is None:
        return docs
    return [d for d in docs
            if any(cat in d.get("title", "") + d.get("category", "") for cat in allowed)]


# ═══════════════════════════════════════════════════════════════════════════════
# HITL 审批判断
# ═══════════════════════════════════════════════════════════════════════════════

# 敏感列——涉及这些列时需要人工审批
SENSITIVE_COLUMNS = {"salary", "cost", "budget"}


def needs_approval(user: dict, sql: str) -> bool:
    """检查 SQL 是否涉及敏感列，且用户角色需要 HITL。"""
    perms = user.get("permissions", {})
    if not perms.get("sensitive_check"):
        return False
    sql_lower = sql.lower()
    return any(col in sql_lower for col in SENSITIVE_COLUMNS)


# ═══════════════════════════════════════════════════════════════════════════════
# System Prompt 注入（Layer 1 软约束）
# ═══════════════════════════════════════════════════════════════════════════════

def build_permission_context(user: dict) -> str:
    """生成注入 system prompt 的权限声明。告诉 Agent 它的边界，减少无效调用。"""
    perms = user.get("permissions", {})
    rows = []

    rows.append(f"当前用户: {user['name']} ({user['role']})")

    # 可用工具
    tools = perms.get("allowed_tools", [])
    rows.append(f"可用工具: {', '.join(tools)}")

    # 数据范围
    tables = perms.get("db_tables")
    if tables:
        rows.append(f"可访问表: {', '.join(tables)}")
    else:
        rows.append("可访问表: 全部")

    # 行级过滤
    row_filter = perms.get("db_row_filter")
    dept_id = user.get("dept_id")
    if row_filter and dept_id:
        for tbl, col in row_filter.items():
            rows.append(f"数据范围限制: {tbl} 表仅返回 {col}={dept_id} 的行（部门数据隔离）")

    # 审批提示
    if perms.get("sensitive_check"):
        rows.append("注意: 查询 salary/cost/budget 等敏感列将触发人工审批")

    return "\n".join(rows)
