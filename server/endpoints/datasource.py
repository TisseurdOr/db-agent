"""POST /api/datasource/upload — CSV upload, /connect — external DB."""

import csv
import os
import sqlite3
import tempfile
from io import StringIO
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Form
from pydantic import BaseModel

router = APIRouter()

_UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "db" / "uploads"
_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


class ConnectRequest(BaseModel):
    type: str  # "sqlite"
    path: str | None = None


@router.post("/datasource/upload")
async def upload_csv(file: UploadFile = File(...)):
    """Upload a CSV file, import into SQLite, return table info."""
    content = await file.read()
    text = content.decode("utf-8")
    reader = csv.DictReader(StringIO(text))
    rows = list(reader)
    columns = reader.fieldnames or []

    # Create table in uploads DB
    table_name = f"uploaded_{file.filename.replace('.csv', '').replace('.', '_')}"
    db_path = _UPLOAD_DIR / "uploads.db"
    conn = sqlite3.connect(str(db_path))

    col_defs = ", ".join(f'"{c}" TEXT' for c in columns)
    conn.execute(f'DROP TABLE IF EXISTS "{table_name}"')
    conn.execute(f'CREATE TABLE "{table_name}" ({col_defs})')

    for row in rows:
        values = [row.get(c, "") for c in columns]
        placeholders = ", ".join("?" for _ in columns)
        conn.execute(f'INSERT INTO "{table_name}" VALUES ({placeholders})', values)

    conn.commit()
    conn.close()

    return {
        "ok": True,
        "table_name": table_name,
        "columns": columns,
        "row_count": len(rows),
    }


@router.post("/datasource/connect")
async def connect_datasource(req: ConnectRequest):
    """Connect to an external SQLite database."""
    if req.type == "sqlite":
        db_path = req.path
        if not db_path or not os.path.exists(db_path):
            return {"ok": False, "error": "数据库文件不存在"}

        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row[0] for row in cursor.fetchall()]
        conn.close()

        return {"ok": True, "tables": tables}

    return {"ok": False, "error": f"不支持的数据源类型: {req.type}"}
