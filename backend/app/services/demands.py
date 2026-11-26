"""Demand (job requirement) CRUD. Stage 4's chatbot produces the same payload as the form."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

from .. import config
from ..db import one, rows, scalar
from . import matching
from .errors import BadRequest, NotFound
from .skills import get_index

WORK_MODES = {"Hybrid", "Work from Office", "Remote"}
PRIORITIES = {"High", "Medium", "Low"}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _skill_list(conn, items, mandatory: bool) -> list[dict]:
    """items: [{'skill': 'pyspark', 'min_proficiency': 3}] or ['pyspark', ...] → resolved demand-skill rows."""
    idx = get_index(conn)
    out, seen = [], set()
    for it in items or []:
        it = {"skill": it} if isinstance(it, str) else it
        name = (it.get("skill") or "").strip()
        if not name:
            continue
        s = idx.get(name) or idx.normalize(name)["match"]
        if not s:
            cands = [c["name"] for c in idx.normalize(name)["candidates"]]
            raise BadRequest({"message": f"Unknown skill '{name}'", "candidates": cands})
        if s["skill_id"] in seen:
            continue
        seen.add(s["skill_id"])
        mp = int(it.get("min_proficiency") or (3 if mandatory else 2))
        if not 1 <= mp <= 5:
            raise BadRequest("min_proficiency must be 1-5")
        out.append({"skill_id": s["skill_id"], "name": s["name"], "mandatory": mandatory, "min_proficiency": mp,
                    "weight": float(it.get("weight") or (1.0 if mandatory else 0.5))})
    return out


def normalize_payload(conn, p: dict) -> dict:
    """Validate a demand payload and fill derived fields. Raises BadRequest with a readable message."""
    p = dict(p)
    if p.get("project_id"):
        pr = one(conn, "SELECT project_id, account_id, ibu_id, work_mode, shift, delivery_locations FROM projects WHERE project_id=?",
                 (p["project_id"],))
        if not pr:
            raise BadRequest("Unknown project")
        p["account_id"], p["ibu_id"] = pr["account_id"], pr["ibu_id"]
        p.setdefault("work_mode", pr["work_mode"])
        p.setdefault("shift", pr["shift"])
        if not p.get("locations"):
            p["locations"] = pr["delivery_locations"].split(";")
    elif p.get("account_id"):
        a = one(conn, "SELECT ibu_id FROM accounts WHERE account_id=?", (p["account_id"],))
        if not a:
            raise BadRequest("Unknown account")
        p["ibu_id"] = a["ibu_id"]
    missing = [k for k in ("role_title", "ibu_id", "needed_by", "positions") if not p.get(k)]
    if missing:
        raise BadRequest(f"Missing required fields: {', '.join(missing)}")
    gmin, gmax = p.get("grade_min") or "A2", p.get("grade_max") or p.get("grade_min") or "B1"
    if gmin not in matching.GRADES or gmax not in matching.GRADES or matching.GRADES.index(gmin) > matching.GRADES.index(gmax):
        raise BadRequest("Invalid grade band")
    p["grade_min"], p["grade_max"] = gmin, gmax
    p["exp_min"] = float(p.get("exp_min") if p.get("exp_min") is not None else 0)
    p["exp_max"] = float(p.get("exp_max") if p.get("exp_max") is not None else 30)
    if p["exp_min"] > p["exp_max"]:
        raise BadRequest("Minimum experience is greater than maximum")
    try:
        nb = date.fromisoformat(str(p["needed_by"]))
    except ValueError:
        raise BadRequest("needed_by must be YYYY-MM-DD")
    if nb < config.as_of():
        raise BadRequest("needed_by cannot be in the past")
    p["needed_by"] = nb.isoformat()
    p["positions"] = int(p["positions"])
    if not 1 <= p["positions"] <= 200:
        raise BadRequest("positions must be between 1 and 200")
    p["work_mode"] = p.get("work_mode") or "Hybrid"
    if p["work_mode"] not in WORK_MODES:
        raise BadRequest(f"work_mode must be one of {sorted(WORK_MODES)}")
    p["remote_allowed"] = bool(p.get("remote_allowed") or p["work_mode"] == "Remote")
    p["locations"] = [l.strip() for l in (p.get("locations") or []) if l and l.strip()]
    if not p["locations"] and not p["remote_allowed"]:
        raise BadRequest("Give at least one location, or allow remote work")
    p["priority"] = p.get("priority") or "Medium"
    if p["priority"] not in PRIORITIES:
        raise BadRequest("priority must be High, Medium or Low")
    op = float(p.get("overprovision_factor") or 1.0)
    if not 1.0 <= op <= 3.0:
        raise BadRequest("Over-provisioning factor must be between 1.0 and 3.0")
    p["overprovision_factor"] = op
    p["mandatory"] = _skill_list(conn, p.get("mandatory_skills"), True)
    p["nice"] = _skill_list(conn, p.get("nice_skills"), False)
    p["nice"] = [n for n in p["nice"] if n["skill_id"] not in {m["skill_id"] for m in p["mandatory"]}]
    if not p["mandatory"]:
        raise BadRequest("Add at least one mandatory skill")
    if not p.get("role_family"):
        p["role_family"] = guess_role_family(conn, [m["skill_id"] for m in p["mandatory"]])
    return p


def guess_role_family(conn, skill_ids: list[str]) -> str | None:
    if not skill_ids:
        return None
    q = ",".join("?" * len(skill_ids))
    r = one(conn, f"""SELECT role_family, sum(CASE tier WHEN 'core' THEN 3 WHEN 'common' THEN 2 ELSE 1 END) s
                      FROM role_family_skills WHERE skill_id IN ({q}) GROUP BY role_family ORDER BY s DESC LIMIT 1""", skill_ids)
    return r["role_family"] if r else None


def to_spec(conn, p: dict) -> dict:
    spec = {"demand_id": None, "ibu_id": p["ibu_id"], "account_id": p.get("account_id"), "project_id": p.get("project_id"),
            "role_family": p.get("role_family"), "grade_min": p["grade_min"], "grade_max": p["grade_max"],
            "exp_min": p["exp_min"], "exp_max": p["exp_max"], "locations": p["locations"], "work_mode": p["work_mode"],
            "remote_allowed": p["remote_allowed"], "needed_by": p["needed_by"], "positions": p["positions"],
            "overprovision_factor": p["overprovision_factor"], "mandatory": p["mandatory"], "nice": p["nice"]}
    return matching.with_context(conn, spec)


def jd_text(conn, p: dict) -> str:
    acc = one(conn, "SELECT name FROM accounts WHERE account_id=?", (p.get("account_id"),)) if p.get("account_id") else None
    proj = one(conn, "SELECT name FROM projects WHERE project_id=?", (p.get("project_id"),)) if p.get("project_id") else None
    where = ", ".join(p["locations"]) or "Remote"
    lines = [
        f"{config_company()} is hiring {p['positions']} x {p['role_title']} ({p['grade_min']}–{p['grade_max']})"
        + (f" for the {proj['name']} engagement" if proj else "") + (f" with {acc['name']}" if acc else "") + ".",
        "Must have: " + ", ".join(f"{m['name']} (level {m['min_proficiency']}+)" for m in p["mandatory"]) + ".",
    ]
    if p["nice"]:
        lines.append("Good to have: " + ", ".join(n["name"] for n in p["nice"]) + ".")
    lines.append(f"Experience: {p['exp_min']:g}–{p['exp_max']:g} years. Location: {where} ({p['work_mode']}"
                 + (", remote allowed" if p["remote_allowed"] and p["work_mode"] != "Remote" else "") + ").")
    extra = [f"Start by {p['needed_by']}"]
    if p.get("duration_months"):
        extra.append(f"duration {p['duration_months']} months")
    if p.get("shift"):
        extra.append(f"shift: {p['shift']}")
    if p.get("client_interview"):
        extra.append("client interview required")
    lines.append("; ".join(extra) + ".")
    return " ".join(lines)


def config_company():
    return "Coforce"


def create(conn, user: dict, payload: dict) -> dict:
    p = normalize_payload(conn, payload)
    n = scalar(conn, "SELECT max(CAST(substr(demand_id, 3) AS INTEGER)) FROM demands") or 0
    did = f"DM{n + 1:05d}"
    text = (payload.get("jd_text") or "").strip() or jd_text(conn, p)
    conn.execute("""INSERT INTO demands (demand_id, created_on, raised_by, ibu_id, account_id, project_id, role_family, role_title,
                    grade_band, positions, exp_min, exp_max, locations, work_mode, remote_allowed, needed_by, duration_months,
                    shift, client_interview, priority, status, overprovision_factor, overprovision_approved_by, jd_text, source)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'Open', ?, NULL, ?, ?)""",
                 (did, config.as_of().isoformat(), user["user_id"], p["ibu_id"], p.get("account_id"), p.get("project_id"),
                  p.get("role_family"), p["role_title"].strip(), f"{p['grade_min']}-{p['grade_max']}", p["positions"],
                  p["exp_min"], p["exp_max"], ";".join(p["locations"]), p["work_mode"], int(p["remote_allowed"]),
                  p["needed_by"], p.get("duration_months"), p.get("shift"), int(bool(p.get("client_interview"))),
                  p["priority"], p["overprovision_factor"], text, payload.get("source") or "form"))
    for s in p["mandatory"] + p["nice"]:
        conn.execute("INSERT INTO demand_skills (demand_id, skill_id, mandatory, min_proficiency, weight) VALUES (?,?,?,?,?)",
                     (did, s["skill_id"], int(s["mandatory"]), s["min_proficiency"], s["weight"]))
    conn.commit()
    matching.run_and_store(conn, did, user["user_id"])
    return get(conn, did)


def get(conn, demand_id: str) -> dict:
    d = one(conn, """SELECT d.*, a.name AS account_name, p.name AS project_name, i.name AS ibu_name,
                            r.first_name || ' ' || r.last_name AS raised_by_name, r.designation AS raised_by_designation
                     FROM demands d LEFT JOIN accounts a ON a.account_id=d.account_id
                     LEFT JOIN projects p ON p.project_id=d.project_id LEFT JOIN ibus i ON i.ibu_id=d.ibu_id
                     LEFT JOIN employees r ON r.emp_id=d.raised_by WHERE d.demand_id=?""", (demand_id,))
    if not d:
        raise NotFound(f"Demand {demand_id} not found")
    d["skills"] = rows(conn, """SELECT ds.skill_id, s.name, s.type, ds.mandatory, ds.min_proficiency, ds.weight
                                FROM demand_skills ds JOIN skills s USING(skill_id) WHERE ds.demand_id=?
                                ORDER BY ds.mandatory DESC, s.name""", (demand_id,))
    for s in d["skills"]:
        s["mandatory"] = bool(s["mandatory"])
    d["locations"] = [l for l in (d["locations"] or "").split(";") if l]
    d["remote_allowed"] = bool(d["remote_allowed"])
    d["client_interview"] = bool(d["client_interview"])
    d["match_stats"] = json.loads(d["match_stats"]) if d.get("match_stats") else None
    d["grade_min"], d["grade_max"] = matching.parse_grade_band(d["grade_band"])
    return d


def list_demands(conn, status: str | None = "Open", ibu_id=None, raised_by=None, q=None, page=1, page_size=25) -> dict:
    where, p = ["1=1"], []
    if status:
        where.append("d.status = ?"); p.append(status)
    if ibu_id:
        where.append("d.ibu_id = ?"); p.append(ibu_id)
    if raised_by:
        where.append("d.raised_by = ?"); p.append(raised_by)
    if q:
        where.append("(d.role_title LIKE ? OR d.demand_id LIKE ? OR a.name LIKE ? OR pr.name LIKE ?)"); p += [f"%{q}%"] * 4
    base = f"""FROM demands d LEFT JOIN accounts a ON a.account_id=d.account_id LEFT JOIN projects pr ON pr.project_id=d.project_id
               LEFT JOIN employees r ON r.emp_id=d.raised_by WHERE {' AND '.join(where)}"""
    total = scalar(conn, f"SELECT count(*) {base}", p)
    page_size = max(1, min(int(page_size), 100))
    items = rows(conn, f"""SELECT d.demand_id, d.created_on, d.role_title, d.grade_band, d.positions, d.needed_by, d.priority,
                                  d.status, d.ibu_id, d.work_mode, d.locations, a.name AS account_name, pr.name AS project_name,
                                  r.first_name || ' ' || r.last_name AS raised_by_name, d.overprovision_factor, d.match_stats,
                                  (SELECT count(*) FROM matches m WHERE m.demand_id=d.demand_id AND m.status='selected') AS selected,
                                  (SELECT count(*) FROM matches m WHERE m.demand_id=d.demand_id
                                     AND m.status IN ('shortlisted','nominated','release_requested','release_approved')) AS in_pipeline
                           {base} ORDER BY CASE d.priority WHEN 'High' THEN 0 WHEN 'Medium' THEN 1 ELSE 2 END, d.needed_by
                           LIMIT ? OFFSET ?""", p + [page_size, (max(1, int(page)) - 1) * page_size])
    for i in items:
        st = json.loads(i.pop("match_stats")) if i.get("match_stats") else None
        i["fully_matching"] = st["fully_matching"] if st else None
        i["eligible"] = st["eligible"] if st else None
        i["locations"] = [l for l in (i["locations"] or "").split(";") if l]
    return {"total": total, "page": page, "page_size": page_size, "items": items}


def set_status(conn, demand_id: str, status: str) -> dict:
    if status not in {"Open", "On Hold", "Cancelled", "Fulfilled - Internal", "Fulfilled - External"}:
        raise BadRequest("Invalid status")
    if not one(conn, "SELECT 1 FROM demands WHERE demand_id=?", (demand_id,)):
        raise NotFound("Demand not found")
    conn.execute("UPDATE demands SET status=? WHERE demand_id=?", (status, demand_id))
    conn.commit()
    return get(conn, demand_id)
