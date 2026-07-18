from typing import TypedDict, Annotated
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages

class DBAgentState(TypedDict):
    messages: Annotated[list, add_messages]  # 对话历史（自动追加）
    tool_results: list
    sql_to_review: str   # 需要 review 的 SQL（安全节点用）