# application/api

FastAPI REST API for the dashboard. Wraps the existing `backend/` **in-process**
(no reimplementation of Azure SQL / Blob / scoring / coaching) and is the sole
first-party client's contract — the React SPA in [`../web`](../web).

## Auth model
The **API is the OAuth confidential client**. Login (Entra SSO redirect or a
password fallback) resolves a canonical principal, stored in a **signed HttpOnly
session cookie** (stateless → works across replicas). RBAC scoping is always
derived from this server-trusted principal, never from client-supplied ids. The
chatbot bearer JWT is minted here.

## Files
| File | Role |
|---|---|
| `main.py` | App factory, CORS (credentials), routers, `/health`. |
| `security.py` | `Principal`, session-cookie mint/verify, Entra MSAL flow, role mapping. |
| `rbac.py` | Server-side scoping: coach → own employees, manager → their coaches, superuser → all / view-as. |
| `services.py` | Lazy, API-safe wrappers over `backend/*` (bypasses Streamlit caching, passes explicit week/prefix). |
| `routes_auth.py` | `/api/auth/{login,login/password,callback,session,logout,chat-token}`. |
| `routes_data.py` | `/api/{programs,managers,filters/*,individual/*,manager/overview,analytics,employees/*/notes}` — every route `Depends(get_principal)` + scope guard. |

## Run
```bash
pip install -r ../requirements.txt -r requirements.txt
uvicorn api.main:app --port 8000        # from usecases/apix/application
```
