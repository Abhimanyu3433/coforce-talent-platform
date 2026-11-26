import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Badge, Card, ErrorBox, Loading, fmtDate, fmtMonths, useApi } from "../components/ui";

export default function Approvals() {
  const { data, loading, error, reload } = useApi("/api/approvals");
  const [notes, setNotes] = useState({});
  const [busy, setBusy] = useState(null);
  const [err, setErr] = useState(null);

  const decide = async (a, approve) => {
    const key = `${a.demand_id}/${a.emp_id}`;
    setBusy(key); setErr(null);
    try { await api(`/api/approvals/${key}`, { method: "POST", body: { approve, note: notes[key] || null } }); reload(); }
    catch (e) { setErr(e); } finally { setBusy(null); }
  };

  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Release approvals</h1>
          <p className="muted">People on your projects who were nominated for another demand. Approving releases them on their planned date.</p></div>
      </div>
      <ErrorBox error={error || err} />
      <Card>
        {loading && !data ? <Loading /> : data && (
          <table className="table">
            <thead><tr><th>Employee</th><th>Currently on</th><th>Requested for</th><th>Match</th><th>Decision</th></tr></thead>
            <tbody>
              {data.map((a) => {
                const key = `${a.demand_id}/${a.emp_id}`;
                return (
                  <tr key={key}>
                    <td><Link to={`/employees/${a.emp_id}`}>{a.emp_name}</Link><div className="muted small">{a.designation} · {a.grade}</div></td>
                    <td>{a.current_project}<div className="muted small">{a.current_account} · {fmtMonths(a.account_residency_months)} on account · roll-off {fmtDate(a.planned_rolloff)}</div></td>
                    <td><Link to={`/demands/${a.demand_id}`}>{a.role_title}</Link>
                      <div className="muted small">{a.demand_project || a.demand_account} · needed {fmtDate(a.needed_by)} · requested by {a.requested_by}</div></td>
                    <td><b>{a.score}</b> <Badge tone="blue">{a.fit_label}</Badge><div className="muted small">{a.pool.replace("_", " ")}</div></td>
                    <td className="review">
                      <input placeholder="Note (optional)" value={notes[key] || ""} onChange={(e) => setNotes({ ...notes, [key]: e.target.value })} />
                      <button className="btn btn-primary" disabled={busy === key} onClick={() => decide(a, true)}>Approve</button>
                      <button className="btn btn-ghost" disabled={busy === key} onClick={() => decide(a, false)}>Decline</button>
                    </td>
                  </tr>
                );
              })}
              {data.length === 0 && <tr><td colSpan={5} className="muted">No pending release requests.</td></tr>}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
