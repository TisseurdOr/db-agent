# short_term_memory.py
import os
from anthropic import Anthropic

SUMMARY_PROMPT = """Summarize this conversation snippet concisely.
Keep: key decisions, user preferences, data mentioned, actions taken.
Drop: greetings, filler, exact tool call details.
Output in Chinese, under 200 characters."""

async def compress_history(client: Anthropic, old_messages: list, model=None):
    """将旧消息压缩为一段摘要。压缩任务不需要旗舰模型，用便宜的即可。"""
    if model is None:
        model = os.getenv("ANTHROPIC_MODEL", "claude-haiku-3-5")
    text = "\n".join([
        f"{'用户' if m['role']=='user' else '助手'}: {str(m['content'])[:500]}"
        for m in old_messages
    ])
    resp = client.messages.create(
        model=model,
        max_tokens=300,
        system=SUMMARY_PROMPT,
        messages=[{"role": "user", "content": text}],
    )
    # content 里可能混有 ThinkingBlock（带思考的模型）——只取第一个文字块，
    # 不能死取 content[0]，否则遇到 ThinkingBlock 会 AttributeError。
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            return block.text
    # 兜底：没有文字块（极少见）时返回空摘要，不让整个 agent 崩
    return ""

class ConversationManager:
    """混合策略：最近 10 条保留原文，更早的压缩为摘要。"""

    def __init__(self, client: Anthropic, max_recent=10, max_summary_tokens=500):
        self.client = client
        self.max_recent = max_recent
        self.max_summary_tokens = max_summary_tokens
        self.messages = []           # 最近的消息（原文）
        self.summary = ""            # 早期消息的摘要
        self._total_compressed = 0   # 总共压缩了多少轮

    async def add_message(self, message: dict):
        self.messages.append(message)

        # 超过窗口 → 压缩最旧的一半
        if len(self.messages) > self.max_recent:
            overflow = self.messages[:-self.max_recent]
            if overflow:
                new_summary = await compress_history(self.client, overflow)
                # 合并摘要
                if self.summary:
                    self.summary = f"{self.summary}\n{new_summary}"
                else:
                    self.summary = new_summary
                self._total_compressed += len(overflow)
            self.messages = self.messages[-self.max_recent:]

    def build_context(self) -> str:
        """构建注入 System Prompt 的上下文块。"""
        if not self.summary:
            return ""
        return (
            f"[早期对话摘要 - 共 {self._total_compressed} 轮]\n"
            f"{self.summary}\n"
            f"[最近对话如下]"
        )

    @staticmethod
    def _estimate(text: str) -> int:
        """粗略把字符数换算成 token：中文约 0.6/字，英文约 0.25/字，取 0.4 折中。"""
        return int(len(text) * 0.4)

    def token_estimate(self) -> dict:
        """估算当前上下文占用的 token（分:最近原文 / 早期摘要 / 合计）。

        只统计消息里真正的文本内容，不把 Python 列表的引号、括号算进去。
        返回 dict 便于打日志时展示分项。
        """
        recent_text = "".join(str(m.get("content", "")) for m in self.messages)
        recent = self._estimate(recent_text)
        summary = self._estimate(self.summary)
        return {
            "recent": recent,        # 最近 N 条原文
            "summary": summary,      # 早期对话摘要
            "total": recent + summary,
            "recent_msgs": len(self.messages),
            "compressed_msgs": self._total_compressed,
        }

    def token_count_exact(self, system: str = "", tools: list = None) -> dict:
        """精确版：调 Anthropic count_tokens 接口，拿真实 input_tokens。

        和 token_estimate 的区别：
        - estimate 是本地字符折算，零成本、零延迟，但只是近似。
        - exact 把 system + summary + messages 打包发给 API，返回服务端真实计费口径的
          token 数（含 system prompt、对话结构、特殊 token 的开销）。多一次网络往返。

        注意：count_tokens 是 Anthropic 官方端点特性，DeepSeek 等兼容端点不一定支持；
        调用失败时自动降级到 token_estimate，不让日志功能拖垮主流程。
        """
        # 把摘要拼进 system——和真实请求里注入上下文的方式保持一致，计数才准。
        system_text = system
        context = self.build_context()
        if context:
            system_text = f"{system}\n\n{context}" if system else context

        # count_tokens 要求至少一条消息；空对话时给个占位，避免接口报错。
        messages = self.messages or [{"role": "user", "content": ""}]

        try:
            kwargs = {
                "model": os.getenv("ANTHROPIC_MODEL", "claude-haiku-3-5"),
                "messages": messages,
            }
            if system_text:
                kwargs["system"] = system_text
            if tools:
                kwargs["tools"] = tools
            resp = self.client.messages.count_tokens(**kwargs)
            return {"total": resp.input_tokens, "source": "api"}
        except Exception as e:
            # 端点不支持 / 网络异常 → 退回本地估算，并标注来源方便排查。
            fallback = self.token_estimate()
            return {"total": fallback["total"], "source": f"estimate ({type(e).__name__})"}