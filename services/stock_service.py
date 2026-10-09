"""
services/stock_service.py
--------------------------
Spare parts stock management.

- Main warehouse (project_id=NULL, is_main=1) — shared across all projects
- Project warehouses (one auto-created per project)
- Stock items: quantity per material per warehouse
- Transactions: every IN/OUT/TRANSFER logged with audit trail
"""

import pandas as pd
from database.db_manager import get_connection
from services import material_service as _materials
from typing import Optional, List
from datetime import date as date_type


# ── WAREHOUSES ────────────────────────────────────────────────────────────────

def get_all_warehouses() -> List[dict]:
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT w.*, COALESCE(p.name,'—') AS project_name
            FROM warehouses w
            LEFT JOIN projects p ON w.project_id=p.id
            ORDER BY w.is_main DESC, w.name
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_main_warehouse() -> dict:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM warehouses WHERE is_main=1 LIMIT 1"
        ).fetchone()
        return dict(row) if row else {"id": 1, "name": "Main Warehouse"}
    finally:
        conn.close()


def create_warehouse(name: str, project_id: Optional[int] = None,
                     notes: str = "") -> int:
    """
    Creates a new warehouse. project_id=None makes a general warehouse
    (not bound to any project). Returns the new warehouse id.
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO warehouses (name, project_id, is_main, notes)
            VALUES (?, ?, 0, ?)
        """, (name.strip(), project_id, notes.strip()))
        wh_id = cur.lastrowid
        conn.commit()
        return wh_id
    finally:
        conn.close()


def rename_warehouse(warehouse_id: int, new_name: str):
    conn = get_connection()
    try:
        conn.execute("UPDATE warehouses SET name=? WHERE id=?",
                     (new_name.strip(), warehouse_id))
        conn.commit()
    finally:
        conn.close()


