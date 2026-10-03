"""Olist public e-commerce data warehouse.

Builds ODS -> DIM -> DWD -> DWS -> ADS layers in the existing SQLite database.
The raw dataset is public test data, not real user feedback.
"""

from __future__ import annotations

import csv
import sqlite3
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAW_DIR = PROJECT_ROOT / "data" / "raw" / "olist"
WAREHOUSE_DB_PATH = PROJECT_ROOT / "db" / "warehouse.db"

DATASET_ID = "MafiaAzulBr/olistcsv"
DATASET_VERSION = "2bf427f68bf25787d2a5a305306feaa0d074bbe9"
DATASET_URL = f"https://huggingface.co/datasets/{DATASET_ID}/resolve/{DATASET_VERSION}"

RAW_FILES = (
    {
        "logical": "customers",
        "file": "archive/olist_customers_dataset.csv",
        "table": "ods_olist_customers",
        "columns": (
            "customer_id",
            "customer_unique_id",
            "customer_zip_code_prefix",
            "customer_city",
            "customer_state",
        ),
    },
    {
        "logical": "geolocation",
        "file": "archive/olist_geolocation_dataset.csv",
        "table": "ods_olist_geolocation",
        "columns": (
            "geolocation_zip_code_prefix",
            "geolocation_lat",
            "geolocation_lng",
            "geolocation_city",
            "geolocation_state",
        ),
    },
    {
        "logical": "order_items",
        "file": "archive/olist_order_items_dataset.csv",
        "table": "ods_olist_order_items",
        "columns": (
            "order_id",
            "order_item_id",
            "product_id",
            "seller_id",
            "shipping_limit_date",
            "price",
            "freight_value",
        ),
    },
    {
        "logical": "payments",
        "file": "archive/olist_order_payments_dataset.csv",
        "table": "ods_olist_payments",
        "columns": (
            "order_id",
            "payment_sequential",
            "payment_type",
            "payment_installments",
            "payment_value",
        ),
    },
    {
        "logical": "reviews",
        "file": "archive/olist_order_reviews_dataset.csv",
        "table": "ods_olist_reviews",
        "columns": (
            "review_id",
            "order_id",
            "review_score",
            "review_comment_title",
            "review_comment_message",
            "review_creation_date",
            "review_answer_timestamp",
        ),
    },
    {
        "logical": "orders",
        "file": "archive/olist_orders_dataset.csv",
        "table": "ods_olist_orders",
        "columns": (
            "order_id",
            "customer_id",
            "order_status",
            "order_purchase_timestamp",
            "order_approved_at",
            "order_delivered_carrier_date",
            "order_delivered_customer_date",
            "order_estimated_delivery_date",
        ),
    },
    {
        "logical": "products",
        "file": "archive/olist_products_dataset.csv",
        "table": "ods_olist_products",
        "columns": (
            "product_id",
            "product_category_name",
            "product_name_lenght",
            "product_description_lenght",
            "product_photos_qty",
            "product_weight_g",
            "product_length_cm",
            "product_height_cm",
            "product_width_cm",
        ),
    },
    {
        "logical": "sellers",
        "file": "archive/olist_sellers_dataset.csv",
        "table": "ods_olist_sellers",
        "columns": (
            "seller_id",
            "seller_zip_code_prefix",
            "seller_city",
            "seller_state",
        ),
    },
    {
        "logical": "category_translation",
        "file": "archive/product_category_name_translation.csv",
        "table": "ods_olist_category_translation",
        "columns": ("product_category_name", "product_category_name_english"),
    },
)

ODS_TABLES = tuple(spec["table"] for spec in RAW_FILES)
DIM_TABLES = (
    "dim_olist_date",
    "dim_olist_customer",
    "dim_olist_product",
    "dim_olist_seller",
    "dim_olist_geography",
)
DWD_TABLES = (
    "dwd_olist_order_items",
    "dwd_olist_payments",
    "dwd_olist_reviews",
    "dwd_olist_order_fulfillment",
)
DWS_TABLES = ("dws_olist_sales_daily", "dws_olist_sales_period")
ADS_TABLES = (
    "ads_olist_metric_catalog",
    "ads_olist_period_metrics",
    "ads_olist_period_comparison",
)

OLIST_WAREHOUSE_TABLES = ODS_TABLES + DIM_TABLES + DWD_TABLES + DWS_TABLES + ADS_TABLES

