# ops-console/backend

FastAPI service that turns the platform's JSONL trace/feedback sinks into fast
monitoring aggregations for the ops console.

## How it works
Each app appends `StepEvent`/`PipelineEvent` rows to `LLMOPS_TRACE_FILE` and
feedback to `APIX_FEEDBACK_PATH` (a shared `/data` volume). `store.py` tails those
files into a SQLite mirror (idempotent, byte-offset tracked); `app.py` serves
aggregations. No direct database dependency on the read path — cost is already
computed into each row.

## Files
| File | Role |
|---|---|
| `store.py` | SQLite schema + JSONL→SQLite ingest (`steps`, `pipelines`, `feedback`, `eval_runs`). |
| `app.py` | FastAPI endpoints (below). |

## Endpoints
`GET /healthz` · `/api/monitoring/summary` · `/api/runs[/{id}]` · `/api/traces` ·
`/api/cost/summary?groupby=app|deployment|day` · `/api/latency` · `/api/guardrails` ·
`/api/eval-runs` · `/api/feedback` (GET+POST) · `POST /api/feedback/promote` ·
`GET /api/prompts`.

## Run
```bash
pip install -r requirements.txt
uvicorn app:app --port 8100
```
