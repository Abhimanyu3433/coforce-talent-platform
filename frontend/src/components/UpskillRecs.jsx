import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { Badge, Card, ErrorBox, Loading, fmtDate, useApi } from "./ui";

const PARTS = [["unlock", "Unlocks demands"], ["demand", "Market demand"], ["adjacency", "Close to your skills"], ["role", "Role fit"], ["effort", "Effort"]];

/** Recommendations for the signed-in user (empId omitted, editable) or for any employee (read-only). */
export default function UpskillRecs({ empId, editable = false, onStarted }) {
  const { isPrivileged } = useAuth();
  const { data, loading, error, reload } = useApi(empId ? `/api/employees/${empId}/upskilling` : "/api/me/upskilling");
  const [open, setOpen] = useState(null);
  const [msg, setMsg] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(null);

  const start = async (r, batchId) => {
    setBusy(r.skill_id); setErr(null); setMsg(null);
    try {
      const out = await api(`/api/me/upskilling/${r.skill_id}/start`, { method: "POST", body: { mode: "self-paced", batch_id: batchId || null } });
      setMsg(`Enrolled: ${out.enrollments.map((e) => `${e.skill} (${e.mode}, ${fmtDate(e.start_date)}–${fmtDate(e.end_date)})`).join(" → ")}. Ready by ${fmtDate(out.ready_by)}.`);
      reload(); onStarted?.();
    } catch (e) { setErr(e); } finally { setBusy(null); }
  };

  return (
    <Card title={editable ? "Recommended for you to learn next" : "Upskilling recommendations"}
          actions={data && <span className="muted small">based on {data.relevant_demands} open demands you could be considered for</span>}>
      <ErrorBox error={error || err} />
      {msg && <div className="callout callout-green small">{msg}</div>}
      {loading && !data ? <Loading /> : data && (
        data.recommendations.length === 0 ? <p className="muted">{data.note || "No recommendations right now — your skills already cover open demand well."}</p> : (
          <div className="recs">
            {data.recommendations.map((r, i) => (
              <div key={r.skill_id} className={`rec ${r.already_learning ? "learning" : ""}`}>
                <div className="rec-head">
                  <span className="rec-rank">{i + 1}</span>
                  <div className="grow">
                    <b>{r.skill}</b> <span className="muted small">{r.category}{r.current_level ? ` · you're at level ${r.current_level}` : ""}</span>
                    <div className="score"><span style={{ width: `${Math.min(100, r.score)}%` }} /></div>
                  </div>
                  <div className="rec-score"><b>{r.score}</b><span className="muted small">/100</span></div>
                </div>
                <div className="chips">
                  {r.unlocks.full > 0 && <Badge tone="green">fully match {r.unlocks.full} demand{r.unlocks.full > 1 ? "s" : ""}</Badge>}
                  {r.unlocks.eligible > 0 && <Badge tone="blue">+{r.unlocks.eligible} eligible</Badge>}
                  <Badge tone="grey">{r.training_days} training days</Badge>
                  {r.already_learning && <Badge tone="amber">already learning</Badge>}
                </div>
                <ul className="reasons">{r.reasons.map((x) => <li key={x}>{x}</li>)}</ul>
                <div className="path">
                  {r.path.map((p, j) => (
                    <span key={p.skill_id} className={`step ${p.is_prerequisite ? "pre" : ""}`}>
                      {j > 0 && <span className="arrow">→</span>}{p.skill} <span className="muted small">{p.days} d{p.is_prerequisite ? " · prerequisite" : ""}</span>
                    </span>))}
                </div>
                <div className="form-row">
                  {editable && !r.already_learning && <button className="btn btn-primary btn-sm" disabled={busy === r.skill_id} onClick={() => start(r)}>Start learning (self-paced)</button>}
                  {editable && !r.already_learning && r.open_batches.map((b) => (
                    <button key={b.batch_id} className="btn btn-ghost btn-sm" disabled={busy === r.skill_id} onClick={() => start(r, b.batch_id)}
                            title={`${b.title} · ${b.delivery} · ${b.location}`}>Join classroom batch {fmtDate(b.start_date)} ({b.seats_left} seats)</button>))}
                  {!editable && r.open_batches.length > 0 && <span className="small muted">Open batch: {fmtDate(r.open_batches[0].start_date)} ({r.open_batches[0].seats_left} seats)</span>}
                  <button className="btn btn-link btn-sm" onClick={() => setOpen(open === r.skill_id ? null : r.skill_id)}>{open === r.skill_id ? "Hide details" : "Why this skill?"}</button>
                </div>
                {open === r.skill_id && (
                  <div className="rec-detail">
                    {PARTS.map(([k, l]) => (
                      <div key={k} className="bar-row">
                        <span className="bar-label">{l}</span>
                        <span className="bar-track"><span className={`bar-fill ${k === "effort" ? "g5" : ""}`}
                          style={{ width: `${(100 * Math.abs(r.components[k])) / ({ unlock: 35, demand: 25, adjacency: 20, role: 15, effort: 5 })[k]}%` }} /></span>
                        <span className="bar-value">{r.components[k]}</span>
                      </div>))}
                    {r.demands.length > 0 && <>
                      <h4>Demands this opens up</h4>
                      {r.demands.map((d) => (
                        <div key={d.demand_id} className="small">
                          {isPrivileged ? <Link to={`/demands/${d.demand_id}`}>{d.role_title}</Link> : <b>{d.role_title}</b>}
                          {" "}· {d.account_name || "—"} · needed {fmtDate(d.needed_by)} · your score would be <b>{d.score_with_skill}</b>
                          {d.full_match && <Badge tone="green">full match</Badge>}
                        </div>))}
                    </>}
                  </div>
                )}
              </div>
            ))}
          </div>
        )
      )}
    </Card>
  );
}