OLIST_LINEAGE_EDGES = (
    {"source": "dim_olist_date", "target": "ods_olist_orders", "type": "etl"},
    {"source": "dim_olist_customer", "target": "ods_olist_customers", "type": "etl"},
    {"source": "dim_olist_product", "target": "ods_olist_products", "type": "etl"},
    {"source": "dim_olist_product", "target": "ods_olist_category_translation", "type": "etl"},
    {"source": "dim_olist_seller", "target": "ods_olist_sellers", "type": "etl"},
    {"source": "dim_olist_geography", "target": "ods_olist_geolocation", "type": "etl"},
    {"source": "dwd_olist_order_items", "target": "ods_olist_order_items", "type": "etl"},
    {"source": "dwd_olist_order_items", "target": "ods_olist_orders", "type": "etl"},
    {"source": "dwd_olist_order_items", "target": "dim_olist_product", "type": "etl"},
    {"source": "dwd_olist_order_items", "target": "dim_olist_customer", "type": "etl"},
    {"source": "dwd_olist_order_items", "target": "dim_olist_seller", "type": "etl"},
    {"source": "dwd_olist_payments", "target": "ods_olist_payments", "type": "etl"},
    {"source": "dwd_olist_reviews", "target": "ods_olist_reviews", "type": "etl"},
    {"source": "dwd_olist_order_fulfillment", "target": "ods_olist_orders", "type": "etl"},
    {"source": "dws_olist_sales_daily", "target": "dwd_olist_order_items", "type": "etl"},
    {"source": "dws_olist_sales_period", "target": "dws_olist_sales_daily", "type": "etl"},
    {"source": "dws_olist_sales_period", "target": "dim_olist_date", "type": "etl"},
    {"source": "ads_olist_period_metrics", "target": "ads_olist_metric_catalog", "type": "reference"},
    {"source": "ads_olist_period_metrics", "target": "dws_olist_sales_period", "type": "etl"},
    {"source": "ads_olist_period_comparison", "target": "ads_olist_period_metrics", "type": "etl"},
)

OLIST_LINEAGE_DESCRIPTIONS = {
    ("dim_olist_date", "ods_olist_orders"): "根据订单购买时间生成日期、月份、季度和半年维度",
    ("dim_olist_customer", "ods_olist_customers"): "清洗客户主数据并保留地区属性",
    ("dim_olist_product", "ods_olist_products"): "标准化商品主数据",
    ("dim_olist_product", "ods_olist_category_translation"): "关联品类英文名，形成统一商品维度",
    ("dim_olist_seller", "ods_olist_sellers"): "标准化卖家及所在地信息",
    ("dim_olist_geography", "ods_olist_geolocation"): "按邮编前缀聚合城市、州和经纬度",
    ("dwd_olist_order_items", "ods_olist_order_items"): "订单商品与金额来源",
    ("dwd_olist_order_items", "ods_olist_orders"): "补充订单状态和购买时间",
    ("dwd_olist_order_items", "dim_olist_product"): "补充商品品类",
    ("dwd_olist_order_items", "dim_olist_customer"): "补充客户所在州",
    ("dwd_olist_order_items", "dim_olist_seller"): "补充卖家所在州",
    ("dwd_olist_payments", "ods_olist_payments"): "标准化支付类型、分期和金额",
    ("dwd_olist_reviews", "ods_olist_reviews"): "标准化订单评价与评分",
    ("dwd_olist_order_fulfillment", "ods_olist_orders"): "根据订单时间戳计算履约时长和延迟",
    ("dws_olist_sales_daily", "dwd_olist_order_items"): "订单明细按日期、地区、品类和状态聚合成日销售",
    ("dws_olist_sales_period", "dws_olist_sales_daily"): "将日销售汇总到月、季度、半年和年度",
    ("dws_olist_sales_period", "dim_olist_date"): "使用统一日历口径生成期间边界",
    ("ads_olist_period_metrics", "ads_olist_metric_catalog"): "沿用指标目录中的统一名称、业务口径和单位",
    ("ads_olist_period_metrics", "dws_olist_sales_period"): "形成供双期对比使用的期间指标表",
    ("ads_olist_period_comparison", "ads_olist_period_metrics"): "计算相邻期变化率并生成数据完整性告警",
}
_ODS_META_COLUMNS = (
    "_source_file",
    "_dataset_id",
    "_dataset_version",
    "_batch_id",
    "_ingested_at",
)

