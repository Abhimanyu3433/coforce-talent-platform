"""Validate and normalise an extracted patch (rules or Gemini) into the session draft."""
from __future__ import annotations

from datetime import date, timedelta

from .. import demands
from . import slots
from .rules import scan_skills, split_skill_list

WORK_MODES = {"hybrid": "Hybrid", "work from office": "Work from Office", "wfo": "Work from Office", "office": "Work from Office",
              "onsite": "Work from Office", "remote": "Remote"}
SHIFTS = {"general (ist)": "General (IST)", "general": "General (IST)", "ist": "General (IST)", "uk overlap": "UK Overlap",
          "uk": "UK Overlap", "us overlap": "US Overlap", "us": "US Overlap", "24x7 rotational": "24x7 Rotational", "24x7": "24x7 Rotational"}


def _resolve_skill(ctx, item) -> tuple[dict | None, list[str], str | None]:
    name = item if isinstance(item, str) else item.get("skill", "")
    name = (name or "").strip()
    if not name:
        return None, [], None
    s = ctx.skills.get(name)
    if s:
        return s, [], None
    r = ctx.skills.normalize(name)
    if r["match"]:
        note = None if r["confidence"] >= 1 else f"read “{name}” as {r['match']['name']}"
        return r["match"], [], note
    # maybe the token is a phrase containing a skill ("strong pyspark skills")
    hits = scan_skills(name, ctx.skills)
    if len(hits) == 1:
        return ctx.skills.get(hits[0][0]), [], None
    return None, [c["name"] for c in r["candidates"][:4]], None


