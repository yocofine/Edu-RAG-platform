from datetime import timedelta

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import csrf_protected, current_user
from ..models import Role, User
from ..rate_limit import limit_auth
from ..schemas import LoginRequest, PasswordChangeRequest, RegisterRequest
from ..security import (
    create_access_token, create_refresh_token, csrf_token, decode_token, hash_password, verify_password,
)


router = APIRouter(prefix="/auth", tags=["认证"])
settings = get_settings()


def _user_payload(user: User) -> dict:
    return {"id": user.id, "username": user.username, "role": user.role, "created_at": user.created_at}


def _set_auth_cookies(response: Response, user: User) -> str:
    access = create_access_token(user.id, user.role)
    refresh = create_refresh_token(user.id, user.role)
    csrf = csrf_token()
    common = {"httponly": True, "secure": settings.cookie_secure, "samesite": "lax", "path": "/"}
    response.set_cookie("access_token", access, max_age=settings.access_token_minutes * 60, **common)
    response.set_cookie("refresh_token", refresh, max_age=settings.refresh_token_days * 86400, **common)
    response.set_cookie(
        "csrf_token", csrf, max_age=settings.refresh_token_days * 86400,
        httponly=False, secure=settings.cookie_secure, samesite="lax", path="/",
    )
    return csrf


@router.post("/register", status_code=201, dependencies=[Depends(limit_auth)])
def register(payload: RegisterRequest, response: Response, db: Session = Depends(get_db)):
    username = payload.username.strip()
    if db.scalar(select(User).where(func.lower(User.username) == username.lower())):
        raise HTTPException(status_code=409, detail="用户名已存在")
    user_count = db.scalar(select(func.count()).select_from(User)) or 0
    user = User(
        username=username,
        password_hash=hash_password(payload.password),
        role=Role.admin.value if user_count == 0 else Role.user.value,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    csrf = _set_auth_cookies(response, user)
    return {"user": _user_payload(user), "csrf_token": csrf}


@router.post("/login", dependencies=[Depends(limit_auth)])
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(func.lower(User.username) == payload.username.strip().lower()))
    if not user or not verify_password(payload.password, user.password_hash) or user.disabled:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    csrf = _set_auth_cookies(response, user)
    return {"user": _user_payload(user), "csrf_token": csrf}


@router.post("/refresh")
def refresh(
    response: Response,
    refresh_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
):
    if not refresh_token:
        raise HTTPException(status_code=401, detail="刷新令牌不存在")
    try:
        token = decode_token(refresh_token, "refresh")
        user = db.get(User, int(token["sub"]))
    except Exception as exc:
        raise HTTPException(status_code=401, detail="刷新令牌无效") from exc
    if not user or user.disabled:
        raise HTTPException(status_code=401, detail="账号不可用")
    csrf = _set_auth_cookies(response, user)
    return {"user": _user_payload(user), "csrf_token": csrf}


@router.post("/logout", dependencies=[Depends(csrf_protected)])
def logout(response: Response):
    for name in ("access_token", "refresh_token", "csrf_token"):
        response.delete_cookie(name, path="/")
    return {"message": "已退出登录"}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return {"user": _user_payload(user)}


@router.put("/password", dependencies=[Depends(csrf_protected)])
def change_password(payload: PasswordChangeRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not verify_password(payload.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="原密码不正确")
    if payload.old_password == payload.new_password:
        raise HTTPException(status_code=400, detail="新密码不能与原密码相同")
    user.password_hash = hash_password(payload.new_password)
    db.commit()
    return {"message": "密码修改成功"}
