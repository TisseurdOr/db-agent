"""LLM 响应处理工具 + 错误日志。

解决一个反复出现的坑：带思考的模型（DeepSeek reasoner、Claude thinking 等）
返回的 content[0] 可能是 ThinkingBlock，它没有 .text 属性，
直接写 resp.content[0].text 就会 AttributeError 崩掉。

extract_text() 统一处理：遍历 content 取第一个 text 块；取不到就写一条日志
（logs/db-agent.log）并返回空串兜底，绝不让整个 agent 因为取文字失败而崩。
"""

import logging
from pathlib import Path

# 日志目录放项目根下的 logs/，不存在就建。
_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)

# 全项目共用一个 logger，避免重复加 handler（模块被多次 import 时）。
logger = logging.getLogger("db-agent")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    _fh = logging.FileHandler(_LOG_DIR / "db-agent.log", encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(_fh)


def extract_text(resp, context: str = "") -> str:
    """从 LLM 响应里安全地取文字内容。

    带思考的模型 content[0] 可能是 ThinkingBlock（无 .text），
    死取 content[0].text 会崩。这里遍历 content 取第一个 type=="text" 的块。

    取不到文字块时：记一条 warning 到 logs/db-agent.log（含 context 和实际的
    block 类型，方便排查），返回空串兜底。

    Args:
        resp: Anthropic messages.create() 的返回对象
        context: 调用来源标记（如 "hyde"、"rerank"），只用于日志定位
    """
    blocks = getattr(resp, "content", None) or []
    for block in blocks:
        if getattr(block, "type", None) == "text":
            return block.text

    # 没有文字块——记录下来，方便回头看是哪个调用、返回了什么类型
    block_types = [getattr(b, "type", "?") for b in blocks]
    logger.warning(
        "extract_text 未找到 text 块 (context=%s, blocks=%s)",
        context or "unknown",
        block_types,
    )
    return ""
