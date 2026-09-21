"""
routers/checklists.py
----------------------
PM checklists between the desktop and the phones.

  PUT  /checklists/templates        — desktop publishes the customer's checklists
  PUT  /checklists/runs             — desktop publishes the planned runs
  GET  /checklists/assigned?project_id=&after=
                                    — phone: what is assigned to its project,
                                      templates and runs in one call (one round
                                      trip is all an engineer on site gets),
                                      paged by uuid until has_more is false
  POST /checklists/runs/{uuid}/results
                                    — phone sends back what was ticked
  GET  /checklists/runs?since=&since_uuid=
                                    — desktop pulls the filled ones back

The desktop SQLite is the source of truth for templates and for which block is
on which campaign; the phone owns only the answers. Both sides settle a run by
updated_at: the later writer wins, which is what the engineer expects when the
same checklist was touched in the office and in the field.

Two timestamps, and they are not the same thing:

  updated_at         when this server row last changed. It only moves forward,
                     so the desktop's pull cursor can walk it.
  client_updated_at  when the writer actually wrote it — the phone's own stamp
                     for a checklist filled at 12:30 and uploaded at 14:00.
                     This is the one the two sides compare, and it is what
                     goes out as `updated_at` on the wire. Stamping at upload
                     time let a phone that synced late beat an office
                     correction made while it was out of signal.
"""

import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user
from models.db_models import (
    User, ChecklistTemplate, ChecklistRun, WorkLogEntry, FieldEvent,
    StockWriteoff,
)
from models.schemas import (
    ChecklistTemplateOut, ChecklistTemplatesSyncRequest,
    ChecklistRunOut, ChecklistRunsSyncRequest, ChecklistAssignedResponse,
    ChecklistResultsIn, ChecklistRunPullResponse,
)

router = APIRouter(prefix="/checklists", tags=["checklists"])

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


def _content_at(row: ChecklistRun) -> str:
    """When this run was last really written. Rows stored before the phone's
    own stamp was kept have only the server one."""
    return (row.client_updated_at or "") or (row.updated_at or "")


def _desktop_only(user: User) -> None:
    """Templates and the plan come from the office desktop, which signs in as
    the admin account. Everyone else fills a checklist in; they do not decide
    which blocks are on the campaign, and — since a run carries deleted_at —
    they cannot wipe a whole campaign either. 'engineer' is what the desktop's
    own user dialog hands out, so a field technician may well hold it: it is
    not proof of being the office."""
    if user.role != "admin":
        raise HTTPException(status_code=403,
                            detail="Only the office can publish checklists")


# ── WHICH PLANTS THIS ACCOUNT MAY SEE ────────────────────────────────────────
# Same reach as routers/sync.py: the admin is the office and sees everything;
# anyone else sees what they are actually involved in. Without this, any
# account could read — and write — every other customer's checklists.

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
        # a checklist they have already filled stays theirs to correct
        db.query(ChecklistRun.project_id).filter(
            ChecklistRun.filled_by == (user.username or "\x00")),
    ):
        ids.update(pid for (pid,) in q.distinct() if pid is not None)
    return ids


def _require_project(db: Session, user: User, project_id: int) -> None:
    """One plant, asked for by name. An account that is already placed at a
    plant stays there — that is what stops a technician of one customer from
    reading and writing the other customer's checklists.

    An account with nothing anywhere yet is a technician the office has just
    created, and there is no other signal to place them by, so the plant they
    ask for is allowed; filling anything in binds them to it from then on.
    The route that hands out *every* project at once (GET /checklists/runs)
    gives such an account nothing at all.
    """
    allowed = _visible_projects(db, user)
    if allowed is None or not allowed or project_id in allowed:
        return
    raise HTTPException(
        status_code=403,
        detail="This account works at another plant. Ask the office to give "
               "you a job here first.")


def _loads(text, default):
    try:
        v = json.loads(text or "")
    except (TypeError, ValueError):
        return default
    return v if isinstance(v, type(default)) else default


