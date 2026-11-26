import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";
import SkillPicker from "../components/SkillPicker";
import { Badge, Card, ErrorBox, Select, useApi, useDebounced } from "../components/ui";

const GRADES = ["A1", "A2", "A3", "B1", "B2", "C1", "C2", "D1", "D2"];
const SHIFTS = ["General (IST)", "UK Overlap", "US Overlap", "24x7 Rotational"];
const VERDICT = {
  healthy: ["green", "Enough internal people fully match in time"],
  trainable: ["amber", "Enough people can be ready in time with short training"],
  scarce: ["red", "Internal supply is short — consider relaxing a skill or hiring externally"],
};

const plusDays = (iso, n) => { const d = new Date(iso); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };

export default function DemandForm() {
  const nav = useNavigate();
  const meta = useApi("/api/meta");
  const asOf = meta.data?.as_of || "2026-11-25";
  const [f, setF] = useState({
    role_title: "", role_family: "", positions: 1, grade_min: "A3", grade_max: "B1", exp_min: 3, exp_max: 8,
    ibu_id: "", account_id: "", project_id: "", locations: [], work_mode: "Hybrid", remote_allowed: false,
    needed_by: "", duration_months: 12, shift: "General (IST)", client_interview: true, priority: "Medium",
    overprovision_factor: 1.5, mandatory: [{ skill: "", min_proficiency: 3 }], nice: [], jd_text: "",
  });
  const set = (k, v) => setF((s) => ({ ...s, [k]: v }));
  const projects = useApi(f.account_id ? "/api/projects" : null, { account_id: f.account_id });
  const [supply, setSupply] = useState(null);
  const [supplyErr, setSupplyErr] = useState(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);

  const [params] = useSearchParams();
  const chatId = params.get("chat");
  const [fromChat, setFromChat] = useState(null);
  useEffect(() => {          // "Open in form" from the demand assistant: prefill from the chat draft
    if (!chatId) return;
    api(`/api/chat/demand/sessions/${chatId}`).then((v) => {
      const d = v.draft;
      setFromChat(chatId);
      setF((s) => ({
        ...s,
        role_title: d.role_title || "", role_family: d.role_family || "", positions: d.positions || 1,
        grade_min: d.grade_min || s.grade_min, grade_max: d.grade_max || s.grade_max,
        exp_min: d.exp_min ?? s.exp_min, exp_max: d.exp_max ?? s.exp_max,
        ibu_id: d.ibu_id || "", account_id: d.account_id || "", project_id: d.project_id || "",
        locations: d.locations || [], work_mode: d.work_mode || "Hybrid", remote_allowed: !!d.remote_allowed,
        needed_by: d.needed_by || s.needed_by, duration_months: d.duration_months || 12, shift: d.shift || s.shift,
        client_interview: d.client_interview ?? s.client_interview, priority: d.priority || "Medium",
        overprovision_factor: d.overprovision_factor || 1.0,
        mandatory: d.mandatory.length ? d.mandatory.map((m) => ({ skill: m.name, min_proficiency: m.min_proficiency || 3 })) : s.mandatory,
        nice: d.nice.map((n) => n.name),
      }));
    }).catch((e) => setError(e));
  }, [chatId]);

  useEffect(() => { if (!f.needed_by && meta.data) set("needed_by", plusDays(asOf, 30)); }, [meta.data]); // eslint-disable-line

  const payload = useMemo(() => ({
    role_title: f.role_title || "Draft role", role_family: f.role_family || null, positions: Number(f.positions),
    grade_min: f.grade_min, grade_max: f.grade_max, exp_min: Number(f.exp_min), exp_max: Number(f.exp_max),
    ibu_id: f.ibu_id || null, account_id: f.account_id || null, project_id: f.project_id || null,
    locations: f.locations, work_mode: f.work_mode, remote_allowed: f.remote_allowed, needed_by: f.needed_by,
    duration_months: Number(f.duration_months) || null, shift: f.shift, client_interview: f.client_interview,
    priority: f.priority, overprovision_factor: Number(f.overprovision_factor),
    mandatory_skills: f.mandatory.filter((m) => m.skill.trim()).map((m) => ({ skill: m.skill, min_proficiency: Number(m.min_proficiency) })),
    nice_skills: f.nice.map((s) => ({ skill: s })), jd_text: f.jd_text || null,
  }), [f]);
  const ready = payload.mandatory_skills.length > 0 && payload.needed_by && (payload.ibu_id || payload.account_id || payload.project_id)
    && (payload.locations.length > 0 || payload.remote_allowed);
  const dPayload = useDebounced(JSON.stringify(payload), 600);

  useEffect(() => {
    if (!ready) { setSupply(null); return; }
    let live = true;
    setChecking(true);
    api("/api/demands/supply-check", { method: "POST", body: JSON.parse(dPayload) })
      .then((r) => { if (live) { setSupply(r); setSupplyErr(null); if (!f.role_family && r.role_family) set("role_family", r.role_family); } })
      .catch((e) => live && setSupplyErr(e))
      .finally(() => live && setChecking(false));
    return () => { live = false; };
  }, [dPayload, ready]); // eslint-disable-line

  const pickProject = (pid) => {
    const p = projects.data?.find((x) => x.project_id === pid);
    setF((s) => ({ ...s, project_id: pid || "", ...(p ? {
      locations: p.delivery_locations.split(";"), work_mode: p.work_mode, shift: p.shift, ibu_id: p.ibu_id,
    } : {}) }));
  };
  const relax = (name) => setF((s) => ({
    ...s, mandatory: s.mandatory.filter((m) => m.skill.toLowerCase() !== name.toLowerCase()),
    nice: s.nice.includes(name) ? s.nice : [...s.nice, name],
  }));

  const submit = async (e) => {
    e.preventDefault();
    setSaving(true); setError(null);
    try {
      const d = await api("/api/demands", { method: "POST", body: { ...payload, role_title: f.role_title, source: fromChat ? "chatbot+form" : "form" } });
      nav(`/demands/${d.demand_id}`);
    } catch (ex) {
      const c = ex.detail?.candidates;
      setError(new Error(c?.length ? `${ex.message}. Did you mean: ${c.join(", ")}?` : ex.message));
    } finally { setSaving(false); }
  };

  const m = meta.data;
  const needed = Math.ceil((Number(f.positions) || 1) * Number(f.overprovision_factor));
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Raise a demand</h1>
          <p className="muted">Structured form — or let the <a href="/demands/assistant" onClick={(e) => { e.preventDefault(); nav("/demands/assistant"); }}>demand assistant</a> ask you the questions</p>
        </div>
      </div>
      {fromChat && <div className="callout callout-blue">Prefilled from your chat — review and adjust anything, then create.</div>}
      <form className="demand-layout" onSubmit={submit}>
        <div className="demand-form">
          <Card title="1 · Role">
            <div className="form-grid">
              <label className="span2">Role title<input required value={f.role_title} onChange={(e) => set("role_title", e.target.value)} placeholder="e.g. Senior Data Engineer" /></label>
              <label>Positions<input type="number" min="1" max="200" required value={f.positions} onChange={(e) => set("positions", e.target.value)} /></label>
              <label>Role family
                {m && <Select value={f.role_family} onChange={(v) => set("role_family", v || "")} placeholder="Auto-detect from skills"
                              options={m.role_families.map((r) => ({ value: r.role_family, label: r.role_family_name }))} />}</label>
              <label>Grade from<select value={f.grade_min} onChange={(e) => set("grade_min", e.target.value)}>{GRADES.map((g) => <option key={g}>{g}</option>)}</select></label>
              <label>Grade to<select value={f.grade_max} onChange={(e) => set("grade_max", e.target.value)}>{GRADES.map((g) => <option key={g}>{g}</option>)}</select></label>
              <label>Experience from (yrs)<input type="number" min="0" step="0.5" value={f.exp_min} onChange={(e) => set("exp_min", e.target.value)} /></label>
              <label>to (yrs)<input type="number" min="0" step="0.5" value={f.exp_max} onChange={(e) => set("exp_max", e.target.value)} /></label>
            </div>
          </Card>

          <Card title="2 · Skills">
            <h4>Mandatory</h4>
            {f.mandatory.map((row, i) => (
              <div key={i} className="form-row">
                <div className="grow"><SkillPicker multi={false} value={row.skill}
                  onChange={(v) => set("mandatory", f.mandatory.map((x, j) => (j === i ? { ...x, skill: v } : x)))} placeholder="Skill or tool" /></div>
                <select value={row.min_proficiency} onChange={(e) => set("mandatory", f.mandatory.map((x, j) => (j === i ? { ...x, min_proficiency: e.target.value } : x)))}>
                  {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>Level {n}+</option>)}
                </select>
                <button type="button" className="btn btn-ghost" onClick={() => set("mandatory", f.mandatory.filter((_, j) => j !== i))}>×</button>
              </div>
            ))}
            <button type="button" className="btn btn-link" onClick={() => set("mandatory", [...f.mandatory, { skill: "", min_proficiency: 3 }])}>+ Add mandatory skill</button>
            {f.mandatory.length > 6 && <div className="callout">More than 6 mandatory skills usually shrinks internal supply sharply.</div>}
            <h4>Good to have</h4>
            <SkillPicker value={f.nice} onChange={(v) => set("nice", v)} />
          </Card>

          <Card title="3 · Engagement & location">
            <div className="form-grid">
              {m && <label>IBU<Select value={f.ibu_id} onChange={(v) => setF((s) => ({ ...s, ibu_id: v || "", account_id: "", project_id: "" }))}
                                       placeholder="Select IBU" options={m.ibus.map((i) => ({ value: i.ibu_id, label: i.name }))} /></label>}
              {m && <label>Account<Select value={f.account_id} onChange={(v) => setF((s) => ({ ...s, account_id: v || "", project_id: "" }))}
                                          placeholder="Select account" options={m.accounts.filter((a) => !f.ibu_id || a.ibu_id === f.ibu_id).map((a) => ({ value: a.account_id, label: a.name }))} /></label>}
              <label className="span2">Project (optional — fills location, work mode, shift)
                <Select value={f.project_id} onChange={pickProject} placeholder={f.account_id ? "Select project" : "Pick an account first"}
                        options={(projects.data || []).map((p) => ({ value: p.project_id, label: `${p.name} · ${p.project_type}` }))} disabled={!f.account_id} /></label>
              <label className="span2">Locations
                <div className="chips">{f.locations.map((l) => (
                  <span key={l} className="chip chip-hl">{l} <button type="button" onClick={() => set("locations", f.locations.filter((x) => x !== l))}>×</button></span>))}</div>
                {m && <Select value="" onChange={(v) => v && !f.locations.includes(v) && set("locations", [...f.locations, v])} placeholder="+ Add location"
                              options={m.locations.map((l) => ({ value: l, label: l }))} />}</label>
              <label>Work mode<select value={f.work_mode} onChange={(e) => set("work_mode", e.target.value)}>
                {["Hybrid", "Work from Office", "Remote"].map((w) => <option key={w}>{w}</option>)}</select></label>
              <label className="check" style={{ alignSelf: "end" }}><input type="checkbox" checked={f.remote_allowed || f.work_mode === "Remote"}
                disabled={f.work_mode === "Remote"} onChange={(e) => set("remote_allowed", e.target.checked)} /> Remote working allowed</label>
            </div>
          </Card>

          <Card title="4 · Timing & process">
            <div className="form-grid">
              <label>Needed by<input type="date" required min={asOf} value={f.needed_by} onChange={(e) => set("needed_by", e.target.value)} /></label>
              <label>Duration (months)<input type="number" min="1" max="60" value={f.duration_months} onChange={(e) => set("duration_months", e.target.value)} /></label>
              <label>Shift<select value={f.shift} onChange={(e) => set("shift", e.target.value)}>{SHIFTS.map((s) => <option key={s}>{s}</option>)}</select></label>
              <label>Priority<select value={f.priority} onChange={(e) => set("priority", e.target.value)}>{["High", "Medium", "Low"].map((p) => <option key={p}>{p}</option>)}</select></label>
              <label className="check"><input type="checkbox" checked={f.client_interview} onChange={(e) => set("client_interview", e.target.checked)} /> Client interview required</label>
              <label>Over-provisioning factor: <b>×{Number(f.overprovision_factor).toFixed(2)}</b> → shortlist/train {needed}
                <input type="range" min="1" max="3" step="0.25" value={f.overprovision_factor} onChange={(e) => set("overprovision_factor", e.target.value)} />
                <span className="small muted">Set by you; HR approves it when the training plan is created (Stage 5).</span></label>
              <label className="span2">Job description (optional — generated from the fields if left empty)
                <textarea rows="3" value={f.jd_text} onChange={(e) => set("jd_text", e.target.value)} /></label>
            </div>
          </Card>
          <ErrorBox error={error} />
          <div className="form-row">
            <button className="btn btn-primary" disabled={saving || !ready || !f.role_title}>{saving ? "Creating & matching…" : "Create demand & find matches"}</button>
            <button type="button" className="btn btn-ghost" onClick={() => nav("/demands")}>Cancel</button>
          </div>
        </div>

        <aside className="supply">
          <Card title={<>Internal supply {checking && <span className="muted small">checking…</span>}</>}>
            {!ready && <p className="muted small">Add a mandatory skill, an IBU/account, a location (or remote) and a date to see how many internal people fit.</p>}
            <ErrorBox error={supplyErr} />
            {supply && ready && (
              <>
                <div className={`callout ${{ green: "callout-green", amber: "", red: "callout-red" }[VERDICT[supply.verdict][0]]}`}>
                  <b>{supply.verdict.toUpperCase()}</b> — {VERDICT[supply.verdict][1]}</div>
                <div className="supply-grid">
                  <div><b>{supply.fully_matching_in_time}</b><span>fully match, available in time</span></div>
                  <div><b>{supply.ready_in_time_with_training}</b><span>ready in time incl. training</span></div>
                  <div><b>{supply.eligible}</b><span>eligible (≥50% skills)</span></div>
                  <div><b>{supply.needed}</b><span>needed (positions × factor)</span></div>
                </div>
                <h4>Eligible by source</h4>
                <div className="small">
                  {Object.entries(supply.by_pool).filter(([, n]) => n).map(([p, n]) => <span key={p} className="chip">{p.replace("_", " ")} <b>{n}</b></span>)}
                </div>
                {supply.relaxations.filter((r) => r.gain > 0).length > 0 && (<>
                  <h4>Bottleneck skills</h4>
                  {supply.relaxations.filter((r) => r.gain > 0).slice(0, 4).map((r) => (
                    <div key={r.skill_id} className="relax">
                      <span>Make <b>{r.name}</b> good-to-have → <b>{r.fully_matching_if_optional}</b> full matches (+{r.gain})</span>
                      <button type="button" className="btn btn-link" onClick={() => relax(r.name)}>Apply</button>
                    </div>
                  ))}
                </>)}
                {supply.top.length > 0 && (<>
                  <h4>Top matches preview</h4>
                  {supply.top.map((t) => <div key={t.emp_id} className="small">{t.name} · <b>{t.score}</b> <Badge tone="grey">{t.fit_label}</Badge> <span className="muted">{t.availability_label}</span></div>)}
                </>)}
                {f.role_family || supply.role_family ? <p className="muted small">Role family: {m?.role_families.find((r) => r.role_family === (f.role_family || supply.role_family))?.role_family_name}</p> : null}
              </>
            )}
          </Card>
        </aside>
      </form>
    </div>
  );
}
