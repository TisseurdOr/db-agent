"""Streaming wrapper around MultiAgentRunner — emits SSE events during execution."""

import asyncio
import time

from anthropic import Anthropic
from langgraph.errors import GraphInterrupt

from multi_agent.orchestrator import MultiAgentRunner
from multi_agent.guardrails import guard_input
from multi_agent.state import MultiAgentState
from utils.tracer import TraceContext
from utils.opik_tracing import (
    flush_opik,
    get_current_opik_trace_id,
    capture_opik_trace_id_for_graph,
)
from server.sse import SSEEvent


class RunnerRegistry:
    """Manages MultiAgentRunner instances keyed by session_id."""

    def __init__(self):
        self._runners: dict[str, MultiAgentRunner] = {}
        self._active: MultiAgentRunner | None = None

    async def get_or_create(
        self,
        session_id: str,
        client: Anthropic,
        model: str,
    ) -> MultiAgentRunner:
        if session_id not in self._runners:
            runner = await MultiAgentRunner.create(
                client,
                model=model,
                enable_data_quality=True,
                thread_id=f"web-{session_id}",
            )
            self._runners[session_id] = runner
        self._active = self._runners[session_id]
        return self._active

    def get_active(self) -> MultiAgentRunner | None:
        return self._active

    async def close_all(self):
        for runner in self._runners.values():
            await runner.aclose()
        self._runners.clear()


runner_registry = RunnerRegistry()


class StreamingRunner:
    """Wraps a MultiAgentRunner to emit SSE events during query execution.

    Usage:
        runner = await runner_registry.get_or_create(...)
        streaming = StreamingRunner(runner)
        await streaming.run_streaming("华东销售额", queue)
    """

    def __init__(self, runner: MultiAgentRunner):
        self.runner = runner

    async def run_streaming(self, query: str, queue: asyncio.Queue) -> None:
        """Execute query and emit SSE events to queue.

        Events emitted (in order):
            step_start(preprocessing) → step_end(preprocessing)
            → step_start(router) → step_end(router)
            → step_start(sql) → text_delta(s) → step_end(sql)
            → step_start(analysis) → text_delta(s) → step_end(analysis)
            → [interrupt] (optional)
            → done
        """
        t_total = time.time()

        # ── Preprocessing ───────────────────────────────────────
        await queue.put(("step_start", SSEEvent.step_start("preprocessing", "输入护栏 + 记忆检查")))

        passed, reason = guard_input(query)
        if not passed:
            await queue.put(("step_end", SSEEvent.step_end("preprocessing", "护栏拦截", time.time() - t_total)))
            await queue.put(("error", SSEEvent.error(reason, "guard_input")))
            return

        await queue.put(("step_end", SSEEvent.step_end("preprocessing", "通过", time.time() - t_total)))

        # ── Inject event queue into config ──────────────────────
        if not hasattr(self.runner, '_current_config') or self.runner._current_config is None:
            self.runner._current_config = {
                "configurable": {
                    "thread_id": self.runner.thread_id,
                    "_client": self.runner.client,
                    "_model": self.runner.model,
                    "_trace": TraceContext(query),
                    "_router_cache": self.runner.router_cache,
                    "_task_manager": self.runner.task_manager,
                }
            }

        config = self.runner._current_config
        config["configurable"]["_event_queue"] = queue

        # ── Build state and invoke graph ────────────────────────
        state = {
            "query": query,
            "messages": [{"role": "user", "content": query}],
            "_recalled_memories": "",
            "_conversation_summary": "",
            "_inject_dq": self.runner._should_inject_dq(),
            "plan": [],
            "results": {},
            "final_answer": "",
            "next": "",
            "_stats": {},
            "_reflection_attempts": 0,
            "_replan_attempts": 0,
            "_replan_feedback": "",
        }

        await queue.put(("step_start", SSEEvent.step_start("graph", "多 Agent 执行中")))

        try:
            result = await self.runner.graph.ainvoke(state, config=config)
        except GraphInterrupt:
            result = None

        # Check for HITL interrupt
        snapshot = await self.runner.graph.aget_state(config)
        if snapshot and snapshot.interrupts:
            interrupt_data = snapshot.interrupts[0].value if snapshot.interrupts else {}
            await queue.put(("interrupt", SSEEvent.interrupt(interrupt_data)))
            return

        await queue.put(("step_end", SSEEvent.step_end("graph", "执行完成", time.time() - t_total)))

        if result is None:
            await queue.put(("error", SSEEvent.error("无法回答")))
            return

        self.runner._last_state = result

        # ── Extract results ─────────────────────────────────────
        answer = result.get("final_answer", "")
        exec_info = self.runner.get_execution_info()

        # ── Extract chart data from SQL agent results ───────────
        charts = _extract_chart_data(result)

        # Save trace + capture Opik UUID for feedback scoring
        trace = config["configurable"].get("_trace")
        oid = (
            get_current_opik_trace_id()
            or capture_opik_trace_id_for_graph(self.runner.graph)
            or (getattr(trace, "opik_trace_id", None) if trace else None)
            or ""
        )
        if trace:
            if oid:
                trace.opik_trace_id = oid
            try:
                self.runner._annotate_turn_cost(trace)
            except Exception:
                pass
            trace.save()
        flush_opik()

        await queue.put((
            "done",
            SSEEvent.done(
                total_elapsed=time.time() - t_total,
                trace_id=trace.trace_id if trace else "",
                opik_trace_id=str(oid or (getattr(trace, "opik_trace_id", None) if trace else None) or ""),
                sql=exec_info.get("sql", ""),
                answer=answer,
                plan=exec_info.get("plan", []),
                charts=charts,
                stats=exec_info.get("stats", {}),
            ),
        ))

    async def resume_streaming(self, approved: bool, queue: asyncio.Queue) -> None:
        """Resume after HITL pause, emitting remaining events to queue."""
        t_total = time.time()

        config = self.runner._current_config
        config["configurable"]["_event_queue"] = queue

        await queue.put(("step_start", SSEEvent.step_start("graph", "审批后继续执行")))

        from langgraph.types import Command
        result = await self.runner.graph.ainvoke(
            Command(resume={"approved": approved}),
            config=config,
        )

        await queue.put(("step_end", SSEEvent.step_end("graph", "执行完成", time.time() - t_total)))

        self.runner._last_state = result
        answer = result.get("final_answer", "") if result else ""
        exec_info = self.runner.get_execution_info()
        charts = _extract_chart_data(result) if result else []

        trace = config["configurable"].get("_trace")
        oid = (
            get_current_opik_trace_id()
            or capture_opik_trace_id_for_graph(self.runner.graph)
            or (getattr(trace, "opik_trace_id", None) if trace else None)
            or ""
        )
        if trace:
            if oid:
                trace.opik_trace_id = oid
            try:
                self.runner._annotate_turn_cost(trace)
            except Exception:
                pass
            trace.save()
        flush_opik()

        await queue.put((
            "done",
            SSEEvent.done(
                total_elapsed=time.time() - t_total,
                trace_id=trace.trace_id if trace else "",
                opik_trace_id=str(oid or (getattr(trace, "opik_trace_id", None) if trace else None) or ""),
                sql=exec_info.get("sql", ""),
                answer=answer,
                plan=exec_info.get("plan", []),
                charts=charts,
                stats=exec_info.get("stats", {}),
            ),
        ))


