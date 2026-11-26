"""One conversation turn as a graph.

    START → understand → apply ─┬─ cancel ─────────────→ END
                                ├─ create ─────────────→ END
                                └─ check → respond ────→ END

With `langgraph` installed the same node functions are compiled into a LangGraph StateGraph; otherwise a tiny
runner walks the identical edges. Set COFORCE_USE_LANGGRAPH=0 to force the built-in runner.
"""
from __future__ import annotations

import json
import os
from typing import Any, TypedDict

from .. import demands, matching
from ..errors import ServiceError
from . import llm, slots
from .apply import apply_patch

try:
    from langgraph.graph import END, START, StateGraph
    HAS_LANGGRAPH = os.getenv("COFORCE_USE_LANGGRAPH", "1") == "1"
except ImportError:      # pragma: no cover - depends on environment
    HAS_LANGGRAPH = False
    START, END = "__start__", "__end__"


class TurnState(TypedDict, total=False):
    ctx: Any
    session: dict
    text: str
    patch: dict
    source: str
    notes: list
    issues: list
    replies: list
    supply_fresh: bool


# ---------------------------------------------------------------- nodes
def understand(ts: TurnState) -> dict:
    s = ts["session"]
    asked = s.get("last_asked") if s["phase"] == "collect" else "confirm"
    patch, source = llm.understand(ts["text"], asked, ts["ctx"], s["draft"])
    return {"patch": patch, "source": source}


def apply(ts: TurnState) -> dict:
    s, ctx, p = ts["session"], ts["ctx"], ts["patch"]
    notes, issues = [], []
    intent = p.get("intent")
    field = s.get("last_asked")
    if intent == "skip" and field and field != "confirm":
        note = slots.apply_default(field, s, ctx)
        if note:
            notes.append(note)
        if field not in s["answered"]:
            s["answered"].append(field)
    elif intent == "none" and field in ("nice", "project", "engagement"):
        if field == "engagement":
            notes.append(slots.apply_default("engagement", s, ctx))
        if field not in s["answered"]:
            s["answered"].append(field)
    elif intent == "confirm" and not slots.can_create(s):
        need = [f.replace("_", " ") for f in slots.REQUIRED_FOR_CREATE if not slots.is_filled(f, s)]
        issues.append(f"Before I can create it I still need: {', '.join(need)}.")
    elif intent not in ("cancel", "confirm"):
        n, i = apply_patch(s, p, ctx)
        notes += n
        issues += i
        if intent == "unknown" and not n and not i:
            issues.append("Sorry, I didn't catch that." if field != "confirm" else
                          "Say “create” to raise the demand, or tell me what to change.")
    return {"notes": notes, "issues": issues}


def route(ts: TurnState) -> str:
    s, intent = ts["session"], ts["patch"].get("intent")
    if intent == "cancel":
        return "cancel"
    if intent == "confirm" and slots.can_create(s):
        return "create"
    return "check"


def check(ts: TurnState) -> dict:
    s, ctx = ts["session"], ts["ctx"]
    payload = slots.supply_payload(s, ctx.today)
    fresh = False
    if payload:
        sig = json.dumps({k: payload[k] for k in ("mandatory_skills", "nice_skills", "locations", "remote_allowed", "needed_by",
                                                  "grade_min", "grade_max", "exp_min", "exp_max", "positions",
                                                  "overprovision_factor", "ibu_id", "account_id", "project_id")}, sort_keys=True)
        if sig != s.get("supply_sig"):
            try:
                p = demands.normalize_payload(ctx.conn, payload)
                sup = matching.supply_check(ctx.conn, demands.to_spec(ctx.conn, p))
                s["supply"] = {k: sup[k] for k in ("verdict", "needed", "eligible", "fully_matching", "fully_matching_in_time",
                                                   "ready_in_time_with_training", "by_pool", "relaxations", "top")}
                s["supply"]["assumed_date"] = not s["draft"].get("needed_by")
                s["supply_sig"] = sig
                fresh = True
            except ServiceError:
                pass
    return {"supply_fresh": fresh}


def _supply_line(sup: dict) -> str:
    word = {"healthy": "Looks healthy", "trainable": "Trainable", "scarce": "Scarce"}[sup["verdict"]]
    line = (f"**Internal supply — {word}:** {sup['fully_matching_in_time']} people fully match and are available in time; "
            f"{sup['ready_in_time_with_training']} could be ready with short training (you need {sup['needed']})")
    if sup.get("assumed_date"):
        line += ", assuming a start in 30 days"
    line += "."
    best = next((r for r in sup.get("relaxations", []) if r["gain"] > 0), None)
    if best and sup["verdict"] != "healthy":
        line += f" Bottleneck: making **{best['name']}** good-to-have would add {best['gain']} full matches."
    return line


