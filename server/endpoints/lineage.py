"""GET /api/lineage — 表级血缘（真实外键）+ 元数据分组。

数据源全部来自 db-agent 已有的本地演示数据，零新依赖：
  - demo.db 的 SQL 表外键（PRAGMA foreign_key_list）→ 真实血缘边（child → parent）
  - Hive 分层表（ods_/dwd_/dim_ 前缀）→ 按层分组，demo 无真实 ETL 边，不造假
  - HBase 内存模拟表 → 无血缘边

节点按 engine 分组（sql/hive/hbase）。前端 LineagePanel 据此画 echarts 关系图。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter

from db.olist_warehouse import (
    OLIST_LINEAGE_DESCRIPTIONS,
    OLIST_LINEAGE_EDGES,
    OLIST_WAREHOUSE_TABLES,
    WAREHOUSE_DB_PATH,
)

router = APIRouter()

ROOT = Path(__file__).resolve().parents[2]
DEMO_DB = ROOT / "db" / "demo.db"

# 系统/内部表：不出现在血缘图里
_SYSTEM_TABLES = {
    "agent_roles",
    "agent_users",
    "user_memory",
    "user_feedback",
    "sqlite_sequence",
}

# HBase 内存模拟表（不在 demo.db，启动时 seed）
HBASE_TABLES = ["orders", "user_profile", "product_catalog"]


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    except sqlite3.Error:
        return 0


def _warehouse_layer(table: str) -> str:
    for layer in ("ods", "dim", "dwd", "dws", "ads"):
        if table.startswith(f"{layer}_"):
            return layer
    return "warehouse"


def _add_warehouse_lineage(nodes: list[dict], edges: list[dict]) -> None:
    if not WAREHOUSE_DB_PATH.exists():
        return
    conn = sqlite3.connect(f"file:{WAREHOUSE_DB_PATH}?mode=ro", uri=True)
    try:
        existing = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for table in OLIST_WAREHOUSE_TABLES:
            if table not in existing:
                continue
            layer = _warehouse_layer(table)
            nodes.append({
                "id": table,
                "label": table,
                "group": layer,
                "layer": layer,
                "source": "warehouse",
                "count": _table_count(conn, table),
            })
        for edge in OLIST_LINEAGE_EDGES:
            if edge["source"] in existing and edge["target"] in existing:
                # 模型定义使用 child → dependency；输出统一为 upstream → downstream
                edge_type = edge.get("type", "etl")
                edges.append({
                    "source": edge["target"],
                    "target": edge["source"],
                    "type": edge_type,
                    "kind": edge_type,
                    "description": OLIST_LINEAGE_DESCRIPTIONS.get(
                        (edge["source"], edge["target"]), ""
                    ),
                })
    finally:
        conn.close()


def build_lineage() -> dict:
    nodes: list[dict] = []
    edges: list[dict] = []

    if DEMO_DB.exists():
        conn = sqlite3.connect(f"file:{DEMO_DB}?mode=ro", uri=True)
        try:
            tables = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            ]
            for t in tables:
                if t in _SYSTEM_TABLES:
                    continue
                if t.startswith(("ods_", "dwd_", "dim_")):
                    group, layer = "hive", t.split("_", 1)[0]
                else:
                    group, layer = "sql", "rel"
                nodes.append({
                    "id": t,
                    "label": t,
                    "group": group,
                    "layer": layer,
                    "source": "demo",
                    "count": _table_count(conn, t),
                })
                for fk in conn.execute(f'PRAGMA foreign_key_list("{t}")'):
                    parent = fk[2]
                    if parent and parent != t:
                        edges.append({
                            "source": parent,
                            "target": t,
                            "type": "fk",
                            "kind": "fk",
                            "from": fk[4],
                            "to": fk[3],
                        })
        finally:
            conn.close()

    _add_warehouse_lineage(nodes, edges)

    for t in HBASE_TABLES:
        nodes.append({
            "id": f"hbase:{t}",
            "label": t,
            "group": "hbase",
            "layer": "kv",
            "source": "demo",
            "count": 0,
        })

    return {
        "nodes": nodes,
        "edges": edges,
        "summary": {
            "nodes": len(nodes),
            "edges": len(edges),
            "sources": sorted({n["source"] for n in nodes}),
            "layers": sorted({n["group"] for n in nodes}),
        },
    }


@router.get("/lineage")
async def lineage():
    return build_lineage()
