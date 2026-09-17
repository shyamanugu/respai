// Dependency-free inline-SVG charts (gauge, bars, line). Keeps the build lean
// and guaranteed-to-render — no external charting library.

export function Gauge({ value, max = 100, label }: { value: number; max?: number; label?: string }) {
  const pct = Math.max(0, Math.min(1, value / max));
  const r = 52, c = 2 * Math.PI * r, dash = c * pct;
  const color = pct >= 0.75 ? "var(--good)" : pct >= 0.5 ? "var(--warn)" : "var(--bad)";
  return (
    <div style={{ textAlign: "center" }}>
      <svg width="140" height="140" viewBox="0 0 140 140">
        <circle cx="70" cy="70" r={r} fill="none" stroke="var(--line)" strokeWidth="12" />
        <circle
          cx="70" cy="70" r={r} fill="none" stroke={color} strokeWidth="12" strokeLinecap="round"
          strokeDasharray={`${dash} ${c}`} transform="rotate(-90 70 70)"
        />
        <text x="70" y="66" textAnchor="middle" fontSize="30" fontWeight="800" fill="var(--ink)">
          {Math.round(value)}
        </text>
        <text x="70" y="88" textAnchor="middle" fontSize="12" fill="var(--muted)">/ {max}</text>
      </svg>
      {label && <div className="muted" style={{ fontSize: 13, fontWeight: 600 }}>{label}</div>}
    </div>
  );
}

export interface BarDatum { label: string; value: number; compare?: number; }

export function Bars({ data, unit = "" }: { data: BarDatum[]; unit?: string }) {
  const max = Math.max(1, ...data.flatMap((d) => [d.value, d.compare ?? 0]));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {data.map((d) => (
        <div key={d.label}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13, marginBottom: 3 }}>
            <span>{d.label}</span>
            <span style={{ fontWeight: 700 }}>
              {fmt(d.value)}{unit}
              {d.compare !== undefined && <span className="muted"> · team {fmt(d.compare)}{unit}</span>}
            </span>
          </div>
          <div style={{ background: "var(--line)", borderRadius: 6, height: 10, position: "relative" }}>
            <div style={{ width: `${(d.value / max) * 100}%`, background: "var(--brand)", height: 10, borderRadius: 6 }} />
            {d.compare !== undefined && (
              <div style={{
                position: "absolute", top: -2, left: `${(d.compare / max) * 100}%`,
                width: 2, height: 14, background: "var(--ink)",
              }} />
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

export function Line({ points, height = 120 }: { points: number[]; height?: number }) {
  if (!points.length) return <div className="muted">No trend data.</div>;
  const w = 320, pad = 8;
  const max = Math.max(...points), min = Math.min(...points);
  const range = max - min || 1;
  const step = (w - pad * 2) / Math.max(1, points.length - 1);
  const path = points.map((p, i) => {
    const x = pad + i * step;
    const y = height - pad - ((p - min) / range) * (height - pad * 2);
    return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  return (
    <svg width="100%" viewBox={`0 0 ${w} ${height}`} preserveAspectRatio="none" style={{ maxWidth: "100%" }}>
      <path d={path} fill="none" stroke="var(--brand)" strokeWidth="2.5" />
      {points.map((p, i) => {
        const x = pad + i * step;
        const y = height - pad - ((p - min) / range) * (height - pad * 2);
        return <circle key={i} cx={x} cy={y} r="3" fill="var(--brand)" />;
      })}
    </svg>
  );
}

function fmt(n: number): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return Number.isInteger(n) ? String(n) : n.toFixed(2);
}
