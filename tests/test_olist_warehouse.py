import sqlite3
from pathlib import Path

import pytest

from db import olist_warehouse as ow


def _insert_ods(conn, table: str, rows: list[dict]) -> None:
    spec = next(item for item in ow.RAW_FILES if item["table"] == table)
    placeholders = ", ".join("?" for _ in (*spec["columns"], *ow._ODS_META_COLUMNS))
    values = [
        tuple([row.get(column) for column in spec["columns"]] + [
            "fixture.csv", ow.DATASET_ID, ow.DATASET_VERSION, "test", "2026-01-01T00:00:00Z"
        ])
        for row in rows
    ]
    conn.executemany(f'INSERT INTO "{table}" VALUES ({placeholders})', values)


def _fixture_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        for spec in ow.RAW_FILES:
            ow._create_ods_table(conn, spec)

        _insert_ods(conn, "ods_olist_customers", [
            {"customer_id": "c1", "customer_unique_id": "u1", "customer_zip_code_prefix": "01001",
             "customer_city": "sao paulo", "customer_state": "SP"},
        ])
        _insert_ods(conn, "ods_olist_sellers", [
            {"seller_id": "s1", "seller_zip_code_prefix": "01002", "seller_city": "sao paulo",
             "seller_state": "SP"},
        ])
        _insert_ods(conn, "ods_olist_products", [
            {"product_id": "p1", "product_category_name": "casa", "product_name_lenght": "10",
             "product_description_lenght": "20", "product_photos_qty": "1",
             "product_weight_g": "100", "product_length_cm": "1", "product_height_cm": "1",
             "product_width_cm": "1"},
        ])
        _insert_ods(conn, "ods_olist_category_translation", [
            {"product_category_name": "casa", "product_category_name_english": "home"},
        ])
        _insert_ods(conn, "ods_olist_orders", [
            {"order_id": "o1", "customer_id": "c1", "order_status": "delivered",
             "order_purchase_timestamp": "2018-01-15 10:00:00", "order_approved_at": "2018-01-15 11:00:00",
             "order_delivered_carrier_date": "2018-01-16 10:00:00",
             "order_delivered_customer_date": "2018-01-20 10:00:00",
             "order_estimated_delivery_date": "2018-01-18 10:00:00"},
            {"order_id": "o2", "customer_id": "c1", "order_status": "delivered",
             "order_purchase_timestamp": "2018-07-15 10:00:00", "order_approved_at": "2018-07-15 11:00:00",
             "order_delivered_carrier_date": "2018-07-16 10:00:00",
             "order_delivered_customer_date": "2018-07-20 10:00:00",
             "order_estimated_delivery_date": "2018-07-18 10:00:00"},
        ])
        _insert_ods(conn, "ods_olist_order_items", [
            {"order_id": "o1", "order_item_id": "1", "product_id": "p1", "seller_id": "s1",
             "shipping_limit_date": "2018-01-17 10:00:00", "price": "100", "freight_value": "10"},
            {"order_id": "o2", "order_item_id": "1", "product_id": "p1", "seller_id": "s1",
             "shipping_limit_date": "2018-07-17 10:00:00", "price": "150", "freight_value": "15"},
        ])
        _insert_ods(conn, "ods_olist_payments", [
            {"order_id": "o1", "payment_sequential": "1", "payment_type": "credit_card",
             "payment_installments": "1", "payment_value": "110"},
            {"order_id": "o2", "payment_sequential": "1", "payment_type": "credit_card",
             "payment_installments": "1", "payment_value": "165"},
        ])
        _insert_ods(conn, "ods_olist_reviews", [
            {"review_id": "r1", "order_id": "o1", "review_score": "5", "review_comment_title": "",
             "review_comment_message": "", "review_creation_date": "2018-01-21 00:00:00",
             "review_answer_timestamp": "2018-01-22 00:00:00"},
        ])
        _insert_ods(conn, "ods_olist_geolocation", [
            {"geolocation_zip_code_prefix": "01001", "geolocation_lat": "-23.5",
             "geolocation_lng": "-46.6", "geolocation_city": "sao paulo",
             "geolocation_state": "SP"},
        ])
        conn.commit()
    finally:
        conn.close()


def test_build_all_layers_and_compare_periods(tmp_path):
    db_path = tmp_path / "warehouse.db"
    _fixture_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        result = ow.build_all_layers(conn)
        assert result["dim"]["tables"]["dim_olist_date"] >= 181
        assert result["dwd"]["tables"]["dwd_olist_order_items"] == 2
        assert result["dws"]["tables"]["dws_olist_sales_daily"] == 2
        validation = ow.validate_warehouse(conn)
        assert validation["ok"] is True
    finally:
        conn.close()

    compared = ow.compare_olist_periods(
        "gross_sales", "2018-H1", "2018-H2", db_path=db_path,
    )
    assert compared["period_a"]["metric_value"] == pytest.approx(110.0)
    assert compared["period_b"]["metric_value"] == pytest.approx(165.0)
    assert compared["growth_rate_pct"] == pytest.approx(50.0)
    assert "period_a_incomplete" in compared["warnings"]
    assert "period_b_incomplete" in compared["warnings"]


def test_warehouse_tables_are_registered():
    assert "ods_olist_orders" in ow.OLIST_WAREHOUSE_TABLES
    assert "dim_olist_date" in ow.OLIST_WAREHOUSE_TABLES
    assert "dwd_olist_order_items" in ow.OLIST_WAREHOUSE_TABLES
    assert "dws_olist_sales_period" in ow.OLIST_WAREHOUSE_TABLES
    assert "ads_olist_period_comparison" in ow.OLIST_WAREHOUSE_TABLES
