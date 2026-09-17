// Read-only client for the ops-console backend. Base is same-origin (nginx
// proxies /ops) or VITE_OPS_API_URL.
const BASE = (import.meta.env.VITE_OPS_API_URL as string) || "/ops";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json() as Promise<T>;
}
async function post<T>(path: string, body: any): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json() as Promise<T>;
}

export const ops = {
  health: () => get<any>("/healthz"),
  summary: (app?: string) => get<any>(`/api/monitoring/summary${app ? `?app=${app}` : ""}`),
  runs: () => get<any[]>("/api/runs"),
  run: (id: string) => get<any>(`/api/runs/${encodeURIComponent(id)}`),
  traces: (app?: string) => get<any[]>(`/api/traces${app ? `?app=${app}` : ""}`),
  cost: (groupby: string) => get<any[]>(`/api/cost/summary?groupby=${groupby}`),
  latency: (groupby: string) => get<any[]>(`/api/latency?groupby=${groupby}`),
  guardrails: () => get<any>("/api/guardrails"),
  evalRuns: () => get<any[]>("/api/eval-runs"),
  feedback: () => get<any[]>("/api/feedback"),
  prompts: () => get<any[]>("/api/prompts"),
  promote: () => post<any>("/api/feedback/promote", {}),
};
