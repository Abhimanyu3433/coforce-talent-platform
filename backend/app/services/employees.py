"""Talent search, employee 360 profile, self-service skills & certifications, HR verification."""
from __future__ import annotations

from datetime import date, datetime, timezone

from .. import config
from ..db import one, rows, scalar
from . import residency
from .errors import BadRequest, Conflict, Forbidden, NotFound
from .skills import get_index

EMP_COLS = """e.emp_id, e.first_name || ' ' || e.last_name AS name, e.email, e.designation, e.grade, e.ibu_id,
              e.role_family, e.role_family_name, e.total_exp_years, e.base_location, e.is_onsite, e.status,
              e.open_to_relocate, e.work_mode_preference"""

SORTS = {"name": "e.first_name, e.last_name", "exp": "e.total_exp_years DESC", "grade": "e.grade DESC",
         "residency": "r.account_residency_months DESC", "bench_aging": "r.bench_aging_days DESC",
         "availability": "r.availability_date ASC"}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _resolve_skills(conn, names: list[str]) -> list[dict]:
    idx = get_index(conn)
    out = []
    for n in names:
        n = n.strip()
        if not n:
            continue
        hit = idx.get(n) or idx.normalize(n)["match"]
        if not hit:
            cands = [c["name"] for c in idx.normalize(n)["candidates"]]
            raise BadRequest(f"Unknown skill '{n}'" + (f". Did you mean: {', '.join(cands)}?" if cands else ""))
        out.append(hit)
    return out


# ------------------------------------------------------------------ search
def search(conn, q=None, ibu_id=None, status=None, role_family=None, grade=None, location=None,
           account_id=None, skills: list[str] | None = None, min_proficiency: int = 1, skill_mode: str = "all",
           verified_only: bool = False, rotation_due: bool | None = None, min_exp=None, max_exp=None,
           include_corporate: bool = False, sort="name", page=1, page_size=25) -> dict:
    where, p = ["1=1"], []
    if not include_corporate:
        where.append("e.function = 'Delivery'")
    if q:
        where.append("(e.first_name || ' ' || e.last_name LIKE ? OR e.emp_id LIKE ? OR e.email LIKE ? OR e.designation LIKE ?)")
        p += [f"%{q}%"] * 4
    for col, val in (("e.ibu_id", ibu_id), ("e.status", status), ("e.role_family", role_family), ("e.grade", grade),
                     ("e.base_location", location), ("r.current_account_id", account_id)):
        if val:
            vals = val if isinstance(val, list) else [v for v in str(val).split(",") if v]
            where.append(f"{col} IN ({','.join('?' * len(vals))})"); p += vals
    if rotation_due is not None:
        where.append("r.rotation_due = ?"); p.append(int(rotation_due))
    if min_exp is not None:
        where.append("e.total_exp_years >= ?"); p.append(float(min_exp))
    if max_exp is not None:
        where.append("e.total_exp_years <= ?"); p.append(float(max_exp))
    skill_rows = _resolve_skills(conn, skills or [])
    ver = " AND es.verified = 1" if verified_only else ""
    if skill_rows:
        conds = [f"EXISTS (SELECT 1 FROM employee_skills es WHERE es.emp_id=e.emp_id AND es.skill_id=? AND es.proficiency>=?{ver})"
                 for _ in skill_rows]
        where.append("(" + (" AND " if skill_mode == "all" else " OR ").join(conds) + ")")
        for s in skill_rows:
            p += [s["skill_id"], int(min_proficiency)]
    base = f"""FROM employees e LEFT JOIN residency_snapshot r USING(emp_id)
               LEFT JOIN projects pr ON pr.project_id = r.current_project_id
               LEFT JOIN accounts a ON a.account_id = r.current_account_id
               LEFT JOIN skills ps ON ps.skill_id = e.primary_skill
               WHERE {' AND '.join(where)}"""
    total = scalar(conn, f"SELECT count(*) {base}", p)
    page_size = max(1, min(int(page_size), 200))
    items = rows(conn, f"""SELECT {EMP_COLS}, ps.name primary_skill, pr.name project_name, a.name account_name,
                r.current_account_id, r.account_residency_months, r.rotation_due, r.residency_band, r.planned_rolloff,
                r.availability_date, r.release_requires_approval, r.bench_aging_days
                {base} ORDER BY {SORTS.get(sort, SORTS['name'])} NULLS LAST, e.emp_id LIMIT ? OFFSET ?""",
                p + [page_size, (max(1, int(page)) - 1) * page_size])
    if items:
        ids = [i["emp_id"] for i in items]
        top = rows(conn, f"""SELECT es.emp_id, s.skill_id, s.name, s.type, es.proficiency, es.verified FROM employee_skills es
                             JOIN skills s USING(skill_id) WHERE es.emp_id IN ({','.join('?' * len(ids))}) AND s.type != 'domain'
                             ORDER BY es.proficiency DESC, es.years_used DESC""", ids)
        by = {}
        for t in top:
            by.setdefault(t["emp_id"], []).append(t)
        wanted = {s["skill_id"] for s in skill_rows}
        for i in items:
            lst = by.get(i["emp_id"], [])
            i["matched_skills"] = [t for t in lst if t["skill_id"] in wanted]
            i["top_skills"] = [t for t in lst if t["skill_id"] not in wanted][:6]
    return {"total": total, "page": page, "page_size": page_size, "items": items,
            "resolved_skills": [s["name"] for s in skill_rows]}


