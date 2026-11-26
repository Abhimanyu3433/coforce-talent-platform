import { useEffect, useMemo, useRef, useState } from "react";
import { api, readFileBase64 } from "../api";
import { useAuth } from "../auth";
import ProofViewer from "./ProofViewer";
import SkillPicker from "./SkillPicker";
import { Badge, Card, ProfDots, StatusBadge, fmtDate, fmtMonths } from "./ui";

const SOURCE_LABEL = {
  project: "Project", prior_experience: "Prior experience", training: "Training", self_declared: "Self-declared",
  certification: "Certification", academic: "Academic",
};
const CERT_TONE = { verified: "green", pending: "amber", rejected: "red" };
const GROUPS = [
  { key: "tech", label: "Technical skills", test: (s) => !["tool", "domain", "business", "methodology"].includes(s.type) },
  { key: "tool", label: "Tools", test: (s) => s.type === "tool" },
  { key: "domain", label: "Domain", test: (s) => s.type === "domain" },
  { key: "biz", label: "Methods & business", test: (s) => ["business", "methodology"].includes(s.type) },
];

const PROOF_EXTS = [".pdf", ".png", ".jpg", ".jpeg"];
const PROOF_ACCEPT = ".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg";

export default function ProfileView({ p, editable = false, onChanged, certPrefill }) {
  const { user } = useAuth() || {};
  const canSeeProof = editable || user?.app_role === "hr";
  const [viewing, setViewing] = useState(null);
  const [group, setGroup] = useState("tech");
  const grouped = useMemo(() => Object.fromEntries(GROUPS.map((g) => [g.key, p.skills.filter(g.test)])), [p.skills]);
  const r = p.residency;
  const thr = r.rotation_threshold_months || 24;
  const pct = r.account_residency_months ? Math.min(100, (100 * r.account_residency_months) / (thr * 1.5)) : 0;

  return (
    <div className="profile">
      <Card className="profile-head">
        <div className="ph-main">
          <div className="avatar">{p.name.split(" ").map((x) => x[0]).join("").slice(0, 2)}</div>
          <div>
            <h1>{p.name} <StatusBadge status={p.status} /></h1>
            <div>{p.designation} · {p.grade} · {p.role_family_name}</div>
            <div className="muted small">{p.emp_id} · {p.email} · {p.ibu_name} · {p.base_location}{p.is_onsite ? " (onsite)" : ""}</div>
          </div>
        </div>
        <dl className="facts">
          <div><dt>Experience</dt><dd>{p.total_exp_years} yrs <span className="muted small">({p.exp_at_joining} at joining)</span></dd></div>
          <div><dt>Joined</dt><dd>{fmtDate(p.date_of_joining)}</dd></div>
          <div><dt>Hired as</dt><dd>{p.hired_as_role}</dd></div>
          <div><dt>Reports to</dt><dd>{p.reporting_manager_name || "—"}</dd></div>
          <div><dt>Mobility</dt><dd>{p.open_to_relocate ? "Open to relocate" : "Not relocating"} · prefers {p.work_mode_preference}</dd></div>
          {p.last_working_day && <div><dt>Last working day</dt><dd>{fmtDate(p.last_working_day)}</dd></div>}
        </dl>
      </Card>

      <div className="grid-2">
        <Card title="Residency">
          {["allocated", "notice"].includes(p.status) ? (
            <>
              <div className="res-line"><span>Current account</span><b>{p.allocations.find((a) => a.is_current)?.account_name || r.current_account_id}</b></div>
              <div className="res-line"><span>Current project</span><b>{p.allocations.find((a) => a.is_current)?.project_name || r.current_project_id}</b></div>
              <div className="res-line"><span>On project</span><b>{fmtMonths(r.project_residency_months)}</b> <span className="muted small">since {fmtDate(r.project_since)}</span></div>
              <div className="res-line"><span>On account</span><b>{fmtMonths(r.account_residency_months)}</b> <span className="muted small">since {fmtDate(r.account_since)}</span></div>
              <div className="meter" title={`Threshold ${thr} months`}>
                <span className={`meter-fill ${r.rotation_due ? "over" : ""}`} style={{ width: `${pct}%` }} />
                <span className="meter-mark" style={{ left: `${100 / 1.5}%` }} />
              </div>
              <div className="muted small">Rotation threshold {thr} months · total time on this account {fmtMonths(r.total_account_months)}</div>
              {r.rotation_due ? (
                <div className="callout callout-red">Past the {thr}-month residency rule by {r.months_over_threshold} months — rotation candidate.
                  {r.release_requires_approval ? " Release needs approval from the project manager." : ""}</div>
              ) : r.release_requires_approval ? (
                <div className="callout">Allocated — visible for demands, release needs approval from the project manager.</div>
              ) : null}
              <div className="res-line"><span>Planned roll-off</span><b>{fmtDate(r.planned_rolloff)}</b> <span className="muted small">in {r.days_to_rolloff} days</span></div>
            </>
          ) : (
            <>
              <div className="res-line"><span>On bench since</span><b>{fmtDate(p.bench_since)}</b> <span className="muted small">{r.bench_aging_days} days</span></div>
              <div className="res-line"><span>Last project</span><b>{p.allocations[0]?.project_name || "—"}</b></div>
              <div className="callout callout-blue">Available now for demands.</div>
            </>
          )}
          <div className="res-line"><span>Bench days (last 12 m)</span><b>{r.bench_days_12m ?? "—"}</b></div>
          <div className="res-line"><span>Accounts / projects served</span><b>{r.accounts_served ?? 0} / {r.projects_served ?? 0}</b></div>
          {r.cool_off?.length > 0 && (
            <div className="small">Cool-off: {r.cool_off.map((c) => `${c.account_name} until ${fmtDate(c.until)}`).join("; ")}</div>
          )}
          <h4>Account history</h4>
          <ul className="timeline">
            {r.account_history.slice().reverse().map((h, i) => (
              <li key={i} className={h.current ? "current" : ""}>
                <b>{h.account_name}</b> · {fmtMonths(h.months)} <span className="muted small">{fmtDate(h.start)} – {h.current ? "now" : fmtDate(h.end)} · {h.stints} stint{h.stints > 1 ? "s" : ""}</span>
              </li>
            ))}
          </ul>
        </Card>

        <Card title={`Skills (${p.skills.length})`} actions={
          <div className="tabs">
            {GROUPS.map((g) => (
              <button key={g.key} className={`tab ${group === g.key ? "active" : ""}`} onClick={() => setGroup(g.key)}>
                {g.label} <span className="muted">{grouped[g.key].length}</span>
              </button>
            ))}
          </div>}>
          <table className="table compact">
            <thead><tr><th>Skill</th><th>Level</th><th className="num">Years</th><th>Last used</th><th>Source</th>{editable && <th />}</tr></thead>
            <tbody>
              {grouped[group].map((s) => (
                <tr key={s.skill_id}>
                  <td>{s.name} {s.verified && <span className="tick" title="Verified">✓</span>}<div className="muted small">{s.category}</div></td>
                  <td><ProfDots level={s.proficiency} /></td>
                  <td className="num">{Number(s.years_used).toFixed(1)}</td>
                  <td>{fmtDate(s.last_used)}</td>
                  <td><Badge tone={s.source === "self_declared" ? "grey" : "blue"}>{SOURCE_LABEL[s.source] || s.source}</Badge></td>
                  {editable && <td>{s.source === "self_declared" &&
                    <button className="btn btn-link" onClick={async () => { await api(`/api/me/skills/${s.skill_id}`, { method: "DELETE" }); onChanged?.(); }}>Remove</button>}</td>}
                </tr>
              ))}
              {grouped[group].length === 0 && <tr><td colSpan={6} className="muted">None</td></tr>}
            </tbody>
          </table>
          {editable && <AddSkill onAdded={onChanged} />}
        </Card>
      </div>

      <Card title="Project history">
        <div className="projects">
          {p.allocations.map((a) => (
            <div key={a.allocation_id} className={`project ${a.is_current ? "current" : ""}`}>
              <div className="project-head">
                <b>{a.project_name}</b> {a.is_current && <Badge tone="green">current</Badge>}
                <span className="muted small"> · {a.account_name} · {a.project_type}</span>
              </div>
              <div className="small">{a.role_on_project} · {fmtDate(a.start_date)} – {a.is_current ? `planned ${fmtDate(a.end_date)}` : fmtDate(a.end_date)}
                · {a.allocation_pct}%{a.billable ? "" : " · non-billable"} {a.project_manager && <span className="muted">· PM {a.project_manager}</span>}</div>
              <p>{a.work_description}</p>
              <div className="chips">{a.skills_used.map((s) => <span key={s} className="chip">{s}</span>)}</div>
            </div>
          ))}
          {p.allocations.length === 0 && <div className="muted">No project allocations yet.</div>}
        </div>
      </Card>

      <div className="grid-2">
        <Card title={`Certifications (${p.certifications.length})`}>
          <table className="table compact">
            <thead><tr><th>Certification</th><th>Issued</th><th>Valid till</th><th>Status</th><th>Proof</th></tr></thead>
            <tbody>
              {p.certifications.map((c) => (
                <tr key={c.cert_id}>
                  <td>{c.name}<div className="muted small">{c.issuer}{c.credential_id ? ` · ${c.credential_id}` : ""}</div></td>
                  <td>{fmtDate(c.issued_on)}</td>
                  <td>{c.valid_till ? fmtDate(c.valid_till) : "No expiry"} {c.expired && <Badge tone="red">expired</Badge>}</td>
                  <td><Badge tone={CERT_TONE[c.verification_status]}>{c.verification_status}</Badge>
                    {c.review_note && c.verification_status !== "verified" && <div className="muted small">{c.review_note}</div>}</td>
                  <td className="proof-cell">
                    {c.has_proof ? (canSeeProof ? <button className="btn btn-link" onClick={() => setViewing(c)}>View</button>
                                                : <Badge tone="blue">attached</Badge>)
                      : c.needs_proof ? <Badge tone="amber">proof needed</Badge>
                      : <span className="muted small">older record</span>}
                    {editable && c.verification_status !== "verified" &&
                      <ProofUpload cert={c} onDone={onChanged} label={c.verification_status === "rejected" ? "Re-submit" : c.has_proof ? "Replace" : "Attach"} />}
                  </td>
                </tr>
              ))}
              {p.certifications.length === 0 && <tr><td colSpan={5} className="muted">None yet</td></tr>}
            </tbody>
          </table>
          {editable && <AddCertification onAdded={onChanged} prefill={certPrefill} />}
          {viewing && <ProofViewer cert={viewing} onClose={() => setViewing(null)} />}
        </Card>
        <Card title={`Training (${p.trainings.length})`}>
          <table className="table compact">
            <thead><tr><th>Course</th><th>Mode</th><th>Dates</th><th>Status</th><th className="num">Score</th></tr></thead>
            <tbody>
              {p.trainings.map((t) => (
                <tr key={t.enrollment_id}>
                  <td>{t.title}<div className="muted small">{t.level} · {t.source.replace("_", " ")}</div></td>
                  <td>{t.mode}</td>
                  <td className="small">{fmtDate(t.start_date)} – {fmtDate(t.end_date)}</td>
                  <td><Badge tone={t.status === "completed" ? "green" : "blue"}>{t.status.replace("_", " ")}</Badge></td>
                  <td className="num">{t.assessment_score ?? "—"}</td>
                </tr>
              ))}
              {p.trainings.length === 0 && <tr><td colSpan={5} className="muted">No training records</td></tr>}
            </tbody>
          </table>
        </Card>
      </div>
    </div>
  );
}

