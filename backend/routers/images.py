"""
routers/images.py
------------------
POST   /worklogs/{id}/images        — upload image (multipart)
GET    /worklogs/{id}/images        — list images for an entry
GET    /images/{image_id}/download  — serve/redirect to file
DELETE /images/{image_id}           — delete image
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
from models.schemas import WorkLogImageOut, ImageUploadResponse
from services.storage_service import save_upload, delete_files, get_file_path, MAX_BYTES

router = APIRouter(tags=["images"])


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _image_out(img: WorkLogImage, request: Request) -> WorkLogImageOut:
    base = str(request.base_url)
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
    if user.role != "admin" and entry.user_id != user.id:
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

    abs_path = get_file_path(img.file_path)
    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="File not found on disk")

    return FileResponse(abs_path, filename=img.filename or "image.jpg")


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