# ------------------------------------------------------------------ profile
def profile(conn, emp_id: str) -> dict:
    e = one(conn, f"""SELECT {EMP_COLS}, e.function, e.date_of_joining, e.exp_at_joining, e.hired_as_role,
                         e.hired_role_family, e.bench_since, e.last_working_day, e.reporting_manager_id,
                         m.first_name || ' ' || m.last_name AS reporting_manager_name, i.name AS ibu_name,
                         u.app_role
                      FROM employees e LEFT JOIN employees m ON m.emp_id = e.reporting_manager_id
                      LEFT JOIN ibus i ON i.ibu_id = e.ibu_id LEFT JOIN users u ON u.user_id = e.emp_id
                      WHERE e.emp_id = ?""", (emp_id,))
    if not e:
        raise NotFound(f"Employee {emp_id} not found")
    e["skills"] = rows(conn, """SELECT s.skill_id, s.name, s.type, s.category, es.proficiency, es.years_used,
                                       es.first_used, es.last_used, es.source, es.verified
                                FROM employee_skills es JOIN skills s USING(skill_id) WHERE es.emp_id=?
                                ORDER BY es.proficiency DESC, es.years_used DESC""", (emp_id,))
    for s in e["skills"]:
        s["verified"] = bool(s["verified"])
    e["certifications"] = rows(conn, """SELECT cert_id, name, issuer, skill_ids, issued_on, valid_till, credential_id,
                                               verification_status, verified_by, verified_on, review_note
                                        FROM certifications WHERE emp_id=? ORDER BY issued_on DESC""", (emp_id,))
    today = config.as_of().isoformat()
    for c in e["certifications"]:
        c["expired"] = bool(c["valid_till"] and c["valid_till"] < today)
    e["allocations"] = rows(conn, """SELECT a.allocation_id, a.project_id, p.name project_name, p.project_type,
                                            a.account_id, ac.name account_name, a.role_on_project, a.work_description,
                                            a.skills_used, a.start_date, a.end_date, a.allocation_pct, a.billable,
                                            pm.first_name || ' ' || pm.last_name AS project_manager
                                     FROM allocations a JOIN projects p USING(project_id) JOIN accounts ac ON ac.account_id=a.account_id
                                     LEFT JOIN employees pm ON pm.emp_id = p.project_manager_id
                                     WHERE a.emp_id=? ORDER BY a.start_date DESC""", (emp_id,))
    idx = get_index(conn)
    for a in e["allocations"]:
        a["skills_used"] = [idx.get(s)["name"] for s in (a["skills_used"] or "").split(";") if idx.get(s)]
        a["billable"] = bool(a["billable"])
        a["is_current"] = a["start_date"] <= today < a["end_date"]
    e["trainings"] = rows(conn, """SELECT t.enrollment_id, t.course_id, c.title, c.level, t.mode, t.start_date, t.end_date,
                                          t.status, t.assessment_score, t.source, t.batch_id
                                   FROM training_enrollments t JOIN courses c USING(course_id)
                                   WHERE t.emp_id=? ORDER BY t.start_date DESC""", (emp_id,))
    e["residency"] = residency.employee_residency(conn, emp_id)
    return e


