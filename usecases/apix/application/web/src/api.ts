// Typed fetch client for the APIX dashboard API. All calls send the HttpOnly
// session cookie (credentials: "include"). Base URL is same-origin by default
// (dev proxy or nginx reverse-proxy), overridable via VITE_DASHBOARD_API_URL.

const BASE = (import.meta.env.VITE_DASHBOARD_API_URL as string) || "";

async function req<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  if (res.status === 401) {
    throw new ApiError("unauthenticated", 401);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* ignore */ }
    throw new ApiError(detail, res.status);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) { super(message); this.status = status; }
}

export interface Principal {
  authenticated: boolean;
  sub: string; name: string; upn?: string | null;
  role: string; coach_ids: string[]; program?: string | null;
}
export interface Program { id: string; label: string; blob_prefix: string; }
export interface Manager { employee_id: string; name: string; coach_ids: string[]; }
export interface Coach { coach_id: string; coach_name: string; }
export interface Employee { employee_id: string; employee_name: string; coach_id: string; coach_name: string; }
export interface Kpi { key: string; value: number; delta?: number; label?: string; unit?: string; }
export interface Risk { area: string; severity: string; value?: number; delta?: number; reason?: string; }
export interface ReportResponse { report: any; program_id: string; score: number; risks: Risk[]; }
export interface Note { id: string; author: string; author_name?: string; author_role?: string; week?: string; note: string; date?: string; updated_at?: string; }

const q = (params: Record<string, string | undefined>) => {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") s.set(k, v);
  const str = s.toString();
  return str ? `?${str}` : "";
};

export const api = {
  // auth
  session: () => req<Principal>("/api/auth/session"),
  loginPassword: (username: string, password: string) =>
    req<{ authenticated: boolean; user?: Principal; detail?: string }>("/api/auth/login/password", {
      method: "POST", body: JSON.stringify({ username, password }),
    }),
  ssoLoginUrl: () => req<{ auth_url: string }>("/api/auth/login"),
  logout: () => req<{ status: string }>("/api/auth/logout", { method: "POST" }),
  chatToken: () => req<{ token: string; chat_api_base: string; expires_in_minutes: number; user_id: string }>("/api/auth/chat-token"),

  // context
  programs: () => req<Program[]>("/api/programs"),
  managers: () => req<Manager[]>("/api/managers"),
  weeks: (program: string) => req<{ weeks: string[] }>(`/api/filters/weeks${q({ program })}`),
  coaches: (program: string, week: string, viewAs?: string) =>
    req<Coach[]>(`/api/filters/coaches${q({ program, week, view_as: viewAs })}`),
  employees: (program: string, week: string, coachId?: string, viewAs?: string) =>
    req<Employee[]>(`/api/filters/employees${q({ program, week, coach_id: coachId, view_as: viewAs })}`),

  // individual
  report: (empId: string, program: string, week: string, viewAs?: string) =>
    req<ReportResponse>(`/api/individual/${empId}/report${q({ program, week, view_as: viewAs })}`),
  metrics: (empId: string, program: string, week: string, viewAs?: string) =>
    req<any>(`/api/individual/${empId}/metrics${q({ program, week, view_as: viewAs })}`),
  coaching: (empId: string, program: string, week: string, current: any, previous: any, viewAs?: string) =>
    req<any>(`/api/individual/${empId}/coaching${q({ program, week, view_as: viewAs })}`, {
      method: "POST", body: JSON.stringify({ current, previous }),
    }),

  // manager / analytics
  managerOverview: (program: string, week: string, coachId?: string, viewAs?: string) =>
    req<any>(`/api/manager/overview${q({ program, week, coach_id: coachId, view_as: viewAs })}`),
  analytics: (program: string, week: string) =>
    req<any>(`/api/analytics${q({ program, week })}`),

  // notes
  notes: (empId: string, program: string, week: string, namespace = "") =>
    req<Note[]>(`/api/employees/${empId}/notes${q({ program, week, namespace })}`),
  addNote: (empId: string, program: string, note: string, week: string, namespace = "") =>
    req<{ id: string }>(`/api/employees/${empId}/notes${q({ program })}`, {
      method: "POST", body: JSON.stringify({ note, week, namespace }),
    }),
  editNote: (empId: string, noteId: string, note: string, namespace = "") =>
    req<{ updated: boolean }>(`/api/employees/${empId}/notes/${noteId}`, {
      method: "PUT", body: JSON.stringify({ note, namespace }),
    }),
  deleteNote: (empId: string, noteId: string, namespace = "") =>
    req<{ hidden: boolean }>(`/api/employees/${empId}/notes/${noteId}${q({ namespace })}`, {
      method: "DELETE",
    }),
};
