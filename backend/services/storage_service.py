"""
services/storage_service.py
-----------------------------
Local filesystem image storage.
All paths stored in DB are RELATIVE to UPLOAD_DIR.
To swap to S3/MinIO, replace save_upload() and get_file_path() here.

Short video rides the same store as photos: a clip is a work_log_images row
whose mime type is a video one. There is no ffmpeg here and there must not be,
so a clip cannot be thumbnailed on the server — the phone records a poster
frame at capture and uploads it with the clip, and that poster IS the row's
thumbnail. It therefore archives and frees with the clip, needs no second row,
and never shows up as an extra photo on a record.

Video has its OWN size cap (MAX_VIDEO_SIZE_MB) and its own allowed-mime list.
They are separate from the photo ones deliberately: a clip is bigger than a
photo, and raising one limit must not silently raise the other.
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

# What a phone's MediaRecorder actually produces: WebM (VP8/VP9 + Opus) on
# Android Chrome, MP4 (H.264 + AAC) where that is offered, QuickTime from an
# iOS capture. Anything else is refused rather than stored as an unknown blob.
ALLOWED_VIDEO_MIMES = {"video/webm", "video/mp4", "video/quicktime"}
MAX_VIDEO_BYTES     = settings.MAX_VIDEO_SIZE_MB * 1024 * 1024

# The uploaded poster frame is a still and is capped like one — it is one JPEG.
MAX_POSTER_BYTES    = 4 * 1024 * 1024


def is_video(mime: Optional[str]) -> bool:
    return bool(mime) and str(mime).split(";")[0].strip().lower() in ALLOWED_VIDEO_MIMES


def max_bytes_for(mime: Optional[str]) -> int:
    """The cap that applies to this kind of upload."""
    return MAX_VIDEO_BYTES if is_video(mime) else MAX_BYTES


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


def sniff_mime(hdr: bytes) -> str:
    """Best-effort MIME from the first bytes of a file.

    Split out from _detect_mime so the upload route can decide WHICH size cap
    to stream against from the first chunk, before the whole body is on disk.

    The ISO base-media check is one test for three formats: MP4, QuickTime and
    HEIC all carry "ftyp" at offset 4 and differ only in the brand that follows.
    That is also why HEIC, which used to reach the store as "image/jpeg" through
    the fallback below, is now named properly — and why a phone video cannot be
    mistaken for a photo and measured against the photo cap.
    """
    hdr = hdr or b""
    if hdr[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if hdr[:4] == b"\x89PNG":
        return "image/png"
    if hdr[:4] == b"RIFF" and hdr[8:12] == b"WEBP":
        return "image/webp"
    if hdr[:4] == b"\x1a\x45\xdf\xa3":
        # EBML: Matroska or WebM. MediaRecorder on Android Chrome writes this.
        return "video/webm"
    if hdr[4:8] == b"ftyp":
        brand = hdr[8:12]
        if brand in (b"qt  ",):
            return "video/quicktime"
        if brand in (b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx",
                     b"mif1", b"msf1"):
            return "image/heic"
        return "video/mp4"
    return ""


def _detect_mime(path: str) -> str:
    """Best-effort MIME from magic bytes; 'image/jpeg' when nothing matches.

    The fallback is kept as it was: the phones have always uploaded formats this
    sniffer does not recognise byte for byte, they were accepted as JPEG, and
    tightening that here would start refusing photos that work today.
    """
    try:
        with open(path, "rb") as f:
            hdr = f.read(16)
    except Exception:
        return "image/jpeg"
    return sniff_mime(hdr) or "image/jpeg"


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
    poster_path: Optional[str] = None,
    duration_ms: Optional[int] = None,
) -> dict:
    """
    Move *src_path* (temp file) into permanent storage under work_log_id/.
    Returns a dict with all metadata needed for WorkLogImage creation.
    Raises ValueError if file is too large or has wrong MIME.

    *poster_path* is a temp file holding the still the phone grabbed from its own
    camera stream, only meaningful for a video: it becomes the row's thumbnail,
    because nothing here can produce one from a clip. Both are optional, so a
    client that knows nothing about video calls this exactly as before.
    """
    size_bytes = os.path.getsize(src_path)
    mime = _detect_mime(src_path)
    video = is_video(mime)

    limit = max_bytes_for(mime)
    if size_bytes > limit:
        raise ValueError(
            "{} too large ({} MB). Max {} MB.".format(
                "Clip" if video else "File",
                size_bytes // (1024 * 1024), limit // (1024 * 1024))
        )
    if not (video or mime in ALLOWED_MIMES):
        raise ValueError(f"Unsupported file type: {mime}")

    image_id = str(uuid.uuid4())
    ext      = os.path.splitext(original_filename)[1].lower() or (
        ".webm" if video else ".jpg")

    # Relative paths (stored in DB)
    rel_file  = os.path.join(work_log_id, f"{image_id}{ext}")
    rel_thumb = os.path.join(work_log_id, f"{image_id}_thumb.jpg")

    abs_dir   = os.path.join(_upload_root(), work_log_id)
    os.makedirs(abs_dir, exist_ok=True)

    abs_file  = os.path.join(_upload_root(), rel_file)
    abs_thumb = os.path.join(_upload_root(), rel_thumb)

    shutil.move(src_path, abs_file)

    sha256   = _compute_sha256(abs_file)
    taken_at = None if video else _get_exif_taken_at(abs_file)

    if video:
        # Pillow cannot open a clip, so the dimensions and the thumbnail both
        # come from the uploaded poster. A clip without one is still stored —
        # losing the whole clip because its poster failed would be the wrong
        # trade — it simply has no thumbnail to show.
        has_thumb = _store_poster(poster_path, abs_thumb)
        width, height = _get_dimensions(poster_path) if has_thumb else (None, None)
    else:
        width, height = _get_dimensions(abs_file)
        has_thumb     = _generate_thumbnail(abs_file, abs_thumb)

    try:
        dur = int(duration_ms) if duration_ms is not None else None
    except (TypeError, ValueError):
        dur = None

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
        "duration_ms":    dur if video else None,
    }


def _store_poster(poster_path: Optional[str], abs_thumb: str) -> bool:
    """Put the phone's poster frame in the thumbnail's place.

    Kept at the size the phone sent, NOT shrunk to THUMB_SIZE like a photo's
    thumbnail. The poster carries the same caption strip a photo does — project,
    date and time, node, coordinates — and at 320x240 that text is no longer
    readable. It is one 854x480 JPEG, a rounding error beside a 3 MB clip.

    Validated on its own: its own magic bytes, its own cap, and Pillow must be
    able to open it. The poster arrives as a separate part of the same request,
    so it is checked as separately as the clip is.
    """
    if not (poster_path and os.path.isfile(poster_path)):
        return False
    try:
        if os.path.getsize(poster_path) > MAX_POSTER_BYTES:
            return False
        with open(poster_path, "rb") as f:
            # closed before the caller unlinks the temp file
            if sniff_mime(f.read(16)) not in ALLOWED_MIMES:
                return False
        if _get_dimensions(poster_path) == (None, None):
            return False              # only looks like a JPEG
        shutil.copyfile(poster_path, abs_thumb)
        return True
    except Exception:
        return False


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
