"""
services/log_service.py
------------------------
Database operations for daily maintenance logs.

Stock integration:
  Each log entry can reference a warehouse (warehouse_id).
  - save:   records an OUT stock transaction and deducts the quantity
  - delete: records a reversing IN transaction and restores the quantity
  - update: reverses the old consumption and applies the new one
  All stock changes happen in the same DB transaction as the log change.
"""

from database.db_manager import get_connection
from models.models import LogEntry
from services.project_service import zone_block_to_plant
from services.stock_service import _adjust_stock
from typing import List, Optional


def _record_stock_out(conn, warehouse_id: int, material_number: str,
                      quantity: float, date: str, project_id: int,
                      reference: str, notes: str = ""):
    """OUT transaction + stock deduction on an open connection."""
    conn.execute("""
        INSERT INTO stock_transactions
            (warehouse_id, material_number, transaction_type,
             quantity, project_id, reference, notes, date)
        VALUES (?, ?, 'OUT', ?, ?, ?, ?, ?)
    """, (warehouse_id, material_number, quantity,
          project_id, reference, notes, date))
    _adjust_stock(conn, warehouse_id, material_number, -quantity)


def _record_stock_in(conn, warehouse_id: int, material_number: str,
                     quantity: float, date: str, project_id: int,
                     reference: str, notes: str = ""):
    """IN transaction + stock restore on an open connection."""
    conn.execute("""
        INSERT INTO stock_transactions
            (warehouse_id, material_number, transaction_type,
             quantity, project_id, reference, notes, date)
        VALUES (?, ?, 'IN', ?, ?, ?, ?, ?)
    """, (warehouse_id, material_number, quantity,
          project_id, reference, notes, date))
    _adjust_stock(conn, warehouse_id, material_number, quantity)


