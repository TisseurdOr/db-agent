"""结构化追踪（Structured Tracing）——让 Agent 的每一步都可追溯。

概念（来自 OpenTelemetry）:
- Trace: 一次完整查询的生命周期 = 用户输入 → 最终回答
- Span:  Trace 里的一个操作单元 = 一个节点执行（router / sql / analysis）

用法:
    from utils.tracer import TraceContext

    trace = TraceContext(query="查华东销售额")
    span = trace.start_span("router", "分析意图")
    # ... 节点执行 ...
    trace.finish_span(span, usage={"input_tokens": 100, "output_tokens": 50})
    trace.save()  # 写入 logs/traces/YYYY-MM-DD.jsonl

查看:
    python -m utils.tracer --today     # 今天的 trace
    python -m utils.tracer --last 3    # 最近 3 条
"""

import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional


# Trace 文件目录
TRACE_DIR = Path(__file__).resolve().parent.parent / "logs" / "traces"
TRACE_DIR.mkdir(parents=True, exist_ok=True)

# 每天的 trace 存一个 JSONL 文件
def _trace_file() -> Path:
    today = datetime.now().strftime("%Y-%m-%d")
    return TRACE_DIR / f"{today}.jsonl"


class Span:
    """一个操作单元。记录一个节点的执行信息。"""

    __slots__ = (
        "node", "task", "started_at", "finished_at",
        "input_tokens", "output_tokens", "turns", "error",
    )

    def __init__(self, node: str, task: str = ""):
        self.node = node
        self.task = task
        self.started_at = time.time()
        self.finished_at = 0.0
        self.input_tokens = 0
        self.output_tokens = 0
        self.turns = 0
        self.error = None

    @property
    def elapsed(self) -> float:
        if self.finished_at:
            return self.finished_at - self.started_at
        return time.time() - self.started_at

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict:
        return {
            "node": self.node,
            "task": self.task,
            "elapsed": round(self.elapsed, 3),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "turns": self.turns,
            "error": self.error,
        }


