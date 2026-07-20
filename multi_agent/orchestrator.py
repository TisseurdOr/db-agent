"""0023 多 Agent 编排: Router + DataQuality + SQL + Analysis via LangGraph.

用法:
    from multi_agent.orchestrator import MultiAgentRunner
    runner = MultiAgentRunner(client, enable_data_quality=True)
    answer = await runner.run("对比华东和华南的销售趋势")
    # 首次调用自动注入 DataQuality，后续跳过
"""

import json
import os

from langgraph.graph import StateGraph, END
from anthropic import Anthropic

from multi_agent.state import MultiAgentState
from multi_agent.agents import (
    sql_agent, analysis_agent, strategy_agent,
    data_quality_agent, ROUTER_PROMPT,
)
from utils.llm import extract_text


# ── 节点函数 ──
# 编排函数
async def node_router(state: MultiAgentState) -> dict:
    """Router: LLM 分析 query → 输出执行计划。"""
    client = state["_client"]
    model = state.get("_model", os.getenv("ANTHROPIC_MODEL", "deepseek-chat"))
    # node_router 调 LLM:
    resp = client.messages.create(
        model=model,
        max_tokens=300,
        system=ROUTER_PROMPT,
        messages=[{"role": "user", "content": state["query"]}],
    )
    text = extract_text(resp, context="router")
    #防止返回的是非json格式，导致json.loads失败
    try:
        plan_data = json.loads(text) if text else {}
    except json.JSONDecodeError:
        plan_data = {"plan": [{"agent": "sql", "task": state["query"]}]}
    # 分配任务
    plan = plan_data.get("plan", [])
    if not plan:
        return {"plan": [], "next": "done"}

    # 首次注入 DataQuality: 在 plan 最前面插入，
    # _inject_dq 由 MultiAgentRunner 控制（通常只在首次查询为 True）。有的话，在 plan 最前面塞一个数据质量检查，先扫库再查数。
    if state.get("_inject_dq") and not any(s["agent"] == "data_quality" for s in plan):
        plan.insert(0, {
            "agent": "data_quality",
            "task": "检查数据库整体数据质量：表行数、日期连续性、NULL比例、异常值。输出事实报告，不做业务判断。",
        })
    # 比如 plan 是 [{agent: "sql"}, {agent: "analysis"}]，就会设 "next": "sql"。
    return {"plan": plan, "next": plan[0]["agent"]}


async def node_data_quality(state: MultiAgentState) -> dict:
    """DataQuality Agent: 扫一遍数据质量。"""
    task = next(s["task"] for s in state["plan"] if s["agent"] == "data_quality")
    result = await data_quality_agent.run(
        state["_client"], task, model=state.get("_model")
    )
    results = {**state.get("results", {}), "data_quality": result}
    return _next_step(state, results, "data_quality")


async def node_sql(state: MultiAgentState) -> dict:
    """SQL Agent: 执行计划中的 sql task。"""
    task = next(s["task"] for s in state["plan"] if s["agent"] == "sql")
    result = await sql_agent.run(
        state["_client"], task, model=state.get("_model")
    )
    results = {**state.get("results", {}), "sql": result}
    return _next_step(state, results, "sql")


async def node_strategy(state: MultiAgentState) -> dict:
    """Strategy Agent: 执行计划中的 strategy task。"""
    task = next(s["task"] for s in state["plan"] if s["agent"] == "strategy")
    result = await strategy_agent.run(
        state["_client"], task, model=state.get("_model")
    )
    results = {**state.get("results", {}), "strategy": result}
    return _next_step(state, results, "strategy")


async def node_analysis(state: MultiAgentState) -> dict:
    """Analysis Agent: 综合所有中间结果，生成最终回答。"""
    context_parts = []
    for name, text in state.get("results", {}).items():
        context_parts.append(f"[{name} Agent 结果]\n{text}")
    context = "\n\n".join(context_parts)

    result = await analysis_agent.run(
        state["_client"],
        task=state["query"],
        context=context,
        model=state.get("_model"),
    )
    return {"final_answer": result}


def _next_step(state: MultiAgentState, results: dict, current: str) -> dict:
    """当前 node 执行完后，决定下一个是谁。"""
    plan = state["plan"]
    executed = set(results.keys())
    pending = [s for s in plan if s["agent"] not in executed]

    if pending:
        return {"results": results, "next": pending[0]["agent"]}

    if current != "analysis":
        return {"results": results, "next": "analysis"}
    return {"results": results, "final_answer": "\n\n".join(results.values())}


# ── Router 函数 ──

def edge_router(state: MultiAgentState) -> str:
    # 必须返回 path map 的 key（如 "done"），不能直接返回 END。
    # 直接返回 END（"__end__"）会触发 KeyError，因为 targets 里没有 "__end__"。
    return state.get("next") or "done"


# ── 构建 Graph ──
#target--》地图
def build_multi_agent_graph():
    builder = StateGraph(MultiAgentState)

    builder.add_node("router", node_router)
    builder.add_node("data_quality", node_data_quality)
    builder.add_node("sql", node_sql)
    builder.add_node("strategy", node_strategy)
    builder.add_node("analysis", node_analysis)

    builder.set_entry_point("router")

    targets = {
        "data_quality": "data_quality",
        "sql": "sql",
        "strategy": "strategy",
        "analysis": "analysis",
        "done": END,
    }

    builder.add_conditional_edges("router", edge_router, targets)
    builder.add_conditional_edges("data_quality", edge_router, targets)
    builder.add_conditional_edges("sql", edge_router, targets)
    builder.add_conditional_edges("strategy", edge_router, targets)
    builder.add_edge("analysis", END)

    return builder.compile()


# ── 高层封装: main.py 直接调 ──

class MultiAgentRunner:
    def __init__(
        self,
        client: Anthropic,
        model: str = "deepseek-chat",
        enable_data_quality: bool = True,
    ):
        self.client = client
        self.model = model
        self.enable_data_quality = enable_data_quality
        self._dq_done = False
        self.graph = build_multi_agent_graph()

    def _should_inject_dq(self) -> bool:
        if not self.enable_data_quality:
            return False
        if self._dq_done:
            return False
        self._dq_done = True
        return True
# 把初始 state 丢进 LangGraph，跑完整条多 Agent 链路，最后取出 final_answer。差别只在同步 / 异步。
    async def run(self, query: str) -> str:
        result = await self.graph.ainvoke({
            "query": query,
            "_client": self.client,
            "_model": self.model,
            "_inject_dq": self._should_inject_dq(),
            "plan": [],
            "results": {},
            "final_answer": "",
            "next": "",
        })
        return result.get("final_answer", "抱歉，无法回答。")

    def run_sync(self, query: str) -> str:
        result = self.graph.invoke({
            "query": query,
            "_client": self.client,
            "_model": self.model,
            "_inject_dq": self._should_inject_dq(),
            "plan": [],
            "results": {},
            "final_answer": "",
            "next": "",
        })
        return result.get("final_answer", "抱歉，无法回答。")
