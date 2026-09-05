"""检索消融评测 — 量化「裸向量 / +HyDE / +rerank」各自贡献。

三种配置跑同一份 golden set + 同一阈值，一次只开一个开关：
    bare         裸向量（走 search + user_id=default 过滤，等价生产 recall()）
    +HyDE        先生成假设性答案再 embedding 检索
    +HyDE+rerank 再加 LLM 重排

输出每种配置的 hit@k / MRR / 负例拦截率 / 耗时 / 真实 LLM 调用次数与 token 数，
横向对比出「加 HyDE 提升几个点、加 rerank 再提升几个点、成本增加多少」。

用法:
    uv run python -m tests.eval_retrieval_ablation --limit 2     # 冒烟
    uv run python -m tests.eval_retrieval_ablation               # 全量
    uv run python -m tests.eval_retrieval_ablation --threshold 0.5
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from anthropic import Anthropic

from harness.memory.long_term_memory import RAGPipeline
from harness.memory.vector_store import VectorMemory
from tests.eval_memory import compute_positive_metrics, find_rank, load_golden

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RESET = "\033[0m"
BOLD = "\033[1m"

USER_FILTER = {"user_id": "default"}  # 对齐生产 recall() 的 user_id 过滤


class CountingClient:
    """包装 Anthropic client，统计真实的 messages.create 调用次数 + token。"""

    def __init__(self, inner):
        self.inner = inner
        self.stats = {"calls": 0, "input_tokens": 0, "output_tokens": 0}

    @property
    def messages(self):
        return self  # 让 self.llm.messages.create(...) 落到下面的 create

    def create(self, **kwargs):
        resp = self.inner.messages.create(**kwargs)
        self.stats["calls"] += 1
        try:
            self.stats["input_tokens"] += resp.usage.input_tokens
            self.stats["output_tokens"] += resp.usage.output_tokens
        except Exception:
            pass
        return resp


def fmt_pct(x: float) -> str:
    return f"{x:.1%}"


async def run_config(name, retrieve_fn, positives, negatives, threshold):
    """跑一种配置，返回指标。retrieve_fn(query, top_k) -> list[{text, score}]

    threshold=None 表示该配置不做分数阈值过滤（BM25/RRF 是排序分，非余弦相似度）。
    """
    t0 = time.time()

    def _apply_thresh(ranked):
        if threshold is None:
            return ranked
        return [r for r in ranked if r.get("score", 0) >= threshold]

    # ── 正例 ──
    pairs = []
    for entry in positives:
        ranked = await retrieve_fn(entry["query"], top_k=10)
        pairs.append((entry, ranked))

    filt_pairs = [(e, _apply_thresh(ranked)) for e, ranked in pairs]
    pos = compute_positive_metrics(filt_pairs, threshold=None)

    killed = 0
    for entry, ranked in pairs:
        rr = find_rank(ranked, entry["expected"])
        rf = find_rank(_apply_thresh(ranked), entry["expected"])
        if rr is not None and rf is None:
            killed += 1

    # ── 负例 ──
    false_positives = []
    for entry in negatives:
        ranked = await retrieve_fn(entry["query"], top_k=3)
        survivors = _apply_thresh(ranked)
        if survivors:
            false_positives.append((entry, survivors[0]))

    n_neg = len(negatives)
    blocked = n_neg - len(false_positives)
    block_rate = blocked / n_neg if n_neg else 0.0

    return {
        "name": name,
        "pos": pos,
        "killed": killed,
        "block_rate": block_rate,
        "false_positives": false_positives,
        "elapsed": time.time() - t0,
    }


async def main():
    parser = argparse.ArgumentParser(description="检索消融评测")
    parser.add_argument("--golden", default="tests/golden_set.json")
    parser.add_argument("--threshold", type=float, default=0.3)
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 条正例+负例（冒烟用）")
    parser.add_argument("--json", default=None, metavar="PATH",
                        help="把召回率结果写进 JSON（供 Web 端展示），如 logs/ablation_results.json")
    args = parser.parse_args()

    golden = load_golden(args.golden)
    positives = [e for e in golden if not e.get("negative")]
    negatives = [e for e in golden if e.get("negative")]
    if args.limit:
        positives = positives[:args.limit]
        negatives = negatives[:args.limit]

    mem = VectorMemory(collection_name="conversations")
    base_llm = Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        base_url=os.environ.get("ANTHROPIC_BASE_URL"),
    )
    counting = CountingClient(base_llm)
    pipe = RAGPipeline(vector_db=mem, llm_client=counting)

    async def bare(query, top_k):
        return await pipe.retrieve(
            query, top_k=top_k, filters=USER_FILTER, use_hyde=False, use_rerank=False
        )

    async def hyde(query, top_k):
        return await pipe.retrieve(
            query, top_k=top_k, filters=USER_FILTER, use_hyde=True, use_rerank=False
        )

    async def hyde_rerank(query, top_k):
        return await pipe.retrieve(
            query, top_k=top_k, filters=USER_FILTER, use_hyde=True, use_rerank=True
        )

    async def hybrid(query, top_k):
        return await pipe.retrieve(
            query, top_k=top_k, filters=USER_FILTER, use_hyde=False, use_rerank=False, use_hybrid=True
        )

    async def hybrid_rerank(query, top_k):
        return await pipe.retrieve(
            query, top_k=top_k, filters=USER_FILTER, use_hyde=False, use_rerank=True, use_hybrid=True
        )

    # 阈值只对「余弦相似度」配置有意义；BM25/RRF 是排序分，threshold=None 跳过过滤
    configs = [
        ("裸向量", bare, args.threshold),
        ("+HyDE", hyde, args.threshold),
        ("+HyDE+rerank", hyde_rerank, args.threshold),
        ("+混合(BM25+RRF)", hybrid, None),
        ("+混合+rerank", hybrid_rerank, None),
    ]

    print(f"\n{BOLD}{CYAN}{'=' * 76}{RESET}")
    print(
        f"{BOLD}{CYAN}  检索消融评测（阈值 {args.threshold}，正例 {len(positives)} + 负例 {len(negatives)}）{RESET}"
    )
    print(f"{BOLD}{CYAN}{'=' * 76}{RESET}")

    results = []
    for name, fn, th in configs:
        counting.stats = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
        res = await run_config(name, fn, positives, negatives, th)
        res["stats"] = dict(counting.stats)
        res["threshold"] = th
        results.append(res)

    # ── 汇总表 ──
    header = (
        f"\n{BOLD}"
        f"{'配置':<16}{'hit@1':>8}{'hit@3':>8}{'hit@5':>8}{'hit@10':>8}{'MRR':>8}"
        f"{'拦截率':>9}{'误杀':>6}{'耗时':>8}{'LLM调用':>8}{'tok in/out':>16}"
        f"{RESET}"
    )
    print(header)
    print("  " + "-" * 76)
    for r in results:
        p = r["pos"]
        s = r["stats"]
        toks = f"{s['input_tokens']}/{s['output_tokens']}"
        if r["threshold"] is None:
            block_s, killed_s = f"{YELLOW}—{RESET}", "—"
        else:
            block_c = GREEN if r["block_rate"] >= 0.9 else (YELLOW if r["block_rate"] >= 0.5 else RED)
            block_s = f"{block_c}{r['block_rate']:>8.0%}{RESET}"
            killed_s = f"{r['killed']}"
        print(
            f"  {r['name']:<16}"
            f"{fmt_pct(p['hit@1']):>8}{fmt_pct(p['hit@3']):>8}"
            f"{fmt_pct(p['hit@5']):>8}{fmt_pct(p['hit@10']):>8}"
            f"{p['mrr']:>8.3f}"
            f"{block_s:>8}"
            f"{killed_s:>6}{r['elapsed']:>7.1f}s"
            f"{s['calls']:>8}{toks:>16}"
        )

    # ── 负例误召回明细 ──
    for r in results:
        if r["false_positives"]:
            print(f"\n{BOLD}[{r['name']}] 负例误召回 {len(r['false_positives'])} 条{RESET}（阈值没拦住）：")
            for entry, top in r["false_positives"]:
                print(f"    {RED}✗{RESET} 「{entry['query'][:24]}」→ top1分={top['score']:.2f} 「{top['text'][:36]}…」")

    # ── 写 JSON（供 Web 端展示召回率）──
    if args.json is not None:
        out_path = Path(args.json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "threshold": args.threshold,
            "n_pos": len(positives),
            "n_neg": len(negatives),
            "configs": [
                {
                    "name": r["name"],
                    "threshold": r["threshold"],
                    **{k: round(v, 4) for k, v in r["pos"].items()},
                    "block_rate": round(r["block_rate"], 4) if r["threshold"] is not None else None,
                    "killed": r["killed"] if r["threshold"] is not None else None,
                    "elapsed": round(r["elapsed"], 1),
                    "llm_calls": r["stats"]["calls"],
                }
                for r in results
            ],
        }
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{GREEN}已写 JSON → {out_path}{RESET}")

    print()


if __name__ == "__main__":
    asyncio.run(main())
