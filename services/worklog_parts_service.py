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

A deduction larger than the stock on hand is REFUSED, not clamped: stock_service
records the full quantity in stock_transactions while `_adjust_stock` floors
the item at 0, so writing off 3 of 1 and then reversing it would leave 3 in the
warehouse — the books would no longer reconcile. The office receives the stock
(an IN on the Spare parts page) or lowers the quantity first; either way the
warehouse and its transaction trail keep explaining each other.
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

def plan_deduction(work_log_id: str) -> dict:
    """What a "write off from stock" on this entry would do, before it does it.

    Returns {'warehouse', 'pending', 'ok', 'short'}:
      warehouse  the resolved target {'id', 'name', 'project_id'}
      pending    the rows that would be written off
      ok         the subset that fits in the stock on hand
      short      one row per material that does NOT fit, as
                 {'material_number', 'unit', 'wanted', 'available', 'short'}

    The shortfall is summed **per material over the whole entry**: two rows of
    2 fuses against 3 in stock are short by 1, even though each row on its own
    looks affordable. So the confirmation can say it before anything moves.
    """
    parts = get_parts_for_entry(work_log_id)
    pending = [p for p in parts if not p["deducted"]]
    wh = resolve_warehouse_for_entry(work_log_id)

    want = {}
    for p in pending:
        w = want.setdefault(p["material_number"], {
            "material_number": p["material_number"],
            "unit": p.get("unit") or "",
            "wanted": 0.0,
            "available": float(p.get("available") or 0.0)})
        w["wanted"] += float(p["quantity"])
    short = [dict(w, short=w["wanted"] - w["available"])
             for w in want.values() if w["wanted"] > w["available"] + 1e-9]
    short_mats = {s["material_number"] for s in short}
    return {"warehouse": wh, "pending": pending, "short": short,
            "ok": [p for p in pending if p["material_number"] not in short_mats]}


def deduct_part(part_id: int, log_date: Optional[str] = None) -> dict:
    """
    Record an OUT stock transaction for one pending part and mark it deducted.
    Returns {'ok', 'message'}.

    Refused when the warehouse does not hold that much — see the module
    docstring: a clamped OUT cannot be reversed back to where it started.
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
    stock = get_stock_quantity(wh["id"], p["material_number"])
    available = float(stock["quantity"]) if stock else 0.0
    if float(p["quantity"]) > available + 1e-9:
        return {"ok": False, "available": available,
                "short": float(p["quantity"]) - available,
                "message": f"Not enough in '{wh['name']}': "
                           f"{p['quantity']:g} asked, {available:g} in stock "
                           f"(short {float(p['quantity']) - available:g}). "
                           f"Book the delivery in on Spare parts, or lower the "
                           f"quantity — nothing was written off."}
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


def counts_for_entries(work_log_ids) -> dict:
    """{work_log_id: {'total', 'pending', 'deducted'}} for many entries in one
    query — the Work list needs a badge per row and must not ask per row.
    Entries with no parts are simply absent from the result.
    """
    ids = [str(i) for i in work_log_ids if i]
    out = {}
    if not ids:
        return out
    conn = get_connection()
    try:
        for i in range(0, len(ids), 400):        # stay under SQLite's var limit
            chunk = ids[i:i + 400]
            rows = conn.execute("""
                SELECT work_log_id,
                       COUNT(*) AS total,
                       SUM(CASE WHEN deducted=0 THEN 1 ELSE 0 END) AS pending,
                       SUM(CASE WHEN deducted=1 THEN 1 ELSE 0 END) AS deducted
                FROM worklog_spare_parts
                WHERE work_log_id IN ({})
                GROUP BY work_log_id
            """.format(",".join("?" * len(chunk))), chunk).fetchall()
            for r in rows:
                out[r["work_log_id"]] = {"total": r["total"] or 0,
                                         "pending": r["pending"] or 0,
                                         "deducted": r["deducted"] or 0}
    finally:
        conn.close()
    return out


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
