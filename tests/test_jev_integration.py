"""Jev 在 Router / 置信度门里的接入测试（含回退语义）。

要点：Jev 是**可选增强**——没配 key 或调用失败时，必须完整回退到原 LLM 路径，
不能让主流程变脆。
"""

import pytest

from harness import jev_client

# ── Router：Agent 选择 ──

@pytest.mark.asyncio
async def test_router_disabled_returns_empty(monkeypatch):
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    from harness.orchestration.multi.nodes import _route_via_jev
    assert await _route_via_jev("各部门销售额") == {}


@pytest.mark.asyncio
async def test_router_maps_jev_choice_to_plan(monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "k")

    async def fake_decide(state, questions, **kw):
        return {"answers": [
            {"id": "next_agent", "choice": "hbase"},
            {"id": "route_confidence", "probability": 0.92},
        ]}

    monkeypatch.setattr(jev_client, "decide", fake_decide)
    from harness.orchestration.multi.nodes import _route_via_jev
    out = await _route_via_jev("HBase 里查一下订单")
    assert out["plan"] == [{"agent": "hbase", "task": "HBase 里查一下订单"}]
    assert out["confidence"] == "high"


@pytest.mark.asyncio
async def test_router_low_confidence_marks_low(monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "k")

    async def fake_decide(state, questions, **kw):
        return {"answers": [
            {"id": "next_agent", "choice": "sql"},
            {"id": "route_confidence", "probability": 0.2},
        ]}

    monkeypatch.setattr(jev_client, "decide", fake_decide)
    from harness.orchestration.multi.nodes import _route_via_jev
    out = await _route_via_jev("那个东西怎么样")
    assert out["confidence"] == "low"


@pytest.mark.asyncio
async def test_router_jev_failure_falls_back(monkeypatch):
    """Jev 启用但返回空 → {} 让调用方走 LLM（不回退到"瞎派"）。"""
    monkeypatch.setenv("JEV_API_KEY", "k")

    async def empty_decide(state, questions, **kw):
        return None

    monkeypatch.setattr(jev_client, "decide", empty_decide)
    from harness.orchestration.multi.nodes import _route_via_jev
    assert await _route_via_jev("随便问问") == {}


# ── 置信度门 ──

@pytest.mark.asyncio
async def test_confidence_uses_jev_probability(monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "k")

    async def fake_decide(state, questions, **kw):
        return {"answers": [{"id": "sql_confidence", "probability": 0.83}]}

    monkeypatch.setattr(jev_client, "decide", fake_decide)
    from harness.orchestration.multi.nodes import _assess_sql_confidence
    data, usage = await _assess_sql_confidence("各部门销售额", "SELECT 1", "结果", None, "m")
    assert data["confidence"] == 0.83
    assert data["explanation"] == "Jev 校准概率"
    assert usage["turns"] == 0  # Jev 不消耗 LLM token


@pytest.mark.asyncio
async def test_confidence_jev_missing_probability_uses_llm(monkeypatch):
    """Jev 返回里没有该问题的概率 → 落到 LLM 自评（这里用假 client 验证走了 LLM 分支）。"""
    monkeypatch.setenv("JEV_API_KEY", "k")

    async def empty_decide(state, questions, **kw):
        return {"answers": []}

    monkeypatch.setattr(jev_client, "decide", empty_decide)

    class FakeResp:
        content = []
        usage = None

    class FakeMessages:
        called = False

        async def create(self, **kw):
            FakeMessages.called = True
            return FakeResp()

    class FakeClient:
        messages = FakeMessages()

    from harness.orchestration.multi.nodes import _assess_sql_confidence
    data, _ = await _assess_sql_confidence("q", "SELECT 1", "res", FakeClient(), "m")
    assert FakeMessages.called, "取不到 Jev 概率时必须以 LLM 兜底"
    assert "confidence" in data
