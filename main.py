# main.py — CLI 入口
#
# 启动流程：
#   1. init_db() —— 确保 SQLite demo.db 有数据
#   2. 注册 Tools + Tool Handlers
#   3. 用 prompts/system_prompt.py 工厂函数生成 System Prompt
#   4. 创建 Anthropic client（支持 DeepSeek 兼容 endpoint）
#   5. 进入 CLI 对话循环 → 调 agent.streaming_agent()
#
# 设计决策：
#   - 用 agent.streaming_agent 而非旧 streaming_agent.py：
#     agent.py 是合并后的统一实现，有 cache_control、temperature=0、
#     结构化错误处理、Tool 结果可视化。streaming_agent.py 是旧版本，已废弃。
#   - System Prompt 用 Python 工厂函数而非 MD 文件：
#     可注入 db_type, user_role, extra_context（Phase 3 memory block 注入点）。
#   - --model 参数：支持在命令行切换模型，方便测试 Haiku vs Sonnet。

import asyncio
import argparse
import os
from dotenv import load_dotenv

load_dotenv()

from anthropic import Anthropic
from db.seed import init_db
from prompts.system_prompt import build_system_prompt

# 注册所有 Tool —— Tool defs 和 handler 在这里绑定，
# 传给 agent.streaming_agent() 时作为一个整体。
# 好处：测试时可以传 mock handler，main.py 传真实 handler，agent.py 不感知。
from tools.schema import (
    LIST_TABLES_TOOL, list_tables,
    DESCRIBE_TABLE_TOOL, describe_table,
    GET_SCHEMA_SUMMARY_TOOL, get_schema_summary,
)
from tools.query import RUN_QUERY_TOOL, run_query
from tools.analysis import (
    ANALYZE_RESULTS_TOOL, analyze_results,
    COMPARE_PERIODS_TOOL, compare_periods,
)
from tools.knowledge import search_knowledge_base, save_to_memory, read_memory

TOOLS = [
    LIST_TABLES_TOOL,
    DESCRIBE_TABLE_TOOL,
    GET_SCHEMA_SUMMARY_TOOL,
    RUN_QUERY_TOOL,
    ANALYZE_RESULTS_TOOL,
    COMPARE_PERIODS_TOOL,
    search_knowledge_base.tool_schema,
    save_to_memory.tool_schema,
    read_memory.tool_schema,
]

TOOL_HANDLERS = {
    "list_tables": list_tables,
    "describe_table": describe_table,
    "get_schema_summary": get_schema_summary,
    "run_query": run_query,
    "analyze_results": analyze_results,
    "compare_periods": compare_periods,
    "search_knowledge_base": search_knowledge_base,
    "save_to_memory": save_to_memory,
    "read_memory": read_memory,
}


async def main():
    parser = argparse.ArgumentParser(description="自然语言数据库分析 Agent")
    parser.add_argument(
        "--model", "-m",
        default=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        help="模型名 (默认: claude-sonnet-4-6，简单查询可用 claude-haiku-3-5)",
    )
    args = parser.parse_args()

    init_db()

    # Anthropic SDK 初始化——base_url 和 api_key 从 .env 读。
    # 如果用 DeepSeek 兼容 endpoint：.env 里设
    #   ANTHROPIC_BASE_URL=https://api.deepseek.com/v1
    #   ANTHROPIC_API_KEY=sk-xxx
    # SDK 的 messages.stream() 需要 endpoint 支持 SSE streaming。
    client = Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        base_url=os.environ.get("ANTHROPIC_BASE_URL"),
    )

    # System Prompt 用工厂函数生成——可注入 db_type, user_role 变量。
    # Phase 3 接入后，extra_context 参数会传入 memory block + RAG 检索结果。
    system_prompt = build_system_prompt(db_type="sqlite", user_role="数据分析师")

    print(f"数据分析 Agent 已启动（模型: {args.model}）")
    print("试试这些：")
    print("  - 有哪些表？查一下整体结构")
    print("  - 上周哪个部门销售额最高？")
    print("  - 各状态的订单数量和金额分别是多少？")
    print("  - 分析一下销售趋势")
    print("输入 'quit' 退出\n")

    while True:
        user_input = input("\n你: ").strip()
        if user_input.lower() == "quit":
            break
        if not user_input:
            continue

        # 调统一 agent loop——streaming + cache_control + tool 结果可视化。
        # 来自 agent.py（已合并 agent_loop + streaming_agent）。
        from agent import streaming_agent
        await streaming_agent(
            client=client,
            user_msg=user_input,
            system_prompt=system_prompt,
            tools=TOOLS,
            handlers=TOOL_HANDLERS,
            model=args.model,
        )


if __name__ == "__main__":
    asyncio.run(main())
