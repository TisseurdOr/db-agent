"""血缘端点的离线自检：真实外键边存在，Hive 分层表不造假边。"""
from server.endpoints.lineage import build_lineage


def test_lineage_real_fk_edges():
    d = build_lineage()
    ids = {n["id"] for n in d["nodes"]}
    fk_sources = {e["source"] for e in d["edges"] if e["target"] == "orders"}
    assert {"products", "customers", "departments", "channels"} <= fk_sources
    assert "hbase:orders" in ids
    assert "ods_orders_hive" in ids


def test_lineage_hive_has_no_fake_edges():
    d = build_lineage()
    hive = {n["id"] for n in d["nodes"] if n["group"] == "hive"}
    assert hive, "expected hive tables in lineage"
    # demo 无真实 ETL，Hive 表之间不造假血缘边（守住「已实现 vs gap」边界）
    assert not any(
        e["source"] in hive and e["target"] in hive for e in d["edges"]
    )


def test_lineage_warehouse_etl():
    import pytest

    from db.olist_warehouse import WAREHOUSE_DB_PATH

    if not WAREHOUSE_DB_PATH.exists():
        pytest.skip("warehouse.db not built")

    d = build_lineage()
    warehouse_nodes = {n["id"] for n in d["nodes"] if n.get("source") == "warehouse"}
    assert len(warehouse_nodes) == 23
    etl = {(e["source"], e["target"]) for e in d["edges"] if e.get("kind") == "etl"}
    assert ("dwd_olist_order_items", "dws_olist_sales_daily") in etl
    assert ("dws_olist_sales_period", "ads_olist_period_metrics") in etl
    assert ("ads_olist_period_metrics", "ads_olist_period_comparison") in etl
    types = {(e["source"], e["target"]): e.get("kind") for e in d["edges"]}
    assert types[("ads_olist_metric_catalog", "ads_olist_period_metrics")] == "reference"
