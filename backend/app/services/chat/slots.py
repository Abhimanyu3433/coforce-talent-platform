"""Slots of a demand, the order we ask for them, defaults, wording and the summary."""
from __future__ import annotations

from datetime import date, timedelta

GRADE_MIN_EXP = [("A1", 0), ("A2", 1), ("A3", 3), ("B1", 5), ("B2", 8), ("C1", 11), ("C2", 14), ("D1", 18), ("D2", 22)]
LEVEL_WORDS = {1: "basic", 2: "beginner", 3: "working", 4: "advanced", 5: "expert"}

FIELDS = ["role_title", "positions", "mandatory", "skill_level", "nice", "experience", "engagement", "project",
          "locations", "work_mode", "needed_by", "duration_months", "shift", "client_interview", "priority",
          "overprovision_factor"]
REQUIRED_FOR_CREATE = ["role_title", "positions", "mandatory", "engagement", "locations", "needed_by"]

TITLE_FAMILY = [
    ("genai", "GENAI"), ("gen ai", "GENAI"), ("llm", "GENAI"), ("data scien", "DATA_SCI"), ("ml engineer", "DATA_SCI"),
    ("machine learning", "DATA_SCI"), ("data engineer", "DATA_ENG"), ("etl", "DATA_ENG"), ("big data", "DATA_ENG"),
    ("bi ", "BI"), ("power bi", "BI"), ("tableau", "BI"), ("data analyst", "BI"), ("report", "BI"),
    (".net", "DOTNET"), ("dotnet", "DOTNET"), ("c#", "DOTNET"), ("java", "JAVA_FS"), ("full stack", "JAVA_FS"),
    ("backend", "JAVA_FS"), ("frontend", "FRONTEND"), ("front end", "FRONTEND"), ("react", "FRONTEND"), ("angular", "FRONTEND"),
    ("ui developer", "FRONTEND"), ("mobile", "MOBILE"), ("android", "MOBILE"), ("ios", "MOBILE"), ("flutter", "MOBILE"),
    ("devops", "DEVOPS"), ("cloud", "DEVOPS"), ("sre", "DEVOPS"), ("platform engineer", "DEVOPS"),
    ("qa", "QA"), ("test", "QA"), ("quality", "QA"), ("automation", "QA"), ("salesforce", "SALESFORCE"),
    ("sap", "SAP"), ("guidewire", "INS_PLATFORMS"), ("servicenow", "SERVICENOW"), ("security", "SECURITY"), ("cyber", "SECURITY"),
    ("mainframe", "MAINFRAME"), ("cobol", "MAINFRAME"), ("support", "APP_SUPPORT"), ("business analyst", "BA"),
    ("project manager", "PM"), ("scrum master", "PM"), ("delivery manager", "PM"),
]


def family_from_title(title: str | None) -> str | None:
    t = f" {(title or '').lower()} "
    for key, fam in TITLE_FAMILY:
        if key in t:
            return fam
    return None


def grade_for_exp(exp: float) -> str:
    g = "A1"
    for grade, lo in GRADE_MIN_EXP:
        if exp >= lo:
            g = grade
    return g


def new_state() -> dict:
    return {"draft": {"mandatory": [], "nice": [], "locations": []}, "answered": [], "last_asked": None,
            "phase": "collect", "demand_id": None, "supply": None, "supply_sig": None}


def is_filled(field: str, s: dict) -> bool:
    d, ans = s["draft"], set(s["answered"])
    if field == "role_title":
        return bool(d.get("role_title"))
    if field == "positions":
        return bool(d.get("positions"))
    if field == "mandatory":
        return len(d["mandatory"]) > 0
    if field == "skill_level":
        return not d["mandatory"] or "skill_level" in ans or all(m.get("level_explicit") for m in d["mandatory"])
    if field == "nice":
        return bool(d["nice"]) or "nice" in ans
    if field == "experience":
        return d.get("exp_min") is not None
    if field == "engagement":
        return bool(d.get("account_id")) or ("engagement" in ans and bool(d.get("ibu_id")))
    if field == "project":
        return bool(d.get("project_id")) or "project" in ans or not d.get("account_id")
    if field == "locations":
        return bool(d["locations"]) or bool(d.get("remote_allowed")) or d.get("work_mode") == "Remote"
    if field in ("work_mode", "needed_by", "duration_months", "shift", "priority", "overprovision_factor"):
        return d.get(field) not in (None, "")
    if field == "client_interview":
        return d.get("client_interview") is not None
    return True


