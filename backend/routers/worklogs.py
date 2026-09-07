"""
routers/worklogs.py
--------------------
GET    /worklogs
GET    /worklogs/{id}
POST   /worklogs
PATCH  /worklogs/{id}
DELETE /worklogs/{id}   (soft delete)
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user
from models.db_models import User, WorkLogEntry, WorkLogTag, WorkLogImage
from models.schemas import (
    WorkLogEntryCreate, WorkLogEntryUpdate, WorkLogEntryOut, WorkLogImageOut,
)

router = APIRouter(prefix="/worklogs", tags=["worklogs"])

REQUEST_BASE_URL = ""   # set at startup from request.base_url in main.py


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _image_out(img: WorkLogImage, base_url: str) -> WorkLogImageOut:
    return WorkLogImageOut(
        id           = img.id,
        work_log_id  = img.work_log_id,
        filename     = img.filename,
        size_bytes   = img.size_bytes,
        width        = img.width,
        height       = img.height,
        taken_at     = img.taken_at,
        uploaded_at  = img.uploaded_at,
        updated_at   = img.updated_at,
        download_url = f"{base_url}images/{img.id}/download",
    )


def _entry_out(entry: WorkLogEntry, base_url: str = "") -> WorkLogEntryOut:
    return WorkLogEntryOut(
        id               = entry.id,
        user_id          = entry.user_id,
        project_id       = entry.project_id,
        container_id     = entry.container_id,
        equipment_serial = entry.equipment_serial or "",
        site_location    = entry.site_location or "",
        category         = entry.category,
        description      = entry.description or "",
        log_date         = entry.log_date,
        created_at       = entry.created_at,
        updated_at       = entry.updated_at,
        deleted_at       = entry.deleted_at,
        version          = entry.version,
        origin_device    = entry.origin_device,
        tags             = [t.tag for t in entry.tags],
        images           = [_image_out(img, base_url) for img in entry.images],
    )


# ── GET /worklogs ─────────────────────────────────────────────────────────────

@router.get("", response_model=list[WorkLogEntryOut])
def list_worklogs(
    project_id: Optional[int] = Query(None),
    date_from:  Optional[str] = Query(None),
    date_to:    Optional[str] = Query(None),
    category:   Optional[str] = Query(None),
    q:          Optional[str] = Query(None, description="Text search in description/location"),
    page:       int           = Query(1, ge=1),
    limit:      int           = Query(50, ge=1, le=200),
    db:         Session       = Depends(get_db),
    user:       User          = Depends(get_current_user),
):
    query = (
        db.query(WorkLogEntry)
        .filter(
            WorkLogEntry.deleted_at == None,
            WorkLogEntry.user_id == user.id   # users see only their own
            if user.role != "admin"
            else True,
        )
    )
    if project_id is not None:
        query = query.filter(WorkLogEntry.project_id == project_id)
    if date_from:
        query = query.filter(WorkLogEntry.log_date >= date_from)
    if date_to:
        query = query.filter(WorkLogEntry.log_date <= date_to)
    if category:
        query = query.filter(WorkLogEntry.category == category)
    if q:
        like = f"%{q}%"
        query = query.filter(
            WorkLogEntry.description.ilike(like) |
            WorkLogEntry.site_location.ilike(like) |
            WorkLogEntry.equipment_serial.ilike(like)
        )

    total  = query.count()
    offset = (page - 1) * limit
    entries = query.order_by(WorkLogEntry.log_date.desc(), WorkLogEntry.created_at.desc()) \
                   .offset(offset).limit(limit).all()

    return [_entry_out(e) for e in entries]


# ── GET /worklogs/{id} ────────────────────────────────────────────────────────

@router.get("/{entry_id}", response_model=WorkLogEntryOut)
def get_worklog(
    entry_id: str,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    entry = db.query(WorkLogEntry).filter(
        WorkLogEntry.id         == entry_id,
        WorkLogEntry.deleted_at == None,
    ).first()

    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    if user.role != "admin" and entry.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your entry")

    return _entry_out(entry)


# ── POST /worklogs ────────────────────────────────────────────────────────────

@router.post("", response_model=WorkLogEntryOut, status_code=201)
def create_worklog(
    body: WorkLogEntryCreate,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    # Idempotent: if UUID already exists and belongs to this user, return it
    existing = db.query(WorkLogEntry).filter(WorkLogEntry.id == body.id).first()
    if existing:
        if existing.user_id != user.id:
            raise HTTPException(status_code=409, detail="ID belongs to another user")
        return _entry_out(existing)

    entry = WorkLogEntry(
        id               = body.id,
        user_id          = user.id,
        project_id       = body.project_id,
        container_id     = body.container_id,
        equipment_serial = body.equipment_serial,
        site_location    = body.site_location,
        category         = body.category,
        description      = body.description,
        log_date         = body.log_date,
        created_at       = body.created_at,
        updated_at       = body.updated_at,
        version          = body.version,
        origin_device    = body.origin_device,
        sync_status      = "synced",
    )
    db.add(entry)

    for tag_str in set(body.tags):
        tag_str = tag_str.strip().lower()
        if tag_str:
            db.add(WorkLogTag(work_log_id=body.id, tag=tag_str))

    db.commit()
    db.refresh(entry)
    return _entry_out(entry)


# ── PATCH /worklogs/{id} ──────────────────────────────────────────────────────

@router.patch("/{entry_id}", response_model=WorkLogEntryOut)
def update_worklog(
    entry_id: str,
    body: WorkLogEntryUpdate,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    entry = db.query(WorkLogEntry).filter(
        WorkLogEntry.id         == entry_id,
        WorkLogEntry.deleted_at == None,
    ).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    if user.role != "admin" and entry.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your entry")

    if body.category         is not None: entry.category         = body.category
    if body.description      is not None: entry.description      = body.description
    if body.equipment_serial is not None: entry.equipment_serial = body.equipment_serial
    if body.site_location    is not None: entry.site_location    = body.site_location
    if body.log_date         is not None: entry.log_date         = body.log_date

    entry.updated_at = _now()
    entry.version   += 1

    if body.tags is not None:
        db.query(WorkLogTag).filter(WorkLogTag.work_log_id == entry_id).delete()
        for tag_str in set(body.tags):
            tag_str = tag_str.strip().lower()
            if tag_str:
                db.add(WorkLogTag(work_log_id=entry_id, tag=tag_str))

    db.commit()
    db.refresh(entry)
    return _entry_out(entry)


# ── DELETE /worklogs/{id} ─────────────────────────────────────────────────────

@router.delete("/{entry_id}", status_code=204)
def delete_worklog(
    entry_id: str,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    entry = db.query(WorkLogEntry).filter(
        WorkLogEntry.id         == entry_id,
        WorkLogEntry.deleted_at == None,
    ).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    if user.role != "admin" and entry.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your entry")

    entry.deleted_at = _now()
    entry.updated_at = _now()
    entry.version   += 1
    db.commit()
