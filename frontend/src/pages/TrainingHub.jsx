import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth";
import Gantt from "../components/Gantt";
import { Badge, Card, ErrorBox, Loading, fmtDate, useApi } from "../components/ui";
import { PLAN_TONE } from "./PlanDetail";

const TABS = [["plans", "Plans"], ["calendar", "Batch calendar"], ["forecast", "Future needs"], ["onboarding", "Onboarding"]];

export default function TrainingHub() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "plans";
  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Training planner</h1>
          <p className="muted">Close skill gaps before demands start — plans per demand, batches for future need, and onboarding for new hires</p></div>
        <div className="tabs">{TABS.map(([k, l]) => <button key={k} className={`tab ${tab === k ? "active" : ""}`} onClick={() => setParams({ tab: k })}>{l}</button>)}</div>
      </div>
      {tab === "plans" && <Plans />}
      {tab === "calendar" && <Calendar />}
      {tab === "forecast" && <Forecast />}
      {tab === "onboarding" && <Onboarding />}
    </div>
  );
}

function Plans() {
  const { user } = useAuth();
  const nav = useNavigate();
  const [status, setStatus] = useState(user.app_role === "hr" ? "pending" : "");
  const { data, loading, error } = useApi("/api/training/plans", { status });
  return (
    <Card title={<div className="tabs">{["pending", "approved", "rejected", "cancelled", ""].map((s) => (
      <button key={s || "all"} className={`tab ${status === s ? "active" : ""}`} onClick={() => setStatus(s)}>{s || "all"}</button>))}</div>}
      actions={<span className="muted small">Create a plan from a demand page (“Plan training”) or from Future needs</span>}>
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : data && (
        <table className="table">
          <thead><tr><th>Plan</th><th>For</th><th className="num">Target</th><th className="num">Fit now</th><th className="num">Trainees</th>
            <th>Coverage</th><th>Status</th><th>Created by</th></tr></thead>
          <tbody>
            {data.map((p) => (
              <tr key={p.plan_id} className="clickable" onClick={() => nav(`/training/plans/${p.plan_id}`)}>
                <td><Link to={`/training/plans/${p.plan_id}`} onClick={(e) => e.stopPropagation()}>{p.plan_id}</Link>
                  <div className="muted small">{p.plan_type === "demand" ? "Demand plan" : "Future need"}</div></td>
                <td>{p.title}<div className="muted small">{p.demand_id ? `${p.demand_id} · needed ${fmtDate(p.needed_by)} · ×${p.overprovision_factor}` : ""}</div></td>
                <td className="num">{p.target}</td><td className="num">{p.ready_count}</td><td className="num">{p.trainees_planned}</td>
                <td>{p.feasible ? <Badge tone="green">feasible</Badge> : <Badge tone="red">{p.shortfall} short</Badge>}</td>
                <td><Badge tone={PLAN_TONE[p.status]}>{p.status}</Badge></td>
                <td className="small">{p.created_by_name}<div className="muted">{fmtDate(p.created_on?.slice(0, 10))}</div></td>
              </tr>
            ))}
            {data.length === 0 && <tr><td colSpan={8} className="muted">No plans here.</td></tr>}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function Calendar() {
  const { data, loading, error } = useApi("/api/training/calendar", { days: 90 });
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  const tone = { Scheduled: "ilt", Running: "run", Completed: "done", Proposed: "proposed" };
  const rows = data.batches.map((b, i) => ({
    key: b.batch_id || `p${i}`, label: b.skill, sub: `${b.batch_id || b.plan_id + " (proposed)"} · ${b.enrolled}/${b.capacity} · ${b.status}`,
    start: b.start_date, end: b.end_date, tone: tone[b.status] || "ilt", barLabel: b.enrolled, title: `${b.title}\n${b.start_date} → ${b.end_date}`,
  }));
  return (
    <Card title={`Classroom batches ${fmtDate(data.from)} – ${fmtDate(data.to)} (${rows.length})`}
          actions={<span className="small"><Badge tone="blue">scheduled</Badge> <Badge tone="green">running</Badge> <Badge tone="grey">completed</Badge> <Badge tone="amber">proposed (pending HR)</Badge></span>}>
      <Gantt rows={rows} from={data.from} to={data.to} markers={[{ date: data.today, label: "Today", tone: "red" }]} />
    </Card>
  );
}

function Forecast() {
  const nav = useNavigate();
  const { data, loading, error } = useApi("/api/training/forecast", { top: 20 });
  return (
    <Card title="Skills where upcoming demand exceeds available supply">
      <p className="small muted">Expected need = open demands × their over-provisioning factor + two months of the last six months' demand run-rate.
        Supply = people on bench or rolling off within 60 days at level 3+. The trainable pool are bench people who hold prerequisites
        or related skills, or whose role family uses the skill.</p>
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : data && (
        <table className="table">
          <thead><tr><th>Skill</th><th className="num">Open need</th><th className="num">Recent / month</th><th className="num">Expected</th>
            <th className="num">Available</th><th className="num">Gap</th><th className="num">Hired externally (6 m)</th><th className="num">Trainable bench</th><th /></tr></thead>
          <tbody>
            {data.map((f) => (
              <tr key={f.skill_id}>
                <td><b>{f.skill}</b></td><td className="num">{f.open_need}</td><td className="num">{f.recent_monthly}</td>
                <td className="num">{f.expected_need}</td><td className="num">{f.available_supply}</td>
                <td className="num"><Badge tone={f.gap >= 10 ? "red" : "amber"}>{f.gap}</Badge></td>
                <td className="num">{f.hired_externally_recently}</td><td className="num">{f.trainable_pool}</td>
                <td><button className="btn btn-ghost btn-sm" disabled={!f.trainable_pool} onClick={() => nav(`/training/new?skill=${f.skill_id}`)}>Plan batch</button></td>
              </tr>
            ))}
            {data.length === 0 && <tr><td colSpan={9} className="muted">Supply covers expected demand for every skill.</td></tr>}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function Onboarding() {
  const { data, loading, error } = useApi("/api/training/onboarding");
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  return (
    <div className="grid-2">
      {data.map((g) => (
        <Card key={g.role_family} title={`${g.role_family_name} — ${g.hires.length} new hire${g.hires.length > 1 ? "s" : ""}`}
              actions={<span className="small muted">{g.in_onboarding} in onboarding · {g.deployed} deployed</span>}>
          <div className="small">Curriculum: {g.curriculum.map((c) => <span key={c} className="chip">{c}</span>)}</div>
          <table className="table compact">
            <tbody>
              {g.hires.slice(0, 8).map((h) => (
                <tr key={h.emp_id}>
                  <td><Link to={`/employees/${h.emp_id}`}>{h.name}</Link><div className="muted small">joined {fmtDate(h.date_of_joining)} · {h.base_location}</div></td>
                  <td style={{ width: "40%" }}><div className="meter"><span className="meter-fill" style={{ width: `${h.progress}%` }} /></div>
                    <span className="small muted">{h.completed}/{h.total} courses</span></td>
                  <td><Badge tone={h.status === "allocated" ? "green" : h.status === "training" ? "blue" : "amber"}>{h.status}</Badge></td>
                </tr>
              ))}
            </tbody>
          </table>
          {g.hires.length > 8 && <div className="muted small">+{g.hires.length - 8} more</div>}
        </Card>
      ))}
    </div>
  );
}
