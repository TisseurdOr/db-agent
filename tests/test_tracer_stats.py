"""trace 聚合统计（--stats）纯函数测试。"""

from harness.observation.tracer import _compute_stats


def _span(node, error=None):
    s = {"node": node, "task": "x", "elapsed": 1.0, "total_tokens": 10}
    if error:
        s["error"] = error
    return s


def _trace(spans, blocked_by=None, elapsed=2.0):
    return {
        "trace_id": "t1",
        "query": "q",
        "elapsed": elapsed,
        "totals": {"total_tokens": 100},
        "spans": spans,
        "blocked_by": blocked_by,
    }


def test_stats_counts_errors_and_rate():
    traces = [
        _trace([_span("router"), _span("sql", error="表不存在")]),
        _trace([_span("router"), _span("analysis")]),
        _trace([_span("router"), _span("sql")]),
    ]
    stats = _compute_stats(traces)
    assert stats["total"] == 3
    assert stats["errored"] == 1
    assert round(stats["error_rate"], 3) == round(1 / 3, 3)
    assert stats["node_calls"]["sql"] == 2
    assert stats["node_errors"]["sql"] == 1
    assert stats["node_errors"].get("analysis") is None
    assert stats["top_errors"][0][0] == "表不存在"


def test_stats_guardrail_blocked():
    traces = [_trace([_span("router")], blocked_by="注入检测")]
    stats = _compute_stats(traces)
    assert stats["blocked"] == 1
    assert stats["errored"] == 0


def test_stats_empty():
    stats = _compute_stats([])
    assert stats["total"] == 0
    assert stats["error_rate"] == 0.0
    assert stats["avg_elapsed"] == 0.0
    assert stats["top_errors"] == []
