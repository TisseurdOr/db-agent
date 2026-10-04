"""脚本生成工具：SELECT 产出 .sql / .py；INSERT 产出默认 dry-run 的 .py。"""

import sqlite3
import subprocess
import sys

import pytest

from harness.tools import script_gen


@pytest.fixture(autouse=True)
def _tmp_out(tmp_path, monkeypatch):
    """产物写到临时目录，别污染仓库 output/。"""
    monkeypatch.setattr(script_gen, "SCRIPT_DIR", tmp_path)


_SQL = (
    "SELECT d.name, SUM(o.total) AS sales FROM orders o "
    "JOIN departments d ON o.dept_id = d.id GROUP BY d.name"
)


def test_generate_sql_script_writes_file():
    r = script_gen.generate_sql_script("各部门销售额", _SQL, "部门汇总")
    assert r["ok"] is True
    content = open(r["path"], encoding="utf-8").read()
    assert "SELECT" in content and "部门汇总" in content


def test_generate_python_script_is_runnable_file():
    r = script_gen.generate_python_script("各部门销售额", _SQL, "部门汇总")
    assert r["ok"] is True
    content = open(r["path"], encoding="utf-8").read()
    assert content.startswith("#!/usr/bin/env python3")
    assert "sqlite3" in content and "SELECT" in content


@pytest.mark.parametrize("bad", [
    "INSERT INTO orders(id) VALUES (1)",
    "UPDATE orders SET total = 0",
    "DELETE FROM orders",
    "DROP TABLE orders",
])
def test_write_sql_rejected(bad):
    """写操作一律拒绝（第一阶段只读）。"""
    assert script_gen.generate_sql_script("x", bad)["ok"] is False
    assert script_gen.generate_python_script("x", bad)["ok"] is False


def test_chinese_name_kept_in_filename():
    r = script_gen.generate_sql_script("各部门销售额", _SQL)
    assert "各部门销售额" in r["path"]


def test_insert_tool_schema_key_columns_is_array():
    """Optional[list] 必须映射成 JSON Schema array，否则模型会把 key_columns 当字符串传。"""
    prop = script_gen.generate_insert_script.tool_schema["input_schema"]["properties"]["key_columns"]
    assert prop["type"] == "array"


def test_generate_insert_script_dry_run_then_commit(tmp_path, monkeypatch):
    db = tmp_path / "insert.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT UNIQUE)")
    conn.commit()
    conn.close()

    # 脚本生成时把默认库指到临时库；产出文件也放临时目录
    import db.seed as seed
    monkeypatch.setattr(seed, "DB_PATH", str(db))
    monkeypatch.setattr(script_gen, "SCRIPT_DIR", tmp_path / "out")

    r = script_gen.generate_insert_script(
        "items",
        {"id": 1, "name": "alice"},
        key_columns=["id"],
        description="新增一条配置",
    )
    assert r["ok"] is True, r
    assert r["columns"] == ["id", "name"]
    content = open(r["path"], encoding="utf-8").read()
    assert r["path"].rsplit("/", 1)[-1] in content, "脚本里的 --commit 用法文件名必须和真实文件名一致"

    dry = subprocess.run(
        [sys.executable, r["path"]], capture_output=True, text=True, check=False,
    )
    assert dry.returncode == 0, dry.stderr
    assert "DRY-RUN" in dry.stdout
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0

    commit = subprocess.run(
        [sys.executable, r["path"], "--commit"], capture_output=True, text=True, check=False,
    )
    assert commit.returncode == 0, commit.stderr
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1

    again = subprocess.run(
        [sys.executable, r["path"], "--commit"], capture_output=True, text=True, check=False,
    )
    assert again.returncode == 0, again.stderr
    assert "幂等" in again.stdout
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1


@pytest.mark.parametrize("table,key_columns", [
    ('items"; DROP TABLE items; --', None),
    ("items", ["missing_key"]),
])
def test_generate_insert_script_rejects_bad_identifiers(table, key_columns):
    r = script_gen.generate_insert_script(table, {"id": 1, "name": "x"}, key_columns=key_columns)
    assert r["ok"] is False
    assert "不合法" in r["error"] or "必须出现在" in r["error"]


def test_generate_insert_script_docstring_cannot_escape(tmp_path, monkeypatch):
    monkeypatch.setattr(script_gen, "SCRIPT_DIR", tmp_path)
    r = script_gen.generate_insert_script(
        "items", {"id": 1}, description='bad """ break',
    )
    assert r["ok"] is True
    compile(open(r["path"], encoding="utf-8").read(), r["path"], "exec")


def test_insert_long_description_filename_is_stable(tmp_path, monkeypatch):
    """长说明会触发 60 字截断；截断后文件名必须稳定，usage 里的文件名要和真实路径一致。"""
    monkeypatch.setattr(script_gen, "SCRIPT_DIR", tmp_path)
    r = script_gen.generate_insert_script(
        "items", {"id": 1}, description="很长的配置说明" * 20,
    )
    assert r["ok"] is True, r
    content = open(r["path"], encoding="utf-8").read()
    assert r["path"].rsplit("/", 1)[-1] in content
