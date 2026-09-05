"""运维指标（进程内，Prometheus 文本格式可导出）。

记录：查询数 / 成功失败 / token / 延迟 / 按节点错误分布。
进程重启清零；查看：
  - HTTP: GET /api/metrics（Prometheus 文本格式，可被 Prometheus 抓取）
  - CLI:  python -m harness.observation.ops_metrics
"""

import threading
import time

_metrics = {
    "queries_total": 0,
    "queries_succeeded": 0,
    "queries_failed": 0,
    "tokens_total": 0,
    "elapsed_total": 0.0,
    "elapsed_max": 0.0,
    "errors_by_node": {},  # node -> count
}
_lock = threading.Lock()


def record_query(succeeded: bool = True) -> None:
    with _lock:
        _metrics["queries_total"] += 1
        if succeeded:
            _metrics["queries_succeeded"] += 1
        else:
            _metrics["queries_failed"] += 1


def record_tokens(n: int) -> None:
    with _lock:
        _metrics["tokens_total"] += int(n)


def record_elapsed(seconds: float) -> None:
    with _lock:
        _metrics["elapsed_total"] += seconds
        _metrics["elapsed_max"] = max(_metrics["elapsed_max"], seconds)


def record_error(node: str, message: str = "") -> None:
    with _lock:
        _metrics["errors_by_node"][node] = _metrics["errors_by_node"].get(node, 0) + 1


def snapshot() -> dict:
    with _lock:
        return {
            "queries_total": _metrics["queries_total"],
            "queries_succeeded": _metrics["queries_succeeded"],
            "queries_failed": _metrics["queries_failed"],
            "tokens_total": _metrics["tokens_total"],
            "elapsed_total": round(_metrics["elapsed_total"], 3),
            "elapsed_avg": round(_metrics["elapsed_total"] / max(_metrics["queries_total"], 1), 3),
            "elapsed_max": round(_metrics["elapsed_max"], 3),
            "error_rate": round(_metrics["queries_failed"] / max(_metrics["queries_total"], 1), 4),
            "errors_by_node": dict(_metrics["errors_by_node"]),
        }


def reset_metrics() -> None:
    """测试隔离用。"""
    global _series, _last_sample_at
    _series.clear()
    _last_sample_at = 0.0
    with _lock:
        _metrics["queries_total"] = 0
        _metrics["queries_succeeded"] = 0
        _metrics["queries_failed"] = 0
        _metrics["tokens_total"] = 0
        _metrics["elapsed_total"] = 0.0
        _metrics["elapsed_max"] = 0.0
        _metrics["errors_by_node"] = {}


def prometheus_text() -> str:
    """Prometheus 文本格式导出（供 /api/metrics 抓取）。"""
    s = snapshot()
    lines = [
        "# HELP dbagent_queries_total 累计查询数",
        "# TYPE dbagent_queries_total counter",
        f"dbagent_queries_total {s['queries_total']}",
        f"dbagent_queries_succeeded {s['queries_succeeded']}",
        f"dbagent_queries_failed {s['queries_failed']}",
        "# HELP dbagent_tokens_total 累计 LLM token",
        "# TYPE dbagent_tokens_total counter",
        f"dbagent_tokens_total {s['tokens_total']}",
        "# HELP dbagent_elapsed_seconds 查询耗时",
        "# TYPE dbagent_elapsed_seconds summary",
        f"dbagent_elapsed_total_seconds {s['elapsed_total']}",
        f"dbagent_elapsed_max_seconds {s['elapsed_max']}",
        "# HELP dbagent_error_rate 错误率",
        "# TYPE dbagent_error_rate gauge",
        f"dbagent_error_rate {s['error_rate']}",
    ]
    for node, count in sorted(s["errors_by_node"].items()):
        lines.append(f'dbagent_errors_total{{node="{node}"}} {count}')
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    import json
    print(json.dumps(snapshot(), ensure_ascii=False, indent=2))


# ═══════════════════════════════════════════════════════════════════════════════
# 内存时间序列（供 Web 图形化展示）
# ═══════════════════════════════════════════════════════════════════════════════
# 不启动后台线程：每次调用时若距上次采样 >= _SERIES_INTERVAL 才追加一条。
# 前端按 ~5s 轮询 /api/ops/series，即可得到 5s 粒度的指标曲线。
# 环形缓冲，重启清零；每条样本含累计值 + 与上一条的增量(d_*)与速率(*_rate)。

_SERIES_INTERVAL = 5.0   # 采样间隔（秒）
_SERIES_MAX = 180        # 最多保留样本数（5s × 180 = 15 分钟窗口）

_series: list[dict] = []
_last_sample_at: float = 0.0


def sample_series(force: bool = False) -> list[dict]:
    """把当前快照采样进内存时间序列，返回完整序列（含增量与速率）。

    Args:
        force: 测试/首次调用时强制采样（忽略间隔）。

    Returns:
        [{ts, dt, queries_total, queries_succeeded, queries_failed,
          tokens_total, elapsed_total, elapsed_avg, elapsed_max,
          error_rate, errors_by_node,
          d_queries, d_succeeded, d_failed, d_tokens, d_elapsed,
          queries_rate, tokens_rate}, ...]
    """
    global _last_sample_at
    now = time.monotonic()
    if not force and now - _last_sample_at < _SERIES_INTERVAL:
        return _copy_series()

    _last_sample_at = now
    s = snapshot()
    prev = _series[-1] if _series else None
    if prev is None:
        item = {
            "ts": time.time(), "dt": 0.0,
            **s,
            "d_queries": 0, "d_succeeded": 0, "d_failed": 0,
            "d_tokens": 0, "d_elapsed": 0.0,
            "queries_rate": 0.0, "tokens_rate": 0.0,
        }
    else:
        dt = max(now - prev["_t"], 1e-6)
        item = {
            "ts": time.time(), "dt": round(dt, 1),
            **s,
            "d_queries": s["queries_total"] - prev["queries_total"],
            "d_succeeded": s["queries_succeeded"] - prev["queries_succeeded"],
            "d_failed": s["queries_failed"] - prev["queries_failed"],
            "d_tokens": s["tokens_total"] - prev["tokens_total"],
            "d_elapsed": round(s["elapsed_total"] - prev["elapsed_total"], 3),
            "queries_rate": round((s["queries_total"] - prev["queries_total"]) / dt, 3),
            "tokens_rate": round((s["tokens_total"] - prev["tokens_total"]) / dt, 1),
        }
    item["_t"] = now  # 内部单调时钟基准，不外发
    _series.append(item)
    if len(_series) > _SERIES_MAX:
        del _series[: len(_series) - _SERIES_MAX]
    return _copy_series()


def _copy_series() -> list[dict]:
    """返回序列副本（剥掉内部 _t 字段），避免调用方改动缓存。"""
    out = []
    for it in _series:
        d = dict(it)
        d.pop("_t", None)
        out.append(d)
    return out
