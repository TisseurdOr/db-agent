"""Rerank 输出解析器单元测试 —— 纯逻辑，零 API 成本。"""

from harness.memory.long_term_memory import _parse_rerank_output


def _candidates(n=10):
    return [{"text": f"候选{i}"} for i in range(n)]


def test_json_scores_sorted_by_score():
    text = '[{"index": 3, "score": 0.95}, {"index": 1, "score": 0.4}]'
    out = _parse_rerank_output(text, _candidates(), top_k=5)
    assert [c["text"] for c in out] == ["候选3", "候选1"]


def test_json_scores_top_k_truncated():
    text = '[{"index": 0, "score": 0.9}, {"index": 1, "score": 0.8}, {"index": 2, "score": 0.7}]'
    out = _parse_rerank_output(text, _candidates(), top_k=2)
    assert [c["text"] for c in out] == ["候选0", "候选1"]


def test_duplicate_index_deduped():
    text = '[{"index": 2, "score": 0.9}, {"index": 2, "score": 0.8}, {"index": 5, "score": 0.7}]'
    out = _parse_rerank_output(text, _candidates(), top_k=5)
    assert [c["text"] for c in out] == ["候选2", "候选5"]


def test_legacy_comma_list_supported():
    out = _parse_rerank_output("3, 7, 12", _candidates(), top_k=5)
    assert [c["text"] for c in out] == ["候选3", "候选7"]


def test_chinese_comma_supported():
    out = _parse_rerank_output("3，1，5", _candidates(), top_k=5)
    assert [c["text"] for c in out] == ["候选3", "候选1", "候选5"]


def test_invalid_index_ignored():
    text = '[{"index": 99, "score": 0.9}, {"index": 1, "score": 0.8}]'
    out = _parse_rerank_output(text, _candidates(), top_k=5)
    assert [c["text"] for c in out] == ["候选1"]


def test_garbage_falls_back_to_original_order():
    out = _parse_rerank_output("我不太确定，随便吧", _candidates(), top_k=3)
    assert [c["text"] for c in out] == ["候选0", "候选1", "候选2"]


def test_empty_text_falls_back():
    out = _parse_rerank_output("", _candidates(), top_k=2)
    assert [c["text"] for c in out] == ["候选0", "候选1"]
