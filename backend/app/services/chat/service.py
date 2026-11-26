"""Chat sessions: start, post a message, read back. State is stored as JSON per session."""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone

from ...db import one, rows
from ..errors import BadRequest, Forbidden, NotFound
from . import graph, jd_file, llm, slots
from .context import ChatContext


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _save_msg(conn, sid, m: dict):
    meta = {k: v for k, v in m.items() if k not in ("role", "content")}
    conn.execute("INSERT INTO chat_messages (session_id, role, content, meta, at) VALUES (?,?,?,?,?)",
                 (sid, m["role"], m["content"], json.dumps(meta), _now()))


def _load(conn, sid: str, user: dict) -> dict:
    r = one(conn, "SELECT * FROM chat_sessions WHERE session_id=?", (sid,))
    if not r:
        raise NotFound("Chat session not found")
    if r["user_id"] != user["user_id"]:
        raise Forbidden("This chat belongs to someone else")
    r["state"] = json.loads(r["state"])
    return r


def _persist(conn, sid: str, state: dict):
    status = {"created": "created"}.get(state["phase"], "open")
    conn.execute("UPDATE chat_sessions SET state=?, status=?, demand_id=?, updated_on=? WHERE session_id=?",
                 (json.dumps(state), status, state.get("demand_id"), _now(), sid))


def status() -> dict:
    return llm.mode() | {"engine": graph.engine_name()}


def start(conn, user: dict) -> dict:
    # reuse an untouched chat (only the greeting so far) instead of piling up empty sessions
    empty = one(conn, """SELECT s.session_id FROM chat_sessions s WHERE s.user_id=? AND s.status='open'
                         AND NOT EXISTS (SELECT 1 FROM chat_messages m WHERE m.session_id=s.session_id AND m.role='user')
                         ORDER BY s.updated_on DESC LIMIT 1""", (user["user_id"],))
    if empty:
        return view(conn, empty["session_id"], user)
    ctx = ChatContext(conn, user)
    sid = "CH" + secrets.token_hex(6)
    state = slots.new_state()
    q, quick = slots.question("role_title", state, ctx)
    state["last_asked"] = "role_title"
    m = status()
    hello = f"Hi {user['first_name']}! I'll help you raise a demand and check internal supply as we go. " + q
    conn.execute("INSERT INTO chat_sessions (session_id, user_id, created_on, updated_on, status, state, engine) VALUES (?,?,?,?,?,?,?)",
                 (sid, user["user_id"], _now(), _now(), "open", json.dumps(state), m["mode"]))
    _save_msg(conn, sid, {"role": "assistant", "content": hello, "kind": "question", "quick_replies": quick, "field": "role_title"})
    conn.commit()
    return view(conn, sid, user)


def post(conn, user: dict, sid: str, text: str, meta: dict | None = None, max_chars: int = 6000) -> dict:
    text = (text or "").strip()
    if not text:
        raise BadRequest("Message is empty")
    if len(text) > max_chars:
        raise BadRequest(f"Message is too long (max {max_chars} characters)")
    sess = _load(conn, sid, user)
    state = sess["state"]
    _save_msg(conn, sid, {"role": "user", "content": text, **(meta or {})})
    if state["phase"] == "created":
        reply = {"role": "assistant", "kind": "created", "demand_id": state["demand_id"],
                 "content": f"This chat already created {state['demand_id']}. Start a new chat to raise another demand.",
                 "quick_replies": ["View matches", "Raise another demand"]}
        _save_msg(conn, sid, reply)
        conn.commit()
        return view(conn, sid, user, new_from=len(rows(conn, "SELECT id FROM chat_messages WHERE session_id=?", (sid,))) - 2)
    ctx = ChatContext(conn, user)
    before = len(rows(conn, "SELECT id FROM chat_messages WHERE session_id=?", (sid,))) - 1
    out = graph.run_turn(ctx, state, text)
    for r in out["replies"]:
        r["source"] = out.get("source")
        _save_msg(conn, sid, r)
    _persist(conn, sid, state)
    conn.commit()
    return view(conn, sid, user, new_from=before)


def post_jd_pdf(conn, user: dict, sid: str, filename: str, data_base64: str) -> dict:
    """Read a job-description PDF and treat its text exactly like a pasted JD."""
    _load(conn, sid, user)                                    # ownership check before doing any work
    info = jd_file.extract_pdf_text(jd_file.decode_base64(data_base64))
    name = (filename or "job-description.pdf").replace("\\", "/").split("/")[-1][:120]
    meta = {"attachment": {"name": name, "pages": info["pages"], "chars": info["chars"], "truncated": info["truncated"]}}
    return post(conn, user, sid, info["text"], meta=meta, max_chars=jd_file.MAX_CHARS)


def view(conn, sid: str, user: dict, new_from: int | None = None) -> dict:
    sess = _load(conn, sid, user)
    state = sess["state"]
    msgs = []
    for m in rows(conn, "SELECT role, content, meta, at FROM chat_messages WHERE session_id=? ORDER BY id", (sid,)):
        msgs.append({"role": m["role"], "content": m["content"], "at": m["at"], **json.loads(m["meta"] or "{}")})
    todo = slots.missing(state)
    return {
        "session_id": sid, "status": sess["status"], "phase": state["phase"], "demand_id": state.get("demand_id"),
        "draft": state["draft"], "payload": slots.to_payload(state), "missing": todo,
        "required_missing": [f for f in slots.REQUIRED_FOR_CREATE if f in todo], "can_create": slots.can_create(state),
        "progress": round(100 * (len(slots.FIELDS) - len(todo)) / len(slots.FIELDS)),
        "supply": state.get("supply"), "messages": msgs, "new_from": new_from, "engine": status(),
    }


def list_sessions(conn, user: dict, limit: int = 20) -> list[dict]:
    out = rows(conn, """SELECT session_id, created_on, updated_on, status, demand_id, state FROM chat_sessions
                        WHERE user_id=? AND EXISTS (SELECT 1 FROM chat_messages m WHERE m.session_id=chat_sessions.session_id
                                                    AND m.role='user')
                        ORDER BY updated_on DESC LIMIT ?""", (user["user_id"], limit))
    for o in out:
        d = json.loads(o.pop("state"))["draft"]
        o["title"] = (f"{d.get('positions') or ''} × {d['role_title']}".strip(" ×") if d.get("role_title") else "New demand")
    return out
