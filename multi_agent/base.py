"""BaseAgent — 专业 Agent 的一次性执行单元。

不依赖 LangGraph。orchestrator 把 task 丢给 Agent，Agent 跑完 tool loop 返回文本结果。
"""

import os
from typing import Optional
from anthropic import Anthropic


class AgentRunError(Exception):
    """Agent 执行失败。"""


async def _simple_agent_run(
    client: Anthropic,
    user_msg: str,
    system_prompt: str,
    tools: list,
    handlers: dict,
    model: str = "deepseek-chat",
    max_turns: int = 8,
) -> str:
    """轻量 Agent loop：和 agent.py 类似，但不依赖 ConversationManager 等上层实例。

    Args:
        client: Anthropic SDK client
        user_msg: 发给 Agent 的任务
        system_prompt: Agent 的 System Prompt
        tools: Tool definitions
        handlers: Tool handler 映射 {name: callable}
        model: 模型名
        max_turns: 最大 tool 调用轮数

    Returns:
        Agent 的最终文本回复
    """
    messages = [{"role": "user", "content": user_msg}]

    for _ in range(max_turns):
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system_prompt,
            messages=messages,
            tools=tools,
        )

        # 检查是否调用了 Tool
        tool_uses = [
            block for block in response.content if block.type == "tool_use"
        ]
        if not tool_uses:
            # 纯文本回复 → 结束
            text_blocks = [
                block.text for block in response.content if block.type == "text"
            ]
            return "\n".join(text_blocks)

        # 执行 Tool
        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for tc in tool_uses:
            handler = handlers.get(tc.name)
            if handler is None:
                content = f"错误: 未知 Tool '{tc.name}'"
            else:
                try:
                    import asyncio
                    if asyncio.iscoroutinefunction(handler):
                        content = str(await handler(**tc.input))
                    else:
                        content = str(handler(**tc.input))
                except Exception as e:
                    content = f"Tool 执行错误: {e}"
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": content,
            })
        messages.append({"role": "user", "content": tool_results})

    return f"(Agent 在 {max_turns} 轮内未完成)"


# ── 便捷构造: 按名称封装 prompt + tools + handlers ──

class ConfiguredAgent:
    """预配置的专业 Agent: prompt + tools + handlers 打包在一起。"""

    def __init__(self, name: str, system_prompt: str, tools: list, handlers: dict):
        self.name = name
        self.system_prompt = system_prompt
        self.tools = tools
        self.handlers = handlers

    async def run(
        self,
        client: Anthropic,
        task: str,
        context: str = "",
        model: Optional[str] = None,
    ) -> str:
        """执行一次任务，返回文本结果。context 非空时拼在 task 前面。"""
        user_msg = f"[上下文]\n{context}\n\n[任务]\n{task}" if context else task
        return await _simple_agent_run(
            client=client,
            user_msg=user_msg,
            system_prompt=self.system_prompt,
            tools=self.tools,
            handlers=self.handlers,
            model=model or os.getenv("ANTHROPIC_MODEL", "deepseek-chat"),
        )
