"""工具调用幂等保护——防止重试/重规划导致副作用重复执行。

经典场景：Agent 在某轮执行了"写"类工具（保存记忆、HBase put/delete 等），
但后续 LLM 调用失败被重试，或失败重规划后模型又发出完全相同的工具调用——
如果不加保护，副作用会执行两次（重复写库、重复发消息、重复扣费）。

实现：按 (tool_name, 规范化参数) 做键，在 TTL 窗口内：
  - 同键调用已成功 → 直接返回缓存结果，不重复执行（replayed=True）
  - 之前失败 → 不缓存，允许重试真正执行
只对"写/副作用"类工具生效（默认列表 + 可配置）；读查询保持实时，不受影响。
"""

import asyncio
import hashlib
import inspect
import json
import os
import time

# 写/副作用类工具名——只有这些才做幂等去重
DEFAULT_WRITE_TOOLS = ("save_to_memory", "run_hbase")


class IdempotencyGuard:
    """按 (tool_name, 参数) 去重的工具执行守卫。"""

    def __init__(self, write_tools=None, ttl_seconds: float | None = None,
                 enabled: bool | None = None):
        self.write_tools = set(write_tools or DEFAULT_WRITE_TOOLS)
        self.ttl = (
            ttl_seconds
            if ttl_seconds is not None
            else float(os.getenv("TOOL_IDEMPOTENCY_TTL", "300"))
        )
        self.enabled = (
            enabled
            if enabled is not None
            else os.getenv("TOOL_IDEMPOTENCY_ENABLED", "1").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        self._cache: dict[str, tuple[float, object]] = {}
        self._lock = asyncio.Lock()

    # ── 键 ───────────────────────────────────────────────────────────

    def _key(self, tool_name: str, tool_input: dict) -> str:
        canonical = json.dumps(
            tool_input, sort_keys=True, ensure_ascii=False, default=str,
        )
        digest = hashlib.md5(canonical.encode("utf-8")).hexdigest()
        return f"{tool_name}:{digest}"

    # ── 执行 ─────────────────────────────────────────────────────────

    async def execute(self, tool_name: str, tool_input: dict, handler) -> tuple[object, bool]:
        """执行工具（带幂等保护）。

        Returns:
            (result, replayed)
            replayed=True 表示命中了幂等缓存，未真实执行 handler。
        """
        if not self.enabled or tool_name not in self.write_tools:
            return await _invoke(handler, tool_input), False

        key = self._key(tool_name, tool_input)
        now = time.monotonic()
        async with self._lock:
            hit = self._cache.get(key)
            if hit is not None and now - hit[0] <= self.ttl:
                return hit[1], True
            self._prune(now)

        result = await _invoke(handler, tool_input)
        async with self._lock:
            self._cache[key] = (time.monotonic(), result)
        return result, False

    def _prune(self, now: float) -> None:
        """清理过期缓存条目。调用方需持有锁。"""
        expired = [k for k, (ts, _) in self._cache.items() if now - ts > self.ttl]
        for k in expired:
            del self._cache[k]

    def clear(self) -> None:
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)


async def _invoke(handler, tool_input: dict):
    """调用 handler（兼容同步/异步），异常原样上抛（失败不缓存）。"""
    result = handler(**tool_input)
    if inspect.isawaitable(result):
        result = await result
    return result


# ── 全局默认守卫 ────────────────────────────────────────────────────

_guard: IdempotencyGuard | None = None


def get_idempotency_guard() -> IdempotencyGuard:
    global _guard
    if _guard is None:
        _guard = IdempotencyGuard()
    return _guard


def reset_idempotency_guard() -> None:
    """重置默认守卫（测试隔离用）。"""
    global _guard
    _guard = None


async def run_tool_with_guard(tool_name: str, tool_input: dict, handler) -> tuple[object, bool]:
    """模块级入口：用默认守卫执行工具，返回 (result, replayed)。"""
    return await get_idempotency_guard().execute(tool_name, tool_input, handler)
