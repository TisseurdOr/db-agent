"""LangFuse LLM 可观测性接入层（与 Opik 并存，默认关闭）。

和 Opik 是同一类东西（trace + token 用量 + 成本 + 延迟），
这里只埋 LLM 调用这一层（acall_with_retry 是唯一入口，9 处 caller 全走它），
一次调用 = 一个 generation observation（model / input / output / usage / 延迟 / 错误）。

环境变量：
    LANGFUSE_ENABLED=1
    LANGFUSE_PUBLIC_KEY=pk-lf-...
    LANGFUSE_SECRET_KEY=sk-lf-...
    LANGFUSE_HOST=http://localhost:3000
"""

from __future__ import annotations

import logging
import os
from contextlib import nullcontext
from typing import Any

logger = logging.getLogger(__name__)

_client: Any | None = None


def langfuse_enabled() -> bool:
    return os.getenv("LANGFUSE_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}


def get_langfuse() -> Any | None:
    """返回 LangFuse 单例；未启用或未安装返回 None。"""
    global _client
    if not langfuse_enabled():
        return None
    if _client is not None:
        return _client
    try:
        from langfuse import Langfuse
    except ImportError:
        logger.warning("LANGFUSE_ENABLED=1 但未安装 langfuse，跳过上报。uv add langfuse")
        return None
    _client = Langfuse(
        public_key=os.getenv("LANGFUSE_PUBLIC_KEY", ""),
        secret_key=os.getenv("LANGFUSE_SECRET_KEY", ""),
        base_url=os.getenv("LANGFUSE_HOST", "http://localhost:3000"),
    )
    return _client


def reset_langfuse() -> None:
    global _client
    _client = None


def start_generation(model: str | None, llm_input: Any) -> Any:
    """开始一次 LLM generation observation；未启用时返回 no-op context manager。

    用法（在 async 函数里）:
        async with start_generation(model, messages) as gen:
            result = await fn(...)
            if gen is not None:
                gen.update(output=..., usage_details=...)
    """
    lf = get_langfuse()
    if lf is None:
        return nullcontext()
    try:
        return lf.start_as_current_observation(
            as_type="generation",
            name=f"llm:{model or 'unknown'}",
            model=model,
            input=llm_input,
        )
    except Exception as exc:
        logger.debug("start_generation 失败，跳过: %s", exc)
        return nullcontext()


def extract_output(result: Any) -> Any:
    """从 LLM 响应里取文本，避免整个对象序列化失败/过大。"""
    content = getattr(result, "content", None)
    if isinstance(content, list):
        texts = [b.text for b in content if getattr(b, "type", None) == "text"]
        if texts:
            return "\n".join(texts)
    return result


def usage_details(result: Any) -> dict[str, int] | None:
    """anthropic Usage → LangFuse usage_details（OpenAI 风格 input/output）。"""
    usage = getattr(result, "usage", None)
    if usage is None:
        return None
    return {
        "input": getattr(usage, "input_tokens", 0) or 0,
        "output": getattr(usage, "output_tokens", 0) or 0,
    }


def flush_langfuse() -> None:
    lf = get_langfuse()
    if lf is not None:
        try:
            lf.flush()
        except Exception:
            pass
