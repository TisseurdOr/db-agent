"""Overview 端点（/api/overview）测试 —— 用临时 trace 目录，零 API 成本。"""

import json
from pathlib import Path

from fastapi.testclient import TestClient

import server.endpoints.overview as ov_mod
from server.main import app

client = TestClient(app)


def _write_trace(dir: Path, name: str, traces: list[dict]) -> None:
    with open(dir / name, "w", encoding="utf-8") as f:
        for t in traces:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")


def _span(node: str, error: str | None = None) -> dict:
    s = {"node": node, "task": "x", "elapsed": 1.0, "total_tokens": 10}
    if error:
        s["error"] = error
    return s


def _trace(query: str = "q", started_at: str = "2026-08-28T09:00:00",
           spans: list[dict] | None = None, blocked_by: str | None = None) -> dict:
    spans = spans or [_span("router"), _span("sql")]
    return {
        "trace_id": "t1",
        "query": query,
        "started_at": started_at,
        "elapsed": 2.0,
        "blocked_by": blocked_by,
        "spans": spans,
        "totals": {"total_tokens": 100, "turns": 2, "span_count": len(spans)},
    }


def test_nodes_metadata_complete():
    """NODES 字典每个节点都有 id / label / group，且 id 与 trace span.node 约定一致。"""
    for n in ov_mod.NODES:
        assert n["id"] and n["label"] and n["group"]
    ids = {n["id"] for n in ov_mod.NODES}
    # 至少覆盖核心执行路径的节点
    for expected in ("router", "sql", "analysis", "hbase", "hive", "strategy", "reflection"):
        assert expected in ids


def test_load_traces_sorts_desc(monkeypatch, tmp_path):
    monkeypatch.setattr(ov_mod, "TRACE_DIR", tmp_path)
    _write_trace(tmp_path, "2026-08-28.jsonl", [
        _trace(query="早", started_at="2026-08-28T08:00:00"),
        _trace(query="晚", started_at="2026-08-28T09:00:00"),
    ])
    traces = ov_mod._load_traces()
    assert [t["query"] for t in traces] == ["晚", "早"]


def test_load_traces_days_filter(monkeypatch, tmp_path):
    monkeypatch.setattr(ov_mod, "TRACE_DIR", tmp_path)
    _write_trace(tmp_path, "2026-08-01.jsonl", [_trace()])
    _write_trace(tmp_path, "2026-08-28.jsonl", [_trace()])
    traces = ov_mod._load_traces(days=1)
    assert len(traces) == 1  # 只保留"今天"（文件名即日期）


def test_overview_endpoint_shape(monkeypatch, tmp_path):
    monkeypatch.setattr(ov_mod, "TRACE_DIR", tmp_path)
    _write_trace(tmp_path, "2026-08-28.jsonl", [
        _trace(query="华东销售额", started_at="2026-08-28T09:00:00",
               spans=[_span("router"), _span("sql", error="表不存在")]),
        _trace(query="各部门工资", started_at="2026-08-28T09:05:00",
               spans=[_span("router"), _span("sql")]),
    ])
    r = client.get("/api/overview")
    assert r.status_code == 200
    data = r.json()

    # 统计
    assert data["stats"]["today"]["total"] == 2
    assert data["stats"]["today"]["errored"] == 1
    assert data["stats"]["today"]["node_calls"]["sql"] == 2
    assert data["stats"]["today"]["node_errors"]["sql"] == 1
    # runtime 指标存在（进程内）
    assert "runtime" in data["stats"]
    assert "queries_total" in data["stats"]["runtime"]

    # 最近查询倒序
    assert data["recent"][0]["query"] == "各部门工资"
    # 节点统计映射
    nodes = {n["id"]: n for n in data["nodes"]}
    assert nodes["sql"]["calls"] == 2
    assert nodes["sql"]["errors"] == 1
    assert nodes["router"]["calls"] == 2
    # Opik 入口（架构图卡片跳转）
    assert "opik" in data
    assert "url" in data["opik"] and data["opik"]["url"]
    assert "ui_url" in data["opik"]


def test_overview_endpoint_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(ov_mod, "TRACE_DIR", tmp_path)
    r = client.get("/api/overview")
    assert r.status_code == 200
    data = r.json()
    assert data["stats"]["today"]["total"] == 0
    assert data["recent"] == []
