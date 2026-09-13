"""血缘端点的离线自检：真实外键边存在，Hive 分层表不造假边。"""
from server.endpoints.lineage import build_lineage


def test_lineage_real_fk_edges():
    d = build_lineage()
    ids = {n["id"] for n in d["nodes"]}
    fk_targets = {e["target"] for e in d["edges"] if e["source"] == "orders"}
    assert {"products", "customers", "departments", "channels"} <= fk_targets
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
