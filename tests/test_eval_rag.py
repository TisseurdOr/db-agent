"""eval_rag_retrieval 纯函数单测 —— 不碰网络/embedding。"""

from tests.eval_rag_retrieval import best_rank, compute_metrics


def test_best_rank_finds_first_match():
    items = [{"sql": "SELECT * FROM products"}, {"sql": "SELECT * FROM orders JOIN departments"}]
    assert best_rank(items, {"orders"}) == 2
    assert best_rank(items, {"products"}) == 1


def test_best_rank_case_insensitive():
    items = [{"sql": "select * from ORDERS"}]
    assert best_rank(items, {"orders"}) == 1


def test_best_rank_miss():
    items = [{"sql": "SELECT 1"}]
    assert best_rank(items, {"orders"}) is None
    assert best_rank([], {"orders"}) is None


def test_compute_metrics():
    pairs = [
        ({"query": "a"}, 1),
        ({"query": "b"}, 3),
        ({"query": "c"}, None),
    ]
    m = compute_metrics(pairs)
    assert m["n"] == 3
    assert m["hit@1"] == 1 / 3
    assert m["hit@3"] == 2 / 3
    assert m["miss"] == 1
    # MRR = 1/1 + 1/3 = 1.3333 / 3
    assert abs(m["mrr"] - (1 + 1 / 3) / 3) < 1e-9


def test_compute_metrics_empty():
    m = compute_metrics([])
    assert m["n"] == 0 and m["mrr"] == 0.0 and m["hit@1"] == 0.0
