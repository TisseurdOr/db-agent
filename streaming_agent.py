from anthropic import Anthropic
import inspect
import json

async def streaming_agent(client, user_msg, system_prompt, tools, handlers):
    messages = [{"role": "user", "content": user_msg}]

    for turn in range(10):
        # 用于积累 streaming 中的 tool_use 和 text
        tool_use_blocks = {}   # {index: {"name": ..., "input": ""}}
        text_content = ""

        with client.messages.stream(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=system_prompt,
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
                            "input": ""
                        }
                        # 实时通知用户：Agent 正在调什么工具
                        print(f"\n🔧 {block.name}...", end="", flush=True)

                elif event.type == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        print(delta.text, end="", flush=True)
                        text_content += delta.text

                    elif delta.type == "input_json_delta":
                        # tool 参数是逐片到达的 JSON
                        idx = event.index
                        tool_use_blocks[idx]["input"] += delta.partial_json

                elif event.type == "content_block_stop":
                    pass  # 这个 block 结束了

            # stream 结束后获取完整 message
            final_msg = stream.get_final_message()

        # 检查是否需要执行 tool
        tool_uses = [b for b in final_msg.content if b.type == "tool_use"]

        if not tool_uses:
            print()  # 换行
            return text_content

        # 显示 tool 完整参数
        for tc in tool_uses:
            print(f"\n   → 参数: {json.dumps(tc.input, ensure_ascii=False)}")

        # 加入历史
        messages.append({"role": "assistant", "content": final_msg.content})

        # 执行 tools
        tool_results = []
        for tc in tool_uses:
            result = handlers[tc.name](**tc.input)
            # 兼容 sync / async handler
            if inspect.isawaitable(result):
                result = await result
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": json.dumps(result, ensure_ascii=False),
            })

        messages.append({"role": "user", "content": tool_results})
        # 继续下一轮

    return "max turns reached"