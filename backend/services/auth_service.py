"""
services/auth_service.py
-------------------------
Password hashing (Argon2id) and JWT token management.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError

from config import settings

# Argon2id: m=64MB, t=3, p=4 (OWASP recommended minimums)
_ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)


# ── Password ──────────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, password)
    except (VerifyMismatchError, VerificationError):
        return False


# ── Access token (short-lived JWT) ────────────────────────────────────────────

def create_access_token(user_id: str, role: str) -> str:
    expires = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {
        "sub":  user_id,
        "role": role,
        "type": "access",
        "exp":  expires,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    """
    Decode and validate.  Raises jwt.PyJWTError on any failure
    (expired, invalid signature, wrong type).
    """
    data = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    if data.get("type") != "access":
        raise jwt.InvalidTokenError("Not an access token")
    return data


# ── Refresh token (opaque random token, stored as hash) ───────────────────────

def create_refresh_token() -> tuple[str, str]:
    """
    Returns (plain_token, sha256_hash).
    Store only the hash in the DB; send plain_token to the client.
    """
    plain = secrets.token_urlsafe(48)
    hashed = hashlib.sha256(plain.encode()).hexdigest()
    return plain, hashed


def hash_refresh_token(plain: str) -> str:
    return hashlib.sha256(plain.encode()).hexdigest()


def refresh_token_expires_at() -> str:
    exp = datetime.now(timezone.utc) + timedelta(
        days=settings.REFRESH_TOKEN_EXPIRE_DAYS
    )
    return exp.strftime("%Y-%m-%d %H:%M:%S")


def is_refresh_token_expired(expires_at: str) -> bool:
    exp = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=timezone.utc
    )
    return datetime.now(timezone.utc) > exp
