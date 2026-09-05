"""eval 跑分历史 / 回归基线 —— 纯本地文件读写，零依赖。"""

from harness.observation import regression


def test_append_and_load_history(tmp_path, monkeypatch):
    monkeypatch.setattr(regression, "HISTORY_FILE", tmp_path / "eval_history.jsonl")
    regression.append_history("fast", {
        "pass_rate": 1.0, "case_count": 6, "passed": 6, "total": 6,
        "model": "", "judge": False, "failed_ids": [],
    })
    regression.append_history("full", {
        "pass_rate": 0.84, "case_count": 47, "passed": 39, "total": 47,
        "model": "deepseek-chat", "judge": True, "failed_ids": ["route-003"],
    })
    hist = regression.load_history()
    assert len(hist) == 2
    assert [h["mode"] for h in hist] == ["fast", "full"]
    full = regression.load_history(mode="full")
    assert len(full) == 1
    assert full[0]["failed_ids"] == ["route-003"]
    assert full[0]["judge"] is True


def test_load_history_skips_corrupt_lines(tmp_path, monkeypatch):
    f = tmp_path / "eval_history.jsonl"
    f.write_text('{"ts":"a","mode":"fast"}\nnot-json\n{"ts":"b","mode":"full"}\n', encoding="utf-8")
    monkeypatch.setattr(regression, "HISTORY_FILE", f)
    hist = regression.load_history()
    assert len(hist) == 2


def test_append_history_creates_parent_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(regression, "HISTORY_FILE", tmp_path / "sub" / "eval_history.jsonl")
    regression.append_history("fast", {"pass_rate": 0.5, "case_count": 6})
    assert (tmp_path / "sub" / "eval_history.jsonl").exists()


def test_baseline_and_history_independent(tmp_path, monkeypatch):
    """baseline 只存最近一次、history 逐次累积，两者互不干扰。"""
    monkeypatch.setattr(regression, "BASELINE_FILE", tmp_path / "eval_baseline.json")
    monkeypatch.setattr(regression, "HISTORY_FILE", tmp_path / "eval_history.jsonl")
    for rate in (1.0, 0.9):
        regression.save_baseline("fast", {"pass_rate": rate, "case_count": 6})
        regression.append_history("fast", {"pass_rate": rate, "case_count": 6})
    assert regression.load_baseline()["fast"]["pass_rate"] == 0.9   # 最近一次
    assert len(regression.load_history()) == 2                       # 两次都留痕