function AddSkill({ onAdded }) {
  const [f, setF] = useState({ skill: "", proficiency: 2, years_used: "", last_used: "" });
  const [msg, setMsg] = useState(null);
  const [err, setErr] = useState(null);
  const submit = async (e) => {
    e.preventDefault();
    setMsg(null); setErr(null);
    try {
      const r = await api("/api/me/skills", { method: "POST", body: {
        skill: f.skill, proficiency: Number(f.proficiency),
        years_used: f.years_used === "" ? null : Number(f.years_used), last_used: f.last_used || null } });
      setMsg(`${r.created ? "Added" : "Updated"} ${r.skill.name}${r.normalized_from ? ` (recognised from "${r.normalized_from}")` : ""} — unverified until backed by a certification or project.`);
      setF({ skill: "", proficiency: 2, years_used: "", last_used: "" });
      onAdded?.();
    } catch (ex) {
      const c = ex.detail?.candidates;
      setErr(c?.length ? `${ex.message}. Did you mean: ${c.map((x) => x.name).join(", ")}?` : ex.message);
    }
  };
  return (
    <form className="inline-form" onSubmit={submit}>
      <h4>Add a skill or tool</h4>
      <div className="form-row">
        <div className="grow"><SkillPicker multi={false} value={f.skill} onChange={(v) => setF({ ...f, skill: v })} placeholder="e.g. dbt, Kafka, Power BI" /></div>
        <select value={f.proficiency} onChange={(e) => setF({ ...f, proficiency: e.target.value })} title="Proficiency">
          {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>Level {n}</option>)}
        </select>
        <input type="number" step="0.5" min="0" placeholder="Years" value={f.years_used} onChange={(e) => setF({ ...f, years_used: e.target.value })} style={{ width: 80 }} />
        <input type="date" value={f.last_used} onChange={(e) => setF({ ...f, last_used: e.target.value })} title="Last used" />
        <button className="btn btn-primary" disabled={!f.skill}>Add</button>
      </div>
      {msg && <div className="ok small">{msg}</div>}
      {err && <div className="error small">{err}</div>}
    </form>
  );
}

