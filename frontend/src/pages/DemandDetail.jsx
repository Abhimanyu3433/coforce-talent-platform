import { Fragment, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { Badge, Card, ErrorBox, Kpi, Loading, fmtDate, fmtMonths, useApi } from "../components/ui";
import { daysLeft } from "./DemandsList";

const FIT_TONE = { Strong: "green", Good: "blue", Trainable: "amber", Weak: "grey" };
const STATUS_TONE = { suggested: "grey", shortlisted: "blue", nominated: "green", release_requested: "amber",
  release_approved: "green", release_rejected: "red", selected: "green", rejected: "red", client_rejected: "red" };
const POOLS = [["all", "All"], ["bench", "Bench"], ["training", "In training"], ["rolling_off", "Rolling off"], ["rotation", "Rotation (24m+)"], ["allocated", "Allocated"]];
const COMPONENTS = [["mandatory", "Mandatory skills"], ["nice", "Good-to-have"], ["experience", "Experience"], ["role", "Role family"],
  ["context", "Domain / IBU / project type"], ["availability", "Availability"], ["location", "Location"], ["bench_priority", "Bench priority"], ["gap_penalty", "Gap penalty"]];
const ACTIONS = {
  suggested: [["shortlist", "Shortlist"], ["nominate", "Nominate"], ["reject", "Reject"]],
  shortlisted: [["nominate", "Nominate"], ["reject", "Reject"]],
  nominated: [["select", "Selected"], ["client_reject", "Client rejected"], ["reject", "Withdraw"]],
  release_requested: [["reject", "Withdraw"]],
  release_approved: [["select", "Selected"], ["client_reject", "Client rejected"]],
  rejected: [["reset", "Restore"]], release_rejected: [["reset", "Restore"]], client_rejected: [["reset", "Restore"]],
};
const POOL_TONE = { bench: "amber", training: "blue", rolling_off: "green", rotation: "red", allocated: "grey" };
const label = (s) => s.replace(/_/g, " ");

export default function DemandDetail() {
  const { demandId } = useParams();
  const d = useApi(`/api/demands/${demandId}`);
  const m = useApi(`/api/demands/${demandId}/matches`);
  const [pool, setPool] = useState("all");
  const [statusF, setStatusF] = useState("all");
  const [open, setOpen] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [allAlloc, setAllAlloc] = useState(false);
  const meta = useApi("/api/meta");

  const rerun = async () => {
    setBusy(true); setErr(null);
    try { await api(`/api/demands/${demandId}/match`, { method: "POST", params: { include_all_allocated: allAlloc || undefined } }); m.reload(); d.reload(); }
    catch (e) { setErr(e); } finally { setBusy(false); }
  };
  const act = async (emp, action) => {
    const note = ["reject", "client_reject"].includes(action) ? window.prompt("Reason (optional)") ?? null : null;
    setErr(null);
    try { await api(`/api/demands/${demandId}/matches/${emp}/action`, { method: "POST", body: { action, note } }); m.reload(); d.reload(); }
    catch (e) { setErr(e); }
  };
  const setStatus = async (status) => {
    try { await api(`/api/demands/${demandId}/status`, { method: "PATCH", body: { status } }); d.reload(); } catch (e) { setErr(e); }
  };

  if (d.loading && !d.data) return <Loading />;
  if (d.error) return <ErrorBox error={d.error} />;
  const dem = d.data;
  const st = m.data?.stats;
  const cands = (m.data?.candidates || []).filter((c) => (pool === "all" || c.pool === pool) &&
    (statusF === "all" || (statusF === "active" ? !["suggested", "rejected", "release_rejected", "client_rejected"].includes(c.status) : c.status === statusF)));
  const left = daysLeft(dem.needed_by, meta.data?.as_of);

  return (
    <div className="page">
      <Link to="/demands" className="back">‹ All demands</Link>
      <Card>
        <div className="demand-head">
          <div>
            <h1>{dem.role_title} <Badge tone={dem.status === "Open" ? "green" : "grey"}>{dem.status}</Badge> <Badge tone={{ High: "red", Medium: "amber", Low: "grey" }[dem.priority]}>{dem.priority}</Badge></h1>
            <div className="muted">{dem.demand_id} · {dem.account_name || "—"} · {dem.project_name || "no project"} · {dem.ibu_name}</div>
            <div className="small">{dem.positions} position{dem.positions > 1 ? "s" : ""} · {dem.grade_band} · {dem.exp_min}–{dem.exp_max} yrs ·
              {" "}{dem.locations.join(", ") || "Remote"} ({dem.work_mode}{dem.remote_allowed ? ", remote OK" : ""}) · {dem.shift} ·
              {" "}needed by <b>{fmtDate(dem.needed_by)}</b> ({left} days) · over-provisioning ×{dem.overprovision_factor}
              {dem.client_interview ? " · client interview" : ""}</div>
            <div className="chips">
              {dem.skills.map((s) => <span key={s.skill_id} className={`chip ${s.mandatory ? "chip-hl" : ""}`}>{s.name}{s.mandatory && <b>{s.min_proficiency}+</b>}</span>)}
            </div>
            <p className="small muted">{dem.jd_text}</p>
            <div className="small muted">Raised by {dem.raised_by_name} on {fmtDate(dem.created_on)}</div>
          </div>
          <div className="demand-side">
            <select value={dem.status} onChange={(e) => setStatus(e.target.value)}>
              {["Open", "On Hold", "Fulfilled - Internal", "Fulfilled - External", "Cancelled"].map((s) => <option key={s}>{s}</option>)}
            </select>
            <button className="btn btn-primary" onClick={rerun} disabled={busy}>{busy ? "Matching…" : "Re-run matching"}</button>
            {dem.status === "Open" && <Link className="btn btn-ghost" to={`/training/new?demand=${dem.demand_id}`}>Plan training for gaps</Link>}
            <label className="check small"><input type="checkbox" checked={allAlloc} onChange={(e) => setAllAlloc(e.target.checked)} /> include all allocated people</label>
            {m.data?.computed_on && <span className="muted small">Last run {new Date(m.data.computed_on).toLocaleString()}</span>}
          </div>
        </div>
      </Card>
      <ErrorBox error={err || m.error} />

      {st && (
        <div className="kpis">
          <Kpi label="Searched" value={st.pool_size} sub="bench, training, rolling off, rotation" />
          <Kpi label="Eligible" value={st.eligible} sub={Object.entries(st.excluded).map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`).join(" · ")} />
          <Kpi label="Fully match all mandatory" value={st.fully_matching} sub={`${st.fully_matching_in_time} available in time`} tone="blue" />
          <Kpi label="Ready in time incl. training" value={st.ready_in_time_with_training} tone="amber" />
          <Kpi label="Pipeline" value={Object.entries(m.data.status_counts).filter(([k]) => k !== "suggested").reduce((a, [, v]) => a + v, 0)}
               sub={Object.entries(m.data.status_counts).map(([k, v]) => `${v} ${label(k)}`).join(" · ")} />
        </div>
      )}

      <Card title={`Ranked candidates (${cands.length})`} actions={
        <div className="filters-inline">
          <div className="tabs">{POOLS.map(([k, l]) => <button key={k} className={`tab ${pool === k ? "active" : ""}`} onClick={() => setPool(k)}>{l}{k !== "all" && st ? ` ${st.by_pool[k] ?? ""}` : ""}</button>)}</div>
          <select value={statusF} onChange={(e) => setStatusF(e.target.value)}>
            <option value="all">Any status</option><option value="active">In pipeline</option>
            {Object.keys(STATUS_TONE).map((s) => <option key={s} value={s}>{label(s)}</option>)}
          </select>
        </div>}>
        {m.loading && !m.data ? <Loading /> : (
          <table className="table match-table">
            <thead><tr><th>#</th><th>Candidate</th><th>Score</th><th>Availability</th><th>Mandatory skills</th><th>Gap → ready by</th><th>Status</th><th /></tr></thead>
            <tbody>
              {cands.map((c) => (
                <Fragment key={c.emp_id}>
                  <tr className={open === c.emp_id ? "selected" : ""}>
                    <td className="num">{c.rank}</td>
                    <td><Link to={`/employees/${c.emp_id}`}>{c.name}</Link>
                      <div className="muted small">{c.designation} · {c.grade} · {c.total_exp_years} yrs · {c.base_location}</div>
                      <div className="small">{c.flags.map((f) => <Badge key={f} tone={f === "rotation_due" ? "red" : "grey"}>{label(f)}</Badge>)}</div></td>
                    <td><div className="score"><span style={{ width: `${c.score}%` }} /></div>
                      <b>{c.score}</b> <Badge tone={FIT_TONE[c.fit_label]}>{c.fit_label}</Badge></td>
                    <td><Badge tone={POOL_TONE[c.pool]}>{label(c.pool)}</Badge>
                      <div className="small">{c.availability_label}</div><div className="muted small">from {fmtDate(c.availability_date)}</div>
                      {c.requires_approval && <Badge tone="amber" title={`Approver: ${c.approver_name || c.approver_id}`}>Approval needed</Badge>}
                      {c.rotation_due && <div className="muted small">{fmtMonths(c.account_residency_months)} on account</div>}</td>
                    <td><b>{c.mandatory_met}/{c.mandatory_total}</b>
                      <div className="chips">{c.matched.map((s) => (
                        <span key={s.skill_id} className={`chip ${s.met ? "chip-ok" : s.actual ? "chip-warn" : "chip-miss"}`}
                              title={s.via ? `Related: ${s.via}` : `${s.actual}/${s.required}${s.verified ? " verified" : ""}`}>
                          {s.name} <b>{s.actual || "–"}/{s.required}</b>{s.verified && <span className="tick">✓</span>}</span>))}</div></td>
                    <td>{c.gaps.length === 0 ? <span className="text-green">No gaps</span> : (<>
                      <div className="small">{c.training_days} training days</div>
                      <div className={`small ${c.ready_in_time ? "text-green" : "text-red"}`}>ready {fmtDate(c.ready_by)} {c.ready_in_time ? "✓" : "(late)"}</div></>)}</td>
                    <td><Badge tone={STATUS_TONE[c.status]}>{label(c.status)}</Badge>
                      {c.status === "release_requested" && <div className="muted small">awaiting {c.approver_name}</div>}
                      {c.note && <div className="muted small" title={c.note}>“{c.note.slice(0, 30)}”</div>}</td>
                    <td className="actions">
                      {(ACTIONS[c.status] || []).map(([a, l]) => (
                        <button key={a} className={`btn ${a === "nominate" || a === "select" ? "btn-primary" : "btn-ghost"} btn-sm`} onClick={() => act(c.emp_id, a)}>
                          {a === "nominate" && c.requires_approval ? "Request release" : l}</button>))}
                      <button className="btn btn-link btn-sm" onClick={() => setOpen(open === c.emp_id ? null : c.emp_id)}>{open === c.emp_id ? "Hide" : "Why?"}</button>
                    </td>
                  </tr>
                  {open === c.emp_id && (
                    <tr className="explain"><td /><td colSpan={7}>
                      <div className="grid-2">
                        <div>
                          <h4>Score breakdown</h4>
                          {COMPONENTS.map(([k, l]) => c.breakdown[k] && (
                            <div key={k} className="bar-row">
                              <span className="bar-label">{l}</span>
                              <span className="bar-track"><span className={`bar-fill ${c.breakdown[k].points < 0 ? "g5" : ""}`}
                                style={{ width: `${c.breakdown[k].max ? (100 * Math.max(0, c.breakdown[k].points)) / c.breakdown[k].max : Math.min(100, -c.breakdown[k].points * 10)}%` }} /></span>
                              <span className="bar-value">{c.breakdown[k].points}{c.breakdown[k].max ? `/${c.breakdown[k].max}` : ""}</span>
                            </div>))}
                        </div>
                        <div>
                          <h4>Training plan to close gaps</h4>
                          {c.gaps.length === 0 ? <p className="small">Meets every mandatory skill at the required level.</p> : (
                            <table className="table compact"><tbody>
                              {c.gaps.map((g) => <tr key={g.skill_id}><td>{g.name}{g.via && <div className="muted small">has related: {g.via}</div>}</td>
                                <td className="small">{g.kind === "upskill" ? `upskill ${g.actual}→${g.required}` : "new skill"}</td>
                                <td className="small">{g.course_title}</td><td className="num">{g.training_days} d</td></tr>)}
                            </tbody></table>)}
                          <h4>Good-to-have</h4>
                          <div className="chips">{c.nice.map((s) => <span key={s.skill_id} className={`chip ${s.actual ? "chip-ok" : "chip-miss"}`}>{s.name}{s.actual ? <b>{s.actual}</b> : null}</span>)}</div>
                          {c.other_active > 0 && <p className="small text-red">Also in {c.other_active} other active pipeline(s).</p>}
                        </div>
                      </div>
                    </td></tr>
                  )}
                </Fragment>
              ))}
              {cands.length === 0 && <tr><td colSpan={8} className="muted">No candidates for this filter.</td></tr>}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
