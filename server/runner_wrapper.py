"""Streaming wrapper around MultiAgentRunner — emits SSE events during execution."""

import asyncio
import time

from anthropic import Anthropic
from langgraph.errors import GraphInterrupt

from harness.constraints.entitlement import reset_request_user, set_request_user
from harness.constraints.guardrails import guard_input
from harness.memory.short_term_memory import ConversationManager
from harness.observation.opik_tracing import (
    capture_opik_trace_id_for_graph,
    flush_opik,
    get_current_opik_trace_id,
)
from harness.observation.tracer import TraceContext
from harness.orchestration.multi.agent_names import AGENT_ANALYSIS, AGENT_SQL
from harness.orchestration.multi.orchestrator import MultiAgentRunner
from server.sse import SSEEvent


def _interrupt_payload(result, snapshot) -> dict | None:
    """从 ainvoke 返回值或 checkpoint snapshot 取出 HITL payload。

    LangGraph ≥1.x：`ainvoke` 遇 `interrupt()` 通常 **不抛** GraphInterrupt，
    而是返回 `{...state, "__interrupt__": (Interrupt,...)}`；旧版才 raise。
    两路都要认，否则会把半截 result 当成成功 → 空 final_answer / 前端像挂死。
    """
    if snapshot is not None and getattr(snapshot, "interrupts", None):
        first = snapshot.interrupts[0]
        val = getattr(first, "value", first)
        return val if isinstance(val, dict) else {"message": str(val)}

    if isinstance(result, dict):
        raw = result.get("__interrupt__")
        if raw:
            first = raw[0] if isinstance(raw, (list, tuple)) else raw
            val = getattr(first, "value", first)
            return val if isinstance(val, dict) else {"message": str(val)}
    return None


