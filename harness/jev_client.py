"""Jev（TypeSafe AI）决策模型客户端。

Jev 是 TypeSafe AI 2026-09 发布的「System One 模型」：**不生成文本**，而是
「状态(state) + 类型化问题(questions) → 带校准概率的结构化答案」，用于替换
代码里的**判断节点**（路由选择、置信度评估等），比自回归 LLM 快/便宜约两个量级。

与本项目其它 provider 一致的设计原则：
- 未配置 ``JEV_API_KEY`` → :func:`is_enabled` 返回 False，调用方走原有 LLM 路径（零侵入）。
- 任何异常/超时 → :func:`decide` 返回 None，调用方回退。**永不因为 Jev 不可用而打断主流程。**

⚠️ 关于接口形态：Jev 尚未完全开源，下面的 endpoint 与字段名是按公开资料推断的，
   接入前请以 TypeSafe 官方文档为准核对（见 ``JEV_BASE_URL`` 与 :func:`decide` 的 payload）。
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# 默认端点（待官方文档核对；可用 JEV_BASE_URL 覆盖）
_DEFAULT_BASE_URL = "https://api.typesafe.ai/v1"


def _api_key() -> str:
    return os.getenv("JEV_API_KEY", "").strip()


def _base_url() -> str:
    return (os.getenv("JEV_BASE_URL", "").strip() or _DEFAULT_BASE_URL).rstrip("/")


def is_enabled() -> bool:
    """是否配置了 Jev（配置了才会启用；否则调用方走 LLM 回退）。"""
    return bool(_api_key())


def _post_json(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    """独立的 HTTP 出口——便于测试 monkeypatch，不真的联网。"""
    import httpx

    resp = httpx.post(url, json=payload, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


async def decide(
    state: str,
    questions: list[dict],
    *,
    timeout: float = 10.0,
) -> dict | None:
    """向 Jev 提一组类型化问题，返回结构化答案（含概率）。

    Args:
        state: 状态文本（例如「用户问题 + 待评估的 SQL」拼成的上下文）。
        questions: 类型化问题列表，每项形如::

            {"id": "sql_reliable", "type": "scale", "min": 0, "max": 1,
             "prompt": "这条 SQL 有多大把握正确回答用户问题？"}
            {"id": "next_agent", "type": "choice",
             "options": ["sql", "hbase", "hive", "strategy", "clarify"],
             "prompt": "这条问题该交给哪个 Agent？"}
            {"id": "should_pause", "type": "boolean", "prompt": "是否应暂停请用户确认？"}

    Returns:
        解析后的结构化答案 dict；未配置 / 失败 / 超时一律返回 None（调用方回退）。
    """
    if not is_enabled():
        return None

    payload: dict[str, Any] = {"state": state, "questions": questions}
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }
    url = f"{_base_url()}/decide"
    try:
        return _post_json(url, payload, headers, timeout)
    except Exception as e:  # noqa: BLE001 - 任何失败都回退，不打断主流程
        logger.warning("jev: 决策调用失败，回退到原路径: %s: %s", type(e).__name__, e)
        return None


def extract_probability(result: dict | None, question_id: str) -> float | None:
    """从 Jev 返回里取出某个问题的概率（0~1）。取不到返回 None。

    容忍多种返回形态：{"answers":[{"id":...,"probability":0.82}]} /
    {"answers":[{...}]} / {"sql_reliable": {"probability": 0.82}}。
    """
    if not isinstance(result, dict):
        return None

    answers = result.get("answers")
    if isinstance(answers, list):
        for a in answers:
            if isinstance(a, dict) and a.get("id") == question_id:
                p = a.get("probability", a.get("confidence", a.get("score")))
                if isinstance(p, (int, float)):
                    return max(0.0, min(1.0, float(p)))
        return None

    node = result.get(question_id)
    if isinstance(node, dict):
        p = node.get("probability", node.get("confidence", node.get("score")))
        if isinstance(p, (int, float)):
            return max(0.0, min(1.0, float(p)))
    return None


def extract_choice(result: dict | None, question_id: str) -> str | None:
    """从 Jev 返回里取出某个"选择题"的答案（字符串）。取不到返回 None。"""
    if not isinstance(result, dict):
        return None

    def _pick(node: dict) -> str | None:
        ch = node.get("choice", node.get("answer", node.get("value")))
        return str(ch) if ch is not None else None

    answers = result.get("answers")
    if isinstance(answers, list):
        for a in answers:
            if isinstance(a, dict) and a.get("id") == question_id:
                return _pick(a)
        return None

    node = result.get(question_id)
    if isinstance(node, dict):
        return _pick(node)
    if isinstance(node, str):
        return node
    return None
