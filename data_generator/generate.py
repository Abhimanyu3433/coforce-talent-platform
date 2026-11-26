"""Coforce synthetic data generator.

Usage (from the project root):
    python -m data_generator.generate --employees 10000 --seed 42 --out data

Writes one CSV per table into <out>/csv/ and a SQLite database <out>/coforce.db.
Only needs numpy + pandas (sqlite3 is in the standard library).
"""
from __future__ import annotations

import argparse
import os
import sqlite3
from collections import defaultdict
from datetime import date, timedelta

import numpy as np
import pandas as pd

from . import config as C
from . import names as N
from . import taxonomy as T

DAY = timedelta(days=1)


def d(x: date | None) -> str | None:
    return x.isoformat() if x else None


class CoforceGenerator:
    def __init__(self, n_employees: int = C.DEFAULT_EMPLOYEES, seed: int = C.DEFAULT_SEED):
        self.n = n_employees
        self.rng = np.random.default_rng(seed)
        self.today = C.TODAY
        self.sk = T.SKILL_BY_NAME
        self.tables: dict[str, pd.DataFrame] = {}
        self._validate_taxonomy()

    # ------------------------------------------------------------------ helpers
    def pick(self, weights: dict, size=None):
        keys = list(weights)
        p = np.array([weights[k] for k in keys], dtype=float)
        p = p / p.sum()
        idx = self.rng.choice(len(keys), size=size, p=p)
        return [keys[i] for i in idx] if size is not None else keys[idx]

    def sample(self, items, k):
        items = list(items)
        k = max(0, min(k, len(items)))
        if k == 0:
            return []
        return [items[i] for i in self.rng.choice(len(items), size=k, replace=False)]

    def rand_date(self, start: date, end: date) -> date:
        span = max(0, (end - start).days)
        return start + timedelta(days=int(self.rng.integers(0, span + 1)))

    def sid(self, name: str) -> str:
        return self.sk[name]["skill_id"]

    @staticmethod
    def grade_for_exp(exp: float) -> str:
        g = "A1"
        for grade, (_, lo, _) in C.GRADES.items():
            if exp >= lo:
                g = grade
        return g

    @staticmethod
    def designation(family: dict, grade: str) -> tuple[str, str]:
        """Return (grade, designation) — clamps grade up to the family's first valid level."""
        ladder = family["ladder"]
        gi = C.GRADE_ORDER.index(grade)
        while ladder[gi] is None:
            gi += 1
        return C.GRADE_ORDER[gi], ladder[gi]

    def _validate_taxonomy(self):
        missing = set()
        refs = []
        for fam in list(T.ROLE_FAMILIES.values()) + list(T.CORPORATE_FUNCTIONS.values()):
            refs += fam["core"] + fam["common"] + fam["optional"]
        refs += [a for a, b, _ in T.PREREQUISITES] + [b for a, b, _ in T.PREREQUISITES]
        refs += [a for a, b, _ in T.ADJACENT] + [b for a, b, _ in T.ADJACENT]
        for members, _ in T.FAMILIES.values():
            refs += members
        for _, _, skills, _ in T.CERTIFICATIONS:
            refs += skills
        for r in refs:
            if r not in self.sk:
                missing.add(r)
        if missing:
            raise ValueError(f"Taxonomy references unknown skills: {sorted(missing)}")

    # ------------------------------------------------------------------ 1. skills
    def build_skills(self):
        self.tables["skills"] = pd.DataFrame(
            [{k: v for k, v in s.items() if k != "synonyms"} for s in T.SKILLS])
        self.tables["skill_synonyms"] = pd.DataFrame(
            [{"skill_id": s["skill_id"], "synonym": syn} for s in T.SKILLS for syn in s["synonyms"]])
        rel = []
        for a, b, w in T.PREREQUISITES:
            rel.append((self.sid(a), self.sid(b), "prerequisite", w))
        for a, b, w in T.ADJACENT:
            rel.append((self.sid(a), self.sid(b), "adjacent", w))
        for members, w in T.FAMILIES.values():
            for i, a in enumerate(members):
                for b in members[i + 1:]:
                    rel.append((self.sid(a), self.sid(b), "same_family", w))
        self.tables["skill_relations"] = pd.DataFrame(rel, columns=["skill_a", "skill_b", "relation", "strength"])

        rf = []
        for key, fam in T.ROLE_FAMILIES.items():
            for tier in ("core", "common", "optional"):
                for s in fam[tier]:
                    rf.append((key, fam["name"], self.sid(s), tier))
        self.tables["role_family_skills"] = pd.DataFrame(rf, columns=["role_family", "role_family_name", "skill_id", "tier"])

    # ------------------------------------------------------------------ 2. courses
    def build_courses(self):
        prereq_of = defaultdict(list)
        for a, b, _ in T.PREREQUISITES:
            prereq_of[b].append(self.sid(a))
        rows, cid = [], 1
        for s in T.SKILLS:
            sp, ilt = T.COURSE_DURATION_BY_TYPE[s["type"]]
            variants = [("Foundations", "Beginner", "self-paced", sp, str(self.rng.choice(T.SELF_PACED_PROVIDERS))),
                        ("Practitioner Bootcamp", "Intermediate", "instructor-led", ilt, "Coforce Academy")]
            if s["type"] in {"framework", "platform", "cloud", "practice"} and self.rng.random() < 0.5:
                variants.append(("Advanced Masterclass", "Advanced", "instructor-led", ilt + 3, "Coforce Academy"))
            for title, level, mode, dur, prov in variants:
                rows.append({"course_id": f"CR{cid:04d}", "title": f"{s['name']} - {title}", "skill_id": s["skill_id"],
                             "level": level, "mode": mode, "duration_days": int(dur), "provider": prov,
                             "prerequisite_skill_ids": ";".join(prereq_of.get(s["name"], [])),
                             "max_batch_size": 25 if mode == "instructor-led" else None})
                cid += 1
        self.tables["courses"] = pd.DataFrame(rows)
        c = self.tables["courses"]
        self.course_lookup = {(r.skill_id, r.mode): r for r in c.itertuples()}

    # ------------------------------------------------------------------ 3. IBUs & accounts
    def build_ibus_accounts(self):
        self.tables["ibus"] = pd.DataFrame([{"ibu_id": k, "name": v[0]} for k, v in C.IBUS.items()])
        rows, used, aid = [], set(), 1
        self.accounts_by_ibu = defaultdict(list)
        for ibu, (_, w, _) in C.IBUS.items():
            n_acc = max(6, round(self.n * w / 90))
            for _ in range(n_acc):
                while True:
                    nm = f"{self.rng.choice(N.CLIENT_SYL_A)}{self.rng.choice(N.CLIENT_SYL_B)} {self.rng.choice(N.CLIENT_SUFFIX[ibu])}"
                    if nm not in used:
                        used.add(nm)
                        break
                country = self.pick(C.CLIENT_COUNTRY_WEIGHTS)
                acc = {"account_id": f"AC{aid:04d}", "name": nm, "ibu_id": ibu, "client_country": country,
                       "client_since": d(self.rand_date(C.HISTORY_START - timedelta(days=3 * 365), self.today - timedelta(days=400))),
                       "size_weight": float(self.rng.lognormal(0, 0.8)),
                       "rotation_threshold_months": C.ROTATION_THRESHOLD_MONTHS, "cool_off_months": C.COOL_OFF_MONTHS,
                       "delivery_head_id": None}
                rows.append(acc)
                self.accounts_by_ibu[ibu].append(acc)
                aid += 1
        self.accounts = rows

    # ------------------------------------------------------------------ 4. employees (attributes)
    def build_employees(self):
        ibu_w = {k: v[1] for k, v in C.IBUS.items()}
        grade_w = {g: v[0] for g, v in C.GRADES.items()}
        switch_map = {"QA": ["DATA_ENG", "DEVOPS", "JAVA_FS"], "APP_SUPPORT": ["DEVOPS", "DATA_ENG"],
                      "JAVA_FS": ["DATA_ENG", "GENAI"], "BI": ["DATA_ENG", "DATA_SCI"], "DATA_ENG": ["GENAI", "DATA_SCI"],
                      "DOTNET": ["DEVOPS", "JAVA_FS"], "MAINFRAME": ["JAVA_FS"], "DATA_SCI": ["GENAI"]}
        offshore_w = C.OFFSHORE_CITIES
        all_onsite = [c for cs in C.ONSITE_CITIES.values() for c in cs]
        emps, used_email = [], defaultdict(int)
        n_corp = int(round(self.n * C.CORPORATE_SHARE))
        for i in range(self.n):
            corporate = i < n_corp
            ibu = self.pick(ibu_w)
            if corporate:
                fkey = self.pick({k: v["weight"] for k, v in T.CORPORATE_FUNCTIONS.items()})
                fam = T.CORPORATE_FUNCTIONS[fkey]
            else:
                aff = T.IBU_FAMILY_AFFINITY.get(ibu, {})
                fkey = self.pick({k: v["weight"] * aff.get(k, 1.0) for k, v in T.ROLE_FAMILIES.items()})
                fam = T.ROLE_FAMILIES[fkey]
            grade = self.pick(grade_w)
            grade, desig = self.designation(fam, grade)
            _, lo, hi = C.GRADES[grade]
            exp = round(float(self.rng.uniform(lo, hi)), 1)
            # tenure at Coforce
            if grade == "A1":
                tenure = float(self.rng.uniform(0, max(exp, 0.15)))
            else:
                tenure = float(min(exp, self.rng.gamma(2.0, 1.6)))
            tenure = min(tenure, (self.today - C.HISTORY_START).days / 365 - 0.1)
            doj = self.today - timedelta(days=int(tenure * 365))
            exp_at_join = max(0.0, exp - tenure)
            if exp_at_join < 1.0:   # campus hires join in quarterly cohorts (1 Jan / Apr / Jul / Oct)
                doj = doj.replace(month=((doj.month - 1) // 3) * 3 + 1, day=1)
            join_grade, _ = self.designation(fam, self.grade_for_exp(exp_at_join))
            hired_fkey = fkey
            if not corporate and tenure > 2 and fkey in sum(switch_map.values(), []) and self.rng.random() < 0.10:
                src = [k for k, v in switch_map.items() if fkey in v]
                hired_fkey = str(self.rng.choice(src))
            hired_fam = T.CORPORATE_FUNCTIONS[hired_fkey] if corporate else T.ROLE_FAMILIES[hired_fkey]
            _, hired_as = self.designation(hired_fam, join_grade)

            onsite = (not corporate) and self.rng.random() < C.ONSITE_SHARE and grade not in ("A1",)
            if onsite and self.rng.random() < 0.3:
                first, last = self.rng.choice(N.INTL_FIRST), self.rng.choice(N.INTL_LAST)
            else:
                first, last = self.rng.choice(N.INDIAN_FIRST), self.rng.choice(N.INDIAN_LAST)
            base = f"{first}.{last}".lower().replace("'", "").replace(" ", "").replace("ü", "u")
            used_email[base] += 1
            email = f"{base}{'' if used_email[base] == 1 else used_email[base]}@{C.EMAIL_DOMAIN}"
            emps.append({
                "emp_id": f"CF{100001 + i}", "first_name": first, "last_name": last, "email": email,
                "function": "Corporate" if corporate else "Delivery",
                "ibu_id": ibu, "role_family": fkey, "role_family_name": fam["name"],
                "grade": grade, "designation": desig, "total_exp_years": exp,
                "date_of_joining": d(doj), "exp_at_joining": round(exp_at_join, 1),
                "hired_as_role": hired_as, "hired_role_family": hired_fkey,
                "base_location": str(self.rng.choice(all_onsite)) if onsite else self.pick(offshore_w),
                "is_onsite": bool(onsite),
                "open_to_relocate": bool(self.rng.random() < 0.35),
                "work_mode_preference": self.pick({"Any": 0.45, "Hybrid": 0.35, "Remote": 0.15, "Work from Office": 0.05}),
                "status": "corporate" if corporate else None, "bench_since": None, "last_working_day": None,
                "reporting_manager_id": None, "current_project_id": None, "primary_skill": None,
            })
        self.emps = emps
        self.emp_idx = {e["emp_id"]: e for e in emps}

    # ------------------------------------------------------------------ 5. projects
    def build_projects(self):
        heads = defaultdict(lambda: defaultdict(int))
        for e in self.emps:
            if e["function"] == "Delivery":
                heads[e["ibu_id"]][e["role_family"]] += 1
        rows, pskills, pid = [], [], 1
        self.projects = []
        for ibu, (_, _, flavours) in C.IBUS.items():
            fam_share = heads[ibu]
            tot = sum(fam_share.values())
            n_proj = max(12, round(tot / 15))
            type_w = {}
            for t, (mix, _, allowed) in T.PROJECT_TYPES.items():
                if allowed and ibu not in allowed:
                    continue
                type_w[t] = sum(w * fam_share.get(f, 0) / tot for f, w in mix.items()) + 1e-4
            accs = self.accounts_by_ibu[ibu]
            acc_w = {i: a["size_weight"] for i, a in enumerate(accs)}
            # guarantee every family present in the IBU has at least ~3 projects that use it
            forced = []
            for f, cnt in fam_share.items():
                if cnt < 5:
                    continue
                cands = [t for t in type_w if f in T.PROJECT_TYPES[t][0]]
                if cands:
                    best = max(cands, key=lambda t: T.PROJECT_TYPES[t][0][f])
                    forced += [best] * 3
            ptypes = forced + self.pick(type_w, size=max(0, n_proj - len(forced)))
            for ptype in ptypes:
                mix, base_m, _ = T.PROJECT_TYPES[ptype]
                acc = accs[self.pick(acc_w)]
                start = self.rand_date(C.HISTORY_START, self.today - timedelta(days=20))
                months = float(np.clip(self.rng.lognormal(np.log(base_m), 0.45), 6, 120))
                end = start + timedelta(days=int(months * 30.44))
                flavour = str(self.rng.choice(flavours))
                n_off = 1 + int(self.rng.random() < 0.4)
                offs = self.sample(list(C.OFFSHORE_CITIES), n_off)
                onsite_city = str(self.rng.choice(C.ONSITE_CITIES[acc["client_country"]]))
                # tech stack
                stack = set()
                main_f = max(mix, key=mix.get)
                for f, w in mix.items():
                    fam = T.ROLE_FAMILIES[f]
                    stack |= set(fam["core"])
                    k = int(self.rng.integers(3, 6)) if f == main_f else int(self.rng.integers(1, 4))
                    stack |= set(self.sample(fam["common"], k))
                    if self.rng.random() < 0.4:
                        stack |= set(self.sample(fam["optional"], 1))
                stack |= set(self.sample(T.DOMAIN_SKILLS[ibu], int(self.rng.integers(2, 4))))
                p = {"project_id": f"PR{pid:04d}", "name": f"{flavour} {ptype}", "project_type": ptype,
                     "account_id": acc["account_id"], "ibu_id": ibu,
                     "start_date": d(start), "end_date": d(end),
                     "status": "Active" if end > self.today else "Closed",
                     "delivery_locations": ";".join(offs), "onsite_location": onsite_city,
                     "work_mode": self.pick(C.WORK_MODES), "shift": self.pick(C.SHIFTS),
                     "billing_model": self.pick({"Time & Material": 0.55, "Fixed Price": 0.30, "Managed Services": 0.15}),
                     "project_manager_id": None,
                     "tech_stack": ";".join(sorted(stack)),
                     "_start": start, "_end": end, "_mix": mix, "_stack": stack, "_acc": acc["account_id"],
                     "_flavour": flavour}
                self.projects.append(p)
                for s in sorted(stack):
                    pskills.append({"project_id": p["project_id"], "skill_id": self.sid(s)})
                pid += 1
        self.tables["project_skills"] = pd.DataFrame(pskills)
        # index: (ibu, family) -> [(project, weight)]
        self.proj_index = defaultdict(list)
        self.proj_index_any = defaultdict(list)
        for p in self.projects:
            for f, w in p["_mix"].items():
                self.proj_index[(p["ibu_id"], f)].append((p, w))
                self.proj_index_any[f].append((p, w))
        self.proj_by_id = {p["project_id"]: p for p in self.projects}

    # ------------------------------------------------------------------ 6. allocation simulation
    WORK_TEMPLATES = {
        "DATA_ENG": ["Built {s1} pipelines ingesting {fl} data into the lakehouse; orchestrated with {s2} and tuned jobs using {s3}.",
                     "Migrated legacy {fl} ETL to {s1}; implemented incremental loads and data quality checks with {s2}.",
                     "Designed medallion-layer models for {fl} analytics using {s1}, {s2} and {s3}."],
        "DATA_SCI": ["Developed {s1} models for {fl} use cases; tracked experiments with {s2} and served predictions via {s3}.",
                     "Built forecasting and propensity models for {fl} using {s1} and {s2}."],
        "GENAI": ["Built a {s1}-based assistant for {fl} teams with {s2} retrieval and {s3} orchestration.",
                  "Implemented RAG pipelines over {fl} documents using {s1} and {s2}; evaluated prompts and guardrails."],
        "BI": ["Built {s1} dashboards for {fl} KPIs on top of {s2} models; automated refresh and row-level security.",
               "Re-platformed {fl} reports from legacy tools to {s1}; optimised {s2} measures."],
        "JAVA_FS": ["Developed {s1} microservices for the {fl} platform with {s2} front-end and {s3} integration.",
                    "Built REST APIs in {s1} for {fl} workflows; containerised with {s2} and integrated {s3}."],
        "DOTNET": ["Modernised {fl} applications to {s1} on {s2}; built APIs with {s3}.",
                   "Developed {fl} modules using {s1} and {s2}; set up CI/CD with {s3}."],
        "FRONTEND": ["Built responsive {fl} UI in {s1} with {s2}; improved performance and accessibility.",
                     "Developed reusable {s1} component library for {fl} portals using {s2}."],
        "MOBILE": ["Developed {fl} mobile app features in {s1} with {s2} backend integration.",
                   "Built cross-platform {fl} app using {s1}; integrated push notifications via {s2}."],
        "DEVOPS": ["Automated {fl} infrastructure with {s1}; built CI/CD on {s2} and deployed to {s3}.",
                   "Migrated {fl} workloads to the cloud using {s1} and {s2}; set up monitoring with {s3}."],
        "QA": ["Built {s1} automation suites for {fl} journeys; integrated with {s2} in CI.",
               "Performed functional and API testing for {fl} releases using {s1} and {s2}."],
        "SALESFORCE": ["Customised {s1} for {fl} sales and service teams; built {s2} components and {s3} integrations."],
        "SAP": ["Configured {s1} for {fl} processes; supported {s2} rollout and data migration."],
        "INS_PLATFORMS": ["Configured {s1} for {fl} products; developed {s2} rules and integrations."],
        "SERVICENOW": ["Implemented {s1} workflows for {fl} service management; scripted with {s2}."],
        "SECURITY": ["Ran {s1} and {s2} for the {fl} estate; remediated findings and hardened access."],
        "MAINFRAME": ["Maintained {s1} batch programs for {fl}; supported {s2} and {s3} changes."],
        "APP_SUPPORT": ["Provided L2/L3 support for {fl} applications following {s1}; monitored with {s2}.",
                        "Handled {fl} incidents and problem tickets using {s1}; automated checks with {s2}."],
        "BA": ["Gathered {fl} requirements, wrote {s1} and ran {s2} with business stakeholders."],
        "PM": ["Managed delivery of the {fl} programme using {s1}; owned {s2} and client reporting."],
    }

    def _pick_project(self, ibu, fam, t, last_acc):
        own = [(p, w) for p, w in self.proj_index[(ibu, fam)] if p["_start"] <= t < p["_end"] - timedelta(days=45)]
        pool = own
        if not own or self.rng.random() < 0.12:
            other = [(p, w) for p, w in self.proj_index_any[fam] if p["_start"] <= t < p["_end"] - timedelta(days=45)]
            pool = other or own
        if not pool:
            return None
        w = np.array([pw * (4.0 if p["_acc"] == last_acc else 1.0) * (1 + 0.2 * self.rng.random()) for p, pw in pool])
        return pool[int(self.rng.choice(len(pool), p=w / w.sum()))][0]

    def simulate_allocations(self):
        allocs, aid = [], 1
        self.skill_acc = defaultdict(dict)     # emp_id -> skill -> {years, last, first, src}
        self.bench_periods = defaultdict(list)  # emp_id -> [(start, end)]
        self.onboarding = {}
        for e in self.emps:
            if e["function"] != "Delivery":
                continue
            fam_key = e["role_family"]
            fam = T.ROLE_FAMILIES[fam_key]
            doj = date.fromisoformat(e["date_of_joining"])
            cursor = doj
            if e["exp_at_joining"] < 1.0:     # fresher: onboarding programme
                ob_days = int(self.rng.integers(30, 76))
                self.onboarding[e["emp_id"]] = (doj, doj + timedelta(days=ob_days))
                cursor = doj + timedelta(days=ob_days)
            last_acc = None
            while cursor < self.today:
                p = self._pick_project(e["ibu_id"], fam_key, cursor, last_acc)
                if p is None:
                    gap = int(self.rng.integers(20, 75))
                    self.bench_periods[e["emp_id"]].append((cursor, cursor + timedelta(days=gap)))
                    cursor += timedelta(days=gap)
                    continue
                length = int(np.clip(self.rng.lognormal(np.log(540), 0.6), 60, 2400))
                start = max(cursor, p["_start"])
                end = min(start + timedelta(days=length), p["_end"])
                fam_skills = set(fam["core"]) | set(fam["common"]) | set(fam["optional"])
                used = (set(fam["core"]) & p["_stack"]) | set(self.sample(sorted((p["_stack"] & fam_skills) - set(fam["core"])), int(self.rng.integers(2, 5))))
                used |= {s for s in sorted(p["_stack"]) if self.sk[s]["type"] == "domain" and self.rng.random() < 0.5}
                used_list = sorted(used - {s for s in used if self.sk[s]["type"] == "domain"}) or fam["core"]
                tech_types = {"tool", "language", "framework", "platform", "cloud", "database", "library"}
                tech = [s for s in used_list if self.sk[s]["type"] in tech_types] or used_list
                pick3 = self.sample(tech, 3) + list(fam["core"])
                flv = p["_flavour"]
                tmpl = str(self.rng.choice(self.WORK_TEMPLATES[fam_key]))
                desc = tmpl.format(s1=pick3[0], s2=pick3[1 % len(pick3)], s3=pick3[2 % len(pick3)], fl=flv)
                alloc_pct = 100 if self.rng.random() < 0.9 else int(self.rng.choice([50, 75]))
                exp_then = e["exp_at_joining"] + (start - doj).days / 365
                g_then, role_then = self.designation(fam, self.grade_for_exp(exp_then))
                allocs.append({"allocation_id": f"AL{aid:06d}", "emp_id": e["emp_id"], "project_id": p["project_id"],
                               "account_id": p["_acc"], "ibu_id": p["ibu_id"], "role_on_project": role_then,
                               "work_description": desc, "skills_used": ";".join(self.sid(s) for s in sorted(used)),
                               "start_date": start, "end_date": end, "allocation_pct": alloc_pct,
                               "billable": bool(self.rng.random() < 0.88)})
                aid += 1
                last_acc = p["_acc"]
                # gap to next stint
                if self.rng.random() < 0.55:
                    gap = int(self.rng.integers(0, 11))
                else:
                    gap = int(np.clip(self.rng.exponential(55), 15, 200))
                nxt = end + timedelta(days=gap + 1)
                if gap >= 15:
                    self.bench_periods[e["emp_id"]].append((end + DAY, min(nxt, self.today)))
                cursor = nxt
        self.allocs = allocs

    # ------------------------------------------------------------------ 7. current status & calibration
    def calibrate_status(self):
        by_emp = defaultdict(list)
        for a in self.allocs:
            by_emp[a["emp_id"]].append(a)
        delivery = [e for e in self.emps if e["function"] == "Delivery"]

        def current(eid):
            for a in by_emp[eid]:
                if a["start_date"] <= self.today < a["end_date"]:
                    return a
            return None

        on_bench = [e for e in delivery if current(e["emp_id"]) is None]
        target = int(C.TARGET_BENCH_SHARE * len(delivery))
        need = target - len(on_bench)
        if need > 0:
            cands = [e for e in delivery if (a := current(e["emp_id"])) and (self.today - a["start_date"]).days > 90]
            for e in self.sample(cands, need):
                a = current(e["emp_id"])
                cut = self.today - timedelta(days=int(np.clip(self.rng.exponential(45), 1, 180)))
                a["end_date"] = max(cut, a["start_date"] + timedelta(days=60))
        for e in delivery:
            eid = e["emp_id"]
            cur = current(eid)
            doj = date.fromisoformat(e["date_of_joining"])
            ob = self.onboarding.get(eid)
            if cur:
                e["status"], e["current_project_id"] = "allocated", cur["project_id"]
            elif ob and ob[1] > self.today:
                e["status"], e["bench_since"] = "training", d(doj)
            else:
                past = [a for a in by_emp[eid] if a["end_date"] <= self.today]
                since = max((a["end_date"] for a in past), default=doj) + DAY
                since = min(since, self.today)
                e["status"], e["bench_since"] = "bench", d(since)
                # truncate any recorded bench period beyond today
                self.bench_periods[eid] = [(s, min(t, self.today)) for s, t in self.bench_periods[eid] if s < since]
                self.bench_periods[eid].append((since, self.today))
        # notice period
        alloc_now = [e for e in delivery if e["status"] == "allocated"]
        for e in self.sample(alloc_now, int(C.TARGET_NOTICE_SHARE * len(delivery))):
            e["status"] = "notice"
            e["last_working_day"] = d(self.today + timedelta(days=int(self.rng.integers(10, 90))))
        self.by_emp_allocs = by_emp

    def accumulate_project_skills(self):
        """Skill experience from (final, calibrated) allocations."""
        name_of = {s["skill_id"]: s["name"] for s in T.SKILLS}
        for a in self.allocs:
            eff_end = min(a["end_date"], self.today)
            yrs = max(0, (eff_end - a["start_date"]).days) / 365 * a["allocation_pct"] / 100
            for sid in a["skills_used"].split(";"):
                s = name_of[sid]
                acc = self.skill_acc[a["emp_id"]].setdefault(s, {"years": 0.0, "last": a["start_date"], "first": a["start_date"], "src": "project"})
                acc["years"] += yrs
                acc["first"] = min(acc["first"], a["start_date"])
                acc["last"] = max(acc["last"], eff_end)

    # ------------------------------------------------------------------ 8. managers / heads
    def assign_managers(self):
        seniors = defaultdict(list)
        pms = defaultdict(list)
        for e in self.emps:
            if e["function"] != "Delivery":
                continue
            gi = C.GRADE_ORDER.index(e["grade"])
            if gi >= C.GRADE_ORDER.index("C1"):
                seniors[e["ibu_id"]].append(e["emp_id"])
            if (e["role_family"] == "PM" and gi >= C.GRADE_ORDER.index("B2")) or gi >= C.GRADE_ORDER.index("C1"):
                pms[e["ibu_id"]].append(e["emp_id"])
        heads = defaultdict(list)
        for e in self.emps:
            if e["function"] == "Delivery" and e["grade"] in ("C2", "D1", "D2"):
                heads[e["ibu_id"]].append(e["emp_id"])
        for a in self.accounts:
            pool = heads[a["ibu_id"]] or seniors[a["ibu_id"]]
            a["delivery_head_id"] = str(self.rng.choice(pool))
        for p in self.projects:
            p["project_manager_id"] = str(self.rng.choice(pms[p["ibu_id"]]))
        corp_seniors = defaultdict(list)
        for e in self.emps:
            if e["function"] == "Corporate" and C.GRADE_ORDER.index(e["grade"]) >= C.GRADE_ORDER.index("C1"):
                corp_seniors[e["role_family"]].append(e["emp_id"])
        for e in self.emps:
            if e["function"] == "Corporate":
                pool = [x for x in corp_seniors[e["role_family"]] if x != e["emp_id"]] or [x for xs in corp_seniors.values() for x in xs if x != e["emp_id"]]
                e["reporting_manager_id"] = str(self.rng.choice(pool)) if pool else None
                continue
            allocs = self.by_emp_allocs.get(e["emp_id"], [])
            mgr = None
            if allocs:
                last = max(allocs, key=lambda a: a["start_date"])
                mgr = self.proj_by_id[last["project_id"]]["project_manager_id"]
            if not mgr or mgr == e["emp_id"]:
                pool = [x for x in seniors[e["ibu_id"]] if x != e["emp_id"]]
                mgr = str(self.rng.choice(pool)) if pool else None
            e["reporting_manager_id"] = mgr

    # ------------------------------------------------------------------ 9. training history
    def build_training(self):
        enr, eid_ = [], 1

        def add(emp, skill, mode, start, status, source):
            nonlocal eid_
            course = self.course_lookup.get((self.sid(skill), mode)) or self.course_lookup.get((self.sid(skill), "self-paced"))
            if status == "current":       # a course that is running today
                dur = int(course.duration_days)
                start = max(start, self.today - timedelta(days=int(self.rng.integers(0, max(dur - 1, 1)))))
                start = min(start, self.today)
                status = "auto"
            end = start + timedelta(days=int(course.duration_days))
            if status == "auto":           # status always follows the dates
                status = "completed" if end <= self.today else ("in_progress" if start <= self.today else "scheduled")
            score = int(np.clip(self.rng.normal(74, 12), 35, 100)) if status == "completed" else None
            enr.append({"enrollment_id": f"EN{eid_:06d}", "emp_id": emp["emp_id"], "course_id": course.course_id,
                        "skill_id": course.skill_id, "mode": course.mode, "start_date": start, "end_date": end,
                        "status": status, "assessment_score": score, "source": source, "batch_id": None})
            eid_ += 1
            if status == "completed" and score and score >= 60:
                acc = self.skill_acc[emp["emp_id"]].setdefault(skill, {"years": 0.0, "last": end, "first": end, "src": "training"})
                acc["years"] += 0.25
                acc["last"] = max(acc["last"], end)

        for e in self.emps:
            if e["function"] != "Delivery":
                continue
            fam = T.ROLE_FAMILIES[e["role_family"]]
            ob = self.onboarding.get(e["emp_id"])
            if ob:
                t = ob[0]
                for s in fam["core"][:4]:
                    add(e, s, "instructor-led", t, "auto", "onboarding")
                    t += timedelta(days=int(self.course_lookup[(self.sid(s), "instructor-led")].duration_days))
            for (bs, be) in self.bench_periods.get(e["emp_id"], []):
                if (be - bs).days < 20 or self.rng.random() > 0.55:
                    continue
                have = set(self.skill_acc[e["emp_id"]])
                cand = [s for s in fam["common"] if s not in have] or fam["common"]
                s = str(self.rng.choice(cand))
                mode = "instructor-led" if self.rng.random() < 0.45 else "self-paced"
                is_current = be >= self.today
                status = "auto" if not is_current else ("current" if self.rng.random() < 0.25 else None)
                if status is None:
                    continue
                add(e, s, mode, bs + timedelta(days=int(self.rng.integers(2, 10))), status, "bench_upskilling")
                if is_current and enr[-1]["status"] == "in_progress" and e["status"] == "bench":
                    e["status"] = "training"
        # new hires still in onboarding whose course chain already finished: back on the bench
        active = {x["emp_id"] for x in enr if x["status"] in ("in_progress", "scheduled")}
        for e in self.emps:
            if e["status"] == "training" and e["emp_id"] not in active:
                e["status"] = "bench"
        # group instructor-led enrollments into batches (course + month, max 25)
        df = pd.DataFrame(enr)
        batches, bid = [], 1
        ilt = df[df["mode"] == "instructor-led"].copy()
        # onboarding cohorts batch monthly; bench upskilling batches run per quarter
        ilt["ym"] = [f"{x.year}-{x.month:02d}" if src == "onboarding" else f"{x.year}-Q{(x.month - 1) // 3 + 1}"
                     for x, src in zip(ilt["start_date"], ilt["source"])]
        courses_ix = self.tables["courses"].set_index("course_id")
        for (cid, ym), grp in ilt.groupby(["course_id", "ym"]):
            idx = list(grp.index)
            if len(idx) < 6:   # Academy does not run a classroom batch below 6 → learners go self-paced
                sp = self.course_lookup.get((df.loc[idx[0], "skill_id"], "self-paced"))
                df.loc[idx, "mode"] = "self-paced"
                df.loc[idx, "course_id"] = sp.course_id
                for i in idx:
                    df.at[i, "end_date"] = df.at[i, "start_date"] + timedelta(days=int(sp.duration_days))
                continue
            n_chunks = -(-len(idx) // 25)
            for chunk in np.array_split(np.array(idx), n_chunks):
                chunk = list(chunk)
                start = min(df.loc[chunk, "start_date"])
                course = courses_ix.loc[cid]
                end = start + timedelta(days=int(course.duration_days))
                b = f"TB{bid:05d}"
                df.loc[chunk, "batch_id"] = b
                df.loc[chunk, "start_date"] = start
                df.loc[chunk, "end_date"] = end
                batches.append({"batch_id": b, "course_id": cid, "skill_id": course.skill_id, "start_date": d(start),
                                "end_date": d(end), "capacity": 25, "enrolled": len(chunk),
                                "location": self.pick(C.OFFSHORE_CITIES), "delivery": self.pick({"Virtual": 0.6, "Classroom": 0.4}),
                                "status": "Completed" if end <= self.today else ("Running" if start <= self.today else "Scheduled"),
                                "linked_demand_ids": None, "created_by": None})
                bid += 1
        # batching can move dates: re-derive non-completed statuses from the final dates
        open_ = df["status"] != "completed"
        df.loc[open_ & (df["end_date"] <= self.today), "status"] = "completed"
        df.loc[open_ & (df["start_date"] > self.today), "status"] = "scheduled"
        df.loc[open_ & (df["start_date"] <= self.today) & (df["end_date"] > self.today), "status"] = "in_progress"
        active = set(df.loc[df["status"].isin(["in_progress", "scheduled"]), "emp_id"])
        for e in self.emps:
            if e["status"] == "training" and e["emp_id"] not in active:
                e["status"] = "bench"
        for col in ("start_date", "end_date"):
            df[col] = df[col].map(d)
        self.tables["training_enrollments"] = df
        self.tables["training_batches"] = pd.DataFrame(batches)

    # ------------------------------------------------------------------ 10. employee skills + certifications
    @staticmethod
    def _prof(years):
        for lvl, cut in ((1, 0.3), (2, 1.0), (3, 2.5), (4, 5.0)):
            if years < cut:
                return lvl
        return 5

    def build_employee_skills(self):
        rows, certs, cert_id = [], [], 1
        for e in self.emps:
            eid = e["emp_id"]
            fam = (T.CORPORATE_FUNCTIONS if e["function"] == "Corporate" else T.ROLE_FAMILIES)[e["role_family"]]
            acc = self.skill_acc[eid]
            doj = date.fromisoformat(e["date_of_joining"])
            prior = e["exp_at_joining"]
            # prior experience (laterals) / corporate staff
            if prior > 0.5 or e["function"] == "Corporate":
                yrs_basis = prior if e["function"] == "Delivery" else e["total_exp_years"]
                for s in fam["core"] + self.sample(fam["common"], int(self.rng.integers(1, 4))):
                    a = acc.setdefault(s, {"years": 0.0, "last": doj, "first": doj - timedelta(days=int(yrs_basis * 365)), "src": "prior_experience"})
                    a["years"] += yrs_basis * float(self.rng.uniform(0.4, 0.9))
                    if e["function"] == "Corporate":
                        a["last"] = self.today
            # freshers: academic exposure to the family's core skills
            if e["exp_at_joining"] < 1.0 and e["function"] == "Delivery":
                for s in fam["core"][:3]:
                    acc.setdefault(s, {"years": 0.2, "last": doj, "first": doj - timedelta(days=365), "src": "academic"})
            # hired role-family skills for switchers
            if e["hired_role_family"] != e["role_family"] and e["function"] == "Delivery":
                for s in T.ROLE_FAMILIES[e["hired_role_family"]]["core"]:
                    acc.setdefault(s, {"years": max(prior, 1.0), "last": doj + timedelta(days=365), "first": doj, "src": "project"})
            # self-declared extras
            for s in self.sample(fam["optional"] + fam["common"], int(self.rng.integers(0, 4))):
                if s not in acc:
                    acc[s] = {"years": float(self.rng.uniform(0.1, 0.6)), "last": self.rand_date(doj, self.today), "first": doj, "src": "self_declared"}
            best, best_score = None, -1
            for s, a in acc.items():
                lvl = self._prof(a["years"])
                if self.rng.random() < 0.2:
                    lvl += int(self.rng.choice([-1, 1]))
                lvl = int(np.clip(lvl, 1, 5))
                if a["src"] == "self_declared":
                    lvl = min(lvl, 2)
                rows.append({"emp_id": eid, "skill_id": self.sid(s), "proficiency": lvl, "years_used": round(a["years"], 2),
                             "first_used": d(a["first"]), "last_used": d(min(a["last"], self.today)), "source": a["src"],
                             "verified": a["src"] in ("project", "training", "certification")})
                score = lvl + (2 if s in fam["core"] else 0) + a["years"] * 0.1
                if self.sk[s]["type"] != "domain" and score > best_score:
                    best, best_score = s, score
            e["primary_skill"] = best
            # certifications
            p_cert = 0.15 + 0.06 * C.GRADE_ORDER.index(e["grade"])
            owned = set(acc)
            options = [c for c in T.CERTIFICATIONS if set(c[2]) & owned]
            if options and self.rng.random() < p_cert:
                for name, issuer, skills, valid in self.sample(options, int(self.rng.integers(1, 3))):
                    issued = self.rand_date(max(doj - timedelta(days=700), date(2016, 1, 1)), self.today - timedelta(days=5))
                    certs.append({"cert_id": f"CE{cert_id:06d}", "emp_id": eid, "name": name, "issuer": issuer,
                                  "skill_ids": ";".join(self.sid(s) for s in skills), "issued_on": d(issued),
                                  "valid_till": d(issued + timedelta(days=365 * valid)) if valid else None,
                                  "credential_id": f"{issuer[:3].upper()}-{int(self.rng.integers(10**6, 10**7))}",
                                  "verification_status": self.pick({"verified": 0.8, "pending": 0.15, "rejected": 0.05})})
                    cert_id += 1
        es = pd.DataFrame(rows)
        # certified skills become verified
        if certs:
            cs = pd.DataFrame(certs)
            ver = cs[cs.verification_status == "verified"].assign(skill_id=lambda x: x.skill_ids.str.split(";")).explode("skill_id")
            key = set(zip(ver.emp_id, ver.skill_id))
            es["verified"] = es["verified"].to_numpy(bool) | np.array([k in key for k in zip(es.emp_id, es.skill_id)], dtype=bool)
            es.loc[[k in key for k in zip(es.emp_id, es.skill_id)] & (es["source"] == "self_declared").to_numpy(), "source"] = "certification"
            self.tables["certifications"] = cs
        self.tables["employee_skills"] = es

    # ------------------------------------------------------------------ 11. demands
    def build_demands(self, n_open=90, n_hist=700):
        rows, dskills, did = [], [], 1
        active = [p for p in self.projects if p["_end"] > self.today + timedelta(days=60)]
        managers = defaultdict(list)
        for e in self.emps:
            if e["function"] == "Delivery" and C.GRADE_ORDER.index(e["grade"]) >= C.GRADE_ORDER.index("B1"):
                managers[e["ibu_id"]].append(e["emp_id"])
        hr = [e["emp_id"] for e in self.emps if e["role_family"] == "HR" and C.GRADE_ORDER.index(e["grade"]) >= C.GRADE_ORDER.index("B1")]
        for i in range(n_open + n_hist):
            is_open = i < n_open
            if is_open:
                p = active[int(self.rng.integers(len(active)))]
                created = self.today - timedelta(days=int(self.rng.integers(0, 25)))
            else:
                created = self.today - timedelta(days=int(self.rng.integers(30, 540)))
                pool = [x for x in self.projects if x["_start"] <= created < x["_end"]]
                p = pool[int(self.rng.integers(len(pool)))]
            fkey = self.pick(p["_mix"])
            fam = T.ROLE_FAMILIES[fkey]
            grade = self.pick({"A2": 0.30, "A3": 0.32, "B1": 0.22, "B2": 0.10, "C1": 0.06})
            grade, title = self.designation(fam, grade)
            gi = C.GRADE_ORDER.index(grade)
            _, lo, hi = C.GRADES[grade]
            mand = sorted(set(fam["core"]) & p["_stack"]) or fam["core"][:2]
            extra = sorted((p["_stack"] & set(fam["common"])) - set(mand))
            mand += self.sample(extra, int(self.rng.integers(1, 3)))
            nice_pool = sorted((set(fam["common"]) | set(fam["optional"]) | (p["_stack"] & set(T.DOMAIN_SKILLS[p["ibu_id"]]))) - set(mand))
            nice = self.sample(nice_pool, int(self.rng.integers(2, 4)))
            positions = int(self.rng.choice([1, 1, 1, 1, 2, 2, 3, 4, 5, 6, 8, 10, 12, 15]))
            needed_by = created + timedelta(days=int(self.rng.integers(10, 75)))
            locs = p["delivery_locations"].split(";")
            if self.rng.random() < 0.15:
                locs = locs + [p["onsite_location"]]
            status = "Open" if is_open else self.pick({"Fulfilled - Internal": 0.55, "Fulfilled - External": 0.25, "Cancelled": 0.20})
            op = None if is_open else round(float(self.rng.choice([1.0, 1.0, 1.25, 1.5, 2.0])), 2)
            raised_by = str(self.rng.choice(managers[p["ibu_id"]]))
            jd = (f"{C.COMPANY} is looking for {positions} x {title} ({grade}) for the {p['name']} engagement "
                  f"({self.ibu_name(p['ibu_id'])}). Mandatory: {', '.join(mand)}. Good to have: {', '.join(nice)}. "
                  f"Experience {lo:g}-{hi:g} years. Location: {', '.join(locs)} ({p['work_mode']}). Shift: {p['shift']}.")
            rows.append({"demand_id": f"DM{did:05d}", "created_on": d(created), "raised_by": raised_by,
                         "ibu_id": p["ibu_id"], "account_id": p["_acc"], "project_id": p["project_id"],
                         "role_family": fkey, "role_title": title, "grade_band": f"{grade}-{C.GRADE_ORDER[min(gi + 1, 8)]}",
                         "positions": positions, "exp_min": lo, "exp_max": hi, "locations": ";".join(locs),
                         "work_mode": p["work_mode"], "remote_allowed": p["work_mode"] == "Remote" or self.rng.random() < 0.2,
                         "needed_by": d(needed_by), "duration_months": int(self.rng.choice([6, 9, 12, 18, 24])),
                         "shift": p["shift"], "client_interview": bool(self.rng.random() < 0.6),
                         "priority": self.pick({"High": 0.3, "Medium": 0.5, "Low": 0.2}), "status": status,
                         "overprovision_factor": op,
                         "overprovision_approved_by": None if is_open else str(self.rng.choice(hr)),
                         "jd_text": jd})
            for s in mand:
                dskills.append({"demand_id": f"DM{did:05d}", "skill_id": self.sid(s), "mandatory": True,
                                "min_proficiency": 3 if gi >= 2 else 2, "weight": 1.0})
            for s in nice:
                dskills.append({"demand_id": f"DM{did:05d}", "skill_id": self.sid(s), "mandatory": False,
                                "min_proficiency": 2, "weight": 0.5})
            did += 1
        self.tables["demands"] = pd.DataFrame(rows)
        self.tables["demand_skills"] = pd.DataFrame(dskills)

    @staticmethod
    def ibu_name(k):
        return C.IBUS[k][0]

    # ------------------------------------------------------------------ 12. users
    def build_users(self):
        rows = []
        for e in self.emps:
            gi = C.GRADE_ORDER.index(e["grade"])
            if e["function"] == "Corporate":
                role = "hr"
            elif gi >= C.GRADE_ORDER.index("C1"):
                role = "manager"
            elif gi >= C.GRADE_ORDER.index("B1"):
                role = "lead"
            else:
                role = "employee"
            rows.append({"user_id": e["emp_id"], "email": e["email"], "app_role": role,
                         "can_view_all_employees": role in ("hr", "manager", "lead"),
                         "can_raise_demand": role in ("hr", "manager", "lead"),
                         "can_approve_overprovision": role == "hr",
                         "can_approve_release": role in ("manager",)})
        self.tables["users"] = pd.DataFrame(rows)

    # ------------------------------------------------------------------ finalise
    def finalise(self):
        self.tables["accounts"] = pd.DataFrame(self.accounts).drop(columns=["size_weight"])
        self.tables["projects"] = pd.DataFrame([{k: v for k, v in p.items() if not k.startswith("_")} for p in self.projects])
        al = pd.DataFrame(self.allocs)
        al["start_date"] = al["start_date"].map(d)
        al["end_date"] = al["end_date"].map(d)
        self.tables["allocations"] = al.sort_values(["emp_id", "start_date"]).reset_index(drop=True)
        self.tables["employees"] = pd.DataFrame(self.emps)
        self.tables["employees"]["primary_skill"] = self.tables["employees"]["primary_skill"].map(
            lambda s: self.sid(s) if isinstance(s, str) else None)

    def run(self):
        steps = [("skills", self.build_skills), ("courses", self.build_courses), ("accounts", self.build_ibus_accounts),
                 ("employees", self.build_employees), ("projects", self.build_projects),
                 ("allocations", self.simulate_allocations), ("status", self.calibrate_status),
                 ("project skills", self.accumulate_project_skills),
                 ("managers", self.assign_managers), ("training", self.build_training),
                 ("employee skills", self.build_employee_skills), ("demands", self.build_demands),
                 ("users", self.build_users), ("finalise", self.finalise)]
        for name, fn in steps:
            print(f"  · {name}")
            fn()
        return self.tables


def write_outputs(tables: dict[str, pd.DataFrame], out_dir: str):
    csv_dir = os.path.join(out_dir, "csv")
    os.makedirs(csv_dir, exist_ok=True)
    db_path = os.path.join(out_dir, "coforce.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    with sqlite3.connect(db_path) as con:
        for name, df in tables.items():
            df.to_csv(os.path.join(csv_dir, f"{name}.csv"), index=False)
            df.to_sql(name, con, index=False)
    return db_path


def main():
    ap = argparse.ArgumentParser(description="Generate Coforce synthetic HR/resource data")
    ap.add_argument("--employees", type=int, default=C.DEFAULT_EMPLOYEES)
    ap.add_argument("--seed", type=int, default=C.DEFAULT_SEED)
    ap.add_argument("--out", default="data")
    args = ap.parse_args()
    print(f"Generating {C.COMPANY} data: {args.employees} employees, seed={args.seed}")
    tables = CoforceGenerator(args.employees, args.seed).run()
    db = write_outputs(tables, args.out)
    print(f"Wrote {len(tables)} tables → {args.out}/csv and {db}")
    from .report import build_report
    path = build_report(args.out)
    print(f"Sanity report → {path}")


if __name__ == "__main__":
    main()