class RunnerRegistry:
    """Manages MultiAgentRunner instances keyed by session_id.

    带空闲 TTL 回收：长期不活跃的 runner 会被关闭，防止并发下内存泄漏。
    同一 session 的 HITL resume 按 session_id 精确定位，不再依赖全局 active。
    """

    # 空闲多久回收（秒）；HITL 暂停中的会话也会在超过该时间后回收
    RUNNER_TTL_SECONDS = 60 * 30

    def __init__(self):
        # session_id -> (runner, last_active_monotonic)
        self._runners: dict[str, tuple[MultiAgentRunner, float]] = {}
        self._active: MultiAgentRunner | None = None

    async def get_or_create(
        self,
        session_id: str,
        client: Anthropic,
        model: str,
        enable_data_quality: bool = False,
        user_id: str = "viewer",
    ) -> MultiAgentRunner:
        now = time.monotonic()
        await self._sweep(now)
        entry = self._runners.get(session_id)
        if entry is None:
            # 默认关闭 DQ（首答更快）；前端可按请求打开
            runner = await MultiAgentRunner.create(
                client,
                model=model,
                enable_data_quality=enable_data_quality,
                thread_id=f"web-{session_id}",
            )
            conversation = ConversationManager(client, session_id=session_id)
            conversation.load()
            runner._session_id = session_id
            runner._conversation = conversation
            self._runners[session_id] = (runner, now)
        else:
            runner = entry[0]
            # 同一会话内切换 DQ：打开时重置时效，保证下一次会扫
            if enable_data_quality and not runner.enable_data_quality:
                runner._dq_done = False
                runner._dq_time = 0.0
            runner.enable_data_quality = enable_data_quality
            if getattr(runner, "_conversation", None) is None:
                conversation = ConversationManager(client, session_id=session_id)
                conversation.load()
                runner._conversation = conversation
            runner._session_id = session_id
            self._runners[session_id] = (runner, now)
        runner._web_user_id = user_id  # StreamingRunner 执行时绑定 RBAC
        self._active = runner
        return runner

    def get(self, session_id: str) -> MultiAgentRunner | None:
        """按 session_id 取 runner（HITL resume 用），没有则 None。"""
        entry = self._runners.get(session_id)
        return entry[0] if entry else None

    def touch(self, session_id: str) -> None:
        """更新会话活跃时间（避免被 TTL 误回收）。"""
        entry = self._runners.get(session_id)
        if entry is not None:
            self._runners[session_id] = (entry[0], time.monotonic())

    def get_active(self) -> MultiAgentRunner | None:
        return self._active

    async def _sweep(self, now: float | None = None) -> None:
        """关闭并移除空闲超过 TTL 的 runner。"""
        now = now or time.monotonic()
        expired = [
            sid for sid, (_, ts) in self._runners.items()
            if now - ts > self.RUNNER_TTL_SECONDS
        ]
        for sid in expired:
            runner, _ = self._runners.pop(sid)
            try:
                await runner.aclose()
            except Exception:
                pass

    async def remove(self, session_id: str) -> None:
        """移除并关闭指定会话的 runner（客户端断连/会话结束时调用）。"""
        entry = self._runners.pop(session_id, None)
        if entry is not None:
            try:
                await entry[0].aclose()
            except Exception:
                pass

    async def close_all(self):
        for runner, _ in self._runners.values():
            await runner.aclose()
        self._runners.clear()
        self._active = None


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

    def _short_term_summary(self) -> str:
        conv = getattr(self.runner, "_conversation", None)
        if conv is None:
            return ""
        try:
            return conv.build_context() or ""
        except Exception:
            return ""

    async def _commit_short_term(self, query: str, answer: str, trace_id: str = "") -> None:
        """写入会话列表（Memory UI）+ ConversationManager 滑动窗口（下一轮注入）。"""
        sid = getattr(self.runner, "_session_id", None) or "default"
        try:
            from server.endpoints.sessions import record_message
            await record_message(sid, "user", query or "", trace_id=trace_id)
            await record_message(sid, "assistant", answer or "", trace_id=trace_id)
        except Exception:
            pass
        conv = getattr(self.runner, "_conversation", None)
        if conv is not None:
            try:
                await conv.add_message({"role": "user", "content": query or ""})
                await conv.add_message({"role": "assistant", "content": answer or ""})
                conv.save()
            except Exception:
                pass
        try:
            from harness.memory.preturn_recall import remember_turn
            await remember_turn(
                query or "",
                answer or "",
                session_id=str(sid),
                client=getattr(self.runner, "client", None),
            )
        except Exception:
            pass

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
        token = set_request_user(getattr(self.runner, "_web_user_id", None))
        try:
            await self._run_streaming_impl(query, queue)
        finally:
            reset_request_user(token)

    async def _run_streaming_impl(self, query: str, queue: asyncio.Queue) -> None:
        t_total = time.time()

        # ── Preprocessing ───────────────────────────────────────
        await queue.put(("step_start", SSEEvent.step_start("preprocessing", "输入护栏 + 记忆检查")))

        passed, reason = guard_input(query)
        if not passed:
            await queue.put(("step_end", SSEEvent.step_end("preprocessing", "护栏拦截", time.time() - t_total)))
            await queue.put(("error", SSEEvent.error(reason, "guard_input")))
            return

        await queue.put(("step_end", SSEEvent.step_end("preprocessing", "通过", time.time() - t_total)))
        self.runner._last_query = query

        # ── 表列举快路径：有哪些表 → 本地列举；SQL / Hive 分开答 ──
        import re as _re
        _list_meta = _re.search(
            r"(有哪些表|哪些表|列出.{0,6}表|表有哪些|list\s+tables|show\s+tables)",
            query,
            _re.I,
        )
        if _list_meta:
            q_low = query.lower()
            ask_hive = any(m in q_low for m in ("hive", "hue", "impala", "hql"))
            ask_sql = bool(_re.search(r"(?<![a-z])sql(?![a-z])|sqlite|关系(?:数据)?库", q_low))
            # 未点名引擎时两者都给，避免业务表和 Hive 表混成一锅
            if not ask_hive and not ask_sql:
                ask_hive = ask_sql = True

            t_sql = time.time()
            task_label = (
                "按引擎列出表" if (ask_sql and ask_hive)
                else ("列出 Olist 数仓表" if ask_hive else "列出 SQL 表")
            )
            await queue.put(("step_start", SSEEvent.step_start("sql", task_label)))
            try:
                from harness.tools.schema import HIVE_SIM_TABLES, list_tables
                from harness.tools.warehouse import list_warehouse_tables

                parts: list[str] = []
                plan = []
                if ask_sql:
                    payload = list_tables()
                    if payload.get("error"):
                        parts.append(str(payload.get("message") or "无权列出 SQL 表"))
                    else:
                        hive_set = set(HIVE_SIM_TABLES)
                        sql_tables = [t for t in (payload.get("tables") or []) if t not in hive_set]
                        body = "\n".join(f"- {t}" for t in sql_tables) if sql_tables else "- （无）"
                        parts.append(f"【SQLite / SQL 业务表】\n{body}")
                    plan.append({"agent": "sql", "task": "列出 SQL 表"})
                if ask_hive:
                    hive_payload = list_warehouse_tables()
                    if hive_payload.get("error"):
                        parts.append(str(hive_payload.get("message") or "无权列出 Hive 表"))
                    else:
                        hive_tables = hive_payload.get("tables") or []
                        body = "\n".join(f"- {t}" for t in hive_tables) if hive_tables else "- （无）"
                        parts.append(f"【Olist Hive/warehouse 表】\n{body}")
                    plan.append({"agent": "hive", "task": "列出 Hive 表"})
                answer = "\n\n".join(parts)
            except Exception as e:
                answer = f"列出表失败：{e}"
                plan = [{"agent": "sql", "task": task_label}]
            await queue.put(("step_end", SSEEvent.step_end("sql", task_label, time.time() - t_sql)))
            await queue.put(("text_delta", SSEEvent.text_delta(answer, node="sql")))
            await queue.put((
                "done",
                SSEEvent.done(
                    total_elapsed=time.time() - t_total,
                    answer=answer,
                    plan=plan,
                ),
            ))
            await self._commit_short_term(query, answer)
            return

        # ── Inject event queue into config ──────────────────────
        if not hasattr(self.runner, '_current_config') or self.runner._current_config is None:
            self.runner._current_config = {
                "configurable": {
                    "thread_id": self.runner.thread_id,
                    "_client": self.runner.client,
                    "_model": self.runner.model,
                    "_trace": TraceContext(query, thread_id=getattr(self.runner, "thread_id", None)),
                    "_router_cache": self.runner.router_cache,
                    "_task_manager": self.runner.task_manager,
                }
            }

        config = self.runner._current_config
        config["configurable"]["_event_queue"] = queue

        # ── Pre-turn 长期记忆（对齐 CLI：Self-Query + 向量召回）──
        recalled_text = ""
        try:
            from harness.memory.preturn_recall import ensure_memory_stack, recall_for_turn
            ensure_memory_stack(self.runner.client)
            bundle = await recall_for_turn(query, client=self.runner.client)
            recalled_text = (
                f"{bundle.meta_hint}{bundle.text}".strip()
                if bundle.meta_hint else (bundle.text or "")
            )
            if recalled_text:
                print(f"   📚 web pre-turn recall ({bundle.source}): {len(bundle.memories)} hits")
        except Exception as e:
            print(f"   [memory] web pre-turn recall skipped: {e}")

        # ── Build state and invoke graph ────────────────────────
        state = {
            "query": query,
            "messages": [{"role": "user", "content": query}],
            "_recalled_memories": recalled_text,
            "_conversation_summary": self._short_term_summary(),
            "_inject_dq": self.runner._should_inject_dq(),
            "plan": [],
            "results": {},
            "final_answer": "",
            "next": "",
            "_stats": {},
            "_reflection_attempts": 0,
            "_replan_attempts": 0,
            "_replan_feedback": "",
            # 演示模式：开启置信度门 + Reflection（CLI 默认即开启；Web 原来为加速首答而跳过）
            "_skip_confidence": False,
            "_skip_reflection": False,
        }

        # 不再发笼统的 graph step——router / 各 Agent 会各自推 step_start/end，避免长静默
        try:
            result = await self.runner.graph.ainvoke(state, config=config)
        except GraphInterrupt:
            # 旧版 LangGraph：interrupt() 以异常冒泡；新版通常直接 return 带 __interrupt__
            result = None

        snapshot = await self.runner.graph.aget_state(config)
        interrupt_data = _interrupt_payload(result, snapshot)
        if interrupt_data is not None:
            # 暂停时也留一份 state，方便 resume / 调试
            if snapshot and getattr(snapshot, "values", None):
                self.runner._last_state = dict(snapshot.values)
            await queue.put(("interrupt", SSEEvent.interrupt(interrupt_data)))
            return

        if result is None:
            await queue.put(("error", SSEEvent.error("无法回答")))
            return

        # 防御：带 __interrupt__ 的半截 dict 不应当成功态往下走
        if isinstance(result, dict) and result.get("__interrupt__"):
            await queue.put(("error", SSEEvent.error("图已暂停但未解析到审批数据，请重试")))
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

        # 若图内未流式推送答案，这里补发 text_delta，避免前端干等到 done 才出字
        if answer:
            await queue.put(("text_delta", SSEEvent.text_delta(answer, node="analysis")))

        tid = trace.trace_id if trace else ""
        await queue.put((
            "done",
            SSEEvent.done(
                total_elapsed=time.time() - t_total,
                trace_id=tid,
                opik_trace_id=str(oid or (getattr(trace, "opik_trace_id", None) if trace else None) or ""),
                sql=exec_info.get("sql", ""),
                answer=answer,
                plan=exec_info.get("plan", []),
                charts=charts,
                stats=exec_info.get("stats", {}),
                tokens=int(exec_info.get("tokens", 0) or 0),
            ),
        ))
        await self._commit_short_term(query, answer, trace_id=tid)

    async def resume_streaming(self, approved: bool, queue: asyncio.Queue, clarified_query: str = "") -> None:
        """Resume after HITL pause, emitting remaining events to queue."""
        token = set_request_user(getattr(self.runner, "_web_user_id", None))
        try:
            await self._resume_streaming_impl(approved, queue, clarified_query)
        finally:
            reset_request_user(token)

    async def _resume_streaming_impl(self, approved: bool, queue: asyncio.Queue, clarified_query: str = "") -> None:
        t_total = time.time()

        config = self.runner._current_config
        config["configurable"]["_event_queue"] = queue

        await queue.put(("step_start", SSEEvent.step_start("graph", "审批后继续执行")))

        from langgraph.types import Command
        result = await self.runner.graph.ainvoke(
            Command(resume={"approved": approved, "clarified_query": clarified_query}),
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

        if answer:
            await queue.put(("text_delta", SSEEvent.text_delta(answer, node="analysis")))

        tid = trace.trace_id if trace else ""
        await queue.put((
            "done",
            SSEEvent.done(
                total_elapsed=time.time() - t_total,
                trace_id=tid,
                opik_trace_id=str(oid or (getattr(trace, "opik_trace_id", None) if trace else None) or ""),
                sql=exec_info.get("sql", ""),
                answer=answer,
                plan=exec_info.get("plan", []),
                charts=charts,
                stats=exec_info.get("stats", {}),
                tokens=int(exec_info.get("tokens", 0) or 0),
            ),
        ))
        query = getattr(self.runner, "_last_query", "") or ""
        await self._commit_short_term(query, answer, trace_id=tid)


def _extract_chart_data(result: dict) -> list[dict]:
    """Extract structured chart configs from agent tool results.

    Checks render_chart results for chart_config, or auto-generates
    simple chart configs from run_query data.
    """
    charts = []
    results = result.get("results", {})

    # Check Analysis Agent results for render_chart output
    analysis_result = results.get(AGENT_ANALYSIS, "")
    if isinstance(analysis_result, str) and "dashboard_path" in analysis_result:
        # render_chart was called — try to extract chart_config from its return
        pass  # config should be in the render_chart return value directly

    # Fallback: auto-generate chart from SQL query results
    sql_result_text = results.get(AGENT_SQL, "")
    if not charts and sql_result_text:
        charts.extend(_auto_chart_from_sql_result(sql_result_text))

    return charts


def _auto_chart_from_sql_result(text: str) -> list[dict]:
    """Try to auto-generate chart configs from a SQL agent result containing tabular data."""
    import json
    import re

    json_match = re.search(r'\{.*"rows"\s*:\s*\[.*?\].*\}', text, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(0))
            rows = data.get("rows", [])
            if rows and len(rows) > 1:
                return _rows_to_charts(rows)
        except (json.JSONDecodeError, KeyError):
            pass
    return []


def _rows_to_charts(rows: list[dict]) -> list[dict]:
    """DB rows → 前端 ChartConfig 列表（复用 analysis 的字段语义推断 + 图型推荐）。"""
    if not rows:
        return []

    from harness.tools.analysis import _infer_field_properties, _suggest_charts

    props = _infer_field_properties(rows)
    value_col = None
    label_col = None
    for col, p in props.items():
        if value_col is None and p["dtype"] == "number":
            value_col = col
        if label_col is None and p["dtype"] in ("string", "category", "date"):
            label_col = col

    if label_col is None or value_col is None:
        return []

    return _suggest_charts(props, rows, value_col, label_col, f"{value_col} by {label_col}")
