"""Resume upload → suggested profile updates. Nothing is saved until the employee confirms (apply)."""
from __future__ import annotations

import re

from ..db import one, rows
from . import documents, employees
from .chat.rules import LEVEL_WORDS, SCAN_STOP_KEYS, SHORT_SKILL_KEYS, scan_skills
from .errors import BadRequest, Conflict, NotFound
from .skills import get_index, norm

LINE = " ¶ "                                    # line marker that survives skills.norm()
EXTRA_LEVEL_WORDS = {"proficient": 4, "hands-on": 3, "hands on": 3, "exposure": 2, "knowledge of": 2, "learning": 1}
YEARS = re.compile(r"(\d{1,2}(?:\.\d)?)\s*\+?\s*(?:years?|yrs?)\b")
TOTAL_EXP = re.compile(r"(\d{1,2}(?:\.\d)?)\s*\+?\s*(?:years?|yrs?)\s+(?:of\s+)?(?:total\s+|overall\s+|professional\s+|"
                       r"it\s+|industry\s+|work\s+)?experience")
EXPLICIT_LEVEL = re.compile(r"(?:level|lvl)\s*([1-5])\b|\b([1-5])\s*/\s*5\b")


def _keys_by_skill(index) -> dict[str, list[str]]:
    """Same trust rules as the demand assistant's scanner: no generic or ambiguous short synonyms."""
    out: dict[str, list[str]] = {}
    for key, sid in index.key_to_id.items():
        if key in SCAN_STOP_KEYS or (len(key) <= 3 and key not in SHORT_SKILL_KEYS and not re.search(r"[#+.]", key)):
            continue
        out.setdefault(sid, []).append(key)
    return out


def _item(line: str, start: int, end: int) -> str:
    """The list item around a mention: 'python (5 years, advanced)' out of 'skills: sql, python (5 years, advanced), git'."""
    depth, s = 0, start
    while s > 0:
        ch = line[s - 1]
        if ch == ")":
            depth += 1
        elif ch == "(":
            if depth == 0:
                break
            depth -= 1
        elif ch in ",;|:" and depth == 0:
            break
        s -= 1
    depth, e = 0, end
    while e < len(line):
        ch = line[e]
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                break
            depth -= 1
        elif ch in ",;|" and depth == 0:
            break
        e += 1
    return line[s:e]


def _years_near(window: str, at: int) -> float | None:
    """Years stated next to the skill ('python (5 years)', '3 yrs of kafka') — not the overall-experience figure."""
    best = None
    for y in YEARS.finditer(window):
        if TOTAL_EXP.match(window, y.start()) or abs(y.start() - at) > 30:
            continue
        v = float(y.group(1))
        if v <= 40 and (best is None or v > best):
            best = v
    return best


def _level(window: str) -> int | None:
    m = EXPLICIT_LEVEL.search(window)
    if m:
        return int(m.group(1) or m.group(2))
    for w, v in {**LEVEL_WORDS, **EXTRA_LEVEL_WORDS}.items():
        if re.search(rf"\b{re.escape(w)}\b", window):
            return v
    return None


def _suggest_level(level: int | None, years: float | None, mentions: int) -> int:
    if level:
        return level
    if years is not None:
        return 2 if years < 1 else 3 if years < 3 else 4
    return 3 if mentions >= 3 else 2


def _cert_catalogue(conn) -> list[dict]:
    cat = rows(conn, """SELECT name, issuer, skill_ids, count(*) n FROM certifications GROUP BY name ORDER BY n DESC""")
    for c in cat:
        m = re.search(r"\(([A-Z]{2,5}(?:-\d{3})?)\)\s*$", c["name"])
        c["code"] = m.group(1) if m else None
        base = re.sub(r"\s*\([^)]*\)\s*$", "", c["name"])
        c["phrases"] = {_plain(base)} | ({_plain(base.split(":", 1)[1])} if ":" in base else set())
    return cat


