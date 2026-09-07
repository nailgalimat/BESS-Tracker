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
