# main.py
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

from anthropic import Anthropic
import agent
from agent import agent_loop
from db.seed import init_db

# 注册所有 Tool
from tools.schema import LIST_TABLES_TOOL, DESCRIBE_TABLE_TOOL, list_tables, describe_table
from tools.query import RUN_QUERY_TOOL, run_query
from tools.analysis import ANALYZE_RESULTS_TOOL, analyze_results

agent.TOOLS = [
    LIST_TABLES_TOOL,
    DESCRIBE_TABLE_TOOL,
    RUN_QUERY_TOOL,
    ANALYZE_RESULTS_TOOL,
]
agent.TOOL_HANDLERS = {
    "list_tables": list_tables,
    "describe_table": describe_table,
    "run_query": run_query,
    "analyze_results": analyze_results,
}

# SYSTEM_PROMPT = open("prompts/system_prompt.md", encoding="utf-8").read()


async def main():
    # 确保数据库有数据
    init_db()

    client = Anthropic(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url=os.environ["ANTHROPIC_BASE_URL"],
    )

    print("数据库助手 Agent 已启动。输入 'quit' 退出。")
    print("试试这些：")
    print("  - 有哪些表？")
    print("  - 上周哪个部门销售额最高？")
    print("  - 各状态的订单数量和金额分别是多少？")
    print()

    messages_history = []  # 多轮对话记忆

    while True:
        user_input = input("\n你: ").strip()
        if user_input.lower() == "quit":
            break
        if not user_input:
            continue

        # 构建 messages（含历史）
        messages_history.append({"role": "user", "content": user_input})

        # 调 Agent Loop
        result = await agent_loop(
            client=client,
            user_message=user_input,  # 简化版：只传当前消息
            # system_prompt=SYSTEM_PROMPT,
        )
        print(f"\nAgent: {result}")

        messages_history.append({"role": "assistant", "content": result})


asyncio.run(main())
