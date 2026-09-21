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

> **This is the doc to follow end-to-end.** Order: (1) local run below, to prove
> your `.env` values are correct before touching Azure; (2) once local works,
> move to [`DEPLOYMENT.md`](DEPLOYMENT.md) for Azure (Contributor-only, no Key
> Vault, manual `az` CLI, ghcr.io images).

## Prerequisites
- Python 3.11 (apps target 3.11; a newer interpreter usually works but isn't
  what was tested against).
- Node.js ≥ 20 + npm (for the two React apps).
- Docker Desktop, **if** you want the one-command `docker compose` path.
- Access to the real Azure resources: Azure OpenAI, Blob Storage, SQL (for a full
  run — the platform's guardrails/tracing/cost pieces work with zero Azure).

## 1. Configure

There are **two separate `.env` concerns** — don't skip either:

1. **Root `.env`** (repo root) — consumed by `docker compose`:
   ```bash
   cp .env.example .env
   ```
2. **Per-app `.env`** — each app loads its **own** `.env` from its **own folder**
   when run directly (not via Docker): `usecases/apix/ai_pipeline/.env`,
   `usecases/apix/application/.env`, `usecases/apix/chatbot/.env`. The simplest
   correct setup: fill in the root `.env` once, then copy it into all three
   (extra unused variables per app are harmless):
   ```bash
   for d in ai_pipeline application chatbot; do
     cp .env "usecases/apix/$d/.env"
   done
   ```
   `ops-console/backend` reads plain environment variables (no `.env` file) —
   every variable it uses has a safe local default, so it runs with none set.

Values tagged `[MUST MATCH]` in `.env.example` must be identical across apps
(`REASONING_MODEL_*`, `AZURE_BLOB_*`, `AZURE_SQL_*`, `CHAT_JWT_SECRET`) — since
each app now has its own copy, if you edit one later, edit all three.

## 2. Run locally — Docker (fastest path)
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

## 3. Run locally — without Docker (per app, in separate terminals)

Each app is a **separate Python environment** (their dependency sets can
conflict) — use a venv per app. The platform (`platform/`) needs no install: each
app's `llmops/` adapter finds `platform/bootstrap.py` automatically by walking up
from its own folder to the repo root, so nothing extra to configure locally.

```bash
# --- ai_pipeline (batch) ---
cd usecases/apix/ai_pipeline
python -m venv .venv && . .venv/Scripts/activate    # or .venv/bin/activate on macOS/Linux
pip install -r requirements.txt -e .                # -e . registers the `ai_pipeline` package
python -m ai_pipeline.main --mode telesales --date 2025-08-28

# --- chatbot (FastAPI, run from inside chatbot/) ---
cd usecases/apix/chatbot
python -m venv .venv && . .venv/Scripts/activate
pip install -r requirements.txt
uvicorn main:app --port 8000 --reload

# --- dashboard API (FastAPI, run from application/ — needs BOTH requirement files) ---
cd usecases/apix/application
python -m venv .venv && . .venv/Scripts/activate
pip install -r requirements.txt -r api/requirements.txt
uvicorn api.main:app --port 8000 --reload

# --- dashboard SPA ---
cd usecases/apix/application/web
npm install && npm run dev                          # http://localhost:5173

# --- ops console backend + web ---
cd ops-console/backend
python -m venv .venv && . .venv/Scripts/activate
pip install -r requirements.txt
uvicorn app:app --port 8100 --reload
cd ../web
npm install && npm run dev                           # http://localhost:5174
```

Quick platform sanity check (no venv needed, uses your system Python + pyyaml):
```bash
python platform/bootstrap.py
```
Should print `wired 9 service src dir(s):` — if it prints 0, you're not running
from the repo root.

## 4. Deploy to Azure

**You have Contributor-only access, no Key Vault** → follow
[`DEPLOYMENT.md`](DEPLOYMENT.md) (manual `az` CLI, Container App secrets, images
via ghcr.io, one Container Apps Environment per tier under a single resource
group). `infra/main.bicep` + `infra/deploy.sh` are an **alternate, automated**
path (managed identity + role assignments + ACR) for whoever has Owner/User
Access Administrator on the subscription — not the path for you today.

## 5. Onboarding another usecase
Copy `usecases/apix/` as a template: give it a usecase id, add config blocks to
`platform/services/{03,04,06,07}/config/*.yaml` (see `usecases/apix/config/`),
and wire an `llmops/` adapter at your app's LLM choke point. No platform `src`
changes — that's the reusability contract.
