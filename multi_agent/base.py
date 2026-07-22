"""BaseAgent — 专业 Agent 的一次性执行单元。

不依赖 LangGraph。orchestrator 把 task 丢给 Agent，Agent 跑完 tool loop 返回文本结果。
"""

import os
import time
from typing import Optional
from anthropic import Anthropic


class AgentRunError(Exception):
    """Agent 执行失败。"""


def _short_input(tool_input: dict) -> str:
    """把 tool input 压缩成一行摘要（用于进度打印）。"""
    parts = []
    for k, v in tool_input.items():
        s = str(v)
        if len(s) > 60:
            s = s[:57] + "..."
        parts.append(f"{k}={s}")
    return ", ".join(parts)


async def _simple_agent_run(
    client: Anthropic,
    user_msg: str,
    system_prompt: str,
    tools: list,
    handlers: dict,
    model: str = "deepseek-chat",
    max_turns: int = 8,
    verbose: bool = False,
) -> tuple[str, dict]:
    """轻量 Agent loop：和 agent.py 类似，但不依赖 ConversationManager 等上层实例。

    Args:
        client: Anthropic SDK client
        user_msg: 发给 Agent 的任务
        system_prompt: Agent 的 System Prompt
        tools: Tool definitions
        handlers: Tool handler 映射 {name: callable}
        model: 模型名
        max_turns: 最大 tool 调用轮数
        verbose: 是否打印 tool 调用进度

    Returns:
        (最终文本回复, {"input_tokens": N, "output_tokens": N, "turns": N})
    """
    messages = [{"role": "user", "content": user_msg}]
    usage = {"input_tokens": 0, "output_tokens": 0, "turns": 0}

    for _ in range(max_turns):
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system_prompt,
            messages=messages,
            tools=tools,
        )
        # 累计 token
        if hasattr(response, "usage") and response.usage:
            usage["input_tokens"] += response.usage.input_tokens or 0
            usage["output_tokens"] += response.usage.output_tokens or 0

        # 检查是否调用了 Tool
        tool_uses = [
            block for block in response.content if block.type == "tool_use"
        ]
        if not tool_uses:
            # 纯文本回复 → 结束
            text_blocks = [
                block.text for block in response.content if block.type == "text"
            ]
            usage["turns"] += 1
            return "\n".join(text_blocks), usage

        usage["turns"] += 1
        # 执行 Tool
        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for tc in tool_uses:
            t0 = time.monotonic()
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
            elapsed = time.monotonic() - t0
            if verbose:
                summary = str(content)[:80].replace("\n", " ")
                print(f"  🔧 {tc.name}({_short_input(tc.input)}) → {summary} ({elapsed:.1f}s)")
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": content,
            })
        messages.append({"role": "user", "content": tool_results})

    return f"(Agent 在 {max_turns} 轮内未完成)", usage


# 超时检测——orchestrator 用这个判断 agent 是否正常完成
def is_agent_timeout(result: str) -> bool:
    """检测 agent 返回文本是否为超时/未完成标记。"""
    return result.startswith("(Agent 在")


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
        verbose: bool = False,
    ) -> tuple[str, dict]:
        """执行一次任务，返回 (文本结果, usage)。

        usage: {"input_tokens": N, "output_tokens": N, "turns": N}
        context 非空时拼在 task 前面。
        """
        user_msg = f"[上下文]\n{context}\n\n[任务]\n{task}" if context else task
        return await _simple_agent_run(
            client=client,
            user_msg=user_msg,
            system_prompt=self.system_prompt,
            tools=self.tools,
            handlers=self.handlers,
            model=model or os.getenv("ANTHROPIC_MODEL", "deepseek-chat"),
            verbose=verbose,
        )
