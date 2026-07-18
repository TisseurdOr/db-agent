from typing import TypedDict, Annotated
from langgraph.graph.message import add_messages


class DBAgentState(TypedDict):
    messages: Annotated[list, add_messages]
    tool_results: list
    sql_to_review: str
    next_step: str
