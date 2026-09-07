"""
services/analytics_service.py
-------------------------------
Pandas-based aggregations for the Analytics dashboard.

All functions return pandas DataFrames — ready to display in the UI table
or export directly to Excel sheets.

We fetch raw data from SQLite once, then use pandas to slice and dice.
This keeps the DB queries simple and the analysis flexible.
"""

import pandas as pd
from database.db_manager import get_connection
from typing import Optional


def _fetch_all_logs() -> pd.DataFrame:
    """
    Internal helper: fetches all log entries with full context as a DataFrame.
    All analytics functions call this, then filter/group as needed.
    """
    conn = get_connection()
    query = """
        SELECT
            dl.id,
            dl.date,
            dl.material_number,
            COALESCE(m.description, 'N/A') AS material_description,
            dl.quantity,
            p.name              AS project,
            c.zone_number       AS zone,
            c.block_number      AS block,
            c.container_index   AS container_num,
            c.container_type    AS container_type,
            COALESCE(c.serial_number, 'N/A') AS serial_number,
            COALESCE(dl.sap_ticket, '')       AS sap_ticket
        FROM daily_logs dl
        JOIN containers c  ON dl.container_id = c.id
        JOIN projects   p  ON dl.project_id   = p.id
        LEFT JOIN materials m ON dl.material_number = m.material_number
        ORDER BY dl.date
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df


# ──────────────────────────────────────────────────────────────────────────────
#  A. MOST FREQUENTLY USED MATERIALS
# ──────────────────────────────────────────────────────────────────────────────

def get_materials_analytics(
    project: Optional[str] = None,
    top_n: int = 20,
    _df: pd.DataFrame = None,   # pass pre-fetched df to avoid re-fetching
) -> pd.DataFrame:
    df = _df if _df is not None else _fetch_all_logs()
    if df.empty:
        return pd.DataFrame(columns=["Material Number","Description",
                                     "Times Used","Total Quantity"])
    if project:
        df = df[df["project"] == project]
    grouped = (
        df.groupby(["material_number","material_description"])
        .agg(times_used=("id","count"), total_quantity=("quantity","sum"))
        .reset_index()
        .sort_values("times_used", ascending=False)
        .head(top_n)
    )
    grouped.columns = ["Material Number","Description","Times Used","Total Quantity"]
    grouped["Total Quantity"] = grouped["Total Quantity"].round(2)
    return grouped.reset_index(drop=True)


def get_containers_analytics(
    project: Optional[str] = None,
    top_n: int = 20,
    _df: pd.DataFrame = None,
) -> pd.DataFrame:
    df = _df if _df is not None else _fetch_all_logs()
    if df.empty:
        return pd.DataFrame(columns=["Serial Number","Project","Zone","Block",
                                     "Container #","Type","Service Events","Total Qty Used"])
    if project:
        df = df[df["project"] == project]
    grouped = (
        df.groupby(["serial_number","project","zone","block","container_num","container_type"])
        .agg(service_events=("id","count"), total_qty=("quantity","sum"))
        .reset_index()
        .sort_values("service_events", ascending=False)
        .head(top_n)
    )
    grouped.columns = ["Serial Number","Project","Zone","Block",
                       "Container #","Type","Service Events","Total Qty Used"]
    grouped["Total Qty Used"] = grouped["Total Qty Used"].round(2)
    return grouped.reset_index(drop=True)


def get_blocks_analytics(
    project: Optional[str] = None,
    top_n: int = 20,
    _df: pd.DataFrame = None,
) -> pd.DataFrame:
    df = _df if _df is not None else _fetch_all_logs()
    if df.empty:
        return pd.DataFrame(columns=["Project","Zone","Block",
                                     "Total Actions","Unique Materials","Total Qty Used"])
    if project:
        df = df[df["project"] == project]
    grouped = (
        df.groupby(["project","zone","block"])
        .agg(total_actions=("id","count"),
             unique_materials=("material_number","nunique"),
             total_qty=("quantity","sum"))
        .reset_index()
        .sort_values("total_actions", ascending=False)
        .head(top_n)
    )
    grouped.columns = ["Project","Zone","Block",
                       "Total Actions","Unique Materials","Total Qty Used"]
    grouped["Total Qty Used"] = grouped["Total Qty Used"].round(2)
    return grouped.reset_index(drop=True)


def get_activity_by_month(
    project: Optional[str] = None,
    _df: pd.DataFrame = None,
) -> pd.DataFrame:
    df = _df if _df is not None else _fetch_all_logs()
    if df.empty:
        return pd.DataFrame(columns=["Month","Actions"])
    if project:
        df = df[df["project"] == project]
    df["month"] = pd.to_datetime(df["date"]).dt.to_period("M").astype(str)
    grouped = (df.groupby("month").agg(actions=("id","count"))
                 .reset_index().sort_values("month"))
    grouped.columns = ["Month","Actions"]
    return grouped.reset_index(drop=True)


def export_analytics_to_excel(file_path: str, project: Optional[str] = None):
    """Fetches logs ONCE and passes to all four analytics functions."""
    df = _fetch_all_logs()   # single fetch
    if project:
        df = df[df["project"] == project]

    materials_df  = get_materials_analytics(project, _df=df)
    containers_df = get_containers_analytics(project, _df=df)
    blocks_df     = get_blocks_analytics(project, _df=df)
    monthly_df    = get_activity_by_month(project, _df=df)

    with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
        for sheet_df, sheet_name in [
            (materials_df,  "Materials"),
            (containers_df, "Containers"),
            (blocks_df,     "Blocks"),
            (monthly_df,    "Monthly Activity"),
        ]:
            sheet_df.to_excel(writer, index=False, sheet_name=sheet_name)
            ws = writer.sheets[sheet_name]
            for col_idx, col_name in enumerate(sheet_df.columns, start=1):
                max_len = max(len(str(col_name)),
                    sheet_df[col_name].astype(str).map(len).max()
                    if len(sheet_df) > 0 else 0)
                ws.column_dimensions[
                    ws.cell(row=1, column=col_idx).column_letter
                ].width = min(max_len + 4, 50)
    """
    Groups log entries by material_number.
    Returns: material number, description, times used, total quantity.
    Sorted by times_used descending.

    Args:
        project: Optional project name filter
        top_n:   How many top materials to return (default 20)
    """
    df = _fetch_all_logs()
    if df.empty:
        return pd.DataFrame(columns=["Material Number", "Description",
                                     "Times Used", "Total Quantity"])

    if project:
        df = df[df["project"] == project]

    grouped = (
        df.groupby(["material_number", "material_description"])
        .agg(
            times_used=("id", "count"),
            total_quantity=("quantity", "sum"),
        )
        .reset_index()
        .sort_values("times_used", ascending=False)
        .head(top_n)
    )

    grouped.columns = ["Material Number", "Description",
                        "Times Used", "Total Quantity"]
    grouped["Total Quantity"] = grouped["Total Quantity"].round(2)
    return grouped.reset_index(drop=True)


# ──────────────────────────────────────────────────────────────────────────────
#  B. MOST SERVICED CONTAINERS
# ──────────────────────────────────────────────────────────────────────────────

def get_containers_analytics(
    project: Optional[str] = None,
    top_n: int = 20,
) -> pd.DataFrame:
    """
    Groups log entries by container (serial number + location).
    Returns containers sorted by number of service events descending.

    Args:
        project: Optional project name filter
        top_n:   How many top containers to return
    """
    df = _fetch_all_logs()
    if df.empty:
        return pd.DataFrame(columns=["Serial Number", "Project", "Zone",
                                     "Block", "Container #", "Type",
                                     "Service Events", "Total Qty Used"])

    if project:
        df = df[df["project"] == project]

    grouped = (
        df.groupby([
            "serial_number", "project", "zone",
            "block", "container_num", "container_type"
        ])
        .agg(
            service_events=("id", "count"),
            total_qty=("quantity", "sum"),
        )
        .reset_index()
        .sort_values("service_events", ascending=False)
        .head(top_n)
    )

    grouped.columns = [
        "Serial Number", "Project", "Zone", "Block",
        "Container #", "Type", "Service Events", "Total Qty Used"
    ]
    grouped["Total Qty Used"] = grouped["Total Qty Used"].round(2)
    return grouped.reset_index(drop=True)


# ──────────────────────────────────────────────────────────────────────────────
#  C. MOST ACTIVE BLOCKS
# ──────────────────────────────────────────────────────────────────────────────

def get_blocks_analytics(
    project: Optional[str] = None,
    top_n: int = 20,
) -> pd.DataFrame:
    """
    Groups log entries by project + zone + block.
    Returns blocks sorted by number of maintenance actions descending.
    """
    df = _fetch_all_logs()
    if df.empty:
        return pd.DataFrame(columns=["Project", "Zone", "Block",
                                     "Total Actions", "Unique Materials",
                                     "Total Qty Used"])

    if project:
        df = df[df["project"] == project]

    grouped = (
        df.groupby(["project", "zone", "block"])
        .agg(
            total_actions=("id", "count"),
            unique_materials=("material_number", "nunique"),
            total_qty=("quantity", "sum"),
        )
        .reset_index()
        .sort_values("total_actions", ascending=False)
        .head(top_n)
    )

    grouped.columns = [
        "Project", "Zone", "Block",
        "Total Actions", "Unique Materials", "Total Qty Used"
    ]
    grouped["Total Qty Used"] = grouped["Total Qty Used"].round(2)
    return grouped.reset_index(drop=True)


# ──────────────────────────────────────────────────────────────────────────────
#  D. ACTIVITY OVER TIME  (bonus — used for the optional bar chart)
# ──────────────────────────────────────────────────────────────────────────────

def get_activity_by_month(project: Optional[str] = None) -> pd.DataFrame:
    """
    Returns number of log entries grouped by month (YYYY-MM).
    Used for the optional trend chart.
    """
    df = _fetch_all_logs()
    if df.empty:
        return pd.DataFrame(columns=["Month", "Actions"])

    if project:
        df = df[df["project"] == project]

    df["month"] = pd.to_datetime(df["date"]).dt.to_period("M").astype(str)

    grouped = (
        df.groupby("month")
        .agg(actions=("id", "count"))
        .reset_index()
        .sort_values("month")
    )
    grouped.columns = ["Month", "Actions"]
    return grouped.reset_index(drop=True)


# ──────────────────────────────────────────────────────────────────────────────
#  EXCEL EXPORT — all analytics as separate sheets
# ──────────────────────────────────────────────────────────────────────────────

def export_analytics_to_excel(file_path: str, project: Optional[str] = None):
    """
    Exports all three analytics tables to a single Excel file,
    each on its own sheet. Also includes a monthly activity sheet.

    Args:
        file_path: Where to save the .xlsx file
        project:   Optional project filter (None = all projects)
    """
    materials_df  = get_materials_analytics(project)
    containers_df = get_containers_analytics(project)
    blocks_df     = get_blocks_analytics(project)
    monthly_df    = get_activity_by_month(project)

    with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
        for df, sheet_name in [
            (materials_df,  "Materials"),
            (containers_df, "Containers"),
            (blocks_df,     "Blocks"),
            (monthly_df,    "Monthly Activity"),
        ]:
            df.to_excel(writer, index=False, sheet_name=sheet_name)

            # Auto-fit column widths
            ws = writer.sheets[sheet_name]
            for col_idx, col_name in enumerate(df.columns, start=1):
                max_len = max(
                    len(str(col_name)),
                    df[col_name].astype(str).map(len).max() if len(df) > 0 else 0
                )
                col_letter = ws.cell(row=1, column=col_idx).column_letter
                ws.column_dimensions[col_letter].width = min(max_len + 4, 50)

    print(f"[Analytics] Exported to {file_path}")
