"""把已生成的 SQL 打包成可执行脚本（.sql / .py）。

两条链路：
- 只读：SELECT → ``.sql`` / ``.py``（生成前过 ``guard_sql``）。
- 写入：INSERT → ``.py``，默认 **dry-run**（事务里试插后回滚），加 ``--commit`` 才落库。

本模块只**生成**脚本文件，**不执行**：由人 review 后再跑，可控、可入版本库。
需要 Agent 直接写库时走 ``harness.tools.write.run_insert``，那里有 RBAC + HITL。
产物落在 ``output/scripts/``，方便查看 / 下载。
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from harness.constraints.guardrails import guard_sql
from harness.tools import tool

# 生成的脚本落在这里（gitignore）
SCRIPT_DIR = Path(__file__).resolve().parents[2] / "output" / "scripts"


def _slug(name: str) -> str:
    """文件名安全化：去掉路径分隔符等危险字符，**保留中文**（macOS/Linux 支持 UTF-8 文件名）。"""
    s = re.sub(r"[^\w-]+", "_", (name or "").strip()).strip("_")
    return s[:60] or "script"


def _check_sql(sql: str) -> str | None:
    """校验 SQL；返回错误信息或 None。写操作会被 guard_sql 拦下。"""
    if not sql or not sql.strip():
        return "SQL 为空"
    passed, reason = guard_sql(sql)
    if not passed:
        return reason
    return None


def _write(ext: str, name: str, content: str) -> Path:
    SCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    path = SCRIPT_DIR / f"{_slug(name)}.{ext}"
    path.write_text(content, encoding="utf-8")
    return path


def _doc_safe(text: str) -> str:
    """把用户说明安全地放进生成脚本的 docstring，避免 ``\"\"\"`` 提前结束字符串。"""
    return (text or "").replace("\\", "\\\\").replace('"""', "'''")


@tool(description=(
    "把一条已经写好的 SELECT 语句保存成 .sql 脚本文件（不执行）。"
    "当用户要求「给我 SQL 脚本 / 导出这个查询 / 以后要重复跑」时调用。"
    "当前只接受只读 SELECT；写操作需要走人工审批，本工具会拒绝。"
    "返回 {ok, path, sql, note}。"
))
def generate_sql_script(name: str, sql: str, description: str = "") -> dict:
    """name: 脚本名（做文件名）
    sql: 要保存的 SELECT 语句
    description: 用途说明（可选）"""
    err = _check_sql(sql)
    if err:
        return {"ok": False, "error": err, "sql": sql}
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    header = f"-- {description}\n" if description else ""
    content = f"{header}-- 由 DataForge 生成于 {ts}\n\n{sql.strip()}\n"
    path = _write("sql", name, content)
    return {"ok": True, "path": str(path), "sql": sql.strip(), "note": "只读脚本，未执行"}


_PY_TMPL = '''#!/usr/bin/env python3
"""{description}

由 DataForge 生成于 {ts}
用法: python {filename} [数据库路径]
"""
import sqlite3
import sys

SQL = {sql!r}


def main() -> None:
    db = sys.argv[1] if len(sys.argv) > 1 else {default_db!r}
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(SQL).fetchall()
    finally:
        conn.close()

    if not rows:
        print("(无结果)")
        return
    cols = list(rows[0].keys())
    print(" | ".join(cols))
    print("-" * 60)
    for r in rows:
        print(" | ".join(str(r[c]) for c in cols))
    print(f"\\n共 {{len(rows)}} 行")


if __name__ == "__main__":
    main()
'''


@tool(description=(
    "把一条已经写好的 SELECT 语句保存成可独立运行的 .py 脚本（连接 SQLite 执行并打印结果）。"
    "当用户要求「给我一个 Python 脚本 / 脚本化这个查询 / 放到定时任务」时调用。"
    "当前只接受只读 SELECT；写操作会被拒绝。"
    "返回 {ok, path, sql, note}。"
))
def generate_python_script(name: str, sql: str, description: str = "", db_path: str = "") -> dict:
    """name: 脚本名
    sql: SELECT 语句
    description: 用途说明（可选）
    db_path: 脚本默认连接的库（可选）"""
    err = _check_sql(sql)
    if err:
        return {"ok": False, "error": err, "sql": sql}
    from db.seed import DB_PATH

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    filename = f"{_slug(name)}.py"
    content = _PY_TMPL.format(
        description=_doc_safe(description or "由自然语言需求生成的查询脚本"),
        ts=ts,
        filename=filename,
        sql=sql.strip(),
        default_db=db_path or DB_PATH,
    )
    path = _write("py", name, content)
    return {"ok": True, "path": str(path), "sql": sql.strip(), "note": "只读脚本，未执行"}


