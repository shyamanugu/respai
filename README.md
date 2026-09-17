# AFNI LLMOps Platform

A reusable, Azure-first **LLMOps platform** and its first onboarded consumer, **APIX** (AFNI Performance Index). The platform provides prompt/model/eval/guardrail/observability/orchestration/serving/feedback as importable libraries; APIX proves reusability by consuming them through thin, fail-open adapters — **without editing any platform source**.

```
afni-llmops/
├─ platform/            # reusable LLMOps services (9 libs) + bootstrap.py (PYTHONPATH wiring)
├─ usecases/apix/       # APIX usecase: ai_pipeline (batch) · application (dashboard) · chatbot (RAG)
├─ ops-console/         # LLMOps monitoring: FastAPI backend (traces/cost/eval/feedback) + React console
├─ packages/            # shared React design system + typed API client
├─ infra/               # Azure Container Apps bicep + deploy.sh
├─ docs/                # ARCHITECTURE · DATA_CONTRACT · ONBOARDING · RUNBOOK
├─ .env.example         # ONE consolidated config file — copy to .env, fill in, deploy
└─ docker-compose.yml   # local all-up
```

## The platform (`platform/`)

Nine real, tested Python libraries consumed via PYTHONPATH src-layout (import `platform.bootstrap` first, or set `PYTHONPATH`):

| Service | Package | Public entry |
|---|---|---|
| 02 prompt-management | `prompt_management` | `PromptRegistry(...).resolve/.render` |
| 03 model-management | `model_management` | `resolve(alias, env) -> ModelHandle` |
| 04 evaluation-gate | `evaluation_gate` | `EvaluationGate(...).run(...)` |
| 05 observability | `observability` | `Tracer`, `compute_cost(...)` |
| 06 guardrails | `guardrails` | `build_guardrail(usecase, env)` |
| 07 data-tools | `data_tools` | `resolve_client_index`, `RetrievalTool` |
| 08 orchestration | `orchestration` | `Pipeline`, `ModelStep` |
| 10 serving-hosting | `serving` | `create_app(registry)` |
| 11 feedback | `feedback` | `FeedbackStore`, `promote_to_golden_dataset` |

Services 01/12/14 contribute Azure IaC/policy (harvested into `infra/`). **Reusability acceptance test: zero edits to `platform/services/**/src`.**

## APIX usecase (`usecases/apix/`)

Three apps sharing one Azure data contract (pipeline writes weekly per-employee JSON to Blob → dashboard reads it → chatbot ingests it to SQLite):

- **`ai_pipeline/`** — batch CLI (`denoise → analysis → summary → individual_metrics → kpi`). Deploys as a Container Apps **Job**.
- **`application/`** — the stakeholder dashboard: `api/` (FastAPI over the in-process backend) + `web/` (React SPA). Deploys as Container **Apps**.
- **`chatbot/`** — FastAPI NL→SQL→NL analytics assistant. Deploys as a Container **App** + ingestion **Job**.

Each app is wired to the platform at its single LLM choke point via a local `llmops/` adapter (model alias resolution, guardrails, prompt overrides, tracing + cost, feedback), all fail-open.

## Quick start (local)

1. `cp .env.example .env` and fill in the values (see the file for what's REQUIRED vs OPTIONAL and what MUST MATCH across services).
2. `docker compose up` — boots the pipeline (one-shot), chatbot, dashboard api+web, and the ops console against the one `.env`.
3. Open the dashboard (`web`) and the ops console; run a pipeline batch and watch traces/cost appear in the console.

## Deploy (Azure Container Apps)

`cd infra && ./deploy.sh dev` — builds each image in ACR and applies the bicep. Secrets come from Key Vault via a managed identity; env vars from the bicep params sourced from your `.env`. See `docs/RUNBOOK.md`.
