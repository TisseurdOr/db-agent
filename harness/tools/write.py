"""写库工具：``run_insert`` —— 真正插入一行，**必须人工审批**。

与只读工具的关键区别：它**会改数据**，所以四道闸：

1. **RBAC**：只有被授予 ``run_insert`` 的角色（默认仅 dba）能用
2. **标识符校验**：表名/列名对着真实 schema 核对，且只允许标识符（防注入）
3. **HITL 中断**：把完整的 INSERT（表 / 列 / 值）交给人看，**等批准**
4. **事务 + 幂等**：批准后在事务里执行，按 key_columns 判重，失败自动回滚

不在 graph 上下文（单 Agent / 测试）时，interrupt 会抛异常 → 返回「需要审批」标记而
**不执行**。也就是说默认是安全的：没有人工批准，就不会写库。
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from db.seed import DB_PATH
from harness.constraints.entitlement import guard, resolve_user_id
from harness.tools import tool

_IDENT = re.compile(r"^\w+$")


def _is_ident(name: str) -> bool:
    return bool(_IDENT.match(name))


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]


@tool(description=(
    "向数据库**真正插入一行**。属于写操作——会先暂停请求人工审批（HITL），"
    "只有用户批准后才会落库；失败或未批准都不会改数据。"
    "仅被授权写库的角色（默认 dba）可用。"
    "参数：table 表名；values {列名: 值}；key_columns 幂等判重列（可选）。"
    "返回 {ok, inserted, table, row} 或拒绝/待审批信息。"
))
def run_insert(table: str, values: dict, key_columns: list | None = None) -> dict:
    """table: 目标表
    values: {列名: 值}
    key_columns: 幂等判重列（可选）"""
    # 1) 权限（工具授权 + 表级授权；未授权用户不应看到后续表结构信息）
    ent = guard(resolve_user_id(), "run_insert", table=str(table or ""))
    if isinstance(ent, dict):
        return ent

    if not table or not isinstance(values, dict) or not values:
        return {"error": "缺少 table 或 values"}

    table_name = str(table)
    if not _is_ident(table_name):
        return {"error": f"表名不合法: {table!r}"}

    norm_values: dict[str, Any] = {str(k): v for k, v in values.items()}
    bad_cols = [c for c in norm_values if not _is_ident(c)]
    if bad_cols:
        return {"error": f"列名不合法: {bad_cols}"}

    if key_columns is None:
        raw_keys: list = []
    elif isinstance(key_columns, (list, tuple)):
        raw_keys = list(key_columns)
    else:
        return {"error": "key_columns 必须是列名数组"}
    keys = [str(k) for k in raw_keys]
    bad_keys = [k for k in keys if not _is_ident(k) or k not in norm_values]
    if bad_keys:
        return {"error": f"幂等列必须是 values 中出现的合法列名: {bad_keys}"}

    # 2) 校验表 / 列（对着真实 schema）
    cols: list[str] = []
    conn = sqlite3.connect(DB_PATH)
    try:
        cols = _table_columns(conn, table_name)
    finally:
        conn.close()
    if not cols:
        return {"error": f"表不存在: {table_name}"}
    unknown = [c for c in norm_values if c not in cols]
    if unknown:
        return {"error": f"列不存在: {unknown}", "table": table_name, "table_columns": cols}

    placeholders = ", ".join("?" for _ in norm_values)
    col_list = ", ".join(f'"{c}"' for c in norm_values)
    preview = f'INSERT INTO "{table_name}" ({col_list}) VALUES ({placeholders})'

    # 3) HITL：把要执行的东西完整摆给人看
    try:
        from langgraph.types import interrupt

        decision = interrupt({
            "type": "hitl_write",
            "tool": "run_insert",
            "table": table_name,
            "sql": preview,
            "values": norm_values,
            "key_columns": keys,
            "message": f"即将向 {table_name} 插入 1 行，请确认。",
        })
        approved = decision.get("approved", False) if isinstance(decision, dict) else False
    except (RuntimeError, ImportError):
        # 不在 graph 上下文 → 不执行，返回待审批标记
        return {
            "error": "需要人工审批",
            "table": table_name,
            "pending_sql": preview,
            "values": norm_values,
            "message": "写操作需要审批；请在多 Agent 交互中确认后执行。",
        }

    if not approved:
        return {"ok": False, "error": "用户拒绝了该插入", "table": table_name}

    # 4) 执行：事务 + 幂等，失败回滚
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("BEGIN")
        if keys:
            where = " AND ".join(f'"{k}" = ?' for k in keys)
            dup = conn.execute(
                f'SELECT 1 FROM "{table_name}" WHERE {where}',
                tuple(norm_values[k] for k in keys),
            ).fetchone()
            if dup:
                conn.rollback()
                return {"ok": True, "inserted": False, "table": table_name,
                        "reason": "已存在相同记录，按幂等跳过"}
        row: dict[str, Any] = dict(norm_values)
        conn.execute(preview, tuple(norm_values.values()))
        conn.commit()
        return {"ok": True, "inserted": True, "table": table_name, "row": row}
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "table": table_name}
    finally:
        conn.close()
