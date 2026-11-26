"""FastAPI dependencies: DB connection per request, current user, role guards."""
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import config
from .db import connect
from .services import auth

bearer = HTTPBearer(auto_error=False)


def get_db():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def current_user(cred: HTTPAuthorizationCredentials | None = Depends(bearer), conn=Depends(get_db)) -> dict:
    if cred is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = auth.decode_token(cred.credentials)          # raises Unauthorized → 401 via handler
    user = auth.get_user(conn, payload["sub"])
    if not user:
        raise HTTPException(status_code=401, detail="User no longer exists")
    return user


def require_roles(*roles: str):
    def guard(user: dict = Depends(current_user)) -> dict:
        if user["app_role"] not in roles:
            raise HTTPException(status_code=403, detail=f"Requires role: {', '.join(roles)}")
        return user
    return guard


privileged = require_roles(*config.PRIVILEGED_ROLES)
hr_only = require_roles("hr")


def can_view_employee(user: dict, emp_id: str):
    if user["user_id"] != emp_id and user["app_role"] not in config.PRIVILEGED_ROLES:
        raise HTTPException(status_code=403, detail="You can only view your own profile")
