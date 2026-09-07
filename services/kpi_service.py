"""
services/kpi_service.py
------------------------
KPI calculations: MTTR and MTBF per container type.

MTTR (Mean Time To Repair):
  Average time between fault_description logged and status changed to 'Fixed'.
  Approximated as: average time between consecutive work log entries
  for the same container where status goes from non-Fixed → Fixed.

MTBF (Mean Time Between Failures):
  Average operating time between failures per container type.
  Calculated as: total_monitoring_hours / number_of_fault_events

Since we don't have exact fault start/end timestamps in work_logs,
we use date-based approximation which is standard for field service.
"""

import pandas as pd
from database.db_manager import get_connection
from typing import Optional
from datetime import datetime


def _load_work_logs_df(project_id: Optional[int] = None) -> pd.DataFrame:
    conn = get_connection()
    try:
        query = """
            SELECT
                wl.id, wl.date, wl.status,
                wl.project_id, wl.container_id,
                c.container_type,
                c.zone_number, c.block_number,
                c.serial_number,
                p.name AS project
            FROM work_logs wl
            JOIN containers c ON wl.container_id=c.id
            JOIN projects   p ON wl.project_id=p.id
            WHERE 1=1
        """
        params = []
        if project_id:
            query += " AND wl.project_id=?"; params.append(project_id)
        query += " ORDER BY wl.container_id, wl.date"
        rows = conn.execute(query, params).fetchall()
        return pd.DataFrame([dict(r) for r in rows])
    finally:
        conn.close()


def calc_mttr(project_id: Optional[int] = None) -> pd.DataFrame:
    """
    MTTR per container type.
    Logic: For each container, find pairs where a non-Fixed entry
    is followed by a Fixed entry. The gap in days = repair time.
    """
    df = _load_work_logs_df(project_id)
    if df.empty:
        return pd.DataFrame(columns=[
            "Container Type","Repair Events","Avg MTTR (days)",
            "Min (days)","Max (days)"
        ])

    df["date"] = pd.to_datetime(df["date"])
    repair_times = []

    for container_id, grp in df.groupby("container_id"):
        grp = grp.sort_values("date").reset_index(drop=True)
        container_type = grp["container_type"].iloc[0]

        for i in range(1, len(grp)):
            prev = grp.iloc[i - 1]
            curr = grp.iloc[i]
            # A repair: previous was not Fixed, current is Fixed
            if prev["status"] in ("Not resolved","Escalated","Monitoring") \
               and curr["status"] == "Fixed":
                days = (curr["date"] - prev["date"]).days
                if 0 < days <= 365:  # sanity check
                    repair_times.append({
                        "container_type": container_type,
                        "repair_days": days,
                    })

    if not repair_times:
        return pd.DataFrame(columns=[
            "Container Type","Repair Events","Avg MTTR (days)",
            "Min (days)","Max (days)"
        ])

    rt_df = pd.DataFrame(repair_times)
    result = (
        rt_df.groupby("container_type")
        .agg(
            repair_events=("repair_days","count"),
            avg_mttr=("repair_days","mean"),
            min_days=("repair_days","min"),
            max_days=("repair_days","max"),
        )
        .reset_index()
        .sort_values("avg_mttr", ascending=False)
    )
    result.columns = [
        "Container Type","Repair Events",
        "Avg MTTR (days)","Min (days)","Max (days)"
    ]
    result["Avg MTTR (days)"] = result["Avg MTTR (days)"].round(1)
    return result.reset_index(drop=True)


