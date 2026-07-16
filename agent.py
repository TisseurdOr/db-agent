# agent.py — 统一的 Agent Loop（streaming + cache_control + 错误处理）
#
# 之前有两个实现：agent.py 的 agent_loop（非 streaming, 有 cache_control）
# 和 streaming_agent.py（streaming, 无 cache_control, model 写死, 不打印 tool 结果）。
# 现在合并为一个——所有路径走 streaming，同时补齐 Phase 2 checklist 的硬要求。
#
# 设计决策：
#   temperature=0: Agent 场景需要确定性的 Tool 调用路径，不能随机选 Tool。
#   cache_control: System Prompt + Tool Defs 加了 ephemeral cache，cache hit
#       后 prompt tokens 从 $3/M 降到 ~$0.014/M，单条请求省 ~$0.004。
#   max_turns=10: 经验值。SQL 查询通常 3-5 轮，10 轮留足安全边际。
#   output token 上限 4096: streaming 场景放多一点，避免分析类回复被截断。
#
# 参考：Lesson 0004 (agent loop), 0006 (cache_control), 0010 (streaming + tool use)

import inspect
import json
import os
from anthropic import Anthropic, APIStatusError

# 不从模块级拿 TOOLS / TOOL_HANDLERS——tools 和 handlers 一律由调用方显式传入。
# 好处：
#   1. 不依赖全局可变状态——调用方传什么就用什么，不会因为 import 顺序出错
#   2. 测试友好——传 mock handler 不需要动模块级变量
#   3. 每个 agent 实例可以用不同的 Tool 组合，互不干扰
#
# 模型选择优先级: 参数 > 环境变量 > 默认值
DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_TURNS = 10


async def _execute_tool(name: str, tool_input: dict, handlers: dict) -> tuple[str, bool]:
    """执行一个 Tool，返回 (content, is_error)。

    不抛异常——Tool 失败是正常情况（SQL 写错、表不存在等），
    把错误信息返回给模型，让它自己纠正，比直接 crash agent loop 好。
    这是 lesson 0008 的"结构化错误返回"原则。
    """
    handler = handlers[name]
    try:
        result = handler(**tool_input)
        if inspect.isawaitable(result):
            result = await result
        return json.dumps(result, ensure_ascii=False), False
    except Exception as e:
        # 结构化错误：告诉模型发生了什么 + 建议下一步
        error_payload = {
            "error": str(e),
            "type": type(e).__name__,
            "suggestion": "检查 Tool 参数是否正确，或尝试其他 Tool",
        }
        return json.dumps(error_payload, ensure_ascii=False), True


def _build_cacheable_system(system_text: str) -> list[dict]:
    """给 System Prompt 包 cache_control。

    为什么：每轮 agent loop 都重复发送同样的 System Prompt + Tool Defs。
    Anthropic 的 prompt caching 让这些重复内容只按 cache hit 计费
    (~$0.014/M tokens，而 cache miss 是 $3/M)，节省 ~99.5% 的 system prompt 成本。

    必须标记至少 1024 tokens 的内容才能触发 cache。System Prompt + Tool Defs
    加在一起通常 1500-2500 tokens，符合条件。

    ephemeral: cache 生命周期 5 分钟，适合交互式对话场景。
    参考: https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
    """
    return [{
        "type": "text",
        "text": system_text,
        "cache_control": {"type": "ephemeral"},
    }]


