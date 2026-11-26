import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";
import PlanView from "../components/PlanView";
import { Card, ErrorBox, Loading, useApi, useDebounced } from "../components/ui";

export default function PlanBuilder() {
  const nav = useNavigate();
  const [params] = useSearchParams();
  const demandId = params.get("demand");
  const skillId = params.get("skill");
  const demand = useApi(demandId ? `/api/demands/${demandId}` : null);
  const [opts, setOpts] = useState({ overprovision_factor: null, include_rotation: false, extra_bench: false, size: 20 });
  const [plan, setPlan] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const dOpts = useDebounced(JSON.stringify(opts), 400);

  useEffect(() => {
    if (demand.data && opts.overprovision_factor === null) setOpts((o) => ({ ...o, overprovision_factor: demand.data.overprovision_factor || 1 }));
  }, [demand.data]); // eslint-disable-line

  const body = () => {
    const o = JSON.parse(dOpts);
    return demandId ? { demand_id: demandId, overprovision_factor: o.overprovision_factor || undefined, include_rotation: o.include_rotation, extra_bench: o.extra_bench }
                    : { skill_id: skillId, size: Number(o.size) };
  };
  useEffect(() => {
    if (demandId && opts.overprovision_factor === null) return;
    let live = true;
    setLoading(true); setError(null);
    api("/api/training/plans/preview", { method: "POST", body: body() })
      .then((p) => live && setPlan(p)).catch((e) => live && setError(e)).finally(() => live && setLoading(false));
    return () => { live = false; };
  }, [dOpts, demandId, skillId]); // eslint-disable-line

  const submit = async () => {
    setSaving(true); setError(null);
    try { const p = await api("/api/training/plans", { method: "POST", body: body() }); nav(`/training/plans/${p.plan_id}`); }
    catch (e) { setError(e); } finally { setSaving(false); }
  };

  return (
    <div className="page">
      <Link to={demandId ? `/demands/${demandId}` : "/training"} className="back">‹ Back</Link>
      <div className="page-head">
        <div>
          <h1>Plan training {demandId ? `for ${demand.data?.role_title || demandId}` : `— future need: ${plan?.gap_summary ? Object.keys(plan.gap_summary)[0] : ""}`}</h1>
          <p className="muted">{demandId
            ? `${demand.data?.positions ?? "…"} positions · needed by ${demand.data?.needed_by ?? "…"} — preview updates as you change the options`
            : "Prepare bench people for a skill that upcoming demand needs more of than we have"}</p>
        </div>
      </div>
      <Card title="Options">
        <div className="form-row plan-opts">
          {demandId ? (<>
            <label>Over-provisioning factor <b>×{Number(opts.overprovision_factor || 1).toFixed(2)}</b>
              {plan && <span className="muted small"> → target {plan.target}</span>}
              <input type="range" min="1" max="3" step="0.25" value={opts.overprovision_factor || 1}
                     onChange={(e) => setOpts({ ...opts, overprovision_factor: Number(e.target.value) })} /></label>
            <label className="check"><input type="checkbox" checked={opts.extra_bench} onChange={(e) => setOpts({ ...opts, extra_bench: e.target.checked })} />
              Also train every other suitable bench person (future need)</label>
            <label className="check"><input type="checkbox" checked={opts.include_rotation} onChange={(e) => setOpts({ ...opts, include_rotation: e.target.checked })} />
              Include rotation-due people (needs release approval)</label>
          </>) : (
            <label>Batch size<input type="number" min="1" max="200" value={opts.size} onChange={(e) => setOpts({ ...opts, size: e.target.value })} /></label>
          )}
          <div className="grow" />
          <button className="btn btn-primary" onClick={submit} disabled={saving || !plan?.trainees?.length}>
            {saving ? "Submitting…" : "Submit for HR approval"}</button>
        </div>
        <p className="small muted">The factor is your call; HR approves it (and may adjust it) together with the plan. Nothing is booked until HR approves.</p>
      </Card>
      <ErrorBox error={error || demand.error} />
      {loading && !plan ? <Loading /> : plan && <PlanView plan={plan} />}
    </div>
  );
}
