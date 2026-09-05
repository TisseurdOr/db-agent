"""GET /api/rbac — 列出可切换的 RBAC 用户与角色。"""

import sqlite3
from pathlib import Path

from fastapi import APIRouter

from harness.constraints.entitlement import (
    can_access_dashboard,
    can_access_database,
    get_user,
    list_roles,
    list_users,
)

router = APIRouter()

_DEPT_FALLBACK = {
    1: "销售部",
    2: "市场部",
    3: "研发部",
    4: "财务部",
    5: "人事部",
    6: "产品部",
}


def _dept_names() -> dict[int, str]:
    """id → 部门名；DB 不可用时用内置映射。"""
    db = Path(__file__).resolve().parents[2] / "db" / "demo.db"
    if not db.exists():
        return dict(_DEPT_FALLBACK)
    try:
        conn = sqlite3.connect(str(db))
        rows = conn.execute("SELECT id, name FROM departments").fetchall()
        conn.close()
        return {int(r[0]): str(r[1]) for r in rows} or dict(_DEPT_FALLBACK)
    except sqlite3.Error:
        return dict(_DEPT_FALLBACK)


@router.get("/rbac")
async def get_rbac():
    """前端侧栏：可选用户（经理带部门名）+ 角色摘要。"""
    depts = _dept_names()
    users = []
    for u in list_users():
        dept_id = u.get("dept_id")
        full = get_user(u["id"])
        users.append({
            **u,
            "dept_name": depts.get(dept_id) if dept_id is not None else None,
            "can_access_database": can_access_database(full),
            "can_access_dashboard": can_access_dashboard(full),
        })
    try:
        roles = list_roles()
    except Exception:
        roles = []
    return {
        "default_user_id": "viewer",
        "users": users,
        "roles": roles,
        "hint": {
            "manager": "DBA / 经理 / 数据分析师可访问数据库；经理查 employees 时自动加 WHERE dept_id=本部门",
            "db_access": "viewer / support 不能浏览 Database；Dashboard 仅 dba / analyst（经理不可）",
        },
    }


@router.get("/rbac/users/{user_id}")
async def get_rbac_user(user_id: str):
    """单个用户的完整权限快照（调试/展示）。"""
    user = get_user(user_id)
    perms = user.get("permissions", {})
    dept_id = user.get("dept_id")
    return {
        "id": user_id,
        "name": user.get("name"),
        "role": user.get("role"),
        "dept_id": dept_id,
        "dept_name": _dept_names().get(dept_id) if dept_id is not None else None,
        "allowed_tools": perms.get("allowed_tools"),
        "db_tables": perms.get("db_tables"),
        "sensitive_check": perms.get("sensitive_check"),
        "row_filter": perms.get("db_row_filter"),
        "can_access_database": can_access_database(user),
        "can_access_dashboard": can_access_dashboard(user),
    }