def get_project_warehouse_id(project_id: int) -> Optional[int]:
    """Returns the warehouse id bound to a project, or None if none exists yet."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM warehouses WHERE project_id=? ORDER BY id LIMIT 1",
            (project_id,)
        ).fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


def ensure_project_warehouse(project_id: int, project_name: str) -> int:
    """Creates a warehouse for a project if it doesn't exist. Returns warehouse_id."""
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id FROM warehouses WHERE project_id=?",
            (project_id,)
        ).fetchone()
        if existing:
            return existing["id"]
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO warehouses (name, project_id, is_main)
            VALUES (?, ?, 0)
        """, (f"{project_name} — Stock", project_id))
        wh_id = cur.lastrowid
        conn.commit()
        return wh_id
    finally:
        conn.close()


# ── STOCK ITEMS ───────────────────────────────────────────────────────────────

def get_stock(warehouse_id: int) -> List[dict]:
    """Returns all stock items for a warehouse with material descriptions."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT
                s.id, s.material_number, s.quantity, s.min_quantity, s.unit,
                COALESCE(m.description,'') AS description,
                CASE WHEN s.quantity <= s.min_quantity THEN 1 ELSE 0 END AS low_stock
            FROM stock_items s
            LEFT JOIN materials m ON m.material_number=s.material_number
            WHERE s.warehouse_id=?
            ORDER BY s.material_number
        """, (warehouse_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_stock_quantity(warehouse_id: int, material_number: str) -> Optional[dict]:
    """
    Returns {'quantity', 'unit'} for a material in a warehouse,
    or None if the material is not stocked there.
    """
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT quantity, unit FROM stock_items
            WHERE warehouse_id=? AND material_number=?
        """, (warehouse_id, material_number)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_stock_all_warehouses() -> List[dict]:
    """Returns stock across all warehouses — for the combined view."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT
                w.name AS warehouse,
                s.material_number,
                COALESCE(m.description,'') AS description,
                s.quantity, s.min_quantity, s.unit,
                CASE WHEN s.quantity <= s.min_quantity THEN 1 ELSE 0 END AS low_stock
            FROM stock_items s
            JOIN warehouses w ON s.warehouse_id=w.id
            LEFT JOIN materials m ON m.material_number=s.material_number
            ORDER BY w.is_main DESC, w.name, s.material_number
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def set_stock_level(warehouse_id: int, material_number: str,
                    quantity: float, min_quantity: float = 0,
                    unit: str = ""):
    """Sets (upserts) stock level for a material in a warehouse.

    The material number is upper-cased, as it is everywhere else the catalogue
    is written (save_material, ensure_stock_item, the Excel import). stock_items
    is unique on (warehouse_id, material_number), so a hand-typed '6sw00366'
    and an imported '6SW00366' would otherwise become two rows for one part
    with nothing to catch it.

    Note this writes all three fields: min_quantity defaults to 0, so a caller
    with no opinion about the alert threshold must not come through here — see
    ensure_stock_item / set_min_quantity.
    """
    material_number = material_number.strip().upper()
    conn = get_connection()
    try:
        existing = conn.execute("""
            SELECT id FROM stock_items
            WHERE warehouse_id=? AND material_number=?
        """, (warehouse_id, material_number)).fetchone()
        if existing:
            conn.execute("""
                UPDATE stock_items
                SET quantity=?, min_quantity=?, unit=?,
                    updated_at=datetime('now')
                WHERE id=?
            """, (quantity, min_quantity, unit, existing["id"]))
        else:
            conn.execute("""
                INSERT INTO stock_items
                    (warehouse_id, material_number, quantity, min_quantity, unit)
                VALUES (?, ?, ?, ?, ?)
            """, (warehouse_id, material_number, quantity, min_quantity, unit))
        conn.commit()
    finally:
        conn.close()


def ensure_stock_item(warehouse_id: int, material_number: str,
                      unit: str = "") -> bool:
    """Makes sure a stock item row exists, and records its unit of measure.

    Returns True when the row had to be created.

    Unlike set_stock_level this never writes `min_quantity` — that is the
    low-stock alert threshold the user set by hand, and set_stock_level's
    `min_quantity=0` default would zero it. An existing row also keeps its
    quantity (move that with record_transaction, so the audit trail explains
    it). A non-empty `unit` is stored; an empty one leaves the stored unit
    alone, because "the caller said nothing" is not "blank it".

    Used by the Excel stock import; safe for any caller that wants a row to
    exist without disturbing what the user configured on it.
    """
    material_number = material_number.strip().upper()
    unit = (unit or "").strip()
    conn = get_connection()
    try:
        existing = conn.execute("""
            SELECT id, unit FROM stock_items
            WHERE warehouse_id=? AND material_number=?
        """, (warehouse_id, material_number)).fetchone()
        if existing is None:
            conn.execute("""
                INSERT INTO stock_items
                    (warehouse_id, material_number, quantity, unit)
                VALUES (?, ?, 0, ?)
            """, (warehouse_id, material_number, unit))
            conn.commit()
            return True
        if unit and unit != (existing["unit"] or ""):
            conn.execute("""
                UPDATE stock_items SET unit=?, updated_at=datetime('now')
                WHERE id=?
            """, (unit, existing["id"]))
            conn.commit()
        return False
    finally:
        conn.close()


def set_min_quantity(warehouse_id: int, material_number: str,
                     min_quantity: float) -> bool:
    """Sets the low-stock alert threshold on a stock item — and nothing else.

    Returns True when the stored threshold actually changed, so a caller can
    count real changes rather than writes attempted.

    The counterpart to ensure_stock_item: that one refuses to touch
    min_quantity, this one touches only min_quantity and never the quantity on
    the shelf. Together they let a caller apply exactly the fields it has an
    opinion about — which is why the Excel import uses this pair instead of
    set_stock_level, which writes quantity, threshold and unit in one go and so
    cannot express "I have no opinion about the threshold".

    Does nothing (and returns False) when the material is not stocked in this
    warehouse yet; call ensure_stock_item first. A negative threshold is a
    caller error, not data to store.
    """
    if min_quantity is None:
        return False
    min_quantity = float(min_quantity)
    if min_quantity < 0:
        raise ValueError("min_quantity cannot be negative: {!r}".format(min_quantity))
    material_number = material_number.strip().upper()
    conn = get_connection()
    try:
        existing = conn.execute("""
            SELECT id, min_quantity FROM stock_items
            WHERE warehouse_id=? AND material_number=?
        """, (warehouse_id, material_number)).fetchone()
        if existing is None:
            return False
        if float(existing["min_quantity"] or 0) == min_quantity:
            return False
        conn.execute("""
            UPDATE stock_items SET min_quantity=?, updated_at=datetime('now')
            WHERE id=?
        """, (min_quantity, existing["id"]))
        conn.commit()
        return True
    finally:
        conn.close()


# ── TRANSACTIONS ──────────────────────────────────────────────────────────────

TRANSACTION_TYPES = ["IN", "OUT", "TRANSFER"]


def record_transaction(
    warehouse_id: int,
    material_number: str,
    transaction_type: str,   # IN / OUT / TRANSFER
    quantity: float,
    transaction_date: str,
    project_id: Optional[int] = None,
    reference: str = "",
    notes: str = "",
    dest_warehouse_id: Optional[int] = None,  # for TRANSFER
) -> int:
    """
    Records a stock transaction and updates stock levels.

    IN:       adds stock to warehouse
    OUT:      removes stock from warehouse
    TRANSFER: moves stock from warehouse → dest_warehouse_id

    Returns the id of the (source) stock_transactions row.
    """
    conn = get_connection()
    try:
        # Log the transaction
        cur = conn.execute("""
            INSERT INTO stock_transactions
                (warehouse_id, material_number, transaction_type,
                 quantity, project_id, reference, notes, date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (warehouse_id, material_number, transaction_type,
              quantity, project_id, reference, notes, transaction_date))
        tx_id = cur.lastrowid

        # Update source warehouse stock
        _adjust_stock(conn, warehouse_id, material_number,
                      quantity if transaction_type == "IN" else -quantity)

        # For TRANSFER: also update destination warehouse
        if transaction_type == "TRANSFER" and dest_warehouse_id:
            conn.execute("""
                INSERT INTO stock_transactions
                    (warehouse_id, material_number, transaction_type,
                     quantity, project_id, reference, notes, date)
                VALUES (?, ?, 'IN', ?, ?, ?, ?, ?)
            """, (dest_warehouse_id, material_number,
                  quantity, project_id, reference,
                  f"Transfer from warehouse {warehouse_id}", transaction_date))
            _adjust_stock(conn, dest_warehouse_id, material_number, quantity)

        conn.commit()
        return tx_id
    finally:
        conn.close()


