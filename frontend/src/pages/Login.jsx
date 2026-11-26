import { useEffect, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";

export default function Login() {
  const { user, login } = useAuth();
  const nav = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [demo, setDemo] = useState(null);
  const [backendDown, setBackendDown] = useState(false);

  useEffect(() => {
    let live = true, timer;
    const load = () => api("/api/auth/demo-users")
      .then((d) => { if (live) { setDemo(d); setBackendDown(false); } })
      .catch((e) => {
        if (!live) return;
        // 404 = demo mode switched off on purpose; anything else = the API can't be reached
        if (e.status !== 404) { setBackendDown(true); timer = setTimeout(load, 3000); }
      });
    load();
    return () => { live = false; clearTimeout(timer); };
  }, []);
  if (user) return <Navigate to="/" replace />;

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      nav("/");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-wrap">
      <form className="login card" onSubmit={submit}>
        <div className="brand big">Coforce<span>Talent</span></div>
        <p className="muted">Residency, skills and bench-to-demand matching</p>
        <label>Email<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus /></label>
        <label>Password<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
        {error && <div className="error">{error}</div>}
        <button className="btn btn-primary" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
        {backendDown && (
          <div className="error small">
            Can't reach the backend at <code>localhost:8000</code>, so sign-in and the demo accounts aren't available.
            Start it in a separate PowerShell window: <code>{"cd coforce\\backend"}</code> then{" "}
            <code>uvicorn app.main:app --reload --port 8000</code> (with the .venv activated). This page retries every 3 seconds.
          </div>
        )}
        {demo && (
          <div className="demo">
            <div className="small muted">Demo accounts (password <code>{demo.password}</code>) — click to fill</div>
            {demo.users.map((u) => (
              <button type="button" key={u.email} className="demo-user"
                      onClick={() => { setEmail(u.email); setPassword(demo.password); }}>
                <b>{u.app_role}</b> {u.name} <span className="muted">· {u.designation} · {u.ibu_id}</span>
              </button>
            ))}
          </div>
        )}
      </form>
    </div>
  );
}
