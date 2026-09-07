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
    FIRST_ADMIN_USERNAME: str          = os.getenv("FIRST_ADMIN_USERNAME", "admin")
    FIRST_ADMIN_PASSWORD: str          = os.getenv("FIRST_ADMIN_PASSWORD", "admin123")
    # Comma-separated allowed browser origins for the PWA/clients ('*' = any).
    CORS_ORIGINS: str                  = os.getenv("CORS_ORIGINS", "*")


settings = Settings()
