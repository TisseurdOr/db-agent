"""CLI 入口端到端测试（子进程，不调 LLM）。

覆盖：
- `python -m main --help` 正常退出（0）
- 缺 ANTHROPIC_API_KEY 时友好报错 + 非 0 退出码（不崩 traceback）
- 安装后 `db-agent` 命令存在
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_cli(args, extra_env=None):
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "main", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_cli_help_exits_zero():
    """--help 应打印用法并以 0 退出（不需要任何 API key）。"""
    proc = _run_cli(["--help"])
    assert proc.returncode == 0
    assert "usage" in (proc.stdout + proc.stderr).lower()
    assert "--mode" in proc.stdout


def test_cli_missing_key_friendly_error():
    """缺 ANTHROPIC_API_KEY 时应友好提示并以非 0 退出，不吐 traceback。"""
    proc = _run_cli(["--mode", "single"], extra_env={"ANTHROPIC_API_KEY": ""})
    output = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "ANTHROPIC_API_KEY" in output
    assert "Traceback" not in output


def test_cli_entry_point_installed():
    """安装后应存在 `db-agent` 命令（console_scripts）。"""
    entry = Path(sys.executable).parent / "db-agent"
    if not entry.exists():
        pytest.skip("db-agent 命令未安装（先运行 uv sync）")
    proc = subprocess.run(
        [str(entry), "--help"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0
    assert "--mode" in proc.stdout