def respond(ts: TurnState) -> dict:
    s, ctx = ts["session"], ts["ctx"]
    parts = []
    if ts["notes"]:
        parts.append("Got it — " + "; ".join(n for n in ts["notes"] if n) + ".")
    elif ts["patch"].get("intent") in ("provide", "edit") and not ts["issues"]:
        parts.append("Got it.")
    parts += ts["issues"]
    replies, quick, kind = [], [], "question"
    supply_meta = None
    if ts.get("supply_fresh") and s.get("supply"):
        parts.append(_supply_line(s["supply"]))
        supply_meta = s["supply"]
    todo = slots.missing(s)
    if todo:
        field = todo[0]
        if field == "skill_level" and all(m.get("min_proficiency") for m in s["draft"]["mandatory"]):
            s["answered"].append("skill_level")
            todo = slots.missing(s)
            field = todo[0] if todo else None
    else:
        field = None
    if field:
        q, quick = slots.question(field, s, ctx)
        parts.append(q)
        s["last_asked"], s["phase"] = field, "collect"
    else:
        s["last_asked"], s["phase"] = "confirm", "confirm"
        kind = "confirm"
        parts.append("Here's the demand:\n\n" + slots.summary(s) + "\n\nShall I create it and find matches?")
        quick = ["Create demand"]
        best = next((r for r in (s.get("supply") or {}).get("relaxations", []) if r["gain"] > 0), None)
        if best:
            quick.append(f"Make {best['name']} good-to-have")
        quick += ["Open in form", "Start over"]
    replies.append({"role": "assistant", "kind": kind, "content": "\n\n".join(p for p in parts if p), "quick_replies": quick,
                    "field": field or "confirm", **({"supply": supply_meta} if supply_meta else {})})
    return {"replies": replies}


def create(ts: TurnState) -> dict:
    s, ctx = ts["session"], ts["ctx"]
    defaults = []
    for f in slots.missing(s):                  # optional fields left open → defaults, stated in the reply
        n = slots.apply_default(f, s, ctx)
        if n:
            defaults.append(n)
        if f not in s["answered"]:
            s["answered"].append(f)
    try:
        d = demands.create(ctx.conn, ctx.user, slots.to_payload(s))
    except ServiceError as e:
        msg = e.detail if isinstance(e.detail, str) else e.detail.get("message", str(e.detail))
        return {"replies": [{"role": "assistant", "kind": "error", "content": f"I couldn't create it: {msg}. What should I change?",
                             "quick_replies": ["Open in form"]}]}
    s["phase"], s["demand_id"], s["last_asked"] = "created", d["demand_id"], None
    st = d.get("match_stats") or {}
    text = (f"Created **{d['demand_id']} — {d['role_title']}** and ran matching: {st.get('eligible', 0)} eligible, "
            f"{st.get('fully_matching', 0)} fully match all mandatory skills, {st.get('ready_in_time_with_training', 0)} "
            f"can be ready in time with training. Open the demand to shortlist and nominate.")
    if defaults:
        text += " Defaults used: " + " ".join(defaults)
    return {"replies": [{"role": "assistant", "kind": "created", "content": text, "demand_id": d["demand_id"],
                         "quick_replies": ["View matches", "Raise another demand"]}]}


def cancel(ts: TurnState) -> dict:
    s = ts["session"]
    fresh = slots.new_state()
    s.clear()
    s.update(fresh)
    q, quick = slots.question("role_title", s, ts["ctx"])
    s["last_asked"] = "role_title"
    return {"replies": [{"role": "assistant", "kind": "question", "content": "Starting over. " + q, "quick_replies": quick}]}


NODES = {"understand": understand, "apply": apply, "check": check, "respond": respond, "create": create, "cancel": cancel}
EDGES = {"understand": "apply", "check": "respond", "respond": END, "create": END, "cancel": END}
CONDITIONAL = {"apply": (route, {"cancel": "cancel", "create": "create", "check": "check"})}

_COMPILED = None


def _compiled():
    global _COMPILED
    if _COMPILED is None:
        g = StateGraph(TurnState)
        for name, fn in NODES.items():
            g.add_node(name, fn)
        g.add_edge(START, "understand")
        for a, b in EDGES.items():
            g.add_edge(a, b)
        for a, (fn, mapping) in CONDITIONAL.items():
            g.add_conditional_edges(a, fn, mapping)
        _COMPILED = g.compile()
    return _COMPILED


def run_turn(ctx, session: dict, text: str) -> TurnState:
    state: TurnState = {"ctx": ctx, "session": session, "text": text, "notes": [], "issues": [], "replies": [],
                        "supply_fresh": False, "patch": {}, "source": ""}
    if HAS_LANGGRAPH:
        return _compiled().invoke(state)
    node = "understand"
    while node != END:
        state.update(NODES[node](state))
        if node in CONDITIONAL:
            fn, mapping = CONDITIONAL[node]
            node = mapping[fn(state)]
        else:
            node = EDGES[node]
    return state


def engine_name() -> str:
    return "langgraph" if HAS_LANGGRAPH else "built-in graph runner"
