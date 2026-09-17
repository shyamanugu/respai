import { useEffect, useState } from "react";
import { useFilters } from "../App";
import { api } from "../api";

export default function Analytics() {
  const f = useFilters();
  const [data, setData] = useState<any>(null);

  useEffect(() => {
    if (!f.program || !f.week) return;
    api.analytics(f.program, f.week).then(setData).catch(() => setData(null));
  }, [f.program, f.week]);

  return (
    <div className="card">
      <h3>Analytics — {data?.title || f.program}</h3>
      <p className="muted">
        Cross-team analytics for the week of {f.week}.
        {data?.coming_soon && " Detailed analytics views are coming soon."}
      </p>
    </div>
  );
}
