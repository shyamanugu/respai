# APIX — AFNI Performance Index

The first product onboarded to the LLMOps platform. Three apps share **one Azure
data contract**: the pipeline produces weekly per-employee intelligence, and the
dashboard + chatbot consume it.

```
ai_pipeline ──writes──▶ Blob: weekly per-employee JSON  ──read──▶ application (dashboard)
     (batch)                       │                                      │ embeds
                                   └────────ingested────▶ chatbot ◀──chat widget (JWT)
     shared: REASONING_MODEL_* · AZURE_BLOB_* · AZURE_SQL_* · CHAT_JWT_SECRET
```

## Apps

| Folder | Role | LLM choke point wired to the platform |
|---|---|---|
| [`ai_pipeline/`](ai_pipeline/) | Batch: transcripts → denoise → analysis → summary → individual_metrics → KPI. Container Apps **Job**. | `services/__init__.py::query()` |
| [`application/`](application/) | Dashboard: [`api/`](application/api/) (FastAPI) + [`web/`](application/web/) (React SPA) over the in-process [`backend/`](application/backend/). Container **App**. | `backend/services/coaching_ai.py::generate_coaching_insights()` |
| [`chatbot/`](chatbot/) | NL→SQL→NL analytics assistant (FastAPI + LangGraph). Container **App** + ingestion **Job**. | `llm/client.py::generate_completion()` |

## How APIX consumes the platform

- **Usecase ids:** `apix` (batch pipeline + dashboard coaching — PII flagged,
  secrets blocked, prompt-injection off) and `apix_chat` (user-facing chatbot —
  prompt-injection **on**, output length capped).
- **Config, not code:** the platform config blocks APIX adds are mirrored in
  [`config/`](config/) (models/guardrails/gates/clients). No platform `src` edits.
- **Adapters:** each app has a local `llmops/` package that wraps its one LLM call
  with model-alias resolution (`reason`/`bulk`/`judge`), guardrails, tracing +
  cost, and feedback — all **fail-open** (no platform / no creds ⇒ app runs as-is).

## Folders that are pure domain code (unchanged from AFNI's app)

`ai_pipeline/{steps,programs_config,services,utils}`, `application/backend`,
`chatbot/{core,llm,orchestration,sources,ingestion,prompts}`. The `llmops/`
packages are the only platform-integration seams added.

See [`../../docs/RUNBOOK.md`](../../docs/RUNBOOK.md) to run and deploy.
