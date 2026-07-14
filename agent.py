# agent.py
import inspect
import json
import os
from anthropic import Anthropic

# Tool 注册表（由 main.py 赋值）
TOOLS = []

# Tool 实现映射——模型说"调 xxx"，我们找到对应的函数执行
TOOL_HANDLERS = {}

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")


async def _run_tool(name: str, tool_input: dict) -> tuple[str, bool]:
    """执行 tool，返回 (content, is_error)。失败时不抛出，交给模型处理。"""
    handler = TOOL_HANDLERS[name]
    try:
        result = handler(**tool_input)
        # 兼容 sync / async Tool
        if inspect.isawaitable(result):
            result = await result
        return json.dumps(result, ensure_ascii=False), False
    except Exception as e:
        return f"Tool error: {type(e).__name__}: {e}", True


async def agent_loop(
    client: Anthropic,
    user_message: str,
    system_prompt: str,
    max_turns: int = 10,
):
    """核心 Agent Loop — Observe → Think → Act → Observe..."""

    messages = [{"role": "user", "content": user_message}]

    for turn in range(max_turns):
        # Think: 发请求给模型，带上所有 Tool 定义
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=system_prompt,
            messages=messages,
            tools=TOOLS,
        )

        # 分析模型的回复
        text_parts = []
        tool_calls = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(block)

        # 没有 tool call → 模型给出最终回复，结束
        if not tool_calls:
            return "\n".join(text_parts)

        # 有 tool call → 把模型的回复加入历史
        messages.append({
            "role": "assistant",
            "content": [b.to_dict() for b in response.content]
        })

        # Act: 执行每个 tool call，结果放入 messages
        tool_results = []
        for tc in tool_calls:
            content, is_error = await _run_tool(tc.name, tc.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": content,
                "is_error": is_error,
            })

        messages.append({"role": "user", "content": tool_results})

        # 循环回到 Think——模型看到 tool 结果后继续推理

    return "已达到最大轮次"


async def agent_loop_streaming(client, user_message, system_prompt, max_turns=10):
    messages = [{"role": "user", "content": user_message}]

    for turn in range(max_turns):
        # streaming 调用
        with client.messages.stream(
            model=MODEL,
            max_tokens=1024,
            system=system_prompt,
            messages=messages,
            tools=TOOLS,
        ) as stream:
            for event in stream:
                if event.type == "text":
                    print(event.text, end="", flush=True)  # 逐字输出

            # stream 结束后，获取完整的 final message
            final_msg = stream.get_final_message()

        # 检查是否有 tool calls
        tool_uses = [b for b in final_msg.content if b.type == "tool_use"]

        if not tool_uses:
            print()  # 换行
            return

        # 有 tool call——打印要做什么
        for tc in tool_uses:
            print(f"\n🔧----- 调用 {tc.name}({tc.input})...")

        # 加入历史
        messages.append({"role": "assistant", "content": final_msg.content})

        # 执行 tools
        tool_results = []
        for tc in tool_uses:
            content, is_error = await _run_tool(tc.name, tc.input)
            marker = "❌" if is_error else "→"
            print(f"   {marker} 结果: {content}")
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": content,
                "is_error": is_error,
            })

        messages.append({"role": "user", "content": tool_results})

    return "已达到最大轮次"
