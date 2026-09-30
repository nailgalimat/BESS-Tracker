"""
routers/images.py
------------------
POST   /worklogs/{id}/images        — upload image (multipart)
GET    /worklogs/{id}/images        — list images for an entry
GET    /images/storage              — how much the server holds vs has handed over
POST   /images/archived             — the desktop confirms; the server drops files
GET    /images/{image_id}/download  — serve/redirect to file
DELETE /images/{image_id}           — delete image

Photo retention: this server is a staging post, not the archive. A phone
uploads a photo here, the office desktop downloads it and verifies its SHA-256
against the hash stored at upload, and only then confirms — at which point the
file and its thumbnail are deleted while the row and all of its metadata stay.
Nothing is ever deleted on a timer or on a guess; an unconfirmed photo is kept
for ever, so a desktop that never confirms anything loses nothing.
"""

import os
import tempfile
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, UploadFile, File, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user
from models.db_models import User, WorkLogEntry, WorkLogImage
from models.schemas import (
    WorkLogImageOut, ImageUploadResponse,
    ImageArchiveRequest, ImageArchiveResult, ImageArchiveResponse,
    ImageStorageReport,
)
from services.storage_service import save_upload, delete_files, get_file_path, MAX_BYTES

router = APIRouter(tags=["images"])

# One sweep of a desktop that has never confirmed anything has hundreds of
# photos to hand over; a cap keeps one request's work and body bounded.
MAX_ARCHIVE_BATCH = 500


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _image_out(img: WorkLogImage, request: Request) -> WorkLogImageOut:
    base = str(request.base_url)
    archived_at = getattr(img, "file_archived_at", None)
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
        download_url = f"{base}images/{img.id}/download",
        archived     = bool(archived_at),
        archived_at  = archived_at,
    )


# ── Upload ────────────────────────────────────────────────────────────────────

@router.post("/worklogs/{entry_id}/images", response_model=ImageUploadResponse, status_code=201)
async def upload_image(
    entry_id:     str,
    file:         UploadFile      = File(...),
    request:      Request         = None,
    x_image_id:   Optional[str]   = Header(None, alias="X-Image-ID"),
    db:           Session         = Depends(get_db),
    user:         User            = Depends(get_current_user),
):
    entry = db.query(WorkLogEntry).filter(
        WorkLogEntry.id         == entry_id,
        WorkLogEntry.deleted_at == None,
    ).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    # The technician the job was given to photographs the job: their upload
    # belongs on that record, not on a second one of their own.
    if (user.role != "admin" and entry.user_id != user.id
            and (entry.assigned_to or "") != user.id):
        raise HTTPException(status_code=403, detail="Not your entry")

    # ── Idempotency: if client sent X-Image-ID and it already exists, return it ─
    if x_image_id:
        existing = db.query(WorkLogImage).filter(WorkLogImage.id == x_image_id).first()
        if existing:
            base = str(request.base_url) if request else ""
            return ImageUploadResponse(
                id           = existing.id,
                work_log_id  = existing.work_log_id,
                filename     = existing.filename,
                size_bytes   = existing.size_bytes,
                sha256       = existing.sha256,
                width        = existing.width,
                height       = existing.height,
                taken_at     = existing.taken_at,
                uploaded_at  = existing.uploaded_at,
                download_url = f"{base}images/{existing.id}/download",
            )

    # Stream to temp file (avoids loading whole file into memory)
    suffix = os.path.splitext(file.filename or "upload")[1] or ".jpg"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        total = 0
        while chunk := await file.read(65536):
            total += len(chunk)
            if total > MAX_BYTES:
                tmp.close()
                os.unlink(tmp.name)
                raise HTTPException(
                    status_code=413,
                    detail=f"File too large. Max {MAX_BYTES // (1024*1024)} MB."
                )
            tmp.write(chunk)
        tmp.close()

        meta = save_upload(entry_id, tmp.name, file.filename or "upload")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        if os.path.exists(tmp.name):
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    now = _now()
    img = WorkLogImage(
        id             = x_image_id or meta["id"],   # prefer client-provided UUID
        work_log_id    = entry_id,
        file_path      = meta["file_path"],
        thumbnail_path = meta.get("thumbnail_path"),
        filename       = meta["filename"],
        size_bytes     = meta["size_bytes"],
        sha256         = meta["sha256"],
        width          = meta.get("width"),
        height         = meta.get("height"),
        taken_at       = meta.get("taken_at"),
        uploaded_at    = now,
        updated_at     = now,
        upload_status  = "uploaded",
    )
    db.add(img)
    db.commit()
    db.refresh(img)

    base = str(request.base_url) if request else ""
    return ImageUploadResponse(
        id           = img.id,
        work_log_id  = img.work_log_id,
        filename     = img.filename,
        size_bytes   = img.size_bytes,
        sha256       = img.sha256,
        width        = img.width,
        height       = img.height,
        taken_at     = img.taken_at,
        uploaded_at  = img.uploaded_at,
        download_url = f"{base}images/{img.id}/download",
    )


