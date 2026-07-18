from .state import DBAgentState
from .tools import TOOLS
from .config import client, model, system_prompt

# node_think: Agent 决定下一步做什么
def node_think(state: DBAgentState) -> dict:
    """LLM 分析当前状态，决定调用哪个 Tool 还是直接回复。"""
    response = client.messages.create(
        model=model,
        system=system_prompt,
        messages=state["messages"],
        tools=TOOLS,
    )
    # 检查模型是否要调 Tool
    if response.stop_reason == "tool_use":
        return {
            "messages": [{"role": "assistant", "content": response.content}],
            "next_step": "execute_tool"
        }
    return {
        "messages": [{"role": "assistant", "content": response.content}],
        "next_step": "done"
    }


# node_execute_tool: 执行 Tool 调用
def node_execute_tool(state: DBAgentState) -> dict:
    """执行模型要求的 Tool，结果追加回 messages。"""
    last_msg = state["messages"][-1]
    results = []
    for block in last_msg.get("content", []):
        if block["type"] == "tool_use":
            result = execute_tool(block["name"], block["input"])
            results.append({
                "type": "tool_result",
                "tool_use_id": block["id"],
                "content": str(result)
            })
    return {
        "messages": [{"role": "user", "content": results}],
        "tool_results": results,
        "next_step": "think"  # 回 think 节点继续
    }


# node_sql_review: 安全节点——SQL 执行前审查
def node_sql_review(state: DBAgentState) -> dict:
    """检查 SQL 是否安全、只读。"""
    if "DROP" in state["sql_to_review"].upper() or \
       "DELETE" in state["sql_to_review"].upper():
        return {
            "messages": [{"role": "user", "content": "SQL 包含危险操作，已阻断"}],
            "next_step": "think"  # 让模型重写
        }
    return {"next_step": "execute"}  # 安全，放行