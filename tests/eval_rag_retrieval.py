"""RAG 检索评测（补全）— 量化 SQL few-shot 检索 与 Schema Linking 的质量。

已有 RAG eval：知识库(eval_knowledge_base) / 记忆召回(eval_memory) / 消融(eval_retrieval_ablation)。
本脚本补两个 SQL Agent 管线里的 RAG 组件：
  1. SQL few-shot（sql_examples.SQLExampleStore.retrieve）
     —— 给问题召回已验证 Q→SQL 样例，注入 SQL Agent prompt
  2. Schema Linking（schema_discovery.SchemaDiscovery.search）
     —— 给问题召回相关表字段，预注入 schema context

Golden set（内嵌）：
  - few-shot：期望命中哪些表（样例 SQL 里应出现该表）
  - schema：期望召回到哪些字段（table.column）

指标：hit@k（前 k 条里有期望内容）+ MRR（期望内容的最优排名倒数）+ 失败明细。
只调 embedding（零 LLM），几十条几秒钟。

用法:
    uv run python -m tests.eval_rag_retrieval            # 两项都跑
    uv run python -m tests.eval_rag_retrieval --sql      # 只跑 few-shot
    uv run python -m tests.eval_rag_retrieval --schema   # 只跑 schema linking
    uv run python -m tests.eval_rag_retrieval --limit 3  # 冒烟
"""

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RESET = "\033[0m"
BOLD = "\033[1m"


# ── Golden set ────────────────────────────────────────────────────────────
# few-shot 条目:  {query, expected_tables: [...]}
# schema 条目:    {query, expected_fields: ["orders.total", "customers.region", ...]}
# 期望值都从 db/seed.py 的真实表字段 + sql_examples 的 seed 样例里取，不凭感觉写。

FEWSHOT_GOLDEN = [
    {"query": "各部门的订单总金额是多少", "expected_tables": ["orders", "departments"]},
    {"query": "华东地区有哪些客户", "expected_tables": ["customers"]},
    {"query": "销量最高的产品是哪个", "expected_tables": ["orders", "products"]},
    {"query": "对比最近两个月的销售额", "expected_tables": ["orders"]},
    {"query": "已取消的订单有多少单，占比多少", "expected_tables": ["orders"]},
    {"query": "每个客户等级的平均订单金额", "expected_tables": ["orders", "customers"]},
    {"query": "各区域客户的订单金额分布", "expected_tables": ["orders", "customers"]},
    {"query": "各部门订单金额的同比环比", "expected_tables": ["orders", "departments"]},
]

SCHEMA_GOLDEN = [
    {"query": "华东区客户按行业分布", "expected_fields": ["customers.region", "customers.industry"]},
    {"query": "各部门预算和人数对比", "expected_fields": ["departments.budget", "departments.headcount"]},
    {"query": "员工薪资分布", "expected_fields": ["employees.salary", "employees.dept_id"]},
    {"query": "各产品类别的销量和单价", "expected_fields": ["products.category", "products.unit_price", "orders.quantity"]},
    {"query": "已完成订单的总金额按月统计", "expected_fields": ["orders.total", "orders.status", "orders.created_at"]},
    {"query": "Hive 订单表的分区和区域", "expected_fields": ["ods_orders_hive.dt", "ods_orders_hive.region"]},
    {"query": "用户事件类型统计", "expected_fields": ["dwd_user_events.event_type"]},
    {"query": "订单状态为取消的订单数", "expected_fields": ["orders.status"]},
]


# ── 纯函数（可单测，不碰网络） ────────────────────────────────────────────

def best_rank(items: list, expected_set: set) -> int | None:
    """在按相关性排序的 items 里找第一个满足期望的内容，返回 1-indexed 排名。

    items: [{"text": ...} or {"sql": ...} or {"table": ...}]
    predicate 用 expected_set 判定（文本包含任一期望串）。
    """
    for i, item in enumerate(items):
        haystack = " ".join(str(v) for v in item.values()).lower()
        if any(e.lower() in haystack for e in expected_set):
            return i + 1
    return None