def _adjust_stock(conn, warehouse_id: int, material_number: str, delta: float):
    """Adds delta to existing stock. Creates item if not exists."""
    existing = conn.execute("""
        SELECT id, quantity FROM stock_items
        WHERE warehouse_id=? AND material_number=?
    """, (warehouse_id, material_number)).fetchone()
    if existing:
        new_qty = max(0, existing["quantity"] + delta)
        conn.execute("""
            UPDATE stock_items SET quantity=?, updated_at=datetime('now')
            WHERE id=?
        """, (new_qty, existing["id"]))
    else:
        conn.execute("""
            INSERT INTO stock_items (warehouse_id, material_number, quantity)
            VALUES (?, ?, ?)
        """, (warehouse_id, material_number, max(0, delta)))


# ── A HAND EDIT ───────────────────────────────────────────────────────────────

def edit_stock_item(
    warehouse_id: int,
    material_number: str,
    quantity: Optional[float] = None,
    min_quantity: Optional[float] = None,
    unit: Optional[str] = None,
    description: Optional[str] = None,
    reason: str = "",
    reference: str = "",
    transaction_date: Optional[str] = None,
    project_id: Optional[int] = None,
) -> dict:
    """Add or correct one stock row by hand — the single entry point for it.

    This is what the Stock Levels tab calls when a material is typed in or a
    row is edited. It exists because the three obvious ways of doing it by hand
    each lose something:

    * `set_stock_level` writes quantity, threshold and unit in one go, so its
      `min_quantity=0` default silently zeroes the low-stock alert of any row
      edited for an unrelated reason, and it moves the quantity without an
      audit row, leaving an unexplained jump in the history.
    * `ensure_stock_item` + `set_min_quantity` keep each field honest but say
      nothing about the quantity.
    * None of them touch the `materials` catalogue, which is where the
      description lives — `get_stock` reads it through a LEFT JOIN. A material
      typed straight into the warehouse therefore had a quantity and a blank
      name, and the only way to give it one was the Materials dialog.

    So: `None` means "no opinion" and leaves that field exactly as it is; a
    value is applied. A description or unit passed as "" is an explicit clear,
    not an absent field — callers that merely have nothing to say pass None
    (the UI sends only the fields the user actually edited).

    The quantity moves as a difference through `record_transaction` — IN when
    it goes up, OUT when it goes down — measured against what is on the shelf
    at this moment, re-read here, so a dialog left open while stock moved
    cannot overshoot. The same rule the Excel import follows. The audit row
    always states the arithmetic; `reason` is the caller's own words added
    after it, which is the one thing the numbers cannot say.

    Returns what it did:
        {'material_number', 'created', 'before', 'after', 'delta',
         'transaction_id', 'threshold_set', 'unit_set', 'catalogue', 'changed'}
    """
    # Everything is validated before anything is written: a refusal halfway
    # through would leave the row created and the quantity not moved, which is
    # exactly the junk a hand edit is supposed to be able to fix.
    material_number = (material_number or "").strip().upper()
    if not material_number:
        raise ValueError("material number is required")
    if quantity is not None:
        quantity = float(quantity)
        if quantity < 0:
            raise ValueError("quantity cannot be negative: {:g}".format(quantity))
    if min_quantity is not None:
        min_quantity = float(min_quantity)
        if min_quantity < 0:
            raise ValueError(
                "min_quantity cannot be negative: {:g}".format(min_quantity))

    unit_text = None if unit is None else unit.strip()
    out = {"material_number": material_number, "created": False,
           "before": 0.0, "after": 0.0, "delta": 0.0,
           "transaction_id": None, "threshold_set": False,
           "unit_set": False, "catalogue": None, "changed": False}

    # The row first, carrying the unit — so a brand-new material arrives with
    # its unit of measure rather than getting one on a later pass.
    out["created"] = ensure_stock_item(warehouse_id, material_number,
                                       unit_text or "")
    if unit_text is not None and not out["created"]:
        out["unit_set"] = _set_unit(warehouse_id, material_number, unit_text)
    elif out["created"] and unit_text:
        out["unit_set"] = True

    held = get_stock_quantity(warehouse_id, material_number) or {}
    out["before"] = float(held.get("quantity") or 0)
    out["after"] = out["before"]

    if quantity is not None:
        target = quantity
        delta = target - out["before"]
        if abs(delta) > 1e-9:
            if project_id is None:
                project_id = _warehouse_project(warehouse_id)
            out["transaction_id"] = record_transaction(
                warehouse_id=warehouse_id,
                material_number=material_number,
                transaction_type="IN" if delta > 0 else "OUT",
                quantity=abs(delta),
                transaction_date=transaction_date or date_type.today().isoformat(),
                project_id=project_id,
                reference=(reference or "").strip() or "MANUAL EDIT",
                notes="Hand edit: {:g} → {:g}".format(out["before"], target)
                      + (" — " + reason.strip() if (reason or "").strip() else ""),
            )
            out["after"] = target
            out["delta"] = delta

    if min_quantity is not None:
        out["threshold_set"] = set_min_quantity(warehouse_id, material_number,
                                                min_quantity)

    if description is not None or unit_text is not None:
        out["catalogue"] = _save_catalogue(material_number, description,
                                           unit_text)

    out["changed"] = bool(out["created"] or out["delta"] or out["threshold_set"]
                          or out["unit_set"] or out["catalogue"])
    return out


