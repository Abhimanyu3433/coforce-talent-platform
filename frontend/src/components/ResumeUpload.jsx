import { useRef, useState } from "react";
import { api, readFileBase64 } from "../api";
import { Badge, Card, ErrorBox } from "./ui";

const STATUS = {
  new: ["New", "green"],
  update: ["Update", "blue"],
  on_profile: ["On profile", "grey"],
};
const FROM = { resume: "level stated in resume", years: "estimated from years", estimate: "estimate — adjust if needed" };

// Upload a resume → the skills it mentions, compared with the profile. Nothing is saved until "Add to my profile".
export default function ResumeUpload({ onApplied, onAddCert }) {
  const input = useRef(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [res, setRes] = useState(null);
  const [rows, setRows] = useState([]);
  const [done, setDone] = useState(null);

  const upload = async (file) => {
    if (!file) return;
    setBusy(true); setError(null); setDone(null);
    try {
      const data_base64 = await readFileBase64(file, { exts: [".pdf", ".docx"], label: "PDF or Word (.docx) resume" });
      const r = await api("/api/me/resume/parse", { method: "POST", body: { filename: file.name, data_base64 } });
      setRes(r);
      setRows(r.skills.map((s) => ({ ...s, pick: !s.locked && s.status !== "on_profile", level: s.suggested_level,
                                      years: s.years_used ?? "" })));
    } catch (e) { setError(e); } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  };

  const set = (i, patch) => setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const picked = rows.filter((r) => r.pick);

  const apply = async () => {
    setBusy(true); setError(null);
    try {
      const out = await api("/api/me/resume/apply", { method: "POST", body: { items: picked.map((r) => ({
        skill_id: r.skill_id, proficiency: Number(r.level), years_used: r.years === "" ? null : Number(r.years) })) } });
      setDone(out); setRes(null); setRows([]);
      onApplied?.();
    } catch (e) { setError(e); } finally { setBusy(false); }
  };

  return (
    <Card title="Update profile from resume" actions={
      <>
        <input ref={input} type="file" hidden accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
               onChange={(e) => upload(e.target.files?.[0])} />
        <button className="btn btn-ghost" disabled={busy} onClick={() => input.current?.click()}>
          {busy && !res ? "Reading…" : res ? "Upload another" : "Upload resume (PDF / Word)"}</button>
      </>}>
      {!res && !done && (
        <p className="muted small">Upload your resume and the skills and tools it mentions are matched to the company skill list.
          You review the suggested levels and choose what goes on your profile. Added skills stay unverified until a
          certificate or project backs them.</p>)}
      <ErrorBox error={error} />
      {done && (
        <div className="callout callout-green">
          {done.added.length > 0 && <>Added {done.added.join(", ")}. </>}
          {done.updated.length > 0 && <>Updated {done.updated.join(", ")}. </>}
          {done.skipped.length > 0 && <>Skipped {done.skipped.map((s) => s.name).join(", ")} (already on your profile from project or training history).</>}
        </div>)}
      {res && (
        <>
          <div className="resume-meta small">
            <span className="pdf-icon">{res.file.kind === "docx" ? "DOC" : "PDF"}</span>
            <b>{res.file.name}</b>
            <span className="muted">{res.file.pages ? `${res.file.pages} page${res.file.pages > 1 ? "s" : ""} · ` : ""}
              {res.skills.length} skills found · {res.counts.new} new · {res.counts.update} to update</span>
          </div>
          {!res.name_found && <div className="callout">Your name wasn't found in this file — make sure it is your own resume.</div>}
          {res.resume_exp_years != null && Math.abs(res.resume_exp_years - res.profile_exp_years) >= 1 && (
            <div className="callout callout-blue">The resume says {res.resume_exp_years} years of experience; your profile
              shows {res.profile_exp_years}. Profile experience is maintained by HR — raise it with them if it's out of date.</div>)}
          {rows.length === 0 ? <div className="muted">No skills from the company skill list were found in this resume.</div> : (
            <table className="table compact">
              <thead><tr><th /><th>Skill</th><th>Status</th><th>Level</th><th className="num">Years</th><th>Found in resume</th></tr></thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={r.skill_id} className={r.locked ? "muted" : ""}>
                    <td><input type="checkbox" checked={r.pick} disabled={r.locked} onChange={(e) => set(i, { pick: e.target.checked })}
                               title={r.locked ? `Already on your profile from ${r.current_source.replace("_", " ")}` : "Add to my profile"} /></td>
                    <td>{r.name}<div className="muted small">{r.category}</div></td>
                    <td><Badge tone={STATUS[r.status][1]}>{STATUS[r.status][0]}</Badge>
                      {r.current_level && <div className="muted small">now level {r.current_level}{r.locked ? ` · ${r.current_source.replace("_", " ")}` : ""}</div>}</td>
                    <td><select value={r.level} disabled={r.locked} onChange={(e) => set(i, { level: e.target.value })} title={FROM[r.level_from]}>
                      {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>Level {n}</option>)}</select>
                      <div className="muted small">{FROM[r.level_from]}</div></td>
                    <td className="num"><input type="number" min="0" max="40" step="0.5" style={{ width: 70 }} value={r.years} disabled={r.locked}
                                               onChange={(e) => set(i, { years: e.target.value })} /></td>
                    <td className="small evidence">{r.evidence}</td>
                  </tr>))}
              </tbody>
            </table>)}
          {res.certifications.length > 0 && (
            <div className="resume-certs">
              <h4>Certifications mentioned</h4>
              {res.certifications.map((c) => (
                <div key={c.name} className="res-line">
                  <span>{c.name} <span className="muted small">· {c.issuer}</span></span>
                  {c.on_profile ? <Badge tone="grey">already added</Badge> :
                    <button className="btn btn-link" onClick={() => onAddCert?.(c)}>Add with proof</button>}
                </div>))}
            </div>)}
          <div className="form-row">
            <button className="btn btn-primary" disabled={busy || picked.length === 0} onClick={apply}>
              {busy ? "Saving…" : `Add ${picked.length} skill${picked.length === 1 ? "" : "s"} to my profile`}</button>
            <button className="btn btn-ghost" disabled={busy} onClick={() => { setRes(null); setRows([]); }}>Discard</button>
          </div>
        </>)}
    </Card>
  );
}