_INSERT_PY_TMPL = '''#!/usr/bin/env python3
"""{description}

由 DataForge 生成于 {ts}

安全设计（默认不落库）：
- DRY_RUN 默认 True：在事务里试插后**回滚**，真实验证约束但不改数据
- 幂等：按 {keys} 判重，已存在就跳过（脚本可重复跑）
- 参数化写入，不拼字符串
确认无误后：python {filename} --commit
"""
import sqlite3
import sys

DB = {db_path!r}
TABLE = "{table}"
ROW = {row}
KEY_COLUMNS = {keys}


def main() -> None:
    dry_run = "--commit" not in sys.argv
    conn = sqlite3.connect(DB)
    try:
        conn.execute("BEGIN")
        if KEY_COLUMNS:
            where = " AND ".join(f'{{c}} = ?' for c in KEY_COLUMNS)
            dup = conn.execute(
                f'SELECT 1 FROM "{{TABLE}}" WHERE {{where}}',
                tuple(ROW[c] for c in KEY_COLUMNS),
            ).fetchone()
            if dup:
                print("已存在，跳过（幂等）")
                conn.rollback()
                return
        cols = ", ".join(f'{{c}}' for c in ROW)
        placeholders = ", ".join("?" for _ in ROW)
        conn.execute(
            f'INSERT INTO "{{TABLE}}" ({{cols}}) VALUES ({{placeholders}})',
            tuple(ROW.values()),
        )
        print(f"将插入 1 行 -> {{TABLE}}")
        for k, v in ROW.items():
            print(f"   {{k}} = {{v}}")
        if dry_run:
            conn.rollback()
            print("（DRY-RUN：约束已验证，已回滚，未落库）")
        else:
            conn.commit()
            print("✅ 已提交")
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        print(f"❌ 失败，已回滚：{{type(e).__name__}}: {{e}}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
'''


@tool(description=(
    "为「插入一行数据」生成一个**默认不落库**的 Python 脚本（dry-run）。"
    "脚本会在事务里试插后回滚（真实验证 NOT NULL/唯一/外键等约束），并按 key_columns 幂等判重。"
    "本工具只**生成脚本**，不执行。当用户要求「生成插入数据的脚本 / 配置开发」时调用。"
    "返回 {ok, path, table, columns, note}。"
))
def generate_insert_script(
    table: str,
    values: dict,
    key_columns: list | None = None,
    description: str = "",
    db_path: str = "",
) -> dict:
    """table: 目标表名
    values: {列名: 值}
    key_columns: 幂等判重列（可选）
    description: 说明（可选）
    db_path: 脚本默认连接的库（可选；不填用当前 demo 库）"""
    if not table or not str(table).strip():
        return {"ok": False, "error": "缺少表名"}
    if not isinstance(values, dict) or not values:
        return {"ok": False, "error": "values 必须是非空的 {列名: 值}"}

    norm_values = {str(k): v for k, v in values.items()}
    # 表名/列名只允许标识符，防注入（脚本里会被拼进 SQL）
    ident = re.compile(r"^\w+$")
    if not ident.match(str(table)):
        return {"ok": False, "error": f"表名不合法: {table!r}"}
    for c in norm_values:
        if not ident.match(c):
            return {"ok": False, "error": f"列名不合法: {c!r}"}

    if key_columns is None:
        raw_keys: list = []
    elif isinstance(key_columns, (list, tuple)):
        raw_keys = list(key_columns)
    else:
        return {"ok": False, "error": "key_columns 必须是列名数组"}

    keys = [str(k) for k in raw_keys]
    for k in keys:
        if not ident.match(k):
            return {"ok": False, "error": f"幂等列名不合法: {k!r}"}
        if k not in norm_values:
            return {"ok": False, "error": f"幂等列必须出现在 values 中: {k!r}"}

    from db.seed import DB_PATH

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    filename = f"{_slug(description or table)}_insert.py"
    content = _INSERT_PY_TMPL.format(
        description=_doc_safe(description or f"向 {table} 插入一行"),
        ts=ts,
        filename=filename,
        db_path=db_path or DB_PATH,
        table=str(table),
        row=repr(norm_values),
        keys=repr(keys),
    )
    path = _write("py", f"{description or table}_insert", content)
    return {
        "ok": True,
        "path": str(path),
        "table": str(table),
        "columns": list(norm_values),
        "key_columns": keys,
        "note": "默认 dry-run，不落库；确认后加 --commit 才真插",
    }
