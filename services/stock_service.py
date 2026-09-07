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
    """Sets (upserts) stock level for a material in a warehouse."""
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
