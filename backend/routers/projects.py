"""
routers/projects.py
--------------------
GET /projects — list projects (for the mobile project picker)
PUT /projects — replace the project list (published by the desktop on sync)

The desktop SQLite is the source of truth for projects; the server keeps
a mirror so mobile clients can attach entries to a project while offline.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user
from models.db_models import User, Project
from models.schemas import ProjectOut, ProjectsSyncRequest

router = APIRouter(prefix="/projects", tags=["projects"])


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@router.get("", response_model=list[ProjectOut])
def list_projects(
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    rows = db.query(Project).order_by(Project.name).all()
    return [
        ProjectOut(id=p.id, name=p.name,
                   project_type=p.project_type or "BESS",
                   num_blocks=p.num_blocks or 0)
        for p in rows
    ]


@router.put("", response_model=dict)
def replace_projects(
    body: ProjectsSyncRequest,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    """Upserts the pushed list and removes projects no longer present."""
    now = _now()
    incoming_ids = {p.id for p in body.projects}

    for p in body.projects:
        existing = db.query(Project).filter(Project.id == p.id).first()
        if existing:
            existing.name         = p.name
            existing.project_type = p.project_type
            existing.num_blocks   = p.num_blocks or 0
            existing.updated_at   = now
        else:
            db.add(Project(
                id           = p.id,
                name         = p.name,
                project_type = p.project_type,
                num_blocks   = p.num_blocks or 0,
                updated_at   = now,
            ))

    if incoming_ids:
        db.query(Project).filter(~Project.id.in_(incoming_ids)).delete(
            synchronize_session=False)
    else:
        db.query(Project).delete(synchronize_session=False)

    db.commit()
    return {"count": len(body.projects)}