# ── List images for an entry ─────────────────────────────────────────────────

@router.get("/worklogs/{entry_id}/images", response_model=list[WorkLogImageOut])
def list_images(
    entry_id: str,
    request:  Request = None,
    db:       Session = Depends(get_db),
    user:     User    = Depends(get_current_user),
):
    entry = db.query(WorkLogEntry).filter(WorkLogEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    if user.role != "admin" and entry.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your entry")

    images = (
        db.query(WorkLogImage)
        .filter(WorkLogImage.work_log_id == entry_id)
        .order_by(WorkLogImage.uploaded_at)
        .all()
    )
    return [_image_out(img, request) for img in images]


# ── Download ──────────────────────────────────────────────────────────────────

@router.get("/images/{image_id}/download")
def download_image(
    image_id: str,
    db:       Session = Depends(get_db),
    user:     User    = Depends(get_current_user),
):
    img = db.query(WorkLogImage).filter(WorkLogImage.id == image_id).first()
    if not img:
        raise HTTPException(status_code=404, detail="Image not found")

    entry = db.query(WorkLogEntry).filter(WorkLogEntry.id == img.work_log_id).first()
    if user.role != "admin" and (not entry or entry.user_id != user.id):
        raise HTTPException(status_code=403, detail="Not your image")

    # Archived: the photo exists, this server just no longer keeps the bytes.
    # 410 Gone, not 404 — a client must be able to tell "the office desktop has
    # it" from "your photo is lost", and say so to the person holding the phone.
    archived_at = getattr(img, "file_archived_at", None)
    if archived_at:
        raise HTTPException(
            status_code=410,
            detail=(
                f"Photo archived on the office desktop on {archived_at[:10]}. "
                "The server no longer keeps a copy — ask the office for it. "
                "Nothing is lost."
            ),
        )

    abs_path = get_file_path(img.file_path)
    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="File not found on disk")

    return FileResponse(abs_path, filename=img.filename or "image.jpg")


# ── The desktop confirms it holds the photo; the server drops its file ────────

@router.post("/images/archived", response_model=ImageArchiveResponse)
def confirm_archived(
    body: ImageArchiveRequest,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    """The desktop says: these photos are safe here, you may delete your files.

    File-only: the `work_log_images` row and every field on it survive, so the
    record still shows a photo exists, `GET /worklogs/{id}/images` still lists
    it (flagged `archived`), and the stored sha256 still identifies the
    desktop's copy. This is deliberately NOT `DELETE /images/{id}`, which
    removes the record itself.

    Idempotent: confirming an already-archived photo is counted as `already`,
    never an error, so a confirm call that was interrupted after the files were
    deleted can simply be repeated on the next sync.

    `updated_at` is intentionally left alone. None of the photo's metadata
    changed, only where its bytes live, and bumping it would re-deliver every
    archived row to every client on their next pull for no gain.
    """
    ids = [i for i in (body.image_ids or []) if i]
    if not ids:
        return ImageArchiveResponse(results=[], archived=0, already=0, freed_bytes=0)
    if len(ids) > MAX_ARCHIVE_BATCH:
        raise HTTPException(
            status_code=413,
            detail=f"Too many ids in one call. Max {MAX_ARCHIVE_BATCH}.",
        )

    rows = {r.id: r for r in db.query(WorkLogImage).filter(WorkLogImage.id.in_(ids)).all()}
    # One query for the parent entries, so a 500-photo sweep is not 500 lookups.
    entry_ids = {r.work_log_id for r in rows.values()}
    entries = {}
    if entry_ids:
        entries = {e.id: e for e in db.query(WorkLogEntry).filter(
            WorkLogEntry.id.in_(entry_ids)).all()}

    now = _now()
    results: list[ImageArchiveResult] = []
    n_archived = n_already = freed = 0

    for image_id in ids:
        img = rows.get(image_id)
        if img is None:
            results.append(ImageArchiveResult(id=image_id, outcome="not_found"))
            continue

        entry = entries.get(img.work_log_id)
        if user.role != "admin" and (not entry or entry.user_id != user.id):
            results.append(ImageArchiveResult(id=image_id, outcome="forbidden"))
            continue

        if img.file_archived_at:
            n_already += 1
            results.append(ImageArchiveResult(id=image_id, outcome="already"))
            continue

        # Measure before deleting: size_bytes is the original upload size, and
        # the thumbnail is freed too.
        bytes_here = 0
        for rel in (img.file_path, img.thumbnail_path):
            if not rel:
                continue
            try:
                p = get_file_path(rel)
                if os.path.isfile(p):
                    bytes_here += os.path.getsize(p)
            except OSError:
                pass

        delete_files(img.file_path, img.thumbnail_path)
        img.file_archived_at = now
        img.archived_by      = user.id
        n_archived += 1
        freed += bytes_here
        results.append(ImageArchiveResult(id=image_id, outcome="archived",
                                          freed_bytes=bytes_here))

    db.commit()
    return ImageArchiveResponse(results=results, archived=n_archived,
                                already=n_already, freed_bytes=freed)


# ── What the server is holding ────────────────────────────────────────────────

@router.get("/images/storage", response_model=ImageStorageReport)
def image_storage(
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    """Counts and bytes: photos whose files are still here vs handed over, plus
    the disk under the uploads directory. Numbers only, no paths. Admin only —
    it is a whole-deployment figure, not one person's photos."""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")

    from sqlalchemy import func
    from config import disk_report

    on_n, on_b = db.query(
        func.count(WorkLogImage.id), func.coalesce(func.sum(WorkLogImage.size_bytes), 0)
    ).filter(WorkLogImage.file_archived_at == None).one()
    ar_n, ar_b = db.query(
        func.count(WorkLogImage.id), func.coalesce(func.sum(WorkLogImage.size_bytes), 0)
    ).filter(WorkLogImage.file_archived_at != None).one()

    return ImageStorageReport(
        on_server_count = int(on_n or 0),
        on_server_bytes = int(on_b or 0),
        archived_count  = int(ar_n or 0),
        archived_bytes  = int(ar_b or 0),
        disk            = disk_report(),
    )


# ── Delete ────────────────────────────────────────────────────────────────────

@router.delete("/images/{image_id}", status_code=204)
def delete_image(
    image_id: str,
    db:       Session = Depends(get_db),
    user:     User    = Depends(get_current_user),
):
    img = db.query(WorkLogImage).filter(WorkLogImage.id == image_id).first()
    if not img:
        raise HTTPException(status_code=404, detail="Image not found")

    entry = db.query(WorkLogEntry).filter(WorkLogEntry.id == img.work_log_id).first()
    if user.role != "admin" and (not entry or entry.user_id != user.id):
        raise HTTPException(status_code=403, detail="Not your image")

    delete_files(img.file_path, img.thumbnail_path)
    db.delete(img)
    db.commit()
