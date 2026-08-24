"""Milvus 向量后端测试（Milvus Lite 嵌入式，无服务时自动跳过）。

覆盖与 Chroma 相同的接口契约：remember/recall/过滤/forget/list_recent/search。
"""

import uuid

import pytest

from tests.fake_embedding import fake_embedding


@pytest.fixture
def milvus_mem(tmp_path):
    """独立临时 URI 的 Milvus 记忆实例；Milvus 不可用自动跳过。"""
    try:
        from harness.memory.vector_store import MilvusBackend, VectorMemory
        backend = MilvusBackend(
            collection_name=f"test_milvus_{uuid.uuid4().hex[:8]}",
            uri=str(tmp_path / "test_milvus.db"),  # 文件路径走构造参数，不放环境变量
        )
        mem = VectorMemory(embed_fn=fake_embedding, backend=backend)
    except Exception as e:  # Milvus Lite 起不来（沙箱/环境）→ 跳过
        pytest.skip(f"Milvus 不可用: {e}")
    yield mem
    try:
        mem.drop()
    except Exception:
        pass


def test_milvus_remember_and_count(milvus_mem):
    milvus_mem.remember(content="测试记忆 A", memory_type="conversation")
    assert milvus_mem.count() == 1


def test_milvus_recall_semantic(milvus_mem):
    """存三条，模糊查询应召回正确主题（与 Chroma 同一断言）。"""
    milvus_mem.remember(
        content="用户在 2026-07-10 查询了 Q2 各地区的订单金额分布。北京最高(120万)，上海次之(98万)。",
        memory_type="conversation",
    )
    milvus_mem.remember(
        content="用户查询了各部门的在职员工人数。销售部45人，研发部120人。",
        memory_type="conversation",
    )
    milvus_mem.remember(
        content="用户查询了 pending 状态的订单数量和总金额。pending 订单共 23 笔。",
        memory_type="conversation",
    )
    results = milvus_mem.recall("查一下上次那个地区销售分析", top_k=3)
    assert len(results) >= 1
    top_text = results[0]["text"]
    assert "地区" in top_text or "订单金额分布" in top_text or "120万" in top_text


def test_milvus_recall_with_type_filter(milvus_mem):
    milvus_mem.remember(content="用户偏好按降序排列查询结果", memory_type="preference")
    milvus_mem.remember(content="用户查询了 2026年6月的月度营收数据", memory_type="conversation")

    prefs = milvus_mem.recall("排序方式", memory_type="preference", top_k=3)
    assert len(prefs) >= 1
    assert any("降序" in p["text"] for p in prefs)

    convs = milvus_mem.recall("营收数据", memory_type="conversation", top_k=3)
    assert len(convs) >= 1
    assert all("营收" in c["text"] or "查询" in c["text"] for c in convs)


def test_milvus_list_recent(milvus_mem):
    milvus_mem.remember(content="最早的一条", memory_type="note")
    milvus_mem.remember(content="最新的一条", memory_type="note")
    recent = milvus_mem.list_recent(limit=2)
    assert len(recent) == 2
    assert "最新" in recent[0]["text"]


def test_milvus_forget(milvus_mem):
    mid = milvus_mem.remember(content="会被删除", memory_type="note")
    assert milvus_mem.count() == 1
    milvus_mem.forget(mid)
    assert milvus_mem.count() == 0


def test_milvus_search_with_filter(milvus_mem):
    """self_query 的低层接口：单向量 + 过滤。"""
    milvus_mem.remember(content="问: 2026 年订单\n答: 200 万", memory_type="conversation", metadata={"year": "2026"})
    vec = fake_embedding("2026 年订单")[0]
    results = milvus_mem.search(vec, top_k=3, filters={"memory_type": "conversation"})
    assert len(results) == 1
    assert "200 万" in results[0]["text"]


def test_milvus_unknown_backend_raises():
    from harness.memory.vector_store import make_backend
    with pytest.raises(ValueError):
        make_backend("bogus", "x", "y")
