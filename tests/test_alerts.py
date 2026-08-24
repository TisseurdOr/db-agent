"""告警模块测试。

覆盖：
- 默认只写日志（无 Webhook 不发网络请求）
- 配置 Webhook 后后台 POST JSON
- 熔断器打开自动触发告警（retry.py 集成）
"""

import json
import logging
import time

import pytest

from harness.observation.alerts import AlertNotifier


def test_send_logs_without_webhook(caplog):
    with caplog.at_level(logging.WARNING, logger="db-agent.alerts"):
        AlertNotifier(webhook_url="", enabled=True).send("测试告警", "内容", level="warning")
    assert any("测试告警" in r.message for r in caplog.records)


def test_no_network_without_webhook(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(
        "harness.observation.alerts.urllib.request.urlopen",
        lambda *a, **k: called.__setitem__("n", called["n"] + 1),
    )
    AlertNotifier(webhook_url="", enabled=True).send("t", "m")
    assert called["n"] == 0


def test_webhook_posted_in_background(monkeypatch):
    posted = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b""

    def fake_urlopen(req, timeout=5):
        posted["url"] = req.full_url
        posted["data"] = json.loads(req.data.decode("utf-8"))
        return FakeResp()

    monkeypatch.setattr("harness.observation.alerts.urllib.request.urlopen", fake_urlopen)
    AlertNotifier(webhook_url="http://hook.example/alert", enabled=True).send(
        "标题", "内容", level="critical", tags={"breaker": "llm"},
    )

    # Webhook 在后台线程发送，轮询等待
    for _ in range(100):
        if posted:
            break
        time.sleep(0.02)
    assert posted["url"] == "http://hook.example/alert"
    assert posted["data"]["title"] == "标题"
    assert posted["data"]["level"] == "critical"
    assert posted["data"]["tags"]["breaker"] == "llm"


def test_webhook_failure_does_not_raise(monkeypatch, caplog):
    def boom(*a, **k):
        raise ConnectionError("network down")

    monkeypatch.setattr("harness.observation.alerts.urllib.request.urlopen", boom)
    # 不应抛异常——告警失败不能影响主流程
    AlertNotifier(webhook_url="http://hook", enabled=True).send("t", "m")
    time.sleep(0.1)  # 给后台线程时间
    assert True


# ═══ 集成：熔断器打开 → 告警 ════════════════════════════════════════


@pytest.mark.asyncio
async def test_breaker_open_triggers_alert(monkeypatch):
    from harness.constraints.retry import (
        acall_with_retry,
        get_circuit_breaker,
        reset_circuit_breaker,
    )

    reset_circuit_breaker()
    monkeypatch.setenv("CIRCUIT_BREAKER_THRESHOLD", "2")
    alerts = []
    monkeypatch.setattr(
        "harness.observation.alerts.send_alert",
        lambda title, message, level="warning", tags=None: alerts.append((title, level, tags)),
    )

    def failing():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await acall_with_retry(failing, max_retries=0)
    with pytest.raises(RuntimeError):
        await acall_with_retry(failing, max_retries=0)

    assert get_circuit_breaker().state == "open"
    assert len(alerts) == 1  # 只发一次（打开那一瞬间）
    title, level, tags = alerts[0]
    assert "熔断" in title
    assert level == "critical"
    assert tags["breaker"] == "llm"
