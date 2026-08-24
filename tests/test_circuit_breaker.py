"""熔断器（第三层自愈）测试。

覆盖：
- 状态机：closed → open → half_open → closed
- retry.py 集成：连续失败打开后快速失败（不发起调用）
- 降级路径：agent_loop / streaming_agent 在熔断时返回可读文案
"""

from types import SimpleNamespace

import pytest

from harness.constraints.circuit_breaker import (
    CircuitBreaker, CircuitOpenError, DEGRADED_MESSAGE,
)
from harness.constraints.retry import (
    acall_with_retry, get_circuit_breaker, reset_circuit_breaker,
)


# ═══ 1. 状态机 ═══════════════════════════════════════════════════════


def test_opens_after_threshold_failures():
    cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=60)
    for _ in range(2):
        cb.record_failure()
    assert cb.state == "closed"
    cb.record_failure()
    assert cb.state == "open"
    assert cb.can_proceed() is False


def test_half_open_after_cooldown_and_success_closes(monkeypatch):
    cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=0.05)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "open"
    assert cb.can_proceed() is False

    import time
    time.sleep(0.08)  # 冷却期结束
    assert cb.state == "half_open"
    assert cb.can_proceed() is True   # 半开放行一次试探
    assert cb.can_proceed() is False  # 并发试探名额已占满

    cb.record_success()
    assert cb.state == "closed"
    assert cb.consecutive_failures == 0


def test_half_open_failure_reopens():
    cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=0.01)
    cb.record_failure()
    cb.record_failure()
    import time
    time.sleep(0.03)
    assert cb.state == "half_open"
    assert cb.can_proceed() is True
    cb.record_failure()  # 试探失败 → 重新打开
    assert cb.state == "open"


# ═══ 2. retry.py 集成 ═════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_acall_with_retry_trips_and_blocks(monkeypatch):
    """连续失败达阈值 → 打开 → 后续调用快速失败且不再执行 fn。"""
    reset_circuit_breaker()
    monkeypatch.setenv("CIRCUIT_BREAKER_THRESHOLD", "2")
    monkeypatch.setenv("CIRCUIT_BREAKER_COOLDOWN", "60")

    calls = {"n": 0}

    def failing():
        calls["n"] += 1
        raise RuntimeError("boom")

    cb = get_circuit_breaker()
    with pytest.raises(RuntimeError):
        await acall_with_retry(failing, max_retries=0)
    assert cb.consecutive_failures == 1
    with pytest.raises(RuntimeError):
        await acall_with_retry(failing, max_retries=0)
    assert cb.state == "open"

    # 打开后：不再执行 fn，直接 CircuitOpenError
    with pytest.raises(CircuitOpenError):
        await acall_with_retry(failing, max_retries=0)
    assert calls["n"] == 2  # fn 只被执行了前两次


@pytest.mark.asyncio
async def test_success_resets_failures():
    reset_circuit_breaker()
    cb = get_circuit_breaker()
    cb.record_failure()
    cb.record_failure()

    result = await acall_with_retry(lambda: 42, max_retries=0)
    assert result == 42
    assert cb.state == "closed"
    assert cb.consecutive_failures == 0


# ═══ 3. 降级路径 ══════════════════════════════════════════════════════


class _ExplodingClient:
    """若被调用则失败——验证熔断时根本不发请求。"""

    class messages:
        @staticmethod
        def create(**kwargs):
            raise AssertionError("熔断时不应发起 LLM 调用")


@pytest.mark.asyncio
async def test_agent_loop_degrades_when_circuit_open():
    from harness.orchestration.single.agent import agent_loop

    reset_circuit_breaker()
    get_circuit_breaker().record_failure()
    get_circuit_breaker().record_failure()  # threshold=5 不够，用 5 次
    get_circuit_breaker().record_failure()
    get_circuit_breaker().record_failure()
    get_circuit_breaker().record_failure()
    assert get_circuit_breaker().state == "open"

    result = await agent_loop(
        _ExplodingClient(), "查一下销售额", "prompt", tools=[], handlers={},
    )
    assert result == DEGRADED_MESSAGE


@pytest.mark.asyncio
async def test_streaming_agent_degrades_when_circuit_open():
    from harness.orchestration.single.agent import streaming_agent

    reset_circuit_breaker()
    cb = get_circuit_breaker()
    for _ in range(5):
        cb.record_failure()
    assert cb.state == "open"

    result = await streaming_agent(
        _ExplodingClient(), "查一下销售额", "prompt", tools=[], handlers={},
    )
    assert result == DEGRADED_MESSAGE