# ------------------------------------------------------------------ self-service skills
def add_or_update_skill(conn, emp_id: str, skill: str, proficiency: int, years_used: float | None = None,
                        last_used: str | None = None) -> dict:
    if not 1 <= int(proficiency) <= 5:
        raise BadRequest("Proficiency must be between 1 and 5")
    if years_used is not None and not 0 <= float(years_used) <= 40:
        raise BadRequest("years_used must be between 0 and 40")
    today = config.as_of().isoformat()
    last_used = last_used or today
    try:
        if date.fromisoformat(last_used) > config.as_of():
            raise BadRequest("last_used cannot be in the future")
    except ValueError:
        raise BadRequest("last_used must be YYYY-MM-DD")
    idx = get_index(conn)
    norm = idx.normalize(skill)
    s = norm["match"]
    if not s:
        raise BadRequest({"message": f"Could not recognise skill '{skill}'", "candidates": norm["candidates"]})
    existing = one(conn, "SELECT * FROM employee_skills WHERE emp_id=? AND skill_id=?", (emp_id, s["skill_id"]))
    if existing and existing["source"] not in ("self_declared",):
        raise Conflict(f"'{s['name']}' is already on your profile from {existing['source'].replace('_', ' ')} "
                       f"(proficiency {existing['proficiency']}). Ask your manager to re-assess it.")
    if existing:
        conn.execute("""UPDATE employee_skills SET proficiency=?, years_used=?, last_used=?, updated_on=?
                        WHERE emp_id=? AND skill_id=?""",
                     (int(proficiency), float(years_used or existing["years_used"]), last_used, _now(), emp_id, s["skill_id"]))
    else:
        conn.execute("""INSERT INTO employee_skills (emp_id, skill_id, proficiency, years_used, first_used, last_used,
                        source, verified, updated_on) VALUES (?,?,?,?,?,?, 'self_declared', 0, ?)""",
                     (emp_id, s["skill_id"], int(proficiency), float(years_used or 0), today, last_used, _now()))
    conn.commit()
    return {"skill": s, "normalized_from": skill if norm["confidence"] < 1 else None, "created": not existing}


def remove_skill(conn, emp_id: str, skill_id: str):
    r = one(conn, "SELECT source FROM employee_skills WHERE emp_id=? AND skill_id=?", (emp_id, skill_id))
    if not r:
        raise NotFound("Skill not on profile")
    if r["source"] != "self_declared":
        raise Forbidden("Only self-declared skills can be removed; project/training skills are part of your history")
    conn.execute("DELETE FROM employee_skills WHERE emp_id=? AND skill_id=?", (emp_id, skill_id))
    conn.commit()


# ------------------------------------------------------------------ certifications
def add_certification(conn, emp_id: str, name: str, issuer: str, skills: list[str], issued_on: str,
                      valid_till: str | None = None, credential_id: str | None = None) -> dict:
    if not name.strip() or not issuer.strip():
        raise BadRequest("Certification name and issuer are required")
    try:
        iss = date.fromisoformat(issued_on)
        if iss > config.as_of():
            raise BadRequest("issued_on cannot be in the future")
        if valid_till and date.fromisoformat(valid_till) < iss:
            raise BadRequest("valid_till must be after issued_on")
    except ValueError:
        raise BadRequest("Dates must be YYYY-MM-DD")
    sk = _resolve_skills(conn, skills)
    if not sk:
        raise BadRequest("Link the certification to at least one skill")
    n = scalar(conn, "SELECT max(CAST(substr(cert_id, 3) AS INTEGER)) FROM certifications") or 0
    cert_id = f"CE{n + 1:06d}"
    conn.execute("""INSERT INTO certifications (cert_id, emp_id, name, issuer, skill_ids, issued_on, valid_till,
                    credential_id, verification_status, created_on) VALUES (?,?,?,?,?,?,?,?, 'pending', ?)""",
                 (cert_id, emp_id, name.strip(), issuer.strip(), ";".join(s["skill_id"] for s in sk), issued_on,
                  valid_till, credential_id, _now()))
    for s in sk:   # make sure linked skills appear on the profile (unverified until HR approves)
        conn.execute("""INSERT OR IGNORE INTO employee_skills (emp_id, skill_id, proficiency, years_used, first_used,
                        last_used, source, verified, updated_on) VALUES (?,?,2,0,?,?, 'self_declared', 0, ?)""",
                     (emp_id, s["skill_id"], issued_on, issued_on, _now()))
    conn.commit()
    return one(conn, "SELECT * FROM certifications WHERE cert_id=?", (cert_id,))


