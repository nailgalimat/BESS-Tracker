"""
services/material_service.py
-----------------------------
Materials catalog DB operations.

FIXES:
  - ensure_materials_table() removed (now handled by db_manager)
  - All functions use try/finally to guarantee conn.close()
"""

import pandas as pd
from database.db_manager import get_connection
from typing import List, Optional


def get_all_materials() -> List[dict]:
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT id, material_number, description, unit, notes
            FROM materials ORDER BY material_number
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_material(material_number: str) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT id, material_number, description, unit, notes
            FROM materials WHERE material_number = ?
        """, (material_number,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def save_material(material_number: str, description: str,
                  unit: str = "", notes: str = "") -> str:
    """Inserts or updates a material. Returns 'created' or 'updated'."""
    material_number = material_number.strip().upper()
    existing = get_material(material_number)
    conn = get_connection()
    try:
        if existing:
            conn.execute("""
                UPDATE materials
                SET description=?, unit=?, notes=?
                WHERE material_number=?
            """, (description.strip(), unit.strip(), notes.strip(), material_number))
            result = "updated"
        else:
            conn.execute("""
                INSERT INTO materials (material_number, description, unit, notes)
                VALUES (?, ?, ?, ?)
            """, (material_number, description.strip(), unit.strip(), notes.strip()))
            result = "created"
        conn.commit()
        return result
    finally:
        conn.close()


def delete_material(material_number: str):
    conn = get_connection()
    try:
        conn.execute("DELETE FROM materials WHERE material_number=?",
                     (material_number,))
        conn.commit()
    finally:
        conn.close()


def get_material_numbers() -> List[str]:
    """Returns just material numbers for autocomplete."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT material_number FROM materials ORDER BY material_number"
        ).fetchall()
        return [r["material_number"] for r in rows]
    finally:
        conn.close()


def import_materials_from_excel(file_path: str) -> dict:
    """Bulk import from Excel. Flexible column name detection."""
    try:
        df = pd.read_excel(file_path, dtype=str)
    except Exception as e:
        raise ValueError(f"Could not read Excel file: {e}")

    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    mat_col = next((c for c in ["material_number","material","code","mat_number","mat"]
                    if c in df.columns), None)
    if mat_col is None:
        raise ValueError("Could not find material number column.\n"
                         "Expected: 'material_number', 'material', or 'code'")

    desc_col = next((c for c in ["description","desc","material_description","name"]
                     if c in df.columns), None)
    if desc_col is None:
        raise ValueError("Could not find description column.\n"
                         "Expected: 'description', 'desc', or 'name'")

    unit_col  = next((c for c in ["unit","uom","unit_of_measure"] if c in df.columns), None)
    notes_col = next((c for c in ["notes","note","comment","remarks"] if c in df.columns), None)

    created = updated = skipped = 0
    errors = []

    for i, row in df.iterrows():
        mat_num = str(row.get(mat_col, "")).strip()
        desc    = str(row.get(desc_col, "")).strip()

        if not mat_num or mat_num.lower() in ("nan", "none", ""):
            skipped += 1
            continue

        desc  = "" if desc.lower()  in ("nan","none") else desc
        unit  = str(row.get(unit_col,  "")).strip() if unit_col  else ""
        notes = str(row.get(notes_col, "")).strip() if notes_col else ""
        unit  = "" if unit.lower()  in ("nan","none") else unit
        notes = "" if notes.lower() in ("nan","none") else notes

        try:
            result = save_material(mat_num, desc, unit, notes)
            if result == "created": created += 1
            else:                   updated += 1
        except Exception as e:
            errors.append(f"Row {i+2}: {e}")

    return {"created": created, "updated": updated,
            "skipped": skipped, "errors": errors}


def export_materials_to_excel(file_path: str):
    """Exports full catalog to Excel."""
    materials = get_all_materials()
    df = pd.DataFrame(materials) if materials else pd.DataFrame(
        columns=["material_number","description","unit","notes"])
    df = df.rename(columns={
        "material_number":"Material Number","description":"Description",
        "unit":"Unit","notes":"Notes"
    })[["Material Number","Description","Unit","Notes"]]

    with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Materials")
        ws = writer.sheets["Materials"]
        for col_idx, col_name in enumerate(df.columns, start=1):
            max_len = max(len(str(col_name)),
                df[col_name].astype(str).map(len).max() if len(df) > 0 else 0)
            ws.column_dimensions[
                ws.cell(row=1, column=col_idx).column_letter
            ].width = min(max_len + 4, 60)
