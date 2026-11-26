import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import ProofViewer, { ProofChecks } from "../components/ProofViewer";
import { Badge, Card, ErrorBox, Loading, fmtDate, useApi } from "../components/ui";

const TONE = { verified: "green", pending: "amber", rejected: "red" };

export default function CertQueue() {
  const [status, setStatus] = useState("pending");
  const { data, loading, error, reload } = useApi("/api/certifications", { status, limit: 200 });
  const [notes, setNotes] = useState({});
  const [busy, setBusy] = useState(null);
  const [failed, setFailed] = useState({});
  const [viewing, setViewing] = useState(null);

  const review = async (c, approve) => {
    setBusy(c.cert_id); setFailed((f) => ({ ...f, [c.cert_id]: null }));
    try {
      await api(`/api/certifications/${c.cert_id}/review`, { method: "POST", body: { approve, note: notes[c.cert_id] || null } });
      setViewing(null);
      reload();
    } catch (e) {
      setFailed((f) => ({ ...f, [c.cert_id]: e.message }));
    } finally { setBusy(null); }
  };

  const actions = (c) => {   // a render helper, not a component — keeps the note input focused while typing
    const note = (notes[c.cert_id] || "").trim();
    return (
      <div className="review">
        <input placeholder={c.needs_proof ? "Note, e.g. please upload the certificate" : "Note (needed to reject)"}
               value={notes[c.cert_id] || ""} onChange={(e) => setNotes({ ...notes, [c.cert_id]: e.target.value })} />
        <button className="btn btn-primary" disabled={busy === c.cert_id || c.needs_proof}
                title={c.needs_proof ? "No proof attached yet" : "Mark as verified — linked skills become verified"}
                onClick={() => review(c, true)}>Verify</button>
        <button className="btn btn-ghost" disabled={busy === c.cert_id || !note} title={note ? "" : "Write a note so the employee knows what to fix"}
                onClick={() => review(c, false)}>Reject</button>
        {failed[c.cert_id] && <div className="error small">{failed[c.cert_id]}</div>}
      </div>
    );
  };

  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Certification reviews</h1><p className="muted">Open the proof employees upload, compare it with the details and
          the automatic checks, then verify or reject. Verified certificates mark the linked skills as verified.</p></div>
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
            <thead><tr><th>Employee</th><th>Certification</th><th>Skills</th><th>Issued / valid till</th><th>Proof</th><th>Status</th>{status === "pending" && <th>Review</th>}</tr></thead>
            <tbody>
              {data.map((c) => (
                <tr key={c.cert_id}>
                  <td><Link to={`/employees/${c.emp_id}`}>{c.emp_name}</Link><div className="muted small">{c.emp_id} · {c.designation} · {c.ibu_id}</div></td>
                  <td>{c.name}<div className="muted small">{c.issuer}{c.credential_id ? ` · ${c.credential_id}` : ""}</div></td>
                  <td className="small">{c.skills.join(", ")}</td>
                  <td className="small">{fmtDate(c.issued_on)}<br />{c.valid_till ? fmtDate(c.valid_till) : "No expiry"}</td>
                  <td className="proof-cell">
                    {c.has_proof ? (
                      <>
                        <button className="btn btn-ghost btn-sm" onClick={() => setViewing(c)}>View {c.proof_kind === "pdf" ? "PDF" : "image"}</button>
                        <ProofChecks checks={c.proof_checks} />
                      </>
                    ) : c.needs_proof ? <Badge tone="amber">no proof yet</Badge>
                      : <span className="muted small" title="Imported from historical records — no file on the system">older record, no file</span>}
                  </td>
                  <td><Badge tone={TONE[c.verification_status]}>{c.verification_status}</Badge>
                    {c.review_note && <div className="muted small">{c.review_note}</div>}</td>
                  {status === "pending" && <td>{actions(c)}</td>}
                </tr>
              ))}
              {data.length === 0 && <tr><td colSpan={7} className="muted">Nothing here.</td></tr>}
            </tbody>
          </table>
        )}
      </Card>
      {viewing && (
        <ProofViewer cert={viewing} onClose={() => setViewing(null)}>
          {viewing.verification_status === "pending" && <div className="modal-foot">{actions(viewing)}</div>}
        </ProofViewer>
      )}
    </div>
  );
}
