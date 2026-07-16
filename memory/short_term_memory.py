# short_term_memory.py
from anthropic import Anthropic

SUMMARY_PROMPT = """Summarize this conversation snippet concisely.
Keep: key decisions, user preferences, data mentioned, actions taken.
Drop: greetings, filler, exact tool call details.
Output in Chinese, under 200 characters."""

async def compress_history(client: Anthropic, old_messages: list, model="claude-haiku-3-5"):
    """将旧消息压缩为一段摘要。用 Haiku——压缩任务不需要 Sonnet。"""
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
    return resp.content[0].text

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

    def token_estimate(self) -> int:
        """估算当前占用的 token 数。"""
        # 简化估算：1 字符 ≈ 0.4 token（中文），1 字符 ≈ 0.25 token（英文）
        msg_text = str(self.messages)
        summary_text = self.summary
        return int(len(msg_text) * 0.4 + len(summary_text) * 0.4)