"""GET /api/overview — Overview 驾驶舱数据（统计 + 最近查询 + 节点分布）。

数据来源全部是 db-agent 已经写好的运行产物，零新依赖：
  - logs/traces/*.jsonl               —— harness.observation.tracer 的结构化 trace
  - harness.observation.ops_metrics    —— 进程内实时指标（进程重启清零）

前端 Overview 页据此渲染：统计卡片 + 架构图节点统计 + 最近查询列表。
另附 Opik UI 链接（点架构图 Opik 卡片可打开）。
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException

from harness.observation.ops_metrics import snapshot as runtime_snapshot
from harness.observation.tracer import TRACE_DIR, _compute_stats, _read_traces

router = APIRouter()
ROOT = Path(__file__).resolve().parents[2]
DEMO_DB = ROOT / "db" / "demo.db"
logger = logging.getLogger(__name__)

RECENT_LIMIT = 15


def _opik_overview() -> dict:
    """返回前端可打开的 Opik UI 链接（尽量深链到当前 project 的 traces）。"""
    api = os.getenv("OPIK_URL_OVERRIDE", "http://localhost:5173/api").rstrip("/")
    ui = api[:-4] if api.endswith("/api") else api
    project = os.getenv("OPIK_PROJECT_NAME", "db-agent")
    enabled = os.getenv("OPIK_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
    url = ui
    try:
        req = urllib.request.Request(
            f"{api}/v1/private/projects?size=50",
            headers={"Accept": "application/json"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        for item in payload.get("content") or []:
            if item.get("name") == project and item.get("id"):
                url = f"{ui}/default/projects/{item['id']}/traces"
                break
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as exc:
        logger.debug("opik project resolve skipped: %s", exc)
    return {
        "enabled": enabled,
        "project": project,
        "ui_url": ui,
        "url": url,
    }

# 架构图节点字典：id 与 trace span.node / SSE step node 一一对应。
# label 与 frontend/src/components/ThinkingSteps.tsx 的 LABELS 保持一致。
NODES = [
    {"id": "preprocessing",   "label": "输入护栏",     "group": "constraints"},
    {"id": "router",          "label": "意图路由",     "group": "core"},
    {"id": "clarify",         "label": "需求澄清",     "group": "core"},
    {"id": "data_quality",    "label": "数据质量",     "group": "agents"},
    {"id": "sql",             "label": "SQL Agent",    "group": "agents"},
    {"id": "hbase",           "label": "HBase Agent",  "group": "agents"},
    {"id": "hive",            "label": "Hive Agent",   "group": "agents"},
    {"id": "strategy",        "label": "制度检索",     "group": "agents"},
    {"id": "analysis",        "label": "综合分析",     "group": "agents"},
    {"id": "confidence_gate", "label": "置信度门控",   "group": "core"},
    {"id": "reflection",      "label": "质量反思",     "group": "core"},
    {"id": "graph",           "label": "多Agent编排",  "group": "core"},
    {"id": "guardrail",       "label": "护栏拦截",     "group": "constraints"},
]


def _load_traces(days: int | None = None) -> list[dict]:
    """读取 trace 文件，按 started_at 倒序。

    days=None 读全部历史；days=N 只读最近 N 个自然日（文件名即日期）。
    """
    files = sorted(TRACE_DIR.glob("*.jsonl"), reverse=True)
    if days is not None:
        cutoff = date.today() - timedelta(days=days - 1)
        files = [f for f in files if f.stem >= cutoff.isoformat()]
    traces: list[dict] = []
    for f in files:
        traces.extend(_read_traces(f))
    traces.sort(key=lambda t: t.get("started_at", ""), reverse=True)
    return traces



def _count_table(table: str) -> int:
    if not DEMO_DB.exists():
        return 0
    try:
        import sqlite3
        conn = sqlite3.connect(f"file:{DEMO_DB}?mode=ro", uri=True)
        try:
            return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        finally:
            conn.close()
    except Exception:
        return 0


def _estimate_spent_usd(total_tokens: int) -> float:
    """Rough all-time $ from tokens (blended DeepSeek-ish rate). Tokens are ground truth."""
    return round((total_tokens / 1_000_000) * 0.20, 2)


def _hero(stats_all: dict) -> dict:
    agent_calls = sum((stats_all.get("node_calls") or {}).values())
    tokens = int(stats_all.get("total_tokens") or 0)
    return {
        "spent_usd": _estimate_spent_usd(tokens),
        "avg_turn_s": float(stats_all.get("avg_elapsed") or 0),
        "turns": int(stats_all.get("total") or 0),
        "agent_calls": int(agent_calls),
        "facts": _count_table("user_memory"),
        "feedback": _count_table("user_feedback"),
        "tokens": tokens,
        "path": str(ROOT),
    }

@router.get("/overview")
async def overview():
    today_traces = _load_traces(days=1)
    all_traces = _load_traces()

    stats_today = _compute_stats(today_traces)
    stats_all = _compute_stats(all_traces)

    node_calls = stats_all["node_calls"]
    node_errors = stats_all["node_errors"]
    nodes = [
        {
            **n,
            "calls": node_calls.get(n["id"], 0),
            "errors": node_errors.get(n["id"], 0),
        }
        for n in NODES
    ]

    return {
        "stats": {
            "today": stats_today,
            "all": stats_all,
            "runtime": runtime_snapshot(),
        },
        "hero": _hero(stats_all),
        "recent": all_traces[:RECENT_LIMIT],
        "nodes": nodes,
        "opik": _opik_overview(),
    }


def _dedupe_traces_by_id(traces: list[dict]) -> list[dict]:
    """Same trace_id may be appended multiple times as the graph progresses — keep richest."""
    best: dict[str, dict] = {}
    order: list[str] = []
    for t in traces:
        tid = str(t.get("trace_id") or "")
        if not tid:
            continue
        spans = t.get("spans") or []
        cur = best.get(tid)
        if cur is None:
            best[tid] = t
            order.append(tid)
            continue
        cur_n = len(cur.get("spans") or [])
        if len(spans) >= cur_n:
            best[tid] = t
    return [best[tid] for tid in order]


def _error_reason(node_id: str, error: str, turns: int | None) -> str:
    err = str(error or "")
    if "超过最大轮数" in err:
        mt = 8
        used = turns if turns is not None else "?"
        return (
            f"{node_id.upper()} Agent hit max_turns={mt} (this span used {used} turns) "
            "without producing a final answer — usually tool-call thrashing "
            "(entity missing from DB, ambiguous task, or repeated describe/query). "
            "Orchestrator may replan afterward; check later spans on the same trace."
        )
    if "熔断" in err:
        return "Circuit breaker open: model/API unavailable; node returned degraded text."
    return "See error text and span task for details."


@router.get("/overview/node-errors/{node_id}")
async def node_errors(node_id: str, limit: int = 40):
    """List recent span errors for a node (from Trace JSONL) — for Node stats ✗ drill-down."""
    known = {n["id"] for n in NODES}
    if node_id not in known:
        raise HTTPException(404, f"unknown node: {node_id}")
    items: list[dict] = []
    seen: set[tuple] = set()
    for t in _dedupe_traces_by_id(_load_traces()):
        spans = t.get("spans") or []
        path = " → ".join(
            f"{s.get('node')}" + ("✗" if s.get("error") else "")
            for s in spans
        )
        for s in spans:
            if s.get("node") != node_id or not s.get("error"):
                continue
            key = (t.get("trace_id"), s.get("task"), str(s.get("error")), s.get("started_at") or t.get("started_at"), s.get("elapsed"))
            if key in seen:
                continue
            seen.add(key)
            turns = s.get("turns")
            err = str(s.get("error") or "")
            items.append({
                "trace_id": t.get("trace_id"),
                "opik_trace_id": t.get("opik_trace_id"),
                "thread_id": t.get("thread_id"),
                "query": str(t.get("query") or "")[:220],
                "error": err[:600],
                "task": s.get("task") or "",
                "started_at": t.get("started_at"),
                "elapsed": s.get("elapsed") if s.get("elapsed") is not None else t.get("elapsed"),
                "turns": turns,
                "max_turns": 8,
                "tokens": s.get("total_tokens"),
                "path": path[:240],
                "reason": _error_reason(node_id, err, turns if isinstance(turns, int) else None),
            })
            if len(items) >= limit:
                break
        if len(items) >= limit:
            break
    return {
        "node": node_id,
        "count": len(items),
        "max_turns_default": 8,
        "items": items,
    }
