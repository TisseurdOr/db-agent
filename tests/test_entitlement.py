"""权限网关专项测试 —— 角色、表级、行级、敏感列 HITL、用户解析。

全部零 API 成本：只测 entitlement.py 的纯逻辑，不调用 LLM。
"""

import pytest

from db.seed import init_db
from harness.constraints.entitlement import (
    authorize_tool,
    check_entitlement,
    check_entitlement_by_role,
    check_table_access,
    deny_payload,
    get_user,
    needs_approval,
    needs_approval_hbase,
    reload,
    resolve_user_id,
    rewrite_sql,
)


@pytest.fixture(autouse=True)
def fresh_entitlement_db(monkeypatch):
    """每个测试前重置 demo.db 并重新加载权限表。"""
    init_db(reset=True)
    # 模块导入时可能读过旧的/不存在的 DB；reset 后强制 reload
    reload()
    # 避免 AGENT_USER 被之前测试污染
    monkeypatch.delenv("AGENT_USER", raising=False)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. 用户解析
# ═══════════════════════════════════════════════════════════════════════════════

def test_resolve_user_id_defaults_to_viewer():
    assert resolve_user_id() == "viewer"
    assert resolve_user_id(None) == "viewer"


def test_resolve_user_id_from_env(monkeypatch):
    monkeypatch.setenv("AGENT_USER", "analyst")
    assert resolve_user_id() == "analyst"


def test_resolve_user_id_explicit_overrides_env(monkeypatch):
    monkeypatch.setenv("AGENT_USER", "viewer")
    assert resolve_user_id("dba") == "dba"


def test_get_user_unknown_fallback():
    user = get_user("not_exist_user")
    assert user["role"] == "viewer"


def test_get_user_manager_has_dept_id():
    user = get_user("xiaoyiming")
    assert user["name"] == "萧一鸣"
    assert user["role"] == "manager"
    assert user["dept_id"] == 2


# ═══════════════════════════════════════════════════════════════════════════════
# 2. 工具授权
# ═══════════════════════════════════════════════════════════════════════════════

def test_authorize_tool_dba_can_run_query():
    user = get_user("dba")
    assert authorize_tool(user, "run_query").passed


def test_viewer_cannot_run_query():
    user = get_user("viewer")
    result = authorize_tool(user, "run_query")
    assert not result.passed
    assert "无权使用" in result.reason


def test_analyst_can_use_hbase():
    user = get_user("analyst")
    assert authorize_tool(user, "run_hbase").passed


def test_manager_cannot_use_hbase():
    user = get_user("zhoufang")
    assert not authorize_tool(user, "run_hbase").passed


# ═══════════════════════════════════════════════════════════════════════════════
# 3. 表级权限
# ═══════════════════════════════════════════════════════════════════════════════

def test_analyst_can_access_business_tables():
    user = get_user("analyst")
    for table in ("employees", "orders", "ods_orders_hive"):
        assert check_table_access(user, table).passed, table


def test_viewer_cannot_access_employees():
    user = get_user("viewer")
    result = check_table_access(user, "employees")
    assert not result.passed
    assert "employees" in result.reason


def test_support_narrow_table_whitelist():
    user = get_user("support")
    assert check_table_access(user, "orders").passed
    assert not check_table_access(user, "employees").passed


def test_dba_unlimited_tables():
    user = get_user("dba")
    assert check_table_access(user, "any_table").passed


# ═══════════════════════════════════════════════════════════════════════════════
# 4. 行级 SQL 改写
# ═══════════════════════════════════════════════════════════════════════════════

def test_rewrite_sql_adds_dept_filter_for_manager():
    user = get_user("xiaoyiming")  # manager dept_id=2
    sql = "SELECT name, salary FROM employees"
    rewritten = rewrite_sql(user, sql)
    assert "dept_id = 2" in rewritten


def test_rewrite_sql_keeps_existing_where():
    user = get_user("xiaoyiming")
    sql = "SELECT name FROM employees WHERE status = 'active'"
    rewritten = rewrite_sql(user, sql)
    assert "status = 'active'" in rewritten
    assert "dept_id = 2" in rewritten


def test_rewrite_sql_no_filter_for_dba():
    user = get_user("dba")
    sql = "SELECT * FROM employees"
    assert rewrite_sql(user, sql) == sql


def test_rewrite_sql_no_filter_for_analyst():
    user = get_user("analyst")
    sql = "SELECT * FROM employees"
    assert rewrite_sql(user, sql) == sql


def test_rewrite_sql_unrelated_table_not_altered():
    user = get_user("xiaoyiming")
    sql = "SELECT * FROM departments"
    assert rewrite_sql(user, sql) == sql


