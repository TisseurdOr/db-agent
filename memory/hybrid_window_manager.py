import os

from memory.token_budget import TokenBudget

# 窗口压缩用便宜快模型；默认 DeepSeek Flash（勿写死 Claude，DeepSeek endpoint 调不通）
DEFAULT_COMPRESS_MODEL = "deepseek-v4-flash"


class HybridWindowManager:
    """滑动窗口 + 分层摘要 + Token 预算联动。

    层级:
      Layer 0: 最近 6 条 — 原文不动（当前上下文）
      Layer 1: 第 7-14 条 — 轻量摘要（2-3 句话/条）
      Layer 2: 更早 — 对话级摘要（一段话概括）
    """

    def __init__(self, client, budget: TokenBudget, compress_model: str | None = None):
        self.client = client
        self.budget = budget
        self.compress_model = compress_model or os.getenv(
            "COMPRESS_MODEL", DEFAULT_COMPRESS_MODEL
        )

    async def manage(self, messages: list[dict]) -> tuple[list[dict], str]:
        """返回 (压缩后的消息列表, 注入 System Prompt 的摘要块)。"""
        if not self.budget.should_compress(messages):
            return messages, ""

        # Layer 1: 保留最近 6 条原文
        layer0 = messages[-6:] if len(messages) > 6 else messages
        middle = messages[-14:-6] if len(messages) > 14 else messages[6:-6] if len(messages) > 6 else []
        old = messages[:-14] if len(messages) > 14 else []

        # Layer 2: 中间层逐条压缩
        mid_summaries = []
        if middle:
            for i in range(0, len(middle), 2):  # 每 2 条消息压缩一次
                pair = middle[i:i+2]
                if pair:
                    s = await self._compress_pair(pair)
                    mid_summaries.append(s)

        # Layer 3: 早期消息全局摘要
        old_summary = ""
        if old:
            old_summary = await self._compress_to_summary(old)

        # 构建注入文本
        context_block = ""
        if old_summary:
            context_block += f"[早期对话 - 共 {len(old)} 条]\n{old_summary}\n\n"
        if mid_summaries:
            context_block += f"[中间过程]:\n" + "\n".join(f"- {s}" for s in mid_summaries) + "\n"

        return layer0, context_block

    async def _compress_pair(self, messages: list[dict]) -> str:
        resp = self.client.messages.create(
            model=self.compress_model,
            max_tokens=100,
            messages=[{"role": "user", "content": f"用一句话总结（中文）: {str(messages)[:500]}"}],
        )
        return resp.content[0].text.strip()

    async def _compress_to_summary(self, messages: list[dict]) -> str:
        resp = self.client.messages.create(
            model=self.compress_model,
            max_tokens=300,
            system="请用中文提炼对话要点，保留关键决策、数据、偏好。200 字符内。",
            messages=[{"role": "user", "content": str(messages)[:3000]}],
        )
        return resp.content[0].text.strip()