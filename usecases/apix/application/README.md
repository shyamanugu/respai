# application

The APIX stakeholder **dashboard**. React-everywhere: a React SPA over a new
FastAPI REST API that wraps the existing in-process backend. Deploys as a
Container **App**.

```
web/ (React SPA) ──/api──▶ api/ (FastAPI) ──in-process──▶ backend/ (Azure SQL, Blob, scoring, coaching)
                                   │ mints JWT
     ChatWidget ──bearer JWT──▶ chatbot service (separate app)
```

## Folders

| Path | What it is |
|---|---|
| [`api/`](api/) | **NEW** FastAPI REST API: SSO + password auth (HttpOnly session cookie), server-side RBAC scoping, data endpoints wrapping `backend/`, chatbot-JWT minting. See its README. |
| [`web/`](web/) | **NEW** React (Vite + TS) SPA — 5 pages 1:1 with the old Streamlit views + a chat widget. See its README. |
| `backend/` | Existing in-process service layer (auth, config, db notes, Azure SQL/Blob, scoring, `coaching_ai`). Unchanged except the `llmops/` adapter. |
| `backend/llmops/` | **Platform adapter** — wraps `coaching_ai.generate_coaching_insights()` (usecase `apix`) with guardrails + tracing + cost, fail-open. |
| `frontend/` | **Legacy** Streamlit UI, kept during the React transition; retire after cutover. |

## Run
```bash
# API (needs both requirement sets):
pip install -r requirements.txt -r api/requirements.txt
uvicorn api.main:app --port 8000
# SPA:
cd web && npm install && npm run dev        # http://localhost:5173
```
The `application` Docker image defaults to the API (`uvicorn api.main:app`); the
legacy Streamlit UI is a documented command override. See [`api/README.md`](api/README.md)
for the endpoint list and auth model.
