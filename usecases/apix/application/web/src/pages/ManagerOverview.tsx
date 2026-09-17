import { useEffect, useState } from "react";
import { useFilters } from "../App";
import { api } from "../api";

export default function ManagerOverview() {
  const f = useFilters();
  const [data, setData] = useState<any>(null);
  const [state, setState] = useState<"idle" | "loading" | "error">("idle");

  useEffect(() => {
    if (!f.program || !f.week) { setData(null); return; }
    setState("loading");
    api.managerOverview(f.program, f.week, f.coachId || undefined, f.viewAs || undefined)
      .then((d) => { setData(d); setState("idle"); })
      .catch(() => setState("error"));
  }, [f.program, f.week, f.coachId, f.viewAs]);

  if (state === "loading") return <div className="spinner">Loading team overview…</div>;
  if (state === "error") return <div className="card error">Failed to load overview.</div>;
  if (!data) return null;

  const s = data.summary || {};
  const roster: any[] = data.roster || [];

  return (
    <>
      <div className="grid cols-3">
        <div className="card kpi"><span className="label">Team Size</span><span className="value">{s.team_size ?? 0}</span></div>
        <div className="card kpi"><span className="label">Avg Performance</span><span className="value">{s.avg_performance ?? 0}</span></div>
        <div className="card kpi"><span className="label">Need Attention</span><span className="value">{(s.needs_attention || []).length}</span></div>
      </div>

      <div className="card">
        <h3>Team Roster</h3>
        <div style={{ overflowX: "auto" }}>
          <table>
            <thead><tr><th>Employee</th><th>Coach</th><th>Performance</th><th>Risks</th></tr></thead>
            <tbody>
              {roster.map((r) => (
                <tr key={r.employee_id}>
                  <td>{r.employee_name}</td>
                  <td className="muted">{r.coach_name}</td>
                  <td>
                    <span className={`pill ${r.score >= 75 ? "good" : r.score >= 50 ? "warn" : "bad"}`}>{r.score}</span>
                  </td>
                  <td>{r.risk_count > 0 ? <span className="pill warn">{r.risk_count}</span> : <span className="muted">—</span>}</td>
                </tr>
              ))}
              {roster.length === 0 && <tr><td colSpan={4} className="muted">No employees in scope for this selection.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
