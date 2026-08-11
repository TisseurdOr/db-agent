"""eval_runner HITL 断言：测的是 interrupt，不是最终文案。"""

import asyncio

from tests.eval_cases import EvalCase
from tests.eval_runner import (
    _agents_from_interrupt,
    _interrupt_data,
    _is_interrupt,
    _run_full_case,
)


def test_is_interrupt_detects_payload():
    assert _is_interrupt({"__interrupt__": True, "data": {"type": "hitl_approval"}})
    assert not _is_interrupt("已写入")
    assert not _is_interrupt({"ok": True})


def test_interrupt_data_and_tool_mapping():
    payload = {
        "__interrupt__": True,
        "data": {"type": "hitl_approval", "tool": "run_hbase", "operation": "put"},
    }
    data = _interrupt_data(payload)
    assert data["type"] == "hitl_approval"
    assert _agents_from_interrupt(data) == {"hbase"}
    assert _agents_from_interrupt({"tool": "run_query"}) == {"sql"}
    assert _agents_from_interrupt({}) == set()


class _FakeRunner:
    def __init__(self, first):
        self.first = first
        self.resume_calls: list[bool] = []

    async def run(self, query: str):
        return self.first

    async def resume(self, approved: bool = True):
        self.resume_calls.append(approved)
        return "用户拒绝了该操作" if not approved else "已写入"


def test_expect_hitl_passes_on_interrupt_and_rejects():
    case = EvalCase(
        id="hitl-unit",
        category="edge",
        query="put",
        description="unit",
        assertions={"expect_hitl": True, "agent_in_plan": ["hbase"]},
    )
    runner = _FakeRunner({
        "__interrupt__": True,
        "data": {
            "type": "hitl_approval",
            "tool": "run_hbase",
            "operation": "put",
            "message": "HBase 写操作需要审批。",
        },
    })
    result, answer = asyncio.run(_run_full_case(case, runner))
    assert result.passed, result.details
    assert runner.resume_calls == [False]
    assert "审批" in answer


def test_expect_hitl_fails_when_no_interrupt():
    case = EvalCase(
        id="hitl-unit-miss",
        category="edge",
        query="put",
        description="unit",
        assertions={"expect_hitl": True},
    )
    runner = _FakeRunner("已经帮你写入了")
    result, _ = asyncio.run(_run_full_case(case, runner))
    assert not result.passed
    assert any("应触发 HITL" in d for d in result.details)
    assert runner.resume_calls == []
