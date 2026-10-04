"""多 Agent 图节点：Router / SQL / Strategy / HBase / Hive / Analysis / Reflection 等。

从原 orchestrator.py 拆分（职责：编排的"大脑节点"）。
"""

import json
import re
import time

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from harness.config import DEFAULT_MODEL
from harness.constraints.confidence import (
    CONFIDENCE_PROMPT,
    format_confidence_report,
    parse_confidence_result,
    should_pause,
)
from harness.constraints.guardrails import guard_output
from harness.constraints.retry import acall_with_retry
from harness.observation.llm import extract_text
from harness.observation.opik_tracing import (
    opik_tag_fewshot,
    opik_tag_guard,
    opik_tag_hitl,
    opik_tag_reflection,
    opik_tag_sql,
)
from harness.observation.tracer import TraceContext, mask_sql
from harness.orchestration.multi.agent_names import (
    AGENT_ANALYSIS,
    AGENT_DATA_QUALITY,
    AGENT_HBASE,
    AGENT_HIVE,
    AGENT_SQL,
    AGENT_STRATEGY,
)
from harness.orchestration.multi.agents import (
    analysis_agent,
    data_quality_agent,
    hbase_agent,
    hive_agent,
    sql_agent,
    strategy_agent,
)
from harness.orchestration.multi.base import is_agent_timeout
from harness.orchestration.multi.helpers import (
    _annotate_route,
    _finish_agent_task,
    _fmt_time,
    _maybe_replan,
    _next_step,
    _next_step_after_sql,
    _run_agent_node,
    _run_agent_with_timeout,
)
from harness.orchestration.multi.router import (
    ROUTER_PROMPT,
    incomplete_query_reason,
    is_chitchat_query,
    route_override,
)
from harness.orchestration.multi.state import MultiAgentState, agent_config, require_client

# Checkpointer 数据库路径。
# 图每执行完一个节点，自动把 state 写进这个 SQLite 文件。
# 同一 thread_id 的后续调用从这个文件恢复 state（messages 累积、results 保留）。
# 必须用 AsyncSqliteSaver：graph.ainvoke 走 async checkpoint API，同步版不兼容。
# 开发用 SQLite；生产可换 PostgresSaver。


# 闲聊统一回复：不查库、不走 LLM，0 token 秒回。
CHITCHAT_RESPONSE = (
    "你好！我是 db-agent，一个企业级自然语言数据库分析助手。"
    "你可以直接问我订单、销售、员工、产品这些数据的问题，"
    "比如「上个月销售额是多少」「各部门有多少人」，也可以让我做趋势分析、数据对比。"
    "有什么想查的？"
)


async def node_router(state: MultiAgentState, config: RunnableConfig) -> dict:
    """Router: 分析用户 query，输出 JSON 执行计划。

    从 state["messages"] 取最近 6 条对话历史，和当前 query 一起发给 LLM。
    这样 Router 能识别"刚才问了什么"等元问题——
    看到历史里上一轮问了"有哪些表"，就知道这不是数据查询。
    """
    queue = agent_config(config).get("_event_queue")
    router_task = "分析意图"
    t_router = time.time()
    if queue:
        await queue.put((
            "step_start",
            {"type": "step_start", "node": "router", "task": router_task, "timestamp": time.time()},
        ))

    try:
        return await _node_router_body(state, config)
    finally:
        if queue:
            elapsed = time.time() - t_router
            tokens = 0
            trace = agent_config(config).get("_trace")
            spans = getattr(trace, "spans", None) if trace is not None else None
            if spans:
                for sp in reversed(spans):
                    if getattr(sp, "node", "") == "router":
                        tokens = int(getattr(sp, "total_tokens", 0) or 0)
                        break
            await queue.put((
                "step_end",
                {
                    "type": "step_end",
                    "node": "router",
                    "task": router_task,
                    "elapsed": round(elapsed, 3),
                    "tokens": tokens,
                },
            ))


