import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import PlanView from "../components/PlanView";
import { Badge, Card, ErrorBox, Loading, fmtDate, useApi } from "../components/ui";

export const PLAN_TONE = { pending: "amber", approved: "green", rejected: "red", cancelled: "grey" };

export default function PlanDetail() {
  const { planId } = useParams();
  const { user } = useAuth();
  const { data: p, loading, error, reload } = useApi(`/api/training/plans/${planId}`);
  const [note, setNote] = useState("");
  const [factor, setFactor] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const decide = async (approve) => {
    setBusy(true); setErr(null);
    try {
      await api(`/api/training/plans/${planId}/decision`, { method: "POST", body: {
        approve, note: note || null, overprovision_factor: approve && factor && factor !== p.overprovision_factor ? factor : null } });
      reload();
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  const cancel = async () => {
    if (!window.confirm("Cancel this plan? Scheduled enrollments will be cancelled.")) return;
    try { await api(`/api/training/plans/${planId}/cancel`, { method: "POST" }); reload(); } catch (e) { setErr(e); }
  };

  if (loading && !p) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  const isHr = user.app_role === "hr";
  const canCancel = ["pending", "approved"].includes(p.status) && (p.created_by === user.user_id || isHr);
  const f = factor ?? p.overprovision_factor;

  return (
    <div className="page">
      <Link to="/training" className="back">‹ Training planner</Link>
      <Card>
        <div className="demand-head">
          <div>
            <h1>{p.plan_id} · {p.role_title} <Badge tone={PLAN_TONE[p.status]}>{p.status}</Badge></h1>
            <div className="muted">
              {p.demand_id ? <>For demand <Link to={`/demands/${p.demand_id}`}>{p.demand_id}</Link> · needed by {fmtDate(p.needed_by)} · </> : "Future-need batch · "}
              created by {p.created_by_name} on {fmtDate(p.created_on?.slice(0, 10))}
              {p.decided_by_name && <> · {p.status} by {p.decided_by_name} {p.decision_note ? `— “${p.decision_note}”` : ""}</>}
            </div>
          </div>
          {canCancel && <div className="demand-side"><button className="btn btn-ghost" onClick={cancel}>Cancel plan</button></div>}
        </div>
      </Card>
      <ErrorBox error={err} />

      {isHr && p.status === "pending" && (
        <Card title="HR decision">
          {p.created_by === user.user_id ? <p className="muted">You created this plan, so another HR colleague has to approve it.</p> : (
            <div className="form-row plan-opts">
              {p.plan_type === "demand" && (
                <label>Approve over-provisioning factor <b>×{Number(f).toFixed(2)}</b>
                  {f !== p.overprovision_factor && <span className="text-red small"> (requested ×{p.overprovision_factor} — plan will be recomputed)</span>}
                  <input type="range" min="1" max="3" step="0.25" value={f} onChange={(e) => setFactor(Number(e.target.value))} /></label>
              )}
              <label className="grow">Note<input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Optional" /></label>
              <button className="btn btn-primary" disabled={busy} onClick={() => decide(true)}>Approve & schedule</button>
              <button className="btn btn-ghost" disabled={busy} onClick={() => decide(false)}>Reject</button>
            </div>
          )}
          <p className="small muted">Approving books the classroom batches and enrollments, records you as approver of the factor on the demand,
            and shortlists the trainees on the demand's match list.</p>
        </Card>
      )}
      <PlanView plan={p} />
    </div>
  );
}
