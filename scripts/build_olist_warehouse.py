"""Build the Olist ODS -> DIM -> DWD -> DWS -> ADS warehouse.

Usage:
    python scripts/build_olist_warehouse.py
    python scripts/build_olist_warehouse.py --force-download
    python scripts/build_olist_warehouse.py --validate-only
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from db.olist_warehouse import (  # noqa: E402
    DATASET_ID,
    DATASET_VERSION,
    DEFAULT_RAW_DIR,
    WAREHOUSE_DB_PATH,
    build_all_layers,
    download_dataset,
    load_ods,
    validate_warehouse,
)


def _print_step(name: str, payload: dict) -> None:
    print(json.dumps({"step": name, **payload}, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build local Olist warehouse layers")
    parser.add_argument("--db", default=str(WAREHOUSE_DB_PATH), help="target SQLite database")
    parser.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR), help="raw CSV directory")
    parser.add_argument("--force-download", action="store_true", help="re-download raw CSVs")
    parser.add_argument("--validate-only", action="store_true", help="only validate existing layers")
    parser.add_argument("--batch-id", default="", help="ODS ingestion batch id")
    args = parser.parse_args()

    db_path = Path(args.db)
    raw_dir = Path(args.raw_dir)
    conn = sqlite3.connect(str(db_path), timeout=120)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        if args.validate_only:
            result = validate_warehouse(conn)
            _print_step("validate", result)
            return 0 if result["ok"] else 1

        _print_step("source", {"dataset_id": DATASET_ID, "dataset_version": DATASET_VERSION})
        download_result = download_dataset(raw_dir, force=args.force_download)
        _print_step("download", download_result)
        ods_result = load_ods(conn, raw_dir, batch_id=args.batch_id or None)
        _print_step("ods", ods_result)
        layer_result = build_all_layers(conn)
        _print_step("layers", layer_result)
        validation = validate_warehouse(conn)
        _print_step("validate", validation)
        return 0 if validation["ok"] else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
