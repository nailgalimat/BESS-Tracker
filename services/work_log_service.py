"""
services/work_log_service.py
-----------------------------
Work logs DB operations.

FIXES:
  - ensure_work_logs_table() removed (now in db_manager)
  - All functions use try/finally for guaranteed conn.close()
"""

import pandas as pd
from database.db_manager import get_connection
from typing import Optional, List

STATUS_OPTIONS = ["Fixed", "Monitoring", "Not resolved", "Escalated"]

STATUS_COLORS = {
    "Fixed":        "#E8F5E9",
    "Monitoring":   "#FFF9C4",
    "Not resolved": "#FFEBEE",
    "Escalated":    "#FCE4EC",
}


def save_work_log(
    project_id, container_id, date,
    zone_number, block_number, container_index,
    serial_number="", fault_description="", work_performed="",
    status="Fixed", sap_ticket="", comments="",
    root_cause="", engineer="", start_time="", end_time="",
    affects_availability=0, unavailability_id=None,
    availability_impact="none", exclusion_id=None,
    alarm_event_id=None,
) -> int:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO work_logs (
                project_id, container_id, date,
                zone_number, block_number, container_index,
                serial_number, fault_description, work_performed,
                status, sap_ticket, comments,
                root_cause, engineer, start_time, end_time,
                affects_availability, unavailability_id,
                availability_impact, exclusion_id, alarm_event_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (project_id, container_id, date,
              zone_number, block_number, container_index,
              serial_number, fault_description, work_performed,
              status, sap_ticket, comments,
              root_cause, engineer, start_time, end_time,
              1 if affects_availability else 0, unavailability_id,
              availability_impact, exclusion_id, alarm_event_id))
        entry_id = cur.lastrowid
        conn.commit()
        return entry_id
    finally:
        conn.close()


# ── Work report ↔ alarms (one incident can span many units) ─────────────────

def link_work_log_alarms(work_log_id: int, alarm_event_ids) -> int:
    """Record every alarm this report answers. Returns how many were linked."""
    ids = [int(a) for a in (alarm_event_ids or []) if a]
    if not ids:
        return 0
    conn = get_connection()
    try:
        conn.executemany(
            "INSERT OR IGNORE INTO work_log_alarms (work_log_id, alarm_event_id) "
            "VALUES (?, ?)", [(int(work_log_id), a) for a in ids])
        conn.commit()
        return len(ids)
    finally:
        conn.close()


def get_work_log_alarms(work_log_id: int) -> List[int]:
    conn = get_connection()
    try:
        return [r[0] for r in conn.execute(
            "SELECT alarm_event_id FROM work_log_alarms WHERE work_log_id=? "
            "ORDER BY alarm_event_id", (work_log_id,))]
    finally:
        conn.close()


# ── Work-report materials (parts consumed) ──────────────────────────────────

