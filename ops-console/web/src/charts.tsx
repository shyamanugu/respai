// Minimal dependency-free SVG charts for the ops console.

export function HBars({ data, unit = "" }: { data: { label: string; value: number }[]; unit?: string }) {
  const max = Math.max(1, ...data.map((d) => d.value));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {data.map((d) => (
        <div key={d.label}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13, marginBottom: 3 }}>
            <span>{d.label}</span><span style={{ fontWeight: 700 }}>{fmt(d.value)}{unit}</span>
          </div>
          <div style={{ background: "var(--line)", borderRadius: 6, height: 10 }}>
            <div style={{ width: `${(d.value / max) * 100}%`, background: "var(--brand)", height: 10, borderRadius: 6 }} />
          </div>
        </div>
      ))}
      {data.length === 0 && <div className="muted">No data.</div>}
    </div>
  );
}

export function Line({ points, height = 120 }: { points: number[]; height?: number }) {
  if (!points.length) return <div className="muted">No data.</div>;
  const w = 320, pad = 8, max = Math.max(...points), min = Math.min(...points), range = max - min || 1;
  const step = (w - pad * 2) / Math.max(1, points.length - 1);
  const path = points.map((p, i) => {
    const x = pad + i * step, y = height - pad - ((p - min) / range) * (height - pad * 2);
    return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  return (
    <svg width="100%" viewBox={`0 0 ${w} ${height}`} preserveAspectRatio="none" style={{ maxWidth: "100%" }}>
      <path d={path} fill="none" stroke="var(--brand)" strokeWidth="2.5" />
    </svg>
  );
}

export function fmt(n: number): string {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "—";
  const x = Number(n);
  return Number.isInteger(x) ? String(x) : x.toFixed(x < 1 ? 4 : 2);
}
