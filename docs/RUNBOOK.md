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
> Vault, manual `az` CLI, ghcr.io images). **Nothing in either doc requires
> Docker on your own machine** — the local run below has a no-Docker path, and
> Azure image builds happen on GitHub's own cloud runners (see `DEPLOYMENT.md`
> Part A), never on yours.

## Prerequisites
- Python 3.11 (apps target 3.11; a newer interpreter usually works but isn't
  what was tested against).
- Node.js ≥ 20 + npm (for the two React apps).
- **No Docker needed.** Docker Desktop is only useful if you happen to have it —
  see §2 vs §3 below. If you don't have it (or can't get it, e.g. on a locked-down
  VDI), skip straight to §3; every command there is plain `python`/`pip`/`npm`.
- **Terminal:** every command in this doc and in `DEPLOYMENT.md` is written for a
  **POSIX shell — Git Bash** (bundled with Git for Windows) **or WSL**. They use
  `$VAR` expansion, `$(...)` command substitution, `for ... do ... done` loops,
  and `\` line continuation, none of which work as-written in PowerShell or
  `cmd.exe`. If Git Bash isn't available on your VDI, tell me and I'll convert
  the command blocks to PowerShell — don't hand-translate them ad hoc, a couple
  of the substitutions aren't a 1:1 syntax swap.
- Access to the real Azure resources: Azure OpenAI, Blob Storage, SQL (for a full
  run — the platform's guardrails/tracing/cost pieces work with zero Azure).

## 1. Configure

There are **two separate `.env` concerns** — don't skip either. This step is the
same whether or not you use Docker.

1. **Root `.env`** (repo root) — the template you fill in once:
   ```bash
   cp .env.example .env
   # edit .env with your real values
   ```
2. **Per-app `.env`** — each app loads its **own** `.env` from its **own folder**
   when run directly (not via Docker): `usecases/apix/ai_pipeline/.env`,
   `usecases/apix/application/.env`, `usecases/apix/chatbot/.env`. Simplest
   correct setup: copy the filled-in root `.env` into all three (extra unused
   variables per app are harmless):
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

## 2. Run locally — without Docker (your path: no Docker required)

Each app is a **separate Python environment** (their dependency sets can
conflict) — use a venv per app. The platform (`platform/`) needs no install: each
app's `llmops/` adapter finds `platform/bootstrap.py` automatically by walking up
from its own folder to the repo root, so nothing extra to configure locally.

Run each block in its **own terminal tab** (they're long-running servers, except
`ai_pipeline` which exits when done):

```bash
# --- ai_pipeline (batch) ---
cd usecases/apix/ai_pipeline
python -m venv .venv && . .venv/Scripts/activate    # Git Bash on Windows; .venv/bin/activate on macOS/Linux
pip install -r requirements.txt -e .                 # -e . registers the `ai_pipeline` package
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
it from the repo root.

**Note:** the two dashboard apps (chatbot + dashboard API) both default to port
`8000` above — if you run them at the same time, start one on a different port,
e.g. `uvicorn main:app --port 8001 --reload` for the chatbot, and update
`CHAT_API_BASE_URL` in `usecases/apix/application/.env` to match.

## 3. Run locally — Docker (optional, only if you happen to have Docker Desktop)

Skip this section entirely if you don't have Docker — §2 above is the complete,
equivalent path.

```bash
docker compose up --build                 # dashboard-web :5173, ops-web :8080,
                                          # dashboard-api :8000, chatbot :8001, ops-backend :8100
# run a pipeline batch (writes Blob JSON + emits traces to ./data):
docker compose --profile jobs run --rm pipeline --mode telesales --date 2025-08-28
# refresh the chatbot's SQLite cache from the new week:
docker compose --profile jobs run --rm chatbot-ingest
```

Either way (§2 or §3), open the **dashboard** (http://localhost:5173) and the
**ops console** — the console shows live cost/tokens/latency/guardrail data from
every LLM call across the three apps.

## 4. Deploy to Azure

**You have Contributor-only access, no Key Vault** → follow
[`DEPLOYMENT.md`](DEPLOYMENT.md) (manual `az` CLI, Container App secrets, images
via ghcr.io — **built on GitHub's cloud runners, not your machine**, one
Container Apps Environment per tier under a single resource group).
`infra/main.bicep` + `infra/deploy.sh` are an **alternate, automated** path
(managed identity + role assignments + ACR) for whoever has Owner/User Access
Administrator on the subscription — not the path for you today.

## 5. Onboarding another usecase
Copy `usecases/apix/` as a template: give it a usecase id, add config blocks to
`platform/services/{03,04,06,07}/config/*.yaml` (see `usecases/apix/config/`),
and wire an `llmops/` adapter at your app's LLM choke point. No platform `src`
changes — that's the reusability contract.
