"""
main.py
--------
FastAPI application entry point.

Run:
  uvicorn main:app --reload --host 0.0.0.0 --port 8000

Docs:
  http://localhost:8000/docs    (Swagger UI)
  http://localhost:8000/redoc   (ReDoc)
"""

import json
import logging
import logging.config
import os
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import text

from config import settings
from database import engine, Base, SessionLocal
from models.db_models import User
from services.auth_service import hash_password
from routers import auth, worklogs, images, sync, projects


# ── Structured logging ────────────────────────────────────────────────────────

logging.config.dictConfig({
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "logging.Formatter",
            "fmt": '{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
            "datefmt": "%Y-%m-%dT%H:%M:%S",
        }
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "json"},
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": "bess_server.log",
            "maxBytes": 10_485_760,   # 10 MB
            "backupCount": 5,
            "formatter": "json",
        },
    },
    "root": {"level": "INFO", "handlers": ["console", "file"]},
})

log = logging.getLogger("bess.main")


# ── Startup: create tables + default admin ────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Security warnings ─────────────────────────────────────────────────────
    if settings.FIRST_ADMIN_PASSWORD in ("admin123", "admin", "password", "changeme"):
        log.warning(
            "startup.insecure_default_password: "
            "FIRST_ADMIN_PASSWORD is set to a well-known default. "
            "Set FIRST_ADMIN_PASSWORD env var before exposing this server."
        )
    if len(settings.SECRET_KEY) < 32:
        raise RuntimeError(
            "SECRET_KEY is too short (< 32 chars). "
            "Set SECRET_KEY env var to a strong random value."
        )

    # ── Create all tables + indexes ───────────────────────────────────────────
    Base.metadata.create_all(bind=engine)

    # Lightweight migration: add optional technical-work columns to an existing
    # work_log_entries table (create_all does not alter existing tables).
    from sqlalchemy import text as _sql_text
    with engine.begin() as _conn:
        _cols = {r[1] for r in _conn.exec_driver_sql(
            "PRAGMA table_info(work_log_entries)").fetchall()}
        for _c in ("fault_name", "status", "sap_ticket", "spare_parts"):
            if _c not in _cols:
                _conn.exec_driver_sql(
                    f"ALTER TABLE work_log_entries ADD COLUMN {_c} TEXT DEFAULT ''")

    # Ensure uploads directory exists
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)

    # Create first admin if no users exist
    db = SessionLocal()
    try:
        if not db.query(User).first():
            admin = User(
                username      = settings.FIRST_ADMIN_USERNAME,
                password_hash = hash_password(settings.FIRST_ADMIN_PASSWORD),
                role          = "admin",
                email         = None,
            )
            db.add(admin)
            db.commit()
            log.info(
                f"startup.admin_created: username='{settings.FIRST_ADMIN_USERNAME}' — "
                "change the password immediately!"
            )
    finally:
        db.close()

    log.info("startup.ready")
    yield   # application runs
    log.info("shutdown.complete")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title       = "BESS Tracker Sync API",
    description = "Backend for BESS Tracker desktop + mobile sync",
    version     = "1.0.0",
    lifespan    = lifespan,
)

# ── Rate limiter ──────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── CORS — allow desktop app and mobile; tighten in production ────────────────
_cors = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()] or ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins     = _cors,
    allow_credentials = _cors != ["*"],   # credentials + wildcard is invalid; PWA is same-origin anyway
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

# ── Request-ID middleware ─────────────────────────────────────────────────────

@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = req_id
    return response

# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(auth.router)
app.include_router(worklogs.router)
app.include_router(images.router)
app.include_router(sync.router)
app.include_router(projects.router)


@app.get("/healthz", tags=["ops"])
def healthz():
    """Liveness probe for the host (Render health check)."""
    return {"ok": True}


# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/health", tags=["system"])
def health():
    # ── Database probe ────────────────────────────────────────────────────────
    db_ok = False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception as exc:
        log.error(f"health.db_probe_failed: {exc}")

    # ── Disk space ────────────────────────────────────────────────────────────
    disk_info: dict = {}
    try:
        usage = shutil.disk_usage(settings.UPLOAD_DIR)
        disk_info = {
            "total_gb": round(usage.total / 1e9, 1),
            "free_gb":  round(usage.free  / 1e9, 1),
            "used_pct": round((usage.used / usage.total) * 100, 1),
        }
    except Exception:
        pass

    status = "ok" if db_ok else "degraded"
    return JSONResponse(
        status_code = 200 if db_ok else 503,
        content     = {
            "status":  status,
            "version": app.version,
            "db":      "ok" if db_ok else "error",
            "disk":    disk_info,
        },
    )


@app.get("/", tags=["system"])
def root():
    return {
        "name":    app.title,
        "version": app.version,
        "docs":    "/docs",
        "pwa":     "/app/",
    }


# ── PWA static files — mounted AFTER API routes so /app/* doesn't shadow them ─
_static_dir = Path(__file__).parent / "static"
app.mount("/app", StaticFiles(directory=str(_static_dir), html=True), name="pwa")
