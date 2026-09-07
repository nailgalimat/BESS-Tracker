"""
services/checklist_service.py
------------------------------
Commissioning checklist service.

Concepts:
  Template  = definition (what to check, for which container type)
  Item      = one check step inside a template
  Run       = one execution of a template for a real container/block
  Result    = pass/fail/N/A for each item in a run
"""

import pandas as pd
from database.db_manager import get_connection
from typing import Optional, List


# ── TEMPLATES ─────────────────────────────────────────────────────────────────

def get_all_templates() -> List[dict]:
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT t.*, COUNT(i.id) AS item_count
            FROM checklist_templates t
            LEFT JOIN checklist_items i ON i.template_id = t.id
            GROUP BY t.id
            ORDER BY t.name
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_template(template_id: int) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM checklist_templates WHERE id=?",
            (template_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def save_template(name: str, description: str = "",
                  container_type: str = "",
                  scope: str = "container",
                  template_id: Optional[int] = None) -> int:
    conn = get_connection()
    try:
        if template_id:
            conn.execute("""
                UPDATE checklist_templates
                SET name=?, description=?, container_type=?, scope=?
                WHERE id=?
            """, (name, description, container_type, scope, template_id))
            result_id = template_id
        else:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO checklist_templates
                    (name, description, container_type, scope)
                VALUES (?, ?, ?, ?)
            """, (name, description, container_type, scope))
            result_id = cur.lastrowid
        conn.commit()
        return result_id
    finally:
        conn.close()


def delete_template(template_id: int):
    conn = get_connection()
    try:
        conn.execute("DELETE FROM checklist_templates WHERE id=?", (template_id,))
        conn.commit()
    finally:
        conn.close()


# ── ITEMS ─────────────────────────────────────────────────────────────────────

def get_items(template_id: int) -> List[dict]:
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM checklist_items
            WHERE template_id=?
            ORDER BY order_num, id
        """, (template_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def save_items(template_id: int, items: List[dict]):
    """
    Replaces all items for a template.
    items: list of {category, description, expected, order_num}
    """
    conn = get_connection()
    try:
        conn.execute("DELETE FROM checklist_items WHERE template_id=?",
                     (template_id,))
        for i, item in enumerate(items):
            conn.execute("""
                INSERT INTO checklist_items
                    (template_id, order_num, category, description, expected)
                VALUES (?, ?, ?, ?, ?)
            """, (template_id, item.get("order_num", i),
                  item.get("category", ""),
                  item.get("description", ""),
                  item.get("expected", "")))
        conn.commit()
    finally:
        conn.close()


# ── RUNS ──────────────────────────────────────────────────────────────────────

def start_run(template_id: int, project_id: int,
              zone_number: int, block_number: int,
              run_date: str, engineer: str = "",
              container_id: Optional[int] = None) -> int:
    """Creates a new checklist run and pre-populates result rows (all Pending)."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO checklist_runs
                (template_id, project_id, container_id,
                 zone_number, block_number, run_date, engineer)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (template_id, project_id, container_id,
              zone_number, block_number, run_date, engineer))
        run_id = cur.lastrowid

        # Pre-create result rows for each item
        items = conn.execute(
            "SELECT id FROM checklist_items WHERE template_id=? ORDER BY order_num",
            (template_id,)
        ).fetchall()
        for item in items:
            conn.execute("""
                INSERT INTO checklist_results (run_id, item_id, result)
                VALUES (?, ?, 'Pending')
            """, (run_id, item["id"]))
        conn.commit()
        return run_id
    finally:
        conn.close()


def get_runs(project_id: Optional[int] = None,
             template_id: Optional[int] = None) -> List[dict]:
    conn = get_connection()
    try:
        query = """
            SELECT
                r.id, r.run_date, r.engineer, r.status, r.notes,
                r.zone_number, r.block_number,
                t.name AS template_name, t.container_type,
                p.name AS project_name,
                COUNT(res.id) AS total_items,
                SUM(CASE WHEN res.result='Pass' THEN 1 ELSE 0 END) AS passed,
                SUM(CASE WHEN res.result='Fail' THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN res.result='Pending' THEN 1 ELSE 0 END) AS pending
            FROM checklist_runs r
            JOIN checklist_templates t ON r.template_id = t.id
            JOIN projects p ON r.project_id = p.id
            LEFT JOIN checklist_results res ON res.run_id = r.id
            WHERE 1=1
        """
        params = []
        if project_id:
            query += " AND r.project_id=?"; params.append(project_id)
        if template_id:
            query += " AND r.template_id=?"; params.append(template_id)
        query += " GROUP BY r.id ORDER BY r.run_date DESC, r.created_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_run_detail(run_id: int) -> List[dict]:
    """Returns all items with their results for a specific run."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT
                i.order_num, i.category, i.description, i.expected,
                res.id AS result_id, res.result, res.measured, res.comment
            FROM checklist_results res
            JOIN checklist_items i ON res.item_id = i.id
            WHERE res.run_id = ?
            ORDER BY i.order_num, i.id
        """, (run_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def save_result(result_id: int, result: str,
                measured: str = "", comment: str = ""):
    conn = get_connection()
    try:
        conn.execute("""
            UPDATE checklist_results
            SET result=?, measured=?, comment=?
            WHERE id=?
        """, (result, measured, comment, result_id))
        conn.commit()
    finally:
        conn.close()


def update_run_status(run_id: int, status: str, notes: str = ""):
    conn = get_connection()
    try:
        conn.execute("""
            UPDATE checklist_runs SET status=?, notes=? WHERE id=?
        """, (status, notes, run_id))
        conn.commit()
    finally:
        conn.close()


def auto_update_run_status(run_id: int):
    """
    Automatically sets run status based on results:
      All Pass/N/A → Passed
      Any Fail     → Failed
      Any Pending  → In Progress
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT result FROM checklist_results WHERE run_id=?",
            (run_id,)
        ).fetchall()
        results = [r["result"] for r in rows]
        if any(r == "Pending" for r in results):
            status = "In Progress"
        elif any(r == "Fail" for r in results):
            status = "Failed"
        else:
            status = "Passed"
        conn.execute("UPDATE checklist_runs SET status=? WHERE id=?",
                     (status, run_id))
        conn.commit()
    finally:
        conn.close()


def delete_run(run_id: int):
    conn = get_connection()
    try:
        conn.execute("DELETE FROM checklist_runs WHERE id=?", (run_id,))
        conn.commit()
    finally:
        conn.close()


# ── EXPORT ────────────────────────────────────────────────────────────────────

def export_run_to_excel(run_id: int, file_path: str):
    """Exports a completed checklist run to Excel."""
    conn = get_connection()
    try:
        run = conn.execute("""
            SELECT r.*, t.name AS template_name, p.name AS project_name
            FROM checklist_runs r
            JOIN checklist_templates t ON r.template_id=t.id
            JOIN projects p ON r.project_id=p.id
            WHERE r.id=?
        """, (run_id,)).fetchone()
        if not run:
            raise ValueError(f"Run {run_id} not found")
        run = dict(run)
    finally:
        conn.close()

    detail = get_run_detail(run_id)
    df = pd.DataFrame(detail).rename(columns={
        "order_num":   "#",
        "category":    "Category",
        "description": "Check Item",
        "expected":    "Expected",
        "result":      "Result",
        "measured":    "Measured Value",
        "comment":     "Comment",
    })
    df = df[["#","Category","Check Item","Expected","Result","Measured Value","Comment"]]

    with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
        # Summary sheet
        summary = pd.DataFrame([
            ["Project",         run["project_name"]],
            ["Template",        run["template_name"]],
            ["Date",            run["run_date"]],
            ["Engineer",        run["engineer"]],
            ["Zone / Block",    f"Z{run['zone_number']} / B{run['block_number']}"],
            ["Status",          run["status"]],
            ["Notes",           run["notes"]],
        ], columns=["Field","Value"])
        summary.to_excel(writer, index=False, sheet_name="Summary")

        # Results sheet
        df.to_excel(writer, index=False, sheet_name="Checklist Results")

        # Style results sheet
        from openpyxl.styles import PatternFill, Font, Alignment
        ws = writer.sheets["Checklist Results"]
        colors = {"Pass":"D4EDDA","Fail":"F8D7DA","N/A":"E2E3E5","Pending":"FFF3CD"}
        for row in ws.iter_rows(min_row=2):
            result_cell = row[4]  # Result column
            fill_color = colors.get(str(result_cell.value), "FFFFFF")
            for cell in row:
                cell.fill = PatternFill("solid", fgColor=fill_color)
                cell.alignment = Alignment(horizontal="left", wrap_text=True)

        for col_idx, width in enumerate([5,15,50,25,10,20,30], start=1):
            ws.column_dimensions[
                ws.cell(row=1, column=col_idx).column_letter
            ].width = width
