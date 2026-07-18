"""LangGraph 节点函数 — 每个节点接收 state，返回部分更新。

不直接 import client/tools/handlers，而是通过闭包注入。
这样测试时可以传 mock，main.py 传真实对象。
"""

from .state import DBAgentState


def _execute_tool(name: str, args: dict, handlers: dict) -> str:
    """执行单个 Tool 调用。复用项目 agent.py 的 handler 绑定。"""
    handler = handlers.get(name)
    if handler is None:
        return f"错误: 未知 Tool '{name}'"
    try:
        import asyncio
        if asyncio.iscoroutinefunction(handler):
            result = asyncio.get_event_loop().run_until_complete(handler(**args))
        else:
            result = handler(**args)
        text = str(result)
        return text[:8000] if len(text) > 8000 else text
    except Exception as e:
        return f"Tool 执行错误: {e}"


def make_node_think(client, model, system_prompt, tools):
    """闭包：注入依赖，返回符合 LangGraph 签名的节点函数。"""

    def node_think(state: DBAgentState) -> dict:
        response = client.messages.create(
            model=model,
            system=system_prompt,
            messages=state["messages"],
            tools=tools,
        )
        if response.stop_reason == "tool_use":
            return {
                "messages": [{"role": "assistant", "content": response.content}],
                "next_step": "execute_tool",
            }
        return {
            "messages": [{"role": "assistant", "content": response.content}],
            "next_step": "done",
        }

    return node_think


def make_node_execute_tool(handlers):
    """闭包：注入 handlers。"""

    def node_execute_tool(state: DBAgentState) -> dict:
        last_msg = state["messages"][-1]
        results = []
        for block in last_msg.get("content", []):
            if block.get("type") == "tool_use":
                result = _execute_tool(block["name"], block["input"], handlers)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": result,
                })
        return {
            "messages": [{"role": "user", "content": results}],
            "tool_results": results,
            "next_step": "think",
        }

    return node_execute_tool


def node_sql_review(state: DBAgentState) -> dict:
    sql = (state.get("sql_to_review") or "").upper()
    if "DROP" in sql or "DELETE" in sql or "INSERT" in sql or "UPDATE" in sql or "ALTER" in sql:
        return {
            "messages": [{"role": "user", "content": "SQL 包含危险操作，已阻断。只允许 SELECT。"}],
            "next_step": "think",
        }
    return {"next_step": "execute"}
