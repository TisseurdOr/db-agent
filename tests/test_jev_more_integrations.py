"""Jev 的另外三处接入：语义门 / rerank / reflection 快速门。

共同约定：**优先 Jev，未启用或取不到概率则完整回退原路径**，绝不因 Jev 失败
而改变原有行为。
"""

import pytest

from harness import jev_client


@pytest.fixture(autouse=True)
def _clear_jev_env(monkeypatch):
    for var in ("OPENROUTER_API_KEY", "JEV_API_KEY", "JEV_MODEL"):
        monkeypatch.delenv(var, raising=False)


# ── ① 语义门 semantic_verify ──

def test_semantic_verify_uses_jev(monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "k")

    def fake_sync(state, questions, **kw):
        return {"answers": {"answers_question": {"type": "noul", "noul": 0.9}}}

    monkeypatch.setattr(jev_client, "decide_sync", fake_sync)
    from harness.memory.feedback import semantic_verify
    assert semantic_verify("各部门销售额", "SELECT SUM(total) FROM orders") is True


def test_semantic_verify_jev_rejects(monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "k")
    monkeypatch.setattr(
        jev_client, "decide_sync",
        lambda s, q, **kw: {"answers": {"answers_question": {"type": "noul", "noul": 0.05}}},
    )
    from harness.memory.feedback import semantic_verify
    assert semantic_verify("各部门销售额", "SELECT COUNT(*) FROM customers") is False


def test_semantic_verify_falls_back_when_jev_disabled(monkeypatch):
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    from harness.memory.feedback import semantic_verify
    # 无 Jev、无 Kimi → 放行（原降级语义不变）
    assert semantic_verify("q", "SELECT 1") is True


# ── ② rerank ──

@pytest.mark.asyncio
async def test_rerank_uses_jev_probabilities(monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "k")
    # RAGPipeline 构造要求 embedding 配置存在（本用例走 Jev 打分，不会真调 embedding）
    monkeypatch.setenv("EMBEDDING_API_KEY", "x")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://localhost:9")

    async def fake_decide(state, questions, **kw):
        # 第 2 个候选最相关
        return {"answers": {
            "c0": {"type": "noul", "noul": 0.1},
            "c1": {"type": "noul", "noul": 0.9},
            "c2": {"type": "noul", "noul": 0.4},
        }}

    monkeypatch.setattr(jev_client, "decide", fake_decide)

    from harness.memory.long_term_memory import RAGPipeline
    from harness.memory.vector_store import VectorMemory

    class NoLLM:
        def __getattr__(self, name):
            raise AssertionError("Jev 生效时不该调 LLM")

    rag = RAGPipeline(vector_db=VectorMemory(collection_name="t_rerank"), llm_client=NoLLM())
    out = await rag._rerank("q", [{"text": "a"}, {"text": "b"}, {"text": "c"}], 2)
    assert [c["text"] for c in out] == ["b", "c"]


# ── ③ reflection 快速门 ──

@pytest.mark.asyncio
async def test_reflection_jev_pass_skips_llm(monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "k")

    async def fake_decide(state, questions, **kw):
        return {"answers": {"needs_redo": {"type": "noul", "noul": 0.1}}}

    monkeypatch.setattr(jev_client, "decide", fake_decide)

    from harness.observation.tracer import TraceContext
    from harness.orchestration.multi.nodes import node_reflection

    calls = {"n": 0}

    class FakeMessages:
        async def create(self, **kw):
            calls["n"] += 1
            raise AssertionError("Jev 判定合格时不该调 LLM")

    class FakeClient:
        messages = FakeMessages()

    cfg = {"configurable": {"_client": FakeClient(), "_model": "m", "_trace": TraceContext("q")}}
    st = {"query": "q", "final_answer": "答", "results": {}, "_reflection_attempts": 0}
    out = await node_reflection(st, cfg)
    assert calls["n"] == 0
    assert out.get("next") == "done"
