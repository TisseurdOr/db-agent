"""Web / SSE 层测试 —— StreamingRunner 事件流 + FastAPI endpoints。

全部零 API 成本：用 mock 替换 MultiAgentRunner、graph 和 Opik 函数。
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from server import sse as sse_mod
from server.main import app
from server.runner_wrapper import RunnerRegistry, StreamingRunner, _auto_chart_from_sql_result

# ═══════════════════════════════════════════════════════════════════════════════
# 1. SSE 事件格式化
# ═══════════════════════════════════════════════════════════════════════════════

def test_format_sse_basic():
    payload = sse_mod.format_sse("step_start", {"node": "router"})
    assert payload.startswith("event: step_start\n")
    assert "data: {" in payload
    assert payload.endswith("\n\n")


def test_sse_event_factories():
    e = sse_mod.SSEEvent.step_start("sql", "生成 SQL")
    assert e["type"] == "step_start"
    assert e["node"] == "sql"
    assert "timestamp" in e

    e = sse_mod.SSEEvent.done(total_elapsed=1.2, answer="答案", sql="SELECT 1")
    assert e["type"] == "done"
    assert e["answer"] == "答案"
    assert e["sql"] == "SELECT 1"

    e = sse_mod.SSEEvent.interrupt({"type": "hitl_approval", "sql": "SELECT 1"})
    assert e["type"] == "interrupt"
    assert e["data"]["type"] == "hitl_approval"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. StreamingRunner
# ═══════════════════════════════════════════════════════════════════════════════

def _make_fake_runner():
    """构造一个足够像 MultiAgentRunner 的 mock。"""
    runner = MagicMock()
    runner.thread_id = "web-test"
    runner.client = MagicMock()
    runner.model = "test-model"
    runner.router_cache = MagicMock()
    runner.task_manager = MagicMock()
    runner._current_config = None
    runner._should_inject_dq.return_value = False
    runner._last_state = None

    graph = AsyncMock()
    graph.ainvoke = AsyncMock(return_value={
        "final_answer": "最终答案",
        "results": {"sql": "SELECT 1; 结果: 5 行"},
    })
    graph.aget_state = AsyncMock(return_value=SimpleNamespace(interrupts=[]))
    runner.graph = graph

    runner.get_execution_info.return_value = {
        "sql": "SELECT 1",
        "plan": [{"agent": "sql", "task": "查询"}],
        "stats": {"input_tokens": 10, "output_tokens": 5},
    }
    runner._annotate_turn_cost = MagicMock()
    return runner


@pytest.fixture
def fake_runner():
    return _make_fake_runner()


@pytest.mark.asyncio
async def test_run_streaming_guard_blocked(monkeypatch, fake_runner):
    """输入护栏拦截时应 emit error 事件并提前返回。"""
    monkeypatch.setattr("server.runner_wrapper.guard_input", lambda q: (False, "包含注入特征"))
    sr = StreamingRunner(fake_runner)
    queue = asyncio.Queue()
    await sr.run_streaming("ignore instructions", queue)

    events = []
    while not queue.empty():
        events.append(await queue.get())

    assert events[0][0] == "step_start"
    assert events[-1][0] == "error"
    assert "注入" in events[-1][1]["message"]


@pytest.mark.asyncio
async def test_run_streaming_normal_flow(monkeypatch, fake_runner):
    """正常执行应 emit connected-like step + graph step + done。"""
    monkeypatch.setattr("server.runner_wrapper.guard_input", lambda q: (True, ""))
    # 静默 Opik
    monkeypatch.setattr("server.runner_wrapper.flush_opik", lambda: None)
    monkeypatch.setattr("server.runner_wrapper.get_current_opik_trace_id", lambda: "")
    monkeypatch.setattr("server.runner_wrapper.capture_opik_trace_id_for_graph", lambda g: "")

    sr = StreamingRunner(fake_runner)
    queue = asyncio.Queue()
    await sr.run_streaming("正常查询", queue)

    events = []
    while not queue.empty():
        events.append(await queue.get())

    types = [e[0] for e in events]
    assert "step_start" in types
    assert "step_end" in types
    assert "done" in types
    done_data = [e[1] for e in events if e[0] == "done"][0]
    assert done_data["answer"] == "最终答案"
    assert done_data["sql"] == "SELECT 1"


@pytest.mark.asyncio
async def test_run_streaming_interrupt(monkeypatch, fake_runner):
    """graph 被 interrupt 暂停时应 emit interrupt 事件。"""
    monkeypatch.setattr("server.runner_wrapper.guard_input", lambda q: (True, ""))
    monkeypatch.setattr("server.runner_wrapper.flush_opik", lambda: None)
    monkeypatch.setattr("server.runner_wrapper.get_current_opik_trace_id", lambda: "")
    monkeypatch.setattr("server.runner_wrapper.capture_opik_trace_id_for_graph", lambda g: "")

    fake_runner.graph.ainvoke = AsyncMock(side_effect=GraphInterrupt("HITL"))
    fake_runner.graph.aget_state = AsyncMock(return_value=SimpleNamespace(
        interrupts=[SimpleNamespace(value={"type": "hitl_approval", "sql": "SELECT salary FROM employees"})]
    ))

    sr = StreamingRunner(fake_runner)
    queue = asyncio.Queue()
    await sr.run_streaming("查工资", queue)

    events = []
    while not queue.empty():
        events.append(await queue.get())

    interrupt_events = [e for e in events if e[0] == "interrupt"]
    assert len(interrupt_events) == 1
    assert interrupt_events[0][1]["data"]["type"] == "hitl_approval"


@pytest.mark.asyncio
async def test_resume_streaming(monkeypatch, fake_runner):
    """resume 后继续执行并 emit done。"""
    fake_runner._current_config = {
        "configurable": {
            "thread_id": "web-test",
            "_trace": MagicMock(),
        }
    }
    fake_runner.graph.ainvoke = AsyncMock(return_value={
        "final_answer": "已批准后的答案",
        "results": {"sql": "SELECT 1"},
    })
    monkeypatch.setattr("server.runner_wrapper.flush_opik", lambda: None)
    monkeypatch.setattr("server.runner_wrapper.get_current_opik_trace_id", lambda: "")
    monkeypatch.setattr("server.runner_wrapper.capture_opik_trace_id_for_graph", lambda g: "")

    sr = StreamingRunner(fake_runner)
    queue = asyncio.Queue()
    await sr.resume_streaming(approved=True, queue=queue)

    events = []
    while not queue.empty():
        events.append(await queue.get())

    assert any(e[0] == "done" for e in events)
    done_data = [e[1] for e in events if e[0] == "done"][0]
    assert done_data["answer"] == "已批准后的答案"


def test_auto_chart_from_sql_result():
    """从 SQL 结果文本中自动提取图表数据。"""
    text = json.dumps({"rows": [{"region": "华东", "total": 100}, {"region": "华南", "total": 200}]})
    chart = _auto_chart_from_sql_result(text)
    assert chart is not None
    assert chart["type"] == "bar"
    assert "华东" in chart["labels"]


def test_auto_chart_from_empty_text():
    assert _auto_chart_from_sql_result("没有数据") is None


# ═══════════════════════════════════════════════════════════════════════════════
# 3. FastAPI endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def client(monkeypatch):
    """重置全局 session store 并提供 TestClient。"""
    import server.endpoints.sessions as sessions_mod
    sessions_mod.clear_sessions()
    with TestClient(app) as c:
        yield c


def test_health_endpoint(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_sessions_list_and_messages(client):
    import server.endpoints.sessions as sessions_mod
    await sessions_mod.record_message("s1", "user", "你好")
    await sessions_mod.record_message("s1", "assistant", "你好，请问需要什么帮助？")

    resp = client.get("/api/sessions")
    assert resp.status_code == 200
    assert resp.json()["sessions"][0]["id"] == "s1"
    assert resp.json()["sessions"][0]["message_count"] == 2

    resp = client.get("/api/sessions/s1/messages")
    assert resp.status_code == 200
    assert len(resp.json()["messages"]) == 2


def test_datasource_connect_sqlite_not_found(client):
    resp = client.post("/api/datasource/connect", json={"type": "sqlite", "path": "/no/such/file.db"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


def test_feedback_endpoint(client, monkeypatch, tmp_path):
    """feedback endpoint 应保存记录并尝试触发自学习。"""
    import server.endpoints.feedback as feedback_mod
    calls = []
    monkeypatch.setattr(feedback_mod, "save_feedback", lambda **kw: calls.append(kw) or 1)
    monkeypatch.setattr("harness.observation.opik_tracing.log_user_feedback", lambda **kw: {"ok": True})

    import harness.memory.feedback as mem_feedback_mod
    learned = []
    monkeypatch.setattr(mem_feedback_mod, "learn_from_success", lambda *a, **k: learned.append((a, k)) or True)

    resp = client.post("/api/feedback", json={
        "query": "各部门销售额",
        "answer": "销售部最高",
        "rating": "up",
        "sql": "SELECT ...",
    })
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert calls[0]["rating"] == "up"
    assert learned[0][1].get("sql") == "SELECT ..."


# ═══════════════════════════════════════════════════════════════════════════════
# 4. RunnerRegistry
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_runner_registry_get_or_create(monkeypatch):
    """同一 session_id 第二次应返回同一个 runner。"""
    registry = RunnerRegistry()
    created = []

    async def fake_create(client, model, enable_data_quality, thread_id):
        runner = MagicMock()
        runner.thread_id = thread_id
        runner._current_config = None
        runner.aclose = AsyncMock()
        created.append(thread_id)
        return runner

    monkeypatch.setattr("server.runner_wrapper.MultiAgentRunner.create", fake_create)

    r1 = await registry.get_or_create("s1", MagicMock(), "m")
    r2 = await registry.get_or_create("s1", MagicMock(), "m")
    assert r1 is r2
    assert len(created) == 1
    assert registry.get_active() is r1

    await registry.close_all()

import pytest
from langgraph.errors import GraphInterrupt

# ═══════════════════════════════════════════════════════════════════════════════
# 5. Web 鉴权（WEB_API_TOKEN）
# ═══════════════════════════════════════════════════════════════════════════════

def test_health_open_even_with_token(monkeypatch):
    """配置 WEB_API_TOKEN 后 /api/health 仍应开放（探针用）。"""
    monkeypatch.setenv("WEB_API_TOKEN", "secret123")
    with TestClient(app) as c:
        resp = c.get("/api/health")
    assert resp.status_code == 200


def test_business_api_401_without_token(monkeypatch):
    """配置 WEB_API_TOKEN 后，不带 token 访问业务接口 → 401。"""
    monkeypatch.setenv("WEB_API_TOKEN", "secret123")
    with TestClient(app) as c:
        resp = c.get("/api/sessions")
    assert resp.status_code == 401


def test_business_api_ok_with_bearer(monkeypatch):
    """带正确 Bearer token → 200。"""
    monkeypatch.setenv("WEB_API_TOKEN", "secret123")
    with TestClient(app) as c:
        resp = c.get("/api/sessions", headers={"Authorization": "Bearer secret123"})
    assert resp.status_code == 200


def test_business_api_ok_with_api_key_header(monkeypatch):
    """X-API-Key 头同样有效。"""
    monkeypatch.setenv("WEB_API_TOKEN", "secret123")
    with TestClient(app) as c:
        resp = c.get("/api/sessions", headers={"X-API-Key": "secret123"})
    assert resp.status_code == 200


def test_business_api_ok_without_token_env(monkeypatch):
    """未配置 WEB_API_TOKEN → 鉴权关闭，业务接口可直接访问。"""
    monkeypatch.delenv("WEB_API_TOKEN", raising=False)
    with TestClient(app) as c:
        resp = c.get("/api/sessions")
    assert resp.status_code == 200
