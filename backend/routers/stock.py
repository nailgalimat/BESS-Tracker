"""
routers/stock.py
-----------------
Spare-parts stock for the mobile app.

  GET  /stock?project_id=       — list a project's stock (phone shows the shelf)
  PUT  /stock                   — desktop replaces the stock mirror (source of truth)
  POST /stock/writeoff          — phone records a part used (decrements the mirror)
  GET  /stock/writeoffs?since=  — desktop pulls write-offs to apply as OUT txns

The desktop SQLite is the source of truth for stock levels; the server keeps a
mirror so the field engineer can see and consume parts without the computer.
Write-offs flow the other way and are reconciled into the desktop on next sync.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user
from models.db_models import User, StockItem, StockWriteoff
from models.schemas import (
    StockItemOut, StockSyncRequest, WriteoffCreate, WriteoffOut,
    WriteoffPullResponse,
)

router = APIRouter(prefix="/stock", tags=["stock"])

_EPOCH = "1970-01-01 00:00:00"
_PAGE = 500


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ── Phone: list a project's stock ─────────────────────────────────────────────
@router.get("", response_model=list[StockItemOut])
def list_stock(
    project_id: int          = Query(...),
    db:         Session      = Depends(get_db),
    user:       User         = Depends(get_current_user),
):
    rows = (db.query(StockItem)
              .filter(StockItem.project_id == project_id)
              .order_by(StockItem.material_number)
              .all())
    return [
        StockItemOut(
            project_id=r.project_id, material_number=r.material_number,
            description=r.description or "", quantity=r.quantity or 0,
            unit=r.unit or "", min_quantity=r.min_quantity or 0,
        ) for r in rows
    ]


# ── Desktop: replace the stock mirror ─────────────────────────────────────────
@router.put("", response_model=dict)
def replace_stock(
    body: StockSyncRequest,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    """Upsert the pushed stock rows and drop any (project, material) no longer
    present. Quantities from the desktop win — it is the source of truth — but
    write-offs since the last push have already been pulled by the desktop, so
    the pushed quantity already reflects them."""
    now = _now()
    incoming = {(it.project_id, it.material_number) for it in body.items}

    for it in body.items:
        row = (db.query(StockItem)
                 .filter(StockItem.project_id == it.project_id,
                         StockItem.material_number == it.material_number)
                 .first())
        if row:
            row.description  = it.description
            row.quantity     = it.quantity
            row.unit         = it.unit
            row.min_quantity = it.min_quantity
            row.updated_at   = now
        else:
            db.add(StockItem(
                project_id=it.project_id, material_number=it.material_number,
                description=it.description, quantity=it.quantity,
                unit=it.unit, min_quantity=it.min_quantity, updated_at=now,
            ))

    # Remove rows the desktop no longer has (per project present in the push)
    pushed_projects = {it.project_id for it in body.items}
    for row in db.query(StockItem).filter(
            StockItem.project_id.in_(pushed_projects or [-1])).all():
        if (row.project_id, row.material_number) not in incoming:
            db.delete(row)

    db.commit()
    return {"count": len(body.items)}


# ── Phone: record a write-off ─────────────────────────────────────────────────
@router.post("/writeoff", response_model=WriteoffOut)
def create_writeoff(
    body: WriteoffCreate,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    """Idempotent by client-supplied id. Decrements the stock mirror so the
    phone immediately sees the reduced quantity."""
    now = _now()
    existing = db.query(StockWriteoff).filter(StockWriteoff.id == body.id).first()
    if existing:
        return existing   # safe retry — already recorded

    wo = StockWriteoff(
        id=body.id, user_id=user.id, project_id=body.project_id,
        material_number=body.material_number, description=body.description,
        quantity=float(body.quantity or 0), block=body.block, note=body.note,
        log_date=body.log_date, created_at=body.created_at or now,
        updated_at=now, version=1,
    )
    db.add(wo)

    # Decrement the mirror (clamp at 0); create the row if the phone knew of a
    # material the mirror somehow lacks.
    item = (db.query(StockItem)
              .filter(StockItem.project_id == body.project_id,
                      StockItem.material_number == body.material_number)
              .first())
    if item:
        item.quantity = max(0.0, (item.quantity or 0) - float(body.quantity or 0))
        item.updated_at = now

    db.commit()
    db.refresh(wo)
    return wo


# ── Desktop: pull write-offs to reconcile into the desktop ────────────────────
@router.get("/writeoffs", response_model=WriteoffPullResponse)
def pull_writeoffs(
    since:     Optional[str] = Query(None),
    db:        Session       = Depends(get_db),
    user:      User          = Depends(get_current_user),
):
    from services.sync_cursor import settled_before
    cursor = since if since and since != "0" else _EPOCH
    q = db.query(StockWriteoff).filter(StockWriteoff.updated_at > cursor,
                                       StockWriteoff.updated_at <= settled_before())
    if user.role != "admin":
        q = q.filter(StockWriteoff.user_id == user.id)
    q = q.order_by(StockWriteoff.updated_at.asc())
    total = q.count()
    rows = q.limit(_PAGE).all()
    has_more = total > _PAGE
    new_cursor = rows[-1].updated_at if rows else cursor
    return WriteoffPullResponse(
        writeoffs=[WriteoffOut.model_validate(r) for r in rows],
        cursor=new_cursor, has_more=has_more,
    )
