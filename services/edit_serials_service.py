"""
services/edit_serials_service.py
---------------------------------
Database update logic for editing container serial numbers and types.
Called by ui/edit_serials_dialog.py.
"""

from database.db_manager import get_connection
from typing import List, Tuple
import pandas as pd


def update_container_serial_and_type(updates: List[Tuple[int, str, str]]):
    """
    Bulk-updates container_type and serial_number for a list of containers.

    Args:
        updates: List of (container_id, new_type, new_serial) tuples
    """
    conn = get_connection()
    for container_id, new_type, new_serial in updates:
        conn.execute("""
            UPDATE containers
            SET container_type = ?,
                serial_number  = ?
            WHERE id = ?
        """, (new_type, new_serial, container_id))
    conn.commit()
    conn.close()


def export_serials_template(containers: list, file_path: str):
    """
    Exports a pre-filled Excel template for a project's containers.
    Sorted by Zone → Block → Container # for easy reading.
    Container ID column is kept for import matching — do not change it.
    """
    rows = []
    for c in containers:
        rows.append({
            "Container ID":  c.id,
            "Zone":          c.zone_number,
            "Block":         c.block_number,
            "Container #":   c.container_index,
            "Type":          c.container_type,
            "Serial Number": c.serial_number or "",
        })

    df = pd.DataFrame(rows)

    # Sort by Zone → Block → Container # so the template reads naturally
    df = df.sort_values(
        ["Zone", "Block", "Container #"]
    ).reset_index(drop=True)

    with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Serials")

        ws = writer.sheets["Serials"]

        # Column widths
        for col_idx, width in enumerate([14, 8, 8, 12, 20, 25], start=1):
            col_letter = ws.cell(row=1, column=col_idx).column_letter
            ws.column_dimensions[col_letter].width = width

        # Style header and data rows
        from openpyxl.styles import PatternFill, Font, Alignment
        from openpyxl.styles.numbers import FORMAT_TEXT

        header_fill   = PatternFill("solid", fgColor="1A2B45")
        editable_fill = PatternFill("solid", fgColor="E8F5E9")  # green = editable
        readonly_fill = PatternFill("solid", fgColor="F5F7FA")  # grey  = read-only

        for col in range(1, 7):
            cell = ws.cell(row=1, column=col)
            cell.fill      = header_fill
            cell.font      = Font(color="FFFFFF", bold=True)
            cell.alignment = Alignment(horizontal="center")

        for row in range(2, len(rows) + 2):
            for col in range(1, 7):
                cell = ws.cell(row=row, column=col)
                cell.alignment = Alignment(horizontal="center")
                if col in (5, 6):
                    cell.fill = editable_fill
                else:
                    cell.fill = readonly_fill
                # Force Serial Number column to Text — prevents Excel
                # from converting serials like S2506110785 to numbers
                if col == 6:
                    cell.number_format = FORMAT_TEXT

        # Freeze the header row so it stays visible when scrolling
        ws.freeze_panes = "A2"

        # Add note below data
        note_row = len(rows) + 3
        note_cell = ws.cell(row=note_row, column=1,
            value="NOTE: Do not change Container ID, Zone, Block, or Container # columns. Only edit Type and Serial Number.")
        note_cell.font = Font(italic=True, color="888888")

    print(f"[Serials] Template exported to {file_path}")


def import_serials_from_excel(file_path: str) -> dict:
    """
    Reads a filled-in serial numbers template and updates the database.
    Matches rows by 'Container ID' column (internal DB id).
    Updates 'Type' and 'Serial Number' columns.
    """
    try:
        # Read as string to prevent Excel converting serials to numbers
        df = pd.read_excel(file_path, sheet_name="Serials", dtype=str)
    except Exception as e:
        raise ValueError(f"Could not read file: {e}")

    # Normalize column names
    df.columns = [c.strip() for c in df.columns]

    # Validate required columns
    required = ["Container ID", "Type", "Serial Number"]
    missing  = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"Missing columns: {', '.join(missing)}\n"
            "Make sure you are using the downloaded template without renaming columns."
        )

    updated = 0
    skipped = 0
    errors  = []

    # Get all valid container IDs from DB for cross-checking
    conn = get_connection()
    try:
        valid_ids = set(
            r[0] for r in conn.execute("SELECT id FROM containers").fetchall()
        )
    finally:
        conn.close()

    conn = get_connection()
    try:
        for i, row in df.iterrows():
            raw_id = str(row.get("Container ID", "")).strip()

            # Skip empty rows and the NOTE row at the bottom of the template
            if not raw_id or raw_id.lower() in ("nan", "none", ""):
                skipped += 1
                continue

            # Skip rows where Container ID is not a plain integer
            # (catches the NOTE row and any other non-data rows)
            try:
                container_id = int(float(raw_id))
            except (ValueError, OverflowError):
                skipped += 1
                continue

            # Skip if container ID doesn't exist in DB
            if container_id not in valid_ids:
                errors.append(
                    f"Row {i+2}: Container ID {container_id} not found in database"
                )
                continue

            new_type   = str(row.get("Type",          "")).strip()
            new_serial = str(row.get("Serial Number", "")).strip()

            # Clean pandas NaN strings
            if new_type.lower()   in ("nan", "none"): new_type   = ""
            if new_serial.lower() in ("nan", "none"): new_serial = ""

            # Remove trailing .0 that pandas adds to numeric-looking serials
            # e.g. "12345.0" → "12345"
            if new_serial.endswith(".0") and new_serial[:-2].isdigit():
                new_serial = new_serial[:-2]

            if not new_type:
                skipped += 1
                continue

            try:
                conn.execute("""
                    UPDATE containers
                    SET container_type = ?,
                        serial_number  = ?
                    WHERE id = ?
                """, (new_type, new_serial, container_id))
                updated += 1
            except Exception as e:
                errors.append(f"Row {i+2}: DB error — {e}")

        conn.commit()
    finally:
        conn.close()

    return {"updated": updated, "skipped": skipped, "errors": errors}

