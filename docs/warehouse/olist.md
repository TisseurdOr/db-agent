# Olist Public E-commerce Warehouse

## Scope

This warehouse uses the public Olist Brazilian e-commerce dataset as ODS test data. It is **public test data, not real user feedback** and cannot be used as evidence of commercial data-flywheel growth.

Pinned Hugging Face mirror:

- Dataset: `MafiaAzulBr/olistcsv`
- Revision: `2bf427f68bf25787d2a5a305306feaa0d074bbe9`
- Mirror metadata: `license: apache-2.0`

The upstream Olist dataset and its original terms must be reviewed before redistribution or commercial use. Keep the source URL, revision, and this notice with derived data.

## Build

```bash
.venv/bin/python scripts/build_olist_warehouse.py
```

The script downloads the raw CSV files to `data/raw/olist/`, builds the independent SQLite warehouse at `db/warehouse.db`, and rebuilds:

- `ods_olist_*` — source-shaped raw data
- `dim_olist_*` — date, customer, product, seller, geography
- `dwd_olist_*` — order-item, payment, review, fulfillment facts
- `dws_olist_*` — daily and period sales summaries
- `ads_olist_*` — metric catalog, period metrics, adjacent-period comparisons

Raw CSV files and SQLite databases are Git-ignored.

## Period Keys

| Period type | Example |
|---|---|
| Month | `2018-07` |
| Quarter | `2018-Q3` |
| Half year | `2018-H2` |
| Year | `2018` |

A period-over-period comparison requires adjacent periods with the same calendar granularity (month, quarter, half-year, or year). Non-adjacent periods are custom period comparisons and must be labelled that way.

## Metrics

| Metric | Definition |
|---|---|
| `gross_sales` | `price + freight_value` for valid order items; excludes `canceled` and `unavailable` |
| `net_sales` | `price` for delivered order items |
| `order_count` | distinct valid orders |
| `item_count` | valid order-item rows |

## Query

Use the deterministic SQL/Hive agent tool:

```text
query_period_comparison(
  metric_name="gross_sales",
  period_a="2018-H1",
  period_b="2018-H2",
  dimension_type="overall"
)
```

Or query ADS directly:

```sql
SELECT
    period_key,
    metric_value,
    absolute_change,
    growth_rate,
    warning_code
FROM ads_olist_period_comparison
WHERE metric_name = 'gross_sales'
  AND dimension_type = 'overall'
  AND dimension_value = 'ALL'
  AND period_type = 'half'
ORDER BY period_start;
```

## Validation

```bash
.venv/bin/python scripts/build_olist_warehouse.py --validate-only
```

The validation checks layer completeness, non-negative sales, period day counts, and order-item referential integrity.
