"""工具调用幂等保护测试。

覆盖：
- 写工具同参去重（执行一次，第二次命中缓存）
- 参数不同 → 各自执行
- 读工具不去重（保持实时）
- 失败不缓存（重试真正执行）
- TTL 过期后重新执行
- agent_loop / _simple_agent_run 集成路径
"""

import pytest

from harness.constraints.idempotency import (
    IdempotencyGuard,
    get_idempotency_guard,
    reset_idempotency_guard,
    run_tool_with_guard,
)


def _counting_handler(calls):
    async def handler(**kw):
        calls.append(kw)
        return {"ok": True, "kw": kw}
    return handler


# ═══ 1. 守卫单元 ═══════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_write_tool_same_input_deduped():
    calls = []
    guard = IdempotencyGuard(write_tools=["save_to_memory"], ttl_seconds=60, enabled=True)
    handler = _counting_handler(calls)

    r1, rep1 = await guard.execute("save_to_memory", {"content": "a"}, handler)
    r2, rep2 = await guard.execute("save_to_memory", {"content": "a"}, handler)

    assert len(calls) == 1          # 只真实执行了一次
    assert rep1 is False and rep2 is True
    assert r1 == r2


@pytest.mark.asyncio
async def test_write_tool_different_input_runs_twice():
    calls = []
    guard = IdempotencyGuard(write_tools=["save_to_memory"], ttl_seconds=60, enabled=True)
    handler = _counting_handler(calls)

    await guard.execute("save_to_memory", {"content": "a"}, handler)
    await guard.execute("save_to_memory", {"content": "b"}, handler)

    assert len(calls) == 2


@pytest.mark.asyncio
async def test_read_tool_not_deduped():
    """读工具（如 run_query）不在写列表 → 每次都真实执行，保证数据实时。"""
    calls = []
    guard = IdempotencyGuard(write_tools=["save_to_memory"], ttl_seconds=60, enabled=True)
    handler = _counting_handler(calls)

    await guard.execute("run_query", {"sql": "SELECT 1"}, handler)
    await guard.execute("run_query", {"sql": "SELECT 1"}, handler)

    assert len(calls) == 2


@pytest.mark.asyncio
async def test_failure_not_cached_retry_runs_again():
    """执行失败 → 不缓存；重试时真正重新执行。"""
    attempts = {"n": 0}

    def flaky(**kw):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("第一次失败")
        return {"ok": True}

    guard = IdempotencyGuard(write_tools=["save_to_memory"], ttl_seconds=60, enabled=True)
    with pytest.raises(RuntimeError):
        await guard.execute("save_to_memory", {"content": "x"}, flaky)
    result, replayed = await guard.execute("save_to_memory", {"content": "x"}, flaky)

    assert attempts["n"] == 2
    assert replayed is False
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_ttl_expiry_runs_again(monkeypatch):
    calls = []
    guard = IdempotencyGuard(write_tools=["save_to_memory"], ttl_seconds=0.05, enabled=True)
    handler = _counting_handler(calls)

    await guard.execute("save_to_memory", {"content": "a"}, handler)
    import time
    time.sleep(0.08)  # 超过 TTL
    await guard.execute("save_to_memory", {"content": "a"}, handler)

    assert len(calls) == 2


# ═══ 2. 全局守卫 / 模块入口 ════════════════════════════════════════


@pytest.mark.asyncio
async def test_module_entry_uses_default_guard():
    reset_idempotency_guard()
    calls = []
    handler = _counting_handler(calls)

    r1, rep1 = await run_tool_with_guard("save_to_memory", {"content": "a"}, handler)
    r2, rep2 = await run_tool_with_guard("save_to_memory", {"content": "a"}, handler)

    assert len(calls) == 1
    assert rep1 is False and rep2 is True
    assert get_idempotency_guard() is not None


# ═══ 3. agent 集成路径 ═════════════════════════════════════════════


@pytest.mark.asyncio
async def test_agent_loop_dedupes_write_tool():
    """Agent 两轮都发同一 save_to_memory 调用 → 真实只执行一次。"""
    from harness.orchestration.single.agent import agent_loop
    from tests.test_agent import _FakeClient, _FakeTextBlock, _FakeToolUseBlock

    reset_idempotency_guard()
    saved = []

    def save_to_memory(content, memory_type="conversation"):
        saved.append(content)
        return {"ok": True}

    client = _FakeClient([
        [_FakeToolUseBlock("save_to_memory", {"content": "记住：销售部最高"})],
        [_FakeToolUseBlock("save_to_memory", {"content": "记住：销售部最高"})],
        [_FakeTextBlock("已保存。")],
    ])
    tools = [{
        "name": "save_to_memory",
        "description": "保存记忆",
        "input_schema": {"type": "object", "properties": {"content": {"type": "string"}}},
    }]
    result = await agent_loop(
        client, "帮我记住这个", "prompt", tools=tools,
        handlers={"save_to_memory": save_to_memory},
    )
    assert "已保存" in result
    assert len(saved) == 1  # 第二次同一调用被幂等拦截


@pytest.mark.asyncio
async def test_simple_agent_run_dedupes_write_tool():
    from harness.orchestration.multi.base import _simple_agent_run
    from tests.test_agent import _FakeClient, _FakeTextBlock, _FakeToolUseBlock

    reset_idempotency_guard()
    saved = []

    def save_to_memory(content):
        saved.append(content)
        return {"ok": True}

    client = _FakeClient([
        [_FakeToolUseBlock("save_to_memory", {"content": "x"})],
        [_FakeToolUseBlock("save_to_memory", {"content": "x"})],
        [_FakeTextBlock("完成")],
    ])
    tools = [{
        "name": "save_to_memory",
        "description": "保存",
        "input_schema": {"type": "object", "properties": {"content": {"type": "string"}}},
    }]
    text, _usage = await _simple_agent_run(
        client, "记住 x", "prompt", tools=tools, handlers={"save_to_memory": save_to_memory},
        max_turns=5,
    )
    assert "完成" in text
    assert len(saved) == 1