class TraceContext:
    """一次查询的完整追踪。跟着 state 在节点间流转。"""

    __slots__ = ("trace_id", "query", "started_at", "finished_at", "spans", "blocked_by")

    def __init__(self, query: str = ""):
        # trace_id: 短 ID，方便肉眼识别（如 "20260721-a3f2"）
        short_id = uuid.uuid4().hex[:4]
        date_str = datetime.now().strftime("%Y%m%d")
        self.trace_id = f"{date_str}-{short_id}"
        self.query = query[:200]  # 截断长 query
        self.started_at = time.time()
        self.finished_at = 0.0
        self.spans: list[Span] = []
        self.blocked_by: Optional[str] = None  # 如果被护栏拦截，记录是哪一层

    @property
    def elapsed(self) -> float:
        if self.finished_at:
            return self.finished_at - self.started_at
        return time.time() - self.started_at

    @property
    def total_input_tokens(self) -> int:
        return sum(s.input_tokens for s in self.spans)

    @property
    def total_output_tokens(self) -> int:
        return sum(s.output_tokens for s in self.spans)

    @property
    def total_turns(self) -> int:
        return sum(s.turns for s in self.spans)

    def start_span(self, node: str, task: str = "") -> Span:
        """开始一个新 span。节点执行前调用。"""
        span = Span(node, task)
        self.spans.append(span)
        return span

    def finish_span(self, span: Span, usage: dict, error: str = None) -> None:
        """结束一个 span。节点执行后调用。

        usage: {"input_tokens": N, "output_tokens": N, "turns": N}
        """
        span.finished_at = time.time()
        span.input_tokens = usage.get("input_tokens", 0)
        span.output_tokens = usage.get("output_tokens", 0)
        span.turns = usage.get("turns", 1)
        span.error = error

    def set_blocked(self, guard_name: str, reason: str) -> None:
        """记录护栏拦截。"""
        self.blocked_by = guard_name
        self.finished_at = time.time()
        # 创建一个虚拟 span 记录拦截
        span = Span("guardrail", reason)
        span.finished_at = time.time()
        self.spans.append(span)

    def to_dict(self) -> dict:
        """序列化为字典，用于写 JSONL。"""
        return {
            "trace_id": self.trace_id,
            "query": self.query,
            "started_at": datetime.fromtimestamp(self.started_at).isoformat(),
            "elapsed": round(self.elapsed, 3),
            "blocked_by": self.blocked_by,
            "spans": [s.to_dict() for s in self.spans],
            "totals": {
                "input_tokens": self.total_input_tokens,
                "output_tokens": self.total_output_tokens,
                "total_tokens": self.total_input_tokens + self.total_output_tokens,
                "turns": self.total_turns,
                "span_count": len(self.spans),
            },
        }

    def save(self) -> Path:
        """写入当天的 JSONL 文件。返回文件路径。"""
        if not self.finished_at:
            self.finished_at = time.time()
        filepath = _trace_file()
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(self.to_dict(), ensure_ascii=False) + "\n")
        return filepath

    # ── 便捷方法：给 orchestrator 用的 ──

    def print_progress(self, span: Span, icon: str = "✅") -> str:
        """生成进度行文本。替代之前散落的 print() 调用。"""
        if span.node == "router":
            return f"⏳ Router → {span.task} ({span.elapsed:.1f}s · {span.total_tokens}t)"
        return (
            f"{icon} {span.node.title()} Agent"
            f" ({span.elapsed:.1f}s · {span.total_tokens}t · {span.turns}轮)"
        )

    def summary(self) -> str:
        """生成最终摘要。"""
        ti = self.total_input_tokens
        to = self.total_output_tokens
        return (
            f"📊 总计 {self.elapsed:.1f}s · {ti + to}t"
            f" (入 {ti} / 出 {to}) · {self.total_turns}轮"
            f"\n   trace: {self.trace_id}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# CLI 查看工具: python -m utils.tracer --today / --last 3 / --id xxx
# ═══════════════════════════════════════════════════════════════════════════════

def _read_traces(filepath: Path) -> list[dict]:
    """读 JSONL 文件，返回 trace 列表。"""
    if not filepath.exists():
        return []
    traces = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    traces.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return traces


def _print_trace(t: dict) -> None:
    """打印一条 trace。"""
    blocked = f" 🚫 护栏拦截: {t['blocked_by']}" if t.get("blocked_by") else ""
    print(f"\ntrace_id: {t['trace_id']}{blocked}")
    print(f"query:    {t['query']}")
    totals = t.get("totals", {})
    print(f"total:    {t['elapsed']}s · {totals.get('total_tokens', 0)}t (入 {totals.get('input_tokens', 0)} / 出 {totals.get('output_tokens', 0)}) · {totals.get('turns', 0)}轮")
    print(f"spans ({totals.get('span_count', 0)}):")
    for s in t.get("spans", []):
        err = f" ❌ {s['error']}" if s.get("error") else ""
        print(f"  {s['node']:12s} {str(s['elapsed'])+'s':>8s}  {str(s['total_tokens'])+'t':>6s}  {s.get('task', '')[:50]}{err}")
    print()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="查看 Agent trace 记录")
    parser.add_argument("--today", action="store_true", help="今天的 trace")
    parser.add_argument("--last", type=int, help="最近 N 条 trace（跨所有文件）")
    parser.add_argument("--id", type=str, help="按 trace_id 查看")
    parser.add_argument("--date", type=str, help="指定日期 (YYYY-MM-DD)")
    args = parser.parse_args()

    if args.date:
        filepath = TRACE_DIR / f"{args.date}.jsonl"
        traces = _read_traces(filepath)
        for t in traces:
            _print_trace(t)
        print(f"共 {len(traces)} 条 trace ({filepath})")

    elif args.today:
        filepath = _trace_file()
        traces = _read_traces(filepath)
        if not traces:
            print(f"今天还没有 trace 记录。({filepath})")
        else:
            for t in traces:
                _print_trace(t)
            print(f"共 {len(traces)} 条 trace ({filepath})")

    elif args.last:
        # 跨所有 JSONL 文件，按时间倒序取最近 N 条
        all_traces = []
        for f in sorted(TRACE_DIR.glob("*.jsonl"), reverse=True):
            all_traces.extend(_read_traces(f))
        for t in all_traces[-args.last:]:
            _print_trace(t)
        print(f"最近 {min(args.last, len(all_traces))} 条 / 共 {len(all_traces)} 条")

    elif args.id:
        for f in sorted(TRACE_DIR.glob("*.jsonl"), reverse=True):
            for t in _read_traces(f):
                if t["trace_id"] == args.id:
                    _print_trace(t)
                    import sys; sys.exit(0)
        print(f"未找到 trace_id={args.id}")

    else:
        parser.print_help()
