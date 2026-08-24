"""熔断器（Circuit Breaker）——三层自愈的第三层。

链路：API 重试(1) → SQL 自愈(2) → 熔断降级(3)。
当 LLM 服务持续不可用（连续失败达到阈值）时，继续重试只是在烧时间烧钱。
熔断器打开后立即"快速失败"（不再调 API），让上层返回可读的降级文案；
冷却期结束后进入半开状态放行一次试探请求，成功则关闭、失败则重新打开。

状态机：
  closed ──(连续失败 ≥ threshold)──▶ open
  open ──(冷却期结束)──▶ half_open
  half_open ──(试探成功)──▶ closed
  half_open ──(试探失败)──▶ open

线程安全：方法都很短且只做状态读写，用 threading.Lock 保护；
从 asyncio 里同步调用不会阻塞事件循环（无 IO、无 sleep）。
"""

import os
import threading
import time


class CircuitOpenError(RuntimeError):
    """熔断器打开：调用被快速失败拦截，未真正执行。"""


# 统一降级文案——熔断时上层返回给用户/Agent 的可读消息
DEGRADED_MESSAGE = "⚠️ 模型服务暂时不可用（已触发熔断降级），请稍后重试。"


class CircuitBreaker:
    """连续失败达到阈值后打开，冷却期后半开试探。

    Args:
        failure_threshold: 连续失败多少次打开（默认读 CIRCUIT_BREAKER_THRESHOLD，默认 5）
        cooldown_seconds: 打开后冷却多久进入半开（默认读 CIRCUIT_BREAKER_COOLDOWN，默认 30s）
        half_open_max: 半开状态最多同时放行几个试探请求（默认 1）
    """

    def __init__(self, failure_threshold: int | None = None,
                 cooldown_seconds: float | None = None,
                 half_open_max: int = 1):
        self.failure_threshold = (
            failure_threshold
            if failure_threshold is not None
            else int(os.getenv("CIRCUIT_BREAKER_THRESHOLD", "5"))
        )
        self.cooldown_seconds = (
            cooldown_seconds
            if cooldown_seconds is not None
            else float(os.getenv("CIRCUIT_BREAKER_COOLDOWN", "30"))
        )
        self.half_open_max = max(1, half_open_max)

        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._state = "closed"          # closed / open / half_open
        self._half_open_inflight = 0
        # RLock：can_proceed() 持锁后还会读 self.state（内部也加锁），必须可重入
        self._lock = threading.RLock()

    # ── 状态 ─────────────────────────────────────────────────────────

    @property
    def state(self) -> str:
        """当前对外状态：open 且冷却期已过 → 视为 half_open。"""
        with self._lock:
            if self._state == "open" and self._opened_at is not None:
                if time.monotonic() - self._opened_at >= self.cooldown_seconds:
                    return "half_open"
            return self._state

    @property
    def consecutive_failures(self) -> int:
        with self._lock:
            return self._consecutive_failures

    # ── 决策 ─────────────────────────────────────────────────────────

    def can_proceed(self) -> bool:
        """是否允许发起一次真实调用。"""
        with self._lock:
            s = self.state
            if s == "closed":
                return True
            if s == "open":
                return False
            # half_open：只放行少量试探请求
            if self._half_open_inflight < self.half_open_max:
                self._half_open_inflight += 1
                return True
            return False

    # ── 记录结果 ─────────────────────────────────────────────────────

    def record_success(self) -> None:
        """调用成功：连续失败清零，回到 closed。"""
        with self._lock:
            self._consecutive_failures = 0
            self._opened_at = None
            self._state = "closed"
            self._half_open_inflight = 0

    def record_failure(self) -> None:
        """调用失败：累计连续失败，达到阈值则打开。"""
        with self._lock:
            self._half_open_inflight = 0
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.failure_threshold:
                self._state = "open"
                self._opened_at = time.monotonic()

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (
            f"CircuitBreaker(state={self.state}, "
            f"failures={self._consecutive_failures}/{self.failure_threshold}, "
            f"cooldown={self.cooldown_seconds}s)"
        )
