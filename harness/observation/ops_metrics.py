"""运维指标（进程内，Prometheus 文本格式可导出）。

记录：查询数 / 成功失败 / token / 延迟 / 按节点错误分布。
进程重启清零；查看：
  - HTTP: GET /api/metrics（Prometheus 文本格式，可被 Prometheus 抓取）
  - CLI:  python -m harness.observation.ops_metrics
"""

import threading

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
