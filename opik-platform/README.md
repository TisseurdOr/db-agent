# Local Opik (AI Observability)

UI: http://localhost:5173

## Start / stop

```bash
cd opik-platform
docker compose --profile opik up -d
docker compose --profile opik down
```

## Wire into db-agent

In `.env`:

```
OPIK_ENABLED=1
OPIK_URL_OVERRIDE=http://localhost:5173/api
OPIK_PROJECT_NAME=db-agent
```

Then run CLI / API as usual. With `OPIK_ENABLED=1`, `wrap_anthropic_client` auto-wraps
the Anthropic SDK via `track_anthropic` (LLM spans + token usage). LangGraph traces and
entrypoint spans are uploaded via `utils/opik_tracing.py`; local JSONL traces in
`logs/traces/` are still written.

Optional playground (dedicated entrypoint process — module-level `ask(query)`,
no CLI loop; keep it running while you use Agent Playground):

```bash
source .venv/bin/activate
opik endpoint --project "db-agent" -- python scripts/opik_playground.py
```


## Harness metadata keys

With `OPIK_ENABLED=1`, `utils/opik_tracing.annotate_opik` writes these fields onto
the current Opik trace/span (best-effort; no-ops when disabled or no active span):

| Key | Source |
|-----|--------|
| `route_source` | router: `rule` / `cache` / `llm` / `fallback_sql` |
| `plan_agents` | router agent chain, e.g. `sql→analysis` |
| `cache_hit_rate` | router cache path |
| `fewshot_hits` | SQL few-shot retrieval count |
| `masked_sql` | successful SQL (via `utils.tracer.mask_sql`) |
| `task_board` | compact `[{agent, status, id}, …]` |
| `reflection_pass` / `reflection_attempts` / `reflection_issues` / `reflection_suggestion` | reflection node |
| `blocked_by` / `block_reason` | input/output guardrails |
| `hitl_event` / `hitl_type` / `confidence` / `approved` | HITL pause/resume |
| `memory_token_budget` | ConversationManager estimate (multi mode) |
| `model` / `input_tokens` / `output_tokens` / `currency` / `input_cost` / `output_cost` / `subtotal` / `cost_cny_equivalent` | turn cost |
| `local_trace_id` | local JSONL TraceContext id |
| `recalled_memories_present` / `conversation_summary_present` | turn start |

Tags commonly attached: `router`, `guard`, `blocked`, `hitl`, `pause`, `resume`,
`fewshot_hit` / `fewshot_miss`, `reflection_pass` / `reflection_retry`,
`task_board`, `memory`, `cost`, `sql`, `turn_complete`.


## User feedback scoring

Frontend thumbs (👍/👎) and `POST /api/feedback` write Opik **Feedback scores**
on the matching trace via `utils.opik_tracing.log_user_feedback`:

| Rating | Scores logged | Value |
|--------|---------------|-------|
| up     | `user_feedback`, `overall_quality` | 1.0 |
| down   | `user_feedback`, `overall_quality` | 0.0 |

The SSE `done` event includes both `trace_id` (local short id) and
`opik_trace_id` (Opik UUID). The feedback API prefers `opik_trace_id`, and
falls back to resolving `metadata.local_trace_id` when only the local id is
available.

### Setup (definitions + annotation queue)

```bash
.venv/bin/python scripts/opik_setup_feedback.py
```

This creates numerical definitions `user_feedback` / `overall_quality` (0–1)
and the traces annotation queue `db-agent-review`.

### How to score in the UI (Annotate)

1. Open http://localhost:5173 → project `db-agent` → **Traces**
2. Open a trace → click **Annotate**
3. Set `user_feedback` / `overall_quality` and optionally a reason

Docs: https://www.comet.com/docs/opik/tracing/advanced/annotate_traces

### Online evaluation (LLM-as-Judge)

Online rules are configured in the UI (often UI-only):

1. Opik → project → **Online evaluation** / **Rules**
2. Create an LLM-as-Judge rule + sampling rate

Docs: https://www.comet.com/docs/opik/production/online-evaluation/rules

### Annotation queues

Created by `scripts/opik_setup_feedback.py` (`db-agent-review`). Use the
queue UI for dedicated human review workflows.

Docs: https://www.comet.com/docs/opik/evaluation/advanced/annotation_queues

### Manual evaluation

On the **Traces** page: select one or more traces → **Evaluate** → apply
existing rules (bypasses sampling / disabled state).


## Offline eval → Datasets / Experiments

Sync `tests/eval_cases.py` into Opik Dataset `db-agent-eval-cases` and upload
a run as an Experiment (pass_rate + per-item `passed` scores):

```bash
# sync + upload experiment after eval
.venv/bin/python -m tests.eval_runner --fast --opik
.venv/bin/python -m tests.eval_runner --full --judge --opik
```

Requires `OPIK_ENABLED=1` (or `--opik` will setdefault it). Also works with
`OPIK_EVAL=1` without the flag.

View in Opik UI: **Datasets** → `db-agent-eval-cases`; **Experiments** → latest `eval-*`
