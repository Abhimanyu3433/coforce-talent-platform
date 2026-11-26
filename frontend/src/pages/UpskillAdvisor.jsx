import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { Badge, Card, ErrorBox, Loading, Pagination, Select, fmtNum, useApi } from "../components/ui";

export default function UpskillAdvisor() {
  const nav = useNavigate();
  const { user } = useAuth();
  const [f, setF] = useState({ ibu_id: undefined, role_family: undefined, min_aging: undefined });
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(false);
  const meta = useApi("/api/meta");
  const res = useApi("/api/upskilling/bench", { ...f, page, page_size: 20 });
  const set = (k, v) => { setF((s) => ({ ...s, [k]: v })); setPage(1); };
  const refresh = async () => { setBusy(true); try { await api("/api/upskilling/refresh", { method: "POST" }); res.reload(); } finally { setBusy(false); } };
  const d = res.data;

  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Upskilling advisor</h1>
          <p className="muted">What each person on the bench should learn next to get onto projects — from their skills, their role and open demand</p></div>
        <div className="filters-inline">
          {meta.data && <>
            <Select value={f.ibu_id} onChange={(v) => set("ibu_id", v)} placeholder="All IBUs" options={meta.data.ibus.map((i) => ({ value: i.ibu_id, label: i.name }))} />
            <Select value={f.role_family} onChange={(v) => set("role_family", v)} placeholder="All role families"
                    options={meta.data.role_families.map((r) => ({ value: r.role_family, label: r.role_family_name }))} />
          </>}
          <select value={f.min_aging || ""} onChange={(e) => set("min_aging", e.target.value || undefined)}>
            <option value="">Any bench age</option><option value="30">30+ days</option><option value="60">60+ days</option><option value="90">90+ days</option>
          </select>
          {["manager", "hr"].includes(user.app_role) && <button className="btn btn-ghost" onClick={refresh} disabled={busy}>{busy ? "Recomputing…" : "Recompute"}</button>}
        </div>
      </div>
      <ErrorBox error={res.error} />
      {res.loading && !d ? <Loading /> : d && (<>
        <Card title="Most recommended skills across this bench" actions={<span className="muted small">computed {d.computed_on?.replace("T", " ").slice(0, 16)} UTC</span>}>
          <table className="table">
            <thead><tr><th>Skill</th><th className="num">People</th><th className="num">Avg score</th><th className="num">Full matches unlocked</th>
              <th className="num">Eligibility unlocked</th><th className="num">Avg days</th><th /></tr></thead>
            <tbody>
              {d.top_skills.map((s) => (
                <tr key={s.skill_id}>
                  <td><b>{s.skill}</b></td><td className="num">{s.people}</td><td className="num">{s.avg_score}</td>
                  <td className="num">{s.unlock_full}</td><td className="num">{s.unlock_eligible}</td><td className="num">{s.avg_days}</td>
                  <td><button className="btn btn-ghost btn-sm" onClick={() => nav(`/training/new?skill=${s.skill_id}`)}>Plan batch</button></td>
                </tr>))}
            </tbody>
          </table>
        </Card>
        <Card title={`${fmtNum(d.total)} people on bench / in training (longest on bench first)`}>
          <table className="table">
            <thead><tr><th>Employee</th><th className="num">Bench</th><th>Top recommendations</th></tr></thead>
            <tbody>
              {d.people.map((p) => (
                <tr key={p.emp_id}>
                  <td><Link to={`/employees/${p.emp_id}`}>{p.name}</Link><div className="muted small">{p.designation} · {p.grade} · {p.ibu_id} · {p.base_location}</div></td>
                  <td className="num">{p.bench_aging_days} d<div><Badge tone={p.status === "training" ? "blue" : "amber"}>{p.status}</Badge></div></td>
                  <td>{p.recommendations.length === 0 ? <span className="muted small">none</span> : p.recommendations.map((r) => (
                    <span key={r.skill_id} className="chip chip-hl" title={r.reasons.join(" ")}>
                      {r.skill} <b>{r.score}</b>{r.unlocks.full ? <span className="tick"> +{r.unlocks.full}✓</span> : null}{r.already_learning ? " · learning" : ""}
                    </span>))}</td>
                </tr>))}
            </tbody>
          </table>
          <Pagination page={page} pageSize={d.page_size} total={d.total} onPage={setPage} />
        </Card>
      </>)}
    </div>
  );
}
