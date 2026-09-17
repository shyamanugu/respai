import { useEffect, useState } from "react";
import { useFilters } from "../App";
import { api, ReportResponse } from "../api";
import { Gauge } from "../components/charts";
import { NotesThread } from "../components/NotesThread";

export default function IndividualReport() {
  const f = useFilters();
  const [data, setData] = useState<ReportResponse | null>(null);
  const [state, setState] = useState<"idle" | "loading" | "error" | "empty">("idle");

  useEffect(() => {
    if (!f.program || !f.week || !f.employeeId) { setData(null); return; }
    setState("loading");
    api.report(f.employeeId, f.program, f.week, f.viewAs || undefined)
      .then((r) => { setData(r); setState("idle"); })
      .catch((e) => setState(e.status === 404 ? "empty" : "error"));
  }, [f.program, f.week, f.employeeId, f.viewAs]);

  if (!f.employeeId) return <div className="card muted">Select an employee to view their report.</div>;
  if (state === "loading") return <div className="spinner">Loading report…</div>;
  if (state === "empty") return <div className="card muted">No report for this employee and week.</div>;
  if (state === "error") return <div className="card error">Failed to load the report.</div>;
  if (!data) return null;

  const kpis: any[] = Array.isArray(data.report?.kpis) ? data.report.kpis : [];
  const emp = data.report?.employee_name || data.report?.EmployeeName || f.employeeId;

  return (
    <>
      <div className="grid cols-2">
        <div className="card" style={{ display: "flex", alignItems: "center", gap: 24 }}>
          <Gauge value={data.score} label="Performance Index" />
          <div>
            <h3 style={{ margin: 0 }}>{emp}</h3>
            <div className="muted">Week of {f.week}</div>
            <div style={{ marginTop: 8 }}>
              <span className={`pill ${data.score >= 75 ? "good" : data.score >= 50 ? "warn" : "bad"}`}>
                {data.score >= 75 ? "Strong" : data.score >= 50 ? "Developing" : "Focus"}
              </span>
            </div>
          </div>
        </div>
        <div className="card">
          <h3>Risk Areas</h3>
          {data.risks.length === 0 && <div className="muted">No elevated risks this week.</div>}
          {data.risks.map((r, i) => (
            <div key={i} style={{ marginBottom: 8 }}>
              <span className={`pill ${r.severity === "critical" ? "bad" : "warn"}`}>{r.severity}</span>{" "}
              <strong>{r.area}</strong> — <span className="muted">{r.reason}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="card">
        <h3>KPIs</h3>
        <div className="grid cols-3">
          {kpis.map((k, i) => {
            const delta = Number(k.delta);
            const cls = !Number.isFinite(delta) || delta === 0 ? "flat" : delta > 0 ? "up" : "down";
            return (
              <div className="kpi" key={i}>
                <span className="label">{k.label || k.key}</span>
                <span className="value">{fmtVal(k.value, k.unit)}</span>
                {Number.isFinite(delta) && delta !== 0 && (
                  <span className={`delta ${cls}`}>{delta > 0 ? "▲" : "▼"} {Math.abs(delta).toFixed(1)}{k.unit === "percent" ? "%" : ""}</span>
                )}
              </div>
            );
          })}
          {kpis.length === 0 && <div className="muted">No KPI values in this report.</div>}
        </div>
      </div>

      {data.report?.reflection && (
        <div className="card">
          <h3>AI Reflection</h3>
          <div style={{ whiteSpace: "pre-wrap", fontSize: 14 }}>{data.report.reflection}</div>
        </div>
      )}

      <NotesThread empId={f.employeeId} program={f.program} week={f.week} namespace="" />
    </>
  );
}

function fmtVal(v: any, unit?: string): string {
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v ?? "—");
  const s = Number.isInteger(n) ? String(n) : n.toFixed(1);
  return unit === "percent" ? `${s}%` : s;
}
