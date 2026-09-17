# AFNI LLMOps Platform

> A reusable, Azure-first **LLMOps platform** — prompt, model, evaluation,
> guardrails, observability, orchestration, serving, and feedback as importable
> libraries — proven by onboarding a real product, **APIX (AFNI Performance
> Index)**, end to end **without editing a single line of platform source.**

The thesis: LLM products shouldn't each re-implement tracing, cost accounting,
model routing, guardrails, prompt versioning, and eval gates. They should
**inherit** them from a shared platform and add only their domain logic. This
repo is that platform **plus** its first consumer, so the reusability claim is
demonstrated, not asserted.

```
                         ┌───────────────────────────────────────────────┐
                         │                 LLMOps PLATFORM                │
                         │  prompt · model · eval · guardrails ·          │
                         │  observability · orchestration · serving ·     │
                         │  feedback     (platform/services/**)           │
                         └───────────────▲───────────────────────────────┘
                                         │  imported via PYTHONPATH,
              thin fail-open adapters    │  configured by YAML — ZERO src edits
        ┌────────────────┬──────────────┴─────────────┬───────────────────┐
        │                │                             │                   │
   ai_pipeline       application                    chatbot          ops-console
   (batch job)   (FastAPI API + React SPA)      (FastAPI NL→SQL)   (monitoring UI)
        └──────────── APIX usecase (usecases/apix) ───────┘        reads the traces
                                                                    every app emits
```

## How reusability works

A usecase consumes the platform three ways — **all config or thin adapter, never
a platform-source edit** (this is the acceptance test):

1. **Import by bare name** after `platform/bootstrap.py` wires the `src` dirs onto
   `sys.path` — e.g. `from model_management.model_router import resolve`.
2. **Configure by YAML** — register the usecase in the platform's config files
   (`models`, `guardrails`, `gates`, `clients`). APIX's blocks live in
   [`usecases/apix/config/`](usecases/apix/config/).
3. **Wrap one choke point** — a small `llmops/` adapter around the app's single
   LLM call adds model-alias resolution, guardrails, tracing + cost, and
   feedback, all **fail-open** (absent platform ⇒ the app runs unchanged).

Onboard a second usecase by copying [`usecases/apix/`](usecases/) as a template.

## The platform (`platform/`)

Nine real, tested libraries (see [`platform/README.md`](platform/README.md)):

| Service | Package | Entry point |
|---|---|---|
| prompt-management | `prompt_management` | `PromptRegistry.resolve/.render` |
| model-management | `model_management` | `resolve(alias, env) → ModelHandle` |
| evaluation-gate | `evaluation_gate` | `EvaluationGate.run(...)` |
| observability | `observability` | `Tracer`, `compute_cost(...)` |
| guardrails | `guardrails` | `build_guardrail(usecase, env)` |
| data-tools | `data_tools` | `resolve_client_index`, `RetrievalTool` |
| orchestration | `orchestration` | `Pipeline`, `ModelStep` |
| serving-hosting | `serving` | `create_app(registry)` |
| feedback | `feedback` | `FeedbackStore`, `promote_to_golden_dataset` |

## Repository map

| Folder | What it is |
|---|---|
| [`platform/`](platform/) | The reusable LLMOps libraries + `bootstrap.py`. **Never edited by a usecase.** |
| [`usecases/`](usecases/) | Onboarded products. Today: [`apix/`](usecases/apix/) — three apps sharing one data contract. |
| [`ops-console/`](ops-console/) | LLMOps monitoring: a FastAPI backend over the trace/cost/eval/feedback sinks + a React console. |
| [`infra/`](infra/) | Azure Container Apps deployment — bicep + `deploy.sh`. |
| [`docs/`](docs/) | Cross-cutting docs — start with [`RUNBOOK.md`](docs/RUNBOOK.md). |
| `.env.example` | One consolidated config file (tagged REQUIRED / OPTIONAL / MUST-MATCH). |
| `docker-compose.yml` | Local all-up: every service against one `.env`. |

## Quick start

```bash
cp .env.example .env            # fill in Azure creds
docker compose up --build       # dashboard :5173 · ops console :8080
docker compose --profile jobs run --rm pipeline --mode telesales --date 2025-08-28
```

Then open the ops console — it shows live cost, tokens, latency, guardrail
decisions, and evals for **every** LLM call across all three APIX apps.

Deploy to Azure: `./infra/deploy.sh dev <resource-group> <acr-name>`. Full
walkthrough in [`docs/RUNBOOK.md`](docs/RUNBOOK.md).
