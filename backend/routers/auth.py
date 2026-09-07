"""
routers/auth.py
----------------
POST /auth/login
POST /auth/refresh
POST /auth/logout
POST /auth/register   (admin only)
GET  /auth/me
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

log     = logging.getLogger("bess.auth")
limiter = Limiter(key_func=get_remote_address)

from database import get_db
from dependencies import get_current_user, require_admin
from models.db_models import User, RefreshToken
from models.schemas import (
    LoginRequest, RefreshRequest, TokenResponse,
    AccessTokenResponse, UserOut,
)
from services.auth_service import (
    hash_password, verify_password,
    create_access_token, create_refresh_token,
    hash_refresh_token, refresh_token_expires_at,
    is_refresh_token_expired,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ── Login ─────────────────────────────────────────────────────────────────────

@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def login(request: Request, body: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(
        User.username == body.username,
        User.is_active == True,
    ).first()

    if not user or not verify_password(body.password, user.password_hash):
        log.warning("auth.login.failed", extra={
            "username": body.username,
            "ip": request.client.host if request.client else "unknown",
        })
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    log.info("auth.login.success", extra={"username": user.username, "role": user.role})

    access_token               = create_access_token(user.id, user.role)
    plain_refresh, hashed_refresh = create_refresh_token()

    rt = RefreshToken(
        user_id    = user.id,
        token_hash = hashed_refresh,
        device_id  = body.device_id,
        expires_at = refresh_token_expires_at(),
    )
    db.add(rt)
    db.commit()

    return TokenResponse(
        access_token  = access_token,
        refresh_token = plain_refresh,
        user          = UserOut.model_validate(user),
    )


# ── Refresh ───────────────────────────────────────────────────────────────────

@router.post("/refresh", response_model=AccessTokenResponse)
def refresh(body: RefreshRequest, db: Session = Depends(get_db)):
    hashed = hash_refresh_token(body.refresh_token)
    rt = db.query(RefreshToken).filter(
        RefreshToken.token_hash == hashed,
        RefreshToken.revoked_at == None,
    ).first()

    if not rt:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    if is_refresh_token_expired(rt.expires_at):
        rt.revoked_at = _now()
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token expired")

    user = db.query(User).filter(User.id == rt.user_id, User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    # Rotate: revoke old, issue new
    rt.revoked_at = _now()
    plain_new, hashed_new = create_refresh_token()
    new_rt = RefreshToken(
        user_id    = user.id,
        token_hash = hashed_new,
        device_id  = body.device_id or rt.device_id,
        expires_at = refresh_token_expires_at(),
    )
    db.add(new_rt)
    db.commit()

    return AccessTokenResponse(
        access_token  = create_access_token(user.id, user.role),
        refresh_token = plain_new,
    )


# ── Logout ────────────────────────────────────────────────────────────────────

@router.post("/logout")
def logout(
    body: RefreshRequest,
    db:   Session = Depends(get_db),
    _user: User   = Depends(get_current_user),
):
    hashed = hash_refresh_token(body.refresh_token)
    rt = db.query(RefreshToken).filter(RefreshToken.token_hash == hashed).first()
    if rt:
        rt.revoked_at = _now()
        db.commit()
    return {"message": "Logged out"}


# ── Register (admin only) ─────────────────────────────────────────────────────

class RegisterRequest(LoginRequest):
    email: str | None = None
    role:  str        = "engineer"


@router.post("/register", response_model=UserOut, status_code=201)
def register(
    body:  RegisterRequest,
    db:    Session = Depends(get_db),
    _admin: User   = Depends(require_admin),
):
    existing = db.query(User).filter(User.username == body.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username already taken")

    user = User(
        username      = body.username,
        email         = body.email,
        password_hash = hash_password(body.password),
        role          = body.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserOut.model_validate(user)


# ── Me ────────────────────────────────────────────────────────────────────────

@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return UserOut.model_validate(user)


# ── List users (admin only) ───────────────────────────────────────────────────

from typing import List as _List

@router.get("/users", response_model=_List[UserOut])
def list_users(
    db:     Session = Depends(get_db),
    _admin: User    = Depends(require_admin),
):
    users = db.query(User).order_by(User.created_at.asc()).all()
    return [UserOut.model_validate(u) for u in users]