METRICS = {
    "gross_sales": {
        "label": "成交金额",
        "definition": "有效订单明细的 price + freight_value；排除 canceled/unavailable。",
        "unit": "BRL",
    },
    "net_sales": {
        "label": "已送达商品金额",
        "definition": "order_status = delivered 的 price 合计，不含运费。",
        "unit": "BRL",
    },
    "order_count": {
        "label": "订单量",
        "definition": "有效订单去重计数；排除 canceled/unavailable。",
        "unit": "orders",
    },
    "item_count": {
        "label": "商品件数",
        "definition": "有效订单明细行数；排除 canceled/unavailable。",
        "unit": "items",
    },
}


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def download_dataset(raw_dir: Path = DEFAULT_RAW_DIR, force: bool = False) -> dict:
    """Download the pinned Olist CSV files, preferring the official HF client."""
    import shutil

    raw_dir.mkdir(parents=True, exist_ok=True)
    downloaded: list[str] = []
    reused: list[str] = []
    existing_targets = [
        raw_dir / spec["logical"] / Path(spec["file"]).name
        for spec in RAW_FILES
    ]
    if not force and all(path.exists() and path.stat().st_size > 0 for path in existing_targets):
        return {
            "raw_dir": str(raw_dir),
            "downloaded": [],
            "reused": sorted(str(path) for path in existing_targets),
            "dataset_id": DATASET_ID,
            "dataset_version": DATASET_VERSION,
            "transport": "local",
        }

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        snapshot_download = None

    if snapshot_download is not None:
        hf_dir = raw_dir / "_hf"
        snapshot_download(
            repo_id=DATASET_ID,
            repo_type="dataset",
            revision=DATASET_VERSION,
            local_dir=hf_dir,
            allow_patterns="archive/*",
            max_workers=8,
            force_download=force,
        )
        for spec in RAW_FILES:
            source = hf_dir / spec["file"]
            target = raw_dir / spec["logical"] / Path(spec["file"]).name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.stat().st_size > 0 and not force:
                reused.append(str(target))
            else:
                shutil.copy2(source, target)
                downloaded.append(str(target))
        return {
            "raw_dir": str(raw_dir),
            "downloaded": sorted(downloaded),
            "reused": sorted(reused),
            "dataset_id": DATASET_ID,
            "dataset_version": DATASET_VERSION,
            "transport": "huggingface_hub",
        }

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def fetch(spec: dict) -> tuple[str, str]:
        target = raw_dir / spec["logical"] / Path(spec["file"]).name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.stat().st_size > 0 and not force:
            return "reused", str(target)
        url = f"{DATASET_URL}/{quote(spec['file'])}"
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "db-agent-olist-warehouse/0.1"},
        )
        tmp = target.with_suffix(target.suffix + ".part")
        with urllib.request.urlopen(request, timeout=180) as response, open(tmp, "wb") as out:
            while True:
                chunk = response.read(4 * 1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        tmp.replace(target)
        return "downloaded", str(target)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(fetch, spec) for spec in RAW_FILES]
        for future in as_completed(futures):
            status, path = future.result()
            (downloaded if status == "downloaded" else reused).append(path)
    return {
        "raw_dir": str(raw_dir),
        "downloaded": sorted(downloaded),
        "reused": sorted(reused),
        "dataset_id": DATASET_ID,
        "dataset_version": DATASET_VERSION,
        "transport": "urllib",
    }

def _drop_owned_objects(conn: sqlite3.Connection) -> None:
    for table in reversed(OLIST_WAREHOUSE_TABLES):
        conn.execute(f'DROP TABLE IF EXISTS "{table}"')


def _create_ods_table(conn: sqlite3.Connection, spec: dict) -> None:
    columns_sql = ", ".join(f'"{name}" TEXT' for name in spec["columns"])
    meta_sql = ", ".join(f'"{name}" TEXT' for name in _ODS_META_COLUMNS)
    conn.execute(f'DROP TABLE IF EXISTS "{spec["table"]}"')
    conn.execute(f'CREATE TABLE "{spec["table"]}" ({columns_sql}, {meta_sql})')


def load_ods(conn: sqlite3.Connection, raw_dir: Path = DEFAULT_RAW_DIR, batch_id: str | None = None) -> dict:
    """Load pinned CSVs into raw-shaped ODS tables."""
    batch_id = batch_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    ingested_at = _utc_now()
    counts: dict[str, int] = {}
    for spec in RAW_FILES:
        source = raw_dir / spec["logical"] / Path(spec["file"]).name
        if not source.exists():
            raise FileNotFoundError(f"missing raw file: {source}")
        _create_ods_table(conn, spec)
        columns = list(spec["columns"])
        placeholders = ", ".join("?" for _ in (*columns, *_ODS_META_COLUMNS))
        insert_sql = f'INSERT INTO "{spec["table"]}" VALUES ({placeholders})'
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            missing = [name for name in columns if name not in (reader.fieldnames or [])]
            if missing:
                raise ValueError(f"{source} missing columns: {missing}")
            batch: list[tuple] = []
            count = 0
            for row in reader:
                batch.append(tuple(
                    [row.get(name) or None for name in columns]
                    + [spec["file"], DATASET_ID, DATASET_VERSION, batch_id, ingested_at]
                ))
                if len(batch) >= 5000:
                    conn.executemany(insert_sql, batch)
                    count += len(batch)
                    batch.clear()
            if batch:
                conn.executemany(insert_sql, batch)
                count += len(batch)
        counts[spec["table"]] = count
    _create_ods_indexes(conn)
    conn.commit()
    return {"batch_id": batch_id, "row_counts": counts}


def _create_ods_indexes(conn: sqlite3.Connection) -> None:
    indexes = (
        ("idx_ods_olist_orders_customer", "ods_olist_orders", "customer_id"),
        ("idx_ods_olist_orders_purchase", "ods_olist_orders", "order_purchase_timestamp"),
        ("idx_ods_olist_items_order", "ods_olist_order_items", "order_id"),
        ("idx_ods_olist_items_product", "ods_olist_order_items", "product_id"),
        ("idx_ods_olist_items_seller", "ods_olist_order_items", "seller_id"),
        ("idx_ods_olist_payments_order", "ods_olist_payments", "order_id"),
        ("idx_ods_olist_reviews_order", "ods_olist_reviews", "order_id"),
    )
    for name, table, column in indexes:
        conn.execute(f'CREATE INDEX IF NOT EXISTS "{name}" ON "{table}"("{column}")')