async def _node_router_body(state: MultiAgentState, config: RunnableConfig) -> dict:
    """Router 主体（从 node_router 拆出，便于 SSE step_start/end 包一层）。"""
    trace = agent_config(config).get("_trace") or TraceContext(state.get("query", ""))
    span = trace.start_span("router", "分析意图")

    client = require_client(config)
    model = agent_config(config).get("_model", DEFAULT_MODEL)
    router_cache = agent_config(config).get("_router_cache")

    # 失败重规划路径：跳过硬规则和缓存——两者都会原样复现失败的 plan，
    # 必须走 LLM 并把失败反馈喂进去，才可能得到不同的计划。
    replan_feedback = state.get("_replan_feedback", "")

    # 硬规则优先：闲聊 / 元问题 / 纯制度查询不依赖 LLM（也避免脏缓存）
    prev_agents = [s["agent"] for s in state.get("plan", [])] if state.get("plan") else []
    t0 = time.time()
    override = None if replan_feedback else route_override(state["query"], prev_agents=prev_agents)
    route_latency = time.time() - t0
    router_usage: dict[str, float] = {"input_tokens": 0, "output_tokens": 0, "turns": 0}
    cached_plan = None
    plan_data = {}  # 硬规则路径不经 LLM，后续读 confidence 前必须有默认值

    # 硬规则兜底：route_override 未兜住且查询明显不完整/含糊 → 直接进 clarify，不花 LLM
    if override is None and not replan_feedback:
        incomplete_reason = incomplete_query_reason(state["query"])
        if incomplete_reason:
            span.task = f"澄清: {incomplete_reason}"
            trace.finish_span(span, router_usage)
            print(f"❓ 硬规则澄清: {state['query'][:40]}（{incomplete_reason}）")
            _annotate_route("clarify_rule", [], router_cache)
            return {
                "plan": [],
                "next": "clarify",
                "_stats": {"elapsed": route_latency, "nodes": ["router(硬规则澄清)"]},
            }

    if override is not None:
        plan = override
        span.task = "硬规则覆盖" if plan else "无需数据查询"
        router_usage["elapsed"] = route_latency
        if not plan:
            trace.finish_span(span, router_usage)
            print(trace.print_progress(span))
            _annotate_route("rule", [], router_cache)
            return {
                "plan": [],
                "next": "done",
                "final_answer": CHITCHAT_RESPONSE,
                "_stats": {"elapsed": route_latency, "nodes": ["router(硬规则)"]},
            }
    else:
        # 查缓存：同样 query 之前解析过，直接复用 plan，省一次 LLM 调用（~250t）
        # 重规划时不读缓存——缓存里存的正是刚失败的 plan
        cached_plan = router_cache.get(state["query"]) if (router_cache and not replan_feedback) else None

        if cached_plan is not None:
            assert router_cache is not None  # 缓存命中必有缓存
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

            from harness.memory.preturn_recall import format_memory_for_router
            mem_block = format_memory_for_router(state.get("_recalled_memories") or "")
            router_system = ROUTER_PROMPT
            if mem_block:
                router_system = f"{ROUTER_PROMPT}\n\n{mem_block}"

            # 优先 Jev 做 Agent 选择（校准概率、快/便宜两个量级）
            plan_data = await _route_via_jev(state["query"], mem_block)
            router_usage: dict[str, float] = {"input_tokens": 0, "output_tokens": 0, "turns": 0, "elapsed": 0.0}

            if not plan_data:
                # 未配置 Jev / 取不到决策 → 原 LLM 规划路径
                t_llm = time.time()
                resp = await acall_with_retry(
                    client.messages.create,
                    model=model,
                    max_tokens=300,
                    system=router_system,
                    messages=router_msgs,
                )
                llm_latency = time.time() - t_llm
                router_usage = {"input_tokens": 0, "output_tokens": 0, "turns": 1, "elapsed": llm_latency}
                _usage = getattr(resp, "usage", None)
                if _usage is not None:
                    router_usage["input_tokens"] = _usage.input_tokens or 0
                    router_usage["output_tokens"] = _usage.output_tokens or 0

                text = extract_text(resp, context="router")
                try:
                    plan_data = json.loads(text) if text else {}
                except json.JSONDecodeError:
                    # 解析失败：不盲派 SQL——留空走下面的安全兜底链
                    # （闲聊→结束 / 不完整→澄清 / 判不了才最后兜底 sql），
                    # 避免把"不该查库"的问题（制度/回忆/闲聊）误派给 SQL Agent。
                    plan_data = {}

        plan = plan_data.get("plan", [])
        if not plan:
            # 空 plan：真闲聊就结束；否则兜底 sql（避免空白回复）
            # 注意：不要用 len>4 —— 「你好，你能做什么」长度很长但仍是闲聊
            query_text = state["query"].strip()
            if is_chitchat_query(query_text):
                span.task = "无需数据查询"
                trace.finish_span(span, router_usage)
                print(trace.print_progress(span))
                _annotate_route("llm", [], router_cache)
                return {"plan": [], "next": "done", "final_answer": CHITCHAT_RESPONSE}
            # 明显不完整/含糊 → 澄清，不猜、不派 SQL
            fallback_reason = incomplete_query_reason(query_text)
            if fallback_reason:
                span.task = f"澄清: {fallback_reason}"
                trace.finish_span(span, router_usage)
                print(trace.print_progress(span))
                _annotate_route("llm_clarify", [], router_cache)
                return {
                    "plan": [],
                    "next": "clarify",
                    "_stats": {"elapsed": router_usage.get("elapsed", 0), "nodes": ["router(兜底澄清)"]},
                }
            # 判不了 → 直接说清楚，不猜 SQL（猜 SQL 会把制度/流程类问题误派去查库）
            span.task = "无法判断 → 请补充"
            trace.finish_span(span, router_usage)
            print(trace.print_progress(span))
            _annotate_route("llm_unclear", [], router_cache)
            return {
                "plan": [],
                "next": "done",
                "final_answer": "抱歉，我没太理解你的问题，能再具体说明一下吗？比如想查什么数据，或问哪方面制度/流程。",
                "_stats": {"elapsed": router_usage.get("elapsed", 0), "nodes": ["router(无法判断)"]},
            }

    # 缓存写入：仅 LLM 路径；硬规则不写缓存（避免污染）；
    # 重规划出的 plan 也不写——它是针对本次失败的补救计划，不是该 query 的通用答案
    if override is None and cached_plan is None and router_cache is not None and plan and not replan_feedback and plan_data.get("confidence") != "low":
        router_cache.set(state["query"], plan)

    # DataQuality 首次注入：在 plan 最前面插入 DQ 检查，先扫库再查数。
    if state.get("_inject_dq") and not any(s["agent"] == AGENT_DATA_QUALITY for s in plan):
        plan.insert(0, {
            "agent": AGENT_DATA_QUALITY,
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
        assert router_cache is not None
        route_source = "cache"
        print(f"   💾 缓存命中 ({router_cache.hit_rate})")
    else:
        route_source = "llm"
        print(f"   🤖 LLM 路由 ({router_usage.get('elapsed', 0):.1f}s · {router_usage.get('input_tokens', 0)}+{router_usage.get('output_tokens', 0)}t)")
    _annotate_route(route_source, plan, router_cache)
    stats = {"elapsed": router_usage.get("elapsed", route_latency), "nodes": ["router"]}

    # 检测模糊查询：Router 返回了 plan 但置信度标记为 low
    if plan_data.get("confidence") == "low" and not override:
        return {"plan": plan, "next": "clarify", "_stats": stats, "_replan_feedback": ""}

    # s12：把 plan 落成 .tasks/ 任务图（先计划再执行的磁盘可见版）
    task_manager = agent_config(config).get("_task_manager")
    if task_manager is not None and plan:
        task_manager.materialize_from_plan(plan, query=state.get("query", ""))

    # mid-flight：Router 扩写 task 后按新语义再召长期记忆（Self-Query）
    from harness.memory.preturn_recall import maybe_rerecall
    recalled = await maybe_rerecall(
        state.get("query", ""),
        plan,
        state.get("_recalled_memories") or "",
        client=client,
    )

    # _replan_feedback 用完即清——留着会让下一轮 Router 误以为又失败了
    out = {"plan": plan, "next": plan[0]["agent"], "_stats": stats, "_replan_feedback": ""}
    if recalled != (state.get("_recalled_memories") or ""):
        out["_recalled_memories"] = recalled
    return out
CLARIFY_PROMPT = """用户问了一个模糊的问题："{query}"

当前数据库中有以下表和相关上下文。请生成 2-3 个简短、具体的澄清问题，帮助理解用户的真实意图。
问题应该引导用户提供更具体的信息（时间范围、实体名、指标、对比维度等）。

只输出问题列表，每行一个，以 "- " 开头。不要其他文字。"""
async def node_clarify(state: MultiAgentState, config: RunnableConfig) -> dict:
    """AskHuman 澄清节点：检测模糊查询并主动提问。

    当 Router 无法确定用户意图时触发。
    用 LLM 生成 2-3 个针对性问题，通过 interrupt 暂停等待用户回复。
    """
    trace = agent_config(config).get("_trace") or TraceContext(state.get("query", ""))
    span = trace.start_span("clarify", "澄清模糊问题")

    client = require_client(config)
    model = agent_config(config).get("_model", DEFAULT_MODEL)

    prompt = CLARIFY_PROMPT.format(query=state["query"])
    resp = await acall_with_retry(
        client.messages.create,
        model=model,
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    text = extract_text(resp, context="clarify")
    usage = {"input_tokens": 0, "output_tokens": 0, "turns": 1}
    resp_usage = getattr(resp, "usage", None)
    if resp_usage:
        usage["input_tokens"] = resp_usage.input_tokens or 0
        usage["output_tokens"] = resp_usage.output_tokens or 0

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

    # 用户回复后 resume —— 用澄清后的 query 重新路由，并按新语义再召记忆
    # 用户取消（approved=False）或没给回答 → 结束本轮，避免同 query 反复触发 clarify 死循环
    if isinstance(response, dict):
        if response.get("approved") is False or not (response.get("clarified_query") or "").strip():
            print("   🚫 用户取消澄清")
            return {
                "final_answer": "问题信息不足，已取消查询。请补充对象/维度/时间后重试。",
                "messages": [AIMessage(content="问题信息不足，已取消查询。请补充对象/维度/时间后重试。")],
                "next": "done",
            }
        clarified = str(response.get("clarified_query", "")).strip()
        if clarified:
            print(f"   💬 用户澄清: {clarified[:80]}")
            from harness.memory.preturn_recall import maybe_rerecall
            recalled = await maybe_rerecall(
                state.get("query", ""),
                state.get("plan"),
                state.get("_recalled_memories") or "",
                client=client,
                clarified_query=clarified,
            )
            return {
                "query": clarified,
                "next": "router",
                "_recalled_memories": recalled,
            }

    return {"next": "done"}
async def node_data_quality(state: MultiAgentState, config: RunnableConfig) -> dict:
    """DataQuality Agent: 扫一遍数据质量（NULL 比例、日期连续性、异常值）。"""
    return await _run_agent_node(state, config, data_quality_agent, AGENT_DATA_QUALITY, AGENT_DATA_QUALITY)
async def node_sql(state: MultiAgentState, config: RunnableConfig) -> dict:
    """SQL Agent: 查数据库（list_tables / describe_table / run_query）。"""
    trace = agent_config(config).get("_trace") or TraceContext(state.get("query", ""))
    client = require_client(config)
    model = agent_config(config).get("_model", DEFAULT_MODEL)
    task = next(s["task"] for s in state["plan"] if s["agent"] == AGENT_SQL)
    span = trace.start_span(AGENT_SQL, task[:60])
    print(f"⏳ SQL Agent: {task[:60]}...")

    # 检索式 few-shot（Vanna 模式）：召回相似问题的已验证 SQL 注入上下文。
    # 用原始 query 而非 Router 改写后的 task 检索——样例库存的是用户口语问法。
    from harness.context.sql_examples import get_sql_fewshot
    from harness.tools.schema import discover_relevant_schema
    fewshot = get_sql_fewshot(state.get("query", "") or task)
    fewshot_hits = 0
    if fewshot:
        fewshot_hits = fewshot.count("\nQ: ")
        print(f"   📚 few-shot: 命中 {fewshot_hits} 条相似 SQL 样例")
    opik_tag_fewshot(fewshot_hits)

    # 预注入相关 schema，省掉 Agent 首轮 discover 工具调用（少 1 次 LLM round-trip）
    schema_ctx = ""
    try:
        discovered = discover_relevant_schema(state.get("query", "") or task)
        schema_text = (discovered or {}).get("schema_text") or ""
        if schema_text:
            schema_ctx = (
                "[相关表结构已预检索——足够则直接写 SQL，不必再调 discover_relevant_schema]\n"
                f"{schema_text}"
            )
            print(f"   🗂️ schema: 预注入 {discovered.get('field_count', 0)} 字段")
    except Exception:
        pass

    from harness.memory.preturn_recall import format_memory_for_sql
    mem_ctx = format_memory_for_sql(state.get("_recalled_memories") or "")
    context = "\n\n".join(p for p in (schema_ctx, fewshot, mem_ctx) if p)
    result, usage = await _run_agent_with_timeout(sql_agent, client, task, model, span, "SQL", context=context, config=config)
    trace.finish_span(span, usage, error=span.error)
    print(f"✅ SQL Agent ({_fmt_time(span.elapsed)} · {span.total_tokens}t · {usage['turns']}轮)")
    _finish_agent_task(config, AGENT_SQL, failed=is_agent_timeout(result))

    # 自学习回流（第 3 项）：优先用工具层捕获的成功 SQL（比从自然语言抽更准）
    if not is_agent_timeout(result):
        from harness.memory.feedback import learn_from_success
        from harness.tools.query import pop_last_successful_sql
        learned_sql = pop_last_successful_sql()
        if learned_sql:
            opik_tag_sql(mask_sql(learned_sql))
        learn_from_success(
            state.get("query", "") or task,
            result,
            sql=learned_sql,
            source="auto",
        )

    results = {**state.get("results", {}), AGENT_SQL: result}
    replan = _maybe_replan(state, results, AGENT_SQL, result)
    if replan:
        return replan
    # SQL 生成后先过置信度门，再决定下一步
    return _next_step_after_sql(state, results)
# 各专职 Agent 的职责（拼进 Jev 的"是非题"，让它有依据地判断）
_ROUTE_AGENT_MENU: dict[str, str] = {
    "sql": "结构化数据查询（订单/员工/部门/产品/客户）",
    "analysis": "综合分析与建议、或对话历史/元问题（自己不查库）",
    "strategy": "公司制度政策（提成/年假/考勤/定价/战略）",
    "hbase": "HBase Shell 命令生成",
    "hive": "Olist 数仓查询 / Hive 方言",
}


async def _route_via_jev(query: str, mem_block: str = "") -> dict:
    """用 Jev 做 Agent 选择。未启用/失败 → 返回 {} 走 LLM 回退。

    关键设计：用**每个 Agent 一个是非题**，而不是"选一个"——因为一个请求可能需要
    多个 Agent（如「SQL 查询 + analysis 画图」），单选表达不了多 Agent 计划。
    plan 里的 task 用原始 query（各 Agent 本就按 query 工作），故不依赖生成能力。
    """
    from harness.jev_client import decide as jev_decide
    from harness.jev_client import extract_bool, extract_probability
    from harness.jev_client import is_enabled as jev_enabled

    if not jev_enabled():
        return {}

    state_text = f"用户问题：{query}"
    if mem_block:
        state_text = f"{state_text}\n\n{mem_block}"

    questions: list[dict] = [
        {
            "id": f"need_{agent}",
            "type": "boolean",
            "prompt": f"这个请求需要「{agent}」Agent 吗？它的职责是：{desc}",
        }
        for agent, desc in _ROUTE_AGENT_MENU.items()
    ]
    questions += [
        {
            "id": "is_chitchat",
            "type": "boolean",
            "prompt": "这是闲聊/能力介绍（如「你好」「你能做什么」），不需要任何数据查询吗？",
        },
        {
            "id": "is_vague",
            "type": "boolean",
            "prompt": "问题太模糊、缺关键信息（对象/时间/指标不明），需要先向用户澄清吗？",
        },
        {
            "id": "route_confidence",
            "type": "scale",
            "min": 0,
            "max": 1,
            "prompt": "你对以上判断的把握有多大？",
        },
    ]

    result = await jev_decide(state_text, questions)
    if not result:
        return {}

    if extract_bool(result, "is_chitchat"):
        return {"plan": [], "confidence": "high"}

    agents = [a for a in _ROUTE_AGENT_MENU if extract_bool(result, f"need_{a}")]
    prob = extract_probability(result, "route_confidence")
    low = extract_bool(result, "is_vague") is True or (prob is not None and prob < 0.5)

    if not agents:
        # 没判出任何 Agent：模糊 → 给个最佳猜测 + low（节点会转澄清）；否则交回兜底链
        if low:
            return {"plan": [{"agent": "sql", "task": query}], "confidence": "low"}
        return {}

    return {
        "plan": [{"agent": a, "task": query} for a in agents],
        "combine": True,
        "confidence": "low" if low else "high",
    }


async def _assess_sql_confidence(query: str, sql: str, sql_result: str, client, model: str):
    """评估 SQL 置信度。

    优先用 Jev（System One 决策模型，返回**校准概率**、快/便宜两个量级）；
    未配置 JEV_API_KEY 或调用失败时，回退到原来的 LLM 自评路径。

    返回 (result_data, usage)。
    """
    from harness.jev_client import decide as jev_decide
    from harness.jev_client import extract_probability
    from harness.jev_client import is_enabled as jev_enabled

    if jev_enabled():
        state_text = (
            f"用户问题：{query}\n\n"
            f"生成的 SQL：\n{sql}\n\n"
            f"SQL Agent 的上下文/结果（截断）：\n{sql_result[:2000]}"
        )
        result = await jev_decide(state_text, [{
            "id": "sql_confidence",
            "type": "scale",
            "min": 0,
            "max": 1,
            "prompt": "这条 SQL 有多大把握正确回答用户问题？（0=完全没把握，1=非常有把握）",
        }])
        prob = extract_probability(result, "sql_confidence")
        if prob is not None:
            return (
                {"confidence": prob, "explanation": "Jev 校准概率", "scores": {}},
                {"input_tokens": 0, "output_tokens": 0, "turns": 0},
            )
        # 取不到概率 → 落回 LLM

    prompt = CONFIDENCE_PROMPT.format(
        query=query,
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
    usage = {"input_tokens": 0, "output_tokens": 0, "turns": 1}
    resp_usage = getattr(resp, "usage", None)
    if resp_usage:
        usage["input_tokens"] = resp_usage.input_tokens or 0
        usage["output_tokens"] = resp_usage.output_tokens or 0
    return parse_confidence_result(text), usage


async def node_confidence_gate(state: MultiAgentState, config: RunnableConfig) -> dict:
    """置信度门：LLM 自评 SQL 质量，低分触发 HITL 审批。

    在 node_sql 之后、node_analysis 之前执行。
    从 sql agent 结果中提取 SQL，让 LLM 按 6 项标准打分。
    置信度 < 0.7 时暂停并请用户确认。
    """
    trace = agent_config(config).get("_trace") or TraceContext(state.get("query", ""))
    span = trace.start_span("confidence_gate", "SQL 置信度评估")

    sql_result = state.get("results", {}).get(AGENT_SQL, "")
    if not sql_result:
        trace.finish_span(span, {"input_tokens": 0, "output_tokens": 0, "turns": 0})
        return _next_step(state, state["results"], AGENT_SQL)

    # 提取 SQL 语句
    sql_match = re.search(r'(SELECT|WITH)\s.+?(?:;|$)', sql_result, re.IGNORECASE | re.DOTALL)
    sql = sql_match.group(0).strip() if sql_match else sql_result[:500]

    client = require_client(config)
    model = agent_config(config).get("_model", DEFAULT_MODEL)

    # 置信度评估：优先 Jev（校准概率），否则 LLM 自评
    result_data, usage = await _assess_sql_confidence(
        state["query"], sql, sql_result, client, model
    )

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
            print("🚫 用户拒绝了低置信度 SQL")
            return {
                "final_answer": "SQL 执行已被用户取消（置信度过低）。",
                "messages": [AIMessage(content="SQL 执行已被用户取消（置信度过低）。")],
                "next": "done",
            }

    # 置信度通过，继续下一步
    return _next_step(state, state["results"], AGENT_SQL)
async def node_strategy(state: MultiAgentState, config: RunnableConfig) -> dict:
    """Strategy Agent: 查公司制度文档（search_knowledge_base）。"""
    return await _run_agent_node(state, config, strategy_agent, AGENT_STRATEGY, AGENT_STRATEGY)
async def node_hbase(state: MultiAgentState, config: RunnableConfig) -> dict:
    """HBase Agent: 生成 HBase Shell 命令。"""
    return await _run_agent_node(state, config, hbase_agent, AGENT_HBASE, AGENT_HBASE)
async def node_hive(state: MultiAgentState, config: RunnableConfig) -> dict:
    """Hive Agent: 生成 Hive/Impala 查询。"""
    return await _run_agent_node(state, config, hive_agent, AGENT_HIVE, AGENT_HIVE)
async def node_analysis(state: MultiAgentState, config: RunnableConfig) -> dict:
    """Analysis Agent: 综合所有中间结果 + 记忆，生成最终回答。

    注入 Analysis Agent 上下文的四层信息（按优先级）：
    1. 早期摘要（_conversation_summary）—— ConversationManager 压缩的超窗口对话
    2. 向量记忆（_recalled_memories）—— 跨会话的长期记忆
    3. 最近对话（messages）—— Checkpointer 累积的本轮消息
    4. 中间结果（results）—— 上游 Agent 的执行输出
    """
    trace = agent_config(config).get("_trace") or TraceContext(state.get("query", ""))
    span = trace.start_span(AGENT_ANALYSIS, "综合分析")

    client = require_client(config)
    model = agent_config(config).get("_model", DEFAULT_MODEL)
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
        context_parts.insert(0, "[最近对话]\n" + "\n".join(recent))
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

    print("⏳ Analysis Agent: 综合分析中...")
    result, usage = await analysis_agent.run(
        client,
        task=state["query"],
        context="\n\n".join(context_parts),
        model=model,
    )
    if is_agent_timeout(result):
        span.error = "Analysis 超过最大轮数"
        print("⚠️ Analysis 超过最大轮数")
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
    _finish_agent_task(config, AGENT_ANALYSIS, failed=is_agent_timeout(result))

    # 把最终回答写回 messages —— Checkpointer 自动持久化，
    # 下一轮 Router 和 Analysis 就能从 messages 里看到这轮说了什么。
    next_node = "done" if state.get("_skip_reflection") else "reflection"
    return {
        "final_answer": result,
        "messages": [AIMessage(content=result)],
        "next": next_node,
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
    trace = agent_config(config).get("_trace") or TraceContext(state.get("query", ""))
    span = trace.start_span("reflection", "回答质量审查")

    answer = state.get("final_answer", "")
    if not answer:
        trace.finish_span(span, {"input_tokens": 0, "output_tokens": 0, "turns": 0})
        return {"next": "done"}

    attempts = state.get("_reflection_attempts", 0)

    client = require_client(config)
    model = agent_config(config).get("_model", DEFAULT_MODEL)

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
    resp_usage = getattr(resp, "usage", None)
    if resp_usage:
        usage["input_tokens"] = resp_usage.input_tokens or 0
        usage["output_tokens"] = resp_usage.output_tokens or 0

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
        "next": AGENT_ANALYSIS,
        "_reflection_attempts": attempts + 1,
    }
