"""
routers/auth.py
----------------
POST  /auth/login
POST  /auth/refresh
POST  /auth/logout
POST  /auth/register        (admin only)
GET   /auth/me
GET   /auth/users           (admin only — every account, active or not)
PATCH /auth/users/{id}      (admin only — role / active, see update_user)
GET   /auth/assignable      (admin + engineer — active accounts only)
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
    AccessTokenResponse, UserOut, AssignableUser,
    UserAdminUpdate, UserAdminOut,
)
from services.auth_service import (
    hash_password, verify_password,
    create_access_token, create_refresh_token,
    hash_refresh_token, refresh_token_expires_at,
    is_refresh_token_expired,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# The roles the whole system understands: dependencies.require_admin, the
# per-route checks in routers/sync.py and routers/checklists.py, and the
# desktop's team_service.ASSIGNABLE_ROLES. Anything else would be an account
# with no permissions at all, so an unknown role is refused rather than stored.
ROLES = ("admin", "engineer", "technician")


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
    """Every account, active or not, each with its `is_active` flag.

    Deliberately not filtered: an admin has to see a deactivated account in
    order to reactivate it, and hiding it would look like the account was
    deleted. /auth/assignable is the filtered list — work is never offered to
    someone who has left."""
    users = db.query(User).order_by(User.created_at.asc()).all()
    return [UserOut.model_validate(u) for u in users]


# ── Change an account: role / active (admin only) ─────────────────────────────

def _revoke_refresh_tokens(db: Session, user_id: str) -> int:
    """Revoke every still-valid refresh token of a user. Returns the count.

    Without this, deactivating an account only blocks the *next login*: this
    deployment issues refresh tokens with a 365-day lifetime (render.yaml), so
    a phone that already holds one could keep minting fresh access tokens for a
    year after the person left."""
    rows = db.query(RefreshToken).filter(
        RefreshToken.user_id == user_id,
        RefreshToken.revoked_at == None,          # noqa: E711  (SQL NULL)
    ).all()
    stamp = _now()
    for rt in rows:
        rt.revoked_at = stamp
    return len(rows)


@router.patch("/users/{user_id}", response_model=UserAdminOut)
def update_user(
    user_id: str,
    body:    UserAdminUpdate,
    db:      Session = Depends(get_db),
    admin:   User    = Depends(require_admin),
):
    """Deactivate / reactivate an account, or move it to another role.

    The only way to revoke someone's access: there is no user DELETE, because
    old work records name their author and must keep doing so.

    Deactivating also revokes the account's outstanding refresh tokens, so a
    phone that is already signed in cannot renew its session. And because every
    authenticated route re-loads the user with `is_active == True`
    (dependencies.get_current_user), the access token it already holds is
    refused too: the cut-off is immediate, not "within the 30-minute token
    lifetime". Same mechanism for a role change — the role comes from the
    account row on every request, not from the token's claim, so it applies on
    the person's next request without a new login. Both are pinned by
    tests/test_account_admin.py.

    Lockout guard: an admin may not deactivate or demote their own account, and
    the last active admin may not be deactivated or demoted. Either would leave
    the deployment with nobody who can administer it, and the only recovery is
    wiping the server's disk."""
    if body.role is None and body.is_active is None:
        raise HTTPException(status_code=400, detail="Nothing to change")
    if body.role is not None and body.role not in ROLES:
        raise HTTPException(
            status_code=400,
            detail="Unknown role '{}'. Known roles: {}".format(
                body.role, ", ".join(ROLES)),
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    was_active   = bool(user.is_active)
    old_role     = user.role or ""
    role_after   = body.role      if body.role      is not None else old_role
    active_after = body.is_active if body.is_active is not None else was_active

    # Would this take away the last hand on the wheel? Only an *active admin*
    # losing either the role or the account can do that. A request from this
    # very endpoint proves at least one active admin exists (require_admin →
    # get_current_user filters is_active), so changing an already-inactive
    # admin cannot lock anybody out.
    loses_admin = was_active and old_role == "admin" and (
        not active_after or role_after != "admin")
    if loses_admin:
        if user.id == admin.id:
            log.warning("auth.user.self_lockout_refused: username='%s'", user.username)
            raise HTTPException(
                status_code=400,
                detail="You cannot deactivate or demote your own admin account. "
                       "Ask another administrator to do it.",
            )
        # Belt and braces. With the self-check above this cannot currently fire:
        # the caller is an active admin, so for any *other* target there is
        # always at least one active admin left. It is the guard that survives
        # if that ever stops being true (a service token, an admin group, a
        # bulk change), and it costs one COUNT.
        others = db.query(User).filter(
            User.role == "admin",
            User.is_active == True,                # noqa: E712
            User.id != user.id,
        ).count()
        if others == 0:
            log.warning("auth.user.last_admin_refused: username='%s'", user.username)
            raise HTTPException(
                status_code=400,
                detail="'{}' is the last active administrator. Create or "
                       "promote another admin first, otherwise nobody could "
                       "administer this server.".format(user.username),
            )

    revoked = 0
    if body.role is not None and role_after != old_role:
        user.role = role_after
        log.info("auth.user.role_changed: username='%s' %s -> %s by='%s'",
                 user.username, old_role or "(none)", role_after, admin.username)
    if body.is_active is not None:
        user.is_active = active_after
        if not active_after:
            # Also on a repeat deactivation: it sweeps up tokens left behind by
            # a first attempt whose revocation did not reach the database.
            revoked = _revoke_refresh_tokens(db, user.id)
            log.info("auth.user.deactivated: username='%s' sessions_revoked=%d by='%s'",
                     user.username, revoked, admin.username)
        elif not was_active:
            log.info("auth.user.reactivated: username='%s' by='%s'",
                     user.username, admin.username)

    db.commit()
    db.refresh(user)
    return UserAdminOut(**UserOut.model_validate(user).model_dump(),
                        revoked_sessions=revoked)


# ── Who a job can be given to ─────────────────────────────────────────────────

@router.get("/assignable", response_model=_List[AssignableUser])
def assignable_users(
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    """The accounts the desktop may hand work to, as id + username + role.

    Separate from /auth/users, which is admin-only account management and
    returns the e-mail addresses with it: planning work only needs the name,
    and an engineer who plans must be able to ask for the list. A technician
    does not assign, so they do not get it.

    Active accounts only, and it stays that way: a deactivated account drops
    out of the list, so work cannot be handed to someone who has left. The
    desktop mirrors exactly this list into `sync_users` (team_service.
    save_users marks everyone it does not receive inactive), which is how a
    deactivation reaches the "Assigned to" picker."""
    if user.role not in ("admin", "engineer"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only the office assigns work")
    rows = (db.query(User).filter(User.is_active == True)
              .order_by(User.username.asc()).all())
    return [AssignableUser(id=u.id, username=u.username, role=u.role or "")
            for u in rows]
