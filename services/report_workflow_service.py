"""
services/report_workflow_service.py
------------------------------------
Backend for the Project → Month → Report workflow.

Three concerns:
  1. project_report_config — static per-project report inputs (entered once):
     customer, O&M, OEM, capacities, contractual/availability basis, cycle
     target, site_type ('tashkent'|'bukhara'), prepared/reviewed by.
  2. report_months — one row per (project, year, month) holding the month's
     narrative (CM, site visits, recommendations, planned-next, incidents).
  3. pm_activities — preventive-maintenance entries per (project, month):
     block(s), date range, hours, description.

Unavailability (exclusions / manual / balancing) stays in availability_service
but is now scoped by (project_id, year, month); the month-scoped getters live
here for convenience.
"""

from typing import Optional, List
from database.db_manager import get_connection

MONTHS_EN = ['', 'January', 'February', 'March', 'April', 'May', 'June',
             'July', 'August', 'September', 'October', 'November', 'December']

_CONFIG_FIELDS = [
    'site_type', 'customer', 'om_company', 'oem', 'equipment',
    'project_capacity_str', 'plant_capacity_mw', 'per_block_capacity_mw',
    'contractual_plant_capacity_mw', 'availability_basis_mwh',
    'redundancy_threshold_pct', 'yearly_cycle_target',
    'prepared_by', 'reviewed_by',
]
_MONTH_FIELDS = [
    'report_number', 'status', 'cm_activities', 'site_visits',
    'recommendations', 'planned_next', 'safety_incidents', 'notes',
]


# ── Projects ────────────────────────────────────────────────────────────────

def list_projects() -> List[dict]:
    conn = get_connection()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM projects ORDER BY name").fetchall()]
    finally:
        conn.close()


