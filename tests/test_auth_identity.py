"""身份来源 & 行级权限归一化的回归测试。

背景：Web 层身份曾直接取自客户端传入的 user_id，任何人传 user_id="dba"
即可提权；行级过滤（RLS）也曾被 `FROM "employees"` / `FROM main.employees`
绕过。这两条都是安全边界，必须锁住。
"""

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from harness.constraints.entitlement import _extract_table_names, rewrite_sql
from server.auth import require_auth, resolve_identity


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/who", dependencies=[Depends(require_auth)])
    def who(user_id: str = "dba"):  # noqa: ARG001  客户端会尝试自称 dba
        return {"resolved": resolve_identity(user_id)}

    return app


# ── 每用户 token（方案 B）──

def test_per_user_token_overrides_client_identity(monkeypatch):
    monkeypatch.setenv("WEB_API_TOKENS", "viewer:tokVIEW,dba:tokDBA")
    monkeypatch.delenv("WEB_API_TOKEN", raising=False)
    with TestClient(_app()) as c:
        # 客户端自称 dba，但 token 属于 viewer → 必须以 token 为准
        r = c.get("/who", headers={"Authorization": "Bearer tokVIEW"})
        assert r.status_code == 200
        assert r.json()["resolved"] == "viewer"


def test_x_api_key_also_maps_identity(monkeypatch):
    monkeypatch.setenv("WEB_API_TOKENS", "zhoufang:tokMGR")
    monkeypatch.delenv("WEB_API_TOKEN", raising=False)
    with TestClient(_app()) as c:
        r = c.get("/who", headers={"X-API-Key": "tokMGR"})
        assert r.json()["resolved"] == "zhoufang"


def test_missing_or_wrong_token_rejected(monkeypatch):
    monkeypatch.setenv("WEB_API_TOKENS", "viewer:tokVIEW")
    monkeypatch.delenv("WEB_API_TOKEN", raising=False)
    with TestClient(_app()) as c:
        assert c.get("/who").status_code == 401
        assert c.get("/who", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_demo_mode_still_allows_client_identity(monkeypatch):
    """两者都没配 → 演示模式：保留前端切角色，身份取客户端值。"""
    monkeypatch.delenv("WEB_API_TOKENS", raising=False)
    monkeypatch.delenv("WEB_API_TOKEN", raising=False)
    with TestClient(_app()) as c:
        assert c.get("/who?user_id=dba").status_code == 200
        assert c.get("/who", params={"user_id": "dba"}).json()["resolved"] == "dba"


# ── 行级权限（RLS）归一化 ──

@pytest.mark.parametrize("sql,expected", [
    ("SELECT * FROM employees", ["employees"]),
    ('SELECT * FROM "employees"', ["employees"]),
    ("SELECT * FROM main.employees", ["employees"]),
    ("SELECT * FROM `employees`", ["employees"]),
    ("SELECT * FROM [employees]", ["employees"]),
    ('SELECT * FROM "main"."employees"', ["employees"]),
])
def test_extract_table_names_normalizes(sql, expected):
    assert _extract_table_names(sql) == expected


@pytest.mark.parametrize("sql", [
    'SELECT * FROM "employees"',
    "SELECT * FROM main.employees",
    "SELECT * FROM `employees`",
])
def test_row_filter_applies_to_quoted_and_qualified_names(sql):
    mgr = {"permissions": {"db_row_filter": {"employees": "dept_id"}}, "dept_id": 2}
    assert "dept_id = 2" in rewrite_sql(mgr, sql)
