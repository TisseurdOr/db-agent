"""LangGraph 图构建 + 编译。

用法:
    from multi_agent.graph import build_graph
    agent = build_graph(client, model, system_prompt, tools, handlers)
    result = agent.invoke({"messages": [{"role": "user", "content": "..."}]})
"""

from langgraph.graph import StateGraph, END
from .state import DBAgentState
from .nodes import make_node_think, make_node_execute_tool, node_sql_review


def router(state: DBAgentState) -> str:
    return state.get("next_step", "think")


def build_graph(client, model, system_prompt, tools, handlers):
    """构建并编译 LangGraph Agent。

    所有外部依赖通过参数注入——不读环境变量、不 import 全局配置。
    """
    node_think = make_node_think(client, model, system_prompt, tools)
    node_execute_tool = make_node_execute_tool(handlers)

    builder = StateGraph(DBAgentState)

    builder.add_node("think", node_think)
    builder.add_node("execute_tool", node_execute_tool)
    builder.add_node("sql_review", node_sql_review)

    builder.set_entry_point("think")

    builder.add_conditional_edges("think", router, {
        "execute_tool": "execute_tool",
        "done": END,
    })
    builder.add_edge("execute_tool", "think")
    builder.add_conditional_edges("sql_review", router, {
        "think": "think",
        "execute": "execute_tool",
    })

    return builder.compile()
