"""
routers/sync.py
----------------
GET  /sync/pull?since=<ISO>&device_id=<uuid>   — pull delta from server
POST /sync/push                                — push batch of local changes
GET  /sync/state                               — server's view of this device

Protocol:
  - `since`  = ISO datetime string ("2026-01-01 00:00:00")
               pass "0" or omit for full snapshot
  - `cursor` = max(updated_at) of returned rows; use as `since` on next call
  - Push outcome per row:
      "applied"  — inserted or updated successfully
      "conflict" — server version newer; client must resolve manually
      "skipped"  — nothing changed (versions equal, same content)
      "error"    — unexpected failure (message field has details)
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user
from models.db_models import User, WorkLogEntry, WorkLogTag, WorkLogImage, SyncIdempotencyRecord
from models.schemas import (
    SyncPushRequest, SyncPushResponse, SyncChangeResult,
    SyncPullResponse, SyncPullChange,
)

log = logging.getLogger("bess.sync")

MAX_PUSH_BATCH = 500   # hard cap per push request

router = APIRouter(prefix="/sync", tags=["sync"])

_EPOCH = "1970-01-01 00:00:00"
_PAGE  = 200   # max rows per pull response


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _entry_to_dict(entry: WorkLogEntry) -> dict:
    return {
        "id":               entry.id,
        "user_id":          entry.user_id,
        "project_id":       entry.project_id,
        "container_id":     entry.container_id,
        "equipment_serial": entry.equipment_serial or "",
        "site_location":    entry.site_location or "",
        "category":         entry.category,
        "description":      entry.description or "",
        "fault_name":       entry.fault_name or "",
        "status":           entry.status or "",
        "sap_ticket":       entry.sap_ticket or "",
        "spare_parts":      entry.spare_parts or "",
        "log_date":         entry.log_date,
        "created_at":       entry.created_at,
        "updated_at":       entry.updated_at,
        "deleted_at":       entry.deleted_at,
        "version":          entry.version,
        "origin_device":    entry.origin_device,
        "tags":             [t.tag for t in entry.tags],
    }


def _image_to_dict(img: WorkLogImage) -> dict:
    return {
        "id":             img.id,
        "work_log_id":    img.work_log_id,
        "filename":       img.filename,
        "size_bytes":     img.size_bytes,
        "sha256":         img.sha256,
        "width":          img.width,
        "height":         img.height,
        "taken_at":       img.taken_at,
        "uploaded_at":    img.uploaded_at,
        "updated_at":     img.updated_at,
        "upload_status":  img.upload_status,
    }


# ── ONE ENTRY ─────────────────────────────────────────────────────────────────

@router.get("/entry/{entry_id}")
def get_entry(
    entry_id: str,
    db:       Session = Depends(get_db),
    user:     User    = Depends(get_current_user),
):
    """The server's current copy of one entry, in the same shape the pull
    sends — what a device needs to settle a sync conflict. (GET /worklogs/{id}
    has a narrower schema: no fault/status/SAP/spare-parts fields.)"""
    entry = db.query(WorkLogEntry).filter(WorkLogEntry.id == entry_id).first()
    if not entry or (user.role != "admin" and entry.user_id != user.id):
        raise HTTPException(status_code=404, detail="Entry not found")
    return _entry_to_dict(entry)


# ── PULL ──────────────────────────────────────────────────────────────────────

@router.get("/pull", response_model=SyncPullResponse)
def pull(
    since:     Optional[str] = Query(None, description="ISO datetime cursor; omit for full sync"),
    device_id: Optional[str] = Query(None),
    db:        Session       = Depends(get_db),
    user:      User          = Depends(get_current_user),
):
    from services.sync_cursor import settled_before
    cursor = since if since and since != "0" else _EPOCH
    settled = settled_before()      # see services/sync_cursor.py

    # Shared visibility filter for entries and images (images join their parent
    # entry). Non-admins see only their own entries; a device never gets its
    # own changes echoed back.
    def _visible(q):
        if user.role != "admin":
            q = q.filter(WorkLogEntry.user_id == user.id)
        if device_id:
            q = q.filter(
                (WorkLogEntry.origin_device == None) |
                (WorkLogEntry.origin_device != device_id)
            )
        return q

    # ── Entries updated after cursor ─────────────────────────────────────────
    entry_q = _visible(
        db.query(WorkLogEntry).filter(WorkLogEntry.updated_at > cursor,
                                      WorkLogEntry.updated_at <= settled)
    ).order_by(WorkLogEntry.updated_at.asc())
    total_entries = entry_q.count()
    entries       = entry_q.limit(_PAGE).all()

    # ── Images updated after cursor, keyed on the IMAGE's OWN timestamp ───────
    # The old code only sent images whose parent entry was in this delta, so a
    # photo uploaded after its entry had already synced was orphaned forever.
    # Selecting by the image's own updated_at fixes that.
    img_q = _visible(
        db.query(WorkLogImage)
          .join(WorkLogEntry, WorkLogImage.work_log_id == WorkLogEntry.id)
          .filter(WorkLogImage.updated_at > cursor,
                  WorkLogImage.updated_at <= settled)
    ).order_by(WorkLogImage.updated_at.asc())
    total_images = img_q.count()
    images       = img_q.limit(_PAGE).all()

    # Build change list
    changes: list[SyncPullChange] = []
    for entry in entries:
        action = "delete" if entry.deleted_at else "upsert"
        changes.append(SyncPullChange(
            entity     = "work_log",
            action     = action,
            id         = entry.id,
            data       = _entry_to_dict(entry),
            updated_at = entry.updated_at,
        ))
    for img in images:
        changes.append(SyncPullChange(
            entity     = "work_log_image",
            action     = "upsert",
            id         = img.id,
            data       = _image_to_dict(img),
            updated_at = img.updated_at,
        ))

    # ── Advance the cursor safely ────────────────────────────────────────────
    # If either stream was truncated to _PAGE, only advance to the frontier of
    # the truncated stream(s) so nothing in between is skipped; otherwise
    # advance past everything sent. Any item re-sent next page is idempotent.
    entries_more = total_entries > _PAGE
    images_more  = total_images  > _PAGE
    caps = []
    if entries_more and entries:
        caps.append(entries[-1].updated_at)
    if images_more and images:
        caps.append(images[-1].updated_at)
    if caps:
        new_cursor = min(caps)
    else:
        sent = [e.updated_at for e in entries] + [i.updated_at for i in images]
        new_cursor = max(sent) if sent else cursor

    return SyncPullResponse(
        changes  = changes,
        cursor   = new_cursor,
        has_more = entries_more or images_more,
    )


# ── PUSH ──────────────────────────────────────────────────────────────────────

@router.post("/push", response_model=SyncPushResponse)
def push(
    body: SyncPushRequest,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    t_start = datetime.now(timezone.utc)

    # ── Hard batch size cap ───────────────────────────────────────────────────
    if len(body.changes) > MAX_PUSH_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"Batch too large: {len(body.changes)} changes (max {MAX_PUSH_BATCH})."
        )

    # ── Idempotency check ─────────────────────────────────────────────────────
    if body.idempotency_key:
        # Clean up expired records (>24 h) opportunistically
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        db.query(SyncIdempotencyRecord).filter(
            SyncIdempotencyRecord.created_at < cutoff
        ).delete(synchronize_session=False)

        cached = db.query(SyncIdempotencyRecord).filter_by(
            device_id       = body.device_id,
            idempotency_key = body.idempotency_key,
        ).first()
        if cached:
            log.info("sync.push.idempotent_replay",
                     extra={"device_id": body.device_id,
                            "idempotency_key": body.idempotency_key,
                            "user_id": user.id})
            return SyncPushResponse(**json.loads(cached.response_json))

    # ── Process changes ───────────────────────────────────────────────────────
    def _apply(change):
        if change.entity == "work_log":
            return _apply_entry_change(change, body.device_id, user, db)
        if change.entity == "work_log_image":
            return _apply_image_change(change, user, db)
        return SyncChangeResult(id=change.id, outcome="error",
                                message=f"Unknown entity: {change.entity}")

    # The whole batch in one transaction — a retry with the same idempotency
    # key then either finds everything or nothing. But one change that fails
    # at commit used to take the batch down with a 500, and since the device
    # resends the same batch every sync, it never got through again — nor did
    # anything queued behind it. If the batch fails, redo it change by change
    # so only the bad one comes back as an error.
    try:
        results = [_apply(c) for c in body.changes]
        db.commit()
    except Exception as exc:
        db.rollback()
        log.warning("sync.push.batch_failed_retrying_singly",
                    extra={"device_id": body.device_id, "error": str(exc)[:200]})
        results = []
        for c in body.changes:
            try:
                r = _apply(c)
                db.commit()
            except Exception as one_exc:
                db.rollback()
                r = SyncChangeResult(id=c.id, outcome="error",
                                     message=str(one_exc)[:200])
            results.append(r)
    response = SyncPushResponse(results=results)

    # ── Store idempotency record ───────────────────────────────────────────────
    if body.idempotency_key:
        try:
            rec = SyncIdempotencyRecord(
                device_id       = body.device_id,
                idempotency_key = body.idempotency_key,
                response_json   = response.model_dump_json(),
            )
            db.add(rec)
            db.commit()
        except Exception:
            db.rollback()   # uniqueness violation on race — safe to ignore

    # ── Structured log ────────────────────────────────────────────────────────
    elapsed_ms = int((datetime.now(timezone.utc) - t_start).total_seconds() * 1000)
    outcomes   = {}
    for r in results:
        outcomes[r.outcome] = outcomes.get(r.outcome, 0) + 1
    log.info("sync.push.complete", extra={
        "user_id":    user.id,
        "device_id":  body.device_id,
        "changes":    len(body.changes),
        "outcomes":   outcomes,
        "elapsed_ms": elapsed_ms,
    })

    return response


def _clean_tags(tags) -> list:
    """Tags as stored: trimmed, lower-case, each once. 'BMS, bms' used to put
    two identical rows into a composite primary key and fail the batch."""
    return sorted({str(t).strip().lower() for t in (tags or []) if str(t).strip()})


def _apply_entry_change(change, device_id: str, user: User, db: Session) -> SyncChangeResult:
    payload    = change.payload
    entry      = db.query(WorkLogEntry).filter(WorkLogEntry.id == change.id).first()
    now        = _now()

    try:
        if change.action == "delete":
            if not entry:
                return SyncChangeResult(id=change.id, outcome="skipped", message="Not found")
            if user.role != "admin" and entry.user_id != user.id:
                return SyncChangeResult(id=change.id, outcome="error", message="Not your entry")
            if entry.version > change.version:
                return SyncChangeResult(
                    id=change.id, outcome="conflict", server_row=_entry_to_dict(entry)
                )
            entry.deleted_at = now
            entry.updated_at = now
            entry.version   += 1
            return SyncChangeResult(id=change.id, outcome="applied")

        # upsert
        if not entry:
            # INSERT
            entry = WorkLogEntry(
                id               = change.id,
                user_id          = user.id,
                project_id       = payload.get("project_id") or None,  # None for mobile entries
                container_id     = payload.get("container_id"),
                equipment_serial = payload.get("equipment_serial", ""),
                site_location    = payload.get("site_location", ""),
                category         = payload.get("category", "maintenance"),
                description      = payload.get("description", ""),
                fault_name       = payload.get("fault_name", ""),
                status           = payload.get("status", ""),
                sap_ticket       = payload.get("sap_ticket", ""),
                spare_parts      = payload.get("spare_parts", ""),
                log_date         = payload.get("log_date", ""),
                created_at       = payload.get("created_at", now),
                updated_at       = now,
                version          = 1,
                origin_device    = device_id,
                sync_status      = "synced",
            )
            db.add(entry)
            db.flush()   # get DB-assigned defaults before returning version
            for tag in _clean_tags(payload.get("tags")):
                db.add(WorkLogTag(work_log_id=change.id, tag=tag))
            return SyncChangeResult(id=change.id, outcome="applied", server_version=entry.version)

        # Existing row
        if user.role != "admin" and entry.user_id != user.id:
            return SyncChangeResult(id=change.id, outcome="error", message="Not your entry")

        if entry.version > change.version:
            # Server is ahead — conflict
            return SyncChangeResult(
                id=change.id, outcome="conflict", server_row=_entry_to_dict(entry)
            )

        # Client ahead. Older desktop builds bumped their local version on
        # every edit, so their edits arrived "ahead" and were skipped — every
        # sync, forever, without anyone being told. If this device was also the
        # last to write the row, nobody else has changed it since and the edit
        # is a plain fast-forward. Otherwise the device cannot have seen the
        # other writer's change: that is a conflict, and it must say so.
        if entry.version < change.version:
            if entry.origin_device != device_id:
                return SyncChangeResult(
                    id=change.id, outcome="conflict", server_row=_entry_to_dict(entry)
                )
            change.version = entry.version

        if entry.version == change.version:
            # Fast-forward update
            if "project_id" in payload:
                entry.project_id = payload.get("project_id") or None
            if "container_id" in payload:
                entry.container_id = payload.get("container_id")
            entry.equipment_serial = payload.get("equipment_serial", entry.equipment_serial)
            entry.site_location    = payload.get("site_location", entry.site_location)
            entry.category         = payload.get("category", entry.category)
            entry.description      = payload.get("description", entry.description)
            entry.fault_name       = payload.get("fault_name", entry.fault_name)
            entry.status           = payload.get("status", entry.status)
            entry.sap_ticket       = payload.get("sap_ticket", entry.sap_ticket)
            entry.spare_parts      = payload.get("spare_parts", entry.spare_parts)
            entry.log_date         = payload.get("log_date", entry.log_date)
            entry.deleted_at       = payload.get("deleted_at")
            entry.updated_at       = now
            entry.version         += 1
            entry.origin_device    = device_id

            if "tags" in payload:
                db.query(WorkLogTag).filter(WorkLogTag.work_log_id == change.id).delete()
                for tag in _clean_tags(payload["tags"]):
                    db.add(WorkLogTag(work_log_id=change.id, tag=tag))

            return SyncChangeResult(id=change.id, outcome="applied", server_version=entry.version)

        return SyncChangeResult(id=change.id, outcome="skipped", server_version=entry.version)

    except Exception as exc:
        return SyncChangeResult(id=change.id, outcome="error", message=str(exc))


def _apply_image_change(change, user: User, db: Session) -> SyncChangeResult:
    """Sync image *metadata* only — binary upload is a separate API call."""
    payload = change.payload
    img     = db.query(WorkLogImage).filter(WorkLogImage.id == change.id).first()
    now     = _now()

    try:
        if img:
            return SyncChangeResult(id=change.id, outcome="skipped", message="Image already exists")

        # Verify the parent entry exists and belongs to this user
        entry = db.query(WorkLogEntry).filter(
            WorkLogEntry.id == payload.get("work_log_id")
        ).first()
        if not entry:
            return SyncChangeResult(id=change.id, outcome="error", message="Parent entry not found")
        if user.role != "admin" and entry.user_id != user.id:
            return SyncChangeResult(id=change.id, outcome="error", message="Not your entry")

        new_img = WorkLogImage(
            id             = change.id,
            work_log_id    = payload["work_log_id"],
            file_path      = payload.get("file_path", ""),
            thumbnail_path = payload.get("thumbnail_path"),
            filename       = payload.get("filename", ""),
            size_bytes     = payload.get("size_bytes", 0),
            sha256         = payload.get("sha256", ""),
            width          = payload.get("width"),
            height         = payload.get("height"),
            taken_at       = payload.get("taken_at"),
            uploaded_at    = payload.get("uploaded_at", now),
            updated_at     = now,
            upload_status  = "pending",   # binary not yet on this server
        )
        db.add(new_img)
        return SyncChangeResult(id=change.id, outcome="applied")

    except Exception as exc:
        return SyncChangeResult(id=change.id, outcome="error", message=str(exc))


# ── STATE ─────────────────────────────────────────────────────────────────────

@router.get("/state")
def sync_state(
    device_id: Optional[str] = Query(None),
    db:        Session       = Depends(get_db),
    user:      User          = Depends(get_current_user),
):
    """Returns the server's latest cursor for this user."""
    q = db.query(WorkLogEntry).filter(WorkLogEntry.user_id == user.id)
    if user.role == "admin":
        q = db.query(WorkLogEntry)

    latest = q.order_by(WorkLogEntry.updated_at.desc()).first()
    cursor = latest.updated_at if latest else _EPOCH

    total = q.filter(WorkLogEntry.deleted_at == None).count()

    return {
        "cursor":      cursor,
        "total_active": total,
        "user_id":     user.id,
        "device_id":   device_id,
    }
