"""
models/db_models.py
--------------------
SQLAlchemy ORM table definitions for the backend.

Tables mirror the desktop work_log_entries schema, plus:
  - users         — authentication
  - refresh_tokens — revocable JWT refresh tokens
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, Integer, Boolean, ForeignKey, BigInteger, Float, Text,
    UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship

from database import Base


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _uuid() -> str:
    return str(uuid.uuid4())


# ── Users ─────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id           = Column(String, primary_key=True, default=_uuid)
    username     = Column(String, unique=True, nullable=False, index=True)
    email        = Column(String, unique=True, nullable=True)
    password_hash = Column(String, nullable=False)
    role         = Column(String, default="engineer")   # admin|engineer|technician
    is_active    = Column(Boolean, default=True)
    created_at   = Column(String, default=_now)

    entries = relationship("WorkLogEntry", back_populates="user", cascade="all, delete")
    tokens  = relationship("RefreshToken", back_populates="user", cascade="all, delete")


# ── Refresh tokens ────────────────────────────────────────────────────────────

class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id          = Column(String, primary_key=True, default=_uuid)
    user_id     = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash  = Column(String, unique=True, nullable=False, index=True)
    device_id   = Column(String, nullable=True)
    expires_at  = Column(String, nullable=False)
    created_at  = Column(String, default=_now)
    revoked_at  = Column(String, nullable=True)

    user = relationship("User", back_populates="tokens")


# ── Projects ──────────────────────────────────────────────────────────────────
# Mirror of the desktop projects table — published by the desktop on each sync
# so mobile clients can offer a project picker. IDs match desktop SQLite ids.

class Project(Base):
    __tablename__ = "projects"

    id           = Column(Integer, primary_key=True, autoincrement=False)
    name         = Column(String, nullable=False)
    project_type = Column(String, default="BESS")
    updated_at   = Column(String, default=_now)


# ── Work log entries ──────────────────────────────────────────────────────────

class WorkLogEntry(Base):
    __tablename__ = "work_log_entries"

    id               = Column(String, primary_key=True)     # UUID from client
    user_id          = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id       = Column(Integer, nullable=True, index=True)   # nullable: mobile entries have no project
    container_id     = Column(Integer, nullable=True)
    equipment_serial = Column(String, default="")
    site_location    = Column(String, default="")
    category         = Column(String, default="maintenance", index=True)
    description      = Column(Text, default="")
    log_date         = Column(String, nullable=False, index=True)   # YYYY-MM-DD
    created_at       = Column(String, nullable=False)
    updated_at       = Column(String, nullable=False, index=True)
    deleted_at       = Column(String, nullable=True, index=True)
    version          = Column(Integer, default=1, nullable=False)
    origin_device    = Column(String, nullable=True)        # 'desktop:<uid>' | 'mobile:<uuid>'
    sync_status      = Column(String, default="synced")
    # Optional technical-work fields (blank when the engineer has no info)
    fault_name       = Column(String, default="")           # fault / problem
    status           = Column(String, default="")           # '' | open | done
    sap_ticket       = Column(String, default="")
    spare_parts      = Column(String, default="")           # parts used (free text; stock link later)

    __table_args__ = (
        # Compound index for non-admin delta pull: WHERE user_id=? AND updated_at > ?
        Index("idx_wle_user_updated_at", "user_id", "updated_at"),
        # Partial-style index for pending entries (SQLite supports this via expression index)
        Index("idx_wle_sync_status", "sync_status"),
    )

    user   = relationship("User", back_populates="entries")
    images = relationship("WorkLogImage", back_populates="entry", cascade="all, delete")
    tags   = relationship("WorkLogTag",   back_populates="entry", cascade="all, delete")


# ── Work log tags ─────────────────────────────────────────────────────────────

class WorkLogTag(Base):
    __tablename__ = "work_log_tags"

    work_log_id = Column(String, ForeignKey("work_log_entries.id", ondelete="CASCADE"), primary_key=True)
    tag         = Column(String, primary_key=True)

    entry = relationship("WorkLogEntry", back_populates="tags")


# ── Work log images ───────────────────────────────────────────────────────────

class WorkLogImage(Base):
    __tablename__ = "work_log_images"

    id             = Column(String, primary_key=True, default=_uuid)
    work_log_id    = Column(String, ForeignKey("work_log_entries.id", ondelete="CASCADE"), nullable=False, index=True)
    file_path      = Column(String, nullable=False)     # relative to UPLOAD_DIR
    thumbnail_path = Column(String, nullable=True)
    filename       = Column(String, default="")
    size_bytes     = Column(BigInteger, default=0)
    sha256         = Column(String, default="")
    width          = Column(Integer, nullable=True)
    height         = Column(Integer, nullable=True)
    taken_at       = Column(String, nullable=True)
    uploaded_at    = Column(String, default=_now, nullable=False)
    updated_at     = Column(String, default=_now, nullable=False, index=True)
    upload_status  = Column(String, default="uploaded")

    entry = relationship("WorkLogEntry", back_populates="images")


# ── Sync idempotency records ───────────────────────────────────────────────────

class SyncIdempotencyRecord(Base):
    """
    Stores the result of a push batch keyed by (device_id, idempotency_key).
    Allows safe retry: if the server already processed this batch, return
    the cached response instead of reprocessing — preventing false conflicts.
    TTL: 24 hours.  Cleaned up at startup and periodically.
    """
    __tablename__ = "sync_idempotency"

    id              = Column(String, primary_key=True, default=_uuid)
    device_id       = Column(String, nullable=False)
    idempotency_key = Column(String, nullable=False)
    response_json   = Column(Text,   nullable=False)   # serialised SyncPushResponse
    created_at      = Column(String, default=_now,     nullable=False)

    __table_args__ = (
        UniqueConstraint("device_id", "idempotency_key", name="uq_sync_idempotency"),
        Index("idx_sync_idem_device_key", "device_id", "idempotency_key"),
    )
