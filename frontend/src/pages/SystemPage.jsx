import { useState } from "react";
import { api } from "../api";
import { Badge, Card, ErrorBox, Loading, useApi } from "../components/ui";

export default function SystemPage() {
  const health = useApi("/api/health");
  const runs = useApi("/api/admin/jobs", { limit: 15 });
  const chat = useApi("/api/chat/demand/status");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const runNow = async () => {
    setBusy(true); setErr(null);
    try { await api("/api/admin/jobs/nightly", { method: "POST" }); runs.reload(); health.reload(); }
    catch (e) { setErr(e); } finally { setBusy(false); }
  };
  const h = health.data;
  return (
    <div className="page">
      <div className="page-head">
        <div><h1>System</h1><p className="muted">Nightly maintenance and environment</p></div>
        <button className="btn btn-primary" onClick={runNow} disabled={busy}>{busy ? "Running (~20 s)…" : "Run nightly job now"}</button>
      </div>
      <ErrorBox error={err || runs.error} />
      {h && (
        <Card title="Environment">
          <div className="grid-3">
            <div><div className="muted small">Version</div><b>{h.version}</b></div>
            <div><div className="muted small">Data as of</div><b>{h.as_of}</b></div>
            <div><div className="muted small">Employees</div><b>{h.employees}</b></div>
            <div><div className="muted small">Nightly schedule</div><b>{h.nightly_at}</b></div>
            <div><div className="muted small">Last nightly run</div><b>{h.last_nightly_run || "never"}</b></div>
            <div><div className="muted small">Demand assistant</div><b>{chat.data ? (chat.data.mode === "gemini" ? `Gemini (${chat.data.model})` : "Built-in parser") : "…"}</b></div>
          </div>
          <p className="small muted">Database: <code>{h.db}</code></p>
        </Card>
      )}
      <Card title="Job runs">
        {runs.loading && !runs.data ? <Loading /> : runs.data && (
          <table className="table">
            <thead><tr><th>Started (UTC)</th><th>By</th><th className="num">Seconds</th><th>Status</th><th>Details</th></tr></thead>
            <tbody>
              {runs.data.map((r) => (
                <tr key={r.id}>
                  <td className="small">{r.started_on.replace("T", " ").slice(0, 19)}</td><td className="small">{r.triggered_by}</td>
                  <td className="num">{r.seconds}</td><td><Badge tone={r.status === "ok" ? "green" : "amber"}>{r.status}</Badge></td>
                  <td className="small">{Object.entries(r.detail).map(([k, v]) => (
                    <div key={k}><b>{k}</b>: {v.error ? <span className="text-red">{v.error}</span> :
                      Object.entries(v).filter(([kk]) => kk !== "computed_on").map(([kk, vv]) => `${kk.replace(/_/g, " ")} ${vv}`).join(" · ")}</div>))}</td>
                </tr>))}
              {runs.data.length === 0 && <tr><td colSpan={5} className="muted">No runs yet.</td></tr>}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
