"""多 Agent 编排辅助函数：重试/超时/重规划/调度核心。

从原 orchestrator.py 拆分（职责：编排的"手"）。
"""

import os
import time

from langgraph.types import RunnableConfig

from harness.constraints.circuit_breaker import DEGRADED_MESSAGE, CircuitOpenError
from harness.observation.opik_tracing import (
    opik_tag_route,
    opik_tag_task_board,
)
from harness.observation.ops_metrics import record_error, record_tokens
from harness.observation.tracer import TraceContext
from harness.orchestration.multi.base import is_agent_timeout
from harness.orchestration.multi.state import MultiAgentState, agent_config

# Checkpointer 数据库路径。
# 图每执行完一个节点，自动把 state 写进这个 SQLite 文件。
# 同一 thread_id 的后续调用从这个文件恢复 state（messages 累积、results 保留）。
# 必须用 AsyncSqliteSaver：graph.ainvoke 走 async checkpoint API，同步版不兼容。
# 开发用 SQLite；生产可换 PostgresSaver。


def _fmt_time(seconds: float) -> str:
    """格式化耗时: <1s 显示 ms, >=1s 显示 s。"""
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    return f"{seconds:.1f}s"
async def _run_agent_with_timeout(agent, client, task, model, trace_span, agent_name: str, context: str = "", config: RunnableConfig | None = None) -> tuple[str, dict]:
    """包装 agent.run()：检测超时，打日志，返回 (result, usage)。

    如果 config 中注入了 _event_queue，emit step_start/step_end SSE 事件。
    """
    queue = agent_config(config).get("_event_queue") if config else None
    if queue:
        await queue.put(("step_start", {"type": "step_start", "node": agent_name.lower(), "task": task[:60], "timestamp": time.time()}))

    try:
        result, usage = await agent.run(client, task, context=context, model=model, verbose=True)
    except CircuitOpenError:
        # 熔断降级：模型服务不可用，节点返回可读文案而不是让整张图崩溃
        record_error(agent_name, "熔断降级")
        trace_span.error = "熔断降级：模型服务不可用"
        print("⚠️ 熔断降级：模型服务暂时不可用，请稍后重试")
        return DEGRADED_MESSAGE, {}

    if queue:
        elapsed = trace_span.elapsed if hasattr(trace_span, 'elapsed') else 0
        tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        await queue.put(("step_end", {"type": "step_end", "node": agent_name.lower(), "task": task[:60], "elapsed": round(elapsed, 3), "tokens": tokens}))

    record_tokens(usage.get("input_tokens", 0) + usage.get("output_tokens", 0))
    if is_agent_timeout(result):
        record_error(agent_name, "超过最大轮数")
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
async def _run_agent_node(state, config, agent, agent_name, result_key):
    """通用 Agent 节点：取 task → 执行 → 写 results。"""
    client = agent_config(config)["_client"]
    trace = agent_config(config).get("_trace") or TraceContext(state.get("query", ""))
    model = agent_config(config).get("_model", os.getenv("ANTHROPIC_MODEL", "deepseek-chat"))
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
