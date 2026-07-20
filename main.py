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
import re
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from anthropic import Anthropic
from db.seed import init_db
from prompts.system_prompt import build_system_prompt
from memory.short_term_memory import ConversationManager
from memory.vector_store import VectorMemory
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
from tools.chart import render_chart
from tools.knowledge import (
    search_knowledge_base, save_to_memory, read_memory, search_memory,
    set_vector_memory, set_llm_client,
)

TOOLS = [
    LIST_TABLES_TOOL,
    DESCRIBE_TABLE_TOOL,
    GET_SCHEMA_SUMMARY_TOOL,
    RUN_QUERY_TOOL,
    ANALYZE_RESULTS_TOOL,
    COMPARE_PERIODS_TOOL,
    render_chart.tool_schema,
    search_knowledge_base.tool_schema,
    save_to_memory.tool_schema,
    read_memory.tool_schema,
    search_memory.tool_schema,
]

TOOL_HANDLERS = {
    "list_tables": list_tables,
    "describe_table": describe_table,
    "get_schema_summary": get_schema_summary,
    "run_query": run_query,
    "analyze_results": analyze_results,
    "compare_periods": compare_periods,
    "render_chart": render_chart,
    "search_knowledge_base": search_knowledge_base,
    "save_to_memory": save_to_memory,
    "read_memory": read_memory,
    "search_memory": search_memory,
}