function ProofUpload({ cert, label, onDone }) {
  const input = useRef(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const pick = async (file) => {
    if (!file) return;
    setBusy(true); setErr(null);
    try {
      const data_base64 = await readFileBase64(file, { exts: PROOF_EXTS, label: "PDF or image of the certificate" });
      await api(`/api/me/certifications/${cert.cert_id}/proof`, { method: "POST", body: { filename: file.name, data_base64 } });
      onDone?.();
    } catch (ex) { setErr(ex.message); } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  };
  return (
    <>
      <input ref={input} type="file" hidden accept={PROOF_ACCEPT} onChange={(e) => pick(e.target.files?.[0])} />
      <button className="btn btn-link" disabled={busy} onClick={() => input.current?.click()}
              title="Upload the certificate as a PDF or image (max 5 MB)">{busy ? "Uploading…" : label}</button>
      {err && <div className="error small">{err}</div>}
    </>
  );
}

function AddCertification({ onAdded, prefill }) {
  const blank = { name: "", issuer: "", skills: [], issued_on: "", valid_till: "", credential_id: "" };
  const [f, setF] = useState(blank);
  const [proof, setProof] = useState(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [msg, setMsg] = useState(null);
  const form = useRef(null);
  useEffect(() => {                                           // "Add with proof" from the resume card
    if (!prefill) return;
    setF({ ...blank, name: prefill.name, issuer: prefill.issuer, skills: prefill.skills || [] });
    setOpen(true); setMsg(null); setErr(null);
    setTimeout(() => form.current?.scrollIntoView({ behavior: "smooth", block: "center" }), 50);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefill]);
  if (!open) return (
    <>
      {msg && <div className="ok small">{msg}</div>}
      <button className="btn btn-ghost" onClick={() => { setOpen(true); setMsg(null); }}>+ Add certification</button>
    </>);
  const submit = async (e) => {
    e.preventDefault();
    setErr(null); setBusy(true);
    try {
      const proof_base64 = await readFileBase64(proof, { exts: PROOF_EXTS, label: "PDF or image of the certificate" });
      await api("/api/me/certifications", { method: "POST", body: {
        ...f, valid_till: f.valid_till || null, credential_id: f.credential_id || null, proof_filename: proof.name, proof_base64 } });
      setF(blank); setProof(null); setOpen(false); setMsg("Submitted with proof — HR will verify it.");
      onAdded?.();
    } catch (ex) { setErr(ex.message); } finally { setBusy(false); }
  };
  return (
    <form ref={form} className="inline-form" onSubmit={submit}>
      <h4>Add certification</h4>
      <div className="form-grid">
        <label>Name<input required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="SnowPro Core Certification" /></label>
        <label>Issuer<input required value={f.issuer} onChange={(e) => setF({ ...f, issuer: e.target.value })} placeholder="Snowflake" /></label>
        <label className="span2">Skills it proves<SkillPicker value={f.skills} onChange={(v) => setF({ ...f, skills: v })} /></label>
        <label>Issued on<input required type="date" value={f.issued_on} onChange={(e) => setF({ ...f, issued_on: e.target.value })} /></label>
        <label>Valid till<input type="date" value={f.valid_till} onChange={(e) => setF({ ...f, valid_till: e.target.value })} /></label>
        <label className="span2">Credential ID<input value={f.credential_id} onChange={(e) => setF({ ...f, credential_id: e.target.value })} /></label>
        <label className="span2">Proof (certificate PDF or image, max 5 MB)
          <input required type="file" accept={PROOF_ACCEPT} onChange={(e) => setProof(e.target.files?.[0] || null)} />
          <span className="muted small">HR compares it with the details above — make sure your name and the credential ID are visible.</span>
        </label>
      </div>
      {err && <div className="error small">{err}</div>}
      <div className="form-row">
        <button className="btn btn-primary" disabled={busy || !f.skills.length || !proof}>{busy ? "Uploading…" : "Submit for verification"}</button>
        <button type="button" className="btn btn-ghost" onClick={() => setOpen(false)}>Cancel</button>
      </div>
    </form>
  );
}