def _set_unit(warehouse_id: int, material_number: str, unit: str) -> bool:
    """Writes the unit of measure on a stock row — including clearing it.

    `ensure_stock_item` deliberately ignores an empty unit ("said nothing" is
    not "blank it"), which is right for the Excel import and wrong for someone
    deleting a unit they typed by mistake. Returns True when it changed.
    """
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT id, unit FROM stock_items
            WHERE warehouse_id=? AND material_number=?
        """, (warehouse_id, material_number)).fetchone()
        if row is None or (row["unit"] or "") == unit:
            return False
        conn.execute("""
            UPDATE stock_items SET unit=?, updated_at=datetime('now')
            WHERE id=?
        """, (unit, row["id"]))
        conn.commit()
        return True
    finally:
        conn.close()


def _warehouse_project(warehouse_id: int) -> Optional[int]:
    """The project a warehouse belongs to, so the audit row carries it."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT project_id FROM warehouses WHERE id=?",
                           (warehouse_id,)).fetchone()
        return row["project_id"] if row else None
    finally:
        conn.close()


def _save_catalogue(material_number: str, description: Optional[str],
                    unit: Optional[str]) -> Optional[str]:
    """Merge a description / unit into the materials catalogue.

    Returns 'created', 'updated' or None when nothing needed writing. Only the
    fields that were passed are touched: `save_material` writes all three
    columns, so whatever is not being edited is read back and written as it
    was, and the notes are carried through untouched.
    """
    existing = _materials.get_material(material_number) or {}
    want_description = ((existing.get("description") or "")
                        if description is None else description.strip())
    want_unit = (existing.get("unit") or "") if unit is None else unit
    if existing and want_description == (existing.get("description") or "") \
            and want_unit == (existing.get("unit") or ""):
        return None
    if not existing and not want_description and not want_unit:
        return None
    return _materials.save_material(material_number, want_description,
                                    want_unit, existing.get("notes") or "")


