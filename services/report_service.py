"""
services/report_service.py
---------------------------
Handles report generation and Excel export.
Uses pandas to structure data and openpyxl (via pandas) to write .xlsx files.
"""

import pandas as pd
from typing import List, Optional
from services.log_service import get_log_entries


# Friendly column names for the exported Excel file
COLUMN_MAP = {
    "id":              "Log ID",
    "project_name":    "Project",
    "date":            "Date",
    "zone_number":     "Zone",
    "block_number":    "Block",
    "container_index": "Container #",
    "container_type":  "Container Type",
    "serial_number":   "Serial Number",
    "material_number": "Material Number",
    "quantity":        "Quantity",
    "warehouse_name":  "Warehouse",
    "comment":         "Comment",
    "created_at":      "Recorded At",
}


def get_report_dataframe(
    project_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    zone_number: Optional[int] = None,
    block_number: Optional[int] = None,
    container_id: Optional[int] = None,
) -> pd.DataFrame:
    """
    Fetches filtered log entries and returns them as a pandas DataFrame.
    This is used both for displaying in the UI table and for Excel export.
    """
    rows = get_log_entries(
        project_id=project_id,
        date_from=date_from,
        date_to=date_to,
        zone_number=zone_number,
        block_number=block_number,
        container_id=container_id,
    )

    if not rows:
        # Return an empty DataFrame with the right columns
        return pd.DataFrame(columns=list(COLUMN_MAP.values()))

    df = pd.DataFrame(rows)

    # Rename columns to human-friendly names
    df = df.rename(columns=COLUMN_MAP)

    # Return only the columns we want (in order)
    available_cols = [c for c in COLUMN_MAP.values() if c in df.columns]
    return df[available_cols]


def export_to_excel(df: pd.DataFrame, file_path: str):
    """
    Exports a DataFrame to an Excel file with basic formatting.

    Args:
        df:        The DataFrame to export (from get_report_dataframe)
        file_path: Full path where the .xlsx file should be saved
    """
    with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Daily Report")

        # Auto-adjust column widths for readability
        worksheet = writer.sheets["Daily Report"]
        for col_idx, column in enumerate(df.columns, start=1):
            # Find max content width in this column
            max_len = max(
                len(str(column)),  # header length
                df[column].astype(str).map(len).max() if len(df) > 0 else 0
            )
            # Add a little padding, cap at 50 chars
            col_letter = worksheet.cell(row=1, column=col_idx).column_letter
            worksheet.column_dimensions[col_letter].width = min(max_len + 4, 50)

    print(f"[Report] Exported {len(df)} rows to {file_path}")
