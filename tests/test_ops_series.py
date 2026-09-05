"""ops_metrics 时间序列采样单测 —— 零依赖纯函数。"""

from harness.observation.ops_metrics import (
    record_query,
    record_tokens,
    reset_metrics,
    sample_series,
)


def test_first_sample_has_no_deltas():
    reset_metrics()
    record_query(succeeded=True)
    record_tokens(100)
    series = sample_series(force=True)
    assert len(series) == 1
    p = series[0]
    assert p["queries_total"] == 1
    assert p["queries_succeeded"] == 1
    assert p["d_queries"] == 0
    assert p["queries_rate"] == 0.0
    assert "_t" not in p  # 内部字段不外发


def test_deltas_between_samples():
    reset_metrics()
    sample_series(force=True)  # t0
    record_query(succeeded=True)
    record_query(succeeded=False)
    record_tokens(500)
    series = sample_series(force=True)  # t1
    assert len(series) == 2
    p = series[-1]
    assert p["d_queries"] == 2
    assert p["d_succeeded"] == 1
    assert p["d_failed"] == 1
    assert p["d_tokens"] == 500
    assert p["queries_total"] == 2
    assert p["queries_rate"] > 0


def test_interval_gating_without_force():
    """不传 force 时，间隔内不追加采样点。"""
    reset_metrics()
    sample_series(force=True)
    first = sample_series(force=True)  # 强制再来一条
    assert len(first) == 2
    # 间隔内（默认 5s）普通调用不应新增
    assert len(sample_series()) == 2


def test_prune_respects_max():
    reset_metrics()
    import harness.observation.ops_metrics as m

    old_max = m._SERIES_MAX
    m._SERIES_MAX = 3
    try:
        for _ in range(6):
            record_query(succeeded=True)
            sample_series(force=True)
        assert len(sample_series(force=True)) == 3
    finally:
        m._SERIES_MAX = old_max


def test_reset_clears_series():
    reset_metrics()
    sample_series(force=True)
    record_query(succeeded=True)
    sample_series(force=True)
    s = sample_series()  # 间隔内不追加，直接读当前序列
    assert len(s) == 2
    reset_metrics()
    s2 = sample_series(force=True)  # 重置后只剩新的首点
    assert len(s2) == 1
