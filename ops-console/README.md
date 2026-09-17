# ops-console/

The **LLMOps monitoring** surface — the "operate" half of the platform. It shows,
for every LLM call across every onboarded usecase, the traces, cost, tokens,
latency, guardrail decisions, evaluation-gate history, and feedback.

```
apps emit StepEvent/PipelineEvent + feedback (JSONL sinks on a shared /data volume)
        │
        ▼
ops-console/backend  (FastAPI) ── tails JSONL → SQLite mirror → aggregation endpoints
        │
        ▼
ops-console/web      (React) ── Overview · Traces · Cost · Guardrails · Evals · Feedback · Prompts
```

## Folders

| Folder | What it is |
|---|---|
| [`backend/`](backend/) | FastAPI service. Ingests the shared JSONL sinks into SQLite and serves `/api/*` aggregations. See its README. |
| [`web/`](web/) | React (Vite + TS) console that reads the backend. See its README. |

## Why JSONL → SQLite

The sink strategy works **both locally and in Azure with no extra services**: each
app writes events to a JSONL file on a shared volume (`LLMOPS_TRACER=jsonl`), and
the backend mirrors them into SQLite for fast queries. Set `LLMOPS_TRACER=azure`
to *also* emit to Application Insights; JSONL stays the console's source of truth
so it works offline.

## Run
```bash
cd ops-console/backend && uvicorn app:app --port 8100     # API
cd ops-console/web && npm install && npm run dev           # console :5174
```
Or via `docker compose up` from the repo root (console at http://localhost:8080).
