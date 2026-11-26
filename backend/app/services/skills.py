"""Skill normalisation (exact → synonym → fuzzy) and search over the canonical taxonomy."""
import difflib
import re

from ..db import rows


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


class SkillIndex:
    def __init__(self, conn):
        self.skills = {s["skill_id"]: s for s in rows(conn, "SELECT skill_id, name, type, category FROM skills")}
        self.key_to_id: dict[str, str] = {}
        for s in self.skills.values():
            self.key_to_id[norm(s["name"])] = s["skill_id"]
        self.synonyms: dict[str, list[str]] = {}
        for r in rows(conn, "SELECT skill_id, synonym FROM skill_synonyms"):
            self.key_to_id.setdefault(norm(r["synonym"]), r["skill_id"])
            self.synonyms.setdefault(r["skill_id"], []).append(r["synonym"])
        self.keys = list(self.key_to_id)

    def get(self, skill_id: str) -> dict | None:
        return self.skills.get(skill_id)

    def normalize(self, text: str) -> dict:
        """Return {'input', 'match', 'confidence', 'candidates'}; match is None when unsure."""
        key = norm(text)
        if key in self.key_to_id:
            return {"input": text, "match": self.skills[self.key_to_id[key]], "confidence": 1.0, "candidates": []}
        close = difflib.get_close_matches(key, self.keys, n=8, cutoff=0.6)
        cands, seen = [], set()
        for k in close:
            sid = self.key_to_id[k]
            if sid not in seen:
                seen.add(sid)
                cands.append({**self.skills[sid], "score": round(difflib.SequenceMatcher(None, key, k).ratio(), 2)})
        # also substring hits ("spark" → "Apache Spark")
        for s in self.search(text, limit=5):
            if s["skill_id"] not in seen:
                seen.add(s["skill_id"])
                cands.append({**s, "score": 0.6})
        cands.sort(key=lambda c: -c["score"])
        best = cands[0] if cands else None
        if best and best["score"] >= 0.88:
            return {"input": text, "match": {k: best[k] for k in ("skill_id", "name", "type", "category")},
                    "confidence": best["score"], "candidates": cands[1:5]}
        return {"input": text, "match": None, "confidence": 0.0, "candidates": cands[:5]}

    def search(self, q: str, limit: int = 10, types: list[str] | None = None) -> list[dict]:
        key = norm(q)
        if not key:
            return []
        starts, contains = [], []
        for s in self.skills.values():
            if types and s["type"] not in types:
                continue
            names = [norm(s["name"])] + [norm(x) for x in self.synonyms.get(s["skill_id"], [])]
            if any(n.startswith(key) for n in names):
                starts.append(s)
            elif any(key in n for n in names):
                contains.append(s)
        return (sorted(starts, key=lambda s: len(s["name"])) + contains)[:limit]


_INDEX: dict[str, SkillIndex] = {}


def get_index(conn) -> SkillIndex:
    """Skills are static reference data — build once per database file."""
    db_file = conn.execute("PRAGMA database_list").fetchone()[2]
    if db_file not in _INDEX:
        _INDEX[db_file] = SkillIndex(conn)
    return _INDEX[db_file]
