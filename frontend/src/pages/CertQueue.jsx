import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Badge, Card, ErrorBox, Loading, fmtDate, useApi } from "../components/ui";

const TONE = { verified: "green", pending: "amber", rejected: "red" };

export default function CertQueue() {
  const [status, setStatus] = useState("pending");
  const { data, loading, error, reload } = useApi("/api/certifications", { status, limit: 200 });
  const [notes, setNotes] = useState({});
  const [busy, setBusy] = useState(null);

  const review = async (id, approve) => {
    setBusy(id);
    try {
      await api(`/api/certifications/${id}/review`, { method: "POST", body: { approve, note: notes[id] || null } });
      reload();
    } finally { setBusy(null); }
  };

  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Certification reviews</h1><p className="muted">Verify certificates employees upload; verified certificates mark the linked skills as verified</p></div>
        <div className="tabs">
          {["pending", "verified", "rejected"].map((s) => (
            <button key={s} className={`tab ${status === s ? "active" : ""}`} onClick={() => setStatus(s)}>{s}</button>
          ))}
        </div>
      </div>
      <Card>
        <ErrorBox error={error} />
        {loading && !data ? <Loading /> : data && (
          <table className="table">
            <thead><tr><th>Employee</th><th>Certification</th><th>Skills</th><th>Issued / valid till</th><th>Status</th>{status === "pending" && <th>Review</th>}</tr></thead>
            <tbody>
              {data.map((c) => (
                <tr key={c.cert_id}>
                  <td><Link to={`/employees/${c.emp_id}`}>{c.emp_name}</Link><div className="muted small">{c.emp_id} · {c.designation} · {c.ibu_id}</div></td>
                  <td>{c.name}<div className="muted small">{c.issuer}{c.credential_id ? ` · ${c.credential_id}` : ""}</div></td>
                  <td className="small">{c.skills.join(", ")}</td>
                  <td className="small">{fmtDate(c.issued_on)}<br />{c.valid_till ? fmtDate(c.valid_till) : "No expiry"}</td>
                  <td><Badge tone={TONE[c.verification_status]}>{c.verification_status}</Badge>
                    {c.review_note && <div className="muted small">{c.review_note}</div>}</td>
                  {status === "pending" && (
                    <td className="review">
                      <input placeholder="Note (optional)" value={notes[c.cert_id] || ""} onChange={(e) => setNotes({ ...notes, [c.cert_id]: e.target.value })} />
                      <button className="btn btn-primary" disabled={busy === c.cert_id} onClick={() => review(c.cert_id, true)}>Verify</button>
                      <button className="btn btn-ghost" disabled={busy === c.cert_id} onClick={() => review(c.cert_id, false)}>Reject</button>
                    </td>
                  )}
                </tr>
              ))}
              {data.length === 0 && <tr><td colSpan={6} className="muted">Nothing here.</td></tr>}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
