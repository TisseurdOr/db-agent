"""错误恢复机制测试 —— API 重试 / SQL 自愈信号 / 失败重规划。

全部零 API 成本：重试用假异常模拟，重规划直接测 _maybe_replan 的纯逻辑。
"""

import pytest

from harness.constraints.retry import acall_with_retry, call_with_retry, is_retriable
from harness.orchestration.multi.agents import HIVE_AGENT_PROMPT, SQL_AGENT_PROMPT
from harness.orchestration.multi.orchestrator import MAX_REPLAN_ATTEMPTS, _maybe_replan
from harness.tools.query import run_query

# ── 假异常：带 status_code 属性，is_retriable 靠它判断 ──

class FakeRateLimit(Exception):
    status_code = 429


class FakeServerError(Exception):
    status_code = 503


class FakeBadRequest(Exception):
    status_code = 400


# ═══ 1. API 重试 ═══

def test_is_retriable_status_codes():
    assert is_retriable(FakeRateLimit())
    assert is_retriable(FakeServerError())
    assert not is_retriable(FakeBadRequest())      # 参数错，重试无意义
    assert not is_retriable(ValueError("普通异常"))  # 无 status_code


def test_retry_recovers_after_transient_failure():
    """前两次 429，第三次成功——应拿到结果且共调用 3 次。"""
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise FakeRateLimit()
        return "ok"

    assert call_with_retry(flaky, max_retries=3, base_delay=0) == "ok"
    assert calls["n"] == 3


def test_retry_gives_up_after_budget():
    """一直 429：重试预算用完后抛出原异常，共调用 max_retries+1 次。"""
    calls = {"n": 0}

    def always_fail():
        calls["n"] += 1
        raise FakeRateLimit()

    with pytest.raises(FakeRateLimit):
        call_with_retry(always_fail, max_retries=2, base_delay=0)
    assert calls["n"] == 3  # 首次 + 2 次重试


def test_non_retriable_raises_immediately():
    """400 不重试——立即抛出，只调用 1 次。"""
    calls = {"n": 0}

    def bad_request():
        calls["n"] += 1
        raise FakeBadRequest()

    with pytest.raises(FakeBadRequest):
        call_with_retry(bad_request, max_retries=3, base_delay=0)
    assert calls["n"] == 1


async def test_async_retry_recovers():
    """异步版本：同步/协程函数都支持，退避不阻塞事件循环。"""
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise FakeServerError()
        return "ok"

    assert await acall_with_retry(flaky, max_retries=2, base_delay=0) == "ok"
    assert calls["n"] == 2


# ═══ 2. SQL 自愈信号 ═══

def test_sql_agent_prompt_has_heal_protocol():
    """SQL/Hive Agent prompt 必须包含自愈协议——报错后重写重试，而不是直接放弃。"""
    for prompt in (SQL_AGENT_PROMPT, HIVE_AGENT_PROMPT):
        assert "自愈" in prompt
        assert "重试" in prompt
        assert "不要编造数据" in prompt


def test_run_query_error_payload_is_healable():
    """SQL 执行报错时，错误返回必须带 retryable 标记 + 可执行的修复指引。"""
    result = run_query("SELECT nonexist_col FROM departments", user_id="dba")
    assert "error" in result
    assert result.get("retryable") is True
    assert "describe_table" in result.get("hint", "")
    assert result.get("sql")  # 失败的 SQL 原样返回，供模型对照重写


# ═══ 3. 失败重规划 ═══

TIMEOUT_RESULT = "(Agent 在 8 轮内未完成)"


def test_replan_triggered_on_agent_timeout():
    """Agent 超轮数失败 → 回 Router，attempts +1，失败结果从 results 移除。"""
    state = {"_replan_attempts": 0, "plan": [{"agent": "sql", "task": "x"}]}
    results = {"sql": TIMEOUT_RESULT, "strategy": "正常结果"}

    update = _maybe_replan(state, results, "sql", TIMEOUT_RESULT)

    assert update is not None
    assert update["next"] == "router"
    assert update["_replan_attempts"] == 1
    assert "sql" not in update["results"]          # 不删的话新 plan 里 sql 会被跳过
    assert update["results"]["strategy"] == "正常结果"  # 其他结果保留
    assert "sql" in update["_replan_feedback"]     # 反馈里说明谁失败了


def test_replan_skipped_on_normal_result():
    state = {"_replan_attempts": 0}
    assert _maybe_replan(state, {"sql": "查到 5 行"}, "sql", "查到 5 行") is None


def test_replan_budget_exhausted():
    """重规划额度用完（上限 1 次）→ 不再回 Router，走原路径由 Analysis 兜底。"""
    state = {"_replan_attempts": MAX_REPLAN_ATTEMPTS}
    update = _maybe_replan(state, {"sql": TIMEOUT_RESULT}, "sql", TIMEOUT_RESULT)
    assert update is None


# ═══ 4. 失败状态的可观测性（Task board / Trace 耗时）═══

