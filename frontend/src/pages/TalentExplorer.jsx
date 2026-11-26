import { useState } from "react";
import { Link } from "react-router-dom";
import SkillPicker from "../components/SkillPicker";
import {
  Badge, Card, ErrorBox, Loading, Pagination, Select, SkillChip, StatusBadge,
  fmtDate, fmtMonths, fmtNum, useApi, useDebounced,
} from "../components/ui";

const EMPTY = {
  q: "", ibu_id: undefined, status: undefined, role_family: undefined, grade: undefined, location: undefined,
  skills: [], min_proficiency: 1, skill_mode: "all", verified_only: false, rotation_due: undefined,
  min_exp: "", max_exp: "", sort: "name",
};

export default function TalentExplorer() {
  const [f, setF] = useState(EMPTY);
  const [page, setPage] = useState(1);
  const meta = useApi("/api/meta");
  const q = useDebounced(f.q, 350);
  const params = { ...f, q, page, page_size: 25, verified_only: f.verified_only || undefined };
  const res = useApi("/api/employees", params);
  const set = (k, v) => { setF((s) => ({ ...s, [k]: v })); setPage(1); };
  const m = meta.data;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Talent explorer</h1>
          <p className="muted">Search every delivery employee by skills, tools, residency and availability</p>
        </div>
        <button className="btn btn-ghost" onClick={() => { setF(EMPTY); setPage(1); }}>Clear filters</button>
      </div>

      <div className="explorer">
        <Card className="filters">
          <label>Search<input value={f.q} onChange={(e) => set("q", e.target.value)} placeholder="Name, emp ID, email, designation" /></label>
          <label>Skills / tools
            <SkillPicker value={f.skills} onChange={(v) => set("skills", v)} />
          </label>
          {f.skills.length > 0 && (
            <div className="row2">
              <label>Min proficiency
                <select value={f.min_proficiency} onChange={(e) => set("min_proficiency", Number(e.target.value))}>
                  {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}+</option>)}
                </select>
              </label>
              <label>Match
                <select value={f.skill_mode} onChange={(e) => set("skill_mode", e.target.value)}>
                  <option value="all">All skills</option><option value="any">Any skill</option>
                </select>
              </label>
            </div>
          )}
          {f.skills.length > 0 && (
            <label className="check"><input type="checkbox" checked={f.verified_only} onChange={(e) => set("verified_only", e.target.checked)} /> Verified skills only</label>
          )}
          {m && <>
            <label>Status<Select value={f.status} onChange={(v) => set("status", v)} options={m.statuses.map((s) => ({ value: s, label: s }))} /></label>
            <label>IBU<Select value={f.ibu_id} onChange={(v) => set("ibu_id", v)} options={m.ibus.map((i) => ({ value: i.ibu_id, label: i.name }))} /></label>
            <label>Role family<Select value={f.role_family} onChange={(v) => set("role_family", v)} options={m.role_families.map((r) => ({ value: r.role_family, label: r.role_family_name }))} /></label>
            <div className="row2">
              <label>Grade<Select value={f.grade} onChange={(v) => set("grade", v)} options={m.grades.map((g) => ({ value: g, label: g }))} /></label>
              <label>Location<Select value={f.location} onChange={(v) => set("location", v)} options={m.locations.map((l) => ({ value: l, label: l }))} /></label>
            </div>
          </>}
          <div className="row2">
            <label>Exp from<input type="number" min="0" value={f.min_exp} onChange={(e) => set("min_exp", e.target.value)} /></label>
            <label>to<input type="number" min="0" value={f.max_exp} onChange={(e) => set("max_exp", e.target.value)} /></label>
          </div>
          <label className="check"><input type="checkbox" checked={f.rotation_due === true}
                                          onChange={(e) => set("rotation_due", e.target.checked ? true : undefined)} /> Past 24-month residency only</label>
          <label>Sort by
            <select value={f.sort} onChange={(e) => set("sort", e.target.value)}>
              <option value="name">Name</option><option value="exp">Experience</option><option value="residency">Account residency</option>
              <option value="availability">Earliest availability</option><option value="bench_aging">Bench aging</option>
            </select>
          </label>
        </Card>

        <Card title={res.data ? `${fmtNum(res.data.total)} employees` : "Employees"}
              actions={res.data?.resolved_skills?.length ? <span className="muted small">Skills: {res.data.resolved_skills.join(", ")}</span> : null}>
          <ErrorBox error={res.error} />
          {res.loading && !res.data ? <Loading /> : res.data && (
            <>
              <table className="table">
                <thead><tr><th>Employee</th><th>Status / account</th><th className="num">Residency</th><th>Available</th><th>Skills</th></tr></thead>
                <tbody>
                  {res.data.items.map((e) => (
                    <tr key={e.emp_id}>
                      <td>
                        <Link to={`/employees/${e.emp_id}`}>{e.name}</Link>
                        <div className="muted small">{e.designation} · {e.grade} · {e.total_exp_years} yrs</div>
                        <div className="muted small">{e.emp_id} · {e.ibu_id} · {e.base_location}</div>
                      </td>
                      <td><StatusBadge status={e.status} />
                        <div className="small">{e.account_name}</div><div className="muted small">{e.project_name}</div></td>
                      <td className="num">{fmtMonths(e.account_residency_months)}
                        {e.rotation_due ? <div><Badge tone="red">&gt; 24 m</Badge></div> : null}
                        {e.bench_aging_days != null && <div className="muted small">bench {e.bench_aging_days} d</div>}</td>
                      <td>{fmtDate(e.availability_date)}
                        {e.release_requires_approval ? <div><Badge tone="amber" title="Allocated: release needs PM/manager approval">Approval needed</Badge></div> : null}</td>
                      <td className="skills-cell">
                        {e.matched_skills.map((s) => <SkillChip key={s.skill_id} s={s} highlight />)}
                        {e.top_skills.slice(0, e.matched_skills.length ? 3 : 6).map((s) => <SkillChip key={s.skill_id} s={s} />)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <Pagination page={page} pageSize={res.data.page_size} total={res.data.total} onPage={setPage} />
            </>
          )}
        </Card>
      </div>
    </div>
  );
}