def apply_patch(state: dict, patch: dict, ctx) -> tuple[list[str], list[str]]:
    d = state["draft"]
    notes, issues = [], []
    asked = state.get("last_asked")

    if patch.get("role_title"):
        d["role_title"] = patch["role_title"].strip()[:80]
        fam = slots.family_from_title(d["role_title"])
        if fam and not d.get("role_family_locked"):
            d["role_family"] = fam
    if patch.get("positions"):
        n = int(patch["positions"])
        if 1 <= n <= 200:
            d["positions"] = n
        else:
            issues.append("Positions must be between 1 and 200.")

    # ---- skills
    def add(items, mandatory: bool):
        for it in items or []:
            s, cands, note = _resolve_skill(ctx, it)
            raw = it if isinstance(it, str) else it.get("skill")
            if not s:
                if raw and len(raw) < 60:
                    issues.append(f"I couldn't match “{raw}” to a known skill" + (f" — did you mean {', '.join(cands)}?" if cands else "."))
                continue
            if note:
                notes.append(note)
            lvl = None if isinstance(it, str) else it.get("min_proficiency")
            d["nice"] = [n for n in d["nice"] if n["skill_id"] != s["skill_id"] or not mandatory]
            if mandatory:
                ex = next((m for m in d["mandatory"] if m["skill_id"] == s["skill_id"]), None)
                if ex:
                    if lvl:
                        ex["min_proficiency"], ex["level_explicit"] = int(lvl), True
                else:
                    d["mandatory"].append({"skill_id": s["skill_id"], "name": s["name"],
                                           "min_proficiency": int(lvl) if lvl else None, "level_explicit": bool(lvl)})
            elif not any(m["skill_id"] == s["skill_id"] for m in d["mandatory"]) and \
                    not any(n["skill_id"] == s["skill_id"] for n in d["nice"]):
                d["nice"].append({"skill_id": s["skill_id"], "name": s["name"]})

    add(patch.get("mandatory_skills"), True)
    add(patch.get("nice_skills"), False)
    if patch.get("skill_level"):
        lv = int(patch["skill_level"])
        if 1 <= lv <= 5:
            for m in d["mandatory"]:
                if not m.get("level_explicit"):
                    m["min_proficiency"] = lv
            _answer(state, "skill_level")
    for name, lv in (patch.get("skill_levels") or {}).items():
        s, _, _ = _resolve_skill(ctx, name)
        m = next((m for m in d["mandatory"] if s and m["skill_id"] == s["skill_id"]), None)
        if m:
            m["min_proficiency"], m["level_explicit"] = int(lv), True
    if patch.get("skill_levels") and asked == "skill_level":
        for m in d["mandatory"]:
            m["min_proficiency"] = m.get("min_proficiency") or 3
        _answer(state, "skill_level")
    for raw in patch.get("relax_skills") or []:
        for name, _ in split_skill_list(raw) or [(raw, None)]:
            s, _, _ = _resolve_skill(ctx, name)
            m = next((m for m in d["mandatory"] if s and m["skill_id"] == s["skill_id"]), None)
            if m:
                d["mandatory"].remove(m)
                d["nice"].append({"skill_id": m["skill_id"], "name": m["name"]})
                notes.append(f"moved {m['name']} to good-to-have")
            else:
                issues.append(f"“{name}” isn't one of the mandatory skills.")
    for raw in patch.get("remove_skills") or []:
        for name, _ in split_skill_list(raw) or [(raw, None)]:
            s, _, _ = _resolve_skill(ctx, name)
            if s and any(x["skill_id"] == s["skill_id"] for x in d["mandatory"] + d["nice"]):
                d["mandatory"] = [x for x in d["mandatory"] if x["skill_id"] != s["skill_id"]]
                d["nice"] = [x for x in d["nice"] if x["skill_id"] != s["skill_id"]]
                notes.append(f"removed {s['name']}")
    if d["mandatory"] and not d.get("role_family"):
        d["role_family"] = demands.guess_role_family(ctx.conn, [m["skill_id"] for m in d["mandatory"]])

    # ---- experience & grade
    if patch.get("exp_min") is not None:
        lo = float(patch["exp_min"])
        hi = float(patch["exp_max"]) if patch.get("exp_max") is not None else lo + 3
        if lo > hi:
            lo, hi = hi, lo
        if 0 <= lo <= 40 and hi <= 45:
            d["exp_min"], d["exp_max"] = lo, hi
            if not d.get("grades_explicit"):
                d["grade_min"], d["grade_max"] = slots.grade_for_exp(lo), slots.grade_for_exp(hi)
        else:
            issues.append("That experience range doesn't look right.")
    if patch.get("grade_min"):
        gmin, gmax = patch["grade_min"].upper(), (patch.get("grade_max") or patch["grade_min"]).upper()
        order = [g for g, _ in slots.GRADE_MIN_EXP]
        if gmin in order and gmax in order:
            if order.index(gmin) > order.index(gmax):
                gmin, gmax = gmax, gmin
            d["grade_min"], d["grade_max"], d["grades_explicit"] = gmin, gmax, True
            if d.get("exp_min") is None:
                d["exp_min"] = float(dict(slots.GRADE_MIN_EXP)[gmin])
                d["exp_max"] = float(dict(slots.GRADE_MIN_EXP)[gmax]) + 3

    # ---- engagement
    if patch.get("ibu") and not d.get("account_id"):
        d["ibu_id"] = patch["ibu"]
    if patch.get("account"):
        a = ctx.acc_by_id.get(patch["account"])
        if not a:
            found, cands = ctx.find_account(str(patch["account"]))
            a = found
            if not a and cands:
                issues.append(f"Which account do you mean: {', '.join(cands[:4])}?")
        if a:
            if d.get("account_id") != a["account_id"]:
                d.pop("project_id", None), d.pop("project_name", None)
            d["account_id"], d["account_name"], d["ibu_id"] = a["account_id"], a["name"], a["ibu_id"]
    elif patch.get("account_candidates"):
        issues.append(f"Which account do you mean: {', '.join(patch['account_candidates'][:4])}?")
    # ---- location & work mode
    if patch.get("locations"):
        locs = [l for l in patch["locations"] if l in ctx.locations] or \
               [next((x for x in ctx.locations if x.lower() == str(l).lower()), None) for l in patch["locations"]]
        locs = [l for l in locs if l]
        if locs:
            d["locations"] = list(dict.fromkeys(locs))
        else:
            issues.append(f"I don't know the location(s) {', '.join(patch['locations'])}. Coforce locations include "
                          f"{', '.join(ctx.locations[:8])}.")
    if patch.get("work_mode"):
        wm = WORK_MODES.get(str(patch["work_mode"]).lower(), patch["work_mode"])
        if wm in ("Hybrid", "Work from Office", "Remote"):
            d["work_mode"] = wm
            if wm == "Remote":
                d["remote_allowed"] = True
    if patch.get("remote_allowed") is not None:
        d["remote_allowed"] = bool(patch["remote_allowed"])

    # ---- timing & process
    if patch.get("needed_by"):
        try:
            nb = date.fromisoformat(str(patch["needed_by"])[:10])
            if nb < ctx.today:
                issues.append(f"{nb.isoformat()} is in the past — when do you need them?")
            else:
                d["needed_by"] = nb.isoformat()
                if nb < ctx.today + timedelta(days=7):
                    notes.append("that's under a week away, so only people available now will fit")
        except ValueError:
            issues.append("I couldn't read that date — try e.g. “15 Jan” or “in 30 days”.")
    if patch.get("duration_months"):
        n = int(patch["duration_months"])
        if 1 <= n <= 60:
            d["duration_months"] = n
    if patch.get("shift"):
        sh = SHIFTS.get(str(patch["shift"]).lower(), patch["shift"])
        if sh in SHIFTS.values():
            d["shift"] = sh
    if patch.get("client_interview") is not None:
        d["client_interview"] = bool(patch["client_interview"])
    if patch.get("priority") and str(patch["priority"]).capitalize() in ("High", "Medium", "Low"):
        d["priority"] = str(patch["priority"]).capitalize()
    if patch.get("overprovision_factor") is not None:
        f = float(patch["overprovision_factor"])
        if f < 1 or f > 3:
            notes.append("over-provisioning is limited to 1.0–3.0×")
        d["overprovision_factor"] = min(3.0, max(1.0, round(f * 4) / 4))
    # ---- project last: it only fills location / work mode / shift that the message did not set
    if patch.get("project"):
        pid = patch["project"]
        proj = next((p for p in ctx.active_projects(d.get("account_id")) if p["project_id"] == pid), None) if d.get("account_id") else None
        if not proj:
            proj, cands = ctx.find_project(str(pid), d.get("account_id"))
            if not proj and cands:
                issues.append(f"Which project: {', '.join(cands[:4])}?")
        if proj:
            if not proj.get("account_id"):
                proj["account_id"] = d.get("account_id")
            d["project_id"], d["project_name"] = proj["project_id"], proj["name"]
            if proj.get("account_id") and proj["account_id"] in ctx.acc_by_id:
                a = ctx.acc_by_id[proj["account_id"]]
                d["account_id"], d["account_name"], d["ibu_id"] = a["account_id"], a["name"], a["ibu_id"]
            filled = []
            if not d["locations"]:
                d["locations"] = proj["delivery_locations"].split(";")
                filled.append("location " + ", ".join(d["locations"]))
            if not d.get("work_mode"):
                d["work_mode"] = proj["work_mode"]
                filled.append(proj["work_mode"])
            if not d.get("shift"):
                d["shift"] = proj["shift"]
                filled.append(f"{proj['shift']} shift")
            if filled:
                notes.append("took " + ", ".join(filled) + " from the project")
    elif patch.get("project_candidates"):
        issues.append(f"Which project: {', '.join(patch['project_candidates'][:4])}?")

    return notes, issues


def _answer(state, field):
    if field not in state["answered"]:
        state["answered"].append(field)
