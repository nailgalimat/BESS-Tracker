"""
services/worklog_parts_service.py
----------------------------------
Structured spare parts attached to a Field Log entry (work_log_entries),
with an explicit link to the spare-parts stock (warehouses / stock_transactions).

Design (user decision: deduction is a SEPARATE, explicit confirmation):
  - Engineers pick material + quantity rows on an entry  → stored here, deducted=0
  - A dedicated "Deduct from stock" action records an OUT stock transaction
    from the entry's project warehouse (or the main warehouse if the entry has
    no project) and flips the row to deducted=1, remembering the tx id.
  - A row can be "reversed" (records an IN to restore the stock) before it is
    removed, so the warehouse is never left inconsistent.

The free-text work_log_entries.spare_parts column is kept as a quick human
summary and still syncs to mobile; this structured table is desktop/office-only
(stock management happens in the office, not in the field).
"""

from datetime import datetime
from typing import List, Optional

from database.db_manager import get_connection
from services.stock_service import (
    record_transaction, get_stock_quantity, ensure_project_warehouse,
    get_main_warehouse,
)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── Warehouse resolution ──────────────────────────────────────────────────────

def resolve_warehouse_for_entry(work_log_id: str) -> dict:
    """
    Which warehouse should this entry's parts be deducted from?
      - entry has a project      → that project's warehouse (auto-created)
      - entry has no project      → the main/central warehouse
    Returns {'id', 'name', 'project_id'}.
    """
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT e.project_id, p.name AS project_name
            FROM work_log_entries e
            LEFT JOIN projects p ON e.project_id = p.id
            WHERE e.id = ?
        """, (work_log_id,)).fetchone()
    finally:
        conn.close()

    if row and row["project_id"] is not None:
        wh_id = ensure_project_warehouse(row["project_id"], row["project_name"] or "Project")
        conn = get_connection()
        try:
            w = conn.execute("SELECT id, name, project_id FROM warehouses WHERE id=?",
                             (wh_id,)).fetchone()
            if w:
                return {"id": w["id"], "name": w["name"], "project_id": w["project_id"]}
        finally:
            conn.close()

    main = get_main_warehouse()
    return {"id": main["id"], "name": main.get("name", "Main Warehouse"),
            "project_id": None}


# ── Create / read / update / delete rows ──────────────────────────────────────

def add_part(work_log_id: str, material_number: str, quantity: float,
             unit: str = "", description: str = "") -> int:
    """Attach a spare-part row to an entry (pending, not yet deducted)."""
    material_number = (material_number or "").strip()
    if not material_number:
        raise ValueError("Material number is required.")
    quantity = float(quantity)

    # Fill unit/description from the materials catalog if not supplied
    if not unit or not description:
        conn = get_connection()
        try:
            m = conn.execute(
                "SELECT description, unit FROM materials WHERE material_number=?",
                (material_number,)
            ).fetchone()
        finally:
            conn.close()
        if m:
            description = description or (m["description"] or "")
            unit = unit or (m["unit"] or "")

    conn = get_connection()
    try:
        cur = conn.execute("""
            INSERT INTO worklog_spare_parts
                (work_log_id, material_number, description, quantity, unit,
                 deducted, created_at)
            VALUES (?, ?, ?, ?, ?, 0, ?)
        """, (work_log_id, material_number, description, quantity, unit, _now()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_parts_for_entry(work_log_id: str) -> List[dict]:
    """
    All structured parts for an entry, enriched with the current available
    stock in the entry's resolved warehouse (so the UI can warn on shortfall).
    """
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT sp.*, COALESCE(w.name, '') AS warehouse_name
            FROM worklog_spare_parts sp
            LEFT JOIN warehouses w ON sp.warehouse_id = w.id
            WHERE sp.work_log_id = ?
            ORDER BY sp.id
        """, (work_log_id,)).fetchall()
        parts = [dict(r) for r in rows]
    finally:
        conn.close()

    if not parts:
        return parts

    wh = resolve_warehouse_for_entry(work_log_id)
    for p in parts:
        stock = get_stock_quantity(wh["id"], p["material_number"])
        p["available"] = stock["quantity"] if stock else 0.0
        p["target_warehouse_id"]   = wh["id"]
        p["target_warehouse_name"] = wh["name"]
    return parts


def update_part(part_id: int, **fields):
    """Update material_number / quantity / unit / description on a PENDING row."""
    allowed = {"material_number", "quantity", "unit", "description"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT deducted FROM worklog_spare_parts WHERE id=?", (part_id,)
        ).fetchone()
        if row and row["deducted"]:
            raise ValueError("This part is already deducted from stock — reverse it first.")
        set_clause = ", ".join(f"{k}=?" for k in updates)
        conn.execute(
            f"UPDATE worklog_spare_parts SET {set_clause} WHERE id=?",
            list(updates.values()) + [part_id]
        )
        conn.commit()
    finally:
        conn.close()


