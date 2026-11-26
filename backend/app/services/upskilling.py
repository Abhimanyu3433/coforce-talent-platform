"""Upskilling advisor (Stage 6): which skills should a person learn next, and how.

Score (0-100) for each skill s the person doesn't have at level 3+:
    35 · Unlock      open demands the person would newly qualify for / fully match if they had s at level 3,
                     measured with the real matching engine (same filters: grade, location, cool-off, skills)
    25 · Demand      how much open demand (positions × factor) + recent demand needs s, across the company
    20 · Adjacency   how close s is to skills they already have (prerequisites, related tools, same family)
    15 · Role fit    s is core/common for their role family (or the role they were hired for)
    −5 · Effort      course days including missing prerequisites (30 working days = full penalty)
Each recommendation comes with reasons, an ordered learning path and any open seats in scheduled batches.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from .. import config
from ..db import one, rows, scalar
from . import matching, training
from .errors import BadRequest, NotFound

W = {"unlock": 35, "demand": 25, "adjacency": 20, "role": 15, "effort": 5}
TARGET_LEVEL = 3
IRRELEVANT = {"grade", "location", "cool_off", "already_on_project"}
TIER = {"core": 1.0, "common": 0.7, "optional": 0.4}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- shared context for one run
class Context:
    """Everything that is the same for every employee in one run (open demands, demand pull, graph, courses)."""

    def __init__(self, conn):
        self.conn = conn
        self.today = config.as_of()
        self.ref = matching.get_reference(conn)
        self.cat = training.catalog(conn)
        self.skills = {s["skill_id"]: s for s in rows(conn, "SELECT skill_id, name, type, category FROM skills")}
        self.specs = [matching.load_spec(conn, d["demand_id"])
                      for d in rows(conn, "SELECT demand_id FROM demands WHERE status='Open' ORDER BY demand_id")]
        self.demand_info = {d["demand_id"]: d for d in rows(conn, """SELECT d.demand_id, d.role_title, d.positions, d.needed_by,
                               a.name AS account_name FROM demands d LEFT JOIN accounts a ON a.account_id=d.account_id
                               WHERE d.status='Open'""")}
        self.family_tier = defaultdict(dict)
        for r in rows(conn, "SELECT role_family, skill_id, tier FROM role_family_skills"):
            self.family_tier[r["role_family"]][r["skill_id"]] = r["tier"]
        # demand pull: open (positions × factor; mandatory 1, nice 0.4) + 2 months of the last 6 months' run-rate
        since = (self.today - timedelta(days=180)).isoformat()
        pull = defaultdict(float)
        self.open_positions = defaultdict(float)
        for r in rows(conn, """SELECT ds.skill_id, ds.mandatory, d.status, d.created_on, d.positions,
                                      coalesce(d.overprovision_factor, 1) f
                               FROM demand_skills ds JOIN demands d USING(demand_id)
                               WHERE d.status='Open' OR d.created_on >= ?""", (since,)):
            w = 1.0 if r["mandatory"] else 0.4
            if r["status"] == "Open":
                pull[r["skill_id"]] += w * r["positions"] * r["f"]
                if r["mandatory"]:
                    self.open_positions[r["skill_id"]] += r["positions"]
            else:
                pull[r["skill_id"]] += w * r["positions"] / 6 * 2
        top = max(pull.values(), default=1)
        self.pull = {k: math.sqrt(v / top) for k, v in pull.items()}      # sqrt: keep niche-but-real demand visible
        self.batches = defaultdict(list)                                  # skill → scheduled batches with free seats
        for b in rows(conn, """SELECT b.batch_id, b.skill_id, b.course_id, c.title, b.start_date, b.end_date, b.capacity,
                                      b.enrolled, b.location, b.delivery FROM training_batches b JOIN courses c USING(course_id)
                               WHERE b.status='Scheduled' AND b.start_date > ? AND b.enrolled < b.capacity
                               ORDER BY b.start_date""", (self.today.isoformat(),)):
            self.batches[b["skill_id"]].append(b)


def load_person(conn, emp_id: str) -> dict:
    p = one(conn, """SELECT e.emp_id, e.first_name || ' ' || e.last_name AS name, e.designation, e.grade, e.ibu_id, e.role_family,
                            e.role_family_name, e.hired_role_family, e.total_exp_years, e.base_location, e.open_to_relocate,
                            e.work_mode_preference, e.status, e.reporting_manager_id, e.function,
                            r.current_project_id, r.current_account_id, r.account_residency_months, r.rotation_due,
                            r.planned_rolloff, r.bench_aging_days, r.cool_off_accounts, pr.project_manager_id
                     FROM employees e JOIN residency_snapshot r USING(emp_id)
                     LEFT JOIN projects pr ON pr.project_id = r.current_project_id WHERE e.emp_id=?""", (emp_id,))
    if not p:
        raise NotFound(f"Employee {emp_id} not found")
    p["skills"] = {s["skill_id"]: (s["proficiency"], s["last_used"], bool(s["verified"]))
                   for s in rows(conn, "SELECT skill_id, proficiency, last_used, verified FROM employee_skills WHERE emp_id=?", (emp_id,))}
    p["project_types"] = {r["project_type"] for r in rows(conn, """SELECT DISTINCT p.project_type FROM allocations a
                                                                   JOIN projects p USING(project_id) WHERE a.emp_id=?""", (emp_id,))}
    p["training_end"] = scalar(conn, "SELECT max(end_date) FROM training_enrollments WHERE emp_id=? AND status='in_progress'", (emp_id,))
    p["learning"] = {r["skill_id"] for r in rows(conn, """SELECT skill_id FROM training_enrollments
                                                          WHERE emp_id=? AND status IN ('in_progress','scheduled')""", (emp_id,))}
    p["other_active"] = 0
    return p


# ---------------------------------------------------------------- core
def recommend(ctx: Context, person: dict, top: int = 5) -> dict:
    if person["function"] != "Delivery":
        return {"emp_id": person["emp_id"], "recommendations": [], "relevant_demands": 0, "note": "Not a delivery role"}
    today = ctx.today
    held = person["skills"]
    have = {s for s, v in held.items() if v[0] >= TARGET_LEVEL}

    # 1) unlock analysis over open demands the person is otherwise eligible for
    unlock = defaultdict(lambda: {"eligible": 0, "full": 0, "gain": 0.0, "demands": []})
    relevant = 0
    for spec in ctx.specs:
        base, why = matching.score_candidate(person, spec, ctx.ref, today)
        if why in IRRELEVANT:
            continue
        relevant += 1
        base_full = bool(base) and base["mandatory_met"] == base["mandatory_total"]
        if base_full:
            continue
        for req in spec["mandatory"] + spec["nice"]:
            sid = req["skill_id"]
            if sid in have:
                continue
            boosted = dict(person, skills={**held, sid: (TARGET_LEVEL, today.isoformat(), False)})
            r, _ = matching.score_candidate(boosted, spec, ctx.ref, today)
            if not r:
                continue
            u = unlock[sid]
            gain = r["score"] - (base["score"] if base else 0)
            if not base:
                u["eligible"] += 1
            if r["mandatory_met"] == r["mandatory_total"]:
                u["full"] += 1
            u["gain"] += max(0.0, gain)
            if not base or r["mandatory_met"] == r["mandatory_total"] or gain >= 5:
                u["demands"].append((spec["demand_id"], round(r["score"], 1), r["mandatory_met"] == r["mandatory_total"]))

    # 2) candidate skills: unlockers ∪ role-family skills ∪ graph neighbours of what they know
    fam = ctx.family_tier.get(person["role_family"], {})
    hired = ctx.family_tier.get(person["hired_role_family"], {})
    cands = set(unlock) | set(fam) | {o for s in held for o, _ in ctx.ref.adj.get(s, [])}
    cands = {s for s in cands if s not in have and ctx.skills.get(s, {}).get("category") != "HR"}

    recs = []
    for sid in cands:
        u = unlock.get(sid, {"eligible": 0, "full": 0, "gain": 0.0, "demands": []})
        u_raw = u["full"] + 0.5 * u["eligible"] + u["gain"] / 100
        f_unlock = 1 - math.exp(-u_raw / 2.5)
        f_demand = ctx.pull.get(sid, 0.0)
        best_adj, via = 0.0, None
        for o, strength in ctx.ref.adj.get(sid, []):
            if o in held:
                v = strength * min(1.0, held[o][0] / 3)
                if v > best_adj:
                    best_adj, via = v, o
        if sid in held:                                  # already has it at a low level → strong head start
            best_adj, via = max(best_adj, 0.6 + 0.1 * held[sid][0]), via
        f_role = max(TIER.get(fam.get(sid), 0), 0.8 * TIER.get(hired.get(sid), 0))
        path = learning_path(ctx, person, sid)
        days = sum(step["days"] for step in path)
        f_effort = min(1.0, days / 30)
        score = W["unlock"] * f_unlock + W["demand"] * f_demand + W["adjacency"] * best_adj + W["role"] * f_role - W["effort"] * f_effort
        if score < 8:
            continue
        reasons = []
        if u["full"] or u["eligible"]:
            bits = []
            if u["full"]:
                bits.append(f"fully match {u['full']} open demand{'s' if u['full'] > 1 else ''}")
            if u["eligible"]:
                bits.append(f"become eligible for {u['eligible']} more")
            reasons.append("Would let you " + " and ".join(bits) + ".")
        elif u["gain"] >= 5:
            reasons.append(f"Raises your match score on open demands by {round(u['gain'])} points in total.")
        if ctx.open_positions.get(sid):
            reasons.append(f"{int(ctx.open_positions[sid])} open positions list it as mandatory.")
        if via:
            reasons.append(f"Builds on your {ctx.skills[via]['name']}.")
        elif sid in held:
            reasons.append(f"You're at level {held[sid][0]} — reaching level 3 makes it count for demands.")
        if fam.get(sid) in ("core", "common"):
            reasons.append(f"{fam[sid].capitalize()} skill for {person['role_family_name']}.")
        demands = sorted(u["demands"], key=lambda x: (not x[2], -x[1]))[:5]
        recs.append({
            "skill_id": sid, "skill": ctx.skills[sid]["name"], "type": ctx.skills[sid]["type"], "category": ctx.skills[sid]["category"],
            "score": round(score, 1), "current_level": held.get(sid, (0,))[0],
            "components": {"unlock": round(W["unlock"] * f_unlock, 1), "demand": round(W["demand"] * f_demand, 1),
                           "adjacency": round(W["adjacency"] * best_adj, 1), "role": round(W["role"] * f_role, 1),
                           "effort": -round(W["effort"] * f_effort, 1)},
            "unlocks": {"full": u["full"], "eligible": u["eligible"], "score_gain": round(u["gain"], 1)},
            "demands": [{"demand_id": d, **{k: ctx.demand_info.get(d, {}).get(k) for k in ("role_title", "account_name", "needed_by")},
                         "score_with_skill": s, "full_match": full} for d, s, full in demands],
            "reasons": reasons, "path": path, "training_days": days,
            "open_batches": [{k: b[k] for k in ("batch_id", "title", "start_date", "end_date", "location", "delivery")}
                             | {"seats_left": b["capacity"] - b["enrolled"]} for b in ctx.batches.get(sid, [])[:3]],
            "already_learning": sid in person["learning"],
        })
    recs.sort(key=lambda r: (r["already_learning"], -r["score"]))
    kept, covered = [], set()           # drop a skill that is already a prerequisite step of a higher-ranked path
    for r in recs:
        if r["skill_id"] in covered:
            continue
        kept.append(r)
        covered |= {p["skill_id"] for p in r["path"]}
    recs = kept
    return {"emp_id": person["emp_id"], "name": person["name"], "status": person["status"], "role_family_name": person["role_family_name"],
            "relevant_demands": relevant, "recommendations": recs[:top]}


def learning_path(ctx: Context, person: dict, sid: str) -> list[dict]:
    """Missing prerequisites (not held at level 2+) in order, then the skill itself."""
    held = person["skills"]
    need, stack = [], [sid]
    while stack:
        s = stack.pop()
        for pre in ctx.cat.prereq.get(s, ()):
            if held.get(pre, (0,))[0] < 2 and pre not in need and pre != sid:
                need.append(pre)
                stack.append(pre)
    order = ctx.cat.order(need, {}) + [sid]
    out = []
    for s in order:
        upskill = held.get(s, (0,))[0] > 0
        sp = ctx.cat.course(s, upskill, classroom=False)
        ilt = ctx.cat.course(s, upskill, classroom=True)
        c = ilt or sp
        if not c:
            continue
        out.append({"skill_id": s, "skill": ctx.skills[s]["name"], "is_prerequisite": s != sid,
                    "days": c["duration_days"], "classroom": {"course_id": ilt["course_id"], "title": ilt["title"],
                                                              "days": ilt["duration_days"]} if ilt else None,
                    "self_paced": {"course_id": sp["course_id"], "title": sp["title"], "days": sp["duration_days"]} if sp else None})
    return out


def for_employee(conn, emp_id: str, top: int = 5) -> dict:
    return recommend(Context(conn), load_person(conn, emp_id), top)


# ---------------------------------------------------------------- self-service: start learning
def start_learning(conn, emp_id: str, skill_id: str, mode: str = "self-paced", batch_id: str | None = None) -> dict:
    ctx = Context(conn)
    if skill_id not in ctx.skills:
        raise NotFound("Unknown skill")
    if one(conn, "SELECT 1 FROM training_enrollments WHERE emp_id=? AND skill_id=? AND status IN ('in_progress','scheduled')",
           (emp_id, skill_id)):
        raise BadRequest(f"You're already enrolled for {ctx.skills[skill_id]['name']}")
    person = load_person(conn, emp_id)
    path = learning_path(ctx, person, skill_id)
    ne = scalar(conn, "SELECT max(CAST(substr(enrollment_id, 3) AS INTEGER)) FROM training_enrollments") or 0
    start = training.next_workday(ctx.today + timedelta(days=1))
    created = []
    for step in path:
        if step["skill_id"] == skill_id and batch_id:
            b = one(conn, """SELECT * FROM training_batches WHERE batch_id=? AND skill_id=? AND status='Scheduled'
                             AND enrolled < capacity""", (batch_id, skill_id))
            if not b:
                raise BadRequest("That batch is full, started or not for this skill")
            s, e, course, m = date.fromisoformat(b["start_date"]), date.fromisoformat(b["end_date"]), b["course_id"], "instructor-led"
            if s < start:
                raise BadRequest("Finish the prerequisites first — that batch starts too early")
            conn.execute("UPDATE training_batches SET enrolled = enrolled + 1 WHERE batch_id=?", (batch_id,))
        else:
            c = step["self_paced"] if mode == "self-paced" or not step["classroom"] else step["classroom"]
            if not c:
                c = step["classroom"] or step["self_paced"]
            course, m = c["course_id"], ("self-paced" if c is step["self_paced"] else "instructor-led")
            s, e = start, training.end_of(start, c["days"])
        ne += 1
        conn.execute("""INSERT INTO training_enrollments (enrollment_id, emp_id, course_id, skill_id, mode, start_date, end_date,
                        status, assessment_score, source, batch_id) VALUES (?,?,?,?,?,?,?,?, NULL, 'self_selected', ?)""",
                     (f"EN{ne:06d}", emp_id, course, step["skill_id"], m, s.isoformat(), e.isoformat(),
                      "in_progress" if s <= ctx.today + timedelta(days=1) else "scheduled",
                      batch_id if step["skill_id"] == skill_id and batch_id else None))
        created.append({"enrollment_id": f"EN{ne:06d}", "skill": step["skill"], "mode": m, "start_date": s.isoformat(),
                        "end_date": e.isoformat()})
        start = training.next_workday(e + timedelta(days=1))
    conn.commit()
    return {"skill": ctx.skills[skill_id]["name"], "enrollments": created, "ready_by": created[-1]["end_date"] if created else None}


# ---------------------------------------------------------------- bench-wide snapshot (manager view)
def refresh_bench(conn, top: int = 3) -> dict:
    ctx = Context(conn)
    people = [r["emp_id"] for r in rows(conn, "SELECT emp_id FROM employees WHERE function='Delivery' AND status IN ('bench','training')")]
    conn.execute("DELETE FROM upskill_recs")
    n = 0
    for eid in people:
        res = recommend(ctx, load_person(conn, eid), top)
        for i, r in enumerate(res["recommendations"], 1):
            conn.execute("""INSERT INTO upskill_recs (emp_id, rank, skill_id, score, unlock_full, unlock_eligible, training_days,
                            relevant_demands, detail, computed_on) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                         (eid, i, r["skill_id"], r["score"], r["unlocks"]["full"], r["unlocks"]["eligible"], r["training_days"],
                          res["relevant_demands"], json.dumps(r), _now()))
            n += 1
    conn.commit()
    return {"employees": len(people), "recommendations": n, "computed_on": _now()}


