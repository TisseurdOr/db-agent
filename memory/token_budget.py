# 精确计数用 tiktoken，这里用工程估算（够用且零依赖）
def estimate_tokens(text: str) -> int:
    """粗糙但快的 token 估算。
    中文: ~1.5 chars/token
    英文: ~0.75 words/token (~4 chars/token)
    """
    chinese_chars = sum(1 for c in text if '一' <= c <= '鿿')
    other_chars = len(text) - chinese_chars
    return int(chinese_chars / 1.5 + other_chars / 4)

# 为什么要自己算而不是全依赖 API？
# 两个原因：
# 1. 在发请求前就要知道会不会超——不能等 API 报错
# 2. API 返回的 usage 是消费记录，你需要的是预算工具


class TokenBudget:
    """主动管理对话窗口的令牌预算。

    不是等窗口满了被动截断——而是持续监控、提前压缩。
    """

    def __init__(self, max_tokens: int = 50000, warn_threshold: float = 0.7):
        self.max_tokens = max_tokens
        self.warn_threshold = warn_threshold  # 70% 就预警
        self.system_prompt_tokens = 0
        self.tool_defs_tokens = 0

    def set_fixed_costs(self, system_prompt: str, tool_defs: list[dict]):
        """设置固定消耗（System Prompt + Tool Defs）。"""
        self.system_prompt_tokens = estimate_tokens(system_prompt)
        tool_text = str(tool_defs)
        self.tool_defs_tokens = estimate_tokens(tool_text)

    def current_usage(self, messages: list[dict]) -> int:
        """当前消息列表的总 token 估算。"""
        return estimate_tokens(str(messages))

    def available(self, messages: list[dict]) -> int:
        """还剩多少 token 空间。"""
        used = self.current_usage(messages) + self.system_prompt_tokens + self.tool_defs_tokens
        return max(0, self.max_tokens - used - 4000)  # 留 4000 给 output

    def should_compress(self, messages: list[dict]) -> bool:
        """是否该触发压缩。"""
        used = self.current_usage(messages) + self.system_prompt_tokens + self.tool_defs_tokens
        return used > self.max_tokens * self.warn_threshold

    def summary(self, messages: list[dict]) -> str:
        """返回可读的预算摘要。"""
        used = self.current_usage(messages)
        total = used + self.system_prompt_tokens + self.tool_defs_tokens
        pct = total / self.max_tokens * 100
        return (
            f"Token Budget: {total:,}/{self.max_tokens:,} ({pct:.0f}%) | "
            f"System: {self.system_prompt_tokens:,} | "
            f"Tools: {self.tool_defs_tokens:,} | "
            f"Messages: {used:,} | "
            f"Available: {self.available(messages):,}"
        )