"""
routers/action_items.py
------------------------
The office's action list, between the desktop and the phones.

  PUT  /action-items                — desktop publishes the list
  GET  /action-items/assigned?project_id=&after=
                                    — phone: the items given to THIS account,
                                      paged by uuid until has_more is false
  POST /action-items/{uuid}/done    — phone sends back the completion
  GET  /action-items?since=&since_uuid=
                                    — desktop pulls the finished ones back

These are organisational items — "confirm the EPC purchased the spare parts",
"get the HVAC BOM" — with no plant block and no hours. They live in their own
table for a reason: the work journal is what the customer's monthly report is
built from, and nothing here belongs in it.

The desktop is the source of truth for the list itself; a phone owns only the
completion of an item that was given to it. Both sides settle by updated_at:
the later writer wins.

Two timestamps, and they are not the same thing (the same rule as
routers/checklists.py, which this follows):

  updated_at         when this server row last changed. It only moves forward,
                     so the desktop's pull cursor can walk it.
  client_updated_at  when the writer actually wrote it — the technician's own
                     stamp for an item finished at 12:30 and uploaded at 14:00.
                     This is the one the two sides compare, and it is what goes
                     out as `updated_at` on the wire.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user
from models.db_models import (
    User, ActionItem, WorkLogEntry, FieldEvent, StockWriteoff, ChecklistRun,
)
from models.schemas import (
    ActionItemOut, ActionItemsSyncRequest, ActionItemsAssignedResponse,
    ActionItemDoneIn, ActionItemPullResponse,
)

router = APIRouter(prefix="/action-items", tags=["action-items"])

_EPOCH = "1970-01-01 00:00:00"
_PAGE = 200


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _stamp(value) -> str:
    """A client's own timestamp in the server's format, or ''.

    The phone sends ISO ('2026-09-21T09:30:00.000Z'), the desktop already
    sends '2026-09-21 09:30:00', and every comparison here is plain text — so
    they have to look the same. A stamp from a phone whose clock runs fast is
    capped at now: otherwise that phone would win every future argument with
    the office for as long as the clock stayed wrong.
    """
    s = str(value or "").strip().replace("T", " ").replace("Z", " ")
    s = s.split(".")[0].strip()[:19]
    if len(s) != 19:
        return ""
    return min(s, _now())


def _content_at(row: ActionItem) -> str:
    """When this item was last really written. Rows stored before the phone's
    own stamp was kept have only the server one."""
    return (row.client_updated_at or "") or (row.updated_at or "")


def _office_only(user: User) -> None:
    """The list comes from the office desktop, which signs in as the admin
    account. Everyone else finishes the item they were given; they do not
    decide what is on the list, and — since an item carries deleted_at — they
    cannot wipe the list either. 'engineer' is what the desktop's own user
    dialog hands out, so a field technician may well hold it: it is not proof
    of being the office."""
    if user.role != "admin":
        raise HTTPException(status_code=403,
                            detail="Only the office can publish the action list")


# ── WHICH PLANTS THIS ACCOUNT MAY SEE ────────────────────────────────────────
# Same reach as routers/sync.py and routers/checklists.py: the admin is the
# office and sees everything; anyone else sees what they are actually involved
# in. Without this, any account could read every other customer's action list.

def _visible_projects(db: Session, user: User):
    """The project ids this user has anything to do with, or None for 'all'."""
    if user.role == "admin":
        return None
    ids = set()
    for q in (
        db.query(WorkLogEntry.project_id).filter(
            (WorkLogEntry.user_id == user.id) |
            (WorkLogEntry.assigned_to == user.id)),
        db.query(FieldEvent.project_id).filter(FieldEvent.user_id == user.id),
        db.query(StockWriteoff.project_id).filter(
            StockWriteoff.user_id == user.id),
        db.query(ChecklistRun.project_id).filter(
            ChecklistRun.filled_by == (user.username or "\x00")),
        # an item given to them binds them to that plant, the same way a
        # checklist they filled does
        db.query(ActionItem.project_id).filter(
            ActionItem.assigned_to == user.id),
    ):
        ids.update(pid for (pid,) in q.distinct() if pid is not None)
    return ids


def _require_project(db: Session, user: User, project_id: int) -> None:
    """One plant, asked for by name. An account already placed at a plant stays
    there — that is what stops a technician of one customer from reading the
    other customer's action list.

    An account with nothing anywhere yet is a technician the office has just
    created, and there is no other signal to place them by, so the plant they
    ask for is allowed; they still only ever see the items assigned to them.
    """
    allowed = _visible_projects(db, user)
    if allowed is None or not allowed or project_id in allowed:
        return
    raise HTTPException(
        status_code=403,
        detail="This account works at another plant. Ask the office to give "
               "you a job here first.")


def _out(r: ActionItem) -> ActionItemOut:
    return ActionItemOut(
        uuid=r.uuid, project_id=r.project_id, seq=r.seq, topic=r.topic or "",
        description=r.description or "", todo=r.todo or "",
        due_date=r.due_date or "", assigned_to=r.assigned_to or "",
        assigned_name=r.assigned_name or "", status=r.status or "open",
        done_at=r.done_at or "", done_note=r.done_note or "",
        done_by=r.done_by or "",
        # the content stamp, not the row's: this is what both sides compare
        updated_at=_content_at(r), deleted_at=r.deleted_at,
    )


# ── Desktop: publish ─────────────────────────────────────────────────────────

@router.put("", response_model=dict)
def put_items(
    body: ActionItemsSyncRequest,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    """Upsert the list. An item the phone finished after the desktop's copy
    was written is left alone — the completion is the field's, not the
    office's. The uuids that were left alone come back in `kept_uuids`: the
    desktop must not mark those as published, or its own newer copy would
    never be offered again."""
    _office_only(user)
    now = _now()
    applied, kept = 0, 0
    kept_uuids = []
    for it in body.items:
        theirs = _stamp(it.updated_at) or now
        row = db.query(ActionItem).filter(ActionItem.uuid == it.uuid).first()
        if row and _content_at(row) > theirs:
            kept += 1
            kept_uuids.append(it.uuid)
            continue
        if not row:
            row = ActionItem(uuid=it.uuid, project_id=it.project_id)
            db.add(row)
        row.project_id    = it.project_id
        row.seq           = it.seq
        row.topic         = it.topic
        row.description   = it.description
        row.todo          = it.todo
        row.due_date      = it.due_date
        row.assigned_to   = it.assigned_to
        row.assigned_name = it.assigned_name
        row.status        = it.status
        row.done_at       = it.done_at
        row.done_note     = it.done_note
        row.done_by       = it.done_by
        row.deleted_at    = it.deleted_at
        row.client_updated_at = theirs
        row.updated_at    = now
        applied += 1
    db.commit()
    return {"applied": applied, "kept": kept, "kept_uuids": kept_uuids}


# ── Phone: what is assigned to me ────────────────────────────────────────────

@router.get("/assigned", response_model=ActionItemsAssignedResponse)
def assigned(
    project_id: int           = Query(...),
    after:      Optional[str] = Query(None, description="cursor: last uuid seen"),
    db:         Session       = Depends(get_db),
    user:       User          = Depends(get_current_user),
):
    """The items of this project given to THIS account, and nobody else's.

    Done ones come too: a note is corrected on the phone until the office
    exports the sheet. Paged by uuid, and the phone loops until `has_more` is
    false — uuid is the cursor because it is the only column that cannot tie.
    """
    _require_project(db, user, project_id)
    q = (db.query(ActionItem)
           .filter(ActionItem.project_id == project_id,
                   ActionItem.assigned_to == user.id,
                   ActionItem.deleted_at == None))      # noqa: E711
    if after:
        q = q.filter(ActionItem.uuid > after)
    rows = q.order_by(ActionItem.uuid.asc()).limit(_PAGE).all()
    return ActionItemsAssignedResponse(
        items=[_out(r) for r in rows],
        cursor=(rows[-1].uuid if rows else (after or "")),
        has_more=(len(rows) == _PAGE),
    )


@router.post("/{item_uuid}/done", response_model=ActionItemOut)
def post_done(
    item_uuid: str,
    body:      ActionItemDoneIn,
    db:        Session = Depends(get_db),
    user:      User    = Depends(get_current_user),
):
    """Mark an item finished from the phone. Only the person it was given to
    may do it — otherwise one technician could close another's item, and the
    office would never know who really did the work."""
    row = db.query(ActionItem).filter(ActionItem.uuid == item_uuid).first()
    if not row:
        raise HTTPException(status_code=404, detail="Action item not found")
    _require_project(db, user, row.project_id)
    if user.role != "admin" and (row.assigned_to or "") != user.id:
        raise HTTPException(
            status_code=403,
            detail="This item was given to someone else.")
    row.status    = body.status or "done"
    row.done_note = body.done_note if body.done_note is not None else (row.done_note or "")
    row.done_by   = body.done_by or row.done_by or user.username
    # When it was really finished, not when the phone found signal.
    row.done_at   = _stamp(body.done_at) or _now()
    row.client_updated_at = _stamp(body.updated_at) or _now()
    row.updated_at = _now()
    db.commit()
    db.refresh(row)
    return _out(row)


# ── Desktop: pull the finished ones back ─────────────────────────────────────

@router.get("", response_model=ActionItemPullResponse)
def pull_items(
    since:      Optional[str] = Query(None),
    since_uuid: Optional[str] = Query(None, description="second half of the cursor"),
    project_id: Optional[int] = Query(None),
    db:         Session       = Depends(get_db),
    user:       User          = Depends(get_current_user),
):
    """The cursor is (updated_at, uuid), not updated_at alone. A publish of a
    whole list stamps every row with the same second, so a strict `>` on the
    timestamp stepped straight over everything else written in that second."""
    from services.sync_cursor import settled_before
    cursor = since if since and since != "0" else _EPOCH
    last_uuid = since_uuid or ""
    q = db.query(ActionItem).filter(
        ActionItem.updated_at <= settled_before(),
        or_(ActionItem.updated_at > cursor,
            and_(ActionItem.updated_at == cursor,
                 ActionItem.uuid > last_uuid)))
    allowed = _visible_projects(db, user)
    if allowed is not None:
        q = q.filter(ActionItem.project_id.in_(allowed or [-1]))
    if project_id is not None:
        q = q.filter(ActionItem.project_id == project_id)
    q = q.order_by(ActionItem.updated_at.asc(), ActionItem.uuid.asc())
    total = q.count()
    rows = q.limit(_PAGE).all()
    return ActionItemPullResponse(
        items=[_out(r) for r in rows],
        cursor=(rows[-1].updated_at if rows else cursor),
        cursor_uuid=(rows[-1].uuid if rows else last_uuid),
        has_more=(total > _PAGE),
    )
