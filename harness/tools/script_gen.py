"""把已生成的 SQL 打包成可执行脚本（.sql / .py）。

设计：**只读优先**（第一阶段）
- 生成前用 ``guard_sql`` 校验，当前只接受 SELECT；写操作（INSERT/UPDATE/…）会被拦下。
- 本工具只**生成**脚本文件，**不执行**——由人自己跑，可控、可 review、可入版本库。
- 产物落在 ``output/scripts/``，方便查看 / 下载。

为什么先做 SELECT 版：先把「自然语言需求 → 可执行脚本」这条链路跑通，并在真实数据上
验证脚本确实能跑出预期结果；确认无误后再放开 INSERT（届时配 HITL 审批）。
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


@tool(description=(
    "把一条已经写好的 SELECT 语句保存成 .sql 脚本文件（不执行）。"
    "当用户要求「给我 SQL 脚本 / 导出这个查询 / 以后要重复跑」时调用。"
    "当前只接受只读 SELECT；写操作需要走人工审批，本工具会拒绝。"
    "返回 {ok, path, sql, note}。"
))
def generate_sql_script(name: str, sql: str, description: str = "") -> dict:
    """name: 脚本名（做文件名）；sql: 要保存的 SELECT 语句；description: 用途说明（可选）"""
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

SQL = """\\
{sql}
"""


def main() -> None:
    db = sys.argv[1] if len(sys.argv) > 1 else r"{default_db}"
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
    """name: 脚本名；sql: SELECT 语句；description: 用途说明（可选）；db_path: 脚本默认连接的库（可选）"""
    err = _check_sql(sql)
    if err:
        return {"ok": False, "error": err, "sql": sql}
    from db.seed import DB_PATH

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    filename = f"{_slug(name)}.py"
    content = _PY_TMPL.format(
        description=description or "由自然语言需求生成的查询脚本",
        ts=ts,
        filename=filename,
        sql=sql.strip(),
        default_db=db_path or DB_PATH,
    )
    path = _write("py", name, content)
    return {"ok": True, "path": str(path), "sql": sql.strip(), "note": "只读脚本，未执行"}