def _template_out(t: ChecklistTemplate) -> ChecklistTemplateOut:
    return ChecklistTemplateOut(
        uuid=t.uuid, project_id=t.project_id, name=t.name or "",
        kind=t.kind or "", items=_loads(t.items, []),
        updated_at=t.updated_at or "",
    )


def _run_out(r: ChecklistRun) -> ChecklistRunOut:
    return ChecklistRunOut(
        uuid=r.uuid, project_id=r.project_id, template_uuid=r.template_uuid,
        plant_block=r.plant_block, campaign=r.campaign or "",
        run_date=r.run_date or "", status=r.status or "",
        results=_loads(r.results, {}), ptw_no=r.ptw_no or "",
        serial=r.serial or "", notes=r.notes or "", filled_by=r.filled_by or "",
        # the content stamp, not the row's: this is what both sides compare
        updated_at=_content_at(r), deleted_at=r.deleted_at,
    )


# ── Desktop: publish ─────────────────────────────────────────────────────────

@router.put("/templates", response_model=dict)
def put_templates(
    body: ChecklistTemplatesSyncRequest,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    """Upsert; nothing is deleted. A run may still point at a template the
    desktop has stopped publishing, and a phone holding that run needs its
    text."""
    _desktop_only(user)
    now = _now()
    for t in body.templates:
        row = db.query(ChecklistTemplate).filter(
            ChecklistTemplate.uuid == t.uuid).first()
        if row:
            row.project_id = t.project_id
            row.name       = t.name
            row.kind       = t.kind
            row.items      = json.dumps(t.items)
            row.updated_at = now
        else:
            db.add(ChecklistTemplate(
                uuid=t.uuid, project_id=t.project_id, name=t.name, kind=t.kind,
                items=json.dumps(t.items), updated_at=now,
            ))
    db.commit()
    return {"count": len(body.templates)}


@router.put("/runs", response_model=dict)
def put_runs(
    body: ChecklistRunsSyncRequest,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    """The planned checklists. A run the phone filled in after the desktop's
    copy was written is left alone — the answers are the field's, not the
    office's. The uuids that were left alone come back in `kept_uuids`: the
    desktop must not mark those as published, or its own newer copy would
    never be sent again."""
    _desktop_only(user)
    now = _now()
    applied, kept = 0, 0
    kept_uuids = []
    for r in body.runs:
        theirs = _stamp(r.updated_at) or now
        row = db.query(ChecklistRun).filter(ChecklistRun.uuid == r.uuid).first()
        if row and _content_at(row) > theirs:
            kept += 1
            kept_uuids.append(r.uuid)
            continue
        if not row:
            row = ChecklistRun(uuid=r.uuid, project_id=r.project_id,
                               template_uuid=r.template_uuid)
            db.add(row)
        row.project_id    = r.project_id
        row.template_uuid = r.template_uuid
        row.plant_block   = r.plant_block
        row.campaign      = r.campaign
        row.run_date      = r.run_date
        row.status        = r.status
        row.results       = json.dumps(r.results)
        row.ptw_no        = r.ptw_no
        row.serial        = r.serial
        row.notes         = r.notes
        row.filled_by     = r.filled_by
        row.deleted_at    = r.deleted_at
        row.client_updated_at = theirs
        row.updated_at    = now
        applied += 1
    db.commit()
    return {"applied": applied, "kept": kept, "kept_uuids": kept_uuids}


# ── Phone: what is assigned here ─────────────────────────────────────────────

@router.get("/assigned", response_model=ChecklistAssignedResponse)
def assigned(
    project_id: int           = Query(...),
    after:      Optional[str] = Query(None, description="cursor: last uuid seen"),
    db:         Session       = Depends(get_db),
    user:       User          = Depends(get_current_user),
):
    """Every open checklist of this project, with the templates they need.
    Done ones come too: a mistake is corrected on the phone until the office
    exports the file.

    Paged by uuid, and the phone loops until `has_more` is false. A bare
    `.limit()` silently stopped at 200 rows, and three checklists on each of
    70 blocks is 210 — so whole blocks reached no phone at all and nothing
    said so. uuid is used for the cursor because it is the only column that
    cannot tie; the phone sorts the list by date and block itself.
    """
    _require_project(db, user, project_id)
    q = (db.query(ChecklistRun)
           .filter(ChecklistRun.project_id == project_id,
                   ChecklistRun.deleted_at == None))        # noqa: E711
    if after:
        q = q.filter(ChecklistRun.uuid > after)
    runs = q.order_by(ChecklistRun.uuid.asc()).limit(_PAGE).all()
    wanted = {r.template_uuid for r in runs}
    tpls = db.query(ChecklistTemplate).filter(
        (ChecklistTemplate.project_id == project_id)
        | (ChecklistTemplate.uuid.in_(wanted or [""]))).all()
    return ChecklistAssignedResponse(
        templates=[_template_out(t) for t in tpls],
        runs=[_run_out(r) for r in runs],
        cursor=(runs[-1].uuid if runs else (after or "")),
        has_more=(len(runs) == _PAGE),
    )


@router.post("/runs/{run_uuid}/results", response_model=ChecklistRunOut)
def post_results(
    run_uuid: str,
    body:     ChecklistResultsIn,
    db:       Session = Depends(get_db),
    user:     User    = Depends(get_current_user),
):
    """Merge what the phone ticked into the run. Only the items it sends are
    touched, so two phones on the same block do not blank each other's work."""
    row = db.query(ChecklistRun).filter(ChecklistRun.uuid == run_uuid).first()
    if not row:
        raise HTTPException(status_code=404, detail="Checklist not found")
    _require_project(db, user, row.project_id)
    results = _loads(row.results, {})
    for item_id, val in (body.results or {}).items():
        if not isinstance(val, dict):
            continue
        results[str(item_id)] = {"result": val.get("result", ""),
                                 "comment": val.get("comment", "") or ""}
    row.results = json.dumps(results)
    if body.status is not None:
        row.status = body.status
    if body.ptw_no is not None:
        row.ptw_no = body.ptw_no
    if body.serial is not None:
        row.serial = body.serial
    if body.notes is not None:
        row.notes = body.notes
    row.filled_by = body.filled_by or row.filled_by or user.username
    # When the technician actually ticked it, not when the phone found signal.
    row.client_updated_at = _stamp(body.updated_at) or _now()
    row.updated_at = _now()
    db.commit()
    db.refresh(row)
    return _run_out(row)


# ── Desktop: pull the filled ones back ───────────────────────────────────────

@router.get("/runs", response_model=ChecklistRunPullResponse)
def pull_runs(
    since:      Optional[str] = Query(None),
    since_uuid: Optional[str] = Query(None, description="second half of the cursor"),
    project_id: Optional[int] = Query(None),
    db:         Session       = Depends(get_db),
    user:       User          = Depends(get_current_user),
):
    """The cursor is (updated_at, uuid), not updated_at alone. A publish of
    200 runs stamps them all with the same second, so a strict `>` on the
    timestamp stepped straight over everything else written in that second —
    a phone's results uploaded while the office published a campaign never
    reached the office at all."""
    from services.sync_cursor import settled_before
    cursor = since if since and since != "0" else _EPOCH
    last_uuid = since_uuid or ""
    q = db.query(ChecklistRun).filter(
        ChecklistRun.updated_at <= settled_before(),
        or_(ChecklistRun.updated_at > cursor,
            and_(ChecklistRun.updated_at == cursor,
                 ChecklistRun.uuid > last_uuid)))
    allowed = _visible_projects(db, user)
    if allowed is not None:
        q = q.filter(ChecklistRun.project_id.in_(allowed or [-1]))
    if project_id is not None:
        q = q.filter(ChecklistRun.project_id == project_id)
    q = q.order_by(ChecklistRun.updated_at.asc(), ChecklistRun.uuid.asc())
    total = q.count()
    rows = q.limit(_PAGE).all()
    return ChecklistRunPullResponse(
        runs=[_run_out(r) for r in rows],
        cursor=(rows[-1].updated_at if rows else cursor),
        cursor_uuid=(rows[-1].uuid if rows else last_uuid),
        has_more=(total > _PAGE),
    )
