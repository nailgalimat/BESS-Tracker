"""
services/worklog_entry_service.py
-----------------------------------
CRUD for work_log_entries, work_log_tags.
Images are handled separately in image_service.py.

Notes:
- All IDs are UUID4 strings (TEXT in SQLite) — safe for future sync.
- Soft-delete: deleted_at is set; rows are never hard-deleted from here.
- updated_at / version are bumped on every update.
"""

import uuid
from datetime import datetime
from typing import Optional, List

import pandas as pd
from database.db_manager import get_connection


# ── Constants ─────────────────────────────────────────────────────────────────

CATEGORIES = [
    "maintenance",
    "fault",
    "inspection",
    "commissioning",
    "repair",
    "other",
]

CATEGORY_LABELS = {
    "maintenance":   "🔧 Maintenance",
    "fault":         "⚡ Fault",
    "inspection":    "🔍 Inspection",
    "commissioning": "🚀 Commissioning",
    "repair":        "🛠 Repair",
    "other":         "📝 Other",
}

CATEGORY_COLORS = {
    "maintenance":   ("#E3F2FD", "#1565C0"),   # bg, fg
    "fault":         ("#FFEBEE", "#C62828"),
    "inspection":    ("#E8F5E9", "#2E7D32"),
    "commissioning": ("#FFF8E1", "#F57F17"),
    "repair":        ("#EDE7F6", "#4527A0"),
    "other":         ("#F5F5F5", "#424242"),
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _new_id() -> str:
    return str(uuid.uuid4())


# ── Create ────────────────────────────────────────────────────────────────────

def save_worklog_entry(
    project_id: int,
    log_date: str,
    description: str,
    category: str = "maintenance",
    container_id: Optional[int] = None,
    equipment_serial: str = "",
    site_location: str = "",
    tags: Optional[List[str]] = None,
    fault_name: str = "",
    status: str = "",
    sap_ticket: str = "",
    spare_parts: str = "",
) -> str:
    """
    Insert a new work_log_entry.  Returns the new UUID string.
    The fault_name / status / sap_ticket / spare_parts fields are optional.
    """
    entry_id = _new_id()
    now = _now()
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO work_log_entries
                (id, project_id, container_id, equipment_serial,
                 site_location, category, description,
                 fault_name, status, sap_ticket, spare_parts, log_date,
                 created_at, updated_at, version, sync_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'local')
        """, (entry_id, project_id, container_id, equipment_serial,
              site_location, category, description,
              fault_name, status, sap_ticket, spare_parts, log_date, now, now))

        if tags:
            _save_tags_cursor(conn, entry_id, tags)

        conn.commit()
        return entry_id
    finally:
        conn.close()


def _save_tags_cursor(conn, entry_id: str, tags: List[str]):
    """Insert tags using an existing connection (no commit)."""
    conn.execute("DELETE FROM work_log_tags WHERE work_log_id=?", (entry_id,))
    for tag in set(t.strip().lower() for t in tags if t.strip()):
        conn.execute(
            "INSERT OR IGNORE INTO work_log_tags (work_log_id, tag) VALUES (?, ?)",
            (entry_id, tag)
        )


# ── Read ──────────────────────────────────────────────────────────────────────

def get_worklog_entries(
    project_id: Optional[int] = None,
    date_from:  Optional[str] = None,
    date_to:    Optional[str] = None,
    category:   Optional[str] = None,
    search_text: Optional[str] = None,
    include_deleted: bool = False,
) -> List[dict]:
    """
    Return entries joined with project + container names.
    By default, soft-deleted rows are excluded.
    """
    conn = get_connection()
    try:
        query = """
            SELECT
                e.id, e.log_date, e.category, e.description,
                e.fault_name, e.status, e.sap_ticket, e.spare_parts,
                e.equipment_serial, e.site_location,
                e.created_at, e.updated_at, e.sync_status,
                e.version, e.deleted_at,
                COALESCE(p.name, '(Mobile)') AS project_name,
                e.project_id, e.container_id,
                c.zone_number, c.block_number, c.container_index,
                c.container_type, c.serial_number
            FROM work_log_entries e
            LEFT JOIN projects p  ON e.project_id   = p.id
            LEFT JOIN containers c ON e.container_id = c.id
            WHERE 1=1
        """
        params: list = []

        if not include_deleted:
            query += " AND e.deleted_at IS NULL"
        if project_id is not None:
            # Include mobile entries (project_id IS NULL) alongside project entries
            query += " AND (e.project_id = ? OR e.project_id IS NULL)"
            params.append(project_id)
        if date_from:
            query += " AND e.log_date >= ?"
            params.append(date_from)
        if date_to:
            query += " AND e.log_date <= ?"
            params.append(date_to)
        if category:
            query += " AND e.category = ?"
            params.append(category)
        if search_text:
            query += " AND (e.description LIKE ? OR e.equipment_serial LIKE ? OR e.site_location LIKE ?)"
            like = f"%{search_text}%"
            params.extend([like, like, like])

        query += " ORDER BY e.log_date DESC, e.created_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_worklog_entry(entry_id: str) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT
                e.*, COALESCE(p.name, '(Mobile)') AS project_name,
                c.zone_number, c.block_number, c.container_index,
                c.container_type, c.serial_number
            FROM work_log_entries e
            LEFT JOIN projects p ON e.project_id = p.id
            LEFT JOIN containers c ON e.container_id = c.id
            WHERE e.id = ?
        """, (entry_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ── Update ────────────────────────────────────────────────────────────────────

def update_worklog_entry(entry_id: str, tags: Optional[List[str]] = None, **fields):
    """
    Update allowed fields and queue the row for sync.
    Pass tags=[] to clear, tags=None to leave unchanged.

    `version` is NOT bumped here. It is the server version this row was last
    based on, and only the server moves it (sync_client stores it after a
    push). Bumping it locally made every desktop edit arrive "ahead" of the
    server, which skipped it — so edits to synced entries never reached the
    server or the phones, and the row re-sent itself every minute.
    """
    allowed = {
        "log_date", "category", "description",
        "equipment_serial", "site_location", "container_id", "project_id",
        "fault_name", "status", "sap_ticket", "spare_parts",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    now = _now()
    updates["updated_at"] = now

    set_clause = ", ".join(f"{k}=?" for k in updates)
    # Re-queue for sync so desktop edits propagate to the server (unless the
    # row is still a never-synced local draft, which is already pending).
    set_clause += ", sync_status = CASE WHEN sync_status='local' " \
                  "THEN 'local' ELSE 'pending' END"

    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE work_log_entries SET {set_clause} WHERE id=?",
            list(updates.values()) + [entry_id]
        )
        if tags is not None:
            _save_tags_cursor(conn, entry_id, tags)
        conn.commit()
    finally:
        conn.close()


# ── Delete (soft) ─────────────────────────────────────────────────────────────

def delete_worklog_entry(entry_id: str):
    """Soft-delete: sets deleted_at and queues the delete for sync.

    It used to bump the version and leave sync_status alone, so a delete made
    on the desktop was never sent: the entry lived on on the server and the
    phones, and came back on the next full pull."""
    conn = get_connection()
    try:
        conn.execute("""
            UPDATE work_log_entries
            SET deleted_at = ?, updated_at = ?,
                sync_status = CASE WHEN sync_status='local' THEN 'local'
                                   ELSE 'pending' END
            WHERE id = ?
        """, (_now(), _now(), entry_id))
        conn.commit()
    finally:
        conn.close()


# ── Tags ──────────────────────────────────────────────────────────────────────

def get_tags(entry_id: str) -> List[str]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT tag FROM work_log_tags WHERE work_log_id=? ORDER BY tag",
            (entry_id,)
        ).fetchall()
        return [r["tag"] for r in rows]
    finally:
        conn.close()


def add_tag(entry_id: str, tag: str):
    tag = tag.strip().lower()
    if not tag:
        return
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO work_log_tags (work_log_id, tag) VALUES (?, ?)",
            (entry_id, tag)
        )
        conn.commit()
    finally:
        conn.close()


