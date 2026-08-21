"""Opik AI Observability 接入层。

默认关闭（OPIK_ENABLED=0）。打开后接入三类集成：
  1. LangGraph（wrap_langgraph）——执行树上报
  2. Anthropic SDK（wrap_anthropic_client / track_anthropic）——LLM 调用 + token usage
  3. Entrypoint（track_entrypoint）——业务入口 span（如 MultiAgentRunner.run）

另：annotate_opik / opik_tag_* 把 harness 信号（router / guard / HITL /
few-shot / reflection / task board / memory / cost / masked SQL）写到当前
Opik span + trace 的 metadata / tags。无当前 span/trace 或 OPIK 关闭时静默 no-op。

本地 TraceContext JSONL 仍双写保留。

环境变量：
    OPIK_ENABLED=1
    OPIK_URL_OVERRIDE=http://localhost:5173/api   # 本地自建
    OPIK_PROJECT_NAME=db-agent
    OPIK_API_KEY=...                              # 仅 Comet Cloud 需要
    OPIK_WORKSPACE=...                            # 仅 Comet Cloud 需要
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def opik_enabled() -> bool:
    return os.getenv("OPIK_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}


def configure_opik() -> bool:
    """按环境变量配置 SDK。成功返回 True；关闭或失败返回 False。"""
    if not opik_enabled():
        return False

    try:
        import opik
    except ImportError:
        logger.warning("OPIK_ENABLED=1 但未安装 opik，跳过上报。请执行: uv add opik")
        return False

    url = os.getenv("OPIK_URL_OVERRIDE", "http://localhost:5173/api").rstrip("/")
    project = os.getenv("OPIK_PROJECT_NAME", "db-agent")
    api_key = os.getenv("OPIK_API_KEY", "")
    workspace = os.getenv("OPIK_WORKSPACE", "")

    # 本地自建通常不需要 API key；Cloud 需要
    use_local = "localhost" in url or "127.0.0.1" in url
    os.environ["OPIK_URL_OVERRIDE"] = url
    os.environ["OPIK_PROJECT_NAME"] = project
    try:
        kwargs: dict[str, Any] = {
            "use_local": use_local,
            "force": True,
            "url_override": url,
            "project_name": project,
            "automatic_approvals": True,
        }
        if not use_local:
            if api_key:
                kwargs["api_key"] = api_key
            if workspace:
                kwargs["workspace"] = workspace
        opik.configure(**kwargs)
    except TypeError:
        # 旧版 SDK 签名可能不同，退化为纯环境变量（已写入上面）
        pass
    except Exception as exc:
        # Config file may be unwritable; env vars are enough for Opik() client.
        logger.warning("Opik configure soft-fail (continuing with env): %s", exc)

    logger.info("Opik 已启用 → %s (project=%s)", url, project)
    return True


def wrap_langgraph(graph: Any) -> Any:
    """用 track_langgraph 包装已编译图；未启用时原样返回。"""
    if not configure_opik():
        return graph

    try:
        from opik.integrations.langchain import OpikTracer, track_langgraph
    except ImportError as exc:
        logger.warning("无法导入 Opik LangGraph 集成: %s", exc)
        return graph

    project = os.getenv("OPIK_PROJECT_NAME", "db-agent")
    try:
        tracer = OpikTracer(project_name=project)
        wrapped = track_langgraph(graph, tracer)
        # Keep tracer on the graph so callers can read created_traces() after ainvoke
        try:
            wrapped._opik_tracer = tracer
        except Exception:
            pass
        logger.info("LangGraph 已接入 OpikTracer (project=%s)", project)
        return wrapped
    except Exception as exc:
        logger.warning("track_langgraph 失败，继续使用本地 TraceContext: %s", exc)
        return graph


def wrap_anthropic_client(client: Any) -> Any:
    """用 track_anthropic 包装 Anthropic client；失败时回退原 client。"""
    if not configure_opik():
        return client

    try:
        from opik.integrations.anthropic import track_anthropic

        project = os.getenv("OPIK_PROJECT_NAME", "db-agent")
        wrapped = track_anthropic(client, project_name=project)
        logger.info("Anthropic client 已接入 Opik track_anthropic (project=%s)", project)
        return wrapped
    except Exception as exc:
        logger.warning("track_anthropic 失败，继续使用原 client: %s", exc)
        return client


def track_entrypoint(fn: Any) -> Any:
    """装饰器：OPIK 启用时标记为 entrypoint；否则原样返回。"""
    if not opik_enabled():
        return fn

    try:
        import opik

        project = os.getenv("OPIK_PROJECT_NAME", "db-agent")
        return opik.track(entrypoint=True, project_name=project, name=fn.__name__)(fn)
    except Exception as exc:
        logger.warning("opik.track entrypoint 失败，跳过: %s", exc)
        return fn


def flush_opik() -> None:
    """进程退出前尽量把缓冲里的 span 刷出去。"""
    if not opik_enabled():
        return
    try:
        import opik

        opik.flush()
    except Exception:
        pass


def get_current_opik_trace_id() -> str | None:
    """Return the active Opik trace UUID, or None if disabled / no context."""
    if not opik_enabled():
        return None
    try:
        from opik import opik_context

        data = opik_context.get_current_trace_data()
        if data is None:
            return None
        return getattr(data, "id", None) or None
    except Exception:
        return None


def _looks_like_opik_uuid(trace_ref: str) -> bool:
    """Heuristic: Opik ids are UUIDs (contain '-' and are longer than short local ids)."""
    return "-" in trace_ref and len(trace_ref) > 30


def resolve_opik_trace_id(trace_ref: str) -> str | None:
    """Resolve an Opik UUID or local TraceContext id to an Opik trace id.

    Local ids (e.g. ``20260810-3ea1``) are matched via ``metadata.local_trace_id``.
    """
    if not trace_ref or not str(trace_ref).strip():
        return None
    if not opik_enabled():
        return None

    trace_ref = str(trace_ref).strip()
    if _looks_like_opik_uuid(trace_ref):
        return trace_ref

    try:
        if not configure_opik():
            return None
        from opik import Opik

        project = os.getenv("OPIK_PROJECT_NAME", "db-agent")
        client = Opik()

        # OQL supports nested metadata via dot notation: metadata.local_trace_id = "..."
        try:
            filtered = client.search_traces(
                project_name=project,
                filter_string=f'metadata.local_trace_id = "{trace_ref}"',
                max_results=5,
            )
            if filtered:
                tid = getattr(filtered[0], "id", None)
                if tid:
                    return tid
        except Exception:
            pass

        # Fallback: scan recent traces in Python
        recent = client.search_traces(project_name=project, max_results=50)
        for tr in recent or []:
            meta = getattr(tr, "metadata", None) or {}
            if isinstance(meta, dict) and meta.get("local_trace_id") == trace_ref:
                tid = getattr(tr, "id", None)
                if tid:
                    return tid
    except Exception as exc:
        logger.debug("resolve_opik_trace_id failed for %s: %s", trace_ref, exc)
    return None


def log_user_feedback(
    trace_ref: str,
    rating: str,
    comment: str = "",
    project_name: str | None = None,
) -> dict:
    """Log thumbs-up/down as Opik feedback scores on a trace.

    rating ``up`` → 1.0, ``down`` → 0.0. Writes both ``user_feedback`` and
    ``overall_quality``. Optional ``comment`` becomes the score reason.
    """
    if not opik_enabled():
        return {"ok": False, "error": "opik_disabled"}

    value = 1.0 if str(rating).strip().lower() == "up" else 0.0
    project = project_name or os.getenv("OPIK_PROJECT_NAME", "db-agent")

    try:
        if not configure_opik():
            return {"ok": False, "error": "opik_disabled"}

        oid = resolve_opik_trace_id(trace_ref)
        if not oid:
            return {"ok": False, "error": "trace_not_found"}

        from opik import Opik

        client = Opik()
        scores: list[dict[str, Any]] = [
            {
                "id": oid,
                "name": "user_feedback",
                "value": value,
                "project_name": project,
            },
            {
                "id": oid,
                "name": "overall_quality",
                "value": value,
                "project_name": project,
            },
        ]
        if comment:
            scores[0]["reason"] = comment
            scores[1]["reason"] = comment

        client.log_traces_feedback_scores(scores=scores)
        flush_opik()
        return {"ok": True, "opik_trace_id": oid, "scores": scores}
    except Exception as exc:
        logger.warning("log_user_feedback failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def ensure_feedback_definitions() -> None:
    """Best-effort create numerical feedback definitions (0–1) for the project."""
    if not opik_enabled():
        logger.warning("ensure_feedback_definitions: OPIK disabled, skip")
        return
    try:
        if not configure_opik():
            return
        from opik import Opik
        from opik.rest_api.types.feedback_create import FeedbackCreate_Numerical
        from opik.rest_api.types.numerical_feedback_detail_create import (
            NumericalFeedbackDetailCreate,
        )

        client = Opik()
        fd = client.rest_client.feedback_definitions
        existing_names: set[str] = set()
        try:
            page = fd.find_feedback_definitions(size=100)
            for item in getattr(page, "content", None) or []:
                name = getattr(item, "name", None)
                if name:
                    existing_names.add(name)
        except Exception as exc:
            logger.debug("find_feedback_definitions: %s", exc)

        details = NumericalFeedbackDetailCreate(min=0.0, max=1.0)
        for name, desc in (
            ("user_feedback", "User thumbs up/down (1.0 / 0.0)"),
            ("overall_quality", "Overall quality score (0–1)"),
        ):
            if name in existing_names:
                continue
            try:
                fd.create_feedback_definition(
                    request=FeedbackCreate_Numerical(
                        name=name,
                        description=desc,
                        details=details,
                    )
                )
                logger.info("Created Opik feedback definition: %s", name)
            except Exception as exc:
                logger.warning("create_feedback_definition(%s) failed: %s", name, exc)
    except Exception as exc:
        logger.warning("ensure_feedback_definitions unavailable: %s", exc)


def ensure_annotation_queue(name: str = "db-agent-review") -> str | None:
    """Create (or reuse) a traces annotation queue. Returns queue id or None."""
    if not opik_enabled():
        logger.warning("ensure_annotation_queue: OPIK disabled, skip")
        return None
    try:
        if not configure_opik():
            return None
        from opik import Opik

        project = os.getenv("OPIK_PROJECT_NAME", "db-agent")
        client = Opik()

        # Reuse existing queue with the same name if present
        try:
            for q in client.get_traces_annotation_queues(project_name=project) or []:
                if getattr(q, "name", None) == name:
                    qid = getattr(q, "id", None)
                    logger.info("Annotation queue already exists: %s (%s)", name, qid)
                    return qid
        except Exception:
            pass

        queue = client.create_traces_annotation_queue(
            name=name,
            project_name=project,
            description="Human review queue for db-agent traces",
            instructions="Score user_feedback / overall_quality (0–1) and add a reason.",
            comments_enabled=True,
            feedback_definition_names=["user_feedback", "overall_quality"],
        )
        qid = getattr(queue, "id", None)
        logger.info("Created annotation queue: %s (%s)", name, qid)
        return qid
    except Exception as exc:
        logger.warning("ensure_annotation_queue failed: %s", exc)
        return None


def capture_opik_trace_id_for_graph(graph: Any = None) -> str | None:
    """Best-effort Opik UUID: current context, else last OpikTracer-created trace."""
    oid = get_current_opik_trace_id()
    if oid:
        return oid
    if graph is None:
        return None
    try:
        tracer = getattr(graph, "_opik_tracer", None)
        if tracer is None:
            return None
        created = tracer.created_traces()
        if not created:
            return None
        last = created[-1]
        return getattr(last, "id", None) or None
    except Exception:
        return None


# ── Harness metadata annotations ─────────────────────────────────────────────


def annotate_opik(
    *,
    metadata: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    span_metadata: dict[str, Any] | None = None,
) -> None:
    """Best-effort: write harness fields onto current Opik span + trace."""
    if not opik_enabled():
        return
    try:
        from opik import opik_context
    except ImportError:
        return

    try:
        trace_kwargs: dict[str, Any] = {}
        if metadata:
            trace_kwargs["metadata"] = metadata
        if tags:
            trace_kwargs["tags"] = tags
        if trace_kwargs:
            opik_context.update_current_trace(**trace_kwargs)

        span_meta = span_metadata if span_metadata is not None else metadata
        span_kwargs: dict[str, Any] = {}
        if span_meta:
            span_kwargs["metadata"] = span_meta
        if tags:
            span_kwargs["tags"] = tags
        if span_kwargs:
            try:
                opik_context.update_current_span(**span_kwargs)
            except Exception:
                pass
    except Exception:
        # Never break the agent for observability
        pass


def opik_tag_route(source: str, **extra: Any) -> None:
    """Router path: rule | cache | llm | fallback_sql."""
    annotate_opik(
        metadata={"route_source": source, **extra},
        tags=["router", source],
    )


def opik_tag_guard(layer: str, reason: str) -> None:
    """Guardrail block: layer=input|output."""
    annotate_opik(
        metadata={"blocked_by": layer, "block_reason": reason},
        tags=["guard", "blocked"],
    )


def opik_tag_hitl(event: str, **extra: Any) -> None:
    """HITL pause|resume."""
    annotate_opik(
        metadata={"hitl_event": event, **extra},
        tags=["hitl", event],
    )


def opik_tag_fewshot(hits: int) -> None:
    annotate_opik(
        metadata={"fewshot_hits": int(hits)},
        tags=["fewshot_hit" if hits > 0 else "fewshot_miss"],
    )


def opik_tag_reflection(
    passed: bool,
    attempts: int,
    issues: list,
    suggestion: str = "",
) -> None:
    annotate_opik(
        metadata={
            "reflection_pass": bool(passed),
            "reflection_attempts": int(attempts),
            "reflection_issues": list(issues or []),
            "reflection_suggestion": suggestion or "",
        },
        tags=["reflection_pass" if passed else "reflection_retry"],
    )


def opik_tag_task_board(tasks: list[dict]) -> None:
    annotate_opik(
        metadata={"task_board": tasks},
        tags=["task_board"],
    )


def opik_tag_memory(est: dict) -> None:
    annotate_opik(
        metadata={"memory_token_budget": est},
        tags=["memory"],
    )


def opik_tag_cost(cost: dict) -> None:
    annotate_opik(metadata=dict(cost), tags=["cost"])


def opik_tag_sql(masked_sql: str) -> None:
    annotate_opik(
        metadata={"masked_sql": masked_sql},
        tags=["sql"],
    )