def build_dimensions(conn: sqlite3.Connection) -> dict:
    """Build DIM tables from ODS."""
    for table in DIM_TABLES:
        conn.execute(f'DROP TABLE IF EXISTS "{table}"')

    conn.execute("""
        CREATE TABLE dim_olist_date (
            date_key TEXT PRIMARY KEY,
            "date" TEXT NOT NULL,
            year INTEGER NOT NULL,
            quarter INTEGER NOT NULL,
            month INTEGER NOT NULL,
            half INTEGER NOT NULL,
            period_month TEXT NOT NULL,
            period_quarter TEXT NOT NULL,
            period_half TEXT NOT NULL,
            period_year TEXT NOT NULL,
            month_start TEXT NOT NULL,
            month_end TEXT NOT NULL,
            quarter_start TEXT NOT NULL,
            quarter_end TEXT NOT NULL,
            half_start TEXT NOT NULL,
            half_end TEXT NOT NULL,
            year_start TEXT NOT NULL,
            year_end TEXT NOT NULL,
            day_of_week INTEGER NOT NULL,
            is_weekend INTEGER NOT NULL
        )
    """)
    conn.execute("""
        WITH RECURSIVE dates(d) AS (
            SELECT date(MIN(order_purchase_timestamp))
            FROM ods_olist_orders
            WHERE order_purchase_timestamp IS NOT NULL
            UNION ALL
            SELECT date(d, '+1 day')
            FROM dates
            WHERE d < (
                SELECT date(MAX(order_purchase_timestamp))
                FROM ods_olist_orders
                WHERE order_purchase_timestamp IS NOT NULL
            )
        ),
        base AS (
            SELECT
                d,
                CAST(strftime('%Y', d) AS INTEGER) AS y,
                CAST(strftime('%m', d) AS INTEGER) AS m,
                ((CAST(strftime('%m', d) AS INTEGER) - 1) / 3) + 1 AS q,
                CASE WHEN CAST(strftime('%m', d) AS INTEGER) <= 6 THEN 1 ELSE 2 END AS h
            FROM dates
        )
        INSERT INTO dim_olist_date
        SELECT
            d,
            d,
            y,
            q,
            m,
            h,
            printf('%04d-%02d', y, m),
            printf('%04d-Q%d', y, q),
            printf('%04d-H%d', y, h),
            printf('%04d', y),
            date(d, 'start of month'),
            date(d, 'start of month', '+1 month', '-1 day'),
            date(printf('%04d-%02d-01', y, ((q - 1) * 3) + 1)),
            date(printf('%04d-%02d-01', y, ((q - 1) * 3) + 1), '+3 months', '-1 day'),
            date(printf('%04d-%02d-01', y, CASE WHEN h = 1 THEN 1 ELSE 7 END)),
            date(printf('%04d-%02d-01', y, CASE WHEN h = 1 THEN 1 ELSE 7 END), '+6 months', '-1 day'),
            date(printf('%04d-01-01', y)),
            date(printf('%04d-12-31', y)),
            CAST(strftime('%w', d) AS INTEGER),
            CASE WHEN strftime('%w', d) IN ('0', '6') THEN 1 ELSE 0 END
        FROM base
    """)

    conn.execute("""
        CREATE TABLE dim_olist_customer AS
        SELECT
            customer_id,
            customer_unique_id,
            customer_zip_code_prefix AS zip_code_prefix,
            customer_city AS city,
            customer_state AS state
        FROM ods_olist_customers
    """)
    conn.execute('CREATE UNIQUE INDEX idx_dim_olist_customer_pk ON dim_olist_customer(customer_id)')

    conn.execute("""
        CREATE TABLE dim_olist_product AS
        SELECT
            p.product_id,
            p.product_category_name AS category_name_pt,
            COALESCE(t.product_category_name_english, p.product_category_name, 'unknown') AS category_name,
            CAST(NULLIF(p.product_name_lenght, '') AS INTEGER) AS name_length,
            CAST(NULLIF(p.product_description_lenght, '') AS INTEGER) AS description_length,
            CAST(NULLIF(p.product_photos_qty, '') AS INTEGER) AS photos_qty,
            CAST(NULLIF(p.product_weight_g, '') AS REAL) AS weight_g,
            CAST(NULLIF(p.product_length_cm, '') AS REAL) AS length_cm,
            CAST(NULLIF(p.product_height_cm, '') AS REAL) AS height_cm,
            CAST(NULLIF(p.product_width_cm, '') AS REAL) AS width_cm
        FROM ods_olist_products p
        LEFT JOIN ods_olist_category_translation t
          ON t.product_category_name = p.product_category_name
    """)
    conn.execute('CREATE UNIQUE INDEX idx_dim_olist_product_pk ON dim_olist_product(product_id)')

    conn.execute("""
        CREATE TABLE dim_olist_seller AS
        SELECT
            seller_id,
            seller_zip_code_prefix AS zip_code_prefix,
            seller_city AS city,
            seller_state AS state
        FROM ods_olist_sellers
    """)
    conn.execute('CREATE UNIQUE INDEX idx_dim_olist_seller_pk ON dim_olist_seller(seller_id)')

    conn.execute("""
        CREATE TABLE dim_olist_geography AS
        SELECT
            geolocation_zip_code_prefix AS zip_code_prefix,
            MIN(geolocation_city) AS city,
            MIN(geolocation_state) AS state,
            AVG(CAST(NULLIF(geolocation_lat, '') AS REAL)) AS latitude,
            AVG(CAST(NULLIF(geolocation_lng, '') AS REAL)) AS longitude,
            COUNT(*) AS source_rows
        FROM ods_olist_geolocation
        GROUP BY geolocation_zip_code_prefix
    """)
    conn.execute('CREATE UNIQUE INDEX idx_dim_olist_geo_pk ON dim_olist_geography(zip_code_prefix)')
    conn.commit()
    return {"tables": {table: _row_count(conn, table) for table in DIM_TABLES}}


