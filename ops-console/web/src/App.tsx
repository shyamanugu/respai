import { useEffect, useState } from "react";
import { ops } from "./api";
import { HBars, fmt } from "./charts";

const TABS = ["Overview", "Traces", "Cost & Tokens", "Guardrails", "Evaluations", "Feedback", "Prompts"] as const;
type Tab = typeof TABS[number];

export default function App() {
  const [tab, setTab] = useState<Tab>("Overview");
  return (
    <div>
      <div className="topbar">
        <div className="brand">APIX LLMOps<small>Operations Console</small></div>
      </div>
      <div className="tabs">
        {TABS.map((t) => (
          <button key={t} className={`tab ${t === tab ? "active" : ""}`} onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>
      <div className="content">
        {tab === "Overview" && <Overview />}
        {tab === "Traces" && <Traces />}
        {tab === "Cost & Tokens" && <Cost />}
        {tab === "Guardrails" && <Guardrails />}
        {tab === "Evaluations" && <Evaluations />}
        {tab === "Feedback" && <Feedback />}
        {tab === "Prompts" && <Prompts />}
      </div>
    </div>
  );
}

function useAsync<T>(fn: () => Promise<T>, deps: any[] = []): { data: T | null; err: boolean } {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => { let ok = true; setErr(false); fn().then((d) => ok && setData(d)).catch(() => ok && setErr(true)); return () => { ok = false; }; }, deps);
  return { data, err };
}

function Overview() {
  const { data: s } = useAsync(() => ops.summary());
  if (!s) return <div className="spinner">Loading…</div>;
  return (
    <>
      <div className="grid cols-4">
        <Kpi label="LLM Calls" value={s.calls} />
        <Kpi label="Total Cost (USD)" value={`$${fmt(s.total_cost_usd)}`} />
        <Kpi label="Total Tokens" value={s.total_tokens} />
        <Kpi label="p95 Latency (ms)" value={fmt(s.latency_p95_ms)} />
        <Kpi label="Error Rate" value={`${(s.error_rate * 100).toFixed(1)}%`} />
        <Kpi label="Guardrail Block Rate" value={`${(s.guardrail_block_rate * 100).toFixed(1)}%`} />
        <Kpi label="p50 Latency (ms)" value={fmt(s.latency_p50_ms)} />
        <Kpi label="Input / Output Tok" value={`${s.input_tokens} / ${s.output_tokens}`} />
      </div>
      <div className="card">
        <h3>Calls by App</h3>
        <HBars data={(s.by_app || []).map((r: any) => ({ label: r.app || "?", value: r.calls }))} />
      </div>
    </>
  );
}

function Kpi({ label, value }: { label: string; value: any }) {
  return <div className="card kpi"><span className="label">{label}</span><span className="value">{value}</span></div>;
}

function Traces() {
  const { data } = useAsync(() => ops.traces());
  const rows = data || [];
  return (
    <div className="card">
      <h3>Recent LLM Calls ({rows.length})</h3>
      <div style={{ overflowX: "auto" }}>
        <table>
          <thead><tr><th>App</th><th>Usecase</th><th>Step</th><th>Alias</th><th>Deployment</th><th>In</th><th>Out</th><th>Cost</th><th>Latency</th><th>Guardrail</th></tr></thead>
          <tbody>
            {rows.map((r: any, i: number) => (
              <tr key={i}>
                <td>{r.app}</td><td>{r.usecase}</td><td>{r.step_name}</td><td>{r.model_alias}</td>
                <td className="mono">{r.deployment}</td><td>{r.input_tokens}</td><td>{r.output_tokens}</td>
                <td>${fmt(r.cost_usd)}</td><td>{fmt(r.latency_ms)}</td>
                <td>{r.guardrail_allowed ? (r.guardrail_reason ? <span className="pill warn">flag</span> : <span className="pill good">ok</span>) : <span className="pill bad">block</span>}</td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={10} className="muted">No traces yet — run a pipeline / chat / coaching call.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Cost() {
  const { data: byApp } = useAsync(() => ops.cost("app"));
  const { data: byDep } = useAsync(() => ops.cost("deployment"));
  return (
    <div className="grid cols-2">
      <div className="card"><h3>Cost by App (USD)</h3>
        <HBars data={(byApp || []).map((r: any) => ({ label: r.app || "?", value: r.cost_usd }))} unit="" />
      </div>
      <div className="card"><h3>Cost by Deployment (USD)</h3>
        <HBars data={(byDep || []).map((r: any) => ({ label: r.deployment || "?", value: r.cost_usd }))} />
      </div>
      <div className="card"><h3>Tokens by App</h3>
        <HBars data={(byApp || []).map((r: any) => ({ label: r.app || "?", value: (r.input_tokens || 0) + (r.output_tokens || 0) }))} />
      </div>
    </div>
  );
}

function Guardrails() {
  const { data } = useAsync(() => ops.guardrails());
  if (!data) return <div className="spinner">Loading…</div>;
  return (
    <>
      <div className="grid cols-4">
        <Kpi label="Total Calls" value={data.total_calls} />
        <Kpi label="Blocked" value={data.blocked} />
        <Kpi label="Flagged" value={data.flagged} />
      </div>
      <div className="card">
        <h3>Guardrail Events</h3>
        <div style={{ overflowX: "auto" }}>
          <table>
            <thead><tr><th>App</th><th>Usecase</th><th>Step</th><th>Verdict</th><th>Reason</th></tr></thead>
            <tbody>
              {(data.events || []).map((e: any, i: number) => (
                <tr key={i}>
                  <td>{e.app}</td><td>{e.usecase}</td><td>{e.step_name}</td>
                  <td>{e.guardrail_allowed ? <span className="pill warn">flag</span> : <span className="pill bad">block</span>}</td>
                  <td className="muted">{e.guardrail_reason}</td>
                </tr>
              ))}
              {(data.events || []).length === 0 && <tr><td colSpan={5} className="muted">No guardrail events.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

function Evaluations() {
  const { data } = useAsync(() => ops.evalRuns());
  const rows = data || [];
  return (
    <div className="card">
      <h3>Evaluation Gate Runs</h3>
      <table>
        <thead><tr><th>Usecase</th><th>Env</th><th>Result</th><th>Pass Rate</th><th>Threshold</th><th>Cases</th></tr></thead>
        <tbody>
          {rows.map((r: any, i: number) => (
            <tr key={i}>
              <td>{r.usecase}</td><td>{r.env}</td>
              <td>{r.passed ? <span className="pill good">pass</span> : <span className="pill bad">fail</span>}</td>
              <td>{(r.pass_rate * 100).toFixed(0)}%</td><td>{(r.threshold * 100).toFixed(0)}%</td><td>{r.n_cases}</td>
            </tr>
          ))}
          {rows.length === 0 && <tr><td colSpan={6} className="muted">No eval-gate runs recorded yet.</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

function Feedback() {
  const { data } = useAsync(() => ops.feedback());
  const rows = data || [];
  const [msg, setMsg] = useState("");
  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h3 style={{ margin: 0 }}>Feedback</h3>
        <button className="tab" style={{ border: "1px solid var(--line)", borderRadius: 8 }}
          onClick={() => ops.promote().then((r) => setMsg(`Promoted ${r.promoted ?? 0} correction(s) to golden.`)).catch(() => setMsg("Promote failed."))}>
          Promote corrections → golden
        </button>
      </div>
      {msg && <div className="muted" style={{ margin: "8px 0" }}>{msg}</div>}
      <table>
        <thead><tr><th>Session</th><th>Step</th><th>Rating</th><th>Comment</th><th>Correction</th></tr></thead>
        <tbody>
          {rows.map((r: any, i: number) => (
            <tr key={i}><td className="mono">{r.session_id}</td><td>{r.step_name}</td><td>{r.rating}</td><td className="muted">{r.comment}</td><td className="mono">{r.corrected_output ? "yes" : "—"}</td></tr>
          ))}
          {rows.length === 0 && <tr><td colSpan={5} className="muted">No feedback recorded.</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

function Prompts() {
  const { data } = useAsync(() => ops.prompts());
  const rows = data || [];
  return (
    <div className="card">
      <h3>Registered Prompts</h3>
      {rows.length === 0 && <div className="muted">No prompts registered (platform prompt registry empty or unavailable).</div>}
      <ul>{rows.map((p: any, i: number) => <li key={i} className="mono">{p.name}</li>)}</ul>
    </div>
  );
}
