"""运维指标（ops_metrics）测试。"""

from fastapi.testclient import TestClient

from harness.observation.ops_metrics import (
    prometheus_text,
    record_elapsed,
    record_error,
    record_query,
    record_tokens,
    reset_metrics,
    snapshot,
)


def test_record_and_snapshot():
    reset_metrics()
    record_query(succeeded=True)
    record_query(succeeded=False)
    record_tokens(120)
    record_elapsed(3.5)
    record_error("sql", "超时")

    s = snapshot()
    assert s["queries_total"] == 2
    assert s["queries_succeeded"] == 1
    assert s["queries_failed"] == 1
    assert s["tokens_total"] == 120
    assert s["errors_by_node"] == {"sql": 1}
    assert s["error_rate"] == 0.5


def test_prometheus_text_format():
    reset_metrics()
    record_query(succeeded=True)
    record_tokens(10)
    text = prometheus_text()
    assert "dbagent_queries_total 1" in text
    assert "dbagent_tokens_total 10" in text
    assert text.endswith("\n")


def test_metrics_endpoint(monkeypatch):
    from server.main import app
    reset_metrics()
    record_query(succeeded=True)
    with TestClient(app) as c:
        resp = c.get("/api/metrics")
    assert resp.status_code == 200
    assert "dbagent_queries_total 1" in resp.text