def add_work_log_material(work_log_id, material_number, description="",
                          quantity=1.0, unit="", warehouse_id=None,
                          tx_id=None) -> int:
    conn = get_connection()
    try:
        cur = conn.execute("""
            INSERT INTO work_log_materials
                (work_log_id, material_number, description, quantity, unit,
                 warehouse_id, tx_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (work_log_id, material_number, description, float(quantity),
              unit, warehouse_id, tx_id))
        mid = cur.lastrowid
        conn.commit()
        return mid
    finally:
        conn.close()


def get_work_log_materials(work_log_id) -> List[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM work_log_materials WHERE work_log_id=? ORDER BY id",
            (work_log_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def set_work_log_unavailability(work_log_id, unavailability_id):
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE work_logs SET unavailability_id=?, affects_availability=1, "
            "availability_impact='counts' WHERE id=?",
            (unavailability_id, work_log_id))
        conn.commit()
    finally:
        conn.close()


def set_work_log_exclusion(work_log_id, exclusion_id):
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE work_logs SET exclusion_id=?, affects_availability=1, "
            "availability_impact='excluded' WHERE id=?",
            (exclusion_id, work_log_id))
        conn.commit()
    finally:
        conn.close()


def get_work_log(entry_id: int) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM work_logs WHERE id=?",
                           (entry_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_work_logs(
    project_id=None, date_from=None, date_to=None,
    zone_number=None, block_number=None, container_id=None,
    status=None, sap_ticket=None,
) -> List[dict]:
    conn = get_connection()
    try:
        query = """
            SELECT
                wl.id, p.name AS project, wl.date,
                wl.zone_number AS zone, wl.block_number AS block,
                wl.container_index AS container_num,
                c.container_type, wl.serial_number,
                wl.fault_description, wl.work_performed,
                wl.status, wl.sap_ticket, wl.comments, wl.created_at,
                wl.root_cause, wl.engineer, wl.start_time, wl.end_time,
                wl.affects_availability, wl.unavailability_id,
                wl.container_id,
                (SELECT COUNT(*) FROM work_log_materials m
                    WHERE m.work_log_id = wl.id) AS material_count
            FROM work_logs wl
            JOIN projects   p ON wl.project_id   = p.id
            JOIN containers c ON wl.container_id = c.id
            WHERE 1=1
        """
        params = []
        if project_id  is not None: query += " AND wl.project_id=?";    params.append(project_id)
        if date_from:               query += " AND wl.date>=?";          params.append(date_from)
        if date_to:                 query += " AND wl.date<=?";          params.append(date_to)
        if zone_number is not None: query += " AND wl.zone_number=?";    params.append(zone_number)
        if block_number is not None:query += " AND wl.block_number=?";   params.append(block_number)
        if container_id is not None:query += " AND wl.container_id=?";   params.append(container_id)
        if status:                  query += " AND wl.status=?";         params.append(status)
        if sap_ticket:              query += " AND wl.sap_ticket LIKE ?";params.append(f"%{sap_ticket}%")
        query += " ORDER BY wl.date DESC, wl.created_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_work_logs_for_month(project_id: int, year: int, month: int) -> List[dict]:
    """All work reports for a project within one calendar month — used to
    auto-populate the monthly report's corrective-maintenance section."""
    import calendar
    last = calendar.monthrange(int(year), int(month))[1]
    start = f"{int(year):04d}-{int(month):02d}-01"
    end   = f"{int(year):04d}-{int(month):02d}-{last:02d}"
    return get_work_logs(project_id=project_id, date_from=start, date_to=end)


def delete_work_log(entry_id: int):
    """Delete a work log and reverse its side effects: restore consumed stock
    (matching IN transaction) and remove the linked downtime event so the
    monthly report no longer counts it."""
    row = get_work_log(entry_id)
    if row:
        # reverse each consumed material with an IN transaction
        from datetime import date as _date
        try:
            from services.stock_service import record_transaction
            for m in get_work_log_materials(entry_id):
                if m.get("warehouse_id") and (m.get("quantity") or 0) > 0:
                    record_transaction(
                        warehouse_id=m["warehouse_id"],
                        material_number=m["material_number"],
                        transaction_type="IN",
                        quantity=float(m["quantity"]),
                        transaction_date=str(_date.today()),
                        project_id=row.get("project_id"),
                        reference=f"WL-{entry_id} reversal",
                        notes="Work log deleted — stock restored",
                    )
        except Exception:
            pass
        # remove the linked availability event (counts) / exclusion (excluded)
        if row.get("unavailability_id"):
            try:
                from services.availability_service import delete_manual_unavailability
                delete_manual_unavailability(row["unavailability_id"])
            except Exception:
                pass
        if row.get("exclusion_id"):
            try:
                from services.availability_service import delete_exclusion
                delete_exclusion(row["exclusion_id"])
            except Exception:
                pass
    conn = get_connection()
    try:
        conn.execute("DELETE FROM work_logs WHERE id=?", (entry_id,))
        conn.commit()
    finally:
        conn.close()


def update_work_log(entry_id: int, **fields):
    allowed = {"date","fault_description","work_performed","status","sap_ticket",
               "comments","root_cause","engineer","start_time","end_time"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{k}=?" for k in updates)
    conn = get_connection()
    try:
        conn.execute(f"UPDATE work_logs SET {set_clause} WHERE id=?",
                     list(updates.values()) + [entry_id])
        conn.commit()
    finally:
        conn.close()


def get_work_logs_dataframe(project_id=None, date_from=None, date_to=None) -> pd.DataFrame:
    rows = get_work_logs(project_id=project_id, date_from=date_from, date_to=date_to)
    if not rows:
        return pd.DataFrame(columns=[
            "ID","Project","Date","Zone","Block","Container #","Container Type",
            "Serial Number","Fault Description","Work Performed",
            "Status","SAP Ticket","Comments"
        ])
    df = pd.DataFrame(rows).rename(columns={
        "id":"ID","project":"Project","date":"Date","zone":"Zone","block":"Block",
        "container_num":"Container #","container_type":"Container Type",
        "serial_number":"Serial Number","fault_description":"Fault Description",
        "work_performed":"Work Performed","status":"Status",
        "sap_ticket":"SAP Ticket","comments":"Comments",
    })
    cols = ["ID","Project","Date","Zone","Block","Container #","Container Type",
            "Serial Number","Fault Description","Work Performed",
            "Status","SAP Ticket","Comments"]
    return df[[c for c in cols if c in df.columns]]


def get_status_summary(project_id=None, date_from=None, date_to=None) -> pd.DataFrame:
    df = get_work_logs_dataframe(project_id, date_from, date_to)
    if df.empty:
        return pd.DataFrame(columns=["Status","Count","%"])
    s = (df.groupby("Status").size().reset_index(name="Count")
           .sort_values("Count", ascending=False))
    s["%"] = (s["Count"] / s["Count"].sum() * 100).round(1).astype(str) + "%"
    return s.reset_index(drop=True)


def get_most_frequent_faults(project_id=None, date_from=None,
                              date_to=None, top_n=10) -> pd.DataFrame:
    rows = get_work_logs(project_id=project_id,
                          date_from=date_from, date_to=date_to)
    if not rows:
        return pd.DataFrame(columns=[
            "Zone","Block","Container #","Serial Number","Incidents","Last Status"])
    df = pd.DataFrame(rows)
    g = (df.groupby(["zone","block","container_num","serial_number"])
           .agg(incidents=("id","count"), last_status=("status","last"))
           .reset_index()
           .sort_values("incidents", ascending=False)
           .head(top_n))
    g.columns = ["Zone","Block","Container #","Serial Number","Incidents","Last Status"]
    return g.reset_index(drop=True)


def export_work_logs_excel(file_path, project_id=None,
                            date_from=None, date_to=None):
    logs_df   = get_work_logs_dataframe(project_id, date_from, date_to)
    status_df = get_status_summary(project_id, date_from, date_to)
    issues_df = get_most_frequent_faults(project_id, date_from, date_to)
    with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
        for df, sheet in [(logs_df,"Work Logs"),
                          (status_df,"Status Summary"),
                          (issues_df,"Top Issues")]:
            df.to_excel(writer, index=False, sheet_name=sheet)
            ws = writer.sheets[sheet]
            for col_idx, col_name in enumerate(df.columns, start=1):
                max_len = max(len(str(col_name)),
                    df[col_name].astype(str).map(len).max() if len(df) > 0 else 0)
                ws.column_dimensions[
                    ws.cell(row=1, column=col_idx).column_letter
                ].width = min(max_len + 4, 60)
