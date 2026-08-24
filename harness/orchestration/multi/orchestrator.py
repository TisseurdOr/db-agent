"""0023 多 Agent 编排: Router + DataQuality + SQL + Analysis via LangGraph.

用法:
    from harness.orchestration.multi.orchestrator import MultiAgentRunner
    # 必须用 create() 工厂方法（异步初始化 SQLite 连接）
    runner = await MultiAgentRunner.create(client, enable_data_quality=True)
    answer = await runner.run("对比华东和华南的销售趋势")
    # 首次调用自动注入 DataQuality，后续跳过
"""

import json
import os
import re
import time
from pathlib import Path

import aiosqlite
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import RunnableConfig, Command, interrupt
from langgraph.errors import GraphInterrupt
from langchain_core.messages import AIMessage
from anthropic import Anthropic

from harness.orchestration.multi.state import MultiAgentState
from harness.orchestration.multi.agents import (
    sql_agent, analysis_agent, strategy_agent,
    data_quality_agent, hbase_agent, hive_agent,
)
from harness.orchestration.multi.router import ROUTER_PROMPT, route_override
from harness.orchestration.multi.base import is_agent_timeout
from harness.constraints.guardrails import guard_input, guard_output
from harness.orchestration.multi.cache import RouterCache
from harness.orchestration.multi.task_system import TaskManager
from harness.constraints.confidence import (
    CONFIDENCE_PROMPT, parse_confidence_result, should_pause, format_confidence_report,
)
from harness.observation.llm import extract_text
from harness.constraints.retry import acall_with_retry
from harness.constraints.circuit_breaker import CircuitOpenError, DEGRADED_MESSAGE
from harness.observation.tracer import TraceContext, mask_sql
from harness.observation.opik_tracing import (
    wrap_langgraph,
    flush_opik,
    annotate_opik,
    get_current_opik_trace_id,
    capture_opik_trace_id_for_graph,
    opik_tag_route,
    opik_tag_guard,
    opik_tag_hitl,
    opik_tag_fewshot,
    opik_tag_reflection,
    opik_tag_task_board,
    opik_tag_sql,
)
from harness.observation.cost import estimate_tokens_cost

# Checkpointer 数据库路径。
# 图每执行完一个节点，自动把 state 写进这个 SQLite 文件。
# 同一 thread_id 的后续调用从这个文件恢复 state（messages 累积、results 保留）。
# 必须用 AsyncSqliteSaver：graph.ainvoke 走 async checkpoint API，同步版不兼容。
# 开发用 SQLite；生产可换 PostgresSaver。
CHECKPOINT_DB = Path(__file__).resolve().parents[3] / "db" / "agent_state.db"


def _fmt_time(seconds: float) -> str:
    """格式化耗时: <1s 显示 ms, >=1s 显示 s。"""
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    return f"{seconds:.1f}s"


async def _run_agent_with_timeout(agent, client, task, model, trace_span, agent_name: str, context: str = "", config: RunnableConfig | None = None) -> tuple[str, dict]:
    """包装 agent.run()：检测超时，打日志，返回 (result, usage)。

    如果 config 中注入了 _event_queue，emit step_start/step_end SSE 事件。
    """
    queue = config["configurable"].get("_event_queue") if config else None
    if queue:
        await queue.put(("step_start", {"type": "step_start", "node": agent_name.lower(), "task": task[:60], "timestamp": time.time()}))

    try:
        result, usage = await agent.run(client, task, context=context, model=model, verbose=True)
    except CircuitOpenError:
        # 熔断降级：模型服务不可用，节点返回可读文案而不是让整张图崩溃
        trace_span.error = "熔断降级：模型服务不可用"
        print("⚠️ 熔断降级：模型服务暂时不可用，请稍后重试")
        return DEGRADED_MESSAGE, {}

    if queue:
        elapsed = trace_span.elapsed if hasattr(trace_span, 'elapsed') else 0
        tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        await queue.put(("step_end", {"type": "step_end", "node": agent_name.lower(), "task": task[:60], "elapsed": round(elapsed, 3), "tokens": tokens}))

    if is_agent_timeout(result):
        trace_span.error = f"{agent_name} 超过最大轮数"
        print(f"⚠️ {agent_name} 超过最大轮数，结果不可用。请缩小查询范围后重试。")
        from harness.observation.alerts import send_alert
        send_alert(
            "Agent 超时",
            f"{agent_name} 超过最大轮数，结果不可用（可能陷入工具调用循环）。",
            level="warning",
            tags={"agent": agent_name},
        )
    return result, usage


MAX_REPLAN_ATTEMPTS = 1  # 失败重规划上限——1 次足够换思路，多了是烧 token 的死循环


