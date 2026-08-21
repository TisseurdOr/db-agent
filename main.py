# main.py — CLI 入口
#
# 启动流程：
#   1. init_db() —— 确保 SQLite demo.db 有数据
#   2. 注册 Tools + Tool Handlers
#   3. 用 prompts/system_prompt.py 工厂函数生成 System Prompt
#   4. 创建 Anthropic client（支持 DeepSeek 兼容 endpoint）
#   5. 进入 CLI 对话循环 → 调 streaming_agent()
#
# 设计决策：
#   - 用 harness.orchestration.single.streaming_agent：
#     single_agent/agent.py 是统一实现，有 cache_control、temperature=0、
#     结构化错误处理、Tool 结果可视化。旧版 streaming_agent.py 已迁到 archive/。
#   - System Prompt 用 Python 工厂函数而非 MD 文件：
#     可注入 db_type, user_role, extra_context（Phase 3 memory block 注入点）。
#   - --model 参数：支持在命令行切换模型，方便测试 Haiku vs Sonnet。

import asyncio
import argparse
import difflib
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from anthropic import Anthropic
from db.seed import init_db
from harness.context.system_prompt import build_system_prompt
from harness.memory.short_term_memory import ConversationManager
from harness.memory.vector_store import VectorMemory
from harness.memory.memory_controller import (
    is_chitchat, is_meta_question, is_meta_memory,
    should_vector_recall, should_remember,
)
from harness.tools.knowledge import set_vector_memory, set_llm_client
from harness.tools.hbase import _seed_hbase_store
from harness.context.template_matcher import get_template_matcher, init_metric_registry
from harness.context.schema_discovery import get_schema_discovery
from harness.orchestration.single.tools_bundle import TOOLS, TOOL_HANDLERS

# 用户输入
#   → main.py: 闲聊跳过 / 元问题走 list_recent / 正常走向量 recall
#   → runner.run(query, memories_text)
#       → graph.ainvoke(state)
#           → node_router: 读 messages 历史 + query → 输出 plan
#           → node_sql: 执行 SQL → 结果写 results
#           → node_analysis: 读向量记忆 + messages + 中间结果 → 综合回答
#           → 返回 {final_answer, messages: [AIMessage]}
#       → Checkpointer 自动存 state 到 agent_state.db
#   → main.py: vector_memory.remember(Q&A pair)
#   → 打印 token 日志

# 退出指令。Agent 链路没有"结束进程"的能力——模型只能回一句"会话结束"，
# 循环还在跑，所以退出必须在这里拦下来，不能交给 Router。
EXIT_COMMANDS = {"quit", "exit", "q", ":q", "bye", "退出", "结束会话", "再见", "拜拜"}


def _exit_intent(text: str) -> str:
    """判断输入是否为退出指令：exit=确定退出，maybe=疑似错字，no=正常提问。"""
    cleaned = text.strip().strip(" 。，！？.,!?、").lower()
    if not cleaned:
        return "no"
    if cleaned in EXIT_COMMANDS:
        return "exit"
    if len(cleaned) <= 5 and "退出" in cleaned:
        return "exit"
    # 拼错的 quit/exit 不能直接退出（怕误杀会话），但也别送进 Agent 白烧 token
    if len(cleaned) <= 6 and cleaned.isascii() and difflib.get_close_matches(
        cleaned, ("quit", "exit"), n=1, cutoff=0.6
    ):
        return "maybe"
    return "no"