def delete_part(part_id: int):
    """Remove a PENDING part row. Deducted rows must be reversed first."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT deducted FROM worklog_spare_parts WHERE id=?", (part_id,)
        ).fetchone()
        if row and row["deducted"]:
            raise ValueError("This part is already deducted from stock — reverse it first.")
        conn.execute("DELETE FROM worklog_spare_parts WHERE id=?", (part_id,))
        conn.commit()
    finally:
        conn.close()


# ── Deduct / reverse against stock ────────────────────────────────────────────

def deduct_part(part_id: int, log_date: Optional[str] = None) -> dict:
    """
    Record an OUT stock transaction for one pending part and mark it deducted.
    Returns {'ok', 'message'}.
    """
    conn = get_connection()
    try:
        p = conn.execute("""
            SELECT sp.*, e.project_id, e.log_date
            FROM worklog_spare_parts sp
            JOIN work_log_entries e ON sp.work_log_id = e.id
            WHERE sp.id = ?
        """, (part_id,)).fetchone()
    finally:
        conn.close()

    if not p:
        return {"ok": False, "message": "Part not found."}
    if p["deducted"]:
        return {"ok": False, "message": "Already deducted."}

    wh = resolve_warehouse_for_entry(p["work_log_id"])
    tx_date = log_date or p["log_date"] or datetime.now().strftime("%Y-%m-%d")

    tx_id = record_transaction(
        warehouse_id     = wh["id"],
        material_number  = p["material_number"],
        transaction_type = "OUT",
        quantity         = p["quantity"],
        transaction_date = tx_date,
        project_id       = p["project_id"],
        reference        = f"LOG-{p['work_log_id'][:8]}",
        notes            = f"Field Log spare part: {p['description'] or p['material_number']}",
    )

    conn = get_connection()
    try:
        conn.execute("""
            UPDATE worklog_spare_parts
            SET deducted=1, warehouse_id=?, tx_id=?, deducted_at=?
            WHERE id=?
        """, (wh["id"], tx_id, _now(), part_id))
        conn.commit()
    finally:
        conn.close()

    return {"ok": True,
            "message": f"Deducted {p['quantity']:g} x {p['material_number']} "
                       f"from '{wh['name']}'."}


def deduct_all_pending(work_log_id: str) -> dict:
    """Deduct every pending part on an entry. Returns a summary dict."""
    parts = get_parts_for_entry(work_log_id)
    pending = [p for p in parts if not p["deducted"]]
    done, failed = 0, []
    for p in pending:
        res = deduct_part(p["id"])
        if res["ok"]:
            done += 1
        else:
            failed.append(f"{p['material_number']}: {res['message']}")
    return {"deducted": done, "failed": failed, "total_pending": len(pending)}


def reverse_part(part_id: int) -> dict:
    """
    Undo a deduction: record an IN transaction to restore the stock and flip
    the row back to pending. Used before deleting a deducted row or to fix a
    mistake.
    """
    conn = get_connection()
    try:
        p = conn.execute("""
            SELECT sp.*, e.project_id, e.log_date
            FROM worklog_spare_parts sp
            JOIN work_log_entries e ON sp.work_log_id = e.id
            WHERE sp.id = ?
        """, (part_id,)).fetchone()
    finally:
        conn.close()

    if not p:
        return {"ok": False, "message": "Part not found."}
    if not p["deducted"]:
        return {"ok": False, "message": "Part is not deducted yet."}

    wh_id = p["warehouse_id"]
    tx_date = p["log_date"] or datetime.now().strftime("%Y-%m-%d")
    record_transaction(
        warehouse_id     = wh_id,
        material_number  = p["material_number"],
        transaction_type = "IN",
        quantity         = p["quantity"],
        transaction_date = tx_date,
        project_id       = p["project_id"],
        reference        = f"LOG-{p['work_log_id'][:8]}",
        notes            = "Reverse Field Log spare-part deduction",
    )

    conn = get_connection()
    try:
        conn.execute("""
            UPDATE worklog_spare_parts
            SET deducted=0, tx_id=NULL, deducted_at=NULL
            WHERE id=?
        """, (part_id,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "message": "Deduction reversed, stock restored."}


# ── Small helpers for the UI / cards ──────────────────────────────────────────

def get_pending_count(work_log_id: str) -> int:
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT COUNT(*) AS n FROM worklog_spare_parts
            WHERE work_log_id=? AND deducted=0
        """, (work_log_id,)).fetchone()
        return row["n"] if row else 0
    finally:
        conn.close()


def get_parts_count(work_log_id: str) -> dict:
    """Returns {'total', 'pending', 'deducted'} for badge display."""
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN deducted=0 THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN deducted=1 THEN 1 ELSE 0 END) AS deducted
            FROM worklog_spare_parts WHERE work_log_id=?
        """, (work_log_id,)).fetchone()
        return {"total": row["total"] or 0,
                "pending": row["pending"] or 0,
                "deducted": row["deducted"] or 0}
    finally:
        conn.close()
