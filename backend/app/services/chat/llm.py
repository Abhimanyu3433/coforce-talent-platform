"""Message understanding: Gemini (structured JSON output) when GEMINI_API_KEY is set, rule-based otherwise.

Gemini's patch is merged with the rule-based patch (Gemini wins on conflicts), so a slow or partial LLM answer
never loses information the rules already found. Any Gemini error falls back to rules for that turn.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Literal

from pydantic import BaseModel

from . import rules

log = logging.getLogger("coforce.chat")

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")


class SkillItem(BaseModel):
    skill: str
    min_proficiency: int | None = None


class Patch(BaseModel):
    intent: Literal["provide", "confirm", "cancel", "skip", "none", "edit", "question", "unknown"]
    role_title: str | None = None
    positions: int | None = None
    mandatory_skills: list[SkillItem] | None = None
    nice_skills: list[SkillItem] | None = None
    skill_level: int | None = None
    skill_levels: list[SkillItem] | None = None
    relax_skills: list[str] | None = None
    remove_skills: list[str] | None = None
    exp_min: float | None = None
    exp_max: float | None = None
    grade_min: str | None = None
    grade_max: str | None = None
    ibu: str | None = None
    account: str | None = None
    project: str | None = None
    locations: list[str] | None = None
    work_mode: Literal["Hybrid", "Work from Office", "Remote"] | None = None
    remote_allowed: bool | None = None
    needed_by: str | None = None
    duration_months: int | None = None
    shift: Literal["General (IST)", "UK Overlap", "US Overlap", "24x7 Rotational"] | None = None
    client_interview: bool | None = None
    priority: Literal["High", "Medium", "Low"] | None = None
    overprovision_factor: float | None = None


SYSTEM = """You extract structured fields for an internal IT-services staffing demand from ONE chat message.
Rules:
- Only extract what the message actually states or clearly implies. Never invent values. Leave everything else null.
- `asked` is the field the assistant just asked about; use it to interpret short answers ("3", "yes", "none", "Pune").
- intent: provide (gives info) | confirm (agrees to create the demand, only when asked == "confirm") | cancel (start over) |
  skip (wants the default for the asked field) | none (says there is nothing for the asked field, e.g. no good-to-have skills) |
  edit (relax/remove skills or change a value) | question (asks something) | unknown.
- Skills: canonical names where possible (e.g. "ADF" -> "Azure Data Factory", "k8s" -> "Kubernetes"). Mandatory vs good-to-have:
  words like "nice to have", "preferred", "plus", "optional" mean good-to-have. min_proficiency 1-5 only if stated
  (expert=5, advanced/strong=4, working/intermediate=3, basic=2).
