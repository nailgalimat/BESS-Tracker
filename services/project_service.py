"""
services/project_service.py
----------------------------
All database operations related to Projects and Containers.
The UI calls these functions — keeping business logic separate from UI code.
"""

from database.db_manager import get_connection
from models.models import Project, Container
from typing import List, Optional


# ──────────────────────────────────────────
#  PROJECT OPERATIONS
# ──────────────────────────────────────────

def create_project(project: Project) -> int:
    """
    Saves a new project to the database.
    Returns the new project's ID.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO projects (name, description, num_zones, num_blocks, num_containers)
        VALUES (?, ?, ?, ?, ?)
    """, (project.name, project.description,
          project.num_zones, project.num_blocks, project.num_containers))
    project_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return project_id


def get_all_projects() -> List[Project]:
    """Returns all projects from the database."""
    conn = get_connection()
    rows = conn.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
    conn.close()
    return [Project(
        id=r["id"],
        name=r["name"],
        description=r["description"] or "",
        num_zones=r["num_zones"],
        num_blocks=r["num_blocks"],
        num_containers=r["num_containers"],
        created_at=r["created_at"]
    ) for r in rows]


def get_project_by_id(project_id: int) -> Optional[Project]:
    """Returns a single project by ID, or None if not found."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    conn.close()
    if row is None:
        return None
    return Project(
        id=row["id"], name=row["name"], description=row["description"] or "",
        num_zones=row["num_zones"], num_blocks=row["num_blocks"],
        num_containers=row["num_containers"], created_at=row["created_at"]
    )


def delete_project(project_id: int):
    """Deletes a project and all its containers/logs (CASCADE)."""
    conn = get_connection()
    conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    conn.commit()
    conn.close()


# ──────────────────────────────────────────
#  CONTAINER OPERATIONS
# ──────────────────────────────────────────

def save_containers(containers: List[Container]):
    """
    Bulk-saves a list of containers for a project.
    Typically called right after project creation.
    """
    conn = get_connection()
    for c in containers:
        conn.execute("""
            INSERT INTO containers
                (project_id, zone_number, block_number, container_index, container_type, serial_number)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (c.project_id, c.zone_number, c.block_number,
              c.container_index, c.container_type, c.serial_number))
    conn.commit()
    conn.close()


def get_containers_for_project(project_id: int) -> List[Container]:
    """Returns all containers belonging to a project."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM containers
        WHERE project_id = ?
        ORDER BY zone_number, block_number, container_index
    """, (project_id,)).fetchall()
    conn.close()
    return [Container(
        id=r["id"],
        project_id=r["project_id"],
        zone_number=r["zone_number"],
        block_number=r["block_number"],
        container_index=r["container_index"],
        container_type=r["container_type"],
        serial_number=r["serial_number"] or ""
    ) for r in rows]


def get_zones_for_project(project_id: int) -> List[int]:
    """Returns sorted list of unique zone numbers for a project."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT DISTINCT zone_number FROM containers
        WHERE project_id = ?
        ORDER BY zone_number
    """, (project_id,)).fetchall()
    conn.close()
    return [r["zone_number"] for r in rows]


def get_blocks_for_zone(project_id: int, zone_number: int) -> List[int]:
    """Returns sorted list of block numbers within a zone."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT DISTINCT block_number FROM containers
        WHERE project_id = ? AND zone_number = ?
        ORDER BY block_number
    """, (project_id, zone_number)).fetchall()
    conn.close()
    return [r["block_number"] for r in rows]


def get_containers_for_block(project_id: int, zone_number: int, block_number: int) -> List[Container]:
    """Returns all containers within a specific zone/block."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM containers
        WHERE project_id = ? AND zone_number = ? AND block_number = ?
        ORDER BY container_index
    """, (project_id, zone_number, block_number)).fetchall()
    conn.close()
    return [Container(
        id=r["id"],
        project_id=r["project_id"],
        zone_number=r["zone_number"],
        block_number=r["block_number"],
        container_index=r["container_index"],
        container_type=r["container_type"],
        serial_number=r["serial_number"] or ""
    ) for r in rows]