def remove_tag(entry_id: str, tag: str):
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM work_log_tags WHERE work_log_id=? AND tag=?",
            (entry_id, tag)
        )
        conn.commit()
    finally:
        conn.close()


# ── DataFrame / export ────────────────────────────────────────────────────────

def get_worklog_entries_dataframe(
    project_id=None, date_from=None, date_to=None, category=None
) -> pd.DataFrame:
    rows = get_worklog_entries(
        project_id=project_id, date_from=date_from,
        date_to=date_to, category=category
    )
    cols = ["ID", "Date", "Project", "Zone", "Block", "Container",
            "Type", "Serial", "Category", "Fault", "Description",
            "Status", "SAP Ticket", "Spare Parts", "Location", "Created"]
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows).rename(columns={
        "id":              "ID",
        "log_date":        "Date",
        "project_name":    "Project",
        "zone_number":     "Zone",
        "block_number":    "Block",
        "container_index": "Container",
        "container_type":  "Type",
        "serial_number":   "Serial",
        "category":        "Category",
        "fault_name":      "Fault",
        "description":     "Description",
        "status":          "Status",
        "sap_ticket":      "SAP Ticket",
        "spare_parts":     "Spare Parts",
        "site_location":   "Location",
        "created_at":      "Created",
    })
    return df[[c for c in cols if c in df.columns]]
