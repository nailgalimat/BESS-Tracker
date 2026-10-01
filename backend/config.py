"""
config.py
----------
All settings from environment variables / .env file.
"""

import os
import secrets
from dotenv import load_dotenv

load_dotenv()


def _normalize_db_url(url: str) -> str:
    """Managed Postgres hosts (Render/Heroku/Railway) hand out a URL starting
    with 'postgres://', but SQLAlchemy 2.x needs the driver-qualified scheme.
    Leaves SQLite and already-qualified URLs untouched — so switching the
    backend to Postgres later is just setting DATABASE_URL, no code change."""
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg2://", 1)
    return url


class Settings:
    SECRET_KEY: str                    = os.getenv("SECRET_KEY", secrets.token_hex(32))
    ACCESS_TOKEN_EXPIRE_MINUTES: int   = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
    REFRESH_TOKEN_EXPIRE_DAYS: int     = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))
    DATABASE_URL: str                  = _normalize_db_url(os.getenv("DATABASE_URL", "sqlite:///./backend.db"))
    UPLOAD_DIR: str                    = os.getenv("UPLOAD_DIR", "./uploads")
    MAX_IMAGE_SIZE_MB: int             = int(os.getenv("MAX_IMAGE_SIZE_MB", "25"))
    # A short clip is bigger than a photo but must not be allowed to be a film:
    # the phone caps recording at 30 s and asks its encoder for ~0.75 Mbit/s, so
    # a clip lands around 3 MB. 40 MB is headroom for a browser that ignores the
    # bitrate hint, not a licence to upload a 4K minute. Separate from
    # MAX_IMAGE_SIZE_MB on purpose — raising the photo cap must not raise this.
    MAX_VIDEO_SIZE_MB: int             = int(os.getenv("MAX_VIDEO_SIZE_MB", "40"))
    FIRST_ADMIN_USERNAME: str          = os.getenv("FIRST_ADMIN_USERNAME", "admin")
    FIRST_ADMIN_PASSWORD: str          = os.getenv("FIRST_ADMIN_PASSWORD", "admin123")
    # Comma-separated allowed browser origins for the PWA/clients ('*' = any).
    CORS_ORIGINS: str                  = os.getenv("CORS_ORIGINS", "*")


settings = Settings()


# ── Deployment health ─────────────────────────────────────────────────────────

def storage_report() -> dict:
    """Is this deployment's state actually persistent?

    Two defaults are safe on a laptop and quietly destructive in the cloud:

      * DATABASE_URL falls back to `sqlite:///./backend.db` — a path *inside*
        the container. Every deploy replaces the container, so the whole
        database (users, work logs, stock, field events) is wiped and the
        startup bootstrap recreates a lone admin. It looks exactly like
        "everyone's accounts vanished".
      * SECRET_KEY falls back to a fresh random value per process, so every
        restart invalidates every access and refresh token and forces
        everyone to log in again.

    Reports which of those are in force, and how full the disk holding the
    uploads is: the server could not previously tell anyone it was running out
    of space, which is how it filled up unnoticed. No paths or secrets are
    returned — only booleans and numbers.
    """
    url = settings.DATABASE_URL
    sqlite_path = ''
    if url.startswith('sqlite'):
        sqlite_path = url.split('///', 1)[-1] if '///' in url else ''

    # A relative sqlite path lives in the container's working directory.
    # Judge with both path flavours: the server runs on Linux, but this is
    # also read from a Windows desktop, where os.path.isabs('/var/data/x')
    # is False and would mislabel a perfectly good deployment.
    import ntpath
    import posixpath
    is_abs = posixpath.isabs(sqlite_path) or ntpath.isabs(sqlite_path)
    db_ephemeral = bool(sqlite_path) and not is_abs

    return {
        'database': 'ephemeral' if db_ephemeral else 'persistent',
        'database_env_set': bool(os.getenv('DATABASE_URL')),
        'uploads_env_set': bool(os.getenv('UPLOAD_DIR')),
        'secret_key_env_set': bool(os.getenv('SECRET_KEY')),
        'disk': disk_report(),
    }


def disk_report() -> dict:
    """Free space on the volume holding the uploads, in MB and percent.

    Render's disk is 1 GB and the photos plus the SQLite database share it.
    Nothing used to report this, so 'the disk is nearly full' only became
    visible as uploads starting to fail. Numbers only — the caller may be
    unauthenticated, so the directory itself is never named.

    An empty dict when the directory cannot be measured (it does not exist yet
    on a fresh container): a health probe must not fail over a missing folder.
    """
    import shutil
    try:
        usage = shutil.disk_usage(settings.UPLOAD_DIR)
    except OSError:
        try:
            os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
            usage = shutil.disk_usage(settings.UPLOAD_DIR)
        except OSError:
            return {}
    mb = 1024 * 1024
    return {
        'total_mb': round(usage.total / mb, 1),
        'used_mb':  round(usage.used / mb, 1),
        'free_mb':  round(usage.free / mb, 1),
        # Of total, not of (used+free): on Linux a few percent are reserved for
        # root, so used+free < total and the two definitions differ.
        'used_pct': round(usage.used * 100.0 / usage.total, 1) if usage.total else 0.0,
    }
