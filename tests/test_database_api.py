"""Database browser API — read-only browse + SQL console."""

from fastapi.testclient import TestClient

from server.main import app

client = TestClient(app)
DB_USER = {"user_id": "dba"}


def test_database_overview_demo():
    r = client.get("/api/database?store=demo&user_id=dba")
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
    r = client.get("/api/database/table/agent_roles?store=demo&limit=10&user_id=dba")
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "agent_roles"
    assert "role" in data["columns"]
    assert data["count"] >= 1
    assert len(data["sample"]) >= 1


def test_database_query_select():
    r = client.post("/api/database/query", json={"user_id": "dba",
        "store": "demo",
        "sql": "SELECT role, name FROM agent_roles LIMIT 5",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert "role" in data["columns"]
    assert len(data["rows"]) >= 1


def test_database_query_blocks_write():
    r = client.post("/api/database/query", json={"user_id": "dba",
        "store": "demo",
        "sql": "DELETE FROM agent_roles",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False
    assert data["error"]


def test_database_traces_virtual():
    r = client.get("/api/database?store=traces&user_id=dba")
    assert r.status_code == 200
    assert r.json()["active"]["id"] == "traces"
    r2 = client.get("/api/database/table/traces?store=traces&limit=5&user_id=dba")
    assert r2.status_code == 200
    assert "query" in r2.json()["columns"]


def test_database_demo_groups_and_hbase():
    r = client.get("/api/database?store=demo&user_id=dba")
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
    r = client.get("/api/database/table/orders?store=hbase&limit=5&user_id=dba")
    assert r.status_code == 200
    data = r.json()
    assert "row_key" in data["columns"]
    assert data["count"] >= 1
    assert len(data["sample"]) >= 1

def test_database_forbidden_for_viewer():
    r = client.get("/api/database?store=demo&user_id=viewer")
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert detail["error"] == "forbidden"


def test_database_ok_for_analyst():
    r = client.get("/api/database?store=demo&user_id=analyst")
    assert r.status_code == 200


def test_database_ok_for_manager():
    r = client.get("/api/database?store=demo&user_id=zhoufang")
    assert r.status_code == 200


def test_database_warehouse_five_layers():
    import pytest

    from db.olist_warehouse import WAREHOUSE_DB_PATH

    if not WAREHOUSE_DB_PATH.exists():
        pytest.skip("warehouse.db not built")

    r = client.get("/api/database?store=warehouse&user_id=dba")
    assert r.status_code == 200
    data = r.json()
    assert any(s["id"] == "warehouse" for s in data["stores"])
    active = data["active"]
    assert set(active["groups"]) == {"ods", "dim", "dwd", "dws", "ads"}
    assert any(t["name"] == "ods_olist_orders" for t in active["groups"]["ods"])
    assert any(t["name"] == "dim_olist_date" for t in active["groups"]["dim"])
    assert any(t["name"] == "dwd_olist_order_items" for t in active["groups"]["dwd"])
    assert any(t["name"] == "dws_olist_sales_period" for t in active["groups"]["dws"])
    assert any(t["name"] == "ads_olist_period_comparison" for t in active["groups"]["ads"])


def test_database_warehouse_table_and_query():
    import pytest

    from db.olist_warehouse import WAREHOUSE_DB_PATH

    if not WAREHOUSE_DB_PATH.exists():
        pytest.skip("warehouse.db not built")

    r = client.get("/api/database/table/ads_olist_period_metrics?store=warehouse&limit=5&user_id=dba")
    assert r.status_code == 200
    data = r.json()
    assert data["group"] == "ads"
    assert "metric_name" in data["columns"]

    r2 = client.post("/api/database/query", json={
        "user_id": "dba",
        "store": "warehouse",
        "sql": "SELECT period_key, metric_value FROM ads_olist_period_metrics LIMIT 5",
    })
    assert r2.status_code == 200
    result = r2.json()
    assert result["ok"] is True
    assert "period_key" in result["columns"]
