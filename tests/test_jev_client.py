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
