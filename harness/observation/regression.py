"""回归基线 — 评测结果与上次对比，发现退化自动告警。

为什么需要：
- 之前每次评测的通过率都存进 Opik 了，但没人对比「这次 vs 上次」——
  数据存了等于没存，改代码把评测改坏了也不会被发现。
- 本模块在评测结束后：读上次基线 → 对比 pass_rate → 下降超过阈值告警 → 存本次为基线。
- 像体检报告存档：身体变差能第一时间发现，而不是等大病。

用法（由 tests/eval_runner.py 调用）:
    from harness.observation.regression import check_regression, save_baseline, load_baseline
    warnings = check_regression(mode, {"pass_rate": 0.86, "case_count": 29})
    for w in warnings: print(w)
    save_baseline(mode, {"pass_rate": 0.86, "case_count": 29})
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

# 基线文件路径：logs/eval_baseline.json（gitignore 之外的运行产物）
BASELINE_FILE = Path(__file__).resolve().parents[2] / "logs" / "eval_baseline.json"

# 默认告警阈值：pass_rate 比上次下降超过 5 个百分点就告警
DEFAULT_THRESHOLD = 0.05


def load_baseline() -> dict:
    """读全部基线；文件不存在或损坏时返回空 dict。"""
    try:
        if BASELINE_FILE.exists():
            with open(BASELINE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def save_baseline(mode: str, stats: dict) -> None:
    """把本次结果写入基线文件（按 mode 区分，fast/full 不混比）。"""
    baseline = load_baseline()
    baseline[mode] = {
        "pass_rate": float(stats.get("pass_rate", 0.0)),
        "case_count": int(stats.get("case_count", 0)),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(BASELINE_FILE, "w", encoding="utf-8") as f:
        json.dump(baseline, f, ensure_ascii=False, indent=2)


def check_regression(
    mode: str,
    current: dict,
    threshold: float = DEFAULT_THRESHOLD,
) -> list[str]:
    """对比本次与上次同 mode 的 pass_rate，返回告警信息列表（无退化则空列表）。

    Args:
        mode: 评测模式（"fast" / "full" / 自定义 category），用于定位上次基线
        current: 本次结果 {"pass_rate": float, "case_count": int}
        threshold: 允许的 pass_rate 最大下降幅度（默认 0.05 = 5 个百分点）

    Returns:
        告警列表，例如:
          ["⚠️ 评测退化: full 通过率 92% → 84%（-8pp），上次 2026-08-20T10:00:00"]
        无上次基线（首次运行）时返回空列表，不误报。
    """
    baseline = load_baseline()
    prev = baseline.get(mode)
    if not prev:
        return []

    cur_rate = float(current.get("pass_rate", 0.0))
    prev_rate = float(prev.get("pass_rate", 0.0))
    drop = prev_rate - cur_rate
    if drop > threshold:
        return [
            f"⚠️ 评测退化告警 [{mode}]: 通过率 {prev_rate:.0%} → {cur_rate:.0%}"
            f"（下降 {drop:.0%}，阈值 {threshold:.0%}）"
            f"，上次基线时间 {prev.get('timestamp', '?')}。"
            "可能是最近改动引入了回归，请检查！"
        ]
    return []

# ═══════════════════════════════════════════════════════════════════════════════
# 跑分历史 —— 每次评测追加一条 JSONL，供 Web Eval 页画趋势
# ═══════════════════════════════════════════════════════════════════════════════
# eval_baseline.json 只保留每个 mode 的「最近一次」；历史趋势需要逐次留痕。
# eval_runner.py 每次跑完调 append_history()，Eval 页读 load_history()。

HISTORY_FILE = Path(__file__).resolve().parents[2] / "logs" / "eval_history.jsonl"


def append_history(mode: str, stats: dict) -> None:
    """把本次评测结果追加进历史（logs/eval_history.jsonl）。

    stats 支持的键: pass_rate, case_count, passed, total, model, judge, failed_ids。
    写盘失败静默（评测主流程不能被留痕拖垮）。
    """
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "mode": mode,
            "pass_rate": float(stats.get("pass_rate", 0.0)),
            "case_count": int(stats.get("case_count", 0)),
            "passed": int(stats.get("passed", 0)),
            "total": int(stats.get("total", 0)),
            "model": str(stats.get("model", "") or ""),
            "judge": bool(stats.get("judge", False)),
            "failed_ids": list(stats.get("failed_ids", [])),
        }
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def load_history(mode: str | None = None) -> list[dict]:
    """读全部跑分历史（可选按 mode 过滤），按时间升序；损坏行跳过。"""
    if not HISTORY_FILE.exists():
        return []
    records: list[dict] = []
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    if mode:
        records = [r for r in records if r.get("mode") == mode]
    records.sort(key=lambda r: r.get("ts", ""))
    return records