def _plain(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9+#]+", " ", s.lower())).strip()


def parse(conn, emp_id: str, filename: str | None, data_base64: str) -> dict:
    emp = one(conn, "SELECT emp_id, first_name, last_name, total_exp_years FROM employees WHERE emp_id=?", (emp_id,))
    if not emp:
        raise NotFound("Employee not found")
    raw = documents.decode(data_base64)
    kind = documents.require_kind(raw, ("pdf", "docx"), "resume")
    info = documents.extract_text(raw, kind)
    text = info["text"]
    index = get_index(conn)

    plain = " " + _plain(text) + " "
    have = {r["name"] for r in rows(conn, "SELECT name FROM certifications WHERE emp_id=?", (emp_id,))}
    certs, cert_titles = [], []
    for c in _cert_catalogue(conn):
        hit = (c["code"] and re.search(rf"(?<![A-Za-z0-9]){re.escape(c['code'])}(?![A-Za-z0-9])", text)) or \
              any(len(p) > 8 and f" {p} " in plain for p in c["phrases"])
        if hit:
            certs.append({"name": c["name"], "issuer": c["issuer"], "on_profile": c["name"] in have,
                          "skills": [index.get(x)["name"] for x in (c["skill_ids"] or "").split(";") if index.get(x)]})
            cert_titles += [norm(c["name"]), norm(re.sub(r"\s*\([^)]*\)\s*$", "", c["name"]))]

    lines = []
    for l in text.splitlines():                  # certificate titles name vendors ("Oracle Certified …"), not skills
        l = norm(l)
        for t in sorted(cert_titles, key=len, reverse=True):
            l = l.replace(t, " ")
        if l.strip():
            lines.append(l)
    found = scan_skills(LINE.join(lines), index)
    keys = _keys_by_skill(index)
    profile = {r["skill_id"]: r for r in rows(conn, "SELECT skill_id, proficiency, years_used, source, verified "
                                                    "FROM employee_skills WHERE emp_id=?", (emp_id,))}
    skills = []
    for sid, _pos in found:
        s = index.get(sid)
        pats = [re.compile(rf"(?<![\w+#.]){re.escape(k)}(?![\w+#])") for k in keys.get(sid, [])]
        evidence, level, years, mentions = None, None, None, 0
        for line in lines:
            m = next((m for p in pats for m in [p.search(line)] if m), None)
            if not m:
                continue
            mentions += 1
            window = _item(line, m.start(), m.end())
            lv = _level(window)
            y = _years_near(window, window.find(line[m.start():m.end()]))
            if lv and (level is None or lv > level):
                level = lv
            if y is not None and (years is None or y > years):
                years = y
            if evidence is None or (lv or y):
                a, b = max(0, m.start() - 60), min(len(line), m.end() + 70)
                evidence = ("…" if a else "") + line[a:b].strip() + ("…" if b < len(line) else "")
        suggested = _suggest_level(level, years, mentions)
        cur = profile.get(sid)
        if cur is None:
            status = "new"
        elif cur["source"] == "self_declared" and (suggested > cur["proficiency"] or (years or 0) > (cur["years_used"] or 0)):
            status = "update"
        else:
            status = "on_profile"
        skills.append({"skill_id": sid, "name": s["name"], "type": s["type"], "category": s["category"],
                       "suggested_level": suggested, "years_used": years, "mentions": mentions,
                       "level_from": "resume" if level else "years" if years is not None else "estimate",
                       "evidence": evidence, "status": status,
                       "current_level": cur["proficiency"] if cur else None,
                       "current_source": cur["source"] if cur else None,
                       "locked": bool(cur and cur["source"] != "self_declared")})
    order = {"new": 0, "update": 1, "on_profile": 2}
    skills.sort(key=lambda x: (order[x["status"]], -x["suggested_level"], x["name"]))

    total = [float(m.group(1)) for m in TOTAL_EXP.finditer(" ".join(lines))]
    full = norm(f"{emp['first_name']} {emp['last_name']}")
    name_found = full in " ".join(lines) or (norm(emp["first_name"]) in plain and norm(emp["last_name"]) in plain)
    return {
        "file": {"name": documents.safe_name(filename, kind), "kind": kind, "pages": info["pages"],
                 "chars": info["chars"], "truncated": info["truncated"]},
        "name_found": bool(name_found),
        "resume_exp_years": max(total) if total else None,
        "profile_exp_years": emp["total_exp_years"],
        "skills": skills,
        "certifications": certs,
        "counts": {k: sum(1 for s in skills if s["status"] == k) for k in order},
    }


def apply(conn, emp_id: str, items: list[dict]) -> dict:
    """items: [{'skill_id', 'proficiency', 'years_used'?}] — the rows the employee ticked."""
    if not items:
        raise BadRequest("Tick at least one skill to add")
    index = get_index(conn)
    added, updated, skipped = [], [], []
    for it in items:
        s = index.get(it.get("skill_id"))
        if not s:
            skipped.append({"name": it.get("skill_id"), "reason": "unknown skill"})
            continue
        try:
            r = employees.add_or_update_skill(conn, emp_id, s["name"], int(it["proficiency"]), it.get("years_used"))
            (added if r["created"] else updated).append(s["name"])
        except (Conflict, BadRequest) as e:
            skipped.append({"name": s["name"], "reason": e.detail if isinstance(e.detail, str) else e.detail.get("message")})
    return {"added": added, "updated": updated, "skipped": skipped}
