"""数据源接口的安全回归：标识符转义 + 路径限制。

- CSV 表头/文件名会被拼进 DDL，必须转义，否则 `"` 可注入列定义。
- /connect 的 path 曾被直接交给 sqlite3.connect → 可读任意本地 SQLite 文件。
"""

import pytest
from fastapi.testclient import TestClient

from server.endpoints.datasource import (
    _quote_ident,
    _resolve_allowed_db,
    _safe_table_name,
)
from server.main import app


def test_quote_ident_escapes_double_quote():
    assert _quote_ident('a" TEXT); --') == '"a"" TEXT); --"'


def test_safe_table_name_strips_metacharacters():
    name = _safe_table_name('evil"; DROP TABLE x; --.csv')
    assert name.startswith("uploaded_")
    assert all(ch.isalnum() or ch == "_" for ch in name)
    assert ";" not in name and '"' not in name


@pytest.mark.parametrize("bad", ["/etc/passwd", "../../etc/passwd", "~/secret.db"])
def test_resolve_allowed_db_blocks_outside_paths(bad):
    assert _resolve_allowed_db(bad) is None


def test_resolve_allowed_db_allows_inside_project():
    assert _resolve_allowed_db("db/demo.db") is not None


def test_connect_rejects_outside_path():
    with TestClient(app) as c:
        r = c.post("/api/datasource/connect", json={"type": "sqlite", "path": "/etc/passwd"})
        assert r.status_code == 200
        assert r.json()["ok"] is False


def test_upload_with_quote_in_header_does_not_inject(tmp_path, monkeypatch):
    """表头带引号时，应作为普通列名处理（转义），不能改坏 DDL 或注入。"""
    import server.endpoints.datasource as ds
    monkeypatch.setattr(ds, "_UPLOAD_DIR", tmp_path)  # 隔离：别写真实 db/uploads
    csv_text = 'a" TEXT); DROP TABLE x; --,b\n1,2\n'
    with TestClient(app) as c:
        r = c.post("/api/datasource/upload", files={"file": ("inject.csv", csv_text, "text/csv")})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["columns"] == ['a" TEXT); DROP TABLE x; --', "b"]
