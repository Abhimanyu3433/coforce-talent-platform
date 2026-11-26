"""Training pipeline planner (Stage 5).

Demand plan
  target   = ceil(positions × over-provisioning factor)         (factor set by the demand raiser, approved by HR)
  ready    = people who already meet every mandatory skill and are available before needed-by
  trainees = best near-fit candidates (bench first, then in-training, then rolling off; optionally rotation-due)
             whose skill gaps can be closed before the deadline, until ready + trainees = target
             (+ optionally every other near-fit bench person, "for now and future need")
  schedule = trainees grouped into cohorts by the week they become available; each cohort runs the union of its
             gap skills as consecutive batches, ordered by prerequisites, on working days (Mon–Fri), finishing before
             needed-by minus an interview buffer. Classroom batches need ≥ 6 learners (else self-paced) and hold ≤ 25.

Forecast plan  bench people prepared for a skill that open + recent demand needs more of than supply has.
Approval       a plan stays 'pending' until HR approves (optionally changing the factor, which re-plans);
               approval writes real batches + enrollments, records HR on the demand and shortlists the trainees.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from .. import config
from ..db import one, rows, scalar
from . import matching
from .errors import BadRequest, Forbidden, NotFound

MIN_CLASSROOM = 6
MAX_BATCH = 25
LEAD_DAYS = 3                  # working days to set up a batch
MAX_TRAINING_DAYS = 30         # working days; longer gaps are not "trainable" for a demand
JOIN_WINDOW = 5                # learners free within 5 working days of each other share a batch
POOL_PRIORITY = {"bench": 0, "training": 1, "rolling_off": 2, "rotation": 3, "allocated": 4}
ACTIVE_PLAN = ("pending", "approved")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- working-day calendar
def next_workday(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def add_workdays(d: date, n: int) -> date:
    d = next_workday(d)
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def end_of(start: date, days: int) -> date:
    """Last day of a course of `days` working days starting on `start`."""
    return add_workdays(start, max(days, 1) - 1)


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


# ---------------------------------------------------------------- reference data
class Catalog:
    def __init__(self, conn):
        self.courses = defaultdict(dict)            # skill_id -> {(level, mode): course}
        for c in rows(conn, "SELECT course_id, title, skill_id, level, mode, duration_days, max_batch_size FROM courses"):
            self.courses[c["skill_id"]][(c["level"], c["mode"])] = c
        self.prereq = defaultdict(set)              # skill -> its prerequisites
        for r in rows(conn, "SELECT skill_a, skill_b FROM skill_relations WHERE relation='prerequisite'"):
            self.prereq[r["skill_b"]].add(r["skill_a"])
        self.names = {s["skill_id"]: s["name"] for s in rows(conn, "SELECT skill_id, name FROM skills")}

    def course(self, skill_id: str, upskill: bool, classroom: bool) -> dict | None:
        c = self.courses.get(skill_id, {})
        if classroom:
            return (c.get(("Advanced", "instructor-led")) if upskill else None) or c.get(("Intermediate", "instructor-led")) \
                or c.get(("Beginner", "self-paced"))
        return c.get(("Beginner", "self-paced")) or c.get(("Intermediate", "instructor-led"))

    def order(self, skills: list[str], weight: dict[str, int]) -> list[str]:
        """Topological order by prerequisites (within the set); ties → most learners first."""
        todo, out = set(skills), []
        while todo:
            free = [s for s in todo if not (self.prereq[s] & todo)] or list(todo)
            nxt = max(free, key=lambda s: (weight.get(s, 0), self.names.get(s, s)))
            out.append(nxt)
            todo.remove(nxt)
        return out


_CAT: dict[str, Catalog] = {}


def catalog(conn) -> Catalog:
    key = conn.execute("PRAGMA database_list").fetchone()[2]
    if key not in _CAT:
        _CAT[key] = Catalog(conn)
    return _CAT[key]


def busy_employees(conn, exclude_plan: str | None = None) -> set[str]:
    q = f"""SELECT DISTINCT t.emp_id FROM training_plan_trainees t JOIN training_plans p USING(plan_id)
            WHERE p.status IN ({','.join('?' * len(ACTIVE_PLAN))})""" + (" AND p.plan_id != ?" if exclude_plan else "")
    return {r["emp_id"] for r in rows(conn, q, list(ACTIVE_PLAN) + ([exclude_plan] if exclude_plan else []))}


# ---------------------------------------------------------------- scheduling
def schedule(cat: Catalog, trainees: list[dict], deadline: date, earliest: date) -> tuple[list[dict], list[dict]]:
    """trainees: [{emp_id, name, available, gaps:[{skill_id, kind}]}] → (batches, trainees with track + ready_by)."""
    cohorts = defaultdict(list)
    for t in trainees:
        start = next_workday(max(earliest, t["available"]))
        t["cohort_start"] = start
        cohorts[monday(start)].append(t)
    batches, bid = [], 1
    for wk in sorted(cohorts):
        group = cohorts[wk]
        free_from = {t["emp_id"]: t["cohort_start"] for t in group}        # per-learner cursor
        need = defaultdict(list)
        for t in group:
            t.setdefault("track", [])
            for g in t["gaps"]:
                need[g["skill_id"]].append((t, g["kind"]))
        for sid in cat.order(list(need), {k: len(v) for k, v in need.items()}):
            # learners free within JOIN_WINDOW working days of each other share a batch (prerequisites first)
            waves, cur = [], []
            for t, k in sorted(need[sid], key=lambda x: free_from[x[0]["emp_id"]]):
                if cur and free_from[t["emp_id"]] > add_workdays(free_from[cur[0][0]["emp_id"]], JOIN_WINDOW):
                    waves.append(cur)
                    cur = []
                cur.append((t, k))
            if cur:
                waves.append(cur)
            for learners in waves:
                upskill = sum(1 for _, k in learners if k == "upskill") > len(learners) / 2
                classroom = len(learners) >= MIN_CLASSROOM
                c = cat.course(sid, upskill, classroom)
                if not c:
                    continue
                days = c["duration_days"]
                n_chunks = math.ceil(len(learners) / MAX_BATCH) if classroom else 1
                for i in range(n_chunks):
                    chunk = learners[i::n_chunks]
                    start = next_workday(max(free_from[t["emp_id"]] for t, _ in chunk))
                    end = end_of(start, days)
                    b = {"key": f"B{bid}", "skill_id": sid, "skill": cat.names.get(sid, sid), "course_id": c["course_id"],
                         "course_title": c["title"], "mode": c["mode"], "level": c["level"], "duration_days": days,
                         "start_date": start.isoformat(), "end_date": end.isoformat(), "learners": [t["emp_id"] for t, _ in chunk],
                         "size": len(chunk), "cohort": wk.isoformat(), "late": end > deadline}
                    batches.append(b)
                    bid += 1
                    for t, _ in chunk:
                        t["track"].append({"batch": b["key"], "skill": b["skill"], "start": b["start_date"],
                                           "end": b["end_date"], "mode": c["mode"]})
                        free_from[t["emp_id"]] = next_workday(end + timedelta(days=1))
    for t in trainees:
        ends = [date.fromisoformat(x["end"]) for x in t.get("track", [])]
        rb = max(ends) if ends else t["cohort_start"]
        t["ready_by"] = rb.isoformat()
        t["on_time"] = rb <= deadline
        t["training_days"] = sum(b["duration_days"] for b in batches if t["emp_id"] in b["learners"])
    return batches, trainees


# ---------------------------------------------------------------- demand plan
def plan_for_demand(conn, demand_id: str, overprovision_factor: float | None = None, include_rotation: bool = False,
                    extra_bench: bool = False, exclude_plan: str | None = None) -> dict:
    spec = matching.load_spec(conn, demand_id)
    d = one(conn, "SELECT role_title, positions, needed_by, client_interview, status, overprovision_factor FROM demands WHERE demand_id=?",
            (demand_id,))
    if d["status"] not in ("Open", "On Hold"):
        raise BadRequest(f"Demand is '{d['status']}' — only open demands can get a training plan")
    factor = float(overprovision_factor or d["overprovision_factor"] or 1.0)
    if not 1.0 <= factor <= 3.0:
        raise BadRequest("Over-provisioning factor must be between 1.0 and 3.0")
    today = config.as_of()
    needed = date.fromisoformat(d["needed_by"])
    deadline = needed - timedelta(days=7 if d["client_interview"] else 2)
    earliest = add_workdays(today, LEAD_DAYS)
    target = math.ceil(d["positions"] * factor)
    busy = busy_employees(conn, exclude_plan)
    selected_elsewhere = {r["emp_id"] for r in rows(conn, "SELECT emp_id FROM matches WHERE status='selected' AND demand_id != ?", (demand_id,))}

    res = matching.run(conn, spec, limit=100_000)
    cands = [c for c in res["candidates"] if c["emp_id"] not in busy and c["emp_id"] not in selected_elsewhere]
    ready = [c for c in cands if c["mandatory_met"] == c["mandatory_total"] and c["availability_date"] <= needed.isoformat()]
    ready.sort(key=lambda c: (POOL_PRIORITY[c["pool"]], -c["score"]))
    ready_used = ready[:target]
    pools = {"bench", "training", "rolling_off"} | ({"rotation"} if include_rotation else set())
    cat = catalog(conn)
    near = []
    for c in cands:
        if c in ready_used or c["mandatory_met"] == c["mandatory_total"] or c["pool"] not in pools or not c["gaps"]:
            continue
        if c["training_days"] > MAX_TRAINING_DAYS:
            continue
        avail = date.fromisoformat(c["availability_date"])
        est_end = add_workdays(next_workday(max(earliest, avail)), c["training_days"])
        if est_end > deadline:
            continue
        near.append(c)
    near.sort(key=lambda c: (POOL_PRIORITY[c["pool"]], -c["score"]))
    need = max(0, target - len(ready_used))

    def as_trainee(c, extra):
        return {"emp_id": c["emp_id"], "name": c["name"], "designation": c["designation"], "grade": c["grade"],
                "base_location": c["base_location"], "pool": c["pool"], "score": c["score"], "rank": c["rank"],
                "available": date.fromisoformat(c["availability_date"]), "requires_approval": c["requires_approval"],
                "gaps": [{"skill_id": g["skill_id"], "name": g["name"], "kind": g["kind"]} for g in c["gaps"]], "extra": extra}

    chosen = near[:need]
    reserve = near[need:]
    for _ in range(3):        # schedule; swap anyone who would finish late for the next candidate; repeat
        pool_extra = [c for c in reserve if c["pool"] == "bench"] if extra_bench else []
        trainees = [as_trainee(c, False) for c in chosen] + [as_trainee(c, True) for c in pool_extra]
        batches, trainees = schedule(cat, trainees, deadline, earliest)
        late_ids = {t["emp_id"] for t in trainees if not t["on_time"] and not t["extra"]}
        if not late_ids or not reserve:
            break
        keep = [c for c in chosen if c["emp_id"] not in late_ids]
        swap = [c for c in reserve if c["pool"] != "rotation" or include_rotation][:len(late_ids)]
        if not swap:
            break
        reserve = [c for c in reserve if c not in swap]
        chosen = keep + swap
    if extra_bench:     # extras that cannot finish in time are not worth scheduling for this demand
        trainees = [t for t in trainees if not (t["extra"] and not t["on_time"])]
        keep_ids = {t["emp_id"] for t in trainees}
        batches = [dict(b, learners=[e for e in b["learners"] if e in keep_ids]) for b in batches]
        batches = [dict(b, size=len(b["learners"])) for b in batches if b["learners"]]
    for t in trainees:
        t["available"] = t["available"].isoformat()
        t["cohort_start"] = t["cohort_start"].isoformat()
    late = [t for t in trainees if not t["on_time"]]
    covered = len(ready_used) + sum(1 for t in trainees if t["on_time"] and not t["extra"])
    shortfall = max(0, target - covered)
    gap_counts = defaultdict(int)
    for t in trainees:
        for g in t["gaps"]:
            gap_counts[g["name"]] += 1
    advice = []
    if shortfall:
        advice.append(f"{shortfall} of {target} still uncovered internally — consider external hiring for {shortfall}, "
                      f"a later start date, or relaxing a mandatory skill.")
    if late:
        advice.append(f"{len(late)} trainee(s) would finish after the deadline ({deadline.isoformat()}).")
    if not trainees and not shortfall:
        advice.append("No training needed — enough people already fully match.")
    return {
        "plan_type": "demand", "demand_id": demand_id, "role_title": d["role_title"], "positions": d["positions"],
        "needed_by": d["needed_by"], "deadline": deadline.isoformat(), "plan_start": earliest.isoformat(),
        "overprovision_factor": factor, "target": target, "ready_count": len(ready_used),
        "ready": [{k: c[k] for k in ("emp_id", "name", "designation", "grade", "pool", "score", "availability_date",
                                     "requires_approval")} for c in ready_used],
        "trainees_needed": need, "trainees_planned": len(trainees), "covered": covered, "shortfall": shortfall,
        "feasible": shortfall == 0 and not late, "near_fit_available": len(near),
        "trainees": trainees, "batches": batches, "gap_summary": dict(sorted(gap_counts.items(), key=lambda x: -x[1])),
        "total_training_days": sum(t["training_days"] for t in trainees), "advice": advice,
        "options": {"include_rotation": include_rotation, "extra_bench": extra_bench},
    }


# ---------------------------------------------------------------- forecast (future need)
def forecast(conn, months_back: int = 6, top: int = 15) -> list[dict]:
    today = config.as_of()
    since = (today - timedelta(days=30 * months_back)).isoformat()
    dem = rows(conn, """SELECT ds.skill_id, s.name, s.type,
                               sum(CASE WHEN d.status='Open' THEN d.positions * coalesce(d.overprovision_factor,1) ELSE 0 END) open_need,
                               sum(CASE WHEN d.status!='Open' AND d.created_on >= ? THEN d.positions ELSE 0 END) recent_need,
                               sum(CASE WHEN d.status='Fulfilled - External' AND d.created_on >= ? THEN d.positions ELSE 0 END) hired_out
                        FROM demand_skills ds JOIN demands d USING(demand_id) JOIN skills s USING(skill_id)
                        WHERE ds.mandatory=1 AND s.type != 'domain' GROUP BY ds.skill_id""", (since, since))
    horizon = (today + timedelta(days=60)).isoformat()
    supply = {r["skill_id"]: r["n"] for r in rows(conn, """SELECT es.skill_id, count(*) n FROM employee_skills es
                    JOIN employees e USING(emp_id) JOIN residency_snapshot r USING(emp_id)
                    WHERE es.proficiency >= 3 AND e.function='Delivery'
                      AND (e.status IN ('bench','training') OR (e.status='allocated' AND r.planned_rolloff <= ?))
                    GROUP BY es.skill_id""", (horizon,))}
    out = []
    for r in dem:
        monthly_recent = (r["recent_need"] or 0) / months_back
        expected = (r["open_need"] or 0) + monthly_recent * 2          # open now + ~2 months of recent run-rate
        sup = supply.get(r["skill_id"], 0)
        gap = expected - sup
        if gap <= 0:
            continue
        out.append({"skill_id": r["skill_id"], "skill": r["name"], "open_need": round(r["open_need"] or 0, 1),
                    "recent_monthly": round(monthly_recent, 1), "expected_need": round(expected, 1),
                    "available_supply": sup, "gap": math.ceil(gap), "hired_externally_recently": r["hired_out"] or 0})
    out.sort(key=lambda x: -x["gap"])
    out = out[:top]
    busy = busy_employees(conn)
    for o in out:
        o["trainable_pool"] = len(forecast_candidates(conn, o["skill_id"], 200, busy))
    return out


def forecast_candidates(conn, skill_id: str, limit: int, busy: set | None = None) -> list[dict]:
    """Bench people without the skill who would learn it fastest: they hold its prerequisites / related skills."""
    busy = busy if busy is not None else busy_employees(conn)
    rel = rows(conn, """SELECT skill_a AS other, strength FROM skill_relations WHERE skill_b=?
                        UNION ALL SELECT skill_b, strength FROM skill_relations WHERE skill_a=?""", (skill_id, skill_id))
    fam = {r["role_family"]: r["tier"] for r in rows(conn, "SELECT role_family, tier FROM role_family_skills WHERE skill_id=?", (skill_id,))}
    if not rel and not fam:
        return []
    strength = {r["other"]: r["strength"] for r in rel}
    pool = rows(conn, """SELECT e.emp_id, e.first_name || ' ' || e.last_name AS name, e.designation, e.grade, e.role_family,
                                e.base_location, r.bench_aging_days
                         FROM employees e JOIN residency_snapshot r USING(emp_id)
                         WHERE e.function='Delivery' AND e.status='bench'
                           AND NOT EXISTS (SELECT 1 FROM employee_skills x WHERE x.emp_id=e.emp_id AND x.skill_id=? AND x.proficiency>=3)""",
                (skill_id,))
    if not pool:
        return []
    ids = [p["emp_id"] for p in pool]
    held = defaultdict(dict)
    for s in conn.execute(f"SELECT emp_id, skill_id, proficiency FROM employee_skills WHERE emp_id IN ({','.join('?' * len(ids))})", ids):
        held[s[0]][s[1]] = s[2]
    out = []
    for p in pool:
        if p["emp_id"] in busy:
            continue
        adj = max((strength[s] * min(1, lv / 3) for s, lv in held[p["emp_id"]].items() if s in strength), default=0)
        tier = {"core": 1.0, "common": 0.7, "optional": 0.4}.get(fam.get(p["role_family"]), 0)
        readiness = round(0.6 * adj + 0.4 * tier, 3)
        if readiness < 0.35:
            continue
        out.append({**p, "readiness": readiness, "current_level": held[p["emp_id"]].get(skill_id, 0)})
    out.sort(key=lambda x: (-x["readiness"], -(x["bench_aging_days"] or 0)))
    return out[:limit]


def plan_for_skill(conn, skill_id: str, size: int | None = None) -> dict:
    cat = catalog(conn)
    if skill_id not in cat.names:
        raise NotFound("Unknown skill")
    today = config.as_of()
    fc = next((f for f in forecast(conn, top=100) if f["skill_id"] == skill_id), None)
    size = int(size or (min(MAX_BATCH, fc["gap"]) if fc else MAX_BATCH))
    if not 1 <= size <= 200:
        raise BadRequest("size must be 1-200")
    cands = forecast_candidates(conn, skill_id, size)
    earliest = add_workdays(today, LEAD_DAYS)
    trainees = [{"emp_id": c["emp_id"], "name": c["name"], "designation": c["designation"], "grade": c["grade"],
                 "base_location": c["base_location"], "pool": "bench", "score": round(c["readiness"] * 100, 1), "rank": i + 1,
                 "available": today, "requires_approval": False, "extra": False,
                 "gaps": [{"skill_id": skill_id, "name": cat.names[skill_id], "kind": "upskill" if c["current_level"] else "new"}]}
                for i, c in enumerate(cands)]
    deadline = today + timedelta(days=120)
    batches, trainees = schedule(cat, trainees, deadline, earliest)
    for t in trainees:
        t["available"] = t["available"].isoformat()
        t["cohort_start"] = t["cohort_start"].isoformat()
    return {"plan_type": "forecast", "demand_id": None, "skill_id": skill_id, "role_title": f"Future need: {cat.names[skill_id]}",
            "positions": None, "needed_by": None, "deadline": deadline.isoformat(), "plan_start": earliest.isoformat(),
            "overprovision_factor": None, "target": size, "ready_count": 0, "ready": [], "trainees_needed": size,
            "trainees_planned": len(trainees), "covered": len(trainees), "shortfall": max(0, size - len(trainees)),
            "feasible": len(trainees) == size, "near_fit_available": len(cands), "trainees": trainees, "batches": batches,
            "gap_summary": {cat.names[skill_id]: len(trainees)}, "total_training_days": sum(t["training_days"] for t in trainees),
            "forecast": fc, "advice": [] if len(trainees) == size else [f"Only {len(trainees)} suitable bench people found."],
            "options": {}}


# ---------------------------------------------------------------- persistence & approval
def _store(conn, plan: dict, user: dict, plan_id: str | None = None) -> str:
    if plan_id is None:
        n = scalar(conn, "SELECT max(CAST(substr(plan_id, 3) AS INTEGER)) FROM training_plans") or 0
        plan_id = f"TP{n + 1:04d}"
        conn.execute("""INSERT INTO training_plans (plan_id, plan_type, demand_id, skill_id, created_by, created_on, status)
                        VALUES (?,?,?,?,?,?, 'pending')""",
                     (plan_id, plan["plan_type"], plan.get("demand_id"), plan.get("skill_id"), user["user_id"], _now()))
    conn.execute("""UPDATE training_plans SET overprovision_factor=?, target=?, ready_count=?, trainees_planned=?, shortfall=?,
                    feasible=?, plan=? WHERE plan_id=?""",
                 (plan["overprovision_factor"], plan["target"], plan["ready_count"], plan["trainees_planned"], plan["shortfall"],
                  int(plan["feasible"]), json.dumps(plan), plan_id))
    conn.execute("DELETE FROM training_plan_trainees WHERE plan_id=?", (plan_id,))
    for t in plan["trainees"]:
        conn.execute("INSERT INTO training_plan_trainees (plan_id, emp_id, ready_by, on_time) VALUES (?,?,?,?)",
                     (plan_id, t["emp_id"], t["ready_by"], int(t["on_time"])))
    conn.commit()
    return plan_id


def create_plan(conn, user: dict, demand_id: str | None = None, skill_id: str | None = None, overprovision_factor=None,
                include_rotation=False, extra_bench=False, size=None) -> dict:
    if demand_id:
        existing = one(conn, f"SELECT plan_id FROM training_plans WHERE demand_id=? AND status IN ({','.join('?' * len(ACTIVE_PLAN))})",
                       (demand_id, *ACTIVE_PLAN))
        if existing:
            raise BadRequest(f"Demand already has an active plan ({existing['plan_id']}) — cancel it first")
        plan = plan_for_demand(conn, demand_id, overprovision_factor, include_rotation, extra_bench)
    elif skill_id:
        plan = plan_for_skill(conn, skill_id, size)
    else:
        raise BadRequest("Give a demand_id or a skill_id")
    if not plan["trainees"]:
        raise BadRequest("Nothing to plan: " + (" ".join(plan["advice"]) or "no trainees found"))
    pid = _store(conn, plan, user)
    return get_plan(conn, pid)


def get_plan(conn, plan_id: str) -> dict:
    p = one(conn, """SELECT tp.*, c.first_name || ' ' || c.last_name AS created_by_name,
                            a.first_name || ' ' || a.last_name AS decided_by_name
                     FROM training_plans tp LEFT JOIN employees c ON c.emp_id=tp.created_by
                     LEFT JOIN employees a ON a.emp_id=tp.decided_by WHERE tp.plan_id=?""", (plan_id,))
    if not p:
        raise NotFound("Training plan not found")
    body = json.loads(p.pop("plan"))
    return {**body, **{k: p[k] for k in ("plan_id", "plan_type", "status", "created_by", "created_by_name", "created_on",
                                         "decided_by", "decided_by_name", "decided_on", "decision_note")},
            "feasible": bool(p["feasible"])}


def list_plans(conn, status: str | None = None, mine_user: str | None = None) -> list[dict]:
    where, prm = ["1=1"], []
    if status:
        where.append("tp.status=?"); prm.append(status)
    if mine_user:
        where.append("tp.created_by=?"); prm.append(mine_user)
    out = rows(conn, f"""SELECT tp.plan_id, tp.plan_type, tp.demand_id, tp.skill_id, tp.status, tp.created_on, tp.target, tp.ready_count,
                                tp.trainees_planned, tp.shortfall, tp.feasible, tp.overprovision_factor, tp.decided_on,
                                d.role_title, d.needed_by, d.positions, d.ibu_id, s.name AS skill,
                                c.first_name || ' ' || c.last_name AS created_by_name
                         FROM training_plans tp LEFT JOIN demands d ON d.demand_id=tp.demand_id LEFT JOIN skills s ON s.skill_id=tp.skill_id
                         LEFT JOIN employees c ON c.emp_id=tp.created_by WHERE {' AND '.join(where)}
                         ORDER BY CASE tp.status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END, tp.created_on DESC""", prm)
    for o in out:
        o["feasible"] = bool(o["feasible"])
        o["title"] = o["role_title"] or f"Future need: {o['skill']}"
    return out


def decide(conn, plan_id: str, hr: dict, approve: bool, note: str | None = None, overprovision_factor: float | None = None) -> dict:
    p = one(conn, "SELECT * FROM training_plans WHERE plan_id=?", (plan_id,))
    if not p:
        raise NotFound("Training plan not found")
    if p["status"] != "pending":
        raise BadRequest(f"Plan is already {p['status']}")
    if p["created_by"] == hr["user_id"]:
        raise Forbidden("HR cannot approve a plan they created")
    if not approve:
        conn.execute("UPDATE training_plans SET status='rejected', decided_by=?, decided_on=?, decision_note=? WHERE plan_id=?",
                     (hr["user_id"], _now(), note, plan_id))
        conn.commit()
        return get_plan(conn, plan_id)
    plan = json.loads(p["plan"])
    if p["plan_type"] == "demand" and overprovision_factor and float(overprovision_factor) != plan["overprovision_factor"]:
        o = plan["options"]
        plan = plan_for_demand(conn, p["demand_id"], float(overprovision_factor), o.get("include_rotation", False),
                               o.get("extra_bench", False), exclude_plan=plan_id)
        _store(conn, plan, {"user_id": p["created_by"]}, plan_id)
        note = (note + " · " if note else "") + f"HR changed factor to ×{overprovision_factor}"
    # materialise batches and enrollments
    nb = scalar(conn, "SELECT max(CAST(substr(batch_id, 3) AS INTEGER)) FROM training_batches") or 0
    ne = scalar(conn, "SELECT max(CAST(substr(enrollment_id, 3) AS INTEGER)) FROM training_enrollments") or 0
    loc = {r["emp_id"]: r["base_location"] for r in plan["trainees"]}
    key_to_id = {}
    for b in plan["batches"]:
        locs = {loc.get(e) for e in b["learners"]}
        batch_id = None
        if b["mode"] == "instructor-led":
            nb += 1
            batch_id = f"TB{nb:05d}"
            key_to_id[b["key"]] = batch_id
            conn.execute("""INSERT INTO training_batches (batch_id, course_id, skill_id, start_date, end_date, capacity, enrolled,
                            location, delivery, status, linked_demand_ids, created_by, plan_id)
                            VALUES (?,?,?,?,?,?,?,?,?, 'Scheduled', ?, ?, ?)""",
                         (batch_id, b["course_id"], b["skill_id"], b["start_date"], b["end_date"], MAX_BATCH, b["size"],
                          next(iter(locs)) if len(locs) == 1 else "Multiple", "Classroom" if len(locs) == 1 else "Virtual",
                          p["demand_id"], p["created_by"], plan_id))
        for emp in b["learners"]:
            ne += 1
            conn.execute("""INSERT INTO training_enrollments (enrollment_id, emp_id, course_id, skill_id, mode, start_date, end_date,
                            status, assessment_score, source, batch_id, plan_id) VALUES (?,?,?,?,?,?,?, 'scheduled', NULL, ?, ?, ?)""",
                         (f"EN{ne:06d}", emp, b["course_id"], b["skill_id"], b["mode"], b["start_date"], b["end_date"],
                          "demand_plan" if p["plan_type"] == "demand" else "forecast_plan", batch_id, plan_id))
    if p["plan_type"] == "demand":
        conn.execute("UPDATE demands SET overprovision_factor=?, overprovision_approved_by=? WHERE demand_id=?",
                     (plan["overprovision_factor"], hr["user_id"], p["demand_id"]))
        for t in plan["trainees"]:
            conn.execute("""UPDATE matches SET status='shortlisted', note=?, updated_by=?, updated_on=?
                            WHERE demand_id=? AND emp_id=? AND status='suggested'""",
                         (f"In training plan {plan_id} — ready {t['ready_by']}", hr["user_id"], _now(), p["demand_id"], t["emp_id"]))
    conn.execute("UPDATE training_plans SET status='approved', decided_by=?, decided_on=?, decision_note=? WHERE plan_id=?",
                 (hr["user_id"], _now(), note, plan_id))
    conn.commit()
    return get_plan(conn, plan_id)


def cancel(conn, plan_id: str, user: dict) -> dict:
    p = one(conn, "SELECT status, created_by FROM training_plans WHERE plan_id=?", (plan_id,))
    if not p:
        raise NotFound("Training plan not found")
    if p["created_by"] != user["user_id"] and user["app_role"] != "hr":
        raise Forbidden("Only the creator or HR can cancel a plan")
    if p["status"] not in ACTIVE_PLAN:
        raise BadRequest(f"Plan is already {p['status']}")
    if p["status"] == "approved":
        conn.execute("UPDATE training_enrollments SET status='cancelled' WHERE plan_id=? AND status='scheduled'", (plan_id,))
        conn.execute("UPDATE training_batches SET status='Cancelled' WHERE plan_id=? AND status='Scheduled'", (plan_id,))
    conn.execute("UPDATE training_plans SET status='cancelled', decided_on=coalesce(decided_on, ?) WHERE plan_id=?", (_now(), plan_id))
    conn.commit()
    return get_plan(conn, plan_id)


# ---------------------------------------------------------------- calendar & onboarding
def calendar(conn, start: str | None = None, days: int = 90) -> dict:
    today = config.as_of()
    s = date.fromisoformat(start) if start else today - timedelta(days=7)
    e = s + timedelta(days=days)
    batches = rows(conn, """SELECT b.batch_id, b.course_id, c.title, b.skill_id, sk.name AS skill, b.start_date, b.end_date, b.enrolled,
                                   b.capacity, b.location, b.delivery, b.status, b.linked_demand_ids, b.plan_id
                            FROM training_batches b JOIN courses c USING(course_id) JOIN skills sk ON sk.skill_id=b.skill_id
                            WHERE b.end_date >= ? AND b.start_date <= ? AND b.status != 'Cancelled' ORDER BY b.start_date""",
                   (s.isoformat(), e.isoformat()))
    pending = []
    for p in rows(conn, "SELECT plan_id, plan FROM training_plans WHERE status='pending'"):
        for b in json.loads(p["plan"])["batches"]:
            if b["mode"] == "instructor-led" and b["end_date"] >= s.isoformat() and b["start_date"] <= e.isoformat():
                pending.append({"batch_id": None, "title": b["course_title"], "skill": b["skill"], "start_date": b["start_date"],
                                "end_date": b["end_date"], "enrolled": b["size"], "capacity": MAX_BATCH, "status": "Proposed",
                                "plan_id": p["plan_id"]})
    return {"from": s.isoformat(), "to": e.isoformat(), "today": today.isoformat(), "batches": batches + pending}


def onboarding(conn) -> list[dict]:
    today = config.as_of()
    since = (today - timedelta(days=150)).isoformat()
    hires = rows(conn, """SELECT e.emp_id, e.first_name || ' ' || e.last_name AS name, e.role_family, e.role_family_name, e.designation,
                                 e.date_of_joining, e.status, e.base_location, e.ibu_id
                          FROM employees e WHERE e.function='Delivery' AND e.exp_at_joining < 1 AND e.date_of_joining >= ?
                          ORDER BY e.date_of_joining DESC""", (since,))
    if not hires:
        return []
    ids = [h["emp_id"] for h in hires]
    enr = defaultdict(list)
    for r in rows(conn, f"""SELECT t.emp_id, s.name, t.status, t.start_date, t.end_date, t.assessment_score
                            FROM training_enrollments t JOIN skills s USING(skill_id)
                            WHERE t.source='onboarding' AND t.emp_id IN ({','.join('?' * len(ids))}) ORDER BY t.start_date""", ids):
        enr[r["emp_id"]].append(r)
    groups = defaultdict(lambda: {"role_family": None, "role_family_name": None, "hires": []})
    for h in hires:
        e = enr.get(h["emp_id"], [])
        h["courses"] = e
        h["completed"] = sum(1 for x in e if x["status"] == "completed")
        h["total"] = len(e)
        h["progress"] = round(100 * h["completed"] / h["total"]) if h["total"] else 0
        g = groups[h["role_family"]]
        g["role_family"], g["role_family_name"] = h["role_family"], h["role_family_name"]
        g["hires"].append(h)
    curriculum = {r["role_family"]: [] for r in hires}
    for r in rows(conn, """SELECT r.role_family, s.name FROM role_family_skills r JOIN skills s USING(skill_id)
                           WHERE r.tier='core' ORDER BY r.role_family, s.name"""):
        if r["role_family"] in curriculum:
            curriculum[r["role_family"]].append(r["name"])
    out = []
    for fam, g in groups.items():
        g["curriculum"] = curriculum.get(fam, [])[:4]
        g["in_onboarding"] = sum(1 for h in g["hires"] if h["status"] == "training")
        g["deployed"] = sum(1 for h in g["hires"] if h["status"] == "allocated")
        out.append(g)
    return sorted(out, key=lambda g: -len(g["hires"]))