def _extract_chart_data(result: dict) -> list[dict]:
    """Extract structured chart configs from agent tool results.

    Checks render_chart results for chart_config, or auto-generates
    simple chart configs from run_query data.
    """
    charts = []
    results = result.get("results", {})

    # Check Analysis Agent results for render_chart output
    analysis_result = results.get("analysis", "")
    if isinstance(analysis_result, str) and "dashboard_path" in analysis_result:
        # render_chart was called — try to extract chart_config from its return
        pass  # config should be in the render_chart return value directly

    # Fallback: auto-generate chart from SQL query results
    sql_result_text = results.get("sql", "")
    if not charts and sql_result_text:
        chart = _auto_chart_from_sql_result(sql_result_text)
        if chart:
            charts.append(chart)

    return charts


def _auto_chart_from_sql_result(text: str) -> dict | None:
    """Try to auto-generate a chart from a SQL agent result that contains tabular data."""
    import re
    import json

    # Look for structured data patterns in the result
    # Try to find JSON with rows/count
    json_match = re.search(r'\{.*"rows"\s*:\s*\[.*?\].*\}', text, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(0))
            rows = data.get("rows", [])
            if rows and len(rows) > 1:
                return _rows_to_chart(rows)
        except (json.JSONDecodeError, KeyError):
            pass
    return None


def _rows_to_chart(rows: list[dict]) -> dict | None:
    """Convert DB rows to a simple chart config."""
    if not rows:
        return None

    keys = list(rows[0].keys())
    # Find label column (string) and value column (numeric)
    label_col = None
    value_col = None
    for k in keys:
        if label_col is None and isinstance(rows[0].get(k), str):
            label_col = k
        if value_col is None and isinstance(rows[0].get(k), (int, float)):
            value_col = k

    if label_col is None or value_col is None:
        return None

    labels = [str(row.get(label_col, "")) for row in rows[:20]]
    values = [float(row.get(value_col, 0) or 0) for row in rows[:20]]

    chart_type = "bar" if len(labels) <= 10 else "bar"

    return {
        "type": chart_type,
        "title": f"{value_col} by {label_col}",
        "labels": labels,
        "values": values,
    }
