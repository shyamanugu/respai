# APIX + LLMOps — Runbook

## What's here
A reusable LLMOps **platform** (`platform/`) and its first onboarded consumer,
**APIX** (`usecases/apix/`), plus an **ops console** (`ops-console/`). APIX = three
apps sharing one Azure data contract:

| App | Role | Deploy target |
|---|---|---|
| `ai_pipeline` | batch: transcripts → weekly per-employee JSON + KPIs | Container Apps **Job** (weekly) |
| `application` (`api` + `web`) | dashboard: FastAPI + React SPA | Container **Apps** |
| `chatbot` | NL→SQL→NL analytics assistant (FastAPI) | Container **App** + ingestion **Job** |
| `ops-console` (`backend` + `web`) | LLMOps monitoring: traces/cost/guardrails/evals/feedback | Container **Apps** |

Every app is wired to the platform at its single LLM choke point via a local
`llmops/` adapter (model-alias resolution, guardrails, tracing + cost, feedback) —
**fail-open**, and with **zero edits to `platform/services/**/src`**.

## 1. Configure
```bash
cp .env.example .env    # fill in Azure OpenAI, Blob, SQL, Entra, CHAT_JWT_SECRET…
```
Values tagged `[MUST MATCH]` must be identical across apps (`REASONING_MODEL_*`,
`AZURE_BLOB_*`, `AZURE_SQL_*`, `CHAT_JWT_SECRET`).

## 2. Run locally (Docker)
```bash
docker compose up --build                 # dashboard-web :5173, ops-web :8080,
                                          # dashboard-api :8000, chatbot :8001, ops-backend :8100
# run a pipeline batch (writes Blob JSON + emits traces to ./data):
docker compose --profile jobs run --rm pipeline --mode telesales --date 2025-08-28
# refresh the chatbot's SQLite cache from the new week:
docker compose --profile jobs run --rm chatbot-ingest
```
Open the **dashboard** (http://localhost:5173) and the **ops console**
(http://localhost:8080) — the console shows live cost/tokens/latency/guardrail
data from every LLM call across the three apps.

## 3. Run a piece without Docker (dev)
```bash
# platform import check
python platform/bootstrap.py
# dashboard API (needs application venv: pip install -r usecases/apix/application/requirements.txt -r .../api/requirements.txt)
cd usecases/apix/application && uvicorn api.main:app --port 8000
# dashboard SPA
cd usecases/apix/application/web && npm install && npm run dev
# ops backend + console
cd ops-console/backend && uvicorn app:app --port 8100
cd ops-console/web && npm install && npm run dev
```

## 4. Deploy to Azure Container Apps
```bash
az login
./infra/deploy.sh dev <resource-group> <acr-name>
```
`deploy.sh` builds every image in ACR (context = repo root, so `platform/` is
included) and applies `infra/main.bicep`: a Container Apps Environment with an
Azure Files share mounted at `/data` (the shared trace/feedback sink), a
user-assigned managed identity, the 5 apps, and the 2 scheduled jobs. Pass
secrets via environment variables (see the script header) or wire Key Vault.
After deploy, grant the managed identity `Storage Blob Data Contributor` + SQL
access, and register the dashboard-api callback URL (`/api/auth/callback`) in
the Entra app registration.

## 5. Onboarding another usecase
Copy `usecases/apix/` as a template: give it a usecase id, add config blocks to
`platform/services/{03,04,06,07}/config/*.yaml` (see `usecases/apix/config/`),
and wire an `llmops/` adapter at your app's LLM choke point. No platform `src`
changes — that's the reusability contract.
```
