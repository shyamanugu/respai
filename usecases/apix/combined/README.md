# combined/ — all 3 APIX apps in one container

Deployment artifacts (no app code of its own) that package `ai_pipeline`,
`application`, and `chatbot` into **one Docker image, one Container App, one
exposed port**, with nginx routing to each by path:

| Path (external) | Routes to | Internal port |
|---|---|---|
| `/` | Dashboard React SPA (static files, built in) | — |
| `/api/*` | Dashboard FastAPI | 8001 |
| `/chatbot/*` | Chatbot FastAPI | 8002 |
| `/pipeline/*` | Pipeline trigger API ([`../ai_pipeline/api/`](../ai_pipeline/api/)) — bearer-token protected | 8003 |

## Why 3 separate Python venvs, not one shared environment

Tried it first. **The four apps' frozen dependency sets have real,
unresolvable conflicts** — confirmed with an actual `pip install` of all four
`requirements.txt` files together: `aiohappyeyeballs==2.6.1` (from
`ai_pipeline`) vs `==2.6.2` (from `application`), and pip's resolver stops
enumerating after the first conflict, so there are almost certainly more.
Sharing one environment would mean silently picking a version for one app that
it was never tested against.

So each app gets its **own venv** (`/opt/venv-dashboard`, `/opt/venv-chatbot`,
`/opt/venv-pipeline`), built in one Docker image. `supervisord` runs all three
uvicorn processes plus nginx as one PID 1 — if any of the four crashes,
supervisord restarts it. Nothing about any app's Python code changed; only how
they're packaged. Each app's own startup lifecycle (chatbot's LLM/SQL/SQLite
warmup, dashboard's SQL pool prewarm) runs exactly as it does standalone,
because each is still a genuine, independent uvicorn process.

## Why same-origin is a real improvement, not just a packaging choice

Because the SPA, the dashboard API, and the chatbot are now all behind one
nginx on one origin: no CORS configuration is needed, and the dashboard's
session cookie is naturally first-party (no cross-origin cookie edge cases).
`CHAT_API_BASE_URL` becomes the **relative** path `/chatbot` instead of an
absolute cross-origin URL — the React `ChatWidget` code didn't need to change,
since it already just concatenates whatever base URL it's given.

## Build & run

```bash
# from the repo root — build context matters, it needs platform/ too:
docker build -f usecases/apix/combined/Dockerfile -t apix-combined .
docker run -p 8080:8080 --env-file .env apix-combined
```

See [`../../../docs/DEPLOYMENT.md`](../../../docs/DEPLOYMENT.md) and
[`infra/azure-setup.ps1`](../../../infra/azure-setup.ps1) for the Azure
Container Apps deployment (this is `ca-llmops-apix-<tier>` there).

## Triggering the pipeline

```bash
curl -X POST https://<host>/pipeline/run \
  -H "Authorization: Bearer $PIPELINE_TRIGGER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mode": "telesales", "date": "2025-08-28"}'
curl https://<host>/pipeline/status/<run_id> -H "Authorization: Bearer $PIPELINE_TRIGGER_TOKEN"
```
Or, for the standalone/no-Docker CLI path (still the normal way to run a real
batch), see [`../ai_pipeline/README.md`](../ai_pipeline/README.md).
