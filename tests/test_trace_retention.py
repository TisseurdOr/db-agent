"""Trace JSONL retention: delete files older than TRACE_RETENTION_DAYS."""

from datetime import date, timedelta
from pathlib import Path

from harness.observation.tracer import cleanup_expired_traces


def _touch(day: date, root: Path) -> Path:
    p = root / f"{day.isoformat()}.jsonl"
    p.write_text('{"trace_id":"x"}\n', encoding="utf-8")
    return p


def test_cleanup_deletes_older_than_retention(tmp_path):
    today = date(2026, 8, 31)
    keep = _touch(today, tmp_path)
    keep2 = _touch(today - timedelta(days=29), tmp_path)  # within 30d window
    drop = _touch(today - timedelta(days=30), tmp_path)   # outside: cutoff = today-29
    junk = tmp_path / "not-a-date.jsonl"
    junk.write_text("{}\n", encoding="utf-8")

    deleted = cleanup_expired_traces(retention_days=30, trace_dir=tmp_path, today=today)
    assert drop in deleted
    assert keep.exists()
    assert keep2.exists()
    assert not drop.exists()
    assert junk.exists()  # non YYYY-MM-DD left alone


def test_cleanup_disabled_when_zero(tmp_path):
    today = date(2026, 8, 31)
    old = _touch(today - timedelta(days=100), tmp_path)
    deleted = cleanup_expired_traces(retention_days=0, trace_dir=tmp_path, today=today)
    assert deleted == []
    assert old.exists()


def test_save_probabilistic_cleanup(monkeypatch, tmp_path):
    import harness.observation.tracer as tr

    monkeypatch.setattr(tr, "TRACE_DIR", tmp_path)
    monkeypatch.setattr(tr, "_TRACE_CLEANUP_PROB", 1.0)
    today = date.today()
    old = tmp_path / f"{(today - timedelta(days=40)).isoformat()}.jsonl"
    old.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("TRACE_RETENTION_DAYS", "30")

    ctx = tr.TraceContext("q")
    ctx.save()
    assert not old.exists()
    assert (tmp_path / f"{today.isoformat()}.jsonl").exists()