def save_log_entry(entry: LogEntry) -> int:
    """
    Saves a new daily log entry. Returns the new entry's ID.
    If entry.warehouse_id is set, deducts the quantity from that warehouse
    and logs an OUT transaction in the same DB transaction.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO daily_logs (project_id, container_id, date,
                material_number, quantity, comment, warehouse_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (entry.project_id, entry.container_id, entry.date,
              entry.material_number, entry.quantity, entry.comment,
              entry.warehouse_id))
        entry_id = cursor.lastrowid

        if entry.warehouse_id:
            _record_stock_out(
                conn, entry.warehouse_id, entry.material_number,
                entry.quantity, entry.date, entry.project_id,
                reference=f"LOG-{entry_id}",
                notes="Daily log consumption",
            )

        conn.commit()
        return entry_id
    finally:
        conn.close()


def update_log_entry(log_id: int, data: dict):
    """
    Updates date / material_number / quantity / comment / sap_ticket
    of an existing entry.

    If the entry is linked to a warehouse and the material or quantity
    changed, the old consumption is reversed (IN) and the new one
    applied (OUT) so stock levels stay correct.
    """
    conn = get_connection()
    try:
        old = conn.execute(
            "SELECT * FROM daily_logs WHERE id=?", (log_id,)
        ).fetchone()
        if not old:
            return
        old = dict(old)

        conn.execute("""
            UPDATE daily_logs
            SET date=?, material_number=?, quantity=?,
                comment=?, sap_ticket=?
            WHERE id=?
        """, (data["date"], data["material_number"], data["quantity"],
              data["comment"], data.get("sap_ticket", ""), log_id))

        wh_id = old.get("warehouse_id")
        material_changed = old["material_number"] != data["material_number"]
        quantity_changed = float(old["quantity"]) != float(data["quantity"])

        if wh_id and (material_changed or quantity_changed):
            _record_stock_in(
                conn, wh_id, old["material_number"], old["quantity"],
                data["date"], old["project_id"],
                reference=f"LOG-{log_id}",
                notes="Log entry edited — old consumption reversed",
            )
            _record_stock_out(
                conn, wh_id, data["material_number"], data["quantity"],
                data["date"], old["project_id"],
                reference=f"LOG-{log_id}",
                notes="Log entry edited — new consumption",
            )

        conn.commit()
    finally:
        conn.close()


def get_log_entries(
    project_id: Optional[int] = None,
    date_from: Optional[str] = None,    # 'YYYY-MM-DD'
    date_to: Optional[str] = None,      # 'YYYY-MM-DD'
    zone_number: Optional[int] = None,
    block_number: Optional[int] = None,
    container_id: Optional[int] = None,
) -> List[dict]:
    """
    Fetches log entries with optional filters.
    Returns a list of dicts (easy to pass to pandas DataFrame).
    Joins with containers table to get zone/block/type/serial info.
    """
    conn = get_connection()

    # Build query dynamically based on which filters are provided
    query = """
        SELECT
            dl.id,
            p.name          AS project_name,
            dl.date,
            c.zone_number,
            c.block_number,
            c.container_index,
            c.container_type,
            c.serial_number,
            dl.material_number,
            dl.quantity,
            dl.warehouse_id,
            COALESCE(w.name, '') AS warehouse_name,
            dl.comment,
            dl.created_at
        FROM daily_logs dl
        JOIN containers c ON dl.container_id = c.id
        JOIN projects   p ON dl.project_id   = p.id
        LEFT JOIN warehouses w ON dl.warehouse_id = w.id
        WHERE 1=1
    """
    params = []

    if project_id is not None:
        query += " AND dl.project_id = ?"
        params.append(project_id)

    if date_from:
        query += " AND dl.date >= ?"
        params.append(date_from)

    if date_to:
        query += " AND dl.date <= ?"
        params.append(date_to)

    if zone_number is not None:
        query += " AND c.zone_number = ?"
        params.append(zone_number)

    if block_number is not None:
        query += " AND c.block_number = ?"
        params.append(block_number)

    if container_id is not None:
        query += " AND dl.container_id = ?"
        params.append(container_id)

    query += " ORDER BY dl.date DESC, dl.created_at DESC"

    rows = conn.execute(query, params).fetchall()
    conn.close()

    # Convert sqlite3.Row objects to plain dicts
    return [dict(r) for r in rows]


# ── The old Daily Log, read-only ─────────────────────────────────────────────
# `daily_logs` is not a work report despite the name: one row is **one material
# issued** against one container, with a free-text comment describing the work.
# The redesign stopped writing it, and the rows that are there became invisible.
# They are history — the Work page and the Spare parts page both show them, both
# read-only, and both through this one function so the two views can never
# disagree. Nothing here writes, and no stock movement is created or reversed:
# whatever `save_log_entry` booked at the time already stands.

# What the container type is called on a node ("Block 47 · PCS"). A block holds
# one LC cabinet, one PCS/converter and four batteries; the battery's number is
# its ordinal among that block's batteries in container_index order, which is
# exactly how work_journal_service.container_for_node reads it back. The LC of a
# battery or PCS row is not recorded, so it is left empty rather than guessed.
_DEVICE_BY_TYPE = {'Battery': 'BESS', 'PCS / Converter': 'PCS',
                   'LC Cabinet': 'LC cabinet'}


def _device_label(container_type: str, unit_no) -> str:
    name = _DEVICE_BY_TYPE.get((container_type or '').strip())
    if name is None:
        return (container_type or '').strip()
    if name == 'BESS' and unit_no:
        return f'BESS {int(unit_no)}'
    return name


def consumption_history(project_id: int, date_from: Optional[str] = None,
                        date_to: Optional[str] = None,
                        material_number: Optional[str] = None,
                        warehouse_id: Optional[int] = None,
                        text: Optional[str] = None) -> List[dict]:
    """The project's old Daily Log rows, newest first. **Read-only.**

    Each row: id, date, material_number, description, quantity, comment,
    sap_ticket, container_id, zone_number, block_number, container_type,
    unit_no, device, serial_number, plant_block, warehouse_id, warehouse,
    created_at, project_id.

    `plant_block` is translated from the container's (zone, local block) pair
    through the project's block map — the only thing that knows zone 6 block 8
    is plant block 47. A row whose container is gone keeps `plant_block` None:
    the block is unknown, and an unknown block is not guessed.
    """
    conn = get_connection()
    try:
        q = """
            SELECT dl.id, dl.project_id, dl.container_id, dl.date,
                   dl.material_number, dl.quantity, dl.comment, dl.created_at,
                   dl.sap_ticket, dl.warehouse_id,
                   c.zone_number, c.block_number, c.container_index,
                   c.container_type, c.serial_number,
                   (SELECT COUNT(*) FROM containers c2
                     WHERE c2.project_id     = c.project_id
                       AND c2.zone_number    = c.zone_number
                       AND c2.block_number   = c.block_number
                       AND c2.container_type = c.container_type
                       AND c2.container_index <= c.container_index) AS unit_no,
                   COALESCE(w.name, '')        AS warehouse,
                   COALESCE(m.description, '') AS description
            FROM daily_logs dl
            LEFT JOIN containers c ON dl.container_id   = c.id
            LEFT JOIN warehouses w ON dl.warehouse_id   = w.id
            LEFT JOIN materials  m ON dl.material_number = m.material_number
            WHERE dl.project_id = ?
        """
        p = [project_id]
        if date_from:
            q += " AND dl.date >= ?"; p.append(date_from)
        if date_to:
            q += " AND dl.date <= ?"; p.append(date_to)
        if material_number:
            q += " AND dl.material_number = ?"; p.append(material_number)
        if warehouse_id is not None:
            q += " AND dl.warehouse_id = ?"; p.append(warehouse_id)
        if text:
            q += (" AND (dl.comment LIKE ? OR dl.material_number LIKE ?"
                  " OR m.description LIKE ? OR dl.sap_ticket LIKE ?)")
            p += ['%' + text + '%'] * 4
        q += " ORDER BY dl.date DESC, dl.created_at DESC, dl.id DESC"
        rows = [dict(r) for r in conn.execute(q, p).fetchall()]
    finally:
        conn.close()
    for r in rows:
        r['plant_block'] = zone_block_to_plant(
            project_id, r.get('zone_number'), r.get('block_number'))
        r['device'] = _device_label(r.get('container_type'), r.get('unit_no'))
    return rows


def delete_log_entry(entry_id: int):
    """
    Deletes a single log entry by ID.
    If the entry deducted stock from a warehouse, the quantity is
    returned to stock with a reversing IN transaction.
    """
    conn = get_connection()
    try:
        old = conn.execute(
            "SELECT * FROM daily_logs WHERE id=?", (entry_id,)
        ).fetchone()
        if not old:
            return
        old = dict(old)

        conn.execute("DELETE FROM daily_logs WHERE id = ?", (entry_id,))

        if old.get("warehouse_id"):
            _record_stock_in(
                conn, old["warehouse_id"], old["material_number"],
                old["quantity"], old["date"], old["project_id"],
                reference=f"LOG-{entry_id}",
                notes="Log entry deleted — stock returned",
            )

        conn.commit()
    finally:
        conn.close()