def compute_metrics(pairs: list[tuple[dict, int | None]]) -> dict:
    """pairs: [(golden_entry, rank_or_None), ...] → hit@k + MRR。"""
    n = len(pairs)
    hits = {1: 0, 3: 0, 5: 0}
    mrr = 0.0
    for _, rank in pairs:
        if rank is None:
            continue
        for k in hits:
            if rank <= k:
                hits[k] += 1
        mrr += 1.0 / rank
    return {
        "n": n,
        "hit@1": hits[1] / n if n else 0.0,
        "hit@3": hits[3] / n if n else 0.0,
        "hit@5": hits[5] / n if n else 0.0,
        "mrr": mrr / n if n else 0.0,
        "miss": n - sum(1 for _, r in pairs if r is not None),
    }


# ── 两项评测 ─────────────────────────────────────────────────────────────

def eval_fewshot(limit: int | None = None) -> tuple[list, dict]:
    """SQL few-shot 检索：问题 → 样例库召回 → 检查 SQL 是否覆盖期望表。"""
    from harness.context.sql_examples import SQLExampleStore

    store = SQLExampleStore()
    store.seed()
    if store._collection.count() == 0:
        print(f"{RED}SQL 样例库为空，无法评测（检查 EMBEDDING_API_KEY）{RESET}")
        sys.exit(1)

    cases = FEWSHOT_GOLDEN[:limit] if limit else FEWSHOT_GOLDEN
    pairs = []
    details = []
    for entry in cases:
        examples = store.retrieve(entry["query"], top_k=5)
        ranked = [{"sql": ex["sql"], "score": ex.get("score")} for ex in examples]
        rank = best_rank(ranked, set(entry["expected_tables"]))
        pairs.append((entry, rank))
        details.append((entry, examples, rank))
    return details, compute_metrics(pairs)


def eval_schema(limit: int | None = None) -> tuple[list, dict]:
    """Schema Linking：问题 → 相关字段检索 → 检查期望字段是否被召回。"""
    from harness.context.schema_discovery import SchemaDiscovery

    sd = SchemaDiscovery()
    sd.build_index()
    if sd._collection.count() == 0:
        print(f"{RED}schema 索引为空，无法评测{RESET}")
        sys.exit(1)

    cases = SCHEMA_GOLDEN[:limit] if limit else SCHEMA_GOLDEN
    pairs = []
    details = []
    for entry in cases:
        fields = sd.search(entry["query"], top_k=15)
        ranked = [
            {"table": f["table"], "column": f["column"], "score": f["score"]}
            for f in fields
        ]
        # 期望字段可能命中多个：取最优排名
        best = None
        for col_id in entry["expected_fields"]:
            tbl, col = col_id.split(".", 1)
            # best_rank 看整行文本；这里限定同表更精确
            r2 = None
            for i, f in enumerate(ranked):
                if f["table"] == tbl and f["column"] == col:
                    r2 = i + 1
                    break
            if r2 is not None and (best is None or r2 < best):
                best = r2
        pairs.append((entry, best))
        details.append((entry, fields, best))
    return details, compute_metrics(pairs)


def _print_section(title: str, details: list, metrics: dict) -> None:
    print(f"\n{BOLD}{CYAN}{'=' * 60}{RESET}")
    print(f"{BOLD}{title}{RESET}")
    print(f"{'=' * 60}")
    m = metrics
    print(f"  {YELLOW}hit@1={m['hit@1']:.2f}  hit@3={m['hit@3']:.2f}  "
          f"hit@5={m['hit@5']:.2f}  MRR={m['mrr']:.2f}  miss={m['miss']}/{m['n']}{RESET}")
    for entry, hits, rank in details:
        ok = rank is not None
        mark = f"{GREEN}✓{RESET}" if ok else f"{RED}✗{RESET}"
        expected = entry.get("expected_tables") or [c.split(".", 1)[1] for c in entry["expected_fields"]]
        print(f"  {mark} rank={rank}  「{entry['query'][:28]}」 expect={expected}")


def main():
    parser = argparse.ArgumentParser(description="RAG 检索评测（few-shot + Schema Linking）")
    parser.add_argument("--sql", action="store_true", help="只跑 SQL few-shot 检索")
    parser.add_argument("--schema", action="store_true", help="只跑 Schema Linking 检索")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 条（冒烟）")
    args = parser.parse_args()

    run_sql = args.sql or not args.schema
    run_schema = args.schema or not args.sql

    if run_sql:
        details, metrics = eval_fewshot(args.limit)
        _print_section("SQL few-shot 检索（命中期望表）", details, metrics)
    if run_schema:
        details, metrics = eval_schema(args.limit)
        _print_section("Schema Linking（期望字段被召回）", details, metrics)


if __name__ == "__main__":
    main()
