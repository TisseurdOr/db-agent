"""共享测试夹具：让测试不依赖 .env / 本机环境，离线可重复。

背景：查库类测试需要有 run_query 权限。权限收紧后仅 dba/manager 可访问数据库，
这里统一把测试会话钉为 dba（权限测试自己会显式 delenv/setenv 覆盖），
保证任何环境行为一致。
"""


import pytest


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    """固定测试会话角色为 dba，消除对 .env 的隐式依赖。"""
    monkeypatch.setenv("AGENT_USER", "dba")
    # 测试默认不走 Redis（保持离线可跑）；Redis 专项测试自己 setenv
    monkeypatch.delenv("REDIS_URL", raising=False)


@pytest.fixture(autouse=True)
def _reset_global_guards():
    """每个用例前重置全局状态（熔断器 / 幂等守卫 / 告警器 / 会话存储），避免污染。"""
    from harness.constraints.idempotency import reset_idempotency_guard
    from harness.constraints.retry import reset_circuit_breaker
    from harness.observation.alerts import reset_alert_notifier
    reset_circuit_breaker()
    reset_idempotency_guard()
    reset_alert_notifier()
    import server.endpoints.sessions as sessions_mod
    sessions_mod._redis_client = None
    sessions_mod.clear_sessions()
    from harness.observation.ops_metrics import reset_metrics
    reset_metrics()
