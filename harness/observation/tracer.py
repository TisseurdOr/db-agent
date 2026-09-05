"""结构化追踪（Structured Tracing）——让 Agent 的每一步都可追溯。

审计日志脱敏：所有 SQL 参数在写入 trace 前自动打码，防止敏感数据泄露。
设计动机：每次 SQL 执行的参数在写入审计日志前自动脱敏——token 替换敏感值，
保留 SQL 结构用于调试，但不暴露真实数据。这个习惯来自金融合规场景。


概念（来自 OpenTelemetry）:
- Trace: 一次完整查询的生命周期 = 用户输入 → 最终回答
- Span:  Trace 里的一个操作单元 = 一个节点执行（router / sql / analysis）

用法:
    from harness.observation.tracer import TraceContext

    trace = TraceContext(query="查华东销售额")
    span = trace.start_span("router", "分析意图")
    # ... 节点执行 ...
    trace.finish_span(span, usage={"input_tokens": 100, "output_tokens": 50})
    trace.save()  # 写入 logs/traces/YYYY-MM-DD.jsonl

查看:
    python -m harness.observation.tracer --today     # 今天的 trace
    python -m harness.observation.tracer --last 3    # 最近 3 条
"""

import json
import os
import random
import re as _re_mask
import sys
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

# ── 审计脱敏：SQL 参数打码 ──
# 设计动机：每次 SQL 执行参数在写入审计日志前自动脱敏——保留 SQL 结构，
# 但不暴露真实数据。这个习惯来自金融合规场景。

_MASK_PATTERNS = [
    (_re_mask.compile(r"(?i)VALUES\s*\([^)]+\)"), "VALUES (***)"),
    (_re_mask.compile(r"(?i)password\s*=\s*'[^']*'"), "password='***'"),
    (_re_mask.compile(r"(?i)WHERE\s+id\s*=\s*'\d{15,19}'"), "WHERE id='***'"),
    (_re_mask.compile(r"(?i)WHERE\s+phone\s*=\s*'\d{11}'"), "WHERE phone='***'"),
]


def mask_sql(sql: str) -> str:
    """脱敏 SQL 参数值，保留 SQL 结构用于调试。"""
    masked = sql
    for pattern, replacement in _MASK_PATTERNS:
        masked = pattern.sub(replacement, masked)
    return masked


# Trace 文件目录（一天一个 YYYY-MM-DD.jsonl）
TRACE_DIR = Path(__file__).resolve().parents[2] / "logs" / "traces"
TRACE_DIR.mkdir(parents=True, exist_ok=True)

# 保留天数：删掉文件名日期 < today-(N-1) 的 jsonl。0 = 关闭清理。
DEFAULT_TRACE_RETENTION_DAYS = 30
# save() 时触发清理的概率（启动时总会跑一次）
_TRACE_CLEANUP_PROB = float(os.getenv("TRACE_CLEANUP_PROB", "0.01"))


def _retention_days() -> int:
    raw = os.getenv("TRACE_RETENTION_DAYS", str(DEFAULT_TRACE_RETENTION_DAYS)).strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_TRACE_RETENTION_DAYS


def _parse_trace_date(path: Path) -> date | None:
    """从 YYYY-MM-DD.jsonl 解析日期；非法文件名返回 None（不删）。"""
    stem = path.stem
    try:
        return datetime.strptime(stem, "%Y-%m-%d").date()
    except ValueError:
        return None


def cleanup_expired_traces(
    retention_days: int | None = None,
    trace_dir: Path | None = None,
    *,
    today: date | None = None,
) -> list[Path]:
    """删除超过保留期的按日 trace 文件。返回已删除路径列表。

    保留窗口：最近 retention_days 个自然日（含今天）→
    cutoff = today - (days - 1)；文件日期 < cutoff 则删。
    retention_days=0 或负数视为关闭。
    """
    days = _retention_days() if retention_days is None else retention_days
    if days <= 0:
        return []
    root = trace_dir or TRACE_DIR
    if not root.exists():
        return []
    ref = today or date.today()
    cutoff = ref - timedelta(days=days - 1)
    deleted: list[Path] = []
    for path in sorted(root.glob("*.jsonl")):
        file_day = _parse_trace_date(path)
        if file_day is None:
            continue
        if file_day < cutoff:
            try:
                path.unlink()
                deleted.append(path)
            except OSError:
                pass
    if deleted:
        print(
            f"[trace] retention={days}d：删除 {len(deleted)} 个过期文件 "
            f"(早于 {cutoff.isoformat()})"
        )
    return deleted


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

    __slots__ = ("trace_id", "query", "started_at", "finished_at", "spans", "blocked_by", "opik_trace_id", "thread_id")

    def __init__(self, query: str = "", thread_id: str | None = None):
        # trace_id: 短 ID，方便肉眼识别（如 "20260721-a3f2"）
        short_id = uuid.uuid4().hex[:4]
        date_str = datetime.now().strftime("%Y%m%d")
        self.trace_id = f"{date_str}-{short_id}"
        self.query = query[:200]  # 截断长 query
        self.started_at = time.time()
        self.finished_at = 0.0
        self.spans: list[Span] = []
        self.blocked_by: str | None = None  # 如果被护栏拦截，记录是哪一层
        self.opik_trace_id: str | None = None  # Opik UUID when available
        self.thread_id = thread_id  # LangGraph / web session thread

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
            "opik_trace_id": self.opik_trace_id,
            "thread_id": self.thread_id,
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
        """写入当天的 JSONL 文件。返回文件路径。

        偶尔（默认 1%）顺带跑一次 retention 清理，避免只依赖进程启动。
        """
        if not self.finished_at:
            self.finished_at = time.time()
        filepath = _trace_file()
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(self.to_dict(), ensure_ascii=False) + "\n")
        try:
            if _TRACE_CLEANUP_PROB > 0 and random.random() < _TRACE_CLEANUP_PROB:
                cleanup_expired_traces()
        except Exception:
            pass
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
# CLI 查看工具: python -m harness.observation.tracer --today / --last 3 / --id xxx
# ═══════════════════════════════════════════════════════════════════════════════