def list_certifications(conn, status: str | None = "pending", ibu_id: str | None = None, limit: int = 200) -> list[dict]:
    where, p = ["1=1"], []
    if status:
        where.append("c.verification_status=?"); p.append(status)
    if ibu_id:
        where.append("e.ibu_id=?"); p.append(ibu_id)
    out = rows(conn, f"""SELECT c.*, e.first_name || ' ' || e.last_name emp_name, e.designation, e.ibu_id
                         FROM certifications c JOIN employees e USING(emp_id) WHERE {' AND '.join(where)}
                         ORDER BY coalesce(c.created_on, c.issued_on) DESC LIMIT ?""", p + [min(int(limit), 1000)])
    idx = get_index(conn)
    for c in out:
        c["skills"] = [idx.get(s)["name"] for s in (c["skill_ids"] or "").split(";") if idx.get(s)]
    return out


def review_certification(conn, cert_id: str, reviewer_id: str, approve: bool, note: str | None = None) -> dict:
    c = one(conn, "SELECT * FROM certifications WHERE cert_id=?", (cert_id,))
    if not c:
        raise NotFound("Certification not found")
    if c["emp_id"] == reviewer_id:
        raise Forbidden("You cannot verify your own certification")
    status = "verified" if approve else "rejected"
    conn.execute("""UPDATE certifications SET verification_status=?, verified_by=?, verified_on=?, review_note=?
                    WHERE cert_id=?""", (status, reviewer_id, _now(), note, cert_id))
    if approve:
        for sid in (c["skill_ids"] or "").split(";"):
            if not sid:
                continue
            ex = one(conn, "SELECT source FROM employee_skills WHERE emp_id=? AND skill_id=?", (c["emp_id"], sid))
            if ex:
                conn.execute("""UPDATE employee_skills SET verified=1, proficiency=max(proficiency, 2), updated_on=?,
                                source=CASE WHEN source='self_declared' THEN 'certification' ELSE source END
                                WHERE emp_id=? AND skill_id=?""", (_now(), c["emp_id"], sid))
            else:
                conn.execute("""INSERT INTO employee_skills (emp_id, skill_id, proficiency, years_used, first_used, last_used,
                                source, verified, updated_on) VALUES (?,?,2,0,?,?, 'certification', 1, ?)""",
                             (c["emp_id"], sid, c["issued_on"], c["issued_on"], _now()))
    conn.commit()
    return one(conn, "SELECT * FROM certifications WHERE cert_id=?", (cert_id,))


# ------------------------------------------------------------------ meta
def meta(conn) -> dict:
    return {
        "ibus": rows(conn, "SELECT ibu_id, name FROM ibus ORDER BY name"),
        "accounts": rows(conn, "SELECT account_id, name, ibu_id FROM accounts ORDER BY name"),
        "role_families": rows(conn, "SELECT DISTINCT role_family, role_family_name FROM employees WHERE function='Delivery' ORDER BY 2"),
        "grades": [r["grade"] for r in rows(conn, "SELECT DISTINCT grade FROM employees ORDER BY grade")],
        "locations": [r["base_location"] for r in rows(conn, "SELECT base_location, count(*) n FROM employees GROUP BY 1 ORDER BY n DESC")],
        "statuses": ["allocated", "bench", "training", "notice"],
        "residency_bands": residency.BAND_ORDER[:-1],
        "skill_categories": [r["category"] for r in rows(conn, "SELECT DISTINCT category FROM skills ORDER BY 1")],
        "as_of": config.as_of().isoformat(),
    }
