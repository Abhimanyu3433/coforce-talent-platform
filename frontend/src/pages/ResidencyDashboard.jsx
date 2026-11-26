import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import {
  Badge, BarList, Card, ErrorBox, Kpi, Loading, Pagination, Select, StatusBadge,
  fmtDate, fmtMonths, fmtNum, useApi,
} from "../components/ui";

const BAND_TONE = { "<12m": "g1", "12-18m": "g2", "18-24m": "g3", "24-36m": "g4", "36m+": "g5", "On bench": "gb" };
const TABS = [
  { key: "rotation", label: "Rotation due (> 24 m)", params: { rotation_due: true, sort: "residency" } },
  { key: "rolloff", label: "Rolling off soon", params: { status: "allocated", sort: "rolloff" } },
  { key: "bench", label: "Bench (oldest first)", params: { status: "bench", sort: "bench_aging" } },
];

export default function ResidencyDashboard() {
  const { user } = useAuth();
  const [f, setF] = useState({ ibu_id: undefined, account_id: undefined, role_family: undefined });
  const [tab, setTab] = useState("rotation");
  const [page, setPage] = useState(1);
  const [refreshing, setRefreshing] = useState(false);
  const meta = useApi("/api/meta");
  const sum = useApi("/api/residency/summary", f);
  const heat = useApi("/api/residency/heatmap", { ibu_id: f.ibu_id });
  const t = TABS.find((x) => x.key === tab);
  const list = useApi("/api/residency/employees", { ...f, ...t.params, page, page_size: 15 });

  const set = (k, v) => { setF((s) => ({ ...s, [k]: v, ...(k === "ibu_id" ? { account_id: undefined } : {}) })); setPage(1); };
  const refresh = async () => {
    setRefreshing(true);
    try { await api("/api/residency/refresh", { method: "POST" }); sum.reload(); heat.reload(); list.reload(); }
    finally { setRefreshing(false); }
  };

  const m = meta.data;
  const s = sum.data;
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Residency dashboard</h1>
          <p className="muted">Time on current account vs the 24-month rotation rule · as of {s ? fmtDate(s.as_of) : "…"}</p>
        </div>
        <div className="filters-inline">
          {m && <>
            <Select value={f.ibu_id} onChange={(v) => set("ibu_id", v)} placeholder="All IBUs"
                    options={m.ibus.map((i) => ({ value: i.ibu_id, label: i.name }))} />
            <Select value={f.account_id} onChange={(v) => set("account_id", v)} placeholder="All accounts"
                    options={m.accounts.filter((a) => !f.ibu_id || a.ibu_id === f.ibu_id).map((a) => ({ value: a.account_id, label: a.name }))} />
            <Select value={f.role_family} onChange={(v) => set("role_family", v)} placeholder="All role families"
                    options={m.role_families.map((r) => ({ value: r.role_family, label: r.role_family_name }))} />
          </>}
          {["manager", "hr"].includes(user.app_role) &&
            <button className="btn btn-ghost" onClick={refresh} disabled={refreshing}>{refreshing ? "Refreshing…" : "Recompute"}</button>}
        </div>
      </div>

      <ErrorBox error={sum.error} />
      {s && (
        <div className="kpis">
          <Kpi label="Headcount" value={fmtNum(s.headcount)} sub={`${fmtNum(s.delivery)} delivery`} />
          <Kpi label="Allocated" value={fmtNum(s.allocated)} sub={`avg ${fmtMonths(s.avg_account_residency_months)} on account`} />
          <Kpi label="Bench + training" value={`${s.bench_pct}%`} sub={`${fmtNum(s.bench)} bench · ${fmtNum(s.training)} training`} tone="amber" />
          <Kpi label="Past 24-month residency" value={fmtNum(s.rotation_due)} sub="rotation candidates" tone="red" />
          <Kpi label="Rolling off ≤ 30 / 60 days" value={`${fmtNum(s.rolling_off_30)} / ${fmtNum(s.rolling_off_60)}`} sub="becoming available" tone="blue" />
          <Kpi label="Avg bench aging" value={`${s.avg_bench_aging_days} d`} sub={`${fmtNum(s.notice)} on notice`} />
        </div>
      )}

      {s && (
        <div className="grid-3">
          <Card title="Account residency bands">
            <BarList data={s.residency_bands} labelKey="band" valueKey="employees" tone={(d) => BAND_TONE[d.band]} />
          </Card>
          <Card title="Bench aging">
            <BarList data={s.bench_aging} labelKey="bucket" valueKey="employees" tone={() => "gb"} />
          </Card>
          <Card title="Accounts with most rotation-due people">
            <BarList data={s.top_rotation_accounts.map((a) => ({ ...a, label: a.account_name }))} valueKey="rotation_due"
                     tone={() => "g5"} onClick={(a) => set("account_id", a.account_id)} />
          </Card>
        </div>
      )}

      {s && !f.ibu_id && (
        <Card title="By IBU">
          <table className="table">
            <thead><tr><th>IBU</th><th className="num">Headcount</th><th className="num">Allocated</th><th className="num">Bench</th>
              <th className="num">Bench %</th><th className="num">Rotation due</th><th className="num">Avg account residency</th></tr></thead>
            <tbody>
              {s.by_ibu.map((i) => (
                <tr key={i.ibu_id} className="clickable" onClick={() => set("ibu_id", i.ibu_id)}>
                  <td>{i.ibu_name}</td><td className="num">{fmtNum(i.headcount)}</td><td className="num">{fmtNum(i.allocated)}</td>
                  <td className="num">{fmtNum(i.bench)}</td><td className="num">{i.bench_pct}%</td>
                  <td className="num">{fmtNum(i.rotation_due)}</td><td className="num">{fmtMonths(i.avg_account_residency_months)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      <Card title={`Account residency heatmap${f.ibu_id ? "" : " (top 20 accounts by headcount)"}`}>
        {heat.loading ? <Loading /> : heat.data && (
          <table className="table heat">
            <thead><tr><th>Account</th><th>IBU</th><th className="num">People</th>
              {Object.keys(BAND_TONE).slice(0, 5).map((b) => <th key={b} className="num">{b}</th>)}
              <th className="num">Rotation due</th><th className="num">Avg months</th></tr></thead>
            <tbody>
              {heat.data.slice(0, f.ibu_id ? 100 : 20).map((a) => (
                <tr key={a.account_id} className={`clickable ${f.account_id === a.account_id ? "selected" : ""}`}
                    onClick={() => set("account_id", a.account_id)}>
                  <td>{a.account_name}</td><td>{a.ibu_id}</td><td className="num">{a.headcount}</td>
                  {Object.entries(a.bands).map(([b, n]) => (
                    <td key={b} className="num heat-cell" style={{ "--a": a.headcount ? n / a.headcount : 0 }}
                        data-band={b}>{n || ""}</td>
                  ))}
                  <td className="num"><b>{a.rotation_due}</b> <span className="muted small">({a.rotation_due_pct}%)</span></td>
                  <td className="num">{a.avg_months}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title="People" actions={
        <div className="tabs">
          {TABS.map((x) => (
            <button key={x.key} className={`tab ${tab === x.key ? "active" : ""}`} onClick={() => { setTab(x.key); setPage(1); }}>{x.label}</button>
          ))}
        </div>}>
        <ErrorBox error={list.error} />
        {list.loading ? <Loading /> : list.data && (
          <>
            <table className="table">
              <thead><tr><th>Employee</th><th>Role</th><th>Account / project</th>
                {tab === "bench"
                  ? <><th className="num">Bench aging</th><th>Location</th></>
                  : <><th className="num">On account</th><th className="num">On project</th><th>Planned roll-off</th><th>Release</th></>}
              </tr></thead>
              <tbody>
                {list.data.items.map((e) => (
                  <tr key={e.emp_id}>
                    <td><Link to={`/employees/${e.emp_id}`}>{e.name}</Link><div className="muted small">{e.emp_id} · {e.grade}</div></td>
                    <td>{e.designation}<div className="muted small">{e.role_family_name}</div></td>
                    <td>{e.account_name || <StatusBadge status={e.status} />}<div className="muted small">{e.project_name}</div></td>
                    {tab === "bench" ? (
                      <><td className="num">{e.bench_aging_days} d</td><td>{e.base_location}</td></>
                    ) : (
                      <>
                        <td className="num">{fmtMonths(e.account_residency_months)}
                          {e.rotation_due ? <div><Badge tone="red">+{e.months_over_threshold} m over</Badge></div> : null}</td>
                        <td className="num">{fmtMonths(e.project_residency_months)}</td>
                        <td>{fmtDate(e.planned_rolloff)}<div className="muted small">{e.days_to_rolloff} days</div></td>
                        <td>{e.release_requires_approval ? <Badge tone="amber" title="Profile visible; PM/manager must approve release">Approval needed</Badge> : "—"}</td>
                      </>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
            <Pagination page={page} pageSize={list.data.page_size} total={list.data.total} onPage={setPage} />
          </>
        )}
      </Card>
    </div>
  );
}
