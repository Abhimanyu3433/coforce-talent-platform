from fastapi import APIRouter, Depends, HTTPException

from .. import config
from ..deps import current_user, get_db
from ..schemas import ChangePasswordIn, LoginIn
from ..services import auth

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
def login(body: LoginIn, conn=Depends(get_db)):
    user = auth.authenticate(conn, body.email, body.password)
    return {"access_token": auth.create_token(user), "token_type": "bearer", "user": user}


@router.get("/me")
def me(user=Depends(current_user)):
    return user


@router.post("/change-password")
def change_password(body: ChangePasswordIn, user=Depends(current_user), conn=Depends(get_db)):
    auth.change_password(conn, user["user_id"], body.old_password, body.new_password)
    return {"ok": True}


@router.get("/demo-users")
def demo_users(conn=Depends(get_db)):
    if not config.DEMO_MODE:
        raise HTTPException(status_code=404, detail="Not found")
    return {"password": config.DEMO_PASSWORD, "users": auth.demo_users(conn)}
