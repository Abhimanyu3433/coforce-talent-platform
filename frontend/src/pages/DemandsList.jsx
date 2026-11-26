import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Badge, Card, ErrorBox, Loading, Pagination, Select, fmtDate, useApi, useDebounced } from "../components/ui";

const STATUSES = ["Open", "On Hold", "Fulfilled - Internal", "Fulfilled - External", "Cancelled", ""];
const PRIORITY_TONE = { High: "red", Medium: "amber", Low: "grey" };
const AS_OF = "2026-11-25";

export const daysLeft = (d, asOf) => Math.round((new Date(d) - new Date(asOf || AS_OF)) / 86400000);

export default function DemandsList() {
  const nav = useNavigate();
  const [status, setStatus] = useState("Open");
  const [ibu, setIbu] = useState();
  const [mine, setMine] = useState(false);
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const dq = useDebounced(q, 300);
  const meta = useApi("/api/meta");
  const res = useApi("/api/demands", { status, ibu_id: ibu, mine: mine || undefined, q: dq, page, page_size: 20 });
  const asOf = meta.data?.as_of;

  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Demands</h1><p className="muted">Open job requirements and their internal match pipeline</p></div>
        <div className="filters-inline">
          <button className="btn btn-ghost" onClick={() => nav("/demands/new")}>Use the form</button>
          <button className="btn btn-primary" onClick={() => nav("/demands/assistant")}>+ Raise with assistant</button>
        </div>
      </div>
      <Card actions={
        <div className="filters-inline">
          <input placeholder="Search role, ID, account, project" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
          {meta.data && <Select value={ibu} onChange={(v) => { setIbu(v); setPage(1); }} placeholder="All IBUs"
                                options={meta.data.ibus.map((i) => ({ value: i.ibu_id, label: i.name }))} />}
          <label className="check"><input type="checkbox" checked={mine} onChange={(e) => { setMine(e.target.checked); setPage(1); }} /> Raised by me</label>
        </div>}
        title={
          <div className="tabs">
            {STATUSES.map((s) => (
              <button key={s || "all"} className={`tab ${status === s ? "active" : ""}`} onClick={() => { setStatus(s); setPage(1); }}>{s || "All"}</button>
            ))}
          </div>}>
        <ErrorBox error={res.error} />
        {res.loading && !res.data ? <Loading /> : res.data && (
          <>
            <table className="table">
              <thead><tr><th>Demand</th><th>Account / project</th><th className="num">Positions</th><th>Needed by</th>
                <th>Priority</th><th className="num">Internal supply</th><th className="num">Pipeline</th><th>Raised by</th></tr></thead>
              <tbody>
                {res.data.items.map((d) => {
                  const left = daysLeft(d.needed_by, asOf);
                  return (
                    <tr key={d.demand_id} className="clickable" onClick={() => nav(`/demands/${d.demand_id}`)}>
                      <td><Link to={`/demands/${d.demand_id}`} onClick={(e) => e.stopPropagation()}>{d.role_title}</Link>
                        <div className="muted small">{d.demand_id} · {d.grade_band} · {d.work_mode} · {d.locations.join(", ")}</div></td>
                      <td>{d.account_name || "—"}<div className="muted small">{d.project_name} · {d.ibu_id}</div></td>
                      <td className="num">{d.positions}{d.overprovision_factor > 1 && <div className="muted small">×{d.overprovision_factor} → {Math.ceil(d.positions * d.overprovision_factor)}</div>}</td>
                      <td>{fmtDate(d.needed_by)}<div className={`small ${left < 14 ? "text-red" : "muted"}`}>{left >= 0 ? `${left} days left` : `${-left} days overdue`}</div></td>
                      <td><Badge tone={PRIORITY_TONE[d.priority]}>{d.priority}</Badge></td>
                      <td className="num">{d.fully_matching ?? "—"} <span className="muted small">full</span>
                        <div className="muted small">{d.eligible ?? "—"} eligible</div></td>
                      <td className="num">{d.in_pipeline}<div className="muted small">{d.selected}/{d.positions} selected</div></td>
                      <td className="small">{d.raised_by_name}<div className="muted">{fmtDate(d.created_on)}</div></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <Pagination page={page} pageSize={res.data.page_size} total={res.data.total} onPage={setPage} />
          </>
        )}
      </Card>
    </div>
  );
}
