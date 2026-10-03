"""Opik Datasets / Experiments 接入 — 把 eval_cases 同步到 Dataset，把跑分结果上传到 Experiment。

用法（经 tests.eval_runner）:
    python -m tests.eval_runner --fast --opik
    python -m tests.eval_runner --full --judge --opik

环境变量同 utils.opik_tracing（OPIK_ENABLED / OPIK_URL_OVERRIDE / OPIK_PROJECT_NAME）。
关闭或配置失败时返回 {ok: False, error: ...}，不抛异常。
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import Any, cast

logger = logging.getLogger(__name__)

DATASET_NAME = "db-agent-eval-cases"

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(s: str) -> str:
    return _ANSI.sub("", str(s) if s is not None else "")


def _project() -> str:
    return os.getenv("OPIK_PROJECT_NAME", "db-agent")


def _ui_base() -> str:
    """Derive UI origin from OPIK_URL_OVERRIDE (…/api → …)."""
    url = os.getenv("OPIK_URL_OVERRIDE", "http://localhost:5173/api").rstrip("/")
    if url.endswith("/api"):
        return url[:-4]
    return url


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def sync_eval_dataset(cases) -> dict[str, str]:
    """Upsert ALL_CASES into Opik dataset. Return {case_id: dataset_item_id}.

    Inserts only missing case_ids (no duplicates). Fields:
    case_id, input(=query), category, description, assertions.
    """
    from harness.observation.opik_tracing import configure_opik, opik_enabled

    if not opik_enabled() or not configure_opik():
        raise RuntimeError("opik_disabled")

    from opik import Opik

    client = Opik()
    project = _project()
    ds = client.get_or_create_dataset(name=DATASET_NAME, project_name=project)

    existing: dict[str, str] = {}
    try:
        items = ds.get_items() or []
    except Exception as exc:
        logger.warning("dataset get_items failed: %s", exc)
        items = []

    for item in items:
        if not isinstance(item, dict):
            continue
        cid = item.get("case_id")
        iid = item.get("id")
        if cid and iid:
            existing[str(cid)] = str(iid)

    to_insert: list[dict[str, Any]] = []
    for case in cases:
        cid = getattr(case, "id", None)
        if cid is None and isinstance(case, dict):
            cid = case.get("id") or case.get("case_id")
        if not cid or str(cid) in existing:
            continue
        query = getattr(case, "query", None)
        if query is None and isinstance(case, dict):
            query = case.get("query") or case.get("input")
        category = getattr(case, "category", None)
        if category is None and isinstance(case, dict):
            category = case.get("category")
        description = getattr(case, "description", None)
        if description is None and isinstance(case, dict):
            description = case.get("description")
        assertions = getattr(case, "assertions", None)
        if assertions is None and isinstance(case, dict):
            assertions = case.get("assertions")
        to_insert.append(
            {
                "case_id": str(cid),
                "input": query if query is not None else "",
                "category": category or "",
                "description": description or "",
                "assertions": assertions if assertions is not None else {},
            }
        )

    if to_insert:
        ds.insert(to_insert)
        try:
            items = ds.get_items() or []
        except Exception as exc:
            logger.warning("dataset get_items (post-insert) failed: %s", exc)
            items = []
        existing = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            cid = item.get("case_id")
            iid = item.get("id")
            if cid and iid:
                existing[str(cid)] = str(iid)

    return existing


def upload_eval_experiment(
    *,
    results,  # list of objects with .case, .passed, .details, .elapsed, .tokens
    answers: dict[str, str] | None = None,
    judge_scores: dict[str, dict] | None = None,
    mode: str = "fast",
    model: str = "",
    experiment_name: str | None = None,
) -> dict:
    """Create experiment, batch_upload_items for each result, log pass_rate.

    Returns {ok, experiment_id, experiment_name, url?, item_count, pass_rate}
    or {ok: False, error: ...} when Opik is disabled / configure fails.
    """
    from harness.observation.opik_tracing import configure_opik, flush_opik, opik_enabled

    if not opik_enabled():
        return {"ok": False, "error": "opik_disabled"}
    try:
        if not configure_opik():
            return {"ok": False, "error": "opik_configure_failed"}
    except Exception as exc:
        return {"ok": False, "error": f"opik_configure_failed: {exc}"}

    answers = answers or {}
    judge_scores = judge_scores or {}

    try:
        from opik import Opik
        from opik.api_objects.experiment.bulk_item import ExperimentItemBulkRecord
        from opik.evaluation.metrics import score_result

        try:
            from tests.eval_cases import ALL_CASES

            id_map = sync_eval_dataset(ALL_CASES)
        except Exception as exc:
            logger.warning("sync_eval_dataset during upload failed: %s", exc)
            cases = [r.case for r in results]
            id_map = sync_eval_dataset(cases)

        pass_count = sum(1 for r in results if r.passed)
        case_count = len(results)
        pass_rate = (pass_count / case_count) if case_count else 0.0
        judge_enabled = bool(judge_scores)

        if not experiment_name:
            experiment_name = f"eval-{mode}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

        tags = ["eval", mode]
        if model:
            tags.append(model)

        project = _project()
        client = Opik()
        exp = client.create_experiment(
            dataset_name=DATASET_NAME,
            name=experiment_name,
            experiment_config={
                "mode": mode,
                "model": model,
                "case_count": case_count,
                "pass_count": pass_count,
                "judge_enabled": judge_enabled,
            },
            project_name=project,
            tags=tags,
        )

        bulk: list[Any] = []
        elapsed_vals: list[float] = []
        acc_vals: list[float] = []
        comp_vals: list[float] = []
        conc_vals: list[float] = []

        for result in results:
            case = result.case
            cid = case.id
            dataset_item_id = id_map.get(cid)
            if not dataset_item_id:
                logger.warning("No dataset_item_id for case %s — skip", cid)
                continue

            fail_only = [d for d in (result.details or []) if d and "\u2713" not in d]
            # Also strip ANSI and drop lines that are ok-markers after strip
            fail_msgs = []
            for d in fail_only:
                plain = strip_ansi(d).strip()
                if not plain or plain.lstrip().startswith("\u2713"):
                    continue
                fail_msgs.append(plain)
            reason = (
                "ok"
                if result.passed
                else ("; ".join(fail_msgs) or "failed")
            )

            feedback: list[dict[str, Any]] = [
                {
                    "name": "passed",
                    "value": 1.0 if result.passed else 0.0,
                    "reason": reason[:2000],
                }
            ]

            js = judge_scores.get(cid)
            if js:
                for dim in ("accuracy", "completeness", "conciseness"):
                    raw = js.get(dim)
                    if raw is None:
                        continue
                    try:
                        val = _clamp01(float(raw) / 5.0)
                    except (TypeError, ValueError):
                        continue
                    feedback.append(
                        {
                            "name": dim,
                            "value": val,
                            "reason": str(js.get("comment", ""))[:2000],
                        }
                    )
                    if dim == "accuracy":
                        acc_vals.append(val)
                    elif dim == "completeness":
                        comp_vals.append(val)
                    else:
                        conc_vals.append(val)

                verdict = str(js.get("verdict", "")).lower()
                feedback.append(
                    {
                        "name": "judge_verdict",
                        "value": 1.0 if verdict == "pass" else 0.0,
                        "reason": str(js.get("comment", ""))[:2000],
                    }
                )
                total = js.get("total")
                if total is not None:
                    try:
                        feedback.append(
                            {
                                "name": "judge_total",
                                "value": _clamp01(float(total) / 15.0),
                                "reason": f"total={total}/15",
                            }
                        )
                    except (TypeError, ValueError):
                        pass

            task_result = {
                "output": (answers.get(cid, "") or "")[:4000] if answers else "",
                "passed": bool(result.passed),
                "details": [strip_ansi(d) for d in (result.details or [])],
                "elapsed": float(result.elapsed or 0.0),
                "tokens": int(result.tokens or 0),
                "case_id": cid,
                "category": getattr(case, "category", "") or "",
            }
            elapsed_vals.append(float(result.elapsed or 0.0))

            bulk.append(
                ExperimentItemBulkRecord(
                    dataset_item_id=dataset_item_id,
                    evaluate_task_result=task_result,
                    feedback_scores=cast(Any, feedback),
                )
            )

        if bulk:
            exp.batch_upload_items(bulk, project_name=project)

        score_list = [
            score_result.ScoreResult(
                name="pass_rate",
                value=pass_rate,
                reason=f"{pass_count}/{case_count} passed",
            )
        ]
        if elapsed_vals:
            avg_elapsed = sum(elapsed_vals) / len(elapsed_vals)
            score_list.append(
                score_result.ScoreResult(
                    name="avg_elapsed",
                    value=avg_elapsed,
                    reason=f"mean over {len(elapsed_vals)} items (seconds)",
                )
            )
        if acc_vals:
            score_list.append(
                score_result.ScoreResult(
                    name="avg_accuracy",
                    value=sum(acc_vals) / len(acc_vals),
                    reason=f"mean over {len(acc_vals)} judged items",
                )
            )
        if comp_vals:
            score_list.append(
                score_result.ScoreResult(
                    name="avg_completeness",
                    value=sum(comp_vals) / len(comp_vals),
                    reason=f"mean over {len(comp_vals)} judged items",
                )
            )
        if conc_vals:
            score_list.append(
                score_result.ScoreResult(
                    name="avg_conciseness",
                    value=sum(conc_vals) / len(conc_vals),
                    reason=f"mean over {len(conc_vals)} judged items",
                )
            )

        try:
            exp.log_experiment_scores(score_list)
        except Exception as exc:
            logger.warning("log_experiment_scores failed: %s", exc)

        flush_opik()

        exp_id = getattr(exp, "id", None) or ""
        ui = _ui_base()
        url = f"{ui}/" if ui else None

        return {
            "ok": True,
            "experiment_id": exp_id,
            "experiment_name": experiment_name,
            "url": url,
            "item_count": len(bulk),
            "pass_rate": pass_rate,
            "pass_count": pass_count,
            "case_count": case_count,
            "dataset": DATASET_NAME,
        }
    except Exception as exc:
        logger.warning("upload_eval_experiment failed: %s", exc)
        return {"ok": False, "error": str(exc)}
