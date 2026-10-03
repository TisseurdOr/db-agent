"""GET /api/dashboard/latest — 最近一次 render_chart 的结构化大屏数据。

仅 dba / analyst 可看；部门经理等角色 403。
"""

from __future__ import annotations

import json
import os

from fastapi import APIRouter, Header, HTTPException, Query

from harness.constraints.entitlement import can_access_dashboard, get_user
from harness.tools.chart import CHART_DIR, get_latest_dashboard

router = APIRouter()


def _fallback_from_disk() -> dict | None:
    """内存为空时：优先 dashboard_latest.json，否则最新 dashboard_*.json。"""
    if not os.path.isdir(CHART_DIR):
        return None
    latest = os.path.join(CHART_DIR, "dashboard_latest.json")
    if os.path.isfile(latest):
        try:
            with open(latest, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    files = sorted(
        (f for f in os.listdir(CHART_DIR) if f.startswith("dashboard_") and f.endswith(".json")
         and f != "dashboard_latest.json"),
        reverse=True,
    )
    if not files:
        return None
    try:
        with open(os.path.join(CHART_DIR, files[0]), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _require_dashboard(user_id: str | None, x_agent_user: str | None = None) -> dict:
    from server.auth import resolve_identity
    uid = resolve_identity(user_id or x_agent_user)
    user = get_user(uid)
    if not can_access_dashboard(user):
        raise HTTPException(
            status_code=403,
            detail={
                "error": "forbidden",
                "message": f"角色「{user.get('name')}」无权查看数据大屏。",
                "suggestion": "请切换为研发 DBA 或数据分析师后再打开 Dashboard。",
                "user_id": uid,
                "role": user.get("role"),
            },
        )
    return user


@router.get("/dashboard/latest")
async def latest_dashboard(
    user_id: str | None = Query(None, description="RBAC 用户，与侧栏 identity 一致"),
    x_agent_user: str | None = Header(None, alias="X-Agent-User"),
):
    """返回结构化大屏，供前端用 Ops/Eval 同款样式实时渲染。

    {title, panels:[{type,title,labels,values}], updated_at, url, panel_count}
    尚未生成时 title/panels 为空，url 可能仍指向旧 HTML（兼容）。
    """
    _require_dashboard(user_id, x_agent_user)
    data = get_latest_dashboard() or _fallback_from_disk()
    if not data:
        return {
            "title": None,
            "panels": [],
            "panel_count": 0,
            "updated_at": None,
            "url": None,
        }
    return {
        "title": data.get("title"),
        "panels": data.get("panels") or [],
        "panel_count": data.get("panel_count") or len(data.get("panels") or []),
        "updated_at": data.get("updated_at"),
        "url": data.get("url"),
    }