def bench_overview(conn, ibu_id=None, role_family=None, min_aging: int | None = None, page=1, page_size=25) -> dict:
    if not scalar(conn, "SELECT count(*) FROM upskill_recs"):
        refresh_bench(conn)
    where, p = ["e.status IN ('bench','training')", "e.function='Delivery'"], []
    if ibu_id:
        where.append("e.ibu_id=?"); p.append(ibu_id)
    if role_family:
        where.append("e.role_family=?"); p.append(role_family)
    if min_aging:
        where.append("r.bench_aging_days >= ?"); p.append(int(min_aging))
    w = " AND ".join(where)
    skills = rows(conn, f"""SELECT u.skill_id, s.name AS skill, count(*) AS people, round(avg(u.score),1) AS avg_score,
                                   sum(u.unlock_full) AS unlock_full, sum(u.unlock_eligible) AS unlock_eligible,
                                   round(avg(u.training_days),1) AS avg_days
                            FROM upskill_recs u JOIN skills s USING(skill_id) JOIN employees e USING(emp_id)
                            JOIN residency_snapshot r USING(emp_id) WHERE {w} GROUP BY u.skill_id
                            ORDER BY people DESC, avg_score DESC LIMIT 15""", p)
    total = scalar(conn, f"SELECT count(*) FROM employees e JOIN residency_snapshot r USING(emp_id) WHERE {w}", p)
    page_size = max(1, min(int(page_size), 100))
    people = rows(conn, f"""SELECT e.emp_id, e.first_name || ' ' || e.last_name AS name, e.designation, e.grade, e.ibu_id,
                                   e.role_family_name, e.status, e.base_location, r.bench_aging_days
                            FROM employees e JOIN residency_snapshot r USING(emp_id) WHERE {w}
                            ORDER BY r.bench_aging_days DESC, e.emp_id LIMIT ? OFFSET ?""",
                    p + [page_size, (max(1, int(page)) - 1) * page_size])
    if people:
        ids = [x["emp_id"] for x in people]
        recs = defaultdict(list)
        for r in rows(conn, f"""SELECT emp_id, rank, detail, relevant_demands FROM upskill_recs
                                WHERE emp_id IN ({','.join('?' * len(ids))}) ORDER BY emp_id, rank""", ids):
            d = json.loads(r["detail"])
            recs[r["emp_id"]].append({k: d[k] for k in ("skill_id", "skill", "score", "unlocks", "training_days", "reasons", "already_learning")})
        for x in people:
            x["recommendations"] = recs.get(x["emp_id"], [])
    computed = scalar(conn, "SELECT max(computed_on) FROM upskill_recs")
    return {"computed_on": computed, "top_skills": skills, "total": total, "page": page, "page_size": page_size, "people": people}