def missing(s: dict) -> list[str]:
    return [f for f in FIELDS if not is_filled(f, s)]


def can_create(s: dict) -> bool:
    return all(is_filled(f, s) for f in REQUIRED_FOR_CREATE)


def apply_default(field: str, s: dict, ctx) -> str | None:
    """Fill a skipped field with its default; returns a short note."""
    d = s["draft"]
    if field == "positions":
        d["positions"] = 1
        return "Assuming 1 position."
    if field == "skill_level":
        for m in d["mandatory"]:
            m["min_proficiency"] = m.get("min_proficiency") or 3
        return "Using level 3 (working knowledge) for the mandatory skills."
    if field == "experience":
        d["exp_min"], d["exp_max"] = 2.0, 6.0
        return "Using 2–6 years of experience."
    if field == "engagement":
        d["ibu_id"] = d.get("ibu_id") or ctx.user["ibu_id"]
        return f"I'll raise it under your IBU ({d['ibu_id']}) without a specific account."
    if field == "work_mode":
        d["work_mode"] = "Hybrid"
        return "Work mode: Hybrid."
    if field == "needed_by":
        d["needed_by"] = (ctx.today + timedelta(days=30)).isoformat()
        return f"Assuming they're needed by {d['needed_by']} (30 days)."
    if field == "duration_months":
        d["duration_months"] = 12
        return "Assuming a 12-month engagement."
    if field == "shift":
        d["shift"] = "General (IST)"
        return "Shift: General (IST)."
    if field == "client_interview":
        d["client_interview"] = False
        return "No client interview."
    if field == "priority":
        d["priority"] = "Medium"
        return "Priority: Medium."
    if field == "overprovision_factor":
        d["overprovision_factor"] = 1.0
        return "No over-provisioning (×1.0)."
    return None


def question(field: str, s: dict, ctx) -> tuple[str, list[str]]:
    d = s["draft"]
    role = d.get("role_title") or "this role"
    if field == "role_title":
        return ("What role do you need, and how many people? For example “3 Senior Data Engineers”. "
                "You can also paste a full job description and I'll pick out the details."), []
    if field == "positions":
        return f"How many {role} positions do you need?", ["1", "2", "3", "5", "10"]
    if field == "mandatory":
        sugg = ctx.suggest_skills(d.get("role_family"), exclude=set(), tier="core")
        return (f"Which skills are **mandatory** for {role}? List them separated by commas — you can add a level, "
                f"e.g. “PySpark 4, SQL, Databricks”."), sugg
    if field == "skill_level":
        names = ", ".join(m["name"] for m in d["mandatory"] if not m.get("level_explicit"))
        return (f"What minimum level do you need on {names}? 1 = basic … 3 = working … 5 = expert. "
                f"You can also set them individually, e.g. “PySpark 4, SQL 3”."), ["3 — working", "4 — advanced", "2 — beginner"]
    if field == "nice":
        have = {m["skill_id"] for m in d["mandatory"]}
        sugg = ctx.suggest_skills(d.get("role_family"), exclude=have, tier="common")
        return "Any **good-to-have** skills? Say “none” to skip.", sugg + ["None"]
    if field == "experience":
        return "What experience range? e.g. “3–6 years” (you can add a grade band like “A3–B1”).", ["0–2 years", "2–5 years", "4–8 years", "8–12 years"]
    if field == "engagement":
        accs = ctx.top_accounts(d.get("ibu_id") or ctx.user["ibu_id"])
        return "Which **account** (client) is this for? Say “none” if it isn't for a specific account.", accs + ["None"]
    if field == "project":
        projs = ctx.active_projects(d["account_id"])
        return (f"Which project under {d.get('account_name')}? Choosing one fills location, work mode and shift. "
                f"Say “none” to skip."), [p["name"] for p in projs[:6]] + ["None"]
    if field == "locations":
        return "Where should they work? Name the city or cities, and say if remote is fine.", ctx.common_locations() + ["Remote OK"]
    if field == "work_mode":
        return "Work mode?", ["Hybrid", "Work from Office", "Remote"]
    if field == "needed_by":
        return "When do you need them to start?", ["In 2 weeks", "In 30 days", "In 45 days", "In 60 days"]
    if field == "duration_months":
        return "How long is the engagement?", ["6 months", "12 months", "18 months", "24 months"]
    if field == "shift":
        return "Which shift?", ["General (IST)", "UK Overlap", "US Overlap", "24x7 Rotational"]
    if field == "client_interview":
        return "Will the client interview the candidates?", ["Yes", "No"]
    if field == "priority":
        return "What's the priority?", ["High", "Medium", "Low"]
    if field == "overprovision_factor":
        n = d.get("positions") or 1
        return (f"Over-provisioning: how many candidates per position should we shortlist and train? For {n} position(s), "
                f"1.5× means {round(n * 1.5 + 0.49)} people. You decide the factor; HR approves it with the training plan."), \
               ["1.0×", "1.5×", "2.0×", "3.0×"]
    return "Anything else?", []


def summary(s: dict) -> str:
    d = s["draft"]
    mand = ", ".join(f"{m['name']} ({m.get('min_proficiency') or 3}+)" for m in d["mandatory"]) or "—"
    nice = ", ".join(n["name"] for n in d["nice"]) or "none"
    where = ", ".join(d["locations"]) or ("Remote" if d.get("remote_allowed") else "—")
    remote = " · remote OK" if d.get("remote_allowed") and d.get("work_mode") != "Remote" else ""
    eng = d.get("account_name") or f"IBU {d.get('ibu_id') or '—'}"
    if d.get("project_name"):
        eng += f" / {d['project_name']}"
    lines = [
        f"**{d.get('positions') or '?'} × {d.get('role_title') or '?'}** ({d.get('grade_min', '?')}–{d.get('grade_max', '?')}, "
        f"{_num(d.get('exp_min'))}–{_num(d.get('exp_max'))} yrs)",
        f"• Engagement: {eng}",
        f"• Mandatory: {mand}",
        f"• Good to have: {nice}",
        f"• Location: {where} ({d.get('work_mode') or '—'}{remote}) · shift {d.get('shift') or '—'}",
        f"• Needed by {d.get('needed_by') or '—'} for {d.get('duration_months') or '—'} months · priority {d.get('priority') or '—'}"
        f" · client interview: {'yes' if d.get('client_interview') else 'no'}",
        f"• Over-provisioning ×{d.get('overprovision_factor') or 1.0} → {_need(d)} candidates (HR approves)",
    ]
    return "\n".join(lines)


def _num(x):
    return "?" if x is None else f"{x:g}"


def _need(d):
    import math
    return math.ceil((d.get("positions") or 1) * (d.get("overprovision_factor") or 1.0))


def to_payload(s: dict) -> dict:
    d = s["draft"]
    return {
        "role_title": d.get("role_title"), "role_family": d.get("role_family"), "positions": d.get("positions") or 1,
        "grade_min": d.get("grade_min"), "grade_max": d.get("grade_max"), "exp_min": d.get("exp_min"), "exp_max": d.get("exp_max"),
        "ibu_id": d.get("ibu_id"), "account_id": d.get("account_id"), "project_id": d.get("project_id"),
        "locations": d["locations"], "work_mode": d.get("work_mode") or "Hybrid", "remote_allowed": bool(d.get("remote_allowed")),
        "needed_by": d.get("needed_by"), "duration_months": d.get("duration_months"), "shift": d.get("shift"),
        "client_interview": bool(d.get("client_interview")), "priority": d.get("priority") or "Medium",
        "overprovision_factor": d.get("overprovision_factor") or 1.0,
        "mandatory_skills": [{"skill": m["skill_id"], "min_proficiency": m.get("min_proficiency") or 3} for m in d["mandatory"]],
        "nice_skills": [{"skill": n["skill_id"]} for n in d["nice"]],
        "source": "chatbot",
    }


def supply_payload(s: dict, today: date) -> dict | None:
    """Payload for a live supply check once enough is known (date defaults to +30 days)."""
    d = s["draft"]
    if not d["mandatory"] or not d.get("ibu_id") or not (d["locations"] or d.get("remote_allowed") or d.get("work_mode") == "Remote"):
        return None
    p = to_payload(s)
    p["role_title"] = p["role_title"] or "Draft"
    p["needed_by"] = p["needed_by"] or (today + timedelta(days=30)).isoformat()
    p["grade_min"] = p["grade_min"] or "A2"
    p["grade_max"] = p["grade_max"] or "C1"
    return p