def build_detail(conn: sqlite3.Connection) -> dict:
    """Build DWD fact/detail tables from ODS and DIM."""
    for table in DWD_TABLES:
        conn.execute(f'DROP TABLE IF EXISTS "{table}"')

    conn.execute("""
        CREATE TABLE dwd_olist_order_items (
            order_id TEXT NOT NULL,
            order_item_id INTEGER NOT NULL,
            customer_id TEXT,
            product_id TEXT,
            seller_id TEXT,
            order_status TEXT,
            purchase_ts TEXT,
            purchase_date TEXT,
            purchase_year INTEGER,
            purchase_quarter INTEGER,
            purchase_month INTEGER,
            purchase_half INTEGER,
            product_category TEXT,
            customer_state TEXT,
            seller_state TEXT,
            price REAL,
            freight_value REAL,
            item_value REAL,
            net_item_value REAL,
            valid_sale INTEGER NOT NULL,
            delivered INTEGER NOT NULL,
            shipping_limit_date TEXT
        )
    """)
    conn.execute("""
        INSERT INTO dwd_olist_order_items
        SELECT
            oi.order_id,
            CAST(oi.order_item_id AS INTEGER),
            o.customer_id,
            oi.product_id,
            oi.seller_id,
            o.order_status,
            o.order_purchase_timestamp,
            date(o.order_purchase_timestamp),
            CAST(strftime('%Y', o.order_purchase_timestamp) AS INTEGER),
            ((CAST(strftime('%m', o.order_purchase_timestamp) AS INTEGER) - 1) / 3) + 1,
            CAST(strftime('%m', o.order_purchase_timestamp) AS INTEGER),
            CASE WHEN CAST(strftime('%m', o.order_purchase_timestamp) AS INTEGER) <= 6 THEN 1 ELSE 2 END,
            COALESCE(p.category_name, 'unknown'),
            c.state,
            s.state,
            CAST(NULLIF(oi.price, '') AS REAL),
            CAST(NULLIF(oi.freight_value, '') AS REAL),
            COALESCE(CAST(NULLIF(oi.price, '') AS REAL), 0)
                + COALESCE(CAST(NULLIF(oi.freight_value, '') AS REAL), 0),
            COALESCE(CAST(NULLIF(oi.price, '') AS REAL), 0),
            CASE WHEN lower(COALESCE(o.order_status, '')) IN ('canceled', 'unavailable') THEN 0 ELSE 1 END,
            CASE WHEN lower(COALESCE(o.order_status, '')) = 'delivered' THEN 1 ELSE 0 END,
            oi.shipping_limit_date
        FROM ods_olist_order_items oi
        LEFT JOIN ods_olist_orders o ON o.order_id = oi.order_id
        LEFT JOIN dim_olist_product p ON p.product_id = oi.product_id
        LEFT JOIN dim_olist_customer c ON c.customer_id = o.customer_id
        LEFT JOIN dim_olist_seller s ON s.seller_id = oi.seller_id
    """)
    conn.execute("""
        CREATE INDEX idx_dwd_olist_items_purchase
        ON dwd_olist_order_items(purchase_date, product_category, customer_state, seller_state)
    """)
    conn.execute("CREATE INDEX idx_dwd_olist_items_order ON dwd_olist_order_items(order_id)")

    conn.execute("""
        CREATE TABLE dwd_olist_payments AS
        SELECT
            order_id,
            CAST(NULLIF(payment_sequential, '') AS INTEGER) AS payment_sequential,
            payment_type,
            CAST(NULLIF(payment_installments, '') AS INTEGER) AS payment_installments,
            CAST(NULLIF(payment_value, '') AS REAL) AS payment_value
        FROM ods_olist_payments
    """)
    conn.execute("CREATE INDEX idx_dwd_olist_payments_order ON dwd_olist_payments(order_id)")

    conn.execute("""
        CREATE TABLE dwd_olist_reviews AS
        SELECT
            review_id,
            order_id,
            CAST(NULLIF(review_score, '') AS INTEGER) AS review_score,
            review_comment_title,
            review_comment_message,
            review_creation_date,
            review_answer_timestamp
        FROM ods_olist_reviews
    """)
    conn.execute("CREATE INDEX idx_dwd_olist_reviews_order ON dwd_olist_reviews(order_id)")

    conn.execute("""
        CREATE TABLE dwd_olist_order_fulfillment AS
        SELECT
            order_id,
            customer_id,
            order_status,
            order_purchase_timestamp AS purchase_ts,
            date(order_purchase_timestamp) AS purchase_date,
            order_approved_at,
            order_delivered_carrier_date,
            order_delivered_customer_date,
            order_estimated_delivery_date,
            CASE WHEN order_delivered_customer_date IS NOT NULL THEN 1 ELSE 0 END AS delivered,
            CASE
                WHEN order_delivered_customer_date IS NOT NULL
                 AND order_estimated_delivery_date IS NOT NULL
                 AND julianday(order_delivered_customer_date) > julianday(order_estimated_delivery_date)
                THEN 1 ELSE 0
            END AS late_delivery,
            ROUND(
                julianday(order_delivered_customer_date) - julianday(order_purchase_timestamp),
                2
            ) AS delivery_days
        FROM ods_olist_orders
    """)
    conn.execute("CREATE INDEX idx_dwd_olist_fulfillment_date ON dwd_olist_order_fulfillment(purchase_date)")
    conn.commit()
    return {"tables": {table: _row_count(conn, table) for table in DWD_TABLES}}


