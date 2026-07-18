"""Self-Query 单元 / 集成测试。

parse_self_query 用 mock LLM，不耗 API。
self_query_retrieve 的过滤/降级路径需要 embedding API。
"""

import json
import os
import uuid
from types import SimpleNamespace

import pytest
from dotenv import load_dotenv

load_dotenv()

from rag.self_query import parse_self_query, self_query_retrieve, _sanitize_filters

requires_embedding = pytest.mark.skipif(
    not os.getenv("EMBEDDING_API_KEY"),
    reason="需要 EMBEDDING_API_KEY 才能跑向量检索测试",
)


class _FakeLLM:
    """返回固定 JSON 文本的假 LLM client。"""

    def __init__(self, payload: dict | str):
        if isinstance(payload, dict):
            self._text = json.dumps(payload, ensure_ascii=False)
        else:
            self._text = payload
        self.messages = self

    def create(self, **kwargs):
        block = SimpleNamespace(type="text", text=self._text)
        return SimpleNamespace(content=[block])


@pytest.fixture
def vector_mem():
    from memory.vector_store import VectorMemory
    col_name = f"test_self_query_{uuid.uuid4().hex[:8]}"
    mem = VectorMemory(collection_name=col_name)
    yield mem
    mem.client.delete_collection(col_name)


# ─── parse / sanitize ─────────────────────────────────────────


def test_sanitize_filters_keeps_allowed_keys():
    assert _sanitize_filters({
        "memory_type": "Conversation",
        "year": 2026,
        "office": "北京",  # 不支持，丢弃
    }) == {"memory_type": "conversation", "year": "2026"}


def test_sanitize_filters_rejects_bad_memory_type():
    assert _sanitize_filters({"memory_type": "unknown"}) == {}


def test_parse_self_query_success():
    llm = _FakeLLM({
        "semantic_query": "销售分析",
        "filters": {"year": "2026", "memory_type": "conversation"},
    })
    parts = parse_self_query("查一下 2026 年的销售分析对话", llm)
    assert parts["semantic_query"] == "销售分析"
    assert parts["filters"] == {"year": "2026", "memory_type": "conversation"}


def test_parse_self_query_json_fence():
    body = json.dumps({"semantic_query": "偏好", "filters": {"memory_type": "preference"}})
    llm = _FakeLLM(f"```json\n{body}\n```")
    parts = parse_self_query("我的偏好是什么", llm)
    assert parts["semantic_query"] == "偏好"
    assert parts["filters"]["memory_type"] == "preference"


def test_parse_self_query_invalid_json_fallback():
    llm = _FakeLLM("这不是 JSON")
    parts = parse_self_query("上次那个地区数据", llm)
    assert parts["semantic_query"] == "上次那个地区数据"
    assert parts["filters"] == {}


# ─── retrieve：过滤命中 + 降级 ─────────────────────────────────


@requires_embedding
@pytest.mark.asyncio
async def test_self_query_retrieve_filters_by_year(vector_mem):
    vector_mem.remember(
        content="问: 查 2025 年订单\n答: 2025 年订单合计 100 万",
        memory_type="conversation",
        metadata={"year": "2025"},
    )
    # 至少 2 条匹配，避免 Self-Query「<2 条则降级」误触
    vector_mem.remember(
        content="问: 查 2026 年订单\n答: 2026 年订单合计 200 万",
        memory_type="conversation",
        metadata={"year": "2026"},
    )
    vector_mem.remember(
        content="问: 2026 年各地区订单\n答: 北京领先",
        memory_type="conversation",
        metadata={"year": "2026"},
    )

    llm = _FakeLLM({
        "semantic_query": "订单合计",
        "filters": {"year": "2026", "memory_type": "conversation"},
    })
    results, parts = await self_query_retrieve(
        "2026 年的订单对话", vector_mem, llm, top_k=5,
    )
    assert parts["filters"]["year"] == "2026"
    assert len(results) >= 1
    assert all(r["metadata"].get("year") == "2026" for r in results)


@requires_embedding
@pytest.mark.asyncio
async def test_self_query_retrieve_degrades_when_filters_too_strict(vector_mem):
    """filters 过严 0 命中 → 去掉业务 filters 后仍应返回结果。"""
    vector_mem.remember(
        content="问: 各地区销售额\n答: 北京最高",
        memory_type="conversation",
        metadata={"year": "2026"},
    )

    llm = _FakeLLM({
        "semantic_query": "销售额",
        "filters": {"year": "1999"},  # 库里没有
    })
    results, parts = await self_query_retrieve(
        "1999 年的销售额分析", vector_mem, llm, top_k=3,
    )
    assert parts["filters"]["year"] == "1999"
    assert len(results) >= 1
    assert "北京" in results[0]["text"]


@requires_embedding
@pytest.mark.asyncio
async def test_self_query_memory_type_override(vector_mem):
    """Tool 显式传入 memory_type 应覆盖 LLM 抽取。"""
    vector_mem.remember(
        content="用户偏好：结果按金额降序",
        memory_type="preference",
        metadata={"year": "2026"},
    )
    vector_mem.remember(
        content="用户偏好：默认输出中文表格",
        memory_type="preference",
        metadata={"year": "2026"},
    )
    vector_mem.remember(
        content="问: 销售额\n答: 本月 50 万",
        memory_type="conversation",
        metadata={"year": "2026"},
    )

    llm = _FakeLLM({
        "semantic_query": "排序偏好",
        "filters": {"memory_type": "conversation"},  # LLM 抽错了
    })
    results, parts = await self_query_retrieve(
        "我的排序偏好",
        vector_mem,
        llm,
        top_k=3,
        memory_type="preference",
    )
    assert parts["filters"]["memory_type"] == "preference"
    assert len(results) >= 1
    assert all(r["metadata"].get("memory_type") == "preference" for r in results)
