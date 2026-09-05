"""多 Agent 构图：条件边 + build_multi_agent_graph。

从原 orchestrator.py 拆分（职责：把节点组装成图）。
"""


from langgraph.graph import END, StateGraph

from harness.orchestration.multi.agent_names import (
    AGENT_ANALYSIS,
    AGENT_DATA_QUALITY,
    AGENT_HBASE,
    AGENT_HIVE,
    AGENT_SQL,
    AGENT_STRATEGY,
)
from harness.orchestration.multi.nodes import (
    node_analysis,
    node_clarify,
    node_confidence_gate,
    node_data_quality,
    node_hbase,
    node_hive,
    node_reflection,
    node_router,
    node_sql,
    node_strategy,
)
from harness.orchestration.multi.state import MultiAgentState

# Checkpointer 数据库路径。
# 图每执行完一个节点，自动把 state 写进这个 SQLite 文件。
# 同一 thread_id 的后续调用从这个文件恢复 state（messages 累积、results 保留）。
# 必须用 AsyncSqliteSaver：graph.ainvoke 走 async checkpoint API，同步版不兼容。
# 开发用 SQLite；生产可换 PostgresSaver。


def edge_router(state: MultiAgentState) -> str:
    """条件边路由：读 state["next"]，决定下一站。

    返回的字符串必须是 targets dict 的 key（"sql"、"analysis"、"done" 等）。
    不能直接返回 END（"__end__"）——targets 里没有这个 key 会抛 KeyError。
    通过 targets["done"] → END 间接映射。
    """
    return state.get("next") or "done"
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
    builder.add_node(AGENT_DATA_QUALITY, node_data_quality)
    builder.add_node(AGENT_SQL, node_sql)
    builder.add_node("confidence_gate", node_confidence_gate)
    builder.add_node(AGENT_STRATEGY, node_strategy)
    builder.add_node(AGENT_HBASE, node_hbase)
    builder.add_node(AGENT_HIVE, node_hive)
    builder.add_node(AGENT_ANALYSIS, node_analysis)
    builder.add_node("reflection", node_reflection)

    builder.set_entry_point("router")

    # targets: edge_router 返回值 → LangGraph 节点名的映射
    targets = {
        "router": "router",  # 失败重规划：Agent 节点失败后回 Router 重排计划
        "clarify": "clarify",
        AGENT_DATA_QUALITY: AGENT_DATA_QUALITY,
        AGENT_SQL: AGENT_SQL,
        AGENT_STRATEGY: AGENT_STRATEGY,
        AGENT_HBASE: AGENT_HBASE,
        AGENT_HIVE: AGENT_HIVE,
        AGENT_ANALYSIS: AGENT_ANALYSIS,
        "confidence_gate": "confidence_gate",
        "reflection": "reflection",
        "done": END,
    }

    builder.add_conditional_edges("router", edge_router, targets)
    builder.add_conditional_edges("clarify", edge_router, targets)
    builder.add_conditional_edges(AGENT_DATA_QUALITY, edge_router, targets)
    builder.add_conditional_edges(AGENT_SQL, edge_router, targets)
    builder.add_conditional_edges(AGENT_STRATEGY, edge_router, targets)
    builder.add_conditional_edges(AGENT_HBASE, edge_router, targets)
    builder.add_conditional_edges(AGENT_HIVE, edge_router, targets)
    builder.add_conditional_edges("confidence_gate", edge_router, targets)
    builder.add_conditional_edges(AGENT_ANALYSIS, edge_router, targets)
    builder.add_conditional_edges("reflection", edge_router, targets)

    return builder.compile(checkpointer=checkpointer)
