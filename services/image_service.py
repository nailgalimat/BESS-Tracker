"""
services/image_service.py
--------------------------
Local image storage for work_log_entries.

Directory layout:
  <app_dir>/field_images/<work_log_id>/<image_id>.jpg
  <app_dir>/field_images/<work_log_id>/<image_id>_thumb.jpg

Notes:
  - Requires Pillow for thumbnail generation and EXIF reading.
    If Pillow is unavailable, images are stored without thumbnails.
  - sha256 is computed after copy for integrity.
  - File sizes are capped at 25 MB; MIME is validated by magic bytes.
"""

import hashlib
import os
import shutil
import sys
import uuid
from datetime import datetime
from typing import Optional, List

from database.db_manager import get_connection

# ── Allowed image types (magic bytes) ────────────────────────────────────────

_MAGIC = {
    b'\xff\xd8\xff': 'image/jpeg',
    b'\x89PNG':      'image/png',
    b'RIFF':         'image/webp',   # partial; webp has RIFF header
    b'\x00\x00\x00': 'image/heic',  # approximate; will be verified by PIL
}

MAX_FILE_BYTES = 25 * 1024 * 1024   # 25 MB
THUMB_SIZE     = (320, 240)


# ── Paths ─────────────────────────────────────────────────────────────────────

def _get_field_images_dir() -> str:
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = os.path.join(base_dir, 'field_images')
    os.makedirs(d, exist_ok=True)
    return d


def get_image_dir(work_log_id: str) -> str:
    d = os.path.join(_get_field_images_dir(), work_log_id)
    os.makedirs(d, exist_ok=True)
    return d


# ── Helpers ───────────────────────────────────────────────────────────────────

def _compute_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def _detect_mime(path: str) -> str:
    """Detect MIME type by magic bytes (first 4 bytes)."""
    try:
        with open(path, 'rb') as f:
            header = f.read(4)
        for magic, mime in _MAGIC.items():
            if header.startswith(magic):
                return mime
    except Exception:
        pass
    return 'image/jpeg'   # fallback


def _generate_thumbnail(src: str, dst: str) -> bool:
    """Generate a THUMB_SIZE JPEG thumbnail.  Returns True on success."""
    try:
        from PIL import Image
        with Image.open(src) as img:
            img = img.convert('RGB')
            img.thumbnail(THUMB_SIZE, Image.LANCZOS)
            img.save(dst, 'JPEG', quality=75, optimize=True)
        return True
    except Exception:
        return False


def _get_exif_taken_at(path: str) -> Optional[str]:
    """Return ISO 8601 string from EXIF DateTimeOriginal, or None."""
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS
        with Image.open(path) as img:
            exif = img._getexif()
            if exif:
                for tag_id, value in exif.items():
                    if TAGS.get(tag_id) == 'DateTimeOriginal':
                        # "2024:03:15 14:30:00" → "2024-03-15T14:30:00"
                        return value[:10].replace(':', '-') + 'T' + value[11:]
    except Exception:
        pass
    return None


def _get_dimensions(path: str):
    """Return (width, height) or (None, None)."""
    try:
        from PIL import Image
        with Image.open(path) as img:
            return img.size
    except Exception:
        return (None, None)


def _now() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


# ── Public API ────────────────────────────────────────────────────────────────

