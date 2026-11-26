import { Link } from "react-router-dom";
import Gantt from "./Gantt";
import { Badge, Card, Kpi, fmtDate } from "./ui";

const POOL_TONE = { bench: "amber", training: "blue", rolling_off: "green", rotation: "red", allocated: "grey" };

export default function PlanView({ plan }) {
  const p = plan;
  const learners = Object.fromEntries(p.trainees.map((t) => [t.emp_id, t]));
  const rows = p.batches.map((b) => ({
    key: b.key, label: `${b.skill}`, sub: `${b.mode === "instructor-led" ? "Classroom" : "Self-paced"} · ${b.size} learner${b.size > 1 ? "s" : ""} · ${b.duration_days} d`,
    start: b.start_date, end: b.end_date, tone: b.late ? "late" : b.mode === "instructor-led" ? "ilt" : "sp",
    barLabel: b.size, title: `${b.course_title}\n${b.start_date} → ${b.end_date}\n${b.learners.map((e) => learners[e]?.name).join(", ")}`,
  }));
  const markers = [{ date: p.plan_start, label: "Plan start", tone: "blue" }, { date: p.deadline, label: "Ready-by deadline", tone: "red" }];
  if (p.needed_by) markers.push({ date: p.needed_by, label: "Needed by", tone: "dark" });

  return (
    <div className="page">
      <div className="kpis">
        <Kpi label="Target" value={p.target} sub={p.positions ? `${p.positions} positions × ${p.overprovision_factor}` : "batch size"} />
        <Kpi label="Already fit" value={p.ready_count} sub="meet all mandatory skills in time" tone="blue" />
        <Kpi label="To train" value={p.trainees_planned} sub={`${p.near_fit_available} near-fit available`} tone="amber" />
        <Kpi label="Covered by deadline" value={`${p.covered}/${p.target}`} sub={p.shortfall ? `${p.shortfall} short` : "fully covered"} tone={p.shortfall ? "red" : undefined} />
        <Kpi label="Training effort" value={`${p.total_training_days} d`} sub={`${p.batches.length} batches · deadline ${fmtDate(p.deadline)}`} />
      </div>
      {p.advice?.map((a) => <div key={a} className="callout">{a}</div>)}
      {p.feasible && p.trainees.length > 0 && <div className="callout callout-green">Feasible: every planned trainee is ready by {fmtDate(p.deadline)}
        {p.needed_by ? ` (needed-by ${fmtDate(p.needed_by)} minus interview buffer)` : ""}.</div>}

      <Card title="Skill gaps being closed">
        <div className="chips">{Object.entries(p.gap_summary).map(([k, v]) => <span key={k} className="chip chip-hl">{k} <b>{v}</b></span>)}</div>
      </Card>

      <Card title="Batch schedule (working days, prerequisites first)">
        <Gantt rows={rows} markers={markers} />
        <p className="small muted">Classroom batches need at least 6 and at most 25 learners; smaller groups take the self-paced course in the same window.
          People free within 5 working days of each other share a batch.</p>
      </Card>

      <Card title={`Trainees (${p.trainees.length})`}>
        <table className="table compact">
          <thead><tr><th>Employee</th><th>Source</th><th>Gaps</th><th>Track</th><th>Ready by</th></tr></thead>
          <tbody>
            {p.trainees.map((t) => (
              <tr key={t.emp_id}>
                <td><Link to={`/employees/${t.emp_id}`}>{t.name}</Link><div className="muted small">{t.designation} · {t.grade} · {t.base_location}</div>
                  {t.extra && <Badge tone="grey">future need</Badge>}</td>
                <td><Badge tone={POOL_TONE[t.pool]}>{t.pool.replace("_", " ")}</Badge><div className="muted small">from {fmtDate(t.available)}</div>
                  {t.requires_approval && <Badge tone="amber">release approval</Badge>}</td>
                <td>{t.gaps.map((g) => <span key={g.skill_id} className="chip">{g.name}<b>{g.kind === "upskill" ? "↑" : "new"}</b></span>)}</td>
                <td className="small">{t.track.map((x) => <div key={x.batch}>{x.skill}: {fmtDate(x.start)} – {fmtDate(x.end)} <span className="muted">({x.mode === "instructor-led" ? "class" : "self"})</span></div>)}</td>
                <td className={t.on_time ? "text-green" : "text-red"}>{fmtDate(t.ready_by)} {t.on_time ? "✓" : "late"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      {p.ready?.length > 0 && (
        <Card title={`Already fit — no training needed (${p.ready.length})`}>
          <div className="chips">{p.ready.map((r) => (
            <Link key={r.emp_id} to={`/employees/${r.emp_id}`} className="chip chip-ok">{r.name} <b>{r.score}</b> · {r.pool.replace("_", " ")}</Link>))}</div>
        </Card>
      )}
    </div>
  );
}
