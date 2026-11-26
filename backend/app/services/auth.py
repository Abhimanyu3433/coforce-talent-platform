"""Password hashing (stdlib PBKDF2), JWT tokens and user lookup."""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt

from .. import config
from ..db import one, rows
from .errors import BadRequest, Unauthorized

_ITER = 200_000


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ITER)
    return f"pbkdf2${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(password, salt).split("$")[2], digest)


USER_SQL = """
SELECT u.user_id, u.email, u.app_role, u.password_hash,
       u.can_view_all_employees, u.can_raise_demand, u.can_approve_overprovision, u.can_approve_release,
       e.first_name, e.last_name, e.designation, e.grade, e.ibu_id, e.function, e.role_family
FROM users u JOIN employees e ON e.emp_id = u.user_id
"""


def _public(u: dict) -> dict:
    u = dict(u)
    u.pop("password_hash", None)
    for k in ("can_view_all_employees", "can_raise_demand", "can_approve_overprovision", "can_approve_release"):
        u[k] = bool(u[k])
    u["name"] = f"{u['first_name']} {u['last_name']}"
    return u


def get_user(conn, user_id: str) -> dict | None:
    u = one(conn, USER_SQL + " WHERE u.user_id = ?", (user_id,))
    return _public(u) if u else None


def authenticate(conn, email: str, password: str) -> dict:
    u = one(conn, USER_SQL + " WHERE lower(u.email) = lower(?)", (email.strip(),))
    if not u:
        raise Unauthorized("Invalid email or password")
    if u["password_hash"]:
        ok = verify_password(password, u["password_hash"])
    else:
        ok = config.DEMO_MODE and password == config.DEMO_PASSWORD
        if ok:
            conn.execute("UPDATE users SET password_hash=? WHERE user_id=?", (hash_password(password), u["user_id"]))
    if not ok:
        raise Unauthorized("Invalid email or password")
    conn.execute("UPDATE users SET last_login=? WHERE user_id=?", (datetime.now(timezone.utc).isoformat(), u["user_id"]))
    conn.commit()
    return _public(u)


def create_token(user: dict) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": user["user_id"], "role": user["app_role"], "iat": now,
               "exp": now + timedelta(minutes=config.TOKEN_MINUTES)}
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise Unauthorized("Session expired, please sign in again")
    except jwt.PyJWTError:
        raise Unauthorized("Invalid token")


def change_password(conn, user_id: str, old: str, new: str):
    u = one(conn, "SELECT u.email FROM users u WHERE u.user_id=?", (user_id,))
    authenticate(conn, u["email"], old)
    if len(new) < 8 or new.isalpha() or new.isdigit():
        raise BadRequest("Password must be at least 8 characters and mix letters with numbers/symbols")
    conn.execute("UPDATE users SET password_hash=? WHERE user_id=?", (hash_password(new), user_id))
    conn.commit()


def demo_users(conn) -> list[dict]:
    """One sample login per app role (only exposed when DEMO_MODE is on)."""
    out = []
    for role in ("manager", "lead", "hr", "employee"):
        out += rows(conn, """SELECT u.email, u.app_role, e.first_name || ' ' || e.last_name AS name, e.designation, e.ibu_id
                             FROM users u JOIN employees e ON e.emp_id=u.user_id
                             WHERE u.app_role=? AND e.status != 'notice' ORDER BY u.user_id LIMIT 1""", (role,))
    return out