def build_summary(conn: sqlite3.Connection) -> dict:
    """Build DWS daily/period aggregates."""
    for table in DWS_TABLES:
        conn.execute(f'DROP TABLE IF EXISTS "{table}"')

    conn.execute("""
        CREATE TABLE dws_olist_sales_daily (
            sales_date TEXT NOT NULL,
            year INTEGER NOT NULL,
            quarter INTEGER NOT NULL,
            month INTEGER NOT NULL,
            half INTEGER NOT NULL,
            period_month TEXT NOT NULL,
            period_quarter TEXT NOT NULL,
            period_half TEXT NOT NULL,
            period_year TEXT NOT NULL,
            customer_state TEXT NOT NULL,
            seller_state TEXT NOT NULL,
            product_category TEXT NOT NULL,
            order_status TEXT NOT NULL,
            order_count INTEGER NOT NULL,
            item_count INTEGER NOT NULL,
            gross_sales REAL NOT NULL,
            net_sales REAL NOT NULL,
            freight_value REAL NOT NULL
        )
    """)
    conn.execute("""
        INSERT INTO dws_olist_sales_daily
        SELECT
            purchase_date,
            purchase_year,
            purchase_quarter,
            purchase_month,
            purchase_half,
            printf('%04d-%02d', purchase_year, purchase_month),
            printf('%04d-Q%d', purchase_year, purchase_quarter),
            printf('%04d-H%d', purchase_year, purchase_half),
            printf('%04d', purchase_year),
            COALESCE(customer_state, 'unknown'),
            COALESCE(seller_state, 'unknown'),
            COALESCE(product_category, 'unknown'),
            COALESCE(order_status, 'unknown'),
            COUNT(DISTINCT CASE WHEN valid_sale = 1 THEN order_id END),
            SUM(CASE WHEN valid_sale = 1 THEN 1 ELSE 0 END),
            ROUND(SUM(CASE WHEN valid_sale = 1 THEN item_value ELSE 0 END), 2),
            ROUND(SUM(CASE WHEN delivered = 1 THEN net_item_value ELSE 0 END), 2),
            ROUND(SUM(CASE WHEN valid_sale = 1 THEN freight_value ELSE 0 END), 2)
        FROM dwd_olist_order_items
        WHERE purchase_date IS NOT NULL
        GROUP BY
            purchase_date, purchase_year, purchase_quarter, purchase_month, purchase_half,
            customer_state, seller_state, product_category, order_status
    """)
    conn.execute("""
        CREATE INDEX idx_dws_olist_daily_period
        ON dws_olist_sales_daily(period_month, period_quarter, period_half, period_year)
    """)

    conn.execute("""
        CREATE TABLE dws_olist_sales_period (
            period_type TEXT NOT NULL,
            period_key TEXT NOT NULL,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            day_count INTEGER NOT NULL,
            expected_days INTEGER NOT NULL,
            dimension_type TEXT NOT NULL,
            dimension_value TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            metric_value REAL NOT NULL,
            order_count INTEGER NOT NULL,
            item_count INTEGER NOT NULL
        )
    """)

    period_configs = (
        ("month", "d.period_month", "dt.month_start", "dt.month_end"),
        ("quarter", "d.period_quarter", "dt.quarter_start", "dt.quarter_end"),
        ("half", "d.period_half", "dt.half_start", "dt.half_end"),
        ("year", "d.period_year", "dt.year_start", "dt.year_end"),
    )
    dimension_configs = (
        ("overall", "'ALL'"),
        ("customer_state", "d.customer_state"),
        ("seller_state", "d.seller_state"),
        ("product_category", "d.product_category"),
    )
    metric_exprs = {
        "gross_sales": "SUM(d.gross_sales)",
        "net_sales": "SUM(d.net_sales)",
        "order_count": "SUM(d.order_count)",
        "item_count": "SUM(d.item_count)",
    }
    for period_type, key_expr, start_expr, end_expr in period_configs:
        for dimension_type, dimension_expr in dimension_configs:
            for metric_name, metric_expr in metric_exprs.items():
                conn.execute(
                    f"""
                    INSERT INTO dws_olist_sales_period
                    SELECT
                        ?,
                        {key_expr},
                        MIN({start_expr}),
                        MAX({end_expr}),
                        COUNT(DISTINCT d.sales_date),
                        CAST(julianday(MAX({end_expr})) - julianday(MIN({start_expr})) + 1 AS INTEGER),
                        ?,
                        {dimension_expr},
                        ?,
                        ROUND({metric_expr}, 2),
                        SUM(d.order_count),
                        SUM(d.item_count)
                    FROM dws_olist_sales_daily d
                    JOIN dim_olist_date dt ON dt."date" = d.sales_date
                    GROUP BY {key_expr}, {dimension_expr}
                    """,
                    (period_type, dimension_type, metric_name),
                )
    conn.execute("""
        CREATE INDEX idx_dws_olist_period_lookup
        ON dws_olist_sales_period(period_type, period_key, dimension_type, dimension_value, metric_name)
    """)
    conn.commit()
    return {"tables": {table: _row_count(conn, table) for table in DWS_TABLES}}


