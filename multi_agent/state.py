"""LangGraph 图用的 State 定义。

两个图各用自己的 State:
- DBAgentState:  graph.py（0020 单 Agent）
- MultiAgentState: orchestrator.py（0023 多 Agent）
"""

from typing import TypedDict, Annotated
from langgraph.graph.message import add_messages


# ── 0020: 单 Agent ──

class DBAgentState(TypedDict):
    messages: Annotated[list, add_messages]
    tool_results: list
    sql_to_review: str
    next_step: str


# ── 0023: 多 Agent 编排 ──
# _client / _model 是 _ 前缀——由 MultiAgentRunner.invoke() 注入，
# 不作为 LangGraph state channel 被追踪（但 TypedDict 声明了才能传进去）。

class MultiAgentState(TypedDict):
    query: str          # 用户问题（invoke 时写入，只读）
    plan: list          # Router 输出的执行计划（router 写入，其他节点读）
    results: dict       # 各 Agent 中间结果（每个 agent 往里塞自己的结果）
    final_answer: str   # 最终输出（analysis 写入，runner.run() 返回）
    next: str           # 下一个节点名（_next_step 写入，edge_router 读取）
    _client: object     # LLM client（注入，各个节点读）
    _model: str         # 模型名（注入）
    _inject_dq: bool    # 是否注入 DQ（注入）


'''
state = {query: "华东", plan: [...], results: {}, next: "", ...}

node_sql(state):
    task = state["plan"]里找 agent=="sql" 的 task    ← 读 plan
    result = await sql_agent.run(task)               ← 执行
    results = {**state["results"], "sql": result}     ← 读+写 results
    return _next_step(state, results, "sql")
         │
         ├── executed = {"sql"}                       ← 看 results keys
         ├── pending = plan里 agent 不在 executed 的  ← 比对 plan 和 executed
         └── return {results, next: "analysis"}      ← 写 next

edge_router(state):
    return state["next"]  # → "analysis"             ← 读 next
         │
    LangGraph 查 targets: "analysis" → node_analysis ← 用 targets

plan 是路线图，executed 是已打卡的站，_next_step 对比两者决定下一站，next 是站牌，targets 是站牌→站点的翻译。

'''