async def main():
    parser = argparse.ArgumentParser(description="自然语言数据库分析 Agent")
    parser.add_argument(
        "--model", "-m",
        default=os.getenv("ANTHROPIC_MODEL", "deepseek-chat"),
        help="模型名 (默认: deepseek-chat，也可用 deepseek-v4-flash / deepseek-v4-pro)",
    )
    parser.add_argument(
        "--mode",
        choices=["single", "multi"],
        default="single",
        help="Agent 模式: single (单 Agent) / multi (多 Agent 编排)",
    )
    parser.add_argument(
        "--dq",
        action="store_true",
        help="多 Agent 模式下开启首次 DataQuality 检查",
    )
    parser.add_argument(
        "--user",
        default=os.getenv("AGENT_USER", "viewer"),
        help="用户名 (默认: viewer，也可用 analyst / xiaoyiming ...)",
    )
    args = parser.parse_args()
    os.environ["AGENT_USER"] = args.user

    init_db()
    _seed_hbase_store()
    init_metric_registry()  # 初始化业务指标模板库
    try:
        get_schema_discovery().build_index()  # 预计算表字段 embedding
    except Exception as e:
        print(f"[schema_discovery] 索引构建跳过: {e}")

    # Anthropic SDK 初始化——base_url 和 api_key 从 .env 读。
    # 如果用 DeepSeek 兼容 endpoint：.env 里设
    #   ANTHROPIC_BASE_URL=https://api.deepseek.com/v1
    #   ANTHROPIC_API_KEY=sk-xxx
    # SDK 的 messages.stream() 需要 endpoint 支持 SSE streaming。
    client = Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        base_url=os.environ.get("ANTHROPIC_BASE_URL"),
    )
    from harness.observation.opik_tracing import wrap_anthropic_client
    client = wrap_anthropic_client(client)

    # 对话记忆管理器——最近 N 轮保留原文，更早的压缩成摘要。
    # 摘要由 streaming_agent 每轮调用前注入 System Prompt，实现跨轮上下文记忆。
    conversation = ConversationManager(client)

    # 长期记忆：VectorMemory（remember / recall）。
    # pre-turn recall: 每轮自动注入 System Prompt 做上下文 priming
    # search_memory Tool: Agent 可主动调用，实现 Agentic RAG
    vector_memory = VectorMemory(collection_name="conversations")
    set_vector_memory(vector_memory)  # 注入给 search_memory Tool
    set_llm_client(client)            # Self-Query 拆解用

    print(f"数据分析 Agent 已启动（模型: {args.model}, 模式: {args.mode}, 用户: {args.user}）")
    print("试试这些：")
    print("  [SQL]   上周哪个部门销售额最高？")
    print("  [Hive]  Hive 里有哪些表？帮我查 ods_orders_hive 分区情况")
    print("  [HBase] 用 HBase scan orders 表，限制 10 行")
    print("  [多引擎] SQL和hive有什么表")
    print("  [分析]  对比本月和上月订单量变化并给建议")
    print("  [制度]  销售人员的提成比例是多少")
    print("输入 quit / 退出 结束会话（Ctrl+C / Ctrl+D 同样有效）\n")

    from harness.orchestration.single import streaming_agent

    # ── multi 模式：进程内只建一次 Runner ──
    # Checkpointer 靠 thread_id 识别"同一本笔记本"——
    # 每次循环都 new Runner 会导致新的 SQLite 连接和新的 thread 上下文，
    # 之前的 messages 历史就丢了。所以在循环前创建一次，每轮复用。
    # 内部用 AsyncSqliteSaver，state 存到 db/agent_state.db，进程重启后还在。
    multi_runner = None
    if args.mode == "multi":
        from harness.orchestration.multi.orchestrator import MultiAgentRunner
        multi_runner = await MultiAgentRunner.create(
            client, model=args.model,
            enable_data_quality=args.dq,
        )
        print(f"Checkpointer: {multi_runner.checkpoint_db}")

    try:
        while True:
            try:
                user_input = input("\n你: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n已退出。")
                break
            if not user_input:
                continue
            intent = _exit_intent(user_input)
            if intent == "exit":
                print("已退出。")
                break
            if intent == "maybe":
                print("没识别出这条输入。想退出请输入 quit，或重新描述你的问题。")
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

            # ── 模板优先匹配：命中则注入预填 SQL 到上下文，LLM 可选用或覆盖 ──
            template_matcher = get_template_matcher()
            template_result = template_matcher.match(user_input)
            template_hint = ""
            if template_result.matched:
                template_hint = (
                    f"\n[SQL 模板匹配] 指标「{template_result.metric_name}」命中，"
                    f"预填 SQL: {template_result.sql}"
                )
                if template_result.caveats:
                    template_hint += f"\n注意事项: {template_result.caveats}"
                template_hint += "\n你可以直接执行此 SQL，或根据需要调整后执行。\n"

            extra_parts = []
            if meta_hint:
                extra_parts.append(meta_hint)
            if template_hint:
                extra_parts.append(template_hint)
            if memories_text:
                extra_parts.append(f"[历史对话]\n{memories_text}")

            turn_prompt = build_system_prompt(
                db_type="sqlite",
                user_role="数据分析师",
                extra_context=("\n".join(extra_parts) if extra_parts else ""),
            )

            # ── 执行 Agent ──
            # multi 模式：走 LangGraph 多 Agent 编排（Router → SQL/Strategy/DQ → Analysis）。
            #   向量记忆通过 recalled_memories 参数传入，由 node_analysis 注入 Analysis Agent 上下文。
            # single 模式：走 streaming_agent 单 Agent loop（tool 调用 + cache_control）。
            if args.mode == "multi":
                # 短期记忆压缩：ConversationManager 把超窗口消息压成摘要，
                # 注入 node_analysis 作为 Layer 1 上下文（_conversation_summary）。
                context = conversation.build_context()
                result = await multi_runner.run(
                    user_input,
                    recalled_memories=memories_text,
                    conversation_summary=context,
                )

                # HITL 审批：graph 在敏感 SQL 处暂停，等待人工确认
                if isinstance(result, dict) and result.get("__interrupt__"):
                    interrupt_data = result["data"]
                    print(f"\n{'='*50}")
                    print(f"⚠️  敏感查询需要审批")
                    print(f" 👩‍💻👨‍💻🧑‍💻用户: {interrupt_data.get('user', '?')}")
                    print(f"  🤖SQL:  {interrupt_data.get('sql', '?')}")
                    print(f"{'='*50}")
                    choice = input("  是否继续执行? (y/n): ").strip().lower()
                    approved = choice == "y"
                    result = await multi_runner.resume(approved=approved)
                    # 自学习：用户批准的 SQL（敏感列 HITL / 置信度门）有人工背书
                    if approved and interrupt_data.get("sql"):
                        from harness.memory.feedback import learn_from_hitl
                        learn_from_hitl(user_input, interrupt_data.get("sql", ""))
                    print(f"\nAgent: {result}")
                else:
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

            # ── 记忆写入 ──
            # 短期记忆（ConversationManager）：两种模式共用——
            #   add_message 触发滑动窗口 + LLM 压缩，build_context() 在下一轮注入。
            #   multi 模式额外靠 LangGraph Checkpointer 持久化完整 messages 历史。
            await conversation.add_message({"role": "user", "content": user_input})
            await conversation.add_message({"role": "assistant", "content": result})
            # 长期记忆（VectorMemory）：两种模式共用——
            #   把本轮问答写入 ChromaDB，下次相关查询时以向量召回方式注入 System Prompt。
            #   元问题（"刚才问了什么"）不写——避免污染向量库。
            if should_remember(user_input):
                vector_memory.remember(
                    content=f"问: {user_input}\n答: {result}",
                    memory_type="conversation",
                    metadata={"year": str(datetime.now().year)},
                )
            # Token 日志：两种模式共用 ConversationManager 的估算
            est = conversation.token_estimate()
            checkpoint_info = ""
            if args.mode == "multi":
                checkpoint_info = f", checkpoint={multi_runner.checkpoint_db.name}"
                from harness.observation.opik_tracing import opik_tag_memory
                opik_tag_memory(est)
            print(
                f"[memory] 本轮 tokens≈{est['total']} "
                f"(原文 {est['recent']}/{est['recent_msgs']}条, "
                f"摘要 {est['summary']}/{est['compressed_msgs']}条已压缩{checkpoint_info})"
            )
    finally:
        # quit 前关掉 aiosqlite，避免 event loop 已关闭后 worker 线程回调报错
        if multi_runner is not None:
            await multi_runner.aclose()


if __name__ == "__main__":
    asyncio.run(main())