- skill_level: a single level that applies to all mandatory skills; skill_levels: per-skill levels.
- relax_skills: skills to move from mandatory to good-to-have ("make Kafka optional"). remove_skills: skills to drop.
- Experience in years (exp_min/exp_max). "5+ years" -> exp_min 5, exp_max null. Grades look like A1..D2.
- Dates: convert to ISO YYYY-MM-DD relative to TODAY. "in 30 days", "by 15 Jan", "ASAP" (= today + 14 days).
- duration_months as an integer ("1 year" = 12). overprovision_factor between 1 and 3 ("2x", "double" = 2).
- ibu must be one of the IBU ids given. locations must be names from the location list. account/project: copy the name or id the user gave.
Return JSON only."""


def _prompt(text: str, asked: str | None, ctx, draft: dict) -> str:
    small = {k: v for k, v in draft.items() if k in ("role_title", "positions", "account_name", "project_name", "ibu_id",
                                                       "locations", "needed_by", "work_mode")}
    small["mandatory"] = [m["name"] for m in draft.get("mandatory", [])]
    small["nice"] = [n["name"] for n in draft.get("nice", [])]
    return json.dumps({
        "TODAY": ctx.today.isoformat(), "asked": asked or "confirm", "current_draft": small,
        "ibus": {"BFS": "Banking & Financial Services", "INS": "Insurance", "HLS": "Healthcare & Life Sciences",
                 "TTA": "Travel, Transportation & Aviation", "RCG": "Retail & Consumer Goods", "MFG": "Manufacturing & Automotive",
                 "ENU": "Energy & Utilities", "AGR": "Agriculture & Agritech", "TMT": "Telecom & Media"},
        "locations": ctx.locations, "message": text,
    }, ensure_ascii=False)


CALL_TIMEOUT_S = float(os.getenv("GEMINI_TIMEOUT_SECONDS", "12"))       # per request
TURN_BUDGET_S = float(os.getenv("GEMINI_TURN_BUDGET_SECONDS", "25"))    # all models together, per chat message
COOLDOWN_S = float(os.getenv("GEMINI_COOLDOWN_SECONDS", "120"))         # skip Gemini this long after an outage
FALLBACK_MODELS = [m.strip() for m in os.getenv("GEMINI_FALLBACK_MODELS", "gemini-flash-latest,gemini-3.5-flash").split(",")
                   if m.strip()]


def _kind(err: Exception) -> str:
    """Classify an SDK/HTTP error: 'auth' (stop), 'model' (try next model), 'busy' (try next model, then cool down)."""
    code = getattr(err, "code", None) or getattr(err, "status_code", None)
    txt = f"{code} {err}".upper()
    if code in (401, 403) or "API_KEY_INVALID" in txt or "PERMISSION_DENIED" in txt:
        return "auth"
    if code == 404 or "NOT_FOUND" in txt or "NO LONGER AVAILABLE" in txt:
        return "model"
    return "busy"          # 429 / 500 / 503 / timeouts / connection errors


class GeminiExtractor:
    def __init__(self, api_key: str, model: str = GEMINI_MODEL):
        from google import genai            # pip install google-genai
        from google.genai import types
        self.types = types
        http = None
        try:     # short timeout, no long hidden retries (fields exist in recent google-genai versions)
            http = types.HttpOptions(timeout=int(CALL_TIMEOUT_S * 1000), retry_options=types.HttpRetryOptions(attempts=1))
        except Exception:
            try:
                http = types.HttpOptions(timeout=int(CALL_TIMEOUT_S * 1000))
            except Exception:
                http = None
        self.client = genai.Client(api_key=api_key, http_options=http) if http else genai.Client(api_key=api_key)
        self.models = [model] + [m for m in FALLBACK_MODELS if m != model]
        self.last_model = model

    def _config(self):
        kw = dict(system_instruction=SYSTEM, temperature=0, response_mime_type="application/json", response_schema=Patch)
        try:
            kw["automatic_function_calling"] = self.types.AutomaticFunctionCallingConfig(disable=True)
        except Exception:
            pass
        return self.types.GenerateContentConfig(**kw)

    def _call(self, model: str, prompt: str) -> str:
        return self.client.models.generate_content(model=model, contents=prompt, config=self._config()).text

    def extract(self, text: str, asked: str | None, ctx, draft: dict) -> dict:
        prompt = _prompt(text, asked, ctx, draft)
        start, last_err = time.monotonic(), None
        for model in self.models:
            if time.monotonic() - start > TURN_BUDGET_S - CALL_TIMEOUT_S and last_err:
                break
            try:
                raw = self._call(model, prompt)
                self.last_model = model
                return _clean(json.loads(raw))
            except Exception as e:
                last_err = e
                kind = _kind(e)
                log.warning("Gemini model %s failed (%s): %s", model, kind, str(e)[:300])
                if kind == "auth":
                    break
        raise last_err or RuntimeError("Gemini unavailable")


def _clean(data: dict) -> dict:
    out = {}
    for k, v in data.items():
        if v in (None, [], ""):
            continue
        if k in ("mandatory_skills", "nice_skills"):
            v = [{"skill": i["skill"], "min_proficiency": i.get("min_proficiency")} for i in v]
        if k == "skill_levels":
            v = {i["skill"]: i["min_proficiency"] for i in v if i.get("min_proficiency")}
        if k == "exp_min" and data.get("exp_max") is None:
            out["exp_max"] = float(v) + (3 if float(v) < 5 else 5)
        out[k] = v
    return out


_EXTRACTOR: GeminiExtractor | None = None
_MODE: str | None = None


def mode() -> dict:
    """Which brain is active: {'mode': 'gemini'|'rules', 'model': ..., 'reason': ...}"""
    global _EXTRACTOR, _MODE
    if _MODE is None:
        key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not key:
            _MODE = "rules: GEMINI_API_KEY not set"
        else:
            try:
                _EXTRACTOR = GeminiExtractor(key)
                _MODE = "gemini"
            except Exception as e:  # SDK missing etc.
                _MODE = f"rules: Gemini unavailable ({e.__class__.__name__}: {e})"
    if _MODE == "gemini":
        return {"mode": "gemini", "model": GEMINI_MODEL, "reason": None}
    return {"mode": "rules", "model": None, "reason": _MODE.split(": ", 1)[1]}


def reset():
    """Re-read the environment (e.g. after the API key was added to backend/.env)."""
    global _EXTRACTOR, _MODE, GEMINI_MODEL, _COOLDOWN_UNTIL
    _COOLDOWN_UNTIL = 0.0
    from ... import config
    config.reload_env_file()
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    _EXTRACTOR, _MODE = None, None


_COOLDOWN_UNTIL = 0.0


def understand(text: str, asked: str | None, ctx, draft: dict) -> tuple[dict, str]:
    global _COOLDOWN_UNTIL
    rb = rules.extract(text, asked, ctx, draft)
    if mode()["mode"] != "gemini" or rb.get("intent") in ("cancel", "confirm", "skip"):
        return rb, "rules"
    if time.monotonic() < _COOLDOWN_UNTIL:
        return rb, "rules (Gemini paused after an outage)"
    try:
        g = _EXTRACTOR.extract(text, asked, ctx, draft)
    except Exception as e:
        log.warning("Gemini extraction failed, using rules: %s", str(e)[:300])
        if _kind(e) == "busy":
            _COOLDOWN_UNTIL = time.monotonic() + COOLDOWN_S
        return rb, "rules (Gemini error)"
    merged = dict(rb)
    merged.update(g)
    if g.get("intent") in (None, "unknown") and rb.get("intent") not in (None, "unknown"):
        merged["intent"] = rb["intent"]
    return merged, "gemini"
