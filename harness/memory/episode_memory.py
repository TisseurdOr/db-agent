"""Episode-grained long-term memory (P1).

Per-turn Q&A goes to a session turn log + in-memory buffer.
Every EPISODE_EVERY_N turns we write ONE episode vector summary
(metadata: session_id, turn_start, turn_end, memory_type=episode).

Compaction: flush remaining buffer; optionally prune old turn-level
conversation vectors (legacy per-turn writes).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import cast

logger = logging.getLogger(__name__)

_DB_DIR = Path(__file__).resolve().parents[2] / "db"
_TURN_DIR = _DB_DIR / "turns"

EPISODE_EVERY_N = int(os.getenv("EPISODE_EVERY_N", "5"))
KEEP_RECENT_TURNS = int(os.getenv("EPISODE_KEEP_RECENT_TURNS", "10"))

# session_id -> list[{turn, query, answer}]
_buffers: dict[str, list[dict]] = {}


def _turn_path(session_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)
    return _TURN_DIR / f"{safe}.jsonl"


def _next_turn_index(session_id: str) -> int:
    path = _turn_path(session_id)
    if not path.exists():
        return 1
    try:
        n = sum(1 for ln in path.open(encoding="utf-8") if ln.strip())
        return n + 1
    except OSError:
        return 1


def append_turn(session_id: str, query: str, answer: str) -> int:
    """Append one Q&A to the session turn log. Returns 1-based turn index."""
    _TURN_DIR.mkdir(parents=True, exist_ok=True)
    turn = _next_turn_index(session_id)
    row = {
        "turn": turn,
        "query": query or "",
        "answer": (answer or "")[:4000],
        "ts": datetime.now().isoformat(),
    }
    path = _turn_path(session_id)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    buf = _buffers.setdefault(session_id, [])
    buf.append(row)
    return turn


def load_turns(session_id: str, turn_start: int, turn_end: int) -> list[dict]:
    """Parent-child expand: load original turns in [start, end] inclusive."""
    path = _turn_path(session_id)
    if not path.exists():
        return []
    out = []
    try:
        for line in path.open(encoding="utf-8"):
            if not line.strip():
                continue
            row = json.loads(line)
            t = int(row.get("turn") or 0)
            if turn_start <= t <= turn_end:
                out.append(row)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return out
    return out


def expand_episode_hit(hit: dict) -> str:
    """If recall hit is an episode, append child turn excerpts (bounded)."""
    meta = hit.get("metadata") or {}
    if (meta.get("memory_type") or hit.get("memory_type")) != "episode":
        return hit.get("text") or ""
    session_id = meta.get("session_id") or "default"
    try:
        start = int(meta.get("turn_start") or 0)
        end = int(meta.get("turn_end") or 0)
    except (TypeError, ValueError):
        return hit.get("text") or ""
    if start <= 0 or end < start:
        return hit.get("text") or ""
    children = load_turns(session_id, start, end)
    if not children:
        return hit.get("text") or ""
    lines = [hit.get("text") or "", "[情节展开]"]
    for c in children[-6:]:
        q = (c.get("query") or "")[:120]
        a = (c.get("answer") or "")[:160]
        lines.append(f"T{c.get('turn')}: 问:{q} → 答:{a}")
    return "\n".join(lines)


async def _summarize_episode(client, turns: list[dict]) -> str:
    from harness.memory.short_term_memory import compress_history

    msgs = []
    for t in turns:
        msgs.append({"role": "user", "content": t.get("query") or ""})
        msgs.append({"role": "assistant", "content": t.get("answer") or ""})
    if client is None:
        # offline fallback
        bits = []
        for t in turns:
            bits.append(f"问:{t.get('query','')[:80]} 答:{(t.get('answer') or '')[:80]}")
        return "情节摘要: " + " | ".join(bits)[:500]
    return await compress_history(client, msgs)


async def flush_episode(session_id: str, vector_memory, client=None, *, force: bool = False) -> str | None:
    """If buffer has >= N turns (or force), write one episode vector and clear those turns."""
    buf = _buffers.get(session_id) or []
    if not buf:
        return None
    n = EPISODE_EVERY_N
    if not force and len(buf) < n:
        return None
    chunk = list(buf) if force else buf[:n]
    summary = await _summarize_episode(client, chunk)
    turn_start = int(chunk[0]["turn"])
    turn_end = int(chunk[-1]["turn"])
    mid = None
    try:
        mid = vector_memory.remember(
            content=summary,
            memory_type="episode",
            user_id="default",
            metadata={
                "session_id": session_id,
                "turn_start": turn_start,
                "turn_end": turn_end,
                "year": str(datetime.now().year),
                "kind": "episode",
            },
        )
        print(
            f"   📦 episode flush session={session_id} "
            f"turns={turn_start}-{turn_end} id={mid}"
        )
    except Exception as e:
        logger.warning("episode flush failed: %s", e)
        return None
    _buffers[session_id] = buf[len(chunk):]
    return mid


async def record_and_maybe_flush(
    session_id: str,
    query: str,
    answer: str,
    vector_memory,
    client=None,
) -> int:
    """Append turn; flush episode every N turns; periodic compact. Returns turn index."""
    turn = append_turn(session_id, query, answer)
    await flush_episode(session_id, vector_memory, client, force=False)
    # 每 4 个 episode 窗口做一次压实（flush 残余 + 轻量 prune）
    if turn > 0 and turn % max(EPISODE_EVERY_N * 4, 1) == 0:
        try:
            await compact_session(session_id, vector_memory, client)
        except Exception as e:
            logger.warning("periodic compact failed: %s", e)
    return turn


async def compact_session(
    session_id: str,
    vector_memory,
    client=None,
    *,
    keep_recent_turns: int = KEEP_RECENT_TURNS,
) -> dict:
    """Flush remaining buffer; prune legacy per-turn conversation vectors for this session.

    Episode vectors are kept. Returns stats.
    """
    flushed = await flush_episode(session_id, vector_memory, client, force=True)
    pruned = 0
    # Prune old conversation-type memories that look like per-turn Q&A (legacy)
    # Prefer index-based listing when available.
    try:
        from harness.memory.recent_index import list_recent_ids

        col = getattr(getattr(vector_memory, "backend", None), "collection_name", None) or "conversations"
        # Scan a bounded window of recent ids
        # keep_recent_turns reserved for future turn-log prune; vectors prune by type/session
        _ = keep_recent_turns
        ids = list_recent_ids(col, user_id="default", limit=200, memory_type="conversation")
        for mid in ids:
            # Only delete legacy "问:/答:" conversation rows without episode kind
            try:
                rows: list[dict] = []
                get = getattr(vector_memory.backend, "get_by_ids", None)
                if callable(get):
                    rows = cast(list[dict], get([mid]) or [])
                else:
                    continue
                if not rows:
                    continue
                meta = rows[0].get("metadata") or {}
                if meta.get("memory_type") == "episode" or meta.get("kind") == "episode":
                    continue
                text = rows[0].get("text") or ""
                if not text.startswith("问:"):
                    continue
                # session-scoped prune if metadata has session_id; else skip foreign
                sid = meta.get("session_id")
                if sid and sid != session_id:
                    continue
                vector_memory.forget(mid)
                pruned += 1
            except Exception:
                continue
    except Exception as e:
        logger.warning("compact prune skipped: %s", e)
    return {"flushed": bool(flushed), "pruned": pruned, "session_id": session_id}
