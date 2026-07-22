"""LangGraph 图用的 State 定义。

两个图各用自己的 State:
- DBAgentState:  graph.py（0020 单 Agent）
- MultiAgentState: orchestrator.py（0023 多 Agent）
"""

from typing import TypedDict, Annotated
from langgraph.graph.message import add_messages


def _merge_stats(left: dict, right: dict) -> dict:
    """累加各节点的 token 和耗时统计。"""
    return {
        "input_tokens": left.get("input_tokens", 0) + right.get("input_tokens", 0),
        "output_tokens": left.get("output_tokens", 0) + right.get("output_tokens", 0),
        "turns": left.get("turns", 0) + right.get("turns", 0),
        "elapsed": left.get("elapsed", 0) + right.get("elapsed", 0),
        "nodes": left.get("nodes", []) + right.get("nodes", []),
    }


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
    query: str                  # 用户问题（ainvoke 时写入）
    messages: Annotated[list, add_messages]  # 对话历史，Checkpointer 持久化
    _recalled_memories: str     # pre-turn 向量召回的记忆
    _conversation_summary: str  # ConversationManager 压缩的早期对话摘要
    plan: list                  # Router 输出的执行计划
    results: dict               # 各 Agent 中间结果
    final_answer: str           # 最终输出（analysis 写入）
    next: str                   # 下一个节点名（_next_step 写入，edge_router 读取）
    _stats: Annotated[dict, _merge_stats]  # 各节点累计: input_tokens/output_tokens/turns/elapsed/nodes
    _client: object             # ⚠️ 已废弃——现在走 configurable，不再放 state
    _model: str                 # ⚠️ 已废弃——同上
    _inject_dq: bool            # 是否注入 DataQuality（首轮为 True）



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