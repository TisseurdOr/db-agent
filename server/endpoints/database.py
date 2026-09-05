"""GET/POST /api/database — 仿 waku Database 页：浏览本地持久化层。

demo.db 按引擎分成三类：SQL（关系业务表）/ Hive（分层模拟表）/ HBase（内存 KV）。
另可切换 agent_state、metric_registry、traces；uploads 仅在文件存在时出现。

只读：SQLite 用 mode=ro；SQL console 走 guard_sql；HBase 为进程内快照。
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from harness.constraints.entitlement import can_access_database, get_user
from harness.constraints.guardrails import DANGEROUS_SQL_KEYWORDS, guard_sql
from harness.observation.tracer import TRACE_DIR, _read_traces

router = APIRouter()

ROOT = Path(__file__).resolve().parents[2]

# demo.db 内表按引擎分组（面试/演示口径）
DEMO_SQL_TABLES = {"departments", "employees", "products", "customers", "orders"}
DEMO_HIVE_TABLES = {"ods_orders_hive", "dwd_user_events", "dim_products_hive"}
DEMO_META_TABLES = {"agent_roles", "agent_users", "user_memory", "user_feedback"}

TABLE_DESC: dict[str, dict[str, str]] = {
    "demo": {
        "user_memory": "长期结构化记忆 — preference / insight / note / entity",
        "user_feedback": "Web 点赞/点踩回流（可触发 few-shot 学习）",
        "agent_roles": "RBAC 角色定义（工具白名单 · 表白名单 · 行级过滤）",
        "agent_users": "演示用户与角色绑定",
        "departments": "SQL：部门维表",
        "employees": "SQL：员工（含敏感列 salary）",
        "products": "SQL：产品",
        "customers": "SQL：客户",
        "orders": "SQL：订单事实表",
        "ods_orders_hive": "Hive：ods 订单层",
        "dwd_user_events": "Hive：dwd 用户事件",
        "dim_products_hive": "Hive：产品维",
    },
    "agent_state": {
        "checkpoints": "LangGraph Checkpointer — HITL resume 用的图状态",
        "writes": "LangGraph 中间写入通道",
    },
    "metric_registry": {
        "metric_registry": "指标口径 / SQL 模板（template matcher）",
    },
    "hbase": {
        "orders": "HBase 模拟：订单宽表（cf:*）",
        "user_profile": "HBase 模拟：用户画像（info / behavior）",
        "product_catalog": "HBase 模拟：商品目录（meta / stock）",
    },
}

STORES: dict[str, dict[str, Any]] = {
    "demo": {
        "id": "demo",
        "label": "demo.db",
        "path": ROOT / "db" / "demo.db",
        "kind": "sqlite",
        "primary": True,
        "summary": "演示主库：SQL 业务表 + Hive 分层表（同文件）· HBase 为内存引擎",
    },
    "hbase": {
        "id": "hbase",
        "label": "HBase (memory)",
        "path": None,
        "kind": "hbase",
        "primary": False,
        "summary": "进程内 HBase KV 模拟（_HBASE_STORE，非 SQLite 文件）",
    },
    "agent_state": {
        "id": "agent_state",
        "label": "agent_state.db",
        "path": ROOT / "db" / "agent_state.db",
        "kind": "sqlite",
        "primary": False,
        "summary": "LangGraph Checkpointer（HITL / 会话状态）",
    },
    "metric_registry": {
        "id": "metric_registry",
        "label": "metric_registry.db",
        "path": ROOT / "db" / "metric_registry.db",
        "kind": "sqlite",
        "primary": False,
        "summary": "指标模板注册表",
    },
    "uploads": {
        "id": "uploads",
        "label": "uploads.db",
        "path": ROOT / "db" / "uploads" / "uploads.db",
        "kind": "sqlite",
        "primary": False,
        "summary": "CSV 上传落地库（上传后才创建）",
    },
    "traces": {
        "id": "traces",
        "label": "traces/*.jsonl",
        "path": TRACE_DIR,
        "kind": "jsonl",
        "primary": False,
        "summary": "每轮 Trace（零依赖 JSONL，Overview 同源）",
    },
}

SAMPLE_LIMIT = 80
QUERY_LIMIT = 200


def _store_meta(store_id: str) -> dict[str, Any]:
    if store_id not in STORES:
        raise HTTPException(404, f"unknown store: {store_id}")
    return STORES[store_id]


def _connect_ro(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise HTTPException(404, f"database file missing: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _cell(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (bytes, memoryview)):
        return f"<blob {len(v)} bytes>"
    s = str(v)
    if len(s) > 500:
        return s[:500] + "…"
    return v if isinstance(v, (int, float, str, bool)) else s


def _list_sqlite_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def _column_types(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    info = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    return {r[1]: (r[2] or "ANY") for r in info}


def _sample_rows(conn: sqlite3.Connection, table: str, limit: int) -> tuple[list[str], list[dict]]:
    cols = list(_column_types(conn, table).keys())
    if not cols:
        return [], []
    order = ""
    lower = {c.lower(): c for c in cols}
    for candidate in ("created_at", "id", "checkpoint_id", "started_at"):
        if candidate in lower:
            order = f' ORDER BY "{lower[candidate]}" DESC'
            break
    rows = conn.execute(f'SELECT * FROM "{table}"{order} LIMIT ?', (limit,)).fetchall()
    sample = [{c: _cell(r[c]) for c in cols} for r in rows]
    return cols, sample


def _demo_group(name: str) -> str:
    if name in DEMO_SQL_TABLES:
        return "sql"
    if name in DEMO_HIVE_TABLES:
        return "hive"
    if name in DEMO_META_TABLES:
        return "meta"
    return "other"


def _sqlite_overview(store_id: str) -> dict[str, Any]:
    meta = _store_meta(store_id)
    path: Path = meta["path"]
    base = {
        "id": store_id,
        "label": meta["label"],
        "kind": "sqlite",
        "primary": meta["primary"],
        "summary": meta["summary"],
        "path": str(path.resolve()) if path.exists() else str(path),
    }
    if not path.exists():
        return {**base, "exists": False, "size": 0, "tables": [], "all_tables": [], "groups": {}}
    conn = _connect_ro(path)
    try:
        names = _list_sqlite_tables(conn)
        desc_map = TABLE_DESC.get(store_id, {})
        tables = []
        for name in names:
            try:
                count = _table_count(conn, name)
            except sqlite3.Error:
                count = 0
            tables.append({
                "name": name,
                "count": count,
                "description": desc_map.get(name, ""),
                "group": _demo_group(name) if store_id == "demo" else "other",
            })
        groups: dict[str, list[dict]] = {}
        if store_id == "demo":
            for g in ("sql", "hive", "meta", "other"):
                items = [t for t in tables if t["group"] == g]
                if items:
                    groups[g] = items
        return {
            **base,
            "exists": True,
            "size": path.stat().st_size,
            "tables": tables,
            "all_tables": names,
            "groups": groups,
        }
    finally:
        conn.close()


def _hbase_overview() -> dict[str, Any]:
    from harness.tools.hbase import (
        _HBASE_DISABLED,
        _HBASE_META,
        _HBASE_STORE,
        _seed_hbase_store,
    )

    _seed_hbase_store()
    desc = TABLE_DESC.get("hbase", {})
    tables = []
    for name in sorted(_HBASE_META.keys()):
        tables.append({
            "name": name,
            "count": len(_HBASE_STORE.get(name, {})),
            "description": desc.get(name, ""),
            "column_families": list(_HBASE_META.get(name, [])),
            "disabled": name in _HBASE_DISABLED,
            "group": "hbase",
        })
    return {
        "id": "hbase",
        "label": "HBase (memory)",
        "kind": "hbase",
        "primary": False,
        "summary": STORES["hbase"]["summary"],
        "path": "in-process · harness.tools.hbase._HBASE_STORE",
        "exists": True,
        "size": 0,
        "tables": tables,
        "all_tables": [t["name"] for t in tables],
        "groups": {"hbase": tables},
    }


def _hbase_table(table_name: str, limit: int) -> dict[str, Any]:
    from harness.tools.hbase import (
        _HBASE_META,
        _HBASE_ROW_ORDER,
        _HBASE_STORE,
        _seed_hbase_store,
    )

    _seed_hbase_store()
    if table_name not in _HBASE_META:
        raise HTTPException(404, f"HBase table not found: {table_name}")

    store = _HBASE_STORE.get(table_name, {})
    order = _HBASE_ROW_ORDER.get(table_name, list(store.keys()))
    # Collect column universe
    col_set: set[str] = set()
    for rk in order:
        col_set.update(store.get(rk, {}).keys())
    columns = ["row_key"] + sorted(col_set)
    sample = []
    for rk in order[:limit]:
        row = {"row_key": rk}
        cells = store.get(rk, {})
        for c in columns[1:]:
            row[c] = cells.get(c)
        sample.append(row)
    types = {c: "TEXT" for c in columns}
    return {
        "store": "hbase",
        "name": table_name,
        "description": TABLE_DESC.get("hbase", {}).get(table_name, ""),
        "count": len(store),
        "columns": columns,
        "types": types,
        "sample": sample,
        "limit": limit,
        "column_families": list(_HBASE_META.get(table_name, [])),
    }


def _traces_overview() -> dict[str, Any]:
    meta = STORES["traces"]
    path: Path = meta["path"]
    files = sorted(path.glob("*.jsonl"), reverse=True) if path.exists() else []
    total = 0
    for f in files:
        with f.open(encoding="utf-8") as fh:
            total += sum(1 for line in fh if line.strip())
    size = sum(f.stat().st_size for f in files)
    tables = [{
        "name": "traces",
        "count": total,
        "description": "结构化 Trace：query / spans / tokens / errors",
        "group": "other",
    }]
    return {
        "id": "traces",
        "label": meta["label"],
        "kind": "jsonl",
        "primary": False,
        "summary": meta["summary"],
        "path": str(path.resolve()),
        "exists": path.exists(),
        "size": size,
        "tables": tables,
        "all_tables": ["traces"],
        "groups": {},
        "files": [f.name for f in files[:14]],
    }


def _traces_table(limit: int) -> dict[str, Any]:
    files = sorted(TRACE_DIR.glob("*.jsonl"), reverse=True) if TRACE_DIR.exists() else []
    traces: list[dict] = []
    for f in files:
        traces.extend(_read_traces(f))
    traces.sort(key=lambda t: t.get("started_at", ""), reverse=True)
    columns = ["trace_id", "query", "started_at", "elapsed", "blocked_by", "span_count", "total_tokens", "error"]
    sample = []
    for t in traces[:limit]:
        spans = t.get("spans") or []
        err = next((s.get("error") for s in spans if s.get("error")), None)
        totals = t.get("totals") or {}
        sample.append({
            "trace_id": t.get("trace_id", ""),
            "query": str(t.get("query", ""))[:200],
            "started_at": t.get("started_at", ""),
            "elapsed": t.get("elapsed"),
            "blocked_by": t.get("blocked_by"),
            "span_count": totals.get("span_count", len(spans)),
            "total_tokens": totals.get("total_tokens", 0),
            "error": err,
        })
    types = {c: "TEXT" for c in columns}
    types.update({"elapsed": "REAL", "span_count": "INTEGER", "total_tokens": "INTEGER"})
    return {
        "store": "traces",
        "name": "traces",
        "description": "结构化 Trace：query / spans / tokens / errors",
        "count": len(traces),
        "columns": columns,
        "types": types,
        "sample": sample,
        "limit": limit,
    }


def _store_exists(sid: str) -> bool:
    meta = STORES[sid]
    if meta["kind"] == "hbase":
        return True
    if meta["kind"] == "jsonl":
        return True
    path: Path | None = meta.get("path")
    return bool(path and path.exists())



def _require_db_browser(user_id: str | None, x_agent_user: str | None = None) -> dict:
    """Database 页与 Agent 工具权限对齐：无 list_tables/run_query 则 403。

    身份来源：query user_id → 头 X-Agent-User → viewer（缺省不放行）。
    """
    uid = (user_id or x_agent_user or "").strip() or "viewer"
    user = get_user(uid)
    if not can_access_database(user):
        raise HTTPException(
            status_code=403,
            detail={
                "error": "forbidden",
                "message": f"角色「{user.get('name')}」无权浏览数据库。",
                "suggestion": "请切换为研发 DBA 或部门经理后再打开 Database 页。",
                "user_id": uid,
                "role": user.get("role"),
            },
        )
    return user

@router.get("/database")
async def database_overview(
    store: str = Query("demo"),
    user_id: str | None = Query(None, description="RBAC 用户，与侧栏 identity 一致"),
    x_agent_user: str | None = Header(None, alias="X-Agent-User"),
):
    _require_db_browser(user_id, x_agent_user)
    stores = []
    for sid, meta in STORES.items():
        exists = _store_exists(sid)
        # uploads 未创建时不出现在列表（避免 missing 干扰）
        if sid == "uploads" and not exists:
            continue
        stores.append({
            "id": sid,
            "label": meta["label"],
            "kind": meta["kind"],
            "primary": meta["primary"],
            "summary": meta["summary"],
            "exists": exists,
        })

    if store == "uploads" and not _store_exists("uploads"):
        raise HTTPException(404, "uploads.db 尚未创建（上传 CSV 后会出现）")

    if store == "traces":
        detail = _traces_overview()
    elif store == "hbase":
        detail = _hbase_overview()
    else:
        detail = _sqlite_overview(store)

    # demo 概览附带 HBase 分组摘要，便于 UI 三栏展示
    if store == "demo":
        hb = _hbase_overview()
        detail = {**detail, "hbase_tables": hb["tables"]}

    return {"stores": stores, "active": detail}


@router.get("/database/table/{table_name}")
async def database_table(
    table_name: str,
    store: str = Query("demo"),
    limit: int = Query(SAMPLE_LIMIT, ge=1, le=500),
    user_id: str | None = Query(None),
    x_agent_user: str | None = Header(None, alias="X-Agent-User"),
):
    _require_db_browser(user_id, x_agent_user)
    if store == "traces":
        if table_name != "traces":
            raise HTTPException(404, "traces store only has table 'traces'")
        return _traces_table(limit)
    if store == "hbase":
        return _hbase_table(table_name, limit)

    meta = _store_meta(store)
    if meta["kind"] != "sqlite":
        raise HTTPException(400, "not a sqlite store")
    conn = _connect_ro(meta["path"])
    try:
        names = _list_sqlite_tables(conn)
        if table_name not in names:
            raise HTTPException(404, f"table not found: {table_name}")
        types = _column_types(conn, table_name)
        count = _table_count(conn, table_name)
        columns, sample = _sample_rows(conn, table_name, limit)
        return {
            "store": store,
            "name": table_name,
            "description": TABLE_DESC.get(store, {}).get(table_name, ""),
            "count": count,
            "columns": columns,
            "types": types,
            "sample": sample,
            "limit": limit,
            "group": _demo_group(table_name) if store == "demo" else "other",
        }
    finally:
        conn.close()


class SqlQueryRequest(BaseModel):
    sql: str = Field(..., min_length=1, max_length=4000)
    store: str = "demo"
    user_id: str | None = None


@router.post("/database/query")
async def database_query(
    req: SqlQueryRequest,
    x_agent_user: str | None = Header(None, alias="X-Agent-User"),
):
    _require_db_browser(req.user_id, x_agent_user)
    if req.store in {"traces", "hbase"}:
        return {
            "ok": False,
            "error": f"{req.store} 不支持 SQL console（请用表浏览）",
            "columns": [],
            "rows": [],
        }
    meta = _store_meta(req.store)
    if meta["kind"] != "sqlite":
        return {"ok": False, "error": "not a sqlite store", "columns": [], "rows": []}

    sql = req.sql.strip().rstrip(";")
    upper = sql.upper()
    if upper.startswith("WITH"):
        for keyword in DANGEROUS_SQL_KEYWORDS:
            if re.search(rf"\b{keyword}\b", upper):
                return {"ok": False, "error": f"SQL 包含危险操作 {keyword}", "columns": [], "rows": []}
    else:
        ok, reason = guard_sql(req.sql)
        if not ok:
            return {"ok": False, "error": reason, "columns": [], "rows": []}

    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        return {"ok": False, "error": "只允许 SELECT / WITH … SELECT。", "columns": [], "rows": []}

    if "LIMIT" not in upper:
        sql = f"SELECT * FROM ({sql}) AS _q LIMIT {QUERY_LIMIT}"

    try:
        conn = _connect_ro(meta["path"])
    except HTTPException as e:
        return {"ok": False, "error": str(e.detail), "columns": [], "rows": []}

    try:
        cur = conn.execute(sql)
        columns = [d[0] for d in (cur.description or [])]
        raw = cur.fetchmany(QUERY_LIMIT)
        rows = [[_cell(v) for v in row] for row in raw]
        return {"ok": True, "columns": columns, "rows": rows, "truncated": len(rows) >= QUERY_LIMIT}
    except sqlite3.Error as exc:
        return {"ok": False, "error": str(exc), "columns": [], "rows": []}
    finally:
        conn.close()