def build_ads(conn: sqlite3.Connection) -> dict:
    """Build application-serving period metrics and adjacent comparisons."""
    for table in ADS_TABLES:
        conn.execute(f'DROP TABLE IF EXISTS "{table}"')

    conn.execute("""
        CREATE TABLE ads_olist_metric_catalog (
            metric_name TEXT PRIMARY KEY,
            metric_label TEXT NOT NULL,
            definition TEXT NOT NULL,
            unit TEXT NOT NULL
        )
    """)
    conn.executemany(
        "INSERT INTO ads_olist_metric_catalog VALUES (?, ?, ?, ?)",
        [
            (name, spec["label"], spec["definition"], spec["unit"])
            for name, spec in METRICS.items()
        ],
    )

    conn.execute(f"""
        CREATE TABLE ads_olist_period_metrics AS
        SELECT
            *,
            '{DATASET_VERSION}' AS data_version
        FROM dws_olist_sales_period
    """)
    conn.execute("""
        CREATE INDEX idx_ads_olist_period_metrics_lookup
        ON ads_olist_period_metrics(
            metric_name, dimension_type, dimension_value, period_type, period_key
        )
    """)

    conn.execute("""
        CREATE TABLE ads_olist_period_comparison (
            period_type TEXT NOT NULL,
            period_key TEXT NOT NULL,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            day_count INTEGER NOT NULL,
            expected_days INTEGER NOT NULL,
            dimension_type TEXT NOT NULL,
            dimension_value TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            metric_value REAL NOT NULL,
            order_count INTEGER NOT NULL,
            item_count INTEGER NOT NULL,
            data_version TEXT NOT NULL,
            previous_period_key TEXT,
            previous_period_start TEXT,
            previous_period_end TEXT,
            previous_metric_value REAL,
            absolute_change REAL,
            growth_rate REAL,
            is_adjacent INTEGER NOT NULL,
            warning_code TEXT NOT NULL
        )
    """)
    conn.execute("""
        WITH ordered AS (
            SELECT
                *,
                LAG(period_key) OVER w AS previous_period_key,
                LAG(period_start) OVER w AS previous_period_start,
                LAG(period_end) OVER w AS previous_period_end,
                LAG(metric_value) OVER w AS previous_metric_value,
                LAG(day_count) OVER w AS previous_day_count,
                LAG(expected_days) OVER w AS previous_expected_days
            FROM ads_olist_period_metrics
            WINDOW w AS (
                PARTITION BY metric_name, dimension_type, dimension_value, period_type
                ORDER BY period_start
            )
        )
        INSERT INTO ads_olist_period_comparison
        SELECT
            period_type,
            period_key,
            period_start,
            period_end,
            day_count,
            expected_days,
            dimension_type,
            dimension_value,
            metric_name,
            metric_value,
            order_count,
            item_count,
            data_version,
            previous_period_key,
            previous_period_start,
            previous_period_end,
            previous_metric_value,
            CASE
                WHEN previous_metric_value IS NULL THEN NULL
                ELSE metric_value - previous_metric_value
            END,
            CASE
                WHEN previous_metric_value IS NULL OR previous_metric_value = 0 THEN NULL
                ELSE (metric_value - previous_metric_value) / previous_metric_value
            END,
            CASE
                WHEN previous_period_end IS NOT NULL
                 AND julianday(period_start) = julianday(previous_period_end) + 1
                THEN 1 ELSE 0
            END,
            CASE
                WHEN previous_period_key IS NULL THEN 'missing_previous'
                WHEN previous_metric_value = 0 THEN 'zero_base'
                WHEN day_count < expected_days THEN 'incomplete_current_period'
                WHEN previous_day_count < previous_expected_days THEN 'incomplete_previous_period'
                ELSE ''
            END
        FROM ordered
        WHERE previous_period_key IS NOT NULL
    """)
    conn.execute("""
        CREATE INDEX idx_ads_olist_period_compare_lookup
        ON ads_olist_period_comparison(
            metric_name, dimension_type, dimension_value, period_type, period_key
        )
    """)
    conn.commit()
    return {"tables": {table: _row_count(conn, table) for table in ADS_TABLES}}


