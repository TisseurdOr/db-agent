"""共享测试夹具：让测试不依赖 .env / 本机环境，离线可重复。

背景：此前部分测试靠 load_dotenv() 读 .env 里的 AGENT_USER=analyst 才通过；
CI 没有 .env 时这些用例会退回 viewer（run_query 被拒、employees 不可见）而失败。
这里统一把测试会话钉为 analyst 角色（权限测试自己会显式 delenv/setenv 覆盖），
保证任何环境行为一致。
"""

import os

import pytest


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    """固定测试会话角色为 analyst，消除对 .env 的隐式依赖。"""
    monkeypatch.setenv("AGENT_USER", "analyst")
