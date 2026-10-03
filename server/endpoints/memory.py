"""GET /api/memory — waku-style Memory view over db-agent stores.

Maps our persistence to three-ish pillars (+ short-term / feedback):
  Short-term  → in-memory / Redis session messages
  Semantic    → demo.db user_memory (structured facts)
  Vector      → Chroma collections (conversations / sql_examples / schema / KB)
  Procedural  → sql_examples few-shot + metric templates
  Feedback    → user_feedback thumbs (learning loop)

Read-only. Soft-fails if Chroma / sessions unavailable.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from harness.observation.tracer import TRACE_DIR

router = APIRouter()
ROOT = Path(__file__).resolve().parents[2]
DEMO_DB = ROOT / "db" / "demo.db"
METRIC_DB = ROOT / "db" / "metric_registry.db"
CHROMA_DIR = Path(os.getenv("VECTOR_PERSIST_DIR", str(ROOT / "harness" / "memory" / "chroma_db")))
AGENT_STATE_DB = ROOT / "db" / "agent_state.db"


def _ro(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _user_memory(limit: int = 50) -> list[dict]:
    conn = _ro(DEMO_DB)
    if not conn:
        return []
    try:
        rows = conn.execute(
            "SELECT id, user_id, memory_type, content, created_at, access_count "
            "FROM user_memory ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def _feedback(limit: int = 30) -> list[dict]:
    conn = _ro(DEMO_DB)
    if not conn:
        return []
    try:
        rows = conn.execute(
            "SELECT id, rating, query, comment, created_at, trace_id "
            "FROM user_feedback ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def _count(path: Path, table: str) -> int:
    conn = _ro(path)
    if not conn:
        return 0
    try:
        return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


def _chroma_collections() -> list[dict[str, Any]]:
    if not CHROMA_DIR.exists():
        return []
    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        out = []
        for col in client.list_collections():
            try:
                n = col.count()
            except Exception:
                n = 0
            out.append({"name": col.name, "count": n})
        return sorted(out, key=lambda x: x["name"])
    except Exception:
        # Fallback: directory names only
        return [
            {"name": p.name, "count": None}
            for p in sorted(CHROMA_DIR.iterdir())
            if p.is_dir() and not p.name.startswith(".")
        ]


def _session_stats() -> dict[str, Any]:
    """Short-term: sessions API list + live ConversationManager windows."""
    sessions: list[dict[str, Any]] = []
    try:
        from server.endpoints import sessions as sess_mod
        store = getattr(sess_mod, "_memory", None) or {}
        for sid, msgs in store.items():
            sessions.append({
                "id": sid,
                "messages": len(msgs or []),
                "preview": list(msgs or [])[-8:],
            })
    except Exception:
        pass

    # Live runners (web path) — richer window + summary
    live: list[dict[str, Any]] = []
    try:
        from server.runner_wrapper import runner_registry
        for sid, (runner, _) in list(getattr(runner_registry, "_runners", {}).items()):
            conv = getattr(runner, "_conversation", None)
            if conv is None:
                continue
            try:
                est = conv.token_estimate()
            except Exception:
                est = {"recent_msgs": len(getattr(conv, "messages", []) or []), "compressed_msgs": 0}
            live.append({
                "id": sid,
                "recent_msgs": est.get("recent_msgs", 0),
                "compressed_msgs": est.get("compressed_msgs", 0),
                "summary": (getattr(conv, "summary", "") or "")[:400],
                "messages": [
                    {"role": m.get("role"), "content": str(m.get("content", ""))[:240]}
                    for m in (getattr(conv, "messages", None) or [])[-10:]
                ],
            })
    except Exception:
        pass

    message_count = sum(s["messages"] for s in sessions)
    # Prefer live window counts when sessions store empty but runners have data
    if message_count == 0 and live:
        message_count = sum(int(x.get("recent_msgs") or 0) for x in live)

    return {
        "session_count": max(len(sessions), len(live)),
        "message_count": message_count,
        "sessions": sessions[:20],
        "live": live[:20],
    }


def _recent_traces(limit: int = 8) -> list[dict]:
    from harness.observation.tracer import _read_traces

    if not TRACE_DIR.exists():
        return []
    traces: list[dict] = []
    for f in sorted(TRACE_DIR.glob("*.jsonl"), reverse=True):
        traces.extend(_read_traces(f))
    traces.sort(key=lambda t: t.get("started_at", ""), reverse=True)
    out = []
    for t in traces[:limit]:
        out.append({
            "trace_id": t.get("trace_id"),
            "query": str(t.get("query", ""))[:120],
            "started_at": t.get("started_at"),
            "elapsed": t.get("elapsed"),
        })
    return out




def _checkpoint_rows(limit: int = 40) -> list[dict[str, Any]]:
    """List LangGraph checkpoints from agent_state.db (SQLite fallback)."""
    if not AGENT_STATE_DB.exists():
        return []
    conn = _ro(AGENT_STATE_DB)
    if not conn:
        return []
    try:
        rows = conn.execute(
            "SELECT thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, metadata "
            "FROM checkpoints ORDER BY checkpoint_id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out = []
        for r in rows:
            meta = r["metadata"]
            if isinstance(meta, (bytes, bytearray)):
                try:
                    meta = meta.decode("utf-8", errors="replace")
                except Exception:
                    meta = repr(meta)[:200]
            elif meta is not None:
                meta = str(meta)
            out.append({
                "thread_id": r["thread_id"],
                "checkpoint_ns": r["checkpoint_ns"],
                "checkpoint_id": r["checkpoint_id"],
                "parent_checkpoint_id": r["parent_checkpoint_id"],
                "type": r["type"],
                "metadata": (meta or "")[:800],
            })
        return out
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def _live_checkpoint_snapshots(limit: int = 12) -> list[dict[str, Any]]:
    """Peek live runner graph state (in-process) — what checkpoint would resume."""
    out: list[dict[str, Any]] = []
    try:
        from server.runner_wrapper import runner_registry
        for sid, (runner, _) in list(getattr(runner_registry, "_runners", {}).items())[:limit]:
            thread_id = getattr(runner, "thread_id", f"web-{sid}")
            entry: dict[str, Any] = {
                "session_id": sid,
                "thread_id": thread_id,
                "keys": [],
                "next": None,
                "summary": "",
            }
            try:
                # Prefer last_state if present (no async needed)
                state = getattr(runner, "_last_state", None) or {}
                if isinstance(state, dict) and state:
                    keys = sorted(k for k in state.keys() if not str(k).startswith("_") or k in {
                        "_conversation_summary", "_recalled_memories", "_stats",
                    })
                    entry["keys"] = keys[:40]
                    entry["next"] = state.get("next")
                    q = str(state.get("query") or "")[:160]
                    fa = str(state.get("final_answer") or "")[:160]
                    entry["summary"] = q or fa
                    entry["has_interrupt"] = bool(state.get("__interrupt__"))
                else:
                    entry["summary"] = "(no completed turn state yet)"
            except Exception as e:
                entry["summary"] = f"(unavailable: {type(e).__name__})"
            out.append(entry)
    except Exception:
        pass
    return out


@router.get("/memory/checkpoints")
async def memory_checkpoints(limit: int = 40):
    """Checkpoint browser: SQLite agent_state.db + live runner snapshots.

    Checkpoint = LangGraph graph-state persistence (HITL resume / thread continuity).
    Closest Architecture mapping: Short-term / session thread — not Semantic facts.
    """
    rows = _checkpoint_rows(limit)
    live = _live_checkpoint_snapshots()
    return {
        "arch_node": "Short-term (session thread)",
        "arch_group": "MEMORY / harness",
        "arch_note": (
            "Checkpoint is LangGraph state snapshots keyed by thread_id "
            "(HITL resume, multi-turn continuity). Shown under Memory because it is "
            "session-scoped persistence — not Semantic long-term facts. "
            "Raw tables also live in Database → agent_state.db."
        ),
        "path": str(AGENT_STATE_DB.relative_to(ROOT)),
        "sqlite_count": len(rows),
        "checkpoints": rows,
        "live": live,
    }

@router.get("/memory/sql_examples")
async def memory_sql_examples():
    """SQL few-shot 样例库：seed 基线（只读）+ learned（自学习回流，可回滚）。"""
    from harness.context.sql_examples import SEED_EXAMPLES, list_learned

    learned = list_learned()
    seed = [
        {"id": f"seed_{i}", "question": e["question"], "sql": e["sql"], "source": "seed"}
        for i, e in enumerate(SEED_EXAMPLES)
    ]
    return {"seed": seed, "learned": learned, "count": len(learned)}


@router.post("/memory/sql_examples/purge")
async def memory_sql_examples_purge():
    """回滚：删除所有非 seed 样例（回到 seed 基线）。返回删除条数。"""
    from harness.context.sql_examples import purge_learned

    return {"purged": purge_learned()}


@router.get("/memory")
async def memory_overview():
    facts = _user_memory()
    feedback = _feedback()
    chroma = _chroma_collections()
    sessions = _session_stats()
    metrics_n = _count(METRIC_DB, "metric_registry")
    sql_ex = next((c for c in chroma if c["name"] == "sql_examples"), None)
    conv = next((c for c in chroma if c["name"] == "conversations"), None)

    pillars = [
        {
            "id": "short",
            "title": "Short-term",
            "count_label": f"{sessions['message_count']} msgs · {sessions['session_count']} sessions",
            "description": "Working memory for the active chat — sliding window / summary compression.",
            "arch_node": "Short-term",
            "arch_group": "MEMORY",
            "arch_note": "1:1 with Architecture → MEMORY → Short-term (session window).",
        },
        {
            "id": "semantic",
            "title": "Semantic",
            "count_label": f"{len(facts)} facts",
            "description": "Durable structured memory in user_memory (preference / insight / note / entity).",
            "arch_node": "Long-term",
            "arch_group": "MEMORY",
            "arch_note": "Part of Architecture → MEMORY → Long-term (structured facts / user_memory).",
        },
        {
            "id": "vector",
            "title": "Vector",
            "count_label": f"{len(chroma)} collections",
            "description": "Chroma (or Milvus) — conversations, schema linking, few-shot, knowledge base.",
            "arch_node": "Vector store",
            "arch_group": "MEMORY",
            "arch_note": "1:1 with Architecture → MEMORY → Vector store (Chroma · HyDE).",
        },
        {
            "id": "procedural",
            "title": "Procedural",
            "count_label": f"{(sql_ex or {}).get('count') or 0} SQL examples · {metrics_n} metrics",
            "description": "How to act — learned Q→SQL examples + metric SQL templates.",
            "arch_node": "Long-term",
            "arch_group": "MEMORY",
            "arch_note": "Part of Architecture → MEMORY → Long-term (self-learning / few-shot / metrics).",
        },
    ]

    return {
        "pillars": pillars,
        "user_memory": facts,
        "feedback": feedback,
        "chroma": chroma,
        "sessions": sessions,
        "metric_count": metrics_n,
        "sql_examples_count": (sql_ex or {}).get("count"),
        "conversations_count": (conv or {}).get("count"),
        "recent_traces": _recent_traces(),
        "arch_legend": {
            "title": "How this page maps to Architecture → MEMORY",
            "boxes": [
                {"arch": "Short-term", "pillars": ["Short-term"]},
                {"arch": "Long-term", "pillars": ["Semantic", "Procedural"]},
                {"arch": "Vector store", "pillars": ["Vector"]},
                {"arch": "save / learn → memory", "pillars": ["Feedback"]},
                {"arch": "Short-term (session thread)", "pillars": ["Checkpoint"]},

            ],
        },
        "feedback_arch": {
            "arch_node": "save / learn → memory",
            "arch_group": "edge (Reply → MEMORY)",
            "arch_note": "Not a MEMORY box — the dashed edge from Reply into MEMORY on Architecture.",
        },
        "paths": {
            "demo_db": str(DEMO_DB.relative_to(ROOT)),
            "chroma": str(CHROMA_DIR.relative_to(ROOT)),
            "traces": str(TRACE_DIR.relative_to(ROOT)),
            "agent_state": str(AGENT_STATE_DB.relative_to(ROOT)),
        },
        "checkpoint_arch": {
            "arch_node": "Short-term (session thread)",
            "arch_group": "MEMORY / harness",
            "arch_note": "LangGraph checkpoints (agent_state.db / Redis) — HITL resume & thread continuity.",
        },
    }
