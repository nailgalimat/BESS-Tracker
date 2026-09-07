"""
services/lifecycle_service.py
------------------------------
Queries for the Container Lifecycle Report.

This service fetches the complete history of a container — every log entry
ever recorded for it — identified either by serial number or container ID.

We JOIN across all relevant tables so the UI gets one flat result set.

ASSUMPTION: Your daily_logs table has a column 'sap_ticket' (TEXT, nullable).
If yours is in a separate sap_tickets table, see the note at the bottom.
"""

from database.db_manager import get_connection
from typing import Optional, List


def get_lifecycle_by_serial(
    serial_number: str,
    date_from: Optional[str] = None,   # 'YYYY-MM-DD'
    date_to: Optional[str] = None,
    material_number: Optional[str] = None,
    sap_ticket: Optional[str] = None,
) -> List[dict]:
    """
    Returns full lifecycle history for a container identified by serial number.
    Sorted chronologically (oldest first).

    Args:
        serial_number:   The container's serial number (partial match supported)
        date_from:       Filter logs on or after this date
        date_to:         Filter logs on or before this date
        material_number: Filter by specific material (partial match)
        sap_ticket:      Filter by SAP ticket number (partial match)

    Returns:
        List of dicts — one per log entry
    """
    conn = get_connection()

    # We use LIKE for serial number so partial searches work (e.g. "SN-001")
    query = """
        SELECT
            dl.id                   AS log_id,
            dl.date                 AS date,
            p.name                  AS project,
            c.zone_number           AS zone,
            c.block_number          AS block,
            c.container_index       AS container_num,
            c.container_type        AS container_type,
            c.serial_number         AS serial_number,
            dl.material_number      AS material_number,
            COALESCE(m.description, '') AS material_description,
            dl.quantity             AS quantity,
            COALESCE(dl.comment, '') AS comment,
            COALESCE(dl.sap_ticket, '') AS sap_ticket,
            dl.created_at           AS recorded_at
        FROM daily_logs dl
        JOIN containers c  ON dl.container_id = c.id
        JOIN projects   p  ON dl.project_id   = p.id
        LEFT JOIN materials m ON dl.material_number = m.material_number
        WHERE c.serial_number LIKE ?
    """
    params = [f"%{serial_number}%"]

    if date_from:
        query += " AND dl.date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND dl.date <= ?"
        params.append(date_to)
    if material_number:
        query += " AND dl.material_number LIKE ?"
        params.append(f"%{material_number}%")
    if sap_ticket:
        query += " AND dl.sap_ticket LIKE ?"
        params.append(f"%{sap_ticket}%")

    # Chronological order — oldest first
    query += " ORDER BY dl.date ASC, dl.created_at ASC"

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_lifecycle_by_container_id(
    container_id: int,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    material_number: Optional[str] = None,
    sap_ticket: Optional[str] = None,
) -> List[dict]:
    """
    Same as get_lifecycle_by_serial but looks up by exact container ID.
    Used when the user selects Project → Zone → Block → Container in the UI.
    """
    conn = get_connection()

    query = """
        SELECT
            dl.id                   AS log_id,
            dl.date                 AS date,
            p.name                  AS project,
            c.zone_number           AS zone,
            c.block_number          AS block,
            c.container_index       AS container_num,
            c.container_type        AS container_type,
            c.serial_number         AS serial_number,
            dl.material_number      AS material_number,
            COALESCE(m.description, '') AS material_description,
            dl.quantity             AS quantity,
            COALESCE(dl.comment, '') AS comment,
            COALESCE(dl.sap_ticket, '') AS sap_ticket,
            dl.created_at           AS recorded_at
        FROM daily_logs dl
        JOIN containers c  ON dl.container_id = c.id
        JOIN projects   p  ON dl.project_id   = p.id
        LEFT JOIN materials m ON dl.material_number = m.material_number
        WHERE dl.container_id = ?
    """
    params = [container_id]

    if date_from:
        query += " AND dl.date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND dl.date <= ?"
        params.append(date_to)
    if material_number:
        query += " AND dl.material_number LIKE ?"
        params.append(f"%{material_number}%")
    if sap_ticket:
        query += " AND dl.sap_ticket LIKE ?"
        params.append(f"%{sap_ticket}%")

    query += " ORDER BY dl.date ASC, dl.created_at ASC"

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_serial_numbers() -> List[str]:
    """
    Returns all unique serial numbers in the database.
    Used to populate autocomplete suggestions in the UI.
    """
    conn = get_connection()
    rows = conn.execute("""
        SELECT DISTINCT serial_number
        FROM containers
        WHERE serial_number IS NOT NULL AND serial_number != ''
        ORDER BY serial_number
    """).fetchall()
    conn.close()
    return [r["serial_number"] for r in rows]


# ──────────────────────────────────────────────────────────────────────────────
# NOTE: If your sap_ticket is in a SEPARATE table (e.g. sap_tickets), replace
# the COALESCE(dl.sap_ticket, '') line with a LEFT JOIN like this:
#
#   LEFT JOIN sap_tickets st ON st.log_id = dl.id
#
# and reference st.ticket_number in the SELECT.
# ──────────────────────────────────────────────────────────────────────────────
