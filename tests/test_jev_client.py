"""Jev（TypeSafe System One 决策模型）适配层的测试。

覆盖：
- 未配置 JEV_API_KEY 时 is_enabled=False、decide 返回 None（调用方回退）
- 配置后能发请求并解析结构化答案（monkeypatch 掉 HTTP 出口，不联网）
- 概率/选项抽取对多种返回形态的容忍
- Router / 置信度门优先用 Jev，取不到则回退
"""

import asyncio

import pytest

from harness import jev_client


@pytest.fixture(autouse=True)
def _clear_jev_env(monkeypatch):
    """每个用例先清空 Jev 相关环境变量，避免 .env 里的 key 影响断言。"""
    for var in ("OPENROUTER_API_KEY", "JEV_API_KEY", "JEV_MODEL", "JEV_BASE_URL"):
        monkeypatch.delenv(var, raising=False)


def test_disabled_without_key(monkeypatch):
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    assert jev_client.is_enabled() is False
    assert asyncio.run(jev_client.decide("state", [])) is None


def test_decide_parses_structured_answer(monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "test-key")
    captured = {}

    def fake_post(url, payload, headers, timeout):
        captured.update(url=url, payload=payload, headers=headers)
        return {"answers": [{"id": "next_agent", "choice": "hbase", "probability": 0.91}]}

    monkeypatch.setattr(jev_client, "_post_json", fake_post)
    result = asyncio.run(jev_client.decide("HBase 里查订单", [
        {"id": "next_agent", "type": "choice", "options": ["sql", "hbase"]},
    ]))
    assert result["answers"][0]["choice"] == "hbase"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["payload"]["state"] == "HBase 里查订单"


def test_decide_returns_none_on_http_failure(monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "test-key")

    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(jev_client, "_post_json", boom)
    assert asyncio.run(jev_client.decide("x", [])) is None  # 失败 → 回退，不抛


@pytest.mark.parametrize("payload,expected", [
    ({"answers": [{"id": "c", "probability": 0.8}]}, 0.8),
    ({"answers": [{"id": "c", "confidence": 0.6}]}, 0.6),
    ({"c": {"probability": 0.7}}, 0.7),
    ({"answers": [{"id": "other", "probability": 0.9}]}, None),
    (None, None),
])
def test_extract_probability(payload, expected):
    assert jev_client.extract_probability(payload, "c") == expected


@pytest.mark.parametrize("payload,expected", [
    ({"answers": [{"id": "a", "choice": "sql"}]}, "sql"),
    ({"answers": [{"id": "a", "value": "hive"}]}, "hive"),
    ({"a": "strategy"}, "strategy"),
    ({"answers": [{"id": "z", "choice": "sql"}]}, None),
])
def test_extract_choice(payload, expected):
    assert jev_client.extract_choice(payload, "a") == expected


# ── OpenRouter 通道（让 Jev 走 OpenRouter，不动主 LLM）──

def test_openrouter_takes_priority(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("JEV_API_KEY", "native-test")
    assert jev_client._provider() == "openrouter"


def test_openrouter_posts_chat_completions(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    captured = {}

    def fake_post(url, payload, headers, timeout):
        captured.update(url=url, payload=payload, headers=headers)
        return {"choices": [{"message": {"content": '{"answers":[{"id":"next_agent","choice":"hive","probability":0.77}]}'}}]}

    monkeypatch.setattr(jev_client, "_post_json", fake_post)
    result = asyncio.run(jev_client.decide("Hive 语法怎么写", [
        {"id": "next_agent", "type": "choice", "options": ["sql", "hive"]},
    ]))

    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["payload"]["model"] == "typesafe/jev-router"
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert captured["headers"]["Authorization"] == "Bearer sk-or-test"
    assert jev_client.extract_choice(result, "next_agent") == "hive"
    assert jev_client.extract_probability(result, "next_agent") == 0.77


def test_openrouter_model_overridable(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("JEV_MODEL", "typesafe/jev-latest")
    captured = {}

    def fake_post(url, payload, headers, timeout):
        captured.update(payload=payload)
        return {"choices": [{"message": {"content": "{}"}}]}

    monkeypatch.setattr(jev_client, "_post_json", fake_post)
    asyncio.run(jev_client.decide("x", [{"id": "a", "type": "boolean", "prompt": "?"}]))
    assert captured["payload"]["model"] == "typesafe/jev-latest"


def test_openrouter_malformed_response_returns_none(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr(jev_client, "_post_json", lambda *a, **k: {"choices": []})
    assert asyncio.run(jev_client.decide("x", [])) is None
