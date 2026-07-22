# 把 SQL Agent 改成子图：用 StateGraph + ToolNode + should_continue 替代 ConfiguredAgent。


from typing import Annotated, TypedDict
from langgraph.graph import START, StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from utils.llm import llm_with_tools
from tools.query import RUN_QUERY_TOOL, run_query
from tools.schema import (
    LIST_TABLES_TOOL, list_tables,
    DESCRIBE_TABLE_TOOL, describe_table,
)
def build_sql_agent():
    '''SQL Agent 子图: 只查数据库，有tool loop的独立图'''
    class SqlAgentState(TypedDict):
        messages: Annotated[list, add_messages]


    builder = StateGraph(SqlAgentState)

    #LLM 节点：决定是否调用tool
    async def call_model(state: SqlAgentState) -> dict:
        response = await llm_with_tools.ainvoke(state['messages'])
        return {'messages': [response]}
    
    #Tool 节点：执行tool
    tool_node = ToolNode(tools=[list_tables, describe_table, run_query])

    def should_continue(state: SqlAgentState) -> str:
        last = state['messages'][-1]
        if hasattr(last, 'tool_calls') and last.tool_calls:
            return 'tools'
        return END 


    builder.add_node(START, 'call_model')
    builder.add_node("call_model", call_model)
    builder.add_node('tools', tool_node)
    builder.add_conditional_edges('call_model', should_continue,{'tools': 'tools', END : END})
    builder.add_edge('tools', 'call_model')

    return builder.compile()