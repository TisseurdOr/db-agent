"""脚本生成工具：产出可执行的 .sql / .py，且只接受只读 SELECT。

第一阶段（只读优先）：写操作（INSERT/UPDATE/…）必须被拒，等接入 HITL 审批后再放开。
"""

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
