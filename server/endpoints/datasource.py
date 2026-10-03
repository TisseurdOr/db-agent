"""POST /api/datasource/upload — CSV upload, /connect — external DB.

安全要点：
- 表名/列名来自用户输入（文件名 / CSV 表头），一律转义后再拼进 DDL，
  否则表头里的 `"` 会破坏引号、注入列定义。
- /connect 只允许打开 DATASOURCE_ALLOWED_DIR（默认项目根目录）内的 SQLite 文件，
  避免任意路径读取（path traversal）。
"""

import csv
import os
import re
import sqlite3
from io import StringIO
from pathlib import Path

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel

router = APIRouter()

ROOT = Path(__file__).resolve().parents[2]
_UPLOAD_DIR = ROOT / "db" / "uploads"
_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# SQLite 允许打开的文件必须落在该目录内（可用环境变量覆盖）
_ALLOWED_BASE = Path(os.getenv("DATASOURCE_ALLOWED_DIR", str(ROOT))).resolve()


class ConnectRequest(BaseModel):
    type: str  # "sqlite"
    path: str | None = None


def _quote_ident(name: str) -> str:
    """转义 SQLite 标识符：双引号翻倍后用双引号包裹。"""
    return '"' + name.replace('"', '""') + '"'


def _safe_table_name(filename: str | None) -> str:
    """从文件名派生安全表名：只保留字母数字下划线。"""
    stem = (filename or "upload.csv").replace(".csv", "")
    safe = re.sub(r"[^0-9A-Za-z_]", "_", stem).strip("_")
    return f"uploaded_{safe or 'table'}"


def _resolve_allowed_db(raw_path: str | None) -> Path | None:
    """把请求路径解析为绝对路径，且必须位于 _ALLOWED_BASE 之内。"""
    if not raw_path:
        return None
    try:
        p = Path(raw_path).expanduser().resolve()
        p.relative_to(_ALLOWED_BASE)  # 不在允许目录内 → ValueError
    except (ValueError, OSError):
        return None
    return p


@router.post("/datasource/upload")
async def upload_csv(file: UploadFile = File(...)):
    """Upload a CSV file, import into SQLite, return table info."""
    content = await file.read()
    text = content.decode("utf-8")
    reader = csv.DictReader(StringIO(text))
    rows = list(reader)
    columns = reader.fieldnames or []
    if not columns:
        return {"ok": False, "error": "CSV 没有表头（第一行需为列名）"}

    table_name = _safe_table_name(file.filename)
    db_path = _UPLOAD_DIR / "uploads.db"
    conn = sqlite3.connect(str(db_path))

    col_defs = ", ".join(f"{_quote_ident(c)} TEXT" for c in columns)
    conn.execute(f"DROP TABLE IF EXISTS {_quote_ident(table_name)}")
    conn.execute(f"CREATE TABLE {_quote_ident(table_name)} ({col_defs})")

    for row in rows:
        values = [row.get(c, "") for c in columns]
        placeholders = ", ".join("?" for _ in columns)
        conn.execute(f"INSERT INTO {_quote_ident(table_name)} VALUES ({placeholders})", values)

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
    """Connect to an external SQLite database（限允许目录内）。"""
    if req.type == "sqlite":
        db_path = _resolve_allowed_db(req.path)
        if db_path is None or not db_path.is_file():
            return {"ok": False, "error": "数据库文件不存在，或不在允许目录内"}

        try:
            conn = sqlite3.connect(str(db_path))
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = [row[0] for row in cursor.fetchall()]
            conn.close()
        except sqlite3.Error:
            return {"ok": False, "error": "不是有效的 SQLite 数据库文件"}

        return {"ok": True, "tables": tables}

    return {"ok": False, "error": f"不支持的数据源类型: {req.type}"}
