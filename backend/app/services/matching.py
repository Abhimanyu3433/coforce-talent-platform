"""Matching engine: rank employees for a demand.

Pipeline
  1. Candidate pool   bench · training · rolling off before needed_by+30d · rotation-due (24m+) [· any allocated, optional]
  2. Hard filters     grade band (one grade below allowed) · location / remote / relocation · cool-off · min mandatory coverage
  3. Score 0-100      mandatory 40 · nice 15 · experience 10 · role 10 · context 10 · availability 10 · location 5  (+≤5 bench priority)
  4. Gap analysis     missing / under-level mandatory skills → course + training days → ready-by date

Every component is returned in `breakdown`, so rankings are explainable.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone

from .. import config
from ..db import one, rows, scalar
from .errors import BadRequest, Forbidden, NotFound

GRADES = ["A1", "A2", "A3", "B1", "B2", "C1", "C2", "D1", "D2"]
WEIGHTS = {"mandatory": 40, "nice": 15, "experience": 10, "role": 10, "context": 10, "availability": 10, "location": 5}
MIN_MANDATORY_COVERAGE = 0.5
ROLLOFF_WINDOW_DAYS = 30
MAX_PARTIAL_CREDIT = 0.45
GAP_PENALTY_MISSING = 4.0        # points per mandatory skill the person does not have at all
GAP_PENALTY_UNDER = 2.0          # points per mandatory skill held below the required level
APPROVAL_AVAILABILITY_FACTOR = 0.85
RELATED_FAMILIES = {
    "DATA_ENG": {"BI", "DATA_SCI", "GENAI"}, "DATA_SCI": {"GENAI", "DATA_ENG", "BI"}, "GENAI": {"DATA_SCI", "DATA_ENG"},
    "BI": {"DATA_ENG", "DATA_SCI"}, "JAVA_FS": {"FRONTEND", "DOTNET", "INS_PLATFORMS", "MAINFRAME"},
    "DOTNET": {"JAVA_FS", "FRONTEND"}, "FRONTEND": {"JAVA_FS", "MOBILE", "DOTNET"}, "MOBILE": {"FRONTEND"},
    "DEVOPS": {"APP_SUPPORT", "SECURITY"}, "QA": {"APP_SUPPORT", "BA"}, "APP_SUPPORT": {"DEVOPS", "SERVICENOW", "QA"},
    "SERVICENOW": {"APP_SUPPORT"}, "SECURITY": {"DEVOPS"}, "BA": {"PM", "QA"}, "PM": {"BA"},
    "MAINFRAME": {"JAVA_FS"}, "INS_PLATFORMS": {"JAVA_FS"}, "SAP": set(), "SALESFORCE": set(),
}
ACTIVE_STATUSES = ("shortlisted", "nominated", "release_requested", "release_approved")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _d(x) -> date | None:
    return date.fromisoformat(x[:10]) if x else None


def parse_grade_band(band: str | None) -> tuple[str, str]:
    if not band:
        return "A1", "D2"
    parts = band.split("-")
    return parts[0], parts[-1]


# ------------------------------------------------------------------ static reference data (cached per DB)
class Reference:
    def __init__(self, conn):
        self.skill = {s["skill_id"]: s for s in rows(conn, "SELECT skill_id, name, type FROM skills")}
        self.adj: dict[str, list[tuple[str, float]]] = {}
        for r in rows(conn, "SELECT skill_a, skill_b, strength FROM skill_relations"):
            self.adj.setdefault(r["skill_a"], []).append((r["skill_b"], r["strength"]))
            self.adj.setdefault(r["skill_b"], []).append((r["skill_a"], r["strength"]))
        self.course, self.adv_course = {}, {}
        for c in rows(conn, "SELECT course_id, title, skill_id, level, mode, duration_days FROM courses"):
            if c["mode"] == "instructor-led" and c["level"] == "Intermediate":
                self.course[c["skill_id"]] = c
            elif c["level"] == "Advanced":
                self.adv_course[c["skill_id"]] = c
            else:
                self.course.setdefault(c["skill_id"], c)

    def name(self, sid):
        return self.skill[sid]["name"] if sid in self.skill else sid


_REF: dict[str, Reference] = {}


def get_reference(conn) -> Reference:
    key = conn.execute("PRAGMA database_list").fetchone()[2]
    if key not in _REF:
        _REF[key] = Reference(conn)
    return _REF[key]


# ------------------------------------------------------------------ demand spec
def load_spec(conn, demand_id: str) -> dict:
    d = one(conn, "SELECT * FROM demands WHERE demand_id=?", (demand_id,))
    if not d:
        raise NotFound(f"Demand {demand_id} not found")
    sk = rows(conn, "SELECT skill_id, mandatory, min_proficiency, weight FROM demand_skills WHERE demand_id=?", (demand_id,))
    gmin, gmax = parse_grade_band(d["grade_band"])
    spec = {
        "demand_id": demand_id, "ibu_id": d["ibu_id"], "account_id": d["account_id"], "project_id": d["project_id"],
        "role_family": d["role_family"], "grade_min": gmin, "grade_max": gmax,
        "exp_min": d["exp_min"], "exp_max": d["exp_max"], "locations": (d["locations"] or "").split(";"),
        "work_mode": d["work_mode"], "remote_allowed": bool(d["remote_allowed"]), "needed_by": d["needed_by"],
        "positions": d["positions"], "overprovision_factor": d["overprovision_factor"] or 1.0,
        "mandatory": [s for s in sk if s["mandatory"]], "nice": [s for s in sk if not s["mandatory"]],
    }
    return with_context(conn, spec)


def with_context(conn, spec: dict) -> dict:
    """Attach project type + domain skills used for the context score."""
    ref = get_reference(conn)
    spec["project_type"] = None
    domain = {s["skill_id"] for s in spec["nice"] + spec["mandatory"] if ref.skill.get(s["skill_id"], {}).get("type") == "domain"}
    if spec.get("project_id"):
        p = one(conn, "SELECT project_type FROM projects WHERE project_id=?", (spec["project_id"],))
        spec["project_type"] = p["project_type"] if p else None
        domain |= {r["skill_id"] for r in rows(conn, """SELECT ps.skill_id FROM project_skills ps JOIN skills s USING(skill_id)
                                                         WHERE ps.project_id=? AND s.type='domain'""", (spec["project_id"],))}
    spec["domain_skills"] = domain
    return spec


# ------------------------------------------------------------------ candidate pool
def load_pool(conn, spec: dict, include_all_allocated: bool = False) -> list[dict]:
    horizon = (_d(spec["needed_by"]) + timedelta(days=ROLLOFF_WINDOW_DAYS)).isoformat()
    alloc_cond = "e.status='allocated'" if include_all_allocated else \
        "(e.status='allocated' AND (r.rotation_due=1 OR r.planned_rolloff <= ?))"
    params = [] if include_all_allocated else [horizon]
    pool = rows(conn, f"""
        SELECT e.emp_id, e.first_name || ' ' || e.last_name AS name, e.designation, e.grade, e.ibu_id, e.role_family,
               e.role_family_name, e.hired_role_family, e.total_exp_years, e.base_location, e.open_to_relocate,
               e.work_mode_preference, e.status, e.reporting_manager_id,
               r.current_project_id, r.current_account_id, r.account_residency_months, r.rotation_due,
               r.planned_rolloff, r.bench_aging_days, r.cool_off_accounts, pr.project_manager_id
        FROM employees e JOIN residency_snapshot r USING(emp_id)
        LEFT JOIN projects pr ON pr.project_id = r.current_project_id
        WHERE e.function='Delivery' AND (e.status IN ('bench','training') OR {alloc_cond})""", params)
    if not pool:
        return []
    ids = [p["emp_id"] for p in pool]
    by = {p["emp_id"]: p for p in pool}
    for p in pool:
        p["skills"], p["project_types"] = {}, set()
    q = ",".join("?" * len(ids))
    for s in conn.execute(f"SELECT emp_id, skill_id, proficiency, last_used, verified FROM employee_skills WHERE emp_id IN ({q})", ids):
        by[s[0]]["skills"][s[1]] = (s[2], s[3], bool(s[4]))
    for a in conn.execute(f"""SELECT DISTINCT a.emp_id, p.project_type FROM allocations a JOIN projects p USING(project_id)
                              WHERE a.emp_id IN ({q})""", ids):
        by[a[0]]["project_types"].add(a[1])
    for t in conn.execute(f"""SELECT emp_id, max(end_date) FROM training_enrollments
                              WHERE status='in_progress' AND emp_id IN ({q}) GROUP BY emp_id""", ids):
        by[t[0]]["training_end"] = t[1]
    active = rows(conn, f"""SELECT emp_id, count(*) n FROM matches WHERE status IN ({','.join('?' * len(ACTIVE_STATUSES))})
                            {'AND demand_id != ?' if spec.get('demand_id') else ''} GROUP BY emp_id""",
                  list(ACTIVE_STATUSES) + ([spec["demand_id"]] if spec.get("demand_id") else []))
    for a in active:
        if a["emp_id"] in by:
            by[a["emp_id"]]["other_active"] = a["n"]
    return pool


# ------------------------------------------------------------------ scoring
def _skill_credit(cand_skills: dict, sid: str, min_prof: int, ref: Reference, today: date):
    if sid in cand_skills:
        prof, last_used, verified = cand_skills[sid]
        lvl = 1.0 if prof >= min_prof else 0.9 * prof / min_prof
        age = (today - _d(last_used)).days / 365 if last_used else 5
        rec = 1.0 if age <= 2 else (0.85 if age <= 4 else 0.7)
        ver = 1.0 if verified else 0.85
        return lvl * rec * ver, {"skill_id": sid, "name": ref.name(sid), "required": min_prof, "actual": prof,
                                 "met": prof >= min_prof, "verified": verified, "last_used": last_used, "via": None}
    best, via = 0.0, None
    for other, strength in ref.adj.get(sid, []):
        if other in cand_skills:
            v = 0.5 * strength * min(1.0, cand_skills[other][0] / 3)
            if v > best:
                best, via = v, other
    return min(best, MAX_PARTIAL_CREDIT), {"skill_id": sid, "name": ref.name(sid), "required": min_prof, "actual": 0,
                                           "met": False, "verified": False, "last_used": None,
                                           "via": ref.name(via) if via else None}


def _coverage(cand, reqs, ref, today):
    if not reqs:
        return 1.0, []
    tot_w, acc, detail = 0.0, 0.0, []
    for r in reqs:
        w = r.get("weight") or 1.0
        credit, info = _skill_credit(cand["skills"], r["skill_id"], r.get("min_proficiency") or 2, ref, today)
        tot_w += w
        acc += w * credit
        detail.append(info)
    return acc / tot_w, detail


def _experience_fit(exp, lo, hi):
    if lo is None or hi is None:
        return 1.0
    if lo <= exp <= hi:
        return 1.0
    if exp < lo:
        return max(0.0, 1 - (lo - exp) * 0.3)
    return max(0.4, 1 - (exp - hi) * 0.15)


def _role_fit(cand, fam):
    if not fam:
        return 1.0
    if cand["role_family"] == fam:
        return 1.0
    if cand["hired_role_family"] == fam:
        return 0.7
    if fam in RELATED_FAMILIES.get(cand["role_family"], set()):
        return 0.45
    return 0.1


def _availability(cand, spec, today):
    needed = _d(spec["needed_by"])
    s = cand["status"]
    if s == "bench":
        return today, "bench", "Available now"
    if s == "training":
        end = _d(cand.get("training_end")) or today
        end = max(end, today)
        return end, "training", f"In training until {end.isoformat()}"
    roll = _d(cand["planned_rolloff"])
    if roll and roll <= needed + timedelta(days=ROLLOFF_WINDOW_DAYS):
        avail = max(roll + timedelta(days=1), today)
        return avail, "rolling_off", f"Rolls off {roll.isoformat()}"
    if cand["rotation_due"]:
        return max(needed, today), "rotation", f"Rotation candidate ({cand['account_residency_months']:.0f} m on account)"
    return max(roll + timedelta(days=1), today) if roll else needed, "allocated", "Allocated (needs release)"


def _location_fit(cand, spec):
    locs = [l for l in spec["locations"] if l]
    if not locs or cand["base_location"] in locs:
        return 1.0, None
    if spec["remote_allowed"] and cand["work_mode_preference"] in ("Any", "Remote", "Hybrid"):
        return 0.9, "remote"
    if cand["open_to_relocate"]:
        return 0.6, "relocation"
    return None, None


def _context_fit(cand, spec):
    v = 0.35 if cand["ibu_id"] == spec.get("ibu_id") else 0.0
    dom = spec.get("domain_skills") or set()
    if dom:
        v += 0.35 * len(dom & set(cand["skills"])) / len(dom)
    if spec.get("project_type") and spec["project_type"] in cand["project_types"]:
        v += 0.30
    elif not spec.get("project_type"):
        v += 0.15
    return min(1.0, v)


def fit_label(score, mandatory_met, mandatory_total):
    if mandatory_met == mandatory_total and score >= 80:
        return "Strong"
    if score >= 68:
        return "Good"
    if score >= 52:
        return "Trainable"
    return "Weak"


def score_candidate(cand, spec, ref, today):
    """Return (result, None) or (None, exclusion_reason)."""
    gi = GRADES.index(cand["grade"])
    if not (GRADES.index(spec["grade_min"]) - 1 <= gi <= GRADES.index(spec["grade_max"])):
        return None, "grade"
    if spec.get("project_id") and cand["current_project_id"] == spec["project_id"]:
        return None, "already_on_project"
    cool = dict(x.split(":") for x in (cand["cool_off_accounts"] or "").split(";") if x)
    if spec.get("account_id") in cool:
        return None, "cool_off"
    loc_fit, loc_note = _location_fit(cand, spec)
    if loc_fit is None:
        return None, "location"
    m_cov, m_detail = _coverage(cand, spec["mandatory"], ref, today)
    if m_cov < MIN_MANDATORY_COVERAGE:
        return None, "skills"
    n_cov, n_detail = _coverage(cand, spec["nice"], ref, today)
    avail, pool, avail_label = _availability(cand, spec, today)
    needed = _d(spec["needed_by"])
    late = (avail - needed).days
    fits = {
        "mandatory": m_cov, "nice": n_cov,
        "experience": _experience_fit(cand["total_exp_years"], spec["exp_min"], spec["exp_max"]),
        "role": _role_fit(cand, spec.get("role_family")),
        "context": _context_fit(cand, spec),
        "availability": 1.0 if late <= 0 else max(0.0, 1 - late / 60),
        "location": loc_fit,
    }
    if pool in ("rotation", "allocated"):
        fits["availability"] *= APPROVAL_AVAILABILITY_FACTOR      # release not guaranteed
    breakdown = {k: {"fit": round(v, 3), "points": round(WEIGHTS[k] * v, 1), "max": WEIGHTS[k]} for k, v in fits.items()}
    bonus = 0.0
    if cand["status"] in ("bench", "training"):
        bonus = 2 + min(3.0, (cand["bench_aging_days"] or 0) / 30)
    breakdown["bench_priority"] = {"fit": None, "points": round(bonus, 1), "max": 5}
    missing = sum(1 for g in m_detail if g["actual"] == 0)
    under = sum(1 for g in m_detail if 0 < g["actual"] < g["required"])
    breakdown["gap_penalty"] = {"fit": None, "points": -(GAP_PENALTY_MISSING * missing + GAP_PENALTY_UNDER * under), "max": 0}
    score = max(0.0, min(100.0, sum(b["points"] for b in breakdown.values())))

    # gaps → training plan
    gaps, train_days = [], 0
    for g in m_detail:
        if g["met"]:
            continue
        upskill = g["actual"] > 0
        c = (ref.adv_course.get(g["skill_id"]) if upskill else None) or ref.course.get(g["skill_id"])
        days = c["duration_days"] if c else 10
        days = math.ceil(days * 0.5) if upskill else days
        train_days += days
        gaps.append({**g, "kind": "upskill" if upskill else "new", "course_id": c["course_id"] if c else None,
                     "course_title": c["title"] if c else None, "training_days": days})
    calendar = math.ceil(train_days * 7 / 5)
    ready_by = avail + timedelta(days=calendar)
    met = sum(1 for g in m_detail if g["met"])
    flags = []
    if cand["rotation_due"]:
        flags.append("rotation_due")
    if loc_note:
        flags.append(loc_note)
    if gi < GRADES.index(spec["grade_min"]):
        flags.append("stretch_grade")
    if cand.get("other_active"):
        flags.append("in_other_pipelines")
    requires_approval = pool in ("rolling_off", "rotation", "allocated") and cand["status"] == "allocated"
    return {
        "emp_id": cand["emp_id"], "name": cand["name"], "designation": cand["designation"], "grade": cand["grade"],
        "ibu_id": cand["ibu_id"], "role_family_name": cand["role_family_name"], "total_exp_years": cand["total_exp_years"],
        "base_location": cand["base_location"], "status": cand["status"], "current_project_id": cand["current_project_id"],
        "current_account_id": cand["current_account_id"], "account_residency_months": cand["account_residency_months"],
        "rotation_due": bool(cand["rotation_due"]), "bench_aging_days": cand["bench_aging_days"],
        "pool": pool, "availability_date": avail.isoformat(), "availability_label": avail_label,
        "requires_approval": requires_approval,
        "approver_id": (cand["project_manager_id"] or cand["reporting_manager_id"]) if requires_approval else None,
        "score": round(score, 1), "fit_label": fit_label(score, met, len(m_detail)), "breakdown": breakdown,
        "mandatory_met": met, "mandatory_total": len(m_detail), "matched": m_detail,
        "nice": n_detail, "gaps": gaps, "training_days": train_days, "ready_by": ready_by.isoformat(),
        "ready_in_time": ready_by <= needed, "other_active": cand.get("other_active", 0), "flags": flags,
    }, None


def run(conn, spec: dict, limit: int = 50, include_all_allocated: bool = False) -> dict:
    if not spec["mandatory"]:
        raise BadRequest("A demand needs at least one mandatory skill")
    ref = get_reference(conn)
    today = config.as_of()
    pool = load_pool(conn, spec, include_all_allocated)
    results, excluded = [], {}
    for c in pool:
        r, why = score_candidate(c, spec, ref, today)
        if r:
            results.append(r)
        else:
            excluded[why] = excluded.get(why, 0) + 1
    results.sort(key=lambda r: (-r["score"], r["availability_date"], r["emp_id"]))
    for i, r in enumerate(results, 1):
        r["rank"] = i
    full = [r for r in results if r["mandatory_met"] == r["mandatory_total"]]
    return {
        "pool_size": len(pool), "eligible": len(results), "excluded": excluded,
        "fully_matching": len(full),
        "fully_matching_in_time": sum(1 for r in full if r["availability_date"] <= spec["needed_by"]),
        "ready_in_time_with_training": sum(1 for r in results if r["ready_in_time"]),
        "by_pool": {p: sum(1 for r in results if r["pool"] == p) for p in ("bench", "training", "rolling_off", "rotation", "allocated")},
        "candidates": results[:limit] if limit else [],
    }


def supply_check(conn, spec: dict) -> dict:
    """How many internal people fit, and how much relaxing each mandatory skill would help (used by the JD form/chatbot)."""
    base = run(conn, spec, limit=5)
    ref = get_reference(conn)
    relax = []
    if len(spec["mandatory"]) > 1:
        for s in spec["mandatory"]:
            alt = dict(spec)
            alt["mandatory"] = [m for m in spec["mandatory"] if m["skill_id"] != s["skill_id"]]
            alt["nice"] = spec["nice"] + [{**s, "weight": 0.5}]
            r = run(conn, alt, limit=0)
            relax.append({"skill_id": s["skill_id"], "name": ref.name(s["skill_id"]),
                          "fully_matching_if_optional": r["fully_matching"],
                          "gain": r["fully_matching"] - base["fully_matching"]})
        relax.sort(key=lambda x: -x["gain"])
    needed = max(1, math.ceil(spec.get("positions", 1) * (spec.get("overprovision_factor") or 1)))
    return {k: base[k] for k in ("pool_size", "eligible", "fully_matching", "fully_matching_in_time",
                                 "ready_in_time_with_training", "by_pool", "excluded")} | {
        "needed": needed, "relaxations": relax,
        "verdict": "healthy" if base["fully_matching_in_time"] >= needed else
                   ("trainable" if base["ready_in_time_with_training"] >= needed else "scarce"),
        "top": [{k: c[k] for k in ("emp_id", "name", "score", "fit_label", "availability_label")} for c in base["candidates"]],
    }


# ------------------------------------------------------------------ persistence
def run_and_store(conn, demand_id: str, user_id: str | None = None, include_all_allocated: bool = False) -> dict:
    spec = load_spec(conn, demand_id)
    keep = max(25, math.ceil(spec["positions"] * (spec["overprovision_factor"] or 1) * 3))
    res = run(conn, spec, limit=keep, include_all_allocated=include_all_allocated)
    now = _now()
    existing = {r["emp_id"]: r for r in rows(conn, "SELECT emp_id, status FROM matches WHERE demand_id=?", (demand_id,))}
    fresh = {c["emp_id"] for c in res["candidates"]}
    for emp, r in existing.items():
        if emp not in fresh and r["status"] == "suggested":
            conn.execute("DELETE FROM matches WHERE demand_id=? AND emp_id=?", (demand_id, emp))
    for c in res["candidates"]:
        payload = (c["rank"], c["score"], c["fit_label"], json.dumps(c), c["availability_date"], c["pool"],
                   int(c["requires_approval"]), c["approver_id"], now)
        if c["emp_id"] in existing:
            conn.execute("""UPDATE matches SET rank=?, score=?, fit_label=?, result=?, availability_date=?, pool=?,
                            requires_approval=?, approver_id=?, computed_on=? WHERE demand_id=? AND emp_id=?""",
                         payload + (demand_id, c["emp_id"]))
        else:
            conn.execute("""INSERT INTO matches (rank, score, fit_label, result, availability_date, pool, requires_approval,
                            approver_id, computed_on, demand_id, emp_id, status) VALUES (?,?,?,?,?,?,?,?,?,?,?, 'suggested')""",
                         payload + (demand_id, c["emp_id"]))
    conn.execute("UPDATE demands SET last_matched_on=?, last_matched_by=? WHERE demand_id=?", (now, user_id, demand_id))
    conn.commit()
    stats = {k: v for k, v in res.items() if k != "candidates"}
    conn.execute("UPDATE demands SET match_stats=? WHERE demand_id=?", (json.dumps(stats), demand_id))
    conn.commit()
    return list_matches(conn, demand_id) | {"stats": stats}


def list_matches(conn, demand_id: str) -> dict:
    d = one(conn, "SELECT demand_id, match_stats, last_matched_on FROM demands WHERE demand_id=?", (demand_id,))
    if not d:
        raise NotFound(f"Demand {demand_id} not found")
    out = []
    for m in rows(conn, """SELECT m.*, a.first_name || ' ' || a.last_name AS approver_name
                           FROM matches m LEFT JOIN employees a ON a.emp_id = m.approver_id
                           WHERE m.demand_id=? ORDER BY m.rank""", (demand_id,)):
        r = json.loads(m["result"])
        r.update({"status": m["status"], "note": m["note"], "approver_name": m["approver_name"],
                  "updated_on": m["updated_on"], "updated_by": m["updated_by"]})
        out.append(r)
    counts = {}
    for r in out:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"demand_id": demand_id, "computed_on": d["last_matched_on"], "status_counts": counts,
            "stats": json.loads(d["match_stats"]) if d["match_stats"] else None, "candidates": out}


# ------------------------------------------------------------------ pipeline actions & release approvals
TRANSITIONS = {
    "shortlist": {"suggested", "rejected"},
    "nominate": {"suggested", "shortlisted"},
    "reject": {"suggested", "shortlisted", "nominated", "release_requested", "release_approved"},
    "select": {"nominated", "release_approved"},
    "client_reject": {"nominated", "release_approved"},
    "reset": {"shortlisted", "rejected", "release_rejected", "client_rejected"},
}
RESULT_STATUS = {"shortlist": "shortlisted", "reject": "rejected", "select": "selected",
                 "client_reject": "client_rejected", "reset": "suggested"}


def _log(conn, demand_id, emp_id, action, by, note):
    conn.execute("INSERT INTO match_events (demand_id, emp_id, action, actor_id, at, note) VALUES (?,?,?,?,?,?)",
                 (demand_id, emp_id, action, by, _now(), note))


def act(conn, demand_id: str, emp_id: str, action: str, user: dict, note: str | None = None) -> dict:
    if action not in TRANSITIONS:
        raise BadRequest(f"Unknown action '{action}'")
    m = one(conn, "SELECT * FROM matches WHERE demand_id=? AND emp_id=?", (demand_id, emp_id))
    if not m:
        raise NotFound("Candidate is not in this demand's match list — run matching first")
    if m["status"] not in TRANSITIONS[action]:
        raise BadRequest(f"Cannot {action.replace('_', ' ')} a candidate who is '{m['status'].replace('_', ' ')}'")
    if action == "nominate":
        new = "release_requested" if m["requires_approval"] else "nominated"
    else:
        new = RESULT_STATUS[action]
    conn.execute("UPDATE matches SET status=?, note=?, updated_by=?, updated_on=? WHERE demand_id=? AND emp_id=?",
                 (new, note, user["user_id"], _now(), demand_id, emp_id))
    _log(conn, demand_id, emp_id, action, user["user_id"], note)
    if new == "selected":
        d = one(conn, "SELECT positions FROM demands WHERE demand_id=?", (demand_id,))
        n = scalar(conn, "SELECT count(*) FROM matches WHERE demand_id=? AND status='selected'", (demand_id,))
        if n >= d["positions"]:
            conn.execute("UPDATE demands SET status='Fulfilled - Internal' WHERE demand_id=?", (demand_id,))
    conn.commit()
    return {"demand_id": demand_id, "emp_id": emp_id, "status": new}


def _can_approve(conn, m: dict, user: dict) -> bool:
    if m["approver_id"] == user["user_id"]:
        return True
    if user["app_role"] == "manager":   # delegate: any manager of the candidate's current IBU
        ibu = scalar(conn, """SELECT p.ibu_id FROM residency_snapshot r JOIN projects p ON p.project_id=r.current_project_id
                              WHERE r.emp_id=?""", (m["emp_id"],))
        return ibu == user["ibu_id"]
    return False


def decide_release(conn, demand_id: str, emp_id: str, user: dict, approve: bool, note: str | None = None) -> dict:
    m = one(conn, "SELECT * FROM matches WHERE demand_id=? AND emp_id=?", (demand_id, emp_id))
    if not m or m["status"] != "release_requested":
        raise BadRequest("No pending release request for this candidate")
    if not _can_approve(conn, m, user):
        raise Forbidden("Only the candidate's project manager (or a manager of that IBU) can decide this release")
    new = "release_approved" if approve else "release_rejected"
    conn.execute("UPDATE matches SET status=?, note=?, updated_by=?, updated_on=? WHERE demand_id=? AND emp_id=?",
                 (new, note, user["user_id"], _now(), demand_id, emp_id))
    _log(conn, demand_id, emp_id, "approve_release" if approve else "reject_release", user["user_id"], note)
    conn.commit()
    return {"demand_id": demand_id, "emp_id": emp_id, "status": new}


def pending_approvals(conn, user: dict) -> list[dict]:
    cond, params = "m.approver_id = ?", [user["user_id"]]
    if user["app_role"] == "manager":
        cond = "(m.approver_id = ? OR cp.ibu_id = ?)"
        params.append(user["ibu_id"])
    out = rows(conn, f"""SELECT m.demand_id, m.emp_id, m.score, m.fit_label, m.availability_date, m.pool, m.updated_on, m.note,
                                m.approver_id, e.first_name || ' ' || e.last_name AS emp_name, e.designation, e.grade,
                                cp.name AS current_project, ca.name AS current_account, r.account_residency_months,
                                r.planned_rolloff, d.role_title, d.positions, d.needed_by, dp.name AS demand_project,
                                da.name AS demand_account, rq.first_name || ' ' || rq.last_name AS requested_by
                         FROM matches m JOIN employees e ON e.emp_id=m.emp_id
                         JOIN residency_snapshot r ON r.emp_id=m.emp_id
                         LEFT JOIN projects cp ON cp.project_id=r.current_project_id
                         LEFT JOIN accounts ca ON ca.account_id=r.current_account_id
                         JOIN demands d ON d.demand_id=m.demand_id
                         LEFT JOIN projects dp ON dp.project_id=d.project_id
                         LEFT JOIN accounts da ON da.account_id=d.account_id
                         LEFT JOIN employees rq ON rq.emp_id=m.updated_by
                         WHERE m.status='release_requested' AND {cond} ORDER BY m.updated_on""", params)
    return out
