"""
routers/checklists.py
----------------------
PM checklists between the desktop and the phones.

  PUT  /checklists/templates        — desktop publishes the customer's checklists
  PUT  /checklists/runs             — desktop publishes the planned runs
  GET  /checklists/assigned?project_id=
                                    — phone: what is assigned to its project,
                                      templates and runs in one call (one round
                                      trip is all an engineer on site gets)
  POST /checklists/runs/{uuid}/results
                                    — phone sends back what was ticked
  GET  /checklists/runs?since=      — desktop pulls the filled ones back

The desktop SQLite is the source of truth for templates and for which block is
on which campaign; the phone owns only the answers. Both sides settle a run by
updated_at: the later writer wins, which is what the engineer expects when the
same checklist was touched in the office and in the field.
"""

import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user
from models.db_models import User, ChecklistTemplate, ChecklistRun
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


def _desktop_only(user: User) -> None:
    """Templates and the plan come from the desktop. A technician fills a
    checklist in; they do not decide which blocks are on the campaign."""
    if user.role not in ("admin", "engineer"):
        raise HTTPException(status_code=403,
                            detail="Only the office can publish checklists")


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
        updated_at=r.updated_at or "", deleted_at=r.deleted_at,
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
    office's."""
    _desktop_only(user)
    now = _now()
    applied, kept = 0, 0
    for r in body.runs:
        row = db.query(ChecklistRun).filter(ChecklistRun.uuid == r.uuid).first()
        if row and (row.updated_at or "") > (r.updated_at or ""):
            kept += 1
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
        row.updated_at    = now
        applied += 1
    db.commit()
    return {"applied": applied, "kept": kept}


# ── Phone: what is assigned here ─────────────────────────────────────────────

@router.get("/assigned", response_model=ChecklistAssignedResponse)
def assigned(
    project_id: int          = Query(...),
    db:         Session      = Depends(get_db),
    user:       User         = Depends(get_current_user),
):
    """Every open checklist of this project, with the templates they need.
    Done ones come too: a mistake is corrected on the phone until the office
    exports the file."""
    runs = (db.query(ChecklistRun)
              .filter(ChecklistRun.project_id == project_id,
                      ChecklistRun.deleted_at == None)      # noqa: E711
              .order_by(ChecklistRun.run_date.desc(), ChecklistRun.plant_block)
              .limit(_PAGE).all())
    wanted = {r.template_uuid for r in runs}
    tpls = db.query(ChecklistTemplate).filter(
        (ChecklistTemplate.project_id == project_id)
        | (ChecklistTemplate.uuid.in_(wanted or [""]))).all()
    return ChecklistAssignedResponse(
        templates=[_template_out(t) for t in tpls],
        runs=[_run_out(r) for r in runs],
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
    row.updated_at = _now()
    db.commit()
    db.refresh(row)
    return _run_out(row)


# ── Desktop: pull the filled ones back ───────────────────────────────────────

@router.get("/runs", response_model=ChecklistRunPullResponse)
def pull_runs(
    since:      Optional[str] = Query(None),
    project_id: Optional[int] = Query(None),
    db:         Session       = Depends(get_db),
    user:       User          = Depends(get_current_user),
):
    from services.sync_cursor import settled_before
    cursor = since if since and since != "0" else _EPOCH
    q = db.query(ChecklistRun).filter(ChecklistRun.updated_at > cursor,
                                      ChecklistRun.updated_at <= settled_before())
    if project_id is not None:
        q = q.filter(ChecklistRun.project_id == project_id)
    q = q.order_by(ChecklistRun.updated_at.asc())
    total = q.count()
    rows = q.limit(_PAGE).all()
    new_cursor = rows[-1].updated_at if rows else cursor
    return ChecklistRunPullResponse(
        runs=[_run_out(r) for r in rows],
        cursor=new_cursor, has_more=(total > _PAGE),
    )
