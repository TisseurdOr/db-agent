"""知识库召回率评测 — 对比「关键词打分」vs「向量语义检索」。

19 篇文档每篇一条自然语言 query，golden set 内嵌。算 hit@k + MRR，
量化「关键词 → 向量」迁移带来的召回提升。query 故意混入口语改写
（如「客户分几个等级」「新员工配什么电脑」），这些关键词往往 miss、
向量能命中，正是语义检索的价值点。

用法:
    uv run python -m tests.eval_knowledge_base
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import harness.tools.knowledge as kb

# (query, 期望命中的文档 title)
GOLDEN = [
    ("销售提成怎么算", "销售提成制度"),
    ("客户分几个等级", "客户分级标准"),
    ("产品卖多少钱", "产品定价说明"),
    ("买了东西能退吗", "产品退换政策"),
    ("几点上班下班", "考勤制度"),
    ("年假有几天", "休假制度"),
    ("绩效怎么评", "绩效考核制度"),
    ("招聘面试几轮", "招聘流程"),
    ("员工有哪些福利", "员工福利政策"),
    ("出差住宿报销多少", "报销制度"),
    ("采购要走什么流程", "采购流程"),
    ("预算超支怎么处理", "预算管理制度"),
    ("数据安全等级划分", "数据安全管理制度"),
    ("新员工配什么电脑", "IT 设备管理"),
    ("今年公司的战略是什么", "2026年公司战略"),
    ("项目立项流程", "项目管理流程"),
    ("HBase 扫描表命令", "HBase操作参考"),
    ("Hive 建表结构", "Hive/Hue表结构参考"),
    ("Hive 和 Impala 区别", "HiveQL与Impala语法差异"),
]

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RESET = "\033[0m"
BOLD = "\033[1m"


def find_rank(ranked: list[dict], expected: str) -> int | None:
    for i, r in enumerate(ranked):
        if expected in r.get("title", ""):
            return i + 1
    return None


def evaluate(search_fn, top_k=5):
    hits = {1: 0, 3: 0, 5: 0}
    mrr = 0.0
    detail = []
    for query, expected in GOLDEN:
        ranked = search_fn(query, top_k=top_k)
        rank = find_rank(ranked, expected)
        for k in hits:
            if rank and rank <= k:
                hits[k] += 1
        if rank:
            mrr += 1.0 / rank
        detail.append((query, expected, rank, ranked))
    n = len(GOLDEN)
    return {
        **{f"hit@{k}": hits[k] / n for k in hits},
        "mrr": mrr / n,
    }, detail


def main():
    import argparse

    parser = argparse.ArgumentParser(description="知识库召回率评测")
    parser.add_argument("--offline", action="store_true",
                        help="用离线 fake_embedding（字符哈希近似，不调真实 API）")
    args = parser.parse_args()

    from harness.memory.vector_store import VectorMemory
    from tests.fake_embedding import fake_embedding

    # 独立 collection，避免污染生产/测试共用的 knowledge_base 索引
    col = "knowledge_base_eval"
    try:
        VectorMemory(collection_name=col).drop()
    except Exception:
        pass

    if args.offline:
        vm = VectorMemory(collection_name=col, embed_fn=fake_embedding)
        tag = "离线 fake_embedding（字符近似，非真实语义）"
    else:
        vm = VectorMemory(collection_name=col)
        tag = "真实 embedding"

    for title, content in kb._KNOWLEDGE_BASE.items():
        vm.remember(content, memory_type="knowledge",
                    metadata={"title": title, "category": kb._DOC_CATEGORIES.get(title, "")})
    kb._kb_memory = vm

    kw_metrics, kw_detail = evaluate(kb._keyword_search)
    vec_metrics, vec_detail = evaluate(kb._vector_search)

    print(f"\n{BOLD}{CYAN}{'=' * 60}{RESET}")
    print(f"{BOLD}{CYAN}  知识库召回率（19 篇，每篇 1 query）{RESET}")
    print(f"{BOLD}{CYAN}  {tag}{RESET}")
    print(f"{BOLD}{CYAN}{'=' * 60}{RESET}\n")

    print(f"{BOLD}{'指标':<10}{'关键词':>10}{'向量':>10}{RESET}")
    print(f"  {'-' * 30}")
    for key in ("hit@1", "hit@3", "hit@5", "mrr"):
        kw, vec = kw_metrics[key], vec_metrics[key]
        kws = f"{kw:.1%}" if key.startswith("hit") else f"{kw:.3f}"
        vecs = f"{vec:.1%}" if key.startswith("hit") else f"{vec:.3f}"
        print(f"  {key:<10}{kws:>10}{vecs:>10}")

    # 逐条：向量找回关键词 miss 的
    improved = [
        (q, e, kw_r, vec_r)
        for (q, e, kw_r, _), (_, _, vec_r, _) in zip(kw_detail, vec_detail)
        if kw_r is None and vec_r is not None
    ]
    print(f"\n{BOLD}向量找回、关键词 miss 的 {len(improved)} 条（语义检索价值点）{RESET}")
    for q, e, kw_r, vec_r in improved:
        print(f"  {GREEN}✓{RESET} 「{q}」 → 「{e}」 rank={vec_r}")

    # 两者都 miss 的
    both_miss = [
        (q, e) for (q, e, kw_r, _), (_, _, vec_r, _) in zip(kw_detail, vec_detail)
        if kw_r is None and vec_r is None
    ]
    if both_miss:
        print(f"\n{BOLD}两者都 miss 的 {len(both_miss)} 条{RESET}")
        for q, e in both_miss:
            print(f"  {RED}✗{RESET} 「{q}」 → 「{e}」")


if __name__ == "__main__":
    main()
