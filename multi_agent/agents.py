from .state import DBAgentState
from .nodes import node_think, node_execute_tool, node_sql_review
from langgraph.graph import StateGraph, END
# Router: 根据 next_step 决定走向
def router(state: DBAgentState) -> str:
    return state.get("next_step", "think")


# 构建 Graph
builder = StateGraph(DBAgentState)

# 加节点
builder.add_node("think", node_think)
builder.add_node("execute_tool", node_execute_tool)
builder.add_node("sql_review", node_sql_review)

# 加边
builder.set_entry_point("think")
builder.add_conditional_edges("think", router, {
    "execute_tool": "execute_tool",
    "done": END
})
builder.add_edge("execute_tool", "think")  # 执行完自动回 think
builder.add_conditional_edges("sql_review", router, {
    "think": "think",
    "execute": "execute_tool"
})

# 编译
agent = builder.compile()