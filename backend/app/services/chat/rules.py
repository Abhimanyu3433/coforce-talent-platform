"""Rule-based understanding of a chat message → raw patch (no LLM needed).

The patch uses the same keys as the Gemini extractor, so apply.py treats both identically.
`asked` (the field we last asked about) disambiguates short answers like "3" or "yes".
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

from ..skills import norm

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m} | {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
             "twelve": 12, "fifteen": 15, "twenty": 20, "a": 1, "an": 1, "single": 1, "couple": 2, "few": 3}
LEVEL_WORDS = {"expert": 5, "advanced": 4, "strong": 4, "senior": 4, "intermediate": 3, "working": 3, "good": 3,
               "basic": 2, "beginner": 2, "familiar": 2, "awareness": 1}
NICE_MARKERS = r"(good[- ]to[- ]have|nice[- ]to[- ]have|preferred|desirable|optional|plus|bonus|added advantage)"
# synonyms too generic to trust when scanning prose (still fine when typed as an explicit skill)
SCAN_STOP_KEYS = {"hiring", "recruitment", "rest", "express", "glue", "elastic", "snow", "fabric", "mule", "ba", "agile",
                  "estimation", "client management", "project management", "forecasting", "containers", "lambda", "linux",
                  "excel", "spark core", "spring", "test design", "functional testing", "l&d", "rmg", "resource management"}
SHORT_SKILL_KEYS = {"s3", "js", "ts", "ml", "dl", "k8s", "adf", "gcp", "aws", "sql", "dbt", "nlp", "rag", "iam", "uat", "lwc", "bdd", "itil"}
YES = {"yes", "y", "yeah", "yep", "sure", "ok", "okay", "correct", "true", "required", "needed"}
NO = {"no", "n", "nope", "not required", "not needed", "false"}
CONFIRM = re.compile(r"^\s*(yes|y|create( it| the demand| demand)?|confirm|submit|go ahead|looks good|lgtm|raise it|done|perfect|ok(ay)?)\W*$", re.I)
STRONG_CONFIRM = re.compile(r"^\s*(create( it| the demand| demand| now)?|submit( it)?|raise it|raise the demand|go ahead and create)\W*$", re.I)
CANCEL = re.compile(r"\b(cancel|start over|restart|reset|abort|discard)\b", re.I)
SKIP = re.compile(r"^\s*(skip|default|defaults|any|doesn'?t matter|no preference|whatever|you decide|not sure)\W*$", re.I)
NONE = re.compile(r"^\s*(none|no|nothing|n/?a|nope|no thanks|not really)\W*$", re.I)


def _num(tok: str) -> float | None:
    tok = tok.lower()
    if tok in NUM_WORDS:
        return NUM_WORDS[tok]
    try:
        return float(tok)
    except ValueError:
        return None


# ---------------------------------------------------------------- dates
def parse_date(text: str, today: date) -> str | None:
    t = text.lower()
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t)
    if m:
        return _safe(int(m[1]), int(m[2]), int(m[3]))
    m = re.search(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})\b", t)          # dd/mm/yyyy (Indian format)
    if m:
        y = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
        return _safe(y, int(m[2]), int(m[1]))
    mon = "|".join(sorted(MONTHS, key=len, reverse=True))
    m = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({mon})\.?,?(?:\s+(\d{{4}}))?\b", t) or \
        re.search(rf"\b({mon})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?(?:\s+(\d{{4}}))?\b", t)
    if m:
        a, b, y = m.group(1), m.group(2), m.group(3)
        day, month = (int(a), MONTHS[b]) if a.isdigit() else (int(b), MONTHS[a])
        return _future(today, month, day, int(y) if y else None)
    m = re.search(r"\b(?:in|within|after)\s+(\w+)\s+(day|week|month)s?\b", t)
    if m and _num(m[1]):
        n = _num(m[1])
        return (today + timedelta(days=int(n * {"day": 1, "week": 7, "month": 30}[m[2]]))).isoformat()
    m = re.search(rf"\b(?:by|from|start of|beginning of|early|mid|end of|in)\s+({mon})\b", t)
    if m:
        month = MONTHS[m[1]]
        day = 25 if "end of" in t else (15 if "mid" in t else 1)
        return _future(today, month, day, None)
    if re.search(r"\b(asap|immediately|urgent(ly)?|right away)\b", t):
        return (today + timedelta(days=14)).isoformat()
    if "next week" in t:
        return (today + timedelta(days=7)).isoformat()
    if "next month" in t:
        return (today + timedelta(days=30)).isoformat()
    if "next quarter" in t:
        return (today + timedelta(days=90)).isoformat()
    return None


def _safe(y, m, d):
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def _future(today, month, day, year):
    y = year or today.year
    dt = _safe(y, month, day)
    if dt and not year and dt < today.isoformat():
        dt = _safe(y + 1, month, day)
    return dt


# ---------------------------------------------------------------- skills
def split_skill_list(text: str) -> list[tuple[str, int | None]]:
    """'PySpark 4, SQL (level 3) and Databricks' → [('PySpark', 4), ('SQL', 3), ('Databricks', None)]"""
    text = re.sub(r"^(mandatory|must[- ]have|required|skills?|they need|need)\s*(skills?)?\s*[:\-–]?\s*", "", text.strip(), flags=re.I)
    parts = re.split(r"\s*(?:,|;|\n|\band\b|&|\|)\s*", text)
    out = []
    for p in parts:
        p = p.strip(" .-–:")
        if not p:
            continue
        lvl = None
        m = re.search(r"\(?\s*(?:level|lvl|l)?\s*[:\-]?\s*([1-5])\s*\+?\s*\)?\s*$", p, re.I)
        if m and len(p) > len(m.group(0)):
            lvl, p = int(m.group(1)), p[: m.start()].strip(" (:-")
        for w, v in LEVEL_WORDS.items():
            m2 = re.search(rf"\b(?:{w})\b", p, re.I)
            if m2:
                lvl = lvl or v
                p = (p[: m2.start()] + p[m2.end():]).strip(" -(): in")
        p = re.sub(r"^(in|with|on|of)\s+", "", p, flags=re.I)
        if p:
            out.append((p, lvl))
    return out


def scan_skills(text: str, index) -> list[tuple[str, int]]:
    """Find canonical skills mentioned anywhere in free text. Returns [(skill_id, position)]."""
    t = " " + norm(text) + " "
    found, masked = [], list(t)
    for key in sorted(index.key_to_id, key=len, reverse=True):
        if key in SCAN_STOP_KEYS or index.skills[index.key_to_id[key]]["category"] == "HR":
            continue
        if len(key) < 3 and key not in SHORT_SKILL_KEYS:
            continue
        if len(key) <= 3 and key not in SHORT_SKILL_KEYS and not re.search(r"[#+.]", key):
            continue
        for m in re.finditer(rf"(?<![\w+#.]){re.escape(key)}(?![\w+#])", "".join(masked)):
            sid = index.key_to_id[key]
            if sid not in {f[0] for f in found}:
                found.append((sid, m.start()))
            for i in range(m.start(), m.end()):
                masked[i] = "\0"
    return sorted(found, key=lambda x: x[1])


# ---------------------------------------------------------------- main extractor
def extract(text: str, asked: str | None, ctx, draft: dict | None = None) -> dict:
    p: dict = {}
    raw = text.strip()
    t = raw.lower()
    draft = draft or {}

    if CANCEL.search(t) and len(t.split()) <= 5:
        return {"intent": "cancel"}
    if (CONFIRM.match(raw) and asked in (None, "confirm")) or STRONG_CONFIRM.match(raw):
        return {"intent": "confirm"}
    if SKIP.match(raw):
        return {"intent": "skip"}
    if NONE.match(raw) and asked in ("nice", "project", "engagement", "client_interview"):
        if asked == "client_interview":
            return {"intent": "provide", "client_interview": False}
        return {"intent": "none"}

    # relax / remove skill commands
    m = re.search(rf"\b(?:make|move|mark|set)\s+(.+?)\s+(?:as\s+)?(?:an?\s+)?{NICE_MARKERS}", t) or re.search(r"\brelax\s+(.+)$", t)
    if m:
        p["relax_skills"] = [s for s, _ in split_skill_list(m.group(1))]
    m = re.search(r"\b(?:remove|drop|delete)\s+(.+?)(?:\s+from.*)?$", t)
    if m and "location" not in t:
        p["remove_skills"] = [s for s, _ in split_skill_list(m.group(1))]
    if p:
        p["intent"] = "edit"
        return p

    # --- short contextual answers
    if asked in ("positions", "duration_months", "overprovision_factor", "skill_level") and \
            (asked == "duration_months" or not re.search(r"month|year|yr|\bmos?\b", t)) and re.fullmatch(r"[\w.×x+ \-–—]{1,25}", t):
        n = next((_num(w) for w in re.findall(r"[\d.]+|[a-z]+", t) if _num(w) is not None), None)
        if n is not None:
            if asked == "positions":
                return {"intent": "provide", "positions": int(n)}
            if asked == "duration_months":
                return {"intent": "provide", "duration_months": int(n * 12 if "year" in t else n)}
            if asked == "overprovision_factor":
                return {"intent": "provide", "overprovision_factor": float(n)}
            if asked == "skill_level" and 1 <= n <= 5 and not re.search(r"[a-z]{3,}\s*[1-5]", t):
                return {"intent": "provide", "skill_level": int(n)}
        if asked == "skill_level":
            for w, v in LEVEL_WORDS.items():
                if re.search(rf"\b{w}\b", t):
                    return {"intent": "provide", "skill_level": v}
    if asked == "overprovision_factor" and "double" in t:
        return {"intent": "provide", "overprovision_factor": 2.0}
    # short answers that are unambiguous whatever was asked
    if len(t.split()) <= 3:
        m = re.fullmatch(r"\s*(\d+)\s*(months?|mos?|years?|yrs?)\s*", t)
        if m and asked != "experience":
            n = int(m.group(1))
            return {"intent": "provide", "duration_months": n * 12 if m.group(2).startswith("y") else n}
        m = re.fullmatch(r"\s*(high|medium|low|urgent|critical|normal)(\s+priority)?\s*", t)
        if m:
            return {"intent": "provide", "priority": {"urgent": "High", "critical": "High", "normal": "Medium"}.get(m.group(1), m.group(1).capitalize())}
        m = re.fullmatch(r"\s*(\d(?:\.\d+)?)\s*(x|×|times)\s*", t)
        if m:
            return {"intent": "provide", "overprovision_factor": float(m.group(1))}
        if re.fullmatch(r"\s*(hybrid|work from office|wfo|remote|fully remote)\s*", t):
            wm = "Hybrid" if "hybrid" in t else ("Remote" if "remote" in t else "Work from Office")
            return {"intent": "provide", "work_mode": wm, **({"remote_allowed": True} if wm == "Remote" else {})}
    if asked == "client_interview":
        first = re.sub(r"[^a-z ]", "", t).strip()
        if first in YES or first.startswith("yes"):
            return {"intent": "provide", "client_interview": True}
        if first in NO or first.startswith("no"):
            return {"intent": "provide", "client_interview": False}
    if asked == "role_title" and len(t.split()) <= 8 and not re.search(r"[,:]", t):
        m = re.match(r"^\s*(?:i\s+need|we\s+need|need|looking\s+for|hiring)?\s*(\d+|[a-z]+)?\s*(?:x\s+)?(.+?)\s*$", t)
        cnt = _num(m.group(1)) if m and m.group(1) else None
        title = (m.group(2) if cnt else (m.group(0) if m else t)).strip()
        title = re.sub(r"^(i need|we need|need|looking for|hiring)\s+", "", title)
        title = re.sub(r"\b(positions?|resources?|people|nos)\b", "", title).strip(" .")
        title = re.split(r"\s+\b(?:for|with|in|at|on|who|to|having|from|by|skilled)\b", title)[0].strip(" .")
        if title:
            p["role_title"] = _title(title)
        if cnt:
            p["positions"] = int(cnt)
        # keep going: the same message may also carry locations, skills, dates…

    # --- free text extraction (works for any message, including pasted JDs)
    m = re.search(r"\b(\d+|" + "|".join(NUM_WORDS) + r")\s*(?:x\s+)?(?:nos?\.?\s+)?(?:senior|sr\.?|junior|jr\.?|lead|principal)?\s*"
                  r"[a-z.#/ ]{0,30}?(?:engineers?|developers?|testers?|analysts?|consultants?|architects?|scientists?|"
                  r"managers?|leads?|admins?|administrators?|specialists?|resources?|people|positions?|roles?)\b", t)
    if m and _num(m.group(1)) and not re.search(r"years?|yrs", t[m.end(): m.end() + 6]) and "positions" not in p:
        if asked != "experience":
            p["positions"] = int(_num(m.group(1)))
            if not draft.get("role_title"):
                title = re.sub(r"^\S+\s*(x\s+)?(nos?\.?\s+)?", "", m.group(0)).strip()
                if title and not re.fullmatch(r"(people|resources|positions?|roles?)", title):
                    p["role_title"] = _title(re.sub(r"s$", "", title) if title.endswith("ers") or title.endswith("sts") else title)

    m = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)\s*(?:\+\s*)?(?:years?|yrs?|y)\b", t) or \
        (re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)\s*", t) if asked == "experience" else None)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        p["exp_min"], p["exp_max"] = min(a, b), max(a, b)
    else:
        m = re.search(r"\b(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)\b", t) or \
            (re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*\+?\s*", t) if asked == "experience" else None)
        if m:
            a = float(m.group(1))
            p["exp_min"], p["exp_max"] = a, a + (3 if a < 5 else 5)
    m = re.search(r"\b([a-d][12])\b\s*(?:-|–|to)\s*\b([a-d][12])\b", t)
    if m:
        p["grade_min"], p["grade_max"] = m.group(1).upper(), m.group(2).upper()
    else:
        m = re.search(r"\bgrade\s*([a-d][12])\b", t)
        if m:
            p["grade_min"] = p["grade_max"] = m.group(1).upper()

    # locations / work mode
    locs = [l for l in ctx.locations if re.search(rf"(?<![a-z]){re.escape(l.lower())}(?![a-z])", t)]
    aliases = {"bangalore": "Bengaluru", "bengaluru": "Bengaluru", "gurgaon": "Noida", "delhi": "Noida", "ncr": "Noida",
               "bombay": "Mumbai", "madras": "Chennai", "calcutta": "Kolkata", "bbsr": "Bhubaneswar"}
    for k, v in aliases.items():
        if re.search(rf"\b{k}\b", t) and v not in locs and v in ctx.locations:
            locs.append(v)
    if locs:
        p["locations"] = locs
    if re.search(r"\b(fully remote|remote only|100% remote|work from home|wfh)\b", t):
        p["work_mode"], p["remote_allowed"] = "Remote", True
    elif re.search(r"\bremote\b", t) and not re.search(r"\bno remote|not remote|remote not\b", t):
        p["remote_allowed"] = True
    if re.search(r"\b(no remote|not remote|remote not (allowed|possible))\b", t):
        p["remote_allowed"] = False
    if re.search(r"\bhybrid\b", t):
        p["work_mode"] = "Hybrid"
    elif re.search(r"\b(work from office|wfo|from office|onsite|on-site|office based|5 days office)\b", t):
        p["work_mode"] = "Work from Office"
    elif asked == "work_mode" and "remote" in t:
        p["work_mode"] = "Remote"

    # dates & duration
    if asked == "needed_by" or re.search(r"\b(by|start|from|join|needed|need them|onboard|asap|immediately|urgent|in \d+ (days|weeks))\b", t):
        dt = parse_date(t, ctx.today)
        if dt:
            p["needed_by"] = dt
    m = re.search(r"\b(?:for|duration( of)?|engagement of|contract of)?\s*(\d+)\s*(months?|mos?|years?|yrs?)\b(?!\s*(?:of\s+)?exp)", t)
    if m and not re.search(r"experience|exp\b", t[max(0, m.start() - 15): m.end() + 15]) and \
            (asked == "duration_months" or re.search(r"\b(duration|engagement|contract|for \d+)", t)):
        n = int(m.group(2))
        p["duration_months"] = n * 12 if m.group(3).startswith("y") else n

    # shift / interview / priority / over-provisioning
    if re.search(r"\b(24x7|24/7|rotational)\b", t):
        p["shift"] = "24x7 Rotational"
    elif re.search(r"\bus (shift|overlap|hours|time)\b|\bus\b.*\bshift\b|\best\b|\bpst\b", t) or (asked == "shift" and re.search(r"\bus\b", t)):
        p["shift"] = "US Overlap"
    elif re.search(r"\buk (shift|overlap|hours|time)\b|\bgmt\b|\bbst\b", t) or (asked == "shift" and re.search(r"\buk\b", t)):
        p["shift"] = "UK Overlap"
    elif re.search(r"\b(general|ist|day shift|normal shift)\b", t):
        p["shift"] = "General (IST)"
    if re.search(r"\b(client|customer)\s+(interview|round|screening)", t):
        p["client_interview"] = not re.search(r"\b(no|without|not)\b[^.]{0,20}(client|customer)\s+(interview|round)", t)
    if re.search(r"\b(high|urgent|critical|p1|top) priority\b|\bpriority\s*(is\s*)?(high|p1)\b|\burgent(ly)?\b", t) or (asked == "priority" and re.search(r"\b(high|urgent|critical)\b", t)):
        p["priority"] = "High"
    elif re.search(r"\blow priority\b|\bpriority\s*(is\s*)?low\b", t) or (asked == "priority" and "low" in t):
        p["priority"] = "Low"
    elif re.search(r"\b(medium|normal) priority\b|\bpriority\s*(is\s*)?(medium|normal)\b", t) or (asked == "priority" and re.search(r"\b(medium|normal)\b", t)):
        p["priority"] = "Medium"
    m = re.search(r"\b(\d(?:\.\d+)?)\s*(?:x|×|times)\b", t)
    if m and (asked == "overprovision_factor" or re.search(r"over[- ]?provision|shortlist|train", t)):
        p["overprovision_factor"] = float(m.group(1))

    # engagement
    acc, acc_cands = ctx.find_account(raw) if asked in ("engagement", None) or re.search(r"\b(account|client|for)\b", t) else (None, [])
    if acc:
        p["account"] = acc["account_id"]
    elif acc_cands and asked == "engagement":
        p["account_candidates"] = acc_cands
    ibu = ctx.find_ibu(raw)
    if ibu and (asked in ("engagement", None) or re.search(r"\b(ibu|vertical|domain|for)\b", t)):
        p["ibu"] = ibu
    proj_account = p.get("account") or draft.get("account_id")
    if asked == "project" or re.search(r"\b(project|programme|program|PR\d{4})\b", raw, re.I):
        proj, proj_cands = ctx.find_project(raw, proj_account)
        if proj:
            p["project"] = proj["project_id"]
        elif proj_cands:
            p["project_candidates"] = proj_cands
    elif proj_account:   # a project named outright, whatever was asked
        named = [x for x in ctx.active_projects(proj_account) if x["name"].lower() in t]
        if named:
            p["project"] = max(named, key=lambda x: len(x["name"]))["project_id"]

    # skills
    skill_context = asked in ("mandatory", "nice") or re.search(r"\b(skills?|must[- ]have|mandatory|required|experience (in|with)|"
                                                                r"knowledge of|hands[- ]on|proficien|expertise)\b", t) or len(t.split()) > 25
    if asked in ("mandatory", "nice") and len(t.split()) <= 25 and not re.search(NICE_MARKERS, t):
        items = split_skill_list(raw)
        key = "mandatory_skills" if asked == "mandatory" else "nice_skills"
        p[key] = [{"skill": s, "min_proficiency": lvl} for s, lvl in items]
        if asked == "skill_level":
            pass
    elif skill_context:
        cut = re.search(NICE_MARKERS, t)
        hits = scan_skills(raw, ctx.skills)
        if hits:
            nice_from = cut.start() if cut else 10 ** 9
            lvl_text = {sid: _level_near(t, ctx.skills.get(sid)["name"].lower()) for sid, _ in hits}
            mand = [{"skill": sid, "min_proficiency": lvl_text[sid]} for sid, pos in hits if pos < nice_from]
            nice = [{"skill": sid} for sid, pos in hits if pos >= nice_from]
            if asked == "nice" and not cut:
                nice, mand = nice + [{"skill": m["skill"]} for m in mand], []
            if mand:
                p["mandatory_skills"] = mand
            if nice:
                p["nice_skills"] = nice
    if asked == "skill_level":
        items = [(s, lvl) for s, lvl in split_skill_list(raw) if lvl]
        if items:
            p["skill_levels"] = {s: lvl for s, lvl in items}

    if not draft.get("role_title") and "role_title" not in p and len(t.split()) > 8:
        m = re.search(r"\b(?:for|hiring|need|looking for)\s+(?:an?\s+|\d+\s+)?((?:senior|sr\.?|lead|junior|principal)?\s*[a-z.#/ ]{2,30}?"
                      r"(?:engineer|developer|tester|analyst|consultant|architect|scientist|manager|lead|specialist))s?\b", t)
        if m:
            p["role_title"] = _title(m.group(1))
    p["intent"] = "provide" if any(k != "intent" for k in p) else "unknown"
    return p


def _level_near(t: str, name: str) -> int | None:
    i = t.find(name)
    if i < 0:
        return None
    # only look inside the list item that contains the skill ("Spring Boot (advanced), microservices")
    and_pos = t.rfind(" and ", 0, i)
    start = max(t.rfind(",", 0, i) + 1, t.rfind(";", 0, i) + 1, t.rfind(". ", 0, i) + 1, and_pos + 5 if and_pos != -1 else 0)
    ends = [x for x in (t.find(",", i), t.find(";", i), t.find(" and ", i), t.find(". ", i)) if x != -1]
    window = t[start: min(ends) if ends else len(t)]
    m = re.search(rf"{re.escape(name)}\s*\(?(?:level|lvl)?\s*([1-5])\b", window)
    if m:
        return int(m.group(1))
    for w, v in LEVEL_WORDS.items():
        if re.search(rf"\b{w}\b", window):
            return v
    return None


def _title(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    words = s.split()
    if words and len(words[-1]) > 4 and words[-1].endswith("s") and not words[-1].endswith("ss") and \
            words[-1].lower() not in {"devops", "analytics", "services", "systems", "ops", "sales", "operations"}:
        words[-1] = words[-1][:-1]
        s = " ".join(words)
    keep = {"qa": "QA", "bi": "BI", "ml": "ML", "ai": "AI", "sap": "SAP", "sre": "SRE", ".net": ".NET", "ui": "UI", "ux": "UX",
            "etl": "ETL", "genai": "GenAI", "devops": "DevOps", "sr.": "Senior", "sr": "Senior", "jr": "Junior", "jr.": "Junior"}
    return " ".join(keep.get(w.lower(), w[:1].upper() + w[1:]) for w in s.split())
