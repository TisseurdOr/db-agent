"""Database browser API — read-only browse + SQL console."""

from fastapi.testclient import TestClient

from server.main import app

client = TestClient(app)


def test_database_overview_demo():
    r = client.get("/api/database?store=demo")
    assert r.status_code == 200
    data = r.json()
    assert any(s["id"] == "demo" for s in data["stores"])
    active = data["active"]
    assert active["id"] == "demo"
    assert active["exists"] is True
    names = {t["name"] for t in active["tables"]}
    assert "user_memory" in names
    assert "user_feedback" in names
    assert "agent_roles" in names


def test_database_table_sample():
    r = client.get("/api/database/table/agent_roles?store=demo&limit=10")
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "agent_roles"
    assert "role" in data["columns"]
    assert data["count"] >= 1
    assert len(data["sample"]) >= 1


def test_database_query_select():
    r = client.post("/api/database/query", json={
        "store": "demo",
        "sql": "SELECT role, name FROM agent_roles LIMIT 5",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert "role" in data["columns"]
    assert len(data["rows"]) >= 1


def test_database_query_blocks_write():
    r = client.post("/api/database/query", json={
        "store": "demo",
        "sql": "DELETE FROM agent_roles",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False
    assert data["error"]


def test_database_traces_virtual():
    r = client.get("/api/database?store=traces")
    assert r.status_code == 200
    assert r.json()["active"]["id"] == "traces"
    r2 = client.get("/api/database/table/traces?store=traces&limit=5")
    assert r2.status_code == 200
    assert "query" in r2.json()["columns"]


def test_database_demo_groups_and_hbase():
    r = client.get("/api/database?store=demo")
    assert r.status_code == 200
    active = r.json()["active"]
    assert "sql" in active["groups"]
    assert "hive" in active["groups"]
    assert any(t["name"] == "orders" for t in active["groups"]["sql"])
    assert any(t["name"] == "ods_orders_hive" for t in active["groups"]["hive"])
    assert active.get("hbase_tables")
    store_ids = {s["id"] for s in r.json()["stores"]}
    assert "uploads" not in store_ids  # missing file hidden
    assert "hbase" in store_ids


def test_database_hbase_table():
    r = client.get("/api/database/table/orders?store=hbase&limit=5")
    assert r.status_code == 200
    data = r.json()
    assert "row_key" in data["columns"]
    assert data["count"] >= 1
    assert len(data["sample"]) >= 1
