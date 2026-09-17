import { useEffect, useState } from "react";
import { useFilters } from "../App";
import { api } from "../api";
import { Bars, BarDatum } from "../components/charts";
import { NotesThread } from "../components/NotesThread";

export default function Metrics() {
  const f = useFilters();
  const [metrics, setMetrics] = useState<any>(null);
  const [coaching, setCoaching] = useState<any>(null);
  const [state, setState] = useState<"idle" | "loading" | "error">("idle");
  const [coachBusy, setCoachBusy] = useState(false);

  useEffect(() => {
    if (!f.program || !f.week || !f.employeeId) { setMetrics(null); return; }
    setState("loading"); setCoaching(null);
    api.metrics(f.employeeId, f.program, f.week, f.viewAs || undefined)
      .then((m) => { setMetrics(m); setState("idle"); })
      .catch(() => setState("error"));
  }, [f.program, f.week, f.employeeId, f.viewAs]);

  if (!f.employeeId) return <div className="card muted">Select an employee.</div>;
  if (state === "loading") return <div className="spinner">Loading metrics…</div>;
  if (state === "error") return <div className="card error">Failed to load metrics.</div>;
  if (!metrics) return null;

  const avg: Record<string, number> = metrics.trend?.avg || {};
  const teamAvg: Record<string, number> = metrics.team?.avg || {};
  const prev: Record<string, number> = metrics.trend?.avg_prev || metrics.trend?.avg_prev1 || {};
  const groups: any[] = metrics.groups?.length ? metrics.groups : [{ name: "Metrics", metrics: Object.keys(avg) }];

  const genCoaching = async () => {
    setCoachBusy(true);
    try { setCoaching(await api.coaching(f.employeeId, f.program, f.week, avg, prev, f.viewAs || undefined)); }
    catch { setCoaching({ available: false }); }
    finally { setCoachBusy(false); }
  };

  return (
    <>
      <div className="grid cols-2">
        {groups.map((g, gi) => {
          const keys: string[] = Array.isArray(g.metrics) ? g.metrics.map(metricKey) : [];
          const data: BarDatum[] = keys
            .filter((k) => k in avg)
            .map((k) => ({ label: humanize(k), value: num(avg[k]), compare: k in teamAvg ? num(teamAvg[k]) : undefined }));
          if (data.length === 0) return null;
          return (
            <div className="card" key={gi}>
              <h3>{g.name || `Group ${gi + 1}`}</h3>
              <Bars data={data} />
            </div>
          );
        })}
      </div>

      <div className="card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h3 style={{ margin: 0 }}>AI Coaching</h3>
          <button className="btn sm" onClick={genCoaching} disabled={coachBusy}>
            {coachBusy ? "Generating…" : "Generate coaching"}
          </button>
        </div>
        {coaching && coaching.available === false && <div className="muted" style={{ marginTop: 10 }}>Coaching AI is not available (check REASONING_MODEL_* config).</div>}
        {coaching && coaching.available !== false && (
          <div style={{ marginTop: 12 }}>
            {coaching.summary && <p>{coaching.summary}</p>}
            {Array.isArray(coaching.tips) && coaching.tips.length > 0 && (
              <ul>{coaching.tips.map((t: any, i: number) => (
                <li key={i}>{typeof t === "string" ? t : (t.tip || t.text || JSON.stringify(t))}</li>
              ))}</ul>
            )}
          </div>
        )}
      </div>

      <NotesThread empId={f.employeeId} program={f.program} week={f.week} namespace="metrics" />
    </>
  );
}

const num = (v: any) => (Number.isFinite(Number(v)) ? Number(v) : 0);
const metricKey = (m: any) => (typeof m === "string" ? m : m.key || m.column_key || m.name || String(m));
function humanize(k: string): string {
  return k.replace(/^(bs_|ch_|wkpi_|wbs_|cmp_|np_|pkpi_)/, "").replace(/_/g, " ")
          .replace(/\b\w/g, (c) => c.toUpperCase());
}
