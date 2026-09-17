import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";

export default function Login() {
  const { user, refresh } = useAuth();
  const nav = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  if (user) { nav("/individual"); return null; }

  const passwordLogin = async () => {
    setErr(""); setBusy(true);
    try {
      const r = await api.loginPassword(username, password);
      if (r.authenticated) { await refresh(); nav("/individual"); }
      else setErr(r.detail || "Invalid credentials");
    } catch { setErr("Login failed"); }
    finally { setBusy(false); }
  };

  const ssoLogin = async () => {
    setErr("");
    try { const { auth_url } = await api.ssoLoginUrl(); window.location.href = auth_url; }
    catch { setErr("SSO is not configured"); }
  };

  return (
    <div className="login-wrap">
      <div className="card login-card">
        <div className="brand" style={{ fontSize: 26 }}>APIX<small>AFNI Performance Index</small></div>
        <button className="btn" style={{ width: "100%", marginBottom: 16 }} onClick={ssoLogin}>
          Sign in with Microsoft
        </button>
        <div className="muted" style={{ textAlign: "center", fontSize: 12, margin: "8px 0" }}>or sign in with credentials</div>
        <label className="muted" style={{ fontSize: 12 }}>Username or UPN</label>
        <input type="text" value={username} onChange={(e) => setUsername(e.target.value)} />
        <label className="muted" style={{ fontSize: 12, marginTop: 8, display: "block" }}>Password</label>
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
               onKeyDown={(e) => e.key === "Enter" && passwordLogin()} />
        {err && <div className="error" style={{ marginTop: 10 }}>{err}</div>}
        <button className="btn" style={{ width: "100%", marginTop: 16 }} onClick={passwordLogin} disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </div>
    </div>
  );
}