async def main():
    parser = argparse.ArgumentParser(description="自然语言数据库分析 Agent")
    parser.add_argument(
        "--model", "-m",
        default=os.getenv("ANTHROPIC_MODEL", "deepseek-chat"),
        help="模型名 (默认: deepseek-chat，复杂查询可用 deepseek-reasoner)",
    )
    parser.add_argument(
        "--mode",
        choices=["single", "multi"],
        default="single",
        help="Agent 模式: single (单 Agent) / multi (多 Agent 编排)",
    )
    parser.add_argument(
        "--no-dq",
        action="store_true",
        help="多 Agent 模式下关闭首次 DataQuality 检查",
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

    # 对话记忆管理器——最近 N 轮保留原文，更早的压缩成摘要。
    # 摘要由 streaming_agent 每轮调用前注入 System Prompt，实现跨轮上下文记忆。
    conversation = ConversationManager(client)

    # 长期记忆：VectorMemory（remember / recall）。
    # pre-turn recall: 每轮自动注入 System Prompt 做上下文 priming
    # search_memory Tool: Agent 可主动调用，实现 Agentic RAG
    vector_memory = VectorMemory(collection_name="conversations")
    set_vector_memory(vector_memory)  # 注入给 search_memory Tool
    set_llm_client(client)            # Self-Query 拆解用

    print(f"数据分析 Agent 已启动（模型: {args.model}, 模式: {args.mode}）")
    print("试试这些：")
    print("  - 有哪些表？查一下整体结构")
    print("  - 上周哪个部门销售额最高？")
    print("  - 各状态的订单数量和金额分别是多少？")
    print("  - 分析一下销售趋势")
    print("输入 'quit' 退出\n")

    from agent import streaming_agent

    GREETING_MARKERS = ("你好", "您好", "hi", "hello", "你是谁", "介绍下",
                    "介绍一下", "自我介绍", "在吗", "谢谢", "再见")

    # 元问题——问"之前做了什么"而非问数据本身。
    # 这类 query 和原始数据查询语义零重叠，向量检索必然失败。
    # 解决：检索走时间倒序（list_recent），不写 remember（防污染）。
    # 覆盖：「刚才我问了什么」「我上一个问题是什么」「刚才查了什么」等。
    META_QUESTION_RE = re.compile(
        r"(刚才|上次|之前|上一个).{0,8}(问了|查了|问题)|"
        r"(问了什么|查了什么|聊了什么|做过什么|查过什么|问过什么|还记得)"
    )
    # 记忆正文若本身是元问答，注入时跳过——否则「最近一条」常是污染过的元问答。
    META_MEMORY_RE = re.compile(
        r"^问:\s*.{0,20}(刚才|上次|之前|上一个).{0,8}(问了|查了|问题)"
    )

    def is_chitchat(q: str) -> bool:
        q = q.strip().lower()
        return any(m in q for m in GREETING_MARKERS)

    def is_meta_question(q: str) -> bool:
        """检测'元问题'——问对话历史本身而非业务数据。"""
        return bool(META_QUESTION_RE.search(q.strip()))

    def is_meta_memory(text: str) -> bool:
        """记忆是否为元问答（不应再当作「上一个业务问题」）。"""
        return bool(META_MEMORY_RE.search((text or "").strip()))

    while True:
        user_input = input("\n你: ").strip()
        if user_input.lower() == "quit":
            break
        if not user_input:
            continue

        # 记忆检索：对话开始时先 recall，再把结果注入本轮 System Prompt。
        # 闲聊跳过，避免无意义 embedding。
        # 元问题（"刚才查了什么"）走时间倒序——语义检索对这类 query 必然失败。
        if is_chitchat(user_input):
            memories = []
        elif is_meta_question(user_input):
            # 按时间倒序；丢掉元问答自身，只保留最近业务问答
            memories = [
                m for m in vector_memory.list_recent(limit=20)
                if not is_meta_memory(m.get("text", ""))
            ][:3]
        else:
            memories = vector_memory.recall(user_input, top_k=3)
            # 分数阈值：相似度 < 0.3 的结果不注入，避免噪声误导模型
            memories = [m for m in memories if m.get("score", 0) >= 0.3]
        memories_text = "\n\n".join(m["text"] for m in memories)
        meta_hint = ""
        if is_meta_question(user_input) and memories:
            meta_hint = (
                "\n[提示] 用户在问「上一次/刚才问了什么」。"
                "请以下面时间最近的一条业务问答为准回答，不要编造更早的话题。\n"
            )
        turn_prompt = build_system_prompt(
            db_type="sqlite",
            user_role="数据分析师",
            extra_context=(
                f"{meta_hint}[历史对话]\n{memories_text}" if memories_text else ""
            ),
        )

        # 调统一 agent loop——streaming + cache_control + tool 结果可视化。
        # - conversation: 早期对话摘要，由 streaming_agent 每轮注入 System Prompt
        # - history: 最近几轮原文，拼在当前消息前，让第二轮能引用第一轮结果
        # 取 history 要在 add_message 之前——此刻 messages 只含"上一轮及更早"。
        if args.mode == "multi":
            from multi_agent.orchestrator import MultiAgentRunner
            runner = MultiAgentRunner(
                client, model=args.model,
                enable_data_quality=not args.no_dq,
            )
            result = await runner.run(user_input)
            print(f"\nAgent: {result}")
        else:
            result = await streaming_agent(
                client=client,
                user_msg=user_input,
                system_prompt=turn_prompt,
                tools=TOOLS,
                handlers=TOOL_HANDLERS,
                model=args.model,
                conversation=conversation,
                history=list(conversation.messages),
            )

        # 多 Agent 模式是一次性执行，不累积对话记忆
        if args.mode != "multi":
            await conversation.add_message({"role": "user", "content": user_input})
            await conversation.add_message({"role": "assistant", "content": result})
            if not is_meta_question(user_input):
                vector_memory.remember(
                    content=f"问: {user_input}\n答: {result}",
                    memory_type="conversation",
                    metadata={"year": str(datetime.now().year)},
                )
            est = conversation.token_estimate()
            print(
                f"[memory] 本轮 tokens≈{est['total']} "
                f"(原文 {est['recent']}/{est['recent_msgs']}条, "
                f"摘要 {est['summary']}/{est['compressed_msgs']}条已压缩)"
            )


if __name__ == "__main__":
    asyncio.run(main())
