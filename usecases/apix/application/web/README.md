# application/web

The dashboard **React SPA** (Vite + React 18 + TypeScript). Replaces the legacy
Streamlit UI. Minimal dependencies (React + React Router) with **custom inline-SVG
charts** — no heavy charting library.

## Structure
| Path | Role |
|---|---|
| `src/api.ts` | Typed fetch client (`credentials: "include"` → session cookie). |
| `src/auth.tsx` | Auth context (session hydrate / logout). |
| `src/App.tsx` | Router, `AuthGuard`, shared filters (program/week/coach/employee/view-as), layout + sidebar. |
| `src/pages/` | `Login`, `IndividualReport`, `Metrics`, `ManagerOverview`, `Analytics` (1:1 with the old Streamlit views). |
| `src/components/` | `charts.tsx` (Gauge/Bars/Line SVG), `ChatWidget.tsx`, `NotesThread.tsx`. |

## Run / build
```bash
npm install
npm run dev          # http://localhost:5173 (proxies /api to :8000)
npm run build        # tsc + vite → dist/
```
Docker: multi-stage build → nginx, which reverse-proxies `/api` to the dashboard
API so the session cookie stays same-origin. Build-time env: `VITE_DASHBOARD_API_URL`
(default same-origin), `VITE_CHAT_API_URL` (browser-reachable chatbot).
