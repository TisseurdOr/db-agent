"""Jev（TypeSafe System One 决策模型）客户端。

Jev **不生成文本**：输入「状态(state) + 类型化问题(questions)」，输出**带校准概率的
结构化答案**。本项目用它替换**判断节点**（Router 选 Agent、SQL 置信度评估）。

支持两条通道（按环境变量自动选，互不干扰主 LLM）：

1. **OpenRouter**（推荐）：配置 ``OPENROUTER_API_KEY`` 即启用。
   ``https://openrouter.ai/api/v1/chat/completions``，模型 ``typesafe/jev-router``。
   → 只需要一个 OpenRouter key，不用单独申请 TypeSafe 账号。
2. **TypeSafe 原生 API**：配置 ``JEV_API_KEY`` 启用（``POST {JEV_BASE_URL}/decide``）。

降级原则（零侵入）：
- 两条通道都没配 → :func:`is_enabled` 为 False，调用方走原 LLM 路径。
- 任何异常/超时 → :func:`decide` 返回 None，调用方回退。**永不打断主流程。**
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_NATIVE_BASE = "https://api.typesafe.ai/v1"
_DEFAULT_OPENROUTER_BASE = "https://openrouter.ai/api/v1"
_DEFAULT_OPENROUTER_MODEL = "typesafe/jev-router"

_JSON_SYSTEM_PROMPT = (
    "你是决策模型 Jev，只做判断、不写文章。"
    "用户会给你一段状态和若干类型化问题。"
    "只输出一个 JSON 对象，形如 "
    '{"answers":[{"id":"<问题id>","choice":"<选项>","probability":<0~1>}]}，'
    "不要输出任何解释或多余文字。choice 问题给 choice，scale/boolean 问题给 probability。"
)


def _provider() -> str:
    """当前生效的通道：openrouter / native / none。

    OpenRouter 优先（只需一个 key，最省事）。
    """
    if os.getenv("OPENROUTER_API_KEY", "").strip():
        return "openrouter"
    if os.getenv("JEV_API_KEY", "").strip():
        return "native"
    return "none"


def is_enabled() -> bool:
    """是否配置了任一 Jev 通道（没配则调用方走 LLM 回退）。"""
    return _provider() != "none"


def _post_json(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    """独立的 HTTP 出口——便于测试 monkeypatch，不真的联网。"""
    import httpx

    resp = httpx.post(url, json=payload, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _extract_json(text: str) -> dict | None:
    """从模型输出的文本里抠出第一个 JSON 对象。"""
    if not text:
        return None
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        data = json.loads(match.group())
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _build_prompt(state: str, questions: list[dict]) -> str:
    """把「状态 + 类型化问题」拼成给 chat 模型的 prompt。"""
    lines = ["# 状态", state, "", "# 问题"]
    for q in questions:
        qid = q.get("id", "")
        qtype = q.get("type", "choice")
        prompt = q.get("prompt", "")
        if qtype == "choice":
            opts = "、".join(str(o) for o in (q.get("options") or []))
            lines.append(f'- id="{qid}"（choice，可选：{opts}）：{prompt}')
        elif qtype == "boolean":
            lines.append(f'- id="{qid}"（boolean）：{prompt}')
        else:  # scale
            lines.append(
                f'- id="{qid}"（scale {q.get("min", 0)}~{q.get("max", 1)}）：{prompt}'
            )
    lines.append("")
    lines.append("按系统提示只返回 JSON。")
    return "\n".join(lines)


def _decide_openrouter(state: str, questions: list[dict], timeout: float) -> dict | None:
    """走 OpenRouter 的 OpenAI 兼容 chat/completions。"""
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    base = (
        os.getenv("OPENROUTER_BASE_URL", "").strip() or _DEFAULT_OPENROUTER_BASE
    ).rstrip("/")
    model = os.getenv("JEV_MODEL", "").strip() or _DEFAULT_OPENROUTER_MODEL

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    # OpenRouter 建议带上来源标识（可选，利于排行榜统计）
    if os.getenv("OPENROUTER_SITE_URL", "").strip():
        headers["HTTP-Referer"] = os.getenv("OPENROUTER_SITE_URL", "").strip()
    if os.getenv("OPENROUTER_SITE_NAME", "").strip():
        headers["X-Title"] = os.getenv("OPENROUTER_SITE_NAME", "").strip()

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _JSON_SYSTEM_PROMPT},
            {"role": "user", "content": _build_prompt(state, questions)},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
    }
    data = _post_json(f"{base}/chat/completions", payload, headers, timeout)
    content = ""
    try:
        content = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return None
    parsed = _extract_json(content)
    if parsed is None:
        return None
    # 统一成与原生通道一致的 {answers: [...]} 形态
    if "answers" not in parsed:
        parsed = {"answers": [parsed]}
    return parsed


def _decide_native(state: str, questions: list[dict], timeout: float) -> dict | None:
    """走 TypeSafe 原生 /decide 接口。"""
    key = os.getenv("JEV_API_KEY", "").strip()
    base = (os.getenv("JEV_BASE_URL", "").strip() or _DEFAULT_NATIVE_BASE).rstrip("/")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload: dict[str, Any] = {"state": state, "questions": questions}
    return _post_json(f"{base}/decide", payload, headers, timeout)


async def decide(
    state: str,
    questions: list[dict],
    *,
    timeout: float = 10.0,
) -> dict | None:
    """向 Jev 提一组类型化问题，返回结构化答案（含概率）。

    questions 每项形如::

        {"id": "next_agent", "type": "choice",
         "options": ["sql", "hbase", "hive", "strategy", "clarify", "none"],
         "prompt": "这条问题该交给哪个 Agent？"}
        {"id": "sql_confidence", "type": "scale", "min": 0, "max": 1,
         "prompt": "这条 SQL 有多大把握正确回答用户问题？"}

    Returns:
        归一化为 ``{"answers": [...]}`` 的 dict；未配置/失败/超时 → None（调用方回退）。
    """
    provider = _provider()
    if provider == "none":
        return None
    try:
        if provider == "openrouter":
            return _decide_openrouter(state, questions, timeout)
        return _decide_native(state, questions, timeout)
    except Exception as e:  # noqa: BLE001 - 任何失败都回退，不打断主流程
        logger.warning("jev[%s]: 决策调用失败，回退到原路径: %s: %s", provider, type(e).__name__, e)
        return None


def extract_probability(result: dict | None, question_id: str) -> float | None:
    """从 Jev 返回里取出某个问题的概率（0~1）。取不到返回 None。"""
    if not isinstance(result, dict):
        return None

    def _num(node: dict) -> float | None:
        p = node.get("probability", node.get("confidence", node.get("score")))
        if isinstance(p, (int, float)):
            return max(0.0, min(1.0, float(p)))
        return None

    answers = result.get("answers")
    if isinstance(answers, list):
        for a in answers:
            if isinstance(a, dict) and a.get("id") == question_id:
                return _num(a)
        return None

    node = result.get(question_id)
    if isinstance(node, dict):
        return _num(node)
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
