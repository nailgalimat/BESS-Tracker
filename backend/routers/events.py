"""
routers/events.py
------------------
Field events captured on the phone — preventive maintenance and downtime
(counts-against-availability or excluded). The desktop pulls them and routes
each into the matching report table.

  POST /events            — phone records a PM / downtime / exclusion event
  GET  /events?since=     — desktop pulls events to reconcile into the report
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user
from models.db_models import User, FieldEvent
from models.schemas import FieldEventCreate, FieldEventOut, FieldEventPullResponse

router = APIRouter(prefix="/events", tags=["events"])

_EPOCH = "1970-01-01 00:00:00"
_PAGE = 500
_KINDS = {"pm", "counts", "excluded"}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@router.post("", response_model=FieldEventOut)
def create_event(
    body: FieldEventCreate,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    """Idempotent by client-supplied id."""
    existing = db.query(FieldEvent).filter(FieldEvent.id == body.id).first()
    if existing:
        return existing
    kind = body.kind if body.kind in _KINDS else "pm"
    now = _now()
    ev = FieldEvent(
        id=body.id, user_id=user.id, project_id=body.project_id, kind=kind,
        blocks=body.blocks or "", date_from=body.date_from, date_to=body.date_to,
        hours=float(body.hours or 0), exclusion_type=body.exclusion_type or "",
        description=body.description or "", created_at=body.created_at or now,
        updated_at=now, version=1,
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return ev


@router.get("", response_model=FieldEventPullResponse)
def pull_events(
    since: Optional[str] = Query(None),
    db:    Session       = Depends(get_db),
    user:  User          = Depends(get_current_user),
):
    cursor = since if since and since != "0" else _EPOCH
    q = db.query(FieldEvent).filter(FieldEvent.updated_at > cursor)
    if user.role != "admin":
        q = q.filter(FieldEvent.user_id == user.id)
    q = q.order_by(FieldEvent.updated_at.asc())
    total = q.count()
    rows = q.limit(_PAGE).all()
    new_cursor = rows[-1].updated_at if rows else cursor
    return FieldEventPullResponse(
        events=[FieldEventOut.model_validate(r) for r in rows],
        cursor=new_cursor, has_more=(total > _PAGE),
    )