# ═══════════════════════════════════════════════════════════════════════════════
# 5. 敏感列 HITL
# ═══════════════════════════════════════════════════════════════════════════════

def test_salary_query_needs_approval_for_analyst():
    user = get_user("analyst")
    assert needs_approval(user, "SELECT name, salary FROM employees")


def test_cost_query_needs_approval():
    user = get_user("analyst")
    assert needs_approval(user, "SELECT cost FROM products")


def test_budget_query_needs_approval():
    user = get_user("analyst")
    assert needs_approval(user, "SELECT budget FROM departments")


def test_non_sensitive_query_no_approval():
    user = get_user("analyst")
    assert not needs_approval(user, "SELECT name FROM departments")


def test_check_entitlement_returns_approval_flag_for_sensitive_sql():
    user = get_user("analyst")
    result = check_entitlement(user, tool_name="run_query", sql="SELECT salary FROM employees")
    assert result.passed
    assert result.needs_approval
    assert "dept_id" not in (result.sql or "")  # analyst 无 row_filter


def test_check_entitlement_marks_sensitive_for_manager():
    user = get_user("xiaoyiming")
    result = check_entitlement(user, tool_name="run_query", sql="SELECT name, salary FROM employees")
    assert result.passed
    assert result.needs_approval
    assert "dept_id = 2" in (result.sql or "")


# ═══════════════════════════════════════════════════════════════════════════════
# 6. run_query 综合权限检查
# ═══════════════════════════════════════════════════════════════════════════════

def test_check_entitlement_run_query_denied_for_viewer():
    user = get_user("viewer")
    result = check_entitlement(user, tool_name="run_query", sql="SELECT * FROM orders")
    assert not result.passed
    assert "无权使用" in result.reason


def test_check_entitlement_run_query_table_denied():
    user = get_user("support")
    result = check_entitlement(user, tool_name="run_query", sql="SELECT * FROM employees")
    assert not result.passed
    assert "无权访问" in result.reason


def test_check_entitlement_write_sql_blocked():
    """虽然 entitlement 不解析 SQL 语义，但 run_query 上层会拦非 SELECT；
    这里 entitlement 对 viewer 直接工具级拒绝。"""
    user = get_user("viewer")
    result = check_entitlement(user, tool_name="run_query", sql="DROP TABLE orders")
    assert not result.passed


def test_check_entitlement_describe_table_allowed():
    user = get_user("viewer")
    result = check_entitlement(user, tool_name="describe_table", table="orders")
    assert result.passed


def test_check_entitlement_describe_table_denied():
    user = get_user("viewer")
    result = check_entitlement(user, tool_name="describe_table", table="employees")
    assert not result.passed


def test_check_entitlement_list_tables_filters():
    user = get_user("viewer")
    all_tables = ["departments", "employees", "orders", "products", "customers"]
    result = check_entitlement(user, tool_name="list_tables", tables=all_tables)
    assert result.passed
    assert "employees" not in result.tables
    assert "orders" in result.tables


# ═══════════════════════════════════════════════════════════════════════════════
# 7. deny_payload 统一返回
# ═══════════════════════════════════════════════════════════════════════════════

def test_deny_payload_structure():
    user = get_user("viewer")
    ent = authorize_tool(user, "run_query")
    payload = deny_payload(ent, sql="SELECT * FROM orders")
    assert payload["error"] is True
    assert "无权使用" in payload["message"]
    assert payload["sql"] == "SELECT * FROM orders"


# ═══════════════════════════════════════════════════════════════════════════════
# 8. HBase 操作审批
# ═══════════════════════════════════════════════════════════════════════════════

def test_hbase_read_ops_no_approval():
    for op in ("scan", "get", "count", "list", "desc"):
        assert not needs_approval_hbase(op)


def test_hbase_destructive_ops_need_approval():
    for op in ("put", "delete", "drop", "truncate"):
        assert needs_approval_hbase(op)


# ═══════════════════════════════════════════════════════════════════════════════
# 9. 兼容签名 check_entitlement_by_role
# ═══════════════════════════════════════════════════════════════════════════════

def test_check_entitlement_by_role_allows_dba():
    ok, reason = check_entitlement_by_role("dba", "SELECT * FROM employees")
    assert ok
    assert reason == ""


def test_check_entitlement_by_role_denies_unknown_role():
    ok, reason = check_entitlement_by_role("ghost", "SELECT 1")
    assert not ok
    assert "未知角色" in reason


def test_check_entitlement_by_role_sensitive_column():
    ok, reason = check_entitlement_by_role("analyst", "SELECT salary FROM employees")
    assert not ok
    assert "人工审批" in reason