def calc_mtbf(project_id: Optional[int] = None) -> pd.DataFrame:
    """
    MTBF per container type.
    Logic: For each container, count fault events (Not resolved / Escalated entries).
    MTBF = total days monitored / number of fault events.
    Total days = max(date) - min(date) of all log entries for that container.
    """
    df = _load_work_logs_df(project_id)
    if df.empty:
        return pd.DataFrame(columns=[
            "Container Type","Containers Monitored",
            "Total Fault Events","Avg MTBF (days)","Interpretation"
        ])

    df["date"] = pd.to_datetime(df["date"])
    mtbf_rows = []

    for container_id, grp in df.groupby("container_id"):
        container_type = grp["container_type"].iloc[0]
        min_date = grp["date"].min()
        max_date = grp["date"].max()
        total_days = max(1, (max_date - min_date).days)
        faults = grp[grp["status"].isin(["Not resolved","Escalated"])].shape[0]

        if faults > 0:
            mtbf = total_days / faults
        else:
            mtbf = total_days  # no faults = MTBF = entire monitoring period

        mtbf_rows.append({
            "container_type": container_type,
            "container_id":   container_id,
            "total_days":     total_days,
            "fault_events":   faults,
            "mtbf":           mtbf,
        })

    if not mtbf_rows:
        return pd.DataFrame(columns=[
            "Container Type","Containers Monitored",
            "Total Fault Events","Avg MTBF (days)","Interpretation"
        ])

    mtbf_df = pd.DataFrame(mtbf_rows)
    result = (
        mtbf_df.groupby("container_type")
        .agg(
            containers_monitored=("container_id","nunique"),
            total_faults=("fault_events","sum"),
            avg_mtbf=("mtbf","mean"),
        )
        .reset_index()
        .sort_values("avg_mtbf", ascending=False)
    )
    result["avg_mtbf"] = result["avg_mtbf"].round(1)
    result["interpretation"] = result["avg_mtbf"].apply(
        lambda x: f"~{x/30:.1f} months" if x >= 30 else f"~{x:.0f} days"
    )
    result.columns = [
        "Container Type","Containers Monitored",
        "Total Fault Events","Avg MTBF (days)","Interpretation"
    ]
    return result.reset_index(drop=True)


def calc_status_overview(project_id: Optional[int] = None) -> pd.DataFrame:
    """Work log status summary per container type."""
    df = _load_work_logs_df(project_id)
    if df.empty:
        return pd.DataFrame(columns=[
            "Container Type","Total Events","Fixed","Monitoring",
            "Not Resolved","Escalated","Resolution Rate"
        ])

    grouped = df.groupby(["container_type","status"]).size().unstack(fill_value=0)
    for col in ["Fixed","Monitoring","Not resolved","Escalated"]:
        if col not in grouped.columns:
            grouped[col] = 0

    grouped["total"]          = grouped.sum(axis=1)
    grouped["resolution_rate"]= (grouped.get("Fixed",0) /
                                   grouped["total"] * 100).round(1)
    grouped = grouped.reset_index().rename(columns={
        "container_type": "Container Type",
        "total":          "Total Events",
        "Not resolved":   "Not Resolved",
        "resolution_rate":"Resolution Rate (%)",
    })
    return grouped[[
        "Container Type","Total Events","Fixed","Monitoring",
        "Not Resolved","Escalated","Resolution Rate (%)"
    ]].reset_index(drop=True)


def get_full_kpi_summary(project_id: Optional[int] = None) -> dict:
    """Returns all KPI DataFrames in one call — used by the UI."""
    return {
        "mttr":     calc_mttr(project_id),
        "mtbf":     calc_mtbf(project_id),
        "overview": calc_status_overview(project_id),
    }


def export_kpi_excel(file_path: str, project_id: Optional[int] = None):
    kpis = get_full_kpi_summary(project_id)
    with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
        for key, sheet in [("overview","Status Overview"),
                            ("mttr","MTTR"),("mtbf","MTBF")]:
            df = kpis[key]
            df.to_excel(writer, index=False, sheet_name=sheet)
            ws = writer.sheets[sheet]
            for col_idx, col in enumerate(df.columns, start=1):
                max_len = max(len(str(col)),
                    df[col].astype(str).map(len).max() if len(df) > 0 else 0)
                ws.column_dimensions[
                    ws.cell(row=1, column=col_idx).column_letter
                ].width = min(max_len + 4, 40)
