"""Pre-turn / mid-flight 长期记忆召回——CLI multi 与 Web 共用。

解决：
  - Web 原先 `_recalled_memories=""` 断链
  - multi 只 Analysis 看见记忆；Router/SQL 需要 grounded 片段
  - Router 改写 / clarify 后语义变了 → mid-flight 再召
  - Self-Query（year / memory_type）在 pre-turn 路径落地
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from harness.memory.memory_controller import (
    is_chitchat,
    is_meta_memory,
    is_meta_question,
    should_remember,
)

logger = logging.getLogger(__name__)

_SCORE_MIN = 0.3
_TOP_K = 3
_SQL_MAX_CHARS = 1400
_ROUTER_MAX_CHARS = 1600

_vm = None
_rag = None
_ready = False


@dataclass
class RecallBundle:
    """一轮召回结果。"""

    text: str = ""
    meta_hint: str = ""
    memories: list[dict] = field(default_factory=list)
    parsed: dict = field(default_factory=dict)
    source: str = ""  # self_query | rag | recent | empty


def ensure_memory_stack(client=None) -> tuple[Any, Any]:
    """惰性初始化 VectorMemory + RAGPipeline，并注入 search_memory Tool 依赖。

    CLI main.py / Web server 都可调用；重复调用是幂等的。
    """
    global _vm, _rag, _ready
    from harness.tools.knowledge import (
        set_llm_client,
        set_rag_pipeline,
        set_vector_memory,
    )

    if client is not None:
        set_llm_client(client)

    if _ready and _vm is not None:
        if client is not None and _rag is not None and getattr(_rag, "llm", None) is not client:
            _rag.llm = client
        return _vm, _rag

    from harness.memory.long_term_memory import RAGPipeline
    from harness.memory.vector_store import VectorMemory

    if _vm is None:
        _vm = VectorMemory(collection_name="conversations")
    set_vector_memory(_vm)

    if _rag is None:
        _rag = RAGPipeline(vector_db=_vm, llm_client=client)
    elif client is not None:
        _rag.llm = client
    set_rag_pipeline(_rag)
    _ready = True
    return _vm, _rag


def format_memory_for_router(text: str, max_chars: int = _ROUTER_MAX_CHARS) -> str:
    if not (text or "").strip():
        return ""
    body = text.strip()
    if len(body) > max_chars:
        body = body[: max_chars - 1] + "…"
    return (
        "[历史相关对话]\n"
        "写 plan.task 时必须把指代消解成完整具体任务（地区/口径/时间/状态等），"
        "禁止把「上次那个」「刚才的」原样传给 sql。\n"
        f"{body}"
    )


def format_memory_for_sql(text: str, max_chars: int = _SQL_MAX_CHARS) -> str:
    if not (text or "").strip():
        return ""
    body = text.strip()
    if len(body) > max_chars:
        body = body[: max_chars - 1] + "…"
    return (
        "[历史口径/指代消解——写 SQL 时必须遵守；勿编造库中不存在的实体]\n"
        f"{body}\n"
        "若仍不够（尤其是「上次」「之前」），可调用 search_memory 按当前任务再检索。"
    )


def _join_plan_tasks(plan: list | None) -> str:
    if not plan:
        return ""
    parts = []
    for step in plan:
        if not isinstance(step, dict):
            continue
        agent = step.get("agent") or ""
        task = (step.get("task") or "").strip()
        if agent in ("sql", "hive", "hbase", "strategy", "analysis") and task:
            parts.append(task)
    return "；".join(parts)


def should_rerecall(original_query: str, rewritten: str) -> bool:
    """改写后语义足够不同才再召，避免无意义二次 embedding。"""
    o = (original_query or "").strip()
    r = (rewritten or "").strip()
    if not r or r == o:
        return False
    if o and o in r and len(r) <= len(o) + 8:
        return False
    if abs(len(r) - len(o)) >= 8:
        return True
    return r.lower() != o.lower()


async def recall_for_turn(
    query: str,
    *,
    client=None,
    top_k: int = _TOP_K,
    score_min: float = _SCORE_MIN,
) -> RecallBundle:
    """统一 pre-turn / mid-flight 召回（含 Self-Query 元数据过滤）。"""
    q = (query or "").strip()
    if not q:
        return RecallBundle(source="empty")

    try:
        vm, rag = ensure_memory_stack(client)
    except Exception as e:
        logger.warning("preturn_recall: memory stack init failed: %s", e)
        return RecallBundle(source="empty")

    if is_chitchat(q):
        return RecallBundle(source="empty")

    memories: list[dict] = []
    parsed: dict = {}
    source = "empty"

    if is_meta_question(q):
        try:
            recent = vm.list_recent(limit=20) if hasattr(vm, "list_recent") else []
            memories = [
                m for m in recent
                if not is_meta_memory(m.get("text", ""))
            ][:top_k]
            source = "recent"
        except Exception as e:
            logger.warning("preturn_recall: list_recent failed: %s", e)
            memories = []
    else:
        try:
            from harness.tools import knowledge as knowledge_mod

            llm = knowledge_mod._llm_client or client
            if llm is not None and vm is not None:
                from harness.context.self_query import self_query_retrieve

                results, parts = await self_query_retrieve(
                    q,
                    vm,
                    llm,
                    top_k=top_k,
                    reranker=rag,
                )
                memories = [
                    m for m in results
                    if (m.get("score") is None or float(m.get("score", 0)) >= score_min)
                ]
                parsed = parts if isinstance(parts, dict) else {}
                source = "self_query"
            elif rag is not None:
                memories = await rag.retrieve(
                    q, top_k=top_k, use_hyde=True, use_rerank=True,
                )
                memories = [
                    m for m in memories
                    if float(m.get("score", 0)) >= score_min
                ]
                source = "rag"
            else:
                memories = [
                    m for m in (vm.recall(q, top_k=top_k) if vm is not None else [])
                    if float(m.get("score", 0)) >= score_min
                ]
                source = "vector"
        except Exception as e:
            logger.warning("preturn_recall: self_query/rag failed, plain recall: %s", e)
            try:
                memories = [
                    m for m in (vm.recall(q, top_k=top_k) if vm is not None else [])
                    if float(m.get("score", 0)) >= score_min
                ]
                source = "vector"
            except Exception as e2:
                logger.warning("preturn_recall: plain recall failed: %s", e2)
                memories = []
                source = "empty"

    try:
        from harness.memory.episode_memory import expand_episode_hit
        texts = []
        for m in memories:
            expanded = expand_episode_hit(m) if m else ""
            if (expanded or "").strip():
                texts.append(expanded.strip())
        text = "\n\n".join(texts)
    except Exception:
        text = "\n\n".join(
            (m.get("text") or "").strip()
            for m in memories
            if (m.get("text") or "").strip()
        )
    meta_hint = ""
    if is_meta_question(q) and text:
        meta_hint = (
            "\n[提示] 用户在问「上一次/刚才问了什么」。"
            "请以下面时间最近的一条业务问答为准回答，不要编造更早的话题。\n"
        )
    return RecallBundle(
        text=text,
        meta_hint=meta_hint,
        memories=memories,
        parsed=parsed,
        source=source,
    )


async def maybe_rerecall(
    original_query: str,
    plan: list | None,
    current_text: str,
    *,
    client=None,
    clarified_query: str = "",
) -> str:
    """Router 扩写 task / clarify 后按新语义再召；无变化则保留 current_text。"""
    clarified = (clarified_query or "").strip()
    seed = clarified or _join_plan_tasks(plan)
    if not seed:
        return current_text or ""
    probe = seed
    orig = (original_query or "").strip()
    if orig and orig not in probe:
        probe = f"{orig}\n{seed}"

    force = bool(clarified and clarified != orig)
    if not force and not should_rerecall(orig or (current_text or ""), probe):
        return current_text or ""

    bundle = await recall_for_turn(probe, client=client)
    if bundle.text:
        print(f"   🔁 mid-flight recall ({bundle.source}): {len(bundle.memories)} hits")
        return bundle.text
    return current_text or ""


async def remember_turn(
    query: str,
    answer: str,
    *,
    session_id: str = "default",
    client=None,
) -> None:
    """把本轮问答记入情节缓冲（每 N 轮写 1 条 episode 向量）。元问题跳过。

    不再逐轮写 conversation 向量——避免几千轮变成几千条低价值碎片。
    """
    if not should_remember(query):
        return
    try:
        vm, _ = ensure_memory_stack(client)
    except Exception:
        return
    try:
        from harness.memory.episode_memory import record_and_maybe_flush
        await record_and_maybe_flush(session_id, query, answer, vm, client=client)
    except Exception as e:
        logger.warning("preturn_recall: episode remember failed: %s", e)
