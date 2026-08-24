# utils/retry.py — LLM API 调用重试（指数退避 + 抖动）
#
# 为什么需要：DeepSeek/Kimi 返回 429（限流）、5xx（服务端错误）或网络超时
# 是运行时常态，不重试就意味着整轮对话直接失败。
#
# 设计决策：
#   只重试"可恢复"错误——429/500/502/503/529 和连接类错误。
#   400（参数错）、401（key 错）、403（权限）重试没有意义，立即抛出。
#   指数退避 + 随机抖动：避免多个并发 Agent 同时限流后同时重试（惊群）。
#   重试预算走环境变量，生产可调，测试可关（LLM_MAX_RETRIES=0）。

import asyncio
import inspect
import os
import random
import time

import anthropic
from harness.constraints.circuit_breaker import (
    CircuitBreaker, CircuitOpenError, DEGRADED_MESSAGE,
)

# 可重试的 HTTP 状态码：
#   429 限流 / 500 服务端错误 / 502,503 网关、过载 / 529 Anthropic overloaded
RETRIABLE_STATUS = {429, 500, 502, 503, 529}

# ── 熔断器（第三层自愈）───────────────────────────────────────────
# 全局默认熔断器：进程内所有 LLM 调用共享。连续失败达到阈值 → 打开 →
# 快速失败（抛 CircuitOpenError）→ 上层返回降级文案。
# 测试通过 reset_circuit_breaker() 在每个用例间重置，避免状态污染。

_default_circuit_breaker: CircuitBreaker | None = None


def get_circuit_breaker() -> CircuitBreaker:
    """获取（必要时创建）全局默认熔断器。"""
    global _default_circuit_breaker
    if _default_circuit_breaker is None:
        _default_circuit_breaker = CircuitBreaker()
    return _default_circuit_breaker


def _alert_circuit_open(cb: CircuitBreaker) -> None:
    """熔断器刚打开时发一条告警（只发一次，后续快速失败不再重复）。"""
    from harness.observation.alerts import send_alert
    send_alert(
        "LLM 服务熔断",
        f"连续失败 {cb.consecutive_failures} 次达到阈值，熔断器已打开，"
        f"后续调用将快速失败降级，冷却 {cb.cooldown_seconds:.0f}s 后半开试探。",
        level="critical",
        tags={"breaker": "llm", "threshold": cb.failure_threshold},
    )


def reset_circuit_breaker() -> None:
    """重置默认熔断器（测试隔离用）。"""
    global _default_circuit_breaker
    _default_circuit_breaker = None


async def circuit_can_proceed() -> bool:
    """熔断器是否放行本次调用；异步签名，方便上层 await。"""
    return get_circuit_breaker().can_proceed()


async def circuit_record_success() -> None:
    get_circuit_breaker().record_success()


async def circuit_record_failure() -> None:
    get_circuit_breaker().record_failure()




def is_retriable(exc: BaseException) -> bool:
    """判断异常是否值得重试。

    连接类错误（含超时）一律可重试；HTTP 错误看状态码。
    通过 getattr 取 status_code——同时兼容 anthropic SDK 异常
    和测试里带 status_code 属性的自定义异常。
    """
    if isinstance(exc, anthropic.APIConnectionError):  # 含 APITimeoutError 子类
        return True
    return getattr(exc, "status_code", None) in RETRIABLE_STATUS


def max_retries_from_env() -> int:
    return int(os.getenv("LLM_MAX_RETRIES", "3"))


def base_delay_from_env() -> float:
    return float(os.getenv("LLM_RETRY_BASE_DELAY", "1.0"))


def backoff_delay(attempt: int, base_delay: float) -> float:
    """第 attempt 次失败后的等待秒数：base * 2^attempt + 随机抖动。"""
    return base_delay * (2 ** attempt) + random.uniform(0, base_delay / 2)


def call_with_retry(fn, *args, max_retries: int | None = None,
                    base_delay: float | None = None, **kwargs):
    """同步调用 fn，可恢复错误时指数退避重试。

    用法: resp = call_with_retry(client.messages.create, model=..., messages=...)
    """
    max_retries = max_retries_from_env() if max_retries is None else max_retries
    base_delay = base_delay_from_env() if base_delay is None else base_delay

    cb = get_circuit_breaker()
    if not cb.can_proceed():
        raise CircuitOpenError("熔断器打开，快速失败（不发起调用）")

    for attempt in range(max_retries + 1):
        try:
            result = fn(*args, **kwargs)
            cb.record_success()
            return result
        except Exception as e:
            if not is_retriable(e) or attempt >= max_retries:
                cb.record_failure()
                if cb.state == "open":
                    _alert_circuit_open(cb)
                raise
            delay = backoff_delay(attempt, base_delay)
            print(f"⚠️ LLM API 错误 ({type(e).__name__})，{delay:.1f}s 后重试 "
                  f"({attempt + 1}/{max_retries})")
            time.sleep(delay)


async def acall_with_retry(fn, *args, max_retries: int | None = None,
                           base_delay: float | None = None, **kwargs):
    """异步版本：等待用 asyncio.sleep，不阻塞事件循环。

    fn 可以是同步函数（如 client.messages.create）或协程函数。
    """
    max_retries = max_retries_from_env() if max_retries is None else max_retries
    base_delay = base_delay_from_env() if base_delay is None else base_delay

    cb = get_circuit_breaker()
    if not cb.can_proceed():
        raise CircuitOpenError("熔断器打开，快速失败（不发起调用）")

    for attempt in range(max_retries + 1):
        try:
            result = fn(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            cb.record_success()
            return result
        except Exception as e:
            if not is_retriable(e) or attempt >= max_retries:
                cb.record_failure()
                if cb.state == "open":
                    _alert_circuit_open(cb)
                raise
            delay = backoff_delay(attempt, base_delay)
            print(f"⚠️ LLM API 错误 ({type(e).__name__})，{delay:.1f}s 后重试 "
                  f"({attempt + 1}/{max_retries})")
            await asyncio.sleep(delay)
