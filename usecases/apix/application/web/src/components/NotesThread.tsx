import { useEffect, useState } from "react";
import { api, Note } from "../api";
import { useAuth } from "../auth";

// Coaching notes for an employee. namespace "" = Individual Report,
// "metrics" = the Metrics page (mirrors the backend store convention).
export function NotesThread({ empId, program, week, namespace = "" }:
  { empId: string; program: string; week: string; namespace?: string }) {
  const { user } = useAuth();
  const [notes, setNotes] = useState<Note[]>([]);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(false);

  const load = async () => {
    if (!empId) return;
    setLoading(true);
    try { setNotes(await api.notes(empId, program, week, namespace)); }
    catch { setNotes([]); }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, [empId, week, namespace]);

  const add = async () => {
    if (!draft.trim()) return;
    await api.addNote(empId, program, draft.trim(), week, namespace);
    setDraft("");
    load();
  };

  const remove = async (id: string) => {
    await api.deleteNote(empId, id, namespace);
    load();
  };

  return (
    <div className="card">
      <h3>Coaching Notes</h3>
      <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
        <input type="text" value={draft} onChange={(e) => setDraft(e.target.value)}
               placeholder="Add a coaching note…" onKeyDown={(e) => e.key === "Enter" && add()} />
        <button className="btn sm" onClick={add}>Add</button>
      </div>
      {loading && <div className="muted">Loading…</div>}
      {!loading && notes.length === 0 && <div className="muted">No notes yet.</div>}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {notes.map((n) => (
          <div key={n.id} style={{ borderBottom: "1px solid var(--line)", paddingBottom: 8 }}>
            <div style={{ fontSize: 14 }}>{n.note}</div>
            <div className="muted" style={{ fontSize: 12, display: "flex", justifyContent: "space-between" }}>
              <span>{n.author_name || n.author} · {n.date || ""}</span>
              {String(n.author) === String(user?.sub) && (
                <button className="btn ghost sm" onClick={() => remove(n.id)}>Delete</button>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
