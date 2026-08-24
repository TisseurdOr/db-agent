#!/usr/bin/env python3
"""One-shot Opik feedback setup for db-agent.

Creates feedback score definitions + an annotation queue, then prints UI steps.

Usage:
    .venv/bin/python scripts/opik_setup_feedback.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")


def main() -> int:
    if os.getenv("OPIK_ENABLED", "0").strip().lower() not in {"1", "true", "yes", "on"}:
        print("OPIK_ENABLED is not set. Add OPIK_ENABLED=1 to .env and retry.")
        return 1

    from harness.observation.opik_tracing import (
        configure_opik,
        ensure_annotation_queue,
        ensure_feedback_definitions,
        flush_opik,
        log_user_feedback,
    )

    if not configure_opik():
        print("configure_opik() failed — check OPIK_URL_OVERRIDE / network.")
        return 1

    project = os.getenv("OPIK_PROJECT_NAME", "db-agent")
    ui = os.getenv("OPIK_URL_OVERRIDE", "http://localhost:5173/api").replace("/api", "")
    print(f"Opik project={project}  UI={ui}")

    print("\n[1/3] Ensuring feedback definitions (user_feedback, overall_quality)…")
    ensure_feedback_definitions()

    print("[2/3] Ensuring annotation queue db-agent-review…")
    qid = ensure_annotation_queue("db-agent-review")
    print(f"      queue_id={qid}")

    print("[3/3] Optional smoke: score latest trace if any…")
    try:
        from opik import Opik

        client = Opik()
        traces = client.search_traces(project_name=project, max_results=1)
        if traces:
            tid = traces[0].id
            result = log_user_feedback(tid, "up", comment="setup smoke test")
            print(f"      scored {tid}: {result}")
        else:
            print("      no traces yet — skip scoring smoke (run an ask first)")
    except Exception as exc:
        print(f"      smoke skipped: {exc}")

    flush_opik()

    print(
        f"""
═══════════════════════════════════════════════════════════════
UI next steps (Opik → project "{project}")
═══════════════════════════════════════════════════════════════

1. Annotate (manual scores)
   • Open Traces → pick a trace → Annotate
   • Score user_feedback / overall_quality (0–1) and add a reason
   Docs: https://www.comet.com/docs/opik/tracing/advanced/annotate_traces

2. Online evaluation (LLM-as-Judge)
   • Project → Online evaluation / Rules → create a rule
   • Pick sampling rate + LLM judge metric
   Docs: https://www.comet.com/docs/opik/production/online-evaluation/rules
   (Rules are UI-configured; SDK setup cannot fully create them.)

3. Annotation queues
   • Queue "db-agent-review" was created by this script (id={qid})
   • Add traces from Traces page or use the queue UI for team review
   Docs: https://www.comet.com/docs/opik/evaluation/advanced/annotation_queues

4. Manual Evaluate
   • Traces → select traces → Evaluate → apply existing rules
   Docs: same annotate_traces page (Manual evaluation section)

Frontend thumbs (👍/👎) call POST /api/feedback which maps to
Opik scores via utils.opik_tracing.log_user_feedback.
UI: {ui}
"""
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