def test_task_board_marks_failure(tmp_path):
    """Agent 失败时任务标 failed（✗），而不是谎报 completed；下游任务保持阻塞。"""
    from harness.orchestration.multi.task_system import TaskManager

    tm = TaskManager(tasks_dir=tmp_path)
    tasks = tm.materialize_from_plan(
        [{"agent": "sql", "task": "查数据"}, {"agent": "analysis", "task": "分析"}]
    )
    tm.on_agent_failed("sql")

    assert tm.get(tasks[0].id).status == "failed"
    assert not tm.can_start(tasks[1].id)  # blockedBy 要求 completed，failed 不解锁


def test_trace_elapsed_never_negative():
    """trace 总耗时必须非负——回归：finished_at 曾用 monotonic 混了 time.time 基准。"""
    from harness.observation.tracer import TraceContext

    trace = TraceContext("测试查询")
    span = trace.start_span("sql", "任务")
    trace.finish_span(span, {"input_tokens": 1, "output_tokens": 1, "turns": 1})
    data = trace.to_dict()
    assert data["elapsed"] >= 0
    assert "-" not in trace.summary().split("·")[0]  # 摘要里不出现负秒数


def test_graph_has_replan_edges():
    """图接线回归：每个业务 Agent 节点必须有回 Router 的边。

    背景：_maybe_replan 返回 next="router"，但 targets 映射里最初漏了
    "router" 键，运行时 KeyError——单测只测纯逻辑抓不到，必须测图本身。
    """
    from harness.orchestration.multi.orchestrator import build_multi_agent_graph

    graph = build_multi_agent_graph()
    edges = {(e.source, e.target) for e in graph.get_graph().edges}
    for agent_node in ("sql", "hbase", "hive", "strategy", "data_quality"):
        assert (agent_node, "router") in edges, f"{agent_node} 缺少回 Router 的重规划边"


# ═══ 4. Streaming 的"未输出才重试" ═══
#
# streaming_agent 的重试策略：一旦有内容打到终端，重试会导致重复输出，
# 所以只有"还没输出任何内容"时才允许重试。用假 client + 假 stream 验证。

from types import SimpleNamespace

from harness.orchestration.single.agent import streaming_agent


class StubBudget:
    """跳过 token 预算逻辑——本测试只关心重试分支。"""

    def set_fixed_costs(self, *args, **kwargs):
        pass

    def should_compress(self, messages):
        return False

    def summary(self, messages):
        return ""


def _text_event(text: str):
    return SimpleNamespace(
        type="content_block_delta",
        delta=SimpleNamespace(type="text_delta", text=text),
        index=0,
    )


class FakeStream:
    """可控假流：先 yield events，然后可选地在迭代中途抛异常。"""

    def __init__(self, events=(), exc=None):
        self.events = list(events)
        self.exc = exc

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        yield from self.events
        if self.exc is not None:
            raise self.exc

    def get_final_message(self):
        # 无 tool_use → streaming_agent 直接返回累积的 text_content
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="")])


class FakeStreamClient:
    """client.messages.stream 按脚本依次返回 FakeStream 或直接抛异常。"""

    def __init__(self, script: list):
        self.script = list(script)
        self.calls = 0
        self.messages = self  # 让 client.messages.stream 指向自己

    def stream(self, **kwargs):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


async def _run_streaming(client):
    return await streaming_agent(
        client,
        user_msg="测试",
        system_prompt="sp",
        tools=[],
        handlers={},
        model="test-model",
        budget=StubBudget(),
        window_manager=object(),  # should_compress 恒 False，不会被用到
    )


async def test_streaming_retries_when_nothing_emitted(monkeypatch):
    """建连即 429（尚未输出任何内容）→ 自动重试，第二次成功。"""
    monkeypatch.setenv("LLM_MAX_RETRIES", "2")
    monkeypatch.setenv("LLM_RETRY_BASE_DELAY", "0")

    client = FakeStreamClient([FakeRateLimit(), FakeStream(events=[_text_event("ok")])])
    result = await _run_streaming(client)

    assert result == "ok"
    assert client.calls == 2


async def test_streaming_no_retry_after_output_emitted(monkeypatch):
    """已经打出部分文字后才失败 → 不重试直接抛（重试会导致重复输出）。"""
    monkeypatch.setenv("LLM_MAX_RETRIES", "3")
    monkeypatch.setenv("LLM_RETRY_BASE_DELAY", "0")

    client = FakeStreamClient([FakeStream(events=[_text_event("部分输出")], exc=FakeRateLimit())])
    with pytest.raises(FakeRateLimit):
        await _run_streaming(client)

    assert client.calls == 1  # 即使还有重试预算也不再调用


async def test_streaming_non_retriable_raises_immediately(monkeypatch):
    """400 参数错：即使没输出过内容也不重试。"""
    monkeypatch.setenv("LLM_MAX_RETRIES", "3")
    monkeypatch.setenv("LLM_RETRY_BASE_DELAY", "0")

    client = FakeStreamClient([FakeBadRequest()])
    with pytest.raises(FakeBadRequest):
        await _run_streaming(client)

    assert client.calls == 1
