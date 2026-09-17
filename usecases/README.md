# usecases/

Products **onboarded onto the LLMOps platform**. Each usecase lives in its own
folder, brings only its domain logic, and consumes the platform by import +
config + a thin `llmops/` adapter — **never by editing `platform/services/**/src`**
(the reusability acceptance test).

## Onboarded

| Usecase | Folder | What it is |
|---|---|---|
| **APIX** (AFNI Performance Index) | [`apix/`](apix/) | Three apps: `ai_pipeline` (batch), `application` (dashboard: FastAPI API + React SPA), `chatbot` (NL→SQL analytics). |

## Onboarding a new usecase (golden path)

1. Copy `apix/` as a starting shape (or start from a single app).
2. Give it a **usecase id** and add config blocks for it to the platform config
   files — see [`apix/config/`](apix/config/) for the exact snippets that go into
   `platform/services/{03,04,06,07}/config/*.yaml`.
3. Add an `llmops/` adapter package next to your app code and wrap your app's
   single LLM call with it (model-alias resolution → guardrails → tracing+cost →
   feedback), all fail-open. Reuse APIX's adapters as the reference pattern.
4. Point the app at `platform/bootstrap.py` (or set `LLMOPS_PLATFORM_ROOT`) so the
   platform packages import by bare name.

If you finish without touching anything under `platform/services/**/src`, the
platform did its job.
