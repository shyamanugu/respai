import { useEffect, useRef, useState } from "react";
import { api } from "../api";

// Floating chat widget. Mints a short-lived JWT from the dashboard API, then
// calls the chatbot service DIRECTLY from the browser (bearer token). Refreshes
// the token on 401. userId is the principal's employee id.

interface Msg { role: "user" | "assistant"; text: string; }

export function ChatWidget({ userId }: { userId: string }) {
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const tokenRef = useRef<{ token: string; base: string } | null>(null);
  const sessionRef = useRef<string>(sessionStorage.getItem("apix_chat_session") || "");

  async function ensureToken(force = false) {
    if (!force && tokenRef.current) return tokenRef.current;
    const t = await api.chatToken();
    tokenRef.current = { token: t.token, base: t.chat_api_base };
    return tokenRef.current;
  }

  async function send() {
    const q = input.trim();
    if (!q || busy) return;
    setInput("");
    setMsgs((m) => [...m, { role: "user", text: q }]);
    setBusy(true);
    try {
      let answer = await callChat(q, false);
      setMsgs((m) => [...m, { role: "assistant", text: answer }]);
    } catch {
      setMsgs((m) => [...m, { role: "assistant", text: "Sorry — I couldn't reach the assistant." }]);
    } finally {
      setBusy(false);
    }
  }

  async function callChat(message: string, retried: boolean): Promise<string> {
    const t = await ensureToken(retried);
    const res = await fetch(`${t.base}/chat_agent`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${t.token}` },
      body: JSON.stringify({ user_id: Number(userId) || userId, message, session_id: sessionRef.current || undefined }),
    });
    if (res.status === 401 && !retried) return callChat(message, true);
    if (!res.ok) throw new Error("chat failed");
    const data = await res.json();
    if (data.session_id) {
      sessionRef.current = data.session_id;
      sessionStorage.setItem("apix_chat_session", data.session_id);
    }
    return data.answer || data.response || data.message || "(no answer)";
  }

  useEffect(() => { if (open) ensureToken().catch(() => {}); }, [open]);

  return (
    <>
      <button
        onClick={() => setOpen((o) => !o)}
        style={{
          position: "fixed", right: 24, bottom: 24, width: 56, height: 56, borderRadius: "50%",
          background: "var(--brand)", color: "#fff", border: "none", fontSize: 24, boxShadow: "var(--shadow)", zIndex: 50,
        }}
        title="Ask APIX"
      >💬</button>
      {open && (
        <div style={{
          position: "fixed", right: 24, bottom: 92, width: 360, height: 480, background: "#fff",
          border: "1px solid var(--line)", borderRadius: 12, boxShadow: "var(--shadow)", display: "flex",
          flexDirection: "column", zIndex: 50,
        }}>
          <div style={{ padding: "12px 16px", background: "var(--brand)", color: "#fff", borderRadius: "12px 12px 0 0", fontWeight: 700 }}>
            APIX Assistant
          </div>
          <div style={{ flex: 1, overflowY: "auto", padding: 12, display: "flex", flexDirection: "column", gap: 8 }}>
            {msgs.length === 0 && <div className="muted" style={{ fontSize: 13 }}>Ask about your team's performance, metrics, or coaching.</div>}
            {msgs.map((m, i) => (
              <div key={i} style={{
                alignSelf: m.role === "user" ? "flex-end" : "flex-start",
                background: m.role === "user" ? "var(--brand-50)" : "var(--bg)",
                border: "1px solid var(--line)", borderRadius: 10, padding: "8px 10px", fontSize: 13, maxWidth: "85%", whiteSpace: "pre-wrap",
              }}>{m.text}</div>
            ))}
            {busy && <div className="muted" style={{ fontSize: 13 }}>Thinking…</div>}
          </div>
          <div style={{ display: "flex", gap: 8, padding: 12, borderTop: "1px solid var(--line)" }}>
            <input
              type="text" value={input} onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && send()} placeholder="Ask a question…"
            />
            <button className="btn sm" onClick={send} disabled={busy}>Send</button>
          </div>
        </div>
      )}
    </>
  );
}