def build_all_layers(conn: sqlite3.Connection) -> dict:
    """Build all non-ODS warehouse layers. ODS must already be loaded."""
    return {
        "dim": build_dimensions(conn),
        "dwd": build_detail(conn),
        "dws": build_summary(conn),
        "ads": build_ads(conn),
    }


def _row_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def validate_warehouse(conn: sqlite3.Connection) -> dict:
    """Run deterministic completeness and sanity checks."""
    checks: list[dict] = []
    table_counts = {
        table: _row_count(conn, table)
        for table in OLIST_WAREHOUSE_TABLES
    }
    required_non_empty = (
        "ods_olist_orders",
        "dim_olist_date",
        "dwd_olist_order_items",
        "dws_olist_sales_daily",
        "dws_olist_sales_period",
        "ads_olist_period_metrics",
        "ads_olist_period_comparison",
    )
    for table in required_non_empty:
        checks.append({
            "check": f"{table}_non_empty",
            "ok": table_counts.get(table, 0) > 0,
            "value": table_counts.get(table, 0),
        })

    bad_periods = conn.execute("""
        SELECT COUNT(*)
        FROM ads_olist_period_metrics
        WHERE day_count > expected_days
    """).fetchone()[0]
    checks.append({"check": "period_day_count_lte_expected", "ok": bad_periods == 0, "value": bad_periods})

    invalid_sales = conn.execute("""
        SELECT COUNT(*)
        FROM ads_olist_period_metrics
        WHERE metric_name IN ('gross_sales', 'net_sales') AND metric_value < 0
    """).fetchone()[0]
    checks.append({"check": "non_negative_sales", "ok": invalid_sales == 0, "value": invalid_sales})

    orphan_items = conn.execute("""
        SELECT COUNT(*)
        FROM dwd_olist_order_items i
        LEFT JOIN ods_olist_orders o ON o.order_id = i.order_id
        WHERE o.order_id IS NULL
    """).fetchone()[0]
    checks.append({"check": "order_items_have_orders", "ok": orphan_items == 0, "value": orphan_items})

    return {
        "ok": all(check["ok"] for check in checks),
        "checks": checks,
        "table_counts": table_counts,
    }


def compare_olist_periods(
    metric_name: str,
    period_a: str,
    period_b: str,
    dimension_type: str = "overall",
    dimension_value: str = "ALL",
    db_path: str | Path = WAREHOUSE_DB_PATH,
) -> dict:
    """Compare two period keys from ADS, with explicit data-quality warnings."""
    if metric_name not in METRICS:
        return {"error": True, "message": f"未知指标: {metric_name}", "available": list(METRICS)}
    if dimension_type not in {"overall", "customer_state", "seller_state", "product_category"}:
        return {"error": True, "message": f"未知维度: {dimension_type}"}

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT *
            FROM ads_olist_period_metrics
            WHERE metric_name = ?
              AND dimension_type = ?
              AND dimension_value = ?
              AND period_key IN (?, ?)
            ORDER BY period_start
        """, (metric_name, dimension_type, dimension_value, period_a, period_b)).fetchall()
    except sqlite3.Error as exc:
        return {"error": True, "message": str(exc), "hint": "请先运行 scripts/build_olist_warehouse.py"}
    finally:
        conn.close()

    by_key = {row["period_key"]: dict(row) for row in rows}
    if period_a not in by_key or period_b not in by_key:
        missing = [key for key in (period_a, period_b) if key not in by_key]
        return {
            "error": True,
            "message": "期间不存在或该维度没有数据",
            "missing_periods": missing,
            "available_periods": sorted({
                row["period_key"]
                for row in rows
            }),
        }

    a, b = by_key[period_a], by_key[period_b]
    absolute_change = float(b["metric_value"]) - float(a["metric_value"])
    growth_rate = (
        absolute_change / float(a["metric_value"])
        if float(a["metric_value"]) != 0
        else None
    )
    warnings = []
    if float(a["day_count"]) < float(a["expected_days"]):
        warnings.append("period_a_incomplete")
    if float(b["day_count"]) < float(b["expected_days"]):
        warnings.append("period_b_incomplete")
    if float(a["metric_value"]) == 0:
        warnings.append("zero_base")

    return {
        "metric": {"name": metric_name, **METRICS[metric_name]},
        "dimension": {"type": dimension_type, "value": dimension_value},
        "period_a": a,
        "period_b": b,
        "absolute_change": round(absolute_change, 2),
        "growth_rate": round(growth_rate, 6) if growth_rate is not None else None,
        "growth_rate_pct": round(growth_rate * 100, 2) if growth_rate is not None else None,
        "direction": "up" if absolute_change > 0 else ("down" if absolute_change < 0 else "flat"),
        "warnings": warnings,
    }