async def streaming_agent(
    client: Anthropic,
    user_msg: str,
    system_prompt: str,
    tools: list[dict],
    handlers: dict,
    model: str = None,
    max_turns: int = MAX_TURNS,
    temperature: float = 0.0,
    show_tool_results: bool = True,
    conversation=None,
    history: list = None,
) -> str:
    """统一的 Agent Loop——streaming + cache_control + 工具调用可视化。

    Args:
        client: Anthropic client（已配置 base_url + api_key）
        user_msg: 当前用户消息
        system_prompt: System Prompt 基础文本（由 prompts/system_prompt.py 工厂函数生成，
            不含对话记忆；对话摘要在本函数内每轮注入）
        tools: Tool definitions 列表（必传——调用方决定注册哪些 Tool）
        handlers: Tool 名 → 实现函数的映射（必传——调用方负责绑定实现）
        model: 模型名，不传则用环境变量 ANTHROPIC_MODEL 或默认 sonnet
        max_turns: 最大推理轮数，防止死循环
        temperature: Agent 场景固定 0——Tool 调用需要确定性，不能随机
        show_tool_results: 是否在终端显示 Tool 调用过程和结果摘要
        conversation: 可选的 ConversationManager；传入后每轮调用前会把
            conversation.build_context()（早期对话摘要）注入 System Prompt
        history: 可选的历史消息列表（最近几轮的原文 user/assistant 消息）。
            传入后会拼在当前 user_msg 之前，让模型看到最近对话——
            这样第二轮问"其中..."时无需重新探索表结构。
    """
    if model is None:
        model = os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL)

    # 历史消息（最近几轮原文）+ 当前消息。history 为空时行为和以前一致。
    messages = list(history or []) + [{"role": "user", "content": user_msg}]

    for turn in range(max_turns):
        # 每轮调用前：把对话摘要注入 System Prompt，再包 cache_control。
        # build_context() 返回早期对话的压缩摘要（无摘要时为空串）。
        # 注意：同一次 streaming_agent 调用内 conversation 不会更新，
        # 所以每轮注入的内容一致——对 prompt cache 友好（内容相同即命中）。
        system_text = system_prompt
        if conversation is not None:
            context = conversation.build_context()
            if context:
                system_text = f"{system_prompt}\n\n---\n## 对话上下文\n{context}\n---"
        cached_system = _build_cacheable_system(system_text)

        # 用于积累 streaming 中到达的 tool_use 和 text
        tool_use_blocks = {}   # {content_block_index: {id, name, input_str}}
        text_content = ""

        # temperature=0 的理由：Agent 选 Tool 必须确定。
        # temperature > 0 时模型可能随机选一个不存在的 Tool，
        # 或者把本该调 run_query 的请求直接编一个回答——这在 Agent 场景不可接受。
        with client.messages.stream(
            model=model,
            max_tokens=4096,
            temperature=temperature,
            system=cached_system,
            messages=messages,
            tools=tools,
        ) as stream:
            for event in stream:
                if event.type == "content_block_start":
                    block = event.content_block
                    if block.type == "tool_use":
                        tool_use_blocks[event.index] = {
                            "id": block.id,
                            "name": block.name,
                            "input": "",
                        }
                        # 实时通知：Agent 正在调什么 Tool
                        print(f"\n🔧 {block.name}...", end="", flush=True)

                elif event.type == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        # 逐字输出——用户看到 Agent "在思考"，信任感来源
                        print(delta.text, end="", flush=True)
                        text_content += delta.text

                    elif delta.type == "input_json_delta":
                        # partial_json 是增量片段，不能解析——只能累积。
                        # 比如 {"city": "Beijing"} 可能分两次到达：
                        #   delta 1: '{"city": "Bei'
                        #   delta 2: 'jing"}'
                        idx = event.index
                        if idx in tool_use_blocks:
                            tool_use_blocks[idx]["input"] += delta.partial_json

                elif event.type == "content_block_stop":
                    pass  # 单个 block（text 或 tool_use）结束

            # stream 结束后，get_final_message() 返回完整、解析好的 message 对象
            final_msg = stream.get_final_message()

        # final_msg.content 里每个 block 的 .input 已经是完整的 Python dict，
        # 不需要再手动解析 JSON（SDK 在 stream 结束后帮我们 parse 了）
        tool_uses = [b for b in final_msg.content if b.type == "tool_use"]

        if not tool_uses:
            print()  # 换行——streaming 输出后收尾
            return text_content

        # 显示 Tool 完整参数（JSON 格式，一行，中文不转义）
        for tc in tool_uses:
            params = json.dumps(tc.input, ensure_ascii=False)
            print(f"\n   → 参数: {params}")

        # 把模型的 Tool 调用请求加入对话历史
        # 注意：必须传 final_msg.content（ToolUseBlock 对象列表），
        # 不能自己拼 dict——SDK 在下轮请求时需要 block 对象做类型判断。
        messages.append({"role": "assistant", "content": final_msg.content})

        # 执行每个 Tool，收集结果
        tool_results = []
        for tc in tool_uses:
            content, is_error = await _execute_tool(tc.name, tc.input, handlers)

            if show_tool_results:
                # 显示结果摘要，截断到 200 字符——太长会淹没终端输出
                marker = "❌" if is_error else "←"
                summary = content[:200] + ("..." if len(content) > 200 else "")
                print(f"   {marker} 结果: {summary}")

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": content,
                "is_error": is_error,
            })

        # Tool 结果以 user 角色加入历史
        # 这是 Anthropic API 的要求——tool_result 必须跟在 assistant(tool_use) 后面，
        # 以 user 角色发送。顺序不对会报 400。
        messages.append({"role": "user", "content": tool_results})

    return "已达到最大轮次"


# 保留非 streaming 版本——给不想看 streaming 输出的集成测试用。
# 实现和 streaming_agent 一致（cache_control, 错误处理, temperature=0），
# 只是不走 stream 事件，直接等 final response。
async def agent_loop(
    client: Anthropic,
    user_message: str,
    system_prompt: str,
    tools: list[dict],
    handlers: dict,
    model: str = None,
    max_turns: int = MAX_TURNS,
    temperature: float = 0.0,
) -> str:
    """非 streaming 版本——供测试和 batch 场景使用。"""
    if model is None:
        model = os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL)

    messages = [{"role": "user", "content": user_message}]
    cached_system = _build_cacheable_system(system_prompt)

    for turn in range(max_turns):
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            temperature=temperature,
            system=cached_system,
            messages=messages,
            tools=tools,
        )

        text_parts = []
        tool_calls = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(block)

        if not tool_calls:
            return "\n".join(text_parts)

        messages.append({
            "role": "assistant",
            "content": [b.to_dict() for b in response.content],
        })

        tool_results = []
        for tc in tool_calls:
            content, is_error = await _execute_tool(tc.name, tc.input, handlers)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": content,
                "is_error": is_error,
            })

        messages.append({"role": "user", "content": tool_results})

    return "已达到最大轮次"