def _maybe_replan(state: MultiAgentState, results: dict, agent_name: str, result: str) -> dict | None:
    """失败重规划：Agent 超轮数失败且额度未用完时，带失败反馈回 Router 重排计划。

    返回 None 表示无需重规划（正常结果，或额度已用完走原路径——
    Analysis 的超时警告兜底仍会提示用户）。

    重规划时把失败 Agent 的结果从 results 里删掉：
    _next_step 用 results 的 key 判断"已执行"，不删的话新 plan 里
    同名 Agent 会被跳过，重规划等于空转。
    """
    if not is_agent_timeout(result):
        return None
    attempts = state.get("_replan_attempts", 0)
    if attempts >= MAX_REPLAN_ATTEMPTS:
        return None

    print(f"🔄 {agent_name} Agent 失败，带反馈回 Router 重规划 (第{attempts + 1}次)")
    cleaned = {k: v for k, v in results.items() if k != agent_name}
    feedback = (
        f"上一轮计划中 {agent_name} Agent 执行失败：超过最大工具调用轮数仍未完成任务。"
        f"请重新规划：把任务拆小、简化任务描述，或改派更合适的 Agent。"
        f"不要原样重复上一轮的 plan。"
    )
    return {
        "results": cleaned,
        "next": "router",
        "_replan_attempts": attempts + 1,
        "_replan_feedback": feedback,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 节点函数
#
# 每个节点 = 图里的一个执行单元，接收 state 返回部分更新。
#
# 为什么用 configurable 而不是 state 传 client/model：
#   Anthropic SDK 的 client 对象不能 JSON 序列化。如果放进 state，
#   Checkpointer 写盘时会崩（TypeError: Type is not msgpack serializable）。
#   configurable 是 LangGraph 专门留给"不可序列化对象"的通道——
#   它随每次调用注入节点，但不被 Checkpointer 持久化。
#   runner.run() 负责把 client 和 model 塞进 configurable。
# ═══════════════════════════════════════════════════════════════════════════════


def _annotate_route(route_source: str, plan: list, router_cache=None) -> None:
    """Tag Opik with router source + plan agent chain."""
    plan_agents = "→".join(s.get("agent", "") for s in (plan or []) if s.get("agent"))
    extra = {"plan_agents": plan_agents}
    if route_source == "cache" and router_cache is not None:
        try:
            extra["cache_hit_rate"] = router_cache.hit_rate
        except Exception:
            pass
    opik_tag_route(route_source, **extra)


async def node_router(state: MultiAgentState, config: RunnableConfig) -> dict:
    """Router: 分析用户 query，输出 JSON 执行计划。

    从 state["messages"] 取最近 6 条对话历史，和当前 query 一起发给 LLM。
    这样 Router 能识别"刚才问了什么"等元问题——
    看到历史里上一轮问了"有哪些表"，就知道这不是数据查询。
    """
    trace = config["configurable"].get("_trace") or TraceContext(state.get("query", ""))
    span = trace.start_span("router", "分析意图")

    client = config["configurable"]["_client"]
    model = config["configurable"].get("_model", os.getenv("ANTHROPIC_MODEL", "deepseek-chat"))
    router_cache = config["configurable"].get("_router_cache")

    # 失败重规划路径：跳过硬规则和缓存——两者都会原样复现失败的 plan，
    # 必须走 LLM 并把失败反馈喂进去，才可能得到不同的计划。
    replan_feedback = state.get("_replan_feedback", "")

    # 硬规则优先：闲聊 / 元问题 / 纯制度查询不依赖 LLM（也避免脏缓存）
    prev_agents = [s["agent"] for s in state.get("plan", [])] if state.get("plan") else []
    t0 = time.time()
    override = None if replan_feedback else route_override(state["query"], prev_agents=prev_agents)
    route_latency = time.time() - t0
    router_usage = {"input_tokens": 0, "output_tokens": 0, "turns": 0}
    cached_plan = None
    route_source_fallback = False
    plan_data = {}  # 硬规则路径不经 LLM，后续读 confidence 前必须有默认值

    if override is not None:
        plan = override
        span.task = "硬规则覆盖" if plan else "无需数据查询"
        router_usage["elapsed"] = route_latency
        if not plan:
            trace.finish_span(span, router_usage)
            print(trace.print_progress(span))
            _annotate_route("rule", [], router_cache)
            return {"plan": [], "next": "done", "_stats": {"elapsed": route_latency, "nodes": ["router(硬规则)"]}}
    else:
        # 查缓存：同样 query 之前解析过，直接复用 plan，省一次 LLM 调用（~250t）
        # 重规划时不读缓存——缓存里存的正是刚失败的 plan
        cached_plan = router_cache.get(state["query"]) if (router_cache and not replan_feedback) else None

        if cached_plan is not None:
            plan_data = {"plan": cached_plan}
            span.task = f"缓存命中 ({router_cache.hit_rate})"
        else:
            # 从 state["messages"] 取最近 6 条，转成 Anthropic 格式的对话
            recent = [m for m in state.get("messages", [])[-6:]]
            router_msgs = []
            for m in recent:
                role = getattr(m, "type", None)
                content = getattr(m, "content", "")
                if role == "human":
                    router_msgs.append({"role": "user", "content": str(content)[:300]})
                elif role == "ai":
                    router_msgs.append({"role": "assistant", "content": str(content)[:300]})
            if replan_feedback:
                router_msgs.append({
                    "role": "user",
                    "content": f"[重规划反馈]\n{replan_feedback}\n\n原问题: {state['query']}",
                })
            else:
                router_msgs.append({"role": "user", "content": state["query"]})

            t_llm = time.time()
            resp = await acall_with_retry(
                client.messages.create,
                model=model,
                max_tokens=300,
                system=ROUTER_PROMPT,
                messages=router_msgs,
            )
            llm_latency = time.time() - t_llm
            router_usage = {"input_tokens": 0, "output_tokens": 0, "turns": 1, "elapsed": llm_latency}
            if hasattr(resp, "usage") and resp.usage:
                router_usage["input_tokens"] = resp.usage.input_tokens or 0
                router_usage["output_tokens"] = resp.usage.output_tokens or 0

            text = extract_text(resp, context="router")
            try:
                plan_data = json.loads(text) if text else {}
            except json.JSONDecodeError:
                plan_data = {"plan": [{"agent": "sql", "task": state["query"]}]}

        plan = plan_data.get("plan", [])
        if not plan:
            # 空 plan：真闲聊就结束；否则兜底 sql（避免空白回复）
            # 注意：不要用 len>4 —— 「你好，你能做什么」长度很长但仍是闲聊
            query_text = state["query"].strip()
            q_low = query_text.lower()
            is_chitchat = any(m in q_low for m in (
                "你好", "您好", "hi", "hello", "你能做什么", "你会什么",
            )) or bool(re.search(r"你.{0,4}是谁", query_text))
            if is_chitchat:
                span.task = "无需数据查询"
                trace.finish_span(span, router_usage)
                print(trace.print_progress(span))
                _annotate_route("llm", [], router_cache)
                return {"plan": [], "next": "done"}
            plan = [{"agent": "sql", "task": query_text}]
            span.task = "空 plan → 兜底 sql"
            route_source_fallback = True

    # 缓存写入：仅 LLM 路径；硬规则不写缓存（避免污染）；
    # 重规划出的 plan 也不写——它是针对本次失败的补救计划，不是该 query 的通用答案
    if override is None and cached_plan is None and router_cache is not None and plan and not replan_feedback:
        router_cache.set(state["query"], plan)

    # DataQuality 首次注入：在 plan 最前面插入 DQ 检查，先扫库再查数。
    if state.get("_inject_dq") and not any(s["agent"] == "data_quality" for s in plan):
        plan.insert(0, {
            "agent": "data_quality",
            "task": "检查数据库整体数据质量：表行数、日期连续性、NULL比例、异常值。输出事实报告，不做业务判断。",
        })
    plan_names = " → ".join(s["agent"] for s in plan)
    span.task = plan_names
    trace.finish_span(span, router_usage)
    print(trace.print_progress(span))
    # 打印路由方式 + 耗时
    if override is not None:
        route_source = "rule"
        print(f"   ⚡ 硬规则路由 ({route_latency*1000:.1f}ms)")
    elif cached_plan is not None:
        route_source = "cache"
        print(f"   💾 缓存命中 ({router_cache.hit_rate})")
    elif route_source_fallback:
        route_source = "fallback_sql"
        print(f"   🤖 LLM 路由 ({router_usage.get('elapsed', 0):.1f}s · {router_usage.get('input_tokens', 0)}+{router_usage.get('output_tokens', 0)}t) [兜底 sql]")
    else:
        route_source = "llm"
        print(f"   🤖 LLM 路由 ({router_usage.get('elapsed', 0):.1f}s · {router_usage.get('input_tokens', 0)}+{router_usage.get('output_tokens', 0)}t)")
    _annotate_route(route_source, plan, router_cache)
    stats = {"elapsed": router_usage.get("elapsed", route_latency), "nodes": ["router"]}

    # 检测模糊查询：Router 返回了 plan 但置信度标记为 low
    if plan_data.get("confidence") == "low" and not override:
        return {"plan": plan, "next": "clarify", "_stats": stats, "_replan_feedback": ""}

    # s12：把 plan 落成 .tasks/ 任务图（先计划再执行的磁盘可见版）
    task_manager = config["configurable"].get("_task_manager")
    if task_manager is not None and plan:
        task_manager.materialize_from_plan(plan, query=state.get("query", ""))

    # _replan_feedback 用完即清——留着会让下一轮 Router 误以为又失败了
    return {"plan": plan, "next": plan[0]["agent"], "_stats": stats, "_replan_feedback": ""}


def _finish_agent_task(config: RunnableConfig, agent: str, failed: bool = False) -> None:
    """Agent 节点结束后推进 Task board——失败标 ✗，成功标 ✓ 并 claim 下一个。"""
    task_manager = config.get("configurable", {}).get("_task_manager")
    if task_manager is None:
        return
    if failed:
        on_failed = getattr(task_manager, "on_agent_failed", None)
        if callable(on_failed):
            on_failed(agent)
        else:
            # 兼容：旧 TaskManager 无 on_agent_failed 时退化为 finished(失败不 claim 下一跳仍走 finished 逻辑需自行扩展)
            pass
    else:
        task_manager.on_agent_finished(agent)

    run_ids = getattr(task_manager, "_run_ids", None) or []
    if run_ids:
        board = []
        for tid in run_ids:
            t = task_manager.get(tid)
            if t:
                board.append({"agent": t.agent, "status": t.status, "id": t.id})
        if board:
            opik_tag_task_board(board)


CLARIFY_PROMPT = """用户问了一个模糊的问题："{query}"

当前数据库中有以下表和相关上下文。请生成 2-3 个简短、具体的澄清问题，帮助理解用户的真实意图。
问题应该引导用户提供更具体的信息（时间范围、实体名、指标、对比维度等）。

只输出问题列表，每行一个，以 "- " 开头。不要其他文字。"""


async def node_clarify(state: MultiAgentState, config: RunnableConfig) -> dict:
    """AskHuman 澄清节点：检测模糊查询并主动提问。

    当 Router 无法确定用户意图时触发。
    用 LLM 生成 2-3 个针对性问题，通过 interrupt 暂停等待用户回复。
    """
    trace = config["configurable"].get("_trace") or TraceContext(state.get("query", ""))
    span = trace.start_span("clarify", "澄清模糊问题")

    client = config["configurable"]["_client"]
    model = config["configurable"].get("_model", os.getenv("ANTHROPIC_MODEL", "deepseek-chat"))

    prompt = CLARIFY_PROMPT.format(query=state["query"])
    resp = await acall_with_retry(
        client.messages.create,
        model=model,
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    text = extract_text(resp, context="clarify")
    usage = {"input_tokens": 0, "output_tokens": 0, "turns": 1}
    if hasattr(resp, "usage") and resp.usage:
        usage["input_tokens"] = resp.usage.input_tokens or 0
        usage["output_tokens"] = resp.usage.output_tokens or 0

    trace.finish_span(span, usage)
    print(f"❓ Clarify: 需要澄清「{state['query'][:40]}」")

    # HITL: 暂停并展示澄清问题
    opik_tag_hitl("pause", hitl_type="clarify")
    response = interrupt({
        "type": "clarify",
        "query": state["query"],
        "questions": text.strip(),
        "message": "这个问题比较模糊，请帮我确认一下：",
    })

    # 用户回复后 resume —— 用澄清后的 query 重新路由
    clarified = response.get("clarified_query", "") if isinstance(response, dict) else ""
    if clarified:
        print(f"   💬 用户澄清: {clarified[:80]}")
        return {"query": clarified, "next": "router"}

    return {"next": "router"}


async def node_data_quality(state: MultiAgentState, config: RunnableConfig) -> dict:
    """DataQuality Agent: 扫一遍数据质量（NULL 比例、日期连续性、异常值）。"""
    trace = config["configurable"].get("_trace") or TraceContext(state.get("query", ""))
    client = config["configurable"]["_client"]
    model = config["configurable"].get("_model", os.getenv("ANTHROPIC_MODEL", "deepseek-chat"))
    task = next(s["task"] for s in state["plan"] if s["agent"] == "data_quality")
    span = trace.start_span("data_quality", task[:60])
    print(f"⏳ DataQuality: {task[:60]}...")
    result, usage = await _run_agent_with_timeout(data_quality_agent, client, task, model, span, "DataQuality", config=config)
    trace.finish_span(span, usage, error=span.error)
    print(f"✅ DataQuality ({_fmt_time(span.elapsed)} · {span.total_tokens}t · {usage['turns']}轮)")
    _finish_agent_task(config, "data_quality", failed=is_agent_timeout(result))
    results = {**state.get("results", {}), "data_quality": result}
    replan = _maybe_replan(state, results, "data_quality", result)
    if replan:
        return replan
    return _next_step(state, results, "data_quality")


async def node_sql(state: MultiAgentState, config: RunnableConfig) -> dict:
    """SQL Agent: 查数据库（list_tables / describe_table / run_query）。"""
    trace = config["configurable"].get("_trace") or TraceContext(state.get("query", ""))
    client = config["configurable"]["_client"]
    model = config["configurable"].get("_model", os.getenv("ANTHROPIC_MODEL", "deepseek-chat"))
    task = next(s["task"] for s in state["plan"] if s["agent"] == "sql")
    span = trace.start_span("sql", task[:60])
    print(f"⏳ SQL Agent: {task[:60]}...")

    # 清掉上一轮残留的成功 SQL，避免超时失败时误回流探索性查询
    from harness.tools.query import pop_last_successful_sql
    pop_last_successful_sql()

    # 检索式 few-shot（Vanna 模式）：召回相似问题的已验证 SQL 注入上下文。
    # 用原始 query 而非 Router 改写后的 task 检索——样例库存的是用户口语问法。
    from harness.context.sql_examples import get_sql_fewshot
    fewshot = get_sql_fewshot(state.get("query", "") or task)
    fewshot_hits = 0
    if fewshot:
        fewshot_hits = fewshot.count("\nQ: ")
        print(f"   📚 few-shot: 命中 {fewshot_hits} 条相似 SQL 样例")
    opik_tag_fewshot(fewshot_hits)

    result, usage = await _run_agent_with_timeout(sql_agent, client, task, model, span, "SQL", context=fewshot, config=config)
    trace.finish_span(span, usage, error=span.error)
    print(f"✅ SQL Agent ({_fmt_time(span.elapsed)} · {span.total_tokens}t · {usage['turns']}轮)")
    _finish_agent_task(config, "sql", failed=is_agent_timeout(result))

    # 自学习回流（第 3 项）：优先用工具层捕获的成功 SQL（比从自然语言抽更准）
    if not is_agent_timeout(result):
        from harness.memory.feedback import learn_from_success
        learned_sql = pop_last_successful_sql()
        if learned_sql:
            opik_tag_sql(mask_sql(learned_sql))
        learn_from_success(
            state.get("query", "") or task,
            result,
            sql=learned_sql,
            source="auto",
        )

    results = {**state.get("results", {}), "sql": result}
    replan = _maybe_replan(state, results, "sql", result)
    if replan:
        return replan
    # SQL 生成后先过置信度门，再决定下一步
    return _next_step_after_sql(state, results)


async def node_confidence_gate(state: MultiAgentState, config: RunnableConfig) -> dict:
    """置信度门：LLM 自评 SQL 质量，低分触发 HITL 审批。

    在 node_sql 之后、node_analysis 之前执行。
    从 sql agent 结果中提取 SQL，让 LLM 按 6 项标准打分。
    置信度 < 0.7 时暂停并请用户确认。
    """
    trace = config["configurable"].get("_trace") or TraceContext(state.get("query", ""))
    span = trace.start_span("confidence_gate", "SQL 置信度评估")

    sql_result = state.get("results", {}).get("sql", "")
    if not sql_result:
        trace.finish_span(span, {"input_tokens": 0, "output_tokens": 0, "turns": 0})
        return _next_step(state, state["results"], "sql")

    # 提取 SQL 语句
    sql_match = re.search(r'(SELECT|WITH)\s.+?(?:;|$)', sql_result, re.IGNORECASE | re.DOTALL)
    sql = sql_match.group(0).strip() if sql_match else sql_result[:500]

    client = config["configurable"]["_client"]
    model = config["configurable"].get("_model", os.getenv("ANTHROPIC_MODEL", "deepseek-chat"))

    prompt = CONFIDENCE_PROMPT.format(
        query=state["query"],
        schema_context=sql_result[:2000],  # sql agent 结果中包含表结构信息
        sql=sql,
    )

    resp = await acall_with_retry(
        client.messages.create,
        model=model,
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    text = extract_text(resp, context="confidence")
    result_data = parse_confidence_result(text)
    usage = {"input_tokens": 0, "output_tokens": 0, "turns": 1}
    if hasattr(resp, "usage") and resp.usage:
        usage["input_tokens"] = resp.usage.input_tokens or 0
        usage["output_tokens"] = resp.usage.output_tokens or 0

    trace.finish_span(span, usage)

    confidence = result_data["confidence"]
    report = format_confidence_report(result_data)
    print(f"🔍 置信度门: {confidence:.2f} ({'✅ 通过' if not should_pause(confidence) else '⚠️ 需确认'})")
    print(report)

    if should_pause(confidence):
        # HITL: 暂停并展示 SQL + 置信度报告给用户确认
        opik_tag_hitl(
            "pause",
            hitl_type="confidence",
            confidence=float(confidence),
            masked_sql=mask_sql(sql) if sql else "",
        )
        response = interrupt({
            "type": "confidence_gate",
            "sql": sql,
            "confidence": confidence,
            "report": report,
            "message": f"SQL 置信度 {confidence:.2f}，低于阈值 0.7，请确认是否继续执行。",
        })
        # 读取用户审批结果
        approved = response.get("approved", True) if isinstance(response, dict) else True
        if not approved:
            print(f"🚫 用户拒绝了低置信度 SQL")
            return {
                "final_answer": "SQL 执行已被用户取消（置信度过低）。",
                "messages": [AIMessage(content="SQL 执行已被用户取消（置信度过低）。")],
                "next": "done",
            }

    # 置信度通过，继续下一步
    return _next_step(state, state["results"], "sql")


def _next_step_after_sql(state: MultiAgentState, results: dict) -> dict:
    """SQL 节点专用调度：先跑完 plan 里其他 Agent，再决定是否进置信度门。

    sql + hive 这类多引擎 plan 必须先把 hive/hbase 跑完，
    不能 sql 一结束就进 confidence_gate 把后续 Agent 堵死。
    """
    plan = state["plan"]
    executed = set(results.keys())
    pending = [s for s in plan if s["agent"] not in executed]
    if pending:
        return {"results": results, "next": pending[0]["agent"]}

    plan_agents = {s["agent"] for s in plan}
    # 只有还要走 analysis 时才过置信度门
    if "analysis" in plan_agents:
        return {"results": results, "next": "confidence_gate"}

    # 单 sql 或 sql+hive/hbase 已全部完成：仍需走 analysis（呈现）+ reflection（审查）
    return {"results": results, "next": "analysis"}


async def node_strategy(state: MultiAgentState, config: RunnableConfig) -> dict:
    """Strategy Agent: 查公司制度文档（search_knowledge_base）。"""
    client = config["configurable"]["_client"]
    trace = config["configurable"].get("_trace") or TraceContext(state.get("query", ""))
    model = config["configurable"].get("_model", os.getenv("ANTHROPIC_MODEL", "deepseek-chat"))
    task = next(s["task"] for s in state["plan"] if s["agent"] == "strategy")
    span = trace.start_span("strategy", task[:60])
    print(f"⏳ Strategy Agent: {task[:60]}...")
    result, usage = await _run_agent_with_timeout(strategy_agent, client, task, model, span, "Strategy", config=config)
    trace.finish_span(span, usage, error=span.error)
    print(f"✅ Strategy Agent ({_fmt_time(span.elapsed)} · {span.total_tokens}t · {usage['turns']}轮)")
    _finish_agent_task(config, "strategy", failed=is_agent_timeout(result))
    results = {**state.get("results", {}), "strategy": result}
    replan = _maybe_replan(state, results, "strategy", result)
    if replan:
        return replan
    return _next_step(state, results, "strategy")


async def _run_agent_node(state, config, agent, agent_name, result_key):
    """通用 Agent 节点：取 task → 执行 → 写 results。"""
    client = config["configurable"]["_client"]
    trace = config["configurable"].get("_trace") or TraceContext(state.get("query", ""))
    model = config["configurable"].get("_model", os.getenv("ANTHROPIC_MODEL", "deepseek-chat"))
    task = next(s["task"] for s in state["plan"] if s["agent"] == agent_name)
    span = trace.start_span(agent_name, task[:60])
    print(f"⏳ {agent_name.upper()} Agent: {task[:60]}...")
    result, usage = await _run_agent_with_timeout(agent, client, task, model, span, agent_name.upper(), config=config)
    trace.finish_span(span, usage, error=span.error)
    print(f"✅ {agent_name.upper()} Agent ({_fmt_time(span.elapsed)} · {span.total_tokens}t · {usage['turns']}轮)")
    _finish_agent_task(config, agent_name, failed=is_agent_timeout(result))
    results = {**state.get("results", {}), result_key: result}
    replan = _maybe_replan(state, results, agent_name, result)
    if replan:
        return replan
    return _next_step(state, results, agent_name)


async def node_hbase(state: MultiAgentState, config: RunnableConfig) -> dict:
    """HBase Agent: 生成 HBase Shell 命令。"""
    return await _run_agent_node(state, config, hbase_agent, "hbase", "hbase")


async def node_hive(state: MultiAgentState, config: RunnableConfig) -> dict:
    """Hive Agent: 生成 Hive/Impala 查询。"""
    return await _run_agent_node(state, config, hive_agent, "hive", "hive")


async def node_analysis(state: MultiAgentState, config: RunnableConfig) -> dict:
    """Analysis Agent: 综合所有中间结果 + 记忆，生成最终回答。

    注入 Analysis Agent 上下文的四层信息（按优先级）：
    1. 早期摘要（_conversation_summary）—— ConversationManager 压缩的超窗口对话
    2. 向量记忆（_recalled_memories）—— 跨会话的长期记忆
    3. 最近对话（messages）—— Checkpointer 累积的本轮消息
    4. 中间结果（results）—— 上游 Agent 的执行输出
    """
    trace = config["configurable"].get("_trace") or TraceContext(state.get("query", ""))
    span = trace.start_span("analysis", "综合分析")

    client = config["configurable"]["_client"]
    model = config["configurable"].get("_model", os.getenv("ANTHROPIC_MODEL", "deepseek-chat"))
    context_parts = []
    # Layer 1: 早期对话摘要——ConversationManager 将超窗口消息压缩为摘要
    if state.get("_conversation_summary"):
        context_parts.insert(0, f"[早期对话摘要]\n{state['_conversation_summary']}")
    # Layer 2: 向量记忆——跨会话语义召回
    if state.get("_recalled_memories"):
        context_parts.insert(0, f"[历史相关对话]\n{state['_recalled_memories']}")
    # Layer 3: 最近对话——Checkpointer 累积的 messages
    recent = []
    for m in state.get("messages", [])[-6:]:
        role = "用户" if getattr(m, "type", "") == "human" else "助手"
        content = str(getattr(m, "content", ""))[:500]
        if content:
            recent.append(f"{role}: {content}")
    if recent:
        context_parts.insert(0, f"[最近对话]\n" + "\n".join(recent))
    # Layer 4: Reflection 反馈（如果有）——放到最前面，让 Analysis 优先看到
    if state.get("results", {}).get("_reflection_feedback"):
        context_parts.insert(0, f"[🔴 重写指令 —— 上次回答被 Reflection 退回，请根据以下建议改进]\n{state['results']['_reflection_feedback']}")

    # Layer 5: 上游 Agent 的中间结果
    for name, text in state.get("results", {}).items():
        if name == "_reflection_feedback":
            continue  # 已单独处理
        context_parts.append(f"[{name} Agent 结果]\n{text}")

    # 检测上游结果是否有超时——如果有，在 context 里加提示
    for name, text in state.get("results", {}).items():
        if is_agent_timeout(text):
            context_parts.append(f"[警告] 上游 {name} Agent 超过最大轮数未完成，其结果为无效文本，请忽略并告知用户重试。")

    print(f"⏳ Analysis Agent: 综合分析中...")
    result, usage = await analysis_agent.run(
        client,
        task=state["query"],
        context="\n\n".join(context_parts),
        model=model,
    )
    if is_agent_timeout(result):
        span.error = "Analysis 超过最大轮数"
        print(f"⚠️ Analysis 超过最大轮数")
    print(f"✅ Analysis Agent ({_fmt_time(span.elapsed)} · {span.total_tokens}t · {usage['turns']}轮)")

    # Layer 3 输出护栏：检查 PII 泄露、system prompt 泄露、异常输出
    passed, reason = guard_output(result)
    if not passed:
        print(f"🚫 输出护栏拦截: {reason}")
        opik_tag_guard("output", reason)
        # finish span before set blocked
        trace.finish_span(span, usage, error = reason)
        trace.set_blocked("output", reason)
        
        return {
            "final_answer": reason,
            "messages": [AIMessage(content=reason)],
        }
    # 处理超时情况，正常情况是NONE，超时才传参进span
    trace.finish_span(span, usage, error=span.error)
    _finish_agent_task(config, "analysis", failed=is_agent_timeout(result))

    # 把最终回答写回 messages —— Checkpointer 自动持久化，
    # 下一轮 Router 和 Analysis 就能从 messages 里看到这轮说了什么。
    return {
        "final_answer": result,
        "messages": [AIMessage(content=result)],
        "next": "reflection",  # 输出后走反思检查
    }


REFLECTION_PROMPT = """你是回答质量审查员。审查以下 Agent 输出，判断是否合格。

【用户问题】
{query}

【Agent 回答】
{answer}

【上游数据来源】
{context}

审核标准（每项 pass/fail）：
1. 完整性：回答是否直接、完整地回应用户问题？（不是岔开话题或只给半截答案）
2. 真实性：有没有编造数据？（数字、日期、名称如果在 context 里找不到就是编造）
3. 可用性：用户读完能立刻用吗？（结论在前、具体数字、不模糊）

只输出 JSON：
{{"pass": true/false, "issues": ["问题1", "问题2"], "suggestion": "如何改进（如果 pass 为 false）"}}

如果 pass=false，suggestion 必须具体、可执行，让 Analysis Agent 能直接据此重写。"""


async def node_reflection(state: MultiAgentState, config: RunnableConfig) -> dict:
    """Reflection 反思节点：审查 Analysis 输出质量，不合格则退回重写。

    借鉴 Self-Refine 模式：
    1. LLM 自审回答质量（完整性/真实性/可用性）
    2. 不通过 → 把改进建议注入 context，退回 Analysis 重写
    3. 最多重试 2 次，避免死循环

    设计动机：
    "在 Analysis 输出后加 Reflection 节点——Self-Refine 模式。
    LLM 自审三个维度：完整性、真实性、可用性。不合格就把改进建议
    喂回 Analysis 重写。最多 2 轮，用 token 换质量。"
    """
    trace = config["configurable"].get("_trace") or TraceContext(state.get("query", ""))
    span = trace.start_span("reflection", "回答质量审查")

    answer = state.get("final_answer", "")
    if not answer:
        trace.finish_span(span, {"input_tokens": 0, "output_tokens": 0, "turns": 0})
        return {"next": "done"}

    attempts = state.get("_reflection_attempts", 0)

    client = config["configurable"]["_client"]
    model = config["configurable"].get("_model", os.getenv("ANTHROPIC_MODEL", "deepseek-chat"))

    # 拼接上游数据作为审查依据
    context = "\n".join(str(v)[:1000] for v in state.get("results", {}).values() if v)

    prompt = REFLECTION_PROMPT.format(
        query=state["query"],
        answer=answer[:2000],
        context=context[:2000],
    )

    resp = await acall_with_retry(
        client.messages.create,
        model=model,
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    text = extract_text(resp, context="reflection")
    usage = {"input_tokens": 0, "output_tokens": 0, "turns": 1}
    if hasattr(resp, "usage") and resp.usage:
        usage["input_tokens"] = resp.usage.input_tokens or 0
        usage["output_tokens"] = resp.usage.output_tokens or 0

    # 解析审查结果
    import json as _json
    try:
        match = re.search(r'\{[\s\S]*\}', text)
        review = _json.loads(match.group()) if match else {"pass": True, "issues": [], "suggestion": ""}
    except (_json.JSONDecodeError, KeyError):
        review = {"pass": True, "issues": [], "suggestion": ""}

    trace.finish_span(span, usage)

    issues = list(review.get("issues") or [])
    suggestion = str(review.get("suggestion") or "")

    if review.get("pass") or attempts >= 2:
        status = "✅ 通过" if review.get("pass") else f"⏭ 已达上限({attempts}次)"
        print(f"🔍 Reflection: {status}")
        opik_tag_reflection(
            passed=bool(review.get("pass")),
            attempts=attempts,
            issues=issues,
            suggestion=suggestion,
        )
        return {"next": "done", "_reflection_attempts": 0}

    # 不通过：退回重写
    print(f"🔍 Reflection: ❌ 不通过，退回重写 (第{attempts+1}次)")
    for issue in issues:
        print(f"   ⚠ {issue}")
    print(f"   💡 {suggestion}")
    opik_tag_reflection(
        passed=False,
        attempts=attempts + 1,
        issues=issues,
        suggestion=suggestion,
    )

    # 把改进建议注入 results，作为 Analysis 的 context
    results_with_feedback = {
        **state.get("results", {}),
        "_reflection_feedback": review.get("suggestion", "请改进回答质量"),
    }

    return {
        "results": results_with_feedback,
        "next": "analysis",
        "_reflection_attempts": attempts + 1,
    }


def _next_step(state: MultiAgentState, results: dict, current: str) -> dict:
    """调度核心：对比 plan 和已执行的 Agent，决定下一个节点。

    逻辑：
    1. plan 中还有没执行的 agent → 路由到下一个
    2. plan 里声明了 analysis 且还没跑 → 再去 analysis
    3. 否则直接用上游结果当 final_answer（不要无脑追加 analysis）
       —— 否则「销售部有多少员工」也会被分析师包装成「抱歉，我无法查库」
    """
    plan = state["plan"]
    executed = set(results.keys())               # 已打卡的 Agent 名
    pending = [s for s in plan if s["agent"] not in executed]  # 还没执行的

    if pending:
        return {"results": results, "next": pending[0]["agent"]}

    plan_agents = {s["agent"] for s in plan}
    if "analysis" in plan_agents and "analysis" not in executed:
        return {"results": results, "next": "analysis"}

    # plan 已跑完且不需要 analysis：
    # 多引擎结果要拼接（sql+hive），不能只取 sql 丢掉 hive
    non_empty = {k: v for k, v in results.items() if v}
    if len(non_empty) > 1:
        answer = "\n\n".join(f"【{k}】\n{v}" for k, v in non_empty.items())
    else:
        answer = (
            results.get("sql")
            or results.get("strategy")
            or results.get("data_quality")
            or results.get("hive")
            or results.get("hbase")
            or "\n\n".join(str(v) for v in results.values() if v)
        )
    return {"results": results, "next": "done", "final_answer": answer}


# ── 路由函数 ──

def edge_router(state: MultiAgentState) -> str:
    """条件边路由：读 state["next"]，决定下一站。

    返回的字符串必须是 targets dict 的 key（"sql"、"analysis"、"done" 等）。
    不能直接返回 END（"__end__"）——targets 里没有这个 key 会抛 KeyError。
    通过 targets["done"] → END 间接映射。
    """
    return state.get("next") or "done"


# ── 构建 Graph ──

def build_multi_agent_graph(checkpointer=None):
    """组装多 Agent 编排图。

    拓扑：
    __start__ → router → (条件边) → sql/strategy/data_quality → analysis → END

    所有非 analysis 节点都挂同一套条件边（edge_router + targets），
    执行完后由 _next_step 写 state["next"]，edge_router 读 next 做路由。

    checkpointer 参数：传入 AsyncSqliteSaver 等实例后，图每步自动存盘。
    """
    builder = StateGraph(MultiAgentState)

    builder.add_node("router", node_router)
    builder.add_node("clarify", node_clarify)
    builder.add_node("data_quality", node_data_quality)
    builder.add_node("sql", node_sql)
    builder.add_node("confidence_gate", node_confidence_gate)
    builder.add_node("strategy", node_strategy)
    builder.add_node("hbase", node_hbase)
    builder.add_node("hive", node_hive)
    builder.add_node("analysis", node_analysis)
    builder.add_node("reflection", node_reflection)

    builder.set_entry_point("router")

    # targets: edge_router 返回值 → LangGraph 节点名的映射
    targets = {
        "router": "router",  # 失败重规划：Agent 节点失败后回 Router 重排计划
        "clarify": "clarify",
        "data_quality": "data_quality",
        "sql": "sql",
        "strategy": "strategy",
        "hbase": "hbase",
        "hive": "hive",
        "analysis": "analysis",
        "confidence_gate": "confidence_gate",
        "reflection": "reflection",
        "done": END,
    }

    builder.add_conditional_edges("router", edge_router, targets)
    builder.add_conditional_edges("clarify", edge_router, targets)
    builder.add_conditional_edges("data_quality", edge_router, targets)
    builder.add_conditional_edges("sql", edge_router, targets)
    builder.add_conditional_edges("strategy", edge_router, targets)
    builder.add_conditional_edges("hbase", edge_router, targets)
    builder.add_conditional_edges("hive", edge_router, targets)
    builder.add_conditional_edges("confidence_gate", edge_router, targets)
    builder.add_conditional_edges("analysis", edge_router, targets)
    builder.add_conditional_edges("reflection", edge_router, targets)

    return builder.compile(checkpointer=checkpointer)


# ═══════════════════════════════════════════════════════════════════════════════
# 高层封装：给 main.py 用的 Runner
# ═══════════════════════════════════════════════════════════════════════════════

class MultiAgentRunner:
    """多 Agent 执行器：封装图的构造、Checkpointer 初始化、state 注入。

    必须用工厂方法构造（不能用 MultiAgentRunner(...)）：
        runner = await MultiAgentRunner.create(client, model="deepseek-chat")

    原因：AsyncSqliteSaver 需要 await 来初始化数据库连接和建表，
    __init__ 是同步的做不到。create() 是 async classmethod，可以 await。
    """

    def __init__(self, *args, **kwargs):
        raise TypeError(
            "请使用 `runner = await MultiAgentRunner.create(...)`，"
            "不要直接 MultiAgentRunner(...)。"
        )

    @classmethod
    async def create(
        cls,
        client: Anthropic,
        model: str = "deepseek-chat",
        enable_data_quality: bool = True,
        checkpoint_db: Path | str | None = None,
        thread_id: str = "default-session",
        checkpoint_redis_url: str | None = None,
        checkpoint_prefix: str | None = None,
    ) -> "MultiAgentRunner":
        """异步工厂方法：初始化 SQLite 连接 + Checkpointer + 编译图。

        Args:
            client: Anthropic SDK 客户端（不可序列化，走 configurable 注入）
            model: LLM 模型名
            enable_data_quality: 首次查询是否自动注入 DataQuality
            checkpoint_db: Checkpointer 数据库路径（默认 db/agent_state.db）
            thread_id: 会话标识——同一 thread_id 共享 messages 历史
            checkpoint_redis_url: 显式指定 Redis checkpoint URL（覆盖 REDIS_URL 环境变量）
            checkpoint_prefix: Redis checkpoint key 前缀（测试隔离用）
        """
        self = object.__new__(cls)
        self.client = client
        self.model = model
        self.enable_data_quality = enable_data_quality
        self._dq_done = False       # 首次查询后置 True，后续不再注入 DQ
        self._dq_time = 0.0         # 上次 DQ 执行时间戳（epoch 秒）
        self.router_cache = RouterCache(max_size=100)
        self.task_manager = TaskManager()
        self.thread_id = thread_id
        self._last_state = None  # 最近一次 graph 执行完成后的 state（供 UI 读取中间结果）

        # 状态外置（可选）：配置 REDIS_URL 后 checkpoint 存 Redis（多实例共享 + TTL 自动清理）。
        # 未配置 / Redis 不可用时自动降级回 SQLite——不阻断启动。
        self._redis_cm = None
        self.checkpointer = None
        redis_url = (checkpoint_redis_url or os.getenv("REDIS_URL", "")).strip()
        if redis_url:
            try:
                from langgraph.checkpoint.redis.aio import AsyncRedisSaver
                ttl_minutes = int(os.getenv("REDIS_CHECKPOINT_TTL_MINUTES", "1440"))
                saver_kwargs: dict = {"ttl": {"default_ttl": ttl_minutes}}  # 24h 自动过期，防无限膨胀
                if checkpoint_prefix:
                    saver_kwargs["checkpoint_prefix"] = checkpoint_prefix
                cm = AsyncRedisSaver.from_conn_string(redis_url, **saver_kwargs)
                self.checkpointer = await cm.__aenter__()
                await self.checkpointer.setup()
                self._redis_cm = cm
                self.checkpoint_db = redis_url
                print(f"📌 Checkpointer: Redis（{redis_url}，TTL {ttl_minutes} 分钟）")
            except Exception as e:
                print(f"⚠️ Redis Checkpointer 初始化失败（{type(e).__name__}: {e}），降级到 SQLite")
                self._redis_cm = None
                self.checkpointer = None

        if self.checkpointer is None:
            db_path = Path(checkpoint_db) if checkpoint_db else CHECKPOINT_DB
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = await aiosqlite.connect(str(db_path))
            self.checkpointer = AsyncSqliteSaver(self._conn)
            await self.checkpointer.setup()  # 建 checkpoints 表
            self.checkpoint_db = db_path

        self.graph = wrap_langgraph(
            build_multi_agent_graph(checkpointer=self.checkpointer)
        )
        return self

    def _should_inject_dq(self) -> bool:
        """DataQuality 时效控制：首次扫描后 1 小时内跳过。

        关闭开关（--no-dq）→ 永远 False。
        首次 DQ 执行后记录时间，1 小时内不再触发。
        超时后允许重新扫描（数据可能已变化）。
        """
        if not self.enable_data_quality:
            return False
        if self._dq_done:
            elapsed = time.time() - self._dq_time
            if elapsed < 3600:    # 1 小时内跳过
                return False
            self._dq_done = False  # 超时，允许重新扫
        self._dq_done = True
        self._dq_time = time.time()
        return True

    async def run(self, query: str, recalled_memories: str = "", conversation_summary: str = "") -> str:
        """执行一次多 Agent 查询，返回 final_answer 文本。

        如果遇到 HITL 审批中断，返回 {"__interrupt__": True, "data": {...}}。
        调用方检测到此标记后展示 SQL 给用户，确认后调 resume() 恢复执行。

        Args:
            query: 用户输入的自然语言问题
            recalled_memories: pre-turn 向量召回的记忆文本（main.py 预处理后传入）
            conversation_summary: ConversationManager 压缩的早期对话摘要（注入 node_analysis）
        """
        # 每个请求创建一个 TraceContext——跟着 configurable 在节点间流转
        trace = TraceContext(query)
        annotate_opik(metadata={
            "recalled_memories_present": bool(recalled_memories),
            "conversation_summary_present": bool(conversation_summary),
        })

        # Layer 1 输入护栏：在 LLM 调用前拦截恶意输入，零 token 成本
        passed, reason = guard_input(query)
        if not passed:
            print(f"🚫 输入护栏拦截: {reason}")
            opik_tag_guard("input", reason)
            trace.set_blocked("input", reason)
            trace.save()
            flush_opik()
            return reason

        self._current_config = {
            "configurable": {
                "thread_id": self.thread_id,
                "_client": self.client,
                "_model": self.model,
                "_trace": trace,
                "_router_cache": self.router_cache,
                "_task_manager": self.task_manager,
            }
        }
        state = {
            "query": query,
            "messages": [{"role": "user", "content": query}],
            "_recalled_memories": recalled_memories,
            "_conversation_summary": conversation_summary,
            "_inject_dq": self._should_inject_dq(),
            "plan": [],
            "results": {},
            "final_answer": "",
            "next": "",
            "_stats": {},
            "_reflection_attempts": 0,
            "_replan_attempts": 0,
            "_replan_feedback": "",
        }

        try:
            result = await self.graph.ainvoke(state, config=self._current_config)
        except GraphInterrupt:
            # 旧版 LangGraph: interrupt() 抛异常，graph 在敏感 SQL 处暂停
            result = None

        # 检查是否被 interrupt 暂停（新/旧版本兼容）
        snapshot = await self.graph.aget_state(self._current_config)
        if snapshot and snapshot.interrupts:
            # 暂停时也保存 state——这样 HITL 场景下调用方（如 eval_runner）
            # 能通过 get_execution_info() 读到 plan，不必去翻 trace 文件
            self._last_state = dict(snapshot.values or {})
            interrupt_data = snapshot.interrupts[0].value if snapshot.interrupts else {}
            hitl_type = interrupt_data.get("type") if isinstance(interrupt_data, dict) else None
            pause_meta = {"hitl_type": hitl_type} if hitl_type else {}
            if isinstance(interrupt_data, dict) and "confidence" in interrupt_data:
                pause_meta["confidence"] = interrupt_data.get("confidence")
            opik_tag_hitl("pause", **{k: v for k, v in pause_meta.items() if v is not None})
            # trace 暂不存盘——等 resume() 完成后一起写
            return {"__interrupt__": True, "data": interrupt_data}

        if result is None:
            return "抱歉，无法回答。"

        # 保存 state 供 UI 读取中间结果
        self._last_state = result

        # 所有节点运行完，落盘 trace。
        # finished_at 由 trace.save() 用 time.time() 补——之前在这里手动赋
        # time.monotonic()，和 started_at 的 time.time() 基准不同，算出负耗时。
        filepath = trace.save()
        print(f"📊 {trace.summary()}  → {filepath}")
        self._annotate_turn_cost(trace)
        flush_opik()

        return result.get("final_answer", "抱歉，无法回答。")

    def _annotate_turn_cost(self, trace: TraceContext) -> None:
        """Annotate Opik with per-turn token cost + local_trace_id + opik_trace_id."""
        cost = estimate_tokens_cost(
            trace.total_input_tokens,
            trace.total_output_tokens,
            model=self.model,
        )
        oid = get_current_opik_trace_id() or capture_opik_trace_id_for_graph(
            getattr(self, "graph", None)
        )
        if oid:
            trace.opik_trace_id = oid
        meta = {**cost, "local_trace_id": trace.trace_id}
        if oid:
            meta["opik_trace_id"] = oid
        annotate_opik(metadata=meta, tags=["turn_complete"])
        # If Opik context already closed (e.g. streaming ainvoke), update via tracer
        if oid and getattr(self, "graph", None) is not None:
            try:
                tracer = getattr(self.graph, "_opik_tracer", None)
                if tracer is not None:
                    created = tracer.created_traces()
                    for t in reversed(created or []):
                        if getattr(t, "id", None) == oid:
                            t.update(metadata=meta, tags=["turn_complete"])
                            break
            except Exception:
                pass

    async def resume(self, approved: bool = True, clarified_query: str = "") -> str:
        """HITL 审批后恢复 graph 执行。

        Args:
            approved: True = 用户批准，继续执行敏感查询（confidence_gate 场景）
            clarified_query: 用户对澄清问题的回复（clarify 场景）
        """
        opik_tag_hitl("resume", approved=bool(approved))
        resume_value = {"approved": approved}
        if clarified_query:
            resume_value["clarified_query"] = clarified_query

        trace = self._current_config["configurable"].get("_trace")
        result = await self.graph.ainvoke(
            Command(resume=resume_value),
            config=self._current_config,
        )
        self._last_state = result
        if trace:
            filepath = trace.save()  # finished_at 由 save() 统一补，避免时钟基准混用
            print(f"📊 {trace.summary()}  → {filepath}")
            self._annotate_turn_cost(trace)
            flush_opik()
        return result.get("final_answer", "抱歉，无法回答。")

    def get_execution_info(self) -> dict:
        """返回最近一次执行的结构化信息（供 UI / eval_runner 展示）。

        从 _last_state 提取：Router 分派方式、执行计划、SQL、Reflection 结果。
        tokens 从内存中的 TraceContext 读取（不依赖 trace 文件落盘，
        HITL 暂停时也能拿到已累计的数字）。
        """
        state = self._last_state or {}
        results = state.get("results", {})

        # 提取 SQL（从 sql agent 的结果中）
        sql_text = ""
        sql_result = results.get("sql", "")
        if sql_result:
            sql_match = re.search(r'(SELECT|WITH)\s.+?(?:;|$)', sql_result, re.IGNORECASE | re.DOTALL)
            sql_text = sql_match.group(0).strip() if sql_match else ""

        # plan 里的 agent 名列表（eval 的 agent_in_plan / agent_not_in_plan 断言用）
        plan = state.get("plan", []) or []
        plan_agents = [s.get("agent", "") for s in plan if s.get("agent")]

        # token 统计：从内存 trace 读（interrupt 后 resume 前也有值）
        # 防御性读取：未执行过 run() 的 runner（或测试桩）可能没有 _current_config
        tokens = 0
        config = getattr(self, "_current_config", None) or {}
        trace = config.get("configurable", {}).get("_trace")
        if trace is not None:
            tokens = int(getattr(trace, "total_input_tokens", 0) or 0) + int(
                getattr(trace, "total_output_tokens", 0) or 0
            )

        return {
            "plan": plan,
            "plan_agents": plan_agents,
            "tokens": tokens,
            "sql": sql_text,
            "reflection": {
                "attempts": state.get("_reflection_attempts", 0),
            },
            "stats": state.get("_stats", {}),
        }

    async def aclose(self) -> None:
        """关闭 Checkpointer 连接（Redis 或 SQLite）。main.py quit 时调用。"""
        flush_opik()
        if self._redis_cm is not None:
            await self._redis_cm.__aexit__(None, None, None)
        elif hasattr(self, "_conn"):
            await self._conn.close()