def get_transactions(warehouse_id: Optional[int] = None,
                     material_number: Optional[str] = None,
                     limit: int = 200) -> List[dict]:
    conn = get_connection()
    try:
        query = """
            SELECT
                t.id, t.date, w.name AS warehouse,
                t.material_number,
                COALESCE(m.description,'') AS description,
                t.transaction_type, t.quantity,
                COALESCE(p.name,'—') AS project,
                t.reference, t.notes
            FROM stock_transactions t
            JOIN warehouses w ON t.warehouse_id=w.id
            LEFT JOIN materials m ON m.material_number=t.material_number
            LEFT JOIN projects p ON t.project_id=p.id
            WHERE 1=1
        """
        params = []
        if warehouse_id:
            query += " AND t.warehouse_id=?"; params.append(warehouse_id)
        if material_number:
            query += " AND t.material_number=?"; params.append(material_number)
        query += f" ORDER BY t.date DESC, t.created_at DESC LIMIT {limit}"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_low_stock_items() -> List[dict]:
    """Returns items where quantity <= min_quantity (alerts)."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT
                w.name AS warehouse,
                s.material_number,
                COALESCE(m.description,'') AS description,
                s.quantity, s.min_quantity, s.unit
            FROM stock_items s
            JOIN warehouses w ON s.warehouse_id=w.id
            LEFT JOIN materials m ON m.material_number=s.material_number
            WHERE s.quantity <= s.min_quantity AND s.min_quantity > 0
            ORDER BY (s.min_quantity - s.quantity) DESC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def export_stock_excel(file_path: str):
    """Exports full stock report to Excel (2 sheets: Stock + Transactions)."""
    stock = get_stock_all_warehouses()
    tx    = get_transactions(limit=500)

    df_stock = pd.DataFrame(stock) if stock else pd.DataFrame(
        columns=["warehouse","material_number","description",
                 "quantity","min_quantity","unit","low_stock"])
    df_tx = pd.DataFrame(tx) if tx else pd.DataFrame(
        columns=["date","warehouse","material_number","description",
                 "transaction_type","quantity","project","reference","notes"])

    df_stock = df_stock.rename(columns={
        "warehouse":"Warehouse","material_number":"Material #",
        "description":"Description","quantity":"Qty",
        "min_quantity":"Min Qty","unit":"Unit","low_stock":"Low Stock Alert"
    })
    df_tx = df_tx.rename(columns={
        "date":"Date","warehouse":"Warehouse",
        "material_number":"Material #","description":"Description",
        "transaction_type":"Type","quantity":"Qty",
        "project":"Project","reference":"Reference","notes":"Notes"
    })

    with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
        df_stock.to_excel(writer, index=False, sheet_name="Current Stock")
        df_tx.to_excel(writer, index=False, sheet_name="Transactions")
        for sheet_name, df in [("Current Stock", df_stock),
                                 ("Transactions", df_tx)]:
            ws = writer.sheets[sheet_name]
            for col_idx, col in enumerate(df.columns, start=1):
                max_len = max(len(str(col)),
                    df[col].astype(str).map(len).max() if len(df) > 0 else 0)
                ws.column_dimensions[
                    ws.cell(row=1, column=col_idx).column_letter
                ].width = min(max_len + 4, 50)
