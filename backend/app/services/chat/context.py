"""Lookups the chat needs on every turn (accounts, projects, locations, skill suggestions)."""
from __future__ import annotations

import difflib
import re

from ... import config
from ...db import rows
from ..skills import get_index

IBU_KEYWORDS = {
    "BFS": ["bfs", "banking", "bank", "financial services", "fintech"],
    "INS": ["insurance", "ins ibu"],
    "HLS": ["healthcare", "health care", "life sciences", "pharma", "hls"],
    "TTA": ["aviation", "airline", "travel", "transportation", "tta"],
    "RCG": ["retail", "consumer goods", "cpg", "rcg"],
    "MFG": ["manufacturing", "automotive", "mfg"],
    "ENU": ["energy", "utilities", "enu"],
    "AGR": ["agriculture", "agritech", "agri", "agr"],
    "TMT": ["telecom", "media", "tmt"],
}


class ChatContext:
    def __init__(self, conn, user: dict):
        self.conn = conn
        self.user = user
        self.today = config.as_of()
        self.skills = get_index(conn)
        self.accounts = rows(conn, "SELECT account_id, name, ibu_id FROM accounts")
        self.acc_by_id = {a["account_id"]: a for a in self.accounts}
        locs = rows(conn, "SELECT base_location AS l, count(*) n FROM employees GROUP BY 1 ORDER BY n DESC")
        self.locations = [r["l"] for r in locs]
        self._projects_cache: dict[str, list[dict]] = {}

    # ---------------- suggestions
    def suggest_skills(self, family: str | None, exclude: set, tier: str = "core", n: int = 6) -> list[str]:
        if not family:
            return []
        tiers = ("core", "common") if tier == "core" else ("common", "optional")
        out = rows(self.conn, f"""SELECT s.skill_id, s.name FROM role_family_skills r JOIN skills s USING(skill_id)
                                  WHERE r.role_family=? AND r.tier IN ({','.join('?' * len(tiers))})""", (family, *tiers))
        return [o["name"] for o in out if o["skill_id"] not in exclude][:n]

    def top_accounts(self, ibu_id: str | None, n: int = 5) -> list[str]:
        r = rows(self.conn, """SELECT a.name, count(*) c FROM residency_snapshot r JOIN accounts a ON a.account_id=r.current_account_id
                               WHERE a.ibu_id=? GROUP BY a.account_id ORDER BY c DESC LIMIT ?""", (ibu_id, n))
        return [x["name"] for x in r]

    def active_projects(self, account_id: str) -> list[dict]:
        if account_id not in self._projects_cache:
            self._projects_cache[account_id] = rows(self.conn, """SELECT project_id, name, project_type, ibu_id, work_mode, shift,
                                                                  delivery_locations FROM projects WHERE account_id=? AND end_date>?
                                                                  ORDER BY start_date DESC""",
                                                    (account_id, self.today.isoformat()))
        return self._projects_cache[account_id]

    def common_locations(self) -> list[str]:
        return [l for l in self.locations[:8] if l in ("Bengaluru", "Hyderabad", "Pune", "Noida", "Chennai", "Mumbai")][:5]

    # ---------------- resolution from free text
    def find_ibu(self, text: str) -> str | None:
        t = f" {text.lower()} "
        for ibu, keys in IBU_KEYWORDS.items():
            if any(re.search(rf"(?<![a-z]){re.escape(k)}(?![a-z])", t) for k in keys):
                return ibu
        return None

    def find_account(self, text: str) -> tuple[dict | None, list[str]]:
        t = text.lower().strip()
        m = re.search(r"\bAC\d{4}\b", text, re.I)
        if m and m.group(0).upper() in self.acc_by_id:
            return self.acc_by_id[m.group(0).upper()], []
        for a in self.accounts:                              # full name inside the text
            if a["name"].lower() in t:
                return a, []
        hits = [a for a in self.accounts if re.search(rf"\b{re.escape(a['name'].split()[0].lower())}\b", t)]
        if len(hits) == 1:
            return hits[0], []
        if len(hits) > 1:
            return None, [h["name"] for h in hits]
        close = difflib.get_close_matches(t, [a["name"].lower() for a in self.accounts], n=3, cutoff=0.75)
        if len(close) == 1:
            return next(a for a in self.accounts if a["name"].lower() == close[0]), []
        return None, [next(a["name"] for a in self.accounts if a["name"].lower() == c) for c in close]

    def find_project(self, text: str, account_id: str | None) -> tuple[dict | None, list[str]]:
        m = re.search(r"\bPR\d{4}\b", text, re.I)
        if m:
            r = rows(self.conn, "SELECT project_id, name, project_type, account_id, ibu_id, work_mode, shift, delivery_locations "
                                "FROM projects WHERE project_id=?", (m.group(0).upper(),))
            if r:
                return r[0], []
        if not account_id:
            return None, []
        projs = self.active_projects(account_id)
        t = text.lower().strip()
        exact = [p for p in projs if p["name"].lower() == t or p["name"].lower() in t]
        if len(exact) >= 1:
            return max(exact, key=lambda p: len(p["name"])), []
        names = [p["name"].lower() for p in projs]
        close = difflib.get_close_matches(t, names, n=3, cutoff=0.6)
        if len(close) == 1:
            return projs[names.index(close[0])], []
        # keyword overlap (e.g. "the payments data platform one")
        words = set(re.findall(r"[a-z]{4,}", t))
        scored = sorted(((len(words & set(re.findall(r"[a-z]{4,}", p["name"].lower()))), p) for p in projs), key=lambda x: -x[0])
        if scored and scored[0][0] >= 2 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
            return scored[0][1], []
        return None, [projs[names.index(c)]["name"] for c in close]
