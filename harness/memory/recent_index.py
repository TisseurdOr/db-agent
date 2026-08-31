"""Append-only recent-id index — list_recent without full-collection scan.

Chroma/Milvus get(limit=N) is not "most recent N by timestamp". We append
(id, ts, user_id, memory_type) on every remember and read the tail for list_recent.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_DB_DIR = Path(__file__).resolve().parents[2] / "db"
_INDEX_DIR = _DB_DIR / "memory_index"


def _path(collection_name: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in collection_name)
    return _INDEX_DIR / f"{safe}_recent.jsonl"


def append_recent(
    collection_name: str,
    memory_id: str,
    *,
    user_id: str = "default",
    timestamp: str = "",
    memory_type: str = "conversation",
) -> None:
    try:
        _INDEX_DIR.mkdir(parents=True, exist_ok=True)
        path = _path(collection_name)
        row = {
            "id": memory_id,
            "user_id": user_id or "default",
            "timestamp": timestamp,
            "memory_type": memory_type,
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def list_recent_ids(
    collection_name: str,
    *,
    user_id: str = "default",
    limit: int = 10,
    memory_type: str | None = None,
) -> list[str]:
    """Return newest-first memory ids from the append-only index (tail scan)."""
    if limit <= 0:
        return []
    path = _path(collection_name)
    if not path.exists():
        return []
    # Read last ~N*20 lines (cheap upper bound) then filter — avoids full file for huge indexes.
    try:
        # For multi-GB indexes we'd use seek; demo-scale: read all lines is still better
        # than Chroma full get of documents. Cap lines scanned.
        max_scan = int(os.getenv("MEMORY_RECENT_INDEX_SCAN", "5000"))
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) > max_scan:
            lines = lines[-max_scan:]
    except OSError:
        return []

    matched: list[str] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if user_id and row.get("user_id", "default") != user_id:
            continue
        if memory_type and row.get("memory_type") != memory_type:
            continue
        mid = row.get("id")
        if mid:
            matched.append(mid)
        if len(matched) >= limit:
            break
    return matched


def drop_index(collection_name: str) -> None:
    path = _path(collection_name)
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass
