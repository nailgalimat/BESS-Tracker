"""
services/storage_service.py
-----------------------------
Local filesystem image storage.
All paths stored in DB are RELATIVE to UPLOAD_DIR.
To swap to S3/MinIO, replace save_upload() and get_file_path() here.
"""

import hashlib
import os
import shutil
import uuid
from typing import Optional

from config import settings

THUMB_SIZE       = (320, 240)
ALLOWED_MIMES    = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/bmp"}
MAX_BYTES        = settings.MAX_IMAGE_SIZE_MB * 1024 * 1024


def _upload_root() -> str:
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    return os.path.abspath(settings.UPLOAD_DIR)


def get_file_path(relative: str) -> str:
    """Resolve a stored relative path to an absolute filesystem path."""
    return os.path.join(_upload_root(), relative)


def _compute_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _detect_mime(path: str) -> str:
    """Best-effort MIME from magic bytes."""
    try:
        with open(path, "rb") as f:
            hdr = f.read(16)
        if hdr[:3] == b"\xff\xd8\xff":
            return "image/jpeg"
        if hdr[:4] == b"\x89PNG":
            return "image/png"
        if hdr[:4] == b"RIFF" and hdr[8:12] == b"WEBP":
            return "image/webp"
    except Exception:
        pass
    return "image/jpeg"


def _get_exif_taken_at(path: str) -> Optional[str]:
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS
        with Image.open(path) as img:
            exif = img._getexif()
            if exif:
                for tag_id, value in exif.items():
                    if TAGS.get(tag_id) == "DateTimeOriginal":
                        return value[:10].replace(":", "-") + "T" + value[11:]
    except Exception:
        pass
    return None


def _get_dimensions(path: str) -> tuple[Optional[int], Optional[int]]:
    try:
        from PIL import Image
        with Image.open(path) as img:
            return img.size          # (width, height)
    except Exception:
        return (None, None)


def _generate_thumbnail(src: str, dst: str) -> bool:
    try:
        from PIL import Image
        with Image.open(src) as img:
            img = img.convert("RGB")
            img.thumbnail(THUMB_SIZE, Image.LANCZOS)
            img.save(dst, "JPEG", quality=75, optimize=True)
        return True
    except Exception:
        return False


def save_upload(
    work_log_id: str,
    src_path: str,
    original_filename: str,
) -> dict:
    """
    Move *src_path* (temp file) into permanent storage under work_log_id/.
    Returns a dict with all metadata needed for WorkLogImage creation.
    Raises ValueError if file is too large or has wrong MIME.
    """
    size_bytes = os.path.getsize(src_path)
    if size_bytes > MAX_BYTES:
        raise ValueError(
            f"File too large ({size_bytes // (1024 * 1024)} MB). Max {settings.MAX_IMAGE_SIZE_MB} MB."
        )

    mime = _detect_mime(src_path)
    if mime not in ALLOWED_MIMES:
        raise ValueError(f"Unsupported file type: {mime}")

    image_id = str(uuid.uuid4())
    ext      = os.path.splitext(original_filename)[1].lower() or ".jpg"

    # Relative paths (stored in DB)
    rel_file  = os.path.join(work_log_id, f"{image_id}{ext}")
    rel_thumb = os.path.join(work_log_id, f"{image_id}_thumb.jpg")

    abs_dir   = os.path.join(_upload_root(), work_log_id)
    os.makedirs(abs_dir, exist_ok=True)

    abs_file  = os.path.join(_upload_root(), rel_file)
    abs_thumb = os.path.join(_upload_root(), rel_thumb)

    shutil.move(src_path, abs_file)

    sha256         = _compute_sha256(abs_file)
    taken_at       = _get_exif_taken_at(abs_file)
    width, height  = _get_dimensions(abs_file)
    has_thumb      = _generate_thumbnail(abs_file, abs_thumb)

    return {
        "id":             image_id,
        "file_path":      rel_file,
        "thumbnail_path": rel_thumb if has_thumb else None,
        "filename":       original_filename,
        "size_bytes":     size_bytes,
        "sha256":         sha256,
        "mime_type":      mime,
        "width":          width,
        "height":         height,
        "taken_at":       taken_at,
    }


def delete_files(file_path: Optional[str], thumbnail_path: Optional[str]):
    """Remove image + thumbnail from disk (silent on failure)."""
    root = _upload_root()
    for rel in [file_path, thumbnail_path]:
        if rel:
            abs_path = os.path.join(root, rel)
            try:
                if os.path.isfile(abs_path):
                    os.remove(abs_path)
            except OSError:
                pass
