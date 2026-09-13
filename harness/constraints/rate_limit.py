"""LLM 调用前的主动限流（pre-call throttling）。

三层自愈里，重试(1)和熔断(3)都是「失败之后」的反应式兜底，
这一层是「调用之前」就主动压住并发和请求速率，避免一开始就打爆供应商配额。

两层：
  1. 并发上限 —— asyncio.Semaphore，最多同时 in-flight N 个 LLM 调用。
  2. RPM 令牌桶 —— 每分钟最多 R 个请求，超了就排队等（不报错、不重试）。

默认 LLM_MAX_CONCURRENCY=8（并发上限），LLM_RPM=0（不限 RPM，按需开）。
"""

import asyncio
import os
import threading
import time


class TokenBucket:
    """线程安全令牌桶：容量 = 每分钟请求数，按秒匀速补充。"""

    def __init__(self, rpm: int):
        self.capacity = float(rpm)
        self.tokens = float(rpm)
        self.refill_per_sec = rpm / 60.0
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def wait_time(self) -> float:
        """取一个 token，返回需要等待的秒数（0 = 立即可用）。"""
        with self._lock:
            now = time.monotonic()
            self.tokens = min(
                self.capacity, self.tokens + (now - self._last) * self.refill_per_sec
            )
            self._last = now
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return 0.0
            wait = (1.0 - self.tokens) / self.refill_per_sec
            self.tokens = 0.0
            return wait


class RateLimiter:
    """组合并发上限 + RPM 限制。全局单例，所有 LLM 调用共享。"""

    def __init__(self):
        self.max_concurrency = int(os.getenv("LLM_MAX_CONCURRENCY", "8"))
        rpm = int(os.getenv("LLM_RPM", "0"))
        self._sem = asyncio.Semaphore(self.max_concurrency)
        self._rpm = TokenBucket(rpm) if rpm > 0 else None

    async def acquire(self) -> None:
        """进入调用前：先占并发名额，再等 RPM token。"""
        await self._sem.acquire()
        try:
            if self._rpm is not None:
                wait = self._rpm.wait_time()
                if wait > 0:
                    await asyncio.sleep(wait)
        except BaseException:
            self._sem.release()
            raise

    def release(self) -> None:
        self._sem.release()


_default: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _default
    if _default is None:
        _default = RateLimiter()
    return _default


def reset_rate_limiter() -> None:
    """重置全局限流器（测试隔离用，和 reset_circuit_breaker 对称）。"""
    global _default
    _default = None


async def _selftest() -> None:
    # 1. 令牌桶：RPM=60 → 容量 60（可突发）、按 1 token/s 补充。
    #    抽干 60 个 token 后，第 61 个要等约 1s。
    tb = TokenBucket(60)
    for _ in range(60):
        assert tb.wait_time() == 0.0, "容量内的 token 应立即可用"
    wait = tb.wait_time()
    assert 0.9 <= wait <= 1.1, f"抽干后第 61 个 token 应等约 1s，实际 {wait}"

    # 2. 并发上限：max_concurrency=1，第二个 acquire 应阻塞到第一个 release
    rl = RateLimiter.__new__(RateLimiter)
    rl.max_concurrency = 1
    rl._sem = asyncio.Semaphore(1)
    rl._rpm = None
    await rl.acquire()
    held = True

    async def second():
        nonlocal held
        await rl.acquire()
        held = False

    t = asyncio.create_task(second())
    await asyncio.sleep(0.05)
    assert held, "并发上限 1 时第二个 acquire 应被阻塞"
    rl.release()
    await t
    assert not held, "release 后第二个 acquire 应放行"

    print("rate_limit selftest OK")


if __name__ == "__main__":
    asyncio.run(_selftest())
