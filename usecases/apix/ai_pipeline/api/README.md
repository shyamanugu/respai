# ai_pipeline/api

Thin FastAPI wrapper exposing the batch pipeline as an HTTP endpoint (`POST
/run`, `GET /status/{run_id}`, `GET /runs`, `GET /health`) instead of only a
CLI. Calls the exact same `run_pipeline()` coroutine `python -m
ai_pipeline.main` uses — no duplicated logic.

Exists for the [combined single-container deployment](../combined/) where the
pipeline needs "an endpoint" alongside the dashboard and chatbot. `python -m
ai_pipeline.main` directly is still the normal way to run it standalone (see
`../README.md`).

**Auth:** every mutating route requires `Authorization: Bearer
<PIPELINE_TRIGGER_TOKEN>` — fail-closed (rejects everything if that env var
isn't set), since this can be reachable on a public ingress and can trigger
real LLM spend.

Run: `uvicorn api.main:app --port 8003` (from `usecases/apix/ai_pipeline/`, in
the pipeline's own venv).
