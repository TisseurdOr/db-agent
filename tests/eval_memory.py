"""记忆召回评测 — 量化 RAG 检索质量（生产路径）。

测的是 main.py 里真正跑的召回：VectorMemory.recall() = 裸向量余弦相似度，
再加 0.3 分数阈值过滤。不调 LLM，只调 embedding，跑一遍几十条几秒钟。

Golden set 两种用例（tests/golden_set.json）:
    正例  {"query": "...", "expected": "标准答案里的独有片段"}
    负例  {"query": "...", "negative": true}   # 不该召回任何记忆

输出指标:
    正例 — hit@k（前 k 条有没有命中标准答案）、MRR（平均倒数排名）、阈值误杀数
    负例 — 拦截率（阈值正确挡掉噪声的比例）、误召回列表

用法:
    uv run python -m tests.eval_memory --list 30    # 看库里记忆，造 golden set
    uv run python -m tests.eval_memory              # 跑评测
    uv run python -m tests.eval_memory --threshold 0.3
"""

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from harness.memory.vector_store import VectorMemory

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RESET = "\033[0m"
BOLD = "\033[1m"


def load_golden(path: str) -> list[dict]:
    """读 golden set，正例要有 expected，负例带 negative=true。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("golden set 顶层必须是数组")
    for i, e in enumerate(data):
        if not e.get("query"):
            raise ValueError(f"golden set 第 {i} 条缺 query: {e}")
        if not e.get("negative") and not e.get("expected"):
            raise ValueError(f"golden set 第 {i} 条：正例缺 expected，或负例加 negative=true")
    return data


def find_rank(ranked: list[dict], expected: str) -> int | None:
    """在召回结果里找标准答案，返回 1-indexed 排名；没找到返回 None。"""
    needle = expected.lower()
    for i, item in enumerate(ranked):
        if needle in item["text"].lower():
            return i + 1
    return None


def compute_positive_metrics(pairs: list[tuple[dict, list[dict]]], threshold: float | None) -> dict:
    """正例：算 hit@k + MRR。threshold 为 None 不过滤。"""
    n = len(pairs)
    hits = {1: 0, 3: 0, 5: 0, 10: 0}
    mrr = 0.0
    for entry, ranked in pairs:
        if threshold is not None:
            ranked = [r for r in ranked if r.get("score", 0) >= threshold]
        rank = find_rank(ranked, entry["expected"])
        if rank:
            for k in hits:
                if rank <= k:
                    hits[k] += 1
            mrr += 1.0 / rank
    return {
        **{f"hit@{k}": hits[k] / n for k in hits},
        "mrr": mrr / n,
    }


def fmt_pct(x: float) -> str:
    return f"{x:.1%}"


def main():
    parser = argparse.ArgumentParser(description="记忆召回评测")
    parser.add_argument("--golden", default="tests/golden_set.json", help="golden set 路径")
    parser.add_argument("--threshold", type=float, default=0.3, help="相似度过滤阈值（对齐 main.py）")
    parser.add_argument("--list", type=int, metavar="N", help="列出库中最近 N 条记忆，用于造 golden set")
    args = parser.parse_args()

    mem = VectorMemory(collection_name="conversations")
    total = mem.count()
    print(f"库内记忆总数: {total}")

    if args.list:
        recent = mem.list_recent(limit=args.list)
        print(f"\n{BOLD}最近 {len(recent)} 条记忆（挑 query 和 expected 片段用）{RESET}\n")
        for r in recent:
            text = r["text"].replace("\n", " ")
            print(f"  · {text[:110]}")
        return

    golden = load_golden(args.golden)
    positives = [e for e in golden if not e.get("negative")]
    negatives = [e for e in golden if e.get("negative")]
    print(f"golden set: {args.golden}（正例 {len(positives)} 条 + 负例 {len(negatives)} 条）")
    print(f"阈值: {args.threshold}\n")

    # ── 正例 ──
    raw_pairs, filt_pairs, details = [], [], []
    for entry in positives:
        ranked = mem.recall(entry["query"], top_k=10)
        rank_raw = find_rank(ranked, entry["expected"])
        ranked_filt = [r for r in ranked if r.get("score", 0) >= args.threshold]
        rank_filt = find_rank(ranked_filt, entry["expected"])
        details.append((entry, ranked, rank_raw, rank_filt))
        raw_pairs.append((entry, ranked))
        filt_pairs.append((entry, ranked_filt))

    raw = compute_positive_metrics(raw_pairs, threshold=None)
    filt = compute_positive_metrics(filt_pairs, threshold=None)
    killed = sum(1 for (_, _, rr, rf) in details if rr is not None and rf is None)

    # ── 负例 ──
    false_positives = []
    for entry in negatives:
        ranked = mem.recall(entry["query"], top_k=3)
        survivors = [r for r in ranked if r.get("score", 0) >= args.threshold]
        if survivors:
            top = survivors[0]
            false_positives.append((entry, top))

    print(f"{BOLD}{CYAN}{'=' * 56}{RESET}")
    print(f"{BOLD}{CYAN}  记忆召回评测（裸向量 + {args.threshold} 阈值）{RESET}")
    print(f"{BOLD}{CYAN}{'=' * 56}{RESET}\n")

    print(f"{BOLD}─ 正例（召回该命中）─{RESET}")
    print(f"  {'指标':<10}{'原始召回':>12}{'阈值后':>12}")
    print(f"  {'-' * 34}")
    for key in ("hit@1", "hit@3", "hit@5", "hit@10", "mrr"):
        rv, fv = raw[key], filt[key]
        r_color = GREEN if rv >= 0.8 else (YELLOW if rv >= 0.5 else RED)
        f_color = GREEN if fv >= 0.8 else (YELLOW if fv >= 0.5 else RED)
        r_s = fmt_pct(rv) if key.startswith("hit") else f"{rv:.3f}"
        f_s = fmt_pct(fv) if key.startswith("hit") else f"{fv:.3f}"
        print(f"  {key:<10}{r_color}{r_s:>12}{RESET}{f_color}{f_s:>12}{RESET}")
    print(f"  {RED}被阈值误杀: {killed} 条{RESET}（原始已命中、过滤后丢失）")

    n_neg = len(negatives)
    blocked = n_neg - len(false_positives)
    rate = blocked / n_neg if n_neg else 0.0
    c = GREEN if rate >= 0.9 else (YELLOW if rate >= 0.5 else RED)
    print(f"\n{BOLD}─ 负例（不该召回任何记忆）─{RESET}")
    print(f"  拦截率: {c}{rate:.0%}{RESET}（{blocked}/{n_neg} 正确挡掉）")
    if false_positives:
        print(f"  {RED}误召回 {len(false_positives)} 条{RESET}（阈值没拦住，噪声被注入）：")
        for entry, top in false_positives:
            print(f"    {RED}✗{RESET} 「{entry['query'][:24]}」→ top1分={top['score']:.2f} 召回「{top['text'][:36]}…」")

    # ── 正例明细 ──
    print(f"\n{BOLD}正例逐条明细（rank=标准答案排名）{RESET}")
    for i, (entry, ranked, rank_raw, rank_filt) in enumerate(details):
        top_score = ranked[0]["score"] if ranked else 0.0
        if rank_raw:
            icon = f"{GREEN}✓{RESET}"
            rk = f"rank={rank_raw}"
        else:
            icon = f"{RED}✗{RESET}"
            rk = "未命中"
        k = f" {RED}[被阈值误杀]{RESET}" if (rank_raw and rank_filt is None) else ""
        print(f"  {icon} [{i+1}] {entry['query'][:32]:<32} {rk:<8} top1分={top_score:.2f} → 「{entry['expected'][:20]}」{k}")


if __name__ == "__main__":
    main()