def get_project(project_id: int) -> Optional[dict]:
    conn = get_connection()
    try:
        r = conn.execute("SELECT * FROM projects WHERE id=?",
                         (project_id,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def create_project(name: str, num_zones: int = 1, num_blocks: int = 1,
                   num_containers: int = 1, project_type: str = 'BESS',
                   description: str = '') -> int:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""INSERT INTO projects
            (name, description, num_zones, num_blocks, num_containers, project_type)
            VALUES (?,?,?,?,?,?)""",
            (name, description, num_zones, num_blocks, num_containers, project_type))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


# ── Project report config (1:1) ──────────────────────────────────────────────

def get_project_config(project_id: int) -> dict:
    """Return the config row merged with defaults ({} fields if not set yet)."""
    conn = get_connection()
    try:
        r = conn.execute("SELECT * FROM project_report_config WHERE project_id=?",
                         (project_id,)).fetchone()
        return dict(r) if r else {'project_id': project_id}
    finally:
        conn.close()


def save_project_config(project_id: int, **fields):
    """Upsert the config row. Unknown keys are ignored."""
    data = {k: v for k, v in fields.items() if k in _CONFIG_FIELDS}
    conn = get_connection()
    try:
        exists = conn.execute(
            "SELECT 1 FROM project_report_config WHERE project_id=?",
            (project_id,)).fetchone()
        if exists:
            if data:
                sets = ", ".join(f"{k}=?" for k in data)
                conn.execute(
                    f"UPDATE project_report_config SET {sets} WHERE project_id=?",
                    [*data.values(), project_id])
        else:
            cols = ['project_id'] + list(data.keys())
            conn.execute(
                f"INSERT INTO project_report_config ({', '.join(cols)}) "
                f"VALUES ({', '.join('?' * len(cols))})",
                [project_id, *data.values()])
        conn.commit()
    finally:
        conn.close()


# ── Report months ─────────────────────────────────────────────────────────────

def list_report_months(project_id: int) -> List[dict]:
    conn = get_connection()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM report_months WHERE project_id=? "
            "ORDER BY year DESC, month DESC", (project_id,)).fetchall()]
    finally:
        conn.close()


def get_report_month(project_id: int, year: int, month: int) -> Optional[dict]:
    conn = get_connection()
    try:
        r = conn.execute(
            "SELECT * FROM report_months WHERE project_id=? AND year=? AND month=?",
            (project_id, year, month)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def ensure_report_month(project_id: int, year: int, month: int) -> int:
    """Return the report_months id for (project, year, month), creating it if
    absent."""
    conn = get_connection()
    try:
        r = conn.execute(
            "SELECT id FROM report_months WHERE project_id=? AND year=? AND month=?",
            (project_id, year, month)).fetchone()
        if r:
            return r[0]
        cur = conn.cursor()
        cur.execute("INSERT INTO report_months (project_id, year, month) "
                    "VALUES (?,?,?)", (project_id, year, month))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def save_report_month(project_id: int, year: int, month: int, **fields):
    """Upsert the narrative fields for a month."""
    ensure_report_month(project_id, year, month)
    data = {k: v for k, v in fields.items() if k in _MONTH_FIELDS}
    if not data:
        return
    conn = get_connection()
    try:
        sets = ", ".join(f"{k}=?" for k in data)
        conn.execute(
            f"UPDATE report_months SET {sets} WHERE project_id=? AND year=? AND month=?",
            [*data.values(), project_id, year, month])
        conn.commit()
    finally:
        conn.close()


def delete_report_month(month_id: int):
    """Delete a month row and its PM/unavailability entries for that period."""
    conn = get_connection()
    try:
        r = conn.execute("SELECT project_id, year, month FROM report_months "
                         "WHERE id=?", (month_id,)).fetchone()
        conn.execute("DELETE FROM report_months WHERE id=?", (month_id,))
        if r:
            pid, y, m = r[0], r[1], r[2]
            for t in ("pm_activities", "availability_exclusions",
                      "manual_unavailability", "balancing_periods"):
                conn.execute(
                    f"DELETE FROM {t} WHERE project_id=? AND year=? AND month=?",
                    (pid, y, m))
        conn.commit()
    finally:
        conn.close()


# ── PM activities ─────────────────────────────────────────────────────────────

def get_pm_activities(project_id: int, year: int, month: int) -> List[dict]:
    conn = get_connection()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM pm_activities WHERE project_id=? AND year=? AND month=? "
            "ORDER BY date_from", (project_id, year, month)).fetchall()]
    finally:
        conn.close()


def add_pm_activity(project_id: int, year: int, month: int,
                    affected_blocks: str = '', date_from: str = '',
                    date_to: str = '', hours: float = 0.0,
                    description: str = '') -> int:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""INSERT INTO pm_activities
            (project_id, year, month, affected_blocks, date_from, date_to, hours, description)
            VALUES (?,?,?,?,?,?,?,?)""",
            (project_id, year, month, affected_blocks, date_from, date_to,
             float(hours or 0), description))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_pm_activity(pm_id: int, affected_blocks: str = '', date_from: str = '',
                       date_to: str = '', hours: float = 0.0,
                       description: str = ''):
    conn = get_connection()
    try:
        conn.execute("""UPDATE pm_activities
            SET affected_blocks=?, date_from=?, date_to=?, hours=?, description=?
            WHERE id=?""",
            (affected_blocks, date_from, date_to, float(hours or 0),
             description, int(pm_id)))
        conn.commit()
    finally:
        conn.close()


def delete_pm_activity(pm_id: int):
    conn = get_connection()
    try:
        conn.execute("DELETE FROM pm_activities WHERE id=?", (int(pm_id),))
        conn.commit()
    finally:
        conn.close()


def pm_as_unavailability(project_id: int, year: int, month: int) -> List[dict]:
    """The month's PM activities as downtime rows the report engine counts
    against availability — one per affected block, whole-block scope."""
    from services.availability_service import parse_block_spec
    nblk = int((get_project(project_id) or {}).get('num_blocks') or 0)
    out = []
    for r in get_pm_activities(project_id, year, month):
        hours = float(r.get('hours') or 0)
        if hours <= 0:
            continue
        df = r.get('date_from') or ''
        dt = r.get('date_to') or df
        desc = (r.get('description') or 'PM').strip()
        blocks = parse_block_spec(r.get('affected_blocks'))
        if blocks is None:                           # whole plant
            blocks = range(1, nblk + 1)
        for b in sorted(blocks):
            out.append({'block': b, 'lc': None, 'date_from': df, 'date_to': dt,
                        'downtime_h': hours,
                        'cause': f"PM: {desc}" if desc else "PM",
                        'subsystem': 'PM'})
    return out


def pm_as_strings(project_id: int, year: int, month: int) -> List[str]:
    """The month's PM activities as the report's 3.1 bullet lines."""
    out = []
    for r in get_pm_activities(project_id, year, month):
        blk = r.get('affected_blocks') or 'all blocks'
        hrs = r.get('hours') or 0
        desc = r.get('description', '') or 'PM'
        out.append(f"Block(s) {blk}: {desc} ({hrs:.0f} h, "
                   f"{r.get('date_from', '')}→{r.get('date_to', '')})")
    return out


def report_inputs(project_id: int, year: int, month: int) -> dict:
    """Everything the monthly report takes from the database for one
    project-month: the project's capacities and targets, and every input that
    moves availability — exclusions, operator-entered downtime, PM as downtime,
    cycle-balancing periods.

    Defined once so the customer's report and a verification run cannot be fed
    differently. They were: the Monthly Reports page assembled these itself,
    Block Performance took every project's and every month's rows, and a test
    that passed only the exclusions checked a figure nobody receives.
    """
    from services import availability_service as av
    cfg = get_project_config(project_id)
    manual = list(av.get_manual_unavailability(
        project_id=project_id, year=year, month=month) or [])
    manual += pm_as_unavailability(project_id, year, month)
    return dict(
        plant_capacity_mw=cfg.get('plant_capacity_mw') or None,
        per_block_capacity_mw=cfg.get('per_block_capacity_mw') or None,
        contractual_plant_capacity_mw=cfg.get('contractual_plant_capacity_mw') or None,
        redundancy_threshold_pct=cfg.get('redundancy_threshold_pct') or 100,
        yearly_cycle_target=cfg.get('yearly_cycle_target') or 365.0,
        pm_activities=pm_as_strings(project_id, year, month) or None,
        exclusions=av.get_exclusions(
            project_id=project_id, year=year, month=month) or None,
        manual_unavailability=manual or None,
        balancing_periods=av.get_balancing_periods(
            project_id=project_id, year=year, month=month) or None,
    )