def save_image(work_log_id: str, source_path: str) -> Optional[dict]:
    """
    Copy *source_path* into app image storage, generate thumbnail,
    compute SHA-256, and insert a work_log_images row.

    Returns the full image record dict, or None on failure.
    """
    if not os.path.isfile(source_path):
        return None

    size_bytes = os.path.getsize(source_path)
    if size_bytes > MAX_FILE_BYTES:
        raise ValueError(
            f"Image too large ({size_bytes // (1024*1024)} MB). Max is 25 MB."
        )

    log_dir   = get_image_dir(work_log_id)
    image_id  = str(uuid.uuid4())
    ext       = os.path.splitext(source_path)[1].lower() or '.jpg'
    dest_path = os.path.join(log_dir, f"{image_id}{ext}")
    thumb_path = os.path.join(log_dir, f"{image_id}_thumb.jpg")

    shutil.copy2(source_path, dest_path)

    sha256    = _compute_sha256(dest_path)
    mime_type = _detect_mime(dest_path)
    taken_at  = _get_exif_taken_at(dest_path)
    width, height = _get_dimensions(dest_path)

    has_thumb = _generate_thumbnail(dest_path, thumb_path)
    if not has_thumb:
        thumb_path = None

    filename  = os.path.basename(source_path)
    now       = _now()

    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO work_log_images
                (id, work_log_id, file_path, thumbnail_path,
                 filename, size_bytes, sha256, width, height,
                 taken_at, uploaded_at, upload_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'local')
        """, (image_id, work_log_id, dest_path, thumb_path,
              filename, size_bytes, sha256, width, height,
              taken_at, now))
        conn.commit()
    finally:
        conn.close()

    return {
        'id':             image_id,
        'work_log_id':    work_log_id,
        'file_path':      dest_path,
        'thumbnail_path': thumb_path,
        'filename':       filename,
        'size_bytes':     size_bytes,
        'sha256':         sha256,
        'mime_type':      mime_type,
        'width':          width,
        'height':         height,
        'taken_at':       taken_at,
        'uploaded_at':    now,
        'upload_status':  'local',
    }


def get_images_for_log(work_log_id: str) -> List[dict]:
    """Return all images for a given work_log_entry, ordered by upload time."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM work_log_images
            WHERE work_log_id = ?
            ORDER BY uploaded_at ASC
        """, (work_log_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_image(image_id: str):
    """Delete image + thumbnail files and remove the DB row."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT file_path, thumbnail_path FROM work_log_images WHERE id=?",
            (image_id,)
        ).fetchone()
        if row:
            for path in [row['file_path'], row['thumbnail_path']]:
                if path and os.path.isfile(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
        conn.execute("DELETE FROM work_log_images WHERE id=?", (image_id,))
        conn.commit()
    finally:
        conn.close()


def download_remote_image(image_id: str, work_log_id: str) -> Optional[str]:
    """
    Return the local file path for *image_id*.
    If the file doesn't exist locally (upload_status='remote'), attempt to
    download it from the sync server, generate a thumbnail, and update the DB.
    Returns the local path on success, None on failure.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM work_log_images WHERE id=?", (image_id,)
        ).fetchone()
        if not row:
            return None
        row = dict(row)
    finally:
        conn.close()

    # Already available locally
    fp = row.get("file_path", "")
    if fp and os.path.isfile(fp):
        return fp

    # Try to download from server
    try:
        from services.sync_client import download_image_file
        log_dir  = get_image_dir(work_log_id)
        filename = row.get("filename") or f"{image_id}.jpg"
        dest     = os.path.join(log_dir, filename)

        if download_image_file(image_id, dest):
            thumb = os.path.join(log_dir, f"{image_id}_thumb.jpg")
            has_thumb = _generate_thumbnail(dest, thumb)

            conn = get_connection()
            try:
                conn.execute(
                    """UPDATE work_log_images
                          SET file_path=?, thumbnail_path=?, upload_status='uploaded'
                        WHERE id=?""",
                    (dest, thumb if has_thumb else None, image_id),
                )
                conn.commit()
            finally:
                conn.close()
            return dest
    except Exception:
        pass
    return None


def delete_images_for_log(work_log_id: str):
    """Delete all images belonging to a log entry (used during full entry delete)."""
    images = get_images_for_log(work_log_id)
    for img in images:
        delete_image(img['id'])


# ── Export photos to the user's computer ──────────────────────────────────────

def _safe_name(s: str, maxlen: int = 60) -> str:
    """Make a string safe to use as a file/folder name."""
    import re
    s = re.sub(r'[^\w\-. ]+', '_', (s or '')).strip().strip('.')
    s = re.sub(r'\s+', '_', s)
    return s[:maxlen] or 'photo'


def export_entry_images(work_log_id: str, dest_dir: str,
                        name_prefix: str = "") -> dict:
    """
    Copy every photo of an entry into *dest_dir* (creating it if needed).
    Remote-only photos are downloaded from the sync server first.

    name_prefix : if given, files are named '<prefix>_01.jpg', '<prefix>_02.jpg' …;
                  otherwise the original filename is kept (sanitised).

    Returns {'saved': int, 'failed': [str], 'total': int, 'dest': str}.
    """
    os.makedirs(dest_dir, exist_ok=True)
    images = get_images_for_log(work_log_id)
    saved, failed = 0, []

    for i, img in enumerate(images, 1):
        src = img.get('file_path') or ''
        if not (src and os.path.isfile(src)):
            src = download_remote_image(img['id'], work_log_id) or ''
        if not (src and os.path.isfile(src)):
            failed.append(img.get('filename') or img['id'])
            continue

        orig = img.get('filename') or os.path.basename(src)
        ext = os.path.splitext(orig)[1] or '.jpg'
        if name_prefix:
            base = f"{_safe_name(name_prefix)}_{i:02d}"
        else:
            base = _safe_name(os.path.splitext(orig)[0] or f"photo_{i}")

        dest = os.path.join(dest_dir, base + ext)
        k = 1
        while os.path.exists(dest):
            dest = os.path.join(dest_dir, f"{base}_{k}{ext}")
            k += 1
        try:
            shutil.copy2(src, dest)
            saved += 1
        except Exception as ex:
            failed.append(f"{orig}: {ex}")

    return {'saved': saved, 'failed': failed, 'total': len(images),
            'dest': dest_dir}