def _read_traces(filepath: Path) -> list[dict]:
    """读 JSONL 文件，返回 trace 列表。"""
    if not filepath.exists():
        return []
    traces = []
    with open(filepath, encoding="utf-8") as f:
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


# ═══════════════════════════════════════════════════════════════════════════════
# 聚合统计（--stats）
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_stats(traces: list[dict]) -> dict:
    """把一批 trace 聚合成错误率 / 节点分布 / Top 错误等指标（纯函数，可测）。"""
    total = len(traces)
    errored = [
        t for t in traces
        if any(s.get("error") for s in t.get("spans", []))
    ]
    blocked = [t for t in traces if t.get("blocked_by")]

    node_calls: dict[str, int] = {}
    node_errors: dict[str, int] = {}
    for t in traces:
        for s in t.get("spans", []):
            node = s.get("node", "?")
            node_calls[node] = node_calls.get(node, 0) + 1
            if s.get("error"):
                node_errors[node] = node_errors.get(node, 0) + 1

    error_msgs: dict[str, int] = {}
    for t in errored:
        for s in t.get("spans", []):
            if s.get("error"):
                msg = str(s["error"])[:60]
                error_msgs[msg] = error_msgs.get(msg, 0) + 1

    return {
        "total": total,
        "errored": len(errored),
        "error_rate": round(len(errored) / total, 4) if total else 0.0,
        "blocked": len(blocked),
        "node_calls": node_calls,
        "node_errors": node_errors,
        "top_errors": sorted(error_msgs.items(), key=lambda x: -x[1])[:5],
        "avg_elapsed": round(sum(t.get("elapsed", 0) for t in traces) / total, 2) if total else 0.0,
        "total_tokens": sum((t.get("totals") or {}).get("total_tokens", 0) for t in traces),
    }


def _print_stats(stats: dict) -> None:
    """打印聚合统计。"""
    print(f"查询总数:   {stats['total']}")
    print(f"出错数:     {stats['errored']}（错误率 {stats['error_rate'] * 100:.1f}%）")
    print(f"护栏拦截:   {stats['blocked']}")
    print(f"平均耗时:   {stats['avg_elapsed']}s · 总 token {stats['total_tokens']}")

    problem_nodes = {n: e for n, e in stats['node_errors'].items() if e}
    if problem_nodes:
        print("\n按节点错误分布（出错 / 总调用）:")
        for node in sorted(problem_nodes, key=lambda n: -problem_nodes[n]):
            print(f"  ❌ {node:14s} {problem_nodes[node]}/{stats['node_calls'].get(node, 0)}")
    else:
        print("\n✅ 没有发现错误 span")

    if stats['top_errors']:
        print("\nTop 错误信息:")
        for msg, cnt in stats['top_errors']:
            print(f"  {cnt:3d}× {msg}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="查看 Agent trace 记录")
    parser.add_argument("--today", action="store_true", help="今天的 trace")
    parser.add_argument("--last", type=int, help="最近 N 条 trace（跨所有文件）")
    parser.add_argument("--id", type=str, help="按 trace_id 查看")
    parser.add_argument("--date", type=str, help="指定日期 (YYYY-MM-DD)")
    parser.add_argument("--stats", action="store_true", help="聚合统计：错误率/节点分布/Top 错误")
    parser.add_argument(
        "--cleanup", action="store_true",
        help=f"按 TRACE_RETENTION_DAYS（默认 {DEFAULT_TRACE_RETENTION_DAYS}）删除过期按日 jsonl",
    )
    args = parser.parse_args()

    if args.cleanup:
        deleted = cleanup_expired_traces()
        print(f"已删除 {len(deleted)} 个文件" if deleted else "无需删除（无过期文件或已关闭 retention）")
        sys.exit(0)

    if args.stats:
        if args.date:
            traces = _read_traces(TRACE_DIR / f"{args.date}.jsonl")
            print(f"=== 统计（{args.date}）===")
        else:
            traces = []
            for f in sorted(TRACE_DIR.glob("*.jsonl")):
                traces.extend(_read_traces(f))
            print(f"=== 统计（全部历史，共 {len(traces)} 条）===")
        _print_stats(_compute_stats(traces))

    elif args.date:
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
                    sys.exit(0)
        print(f"未找到 trace_id={args.id}")

    else:
        parser.print_help()
