# ops-console/web

The **LLMOps monitoring console** (Vite + React + TypeScript). Read-only; reads
[`../backend`](../backend). Minimal deps + custom inline-SVG charts.

## Tabs
Overview (KPI tiles + calls-by-app) · Traces (recent LLM calls with guardrail
verdict) · Cost & Tokens (by app / deployment) · Guardrails (blocked/flagged
audit) · Evaluations (gate runs) · Feedback (+ promote-to-golden) · Prompts.

## Files
`src/api.ts` (read client), `src/App.tsx` (tabs + panels), `src/charts.tsx`
(HBars/Line SVG).

## Run / build
```bash
npm install
npm run dev          # http://localhost:5174 (proxies /ops to :8100)
npm run build        # tsc + vite → dist/
```
Docker: build → nginx, which proxies `/ops` to the ops backend (same-origin, no
CORS). Build-time env: `VITE_OPS_API_URL` (default same-origin).
