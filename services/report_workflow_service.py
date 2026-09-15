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

import datetime
import re
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
# One PM record per (project, plant block, date). Five places used to write PM
# hours — the phone's PM event, planner completion, the Monthly Reports PM tab,
# Excel import through the planner, and the Work Report — with nothing between
# them, so block 41 on 16.09 could carry a 4-h job, a 3-h phone event and a 4-h
# tab row: 11 h x 4 PCS = 44 unit-h for one PM. Every PM write now goes through
# record_pm / update_pm_record, which validate the entry and refuse a second
# record for a block-day unless the caller says what to do with it. The report
# side (pm_as_unavailability / pm_as_strings) counts the first record of a
# block-day only, so a duplicate that already exists is shown, not charged.

PM_MAX_HOURS = 24.0          # a PM record is one block-day's work
PM_CONFIRM_HOURS = 12.0      # the desktop asks "sure?" above this
PM_EARLIEST_DATE = '2020-01-01'

_BLOCK_TOKEN = re.compile(r'^\s*\d+\s*(?:-\s*\d+\s*)?$')


class PMValidationError(ValueError):
    """A PM entry that must not be saved as it is; `problems` says why."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__('; '.join(self.problems))


class PMDuplicateError(ValueError):
    """A PM record already exists for one or more block-days. Nothing written.
    `duplicates` = [{'block', 'date', 'existing': row}]."""

    def __init__(self, duplicates):
        self.duplicates = list(duplicates)
        super().__init__('; '.join(
            f"block {d['block']} on {d['date']} already has PM record "
            f"#{d['existing']['id']} ({fmt_hours(d['existing'].get('hours'))} h"
            f"{', ' + d['existing']['source'] if d['existing'].get('source') else ''})"
            for d in self.duplicates))


def fmt_hours(h) -> str:
    """Hours as printed: 3.25 -> '3.25', 2.5 -> '2.5', 4.0 -> '4'. Never
    rounded to whole hours — the 3.1 lines used to print 3.25 h as '3 h'."""
    try:
        return f"{round(float(h or 0), 2):g}"
    except (TypeError, ValueError):
        return str(h)


def pm_blocks(affected_blocks, n_blocks: int = 0) -> List[int]:
    """Plant blocks a PM record names. An empty field names none — it is never
    'the whole plant' (a phone PM with the block left out used to charge all 70
    blocks); that takes an explicit 'all'. Blocks outside 1..n_blocks are
    dropped (validate_pm refuses them on entry)."""
    s = str(affected_blocks or '').strip()
    if not s:
        return []
    if s.lower() in ('all', 'all blocks'):
        return list(range(1, int(n_blocks or 0) + 1))
    from services.availability_service import parse_block_spec
    got = parse_block_spec(s) or set()
    return sorted(b for b in got if b >= 1 and (not n_blocks or b <= n_blocks))


def _iso_date(s):
    try:
        return datetime.date.fromisoformat(str(s or '').strip()[:10])
    except ValueError:
        return None


def validate_pm(project_id: int, affected_blocks, date_from, date_to=None,
                hours=None, today: datetime.date = None) -> dict:
    """Check a PM entry before it is written. Returns {blocks, date_from,
    date_to, hours, n_blocks}; raises PMValidationError listing every problem.

    Rules (the same for the phone, the planner, the PM tab and Excel import):
    a block is required — the whole plant only as an explicit 'all'; every
    token must be a plant block number; 0 < hours <= 24; dates are ISO dates
    from 2020-01-01 up to tomorrow, and the end is not before the start.
    Raises RuntimeError when the project is not on this desktop (a phone event
    for it then waits in the sync inbox and is retried).
    """
    proj = get_project(project_id)
    if not proj:
        raise RuntimeError(f"project {project_id} is not on this desktop yet")
    n = int(proj.get('num_blocks') or 0)
    problems, blocks = [], []
    spec = str(affected_blocks or '').strip()
    if not spec:
        problems.append("a block is required (choose 'all' explicitly for the whole plant)")
    elif spec.lower() in ('all', 'all blocks'):
        blocks = list(range(1, n + 1))
        if not blocks:
            problems.append("the project has no block count - list the blocks")
    else:
        from services.availability_service import parse_block_spec
        bad = [t.strip() for t in spec.split(',') if t.strip() and not _BLOCK_TOKEN.match(t)]
        if bad:
            problems.append("not a block number: " + ', '.join(repr(t) for t in bad[:3]))
        got = parse_block_spec(spec) or set()
        outside = sorted(b for b in got if b < 1 or (n and b > n))
        if outside:
            problems.append("not a block of this plant (1..{}): {}".format(
                n or '?', ', '.join(map(str, outside[:5]))))
        blocks = sorted(set(got) - set(outside))
        if not blocks and not bad and not outside:
            problems.append("a block is required")

    today = today or datetime.date.today()
    latest = today + datetime.timedelta(days=1)
    earliest = datetime.date.fromisoformat(PM_EARLIEST_DATE)
    d1 = _iso_date(date_from)
    d2 = _iso_date(date_to) if str(date_to or '').strip() else d1
    if d1 is None:
        problems.append("the date is missing or not a date (YYYY-MM-DD)")
    elif not (earliest <= d1 <= latest):
        problems.append(f"date {d1} is outside {earliest} .. {latest}")
    if str(date_to or '').strip() and d2 is None:
        problems.append("the end date is not a date (YYYY-MM-DD)")
    elif d1 and d2:
        if d2 < d1:
            problems.append("the end date is before the start date")
        elif d2 > latest:
            problems.append(f"end date {d2} is after {latest}")

    try:
        h = float(hours)
    except (TypeError, ValueError):
        h = None
    if h is None or not (0 < h <= PM_MAX_HOURS):
        problems.append(f"hours must be more than 0 and at most {PM_MAX_HOURS:g} "
                        f"(got {hours if hours not in (None, '') else 'nothing'})")
    if problems:
        raise PMValidationError(problems)
    return {'blocks': blocks, 'date_from': d1.isoformat(), 'date_to': d2.isoformat(),
            'hours': h, 'n_blocks': n}


def get_pm_activities(project_id: int, year: int, month: int) -> List[dict]:
    """The month's PM records, by date then entry order (the first record of a
    block-day is the one the report counts)."""
    conn = get_connection()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM pm_activities WHERE project_id=? AND year=? AND month=? "
            "ORDER BY date_from, id", (project_id, year, month)).fetchall()]
    finally:
        conn.close()


def get_pm_record(pm_id: int) -> Optional[dict]:
    conn = get_connection()
    try:
        r = conn.execute("SELECT * FROM pm_activities WHERE id=?", (int(pm_id),)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def _pm_holders(conn, project_id: int, date: str, n_blocks: int, exclude_id=None) -> dict:
    """{block: row} for PM records of the project dated `date` (first by id)."""
    held = {}
    for r in conn.execute("SELECT * FROM pm_activities WHERE project_id=? AND date_from=? "
                          "ORDER BY id", (project_id, date)).fetchall():
        r = dict(r)
        if exclude_id is not None and r['id'] == exclude_id:
            continue
        for b in pm_blocks(r.get('affected_blocks'), n_blocks):
            held.setdefault(b, r)
    return held


def record_pm(project_id: int, affected_blocks, date_from, date_to=None,
              hours=None, description: str = '', source: str = 'desktop',
              source_ref: str = '', on_duplicate: str = 'raise',
              today: datetime.date = None) -> dict:
    """The one way a PM record is written. Validates (validate_pm), splits a
    multi-block entry into one record per block, and stamps year/month from the
    date.

    A block-day that already has a record is a duplicate — unless that record
    came from the same source and reference (the same phone event or plan job
    applied again), which is updated in place. `on_duplicate`:
      'raise'  — write nothing and raise PMDuplicateError (the default; the
                 desktop asks, a phone event waits for the desktop)
      'update' — put these hours (and description, if given) on the existing
                 record — the "replace" answer
      'skip'   — leave the existing record alone and write only the free blocks

    Returns {'created': [ids], 'updated': [ids], 'skipped': [{'block', 'existing'}],
    'blocks': [...]}.
    """
    if on_duplicate not in ('raise', 'update', 'skip'):
        raise ValueError("on_duplicate must be 'raise', 'update' or 'skip'")
    v = validate_pm(project_id, affected_blocks, date_from, date_to, hours, today=today)
    df, dt, h, n = v['date_from'], v['date_to'], v['hours'], v['n_blocks']
    desc = (description or '').strip()
    y, m = int(df[:4]), int(df[5:7])
    ref = str(source_ref or '')
    conn = get_connection()
    try:
        held = _pm_holders(conn, project_id, df, n)
        plan, dups = [], []
        for b in v['blocks']:
            r = held.get(b)
            if r is None:
                plan.append((b, None, 'new'))
            elif ref and r.get('source') == source and (r.get('source_ref') or '') == ref:
                plan.append((b, r, 'own'))
            else:
                plan.append((b, r, 'dup'))
                dups.append({'block': b, 'date': df, 'existing': r})
        if dups and on_duplicate == 'raise':
            raise PMDuplicateError(dups)
        created, updated, skipped = [], [], []
        for b, r, what in plan:
            if what == 'new':
                cur = conn.execute("""
                    INSERT INTO pm_activities
                        (project_id, year, month, affected_blocks, date_from, date_to,
                         hours, description, source, source_ref)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (project_id, y, m, str(b), df, dt, h,
                     desc or 'Preventive maintenance', source, ref))
                created.append(cur.lastrowid)
            elif what == 'own' or on_duplicate == 'update':
                if len(pm_blocks(r.get('affected_blocks'), n)) != 1:
                    raise PMValidationError([
                        f"block {b} is part of PM record #{r['id']} covering blocks "
                        f"{r.get('affected_blocks')} - edit that record instead"])
                conn.execute("""
                    UPDATE pm_activities SET hours=?, date_to=?, description=?
                     WHERE id=?""",
                    (h, dt, desc or r.get('description') or 'Preventive maintenance', r['id']))
                if r['id'] not in updated:
                    updated.append(r['id'])
            else:
                skipped.append({'block': b, 'existing': r})
        conn.commit()
    finally:
        conn.close()
    return {'created': created, 'updated': updated, 'skipped': skipped,
            'blocks': v['blocks']}


def update_pm_record(pm_id: int, affected_blocks, date_from, date_to=None,
                     hours=None, description: str = '',
                     today: datetime.date = None) -> dict:
    """Edit a PM record with the same rules as record_pm. More than one block
    splits it: this record keeps the first, new records (same source) take the
    rest. A block-day held by another record raises PMDuplicateError and
    changes nothing. Returns {'updated': id, 'created': [ids]}."""
    row = get_pm_record(pm_id)
    if not row:
        raise ValueError(f"No PM record {pm_id}")
    v = validate_pm(row['project_id'], affected_blocks, date_from, date_to, hours, today=today)
    df, dt, h, n = v['date_from'], v['date_to'], v['hours'], v['n_blocks']
    desc = (description or '').strip() or row.get('description') or 'Preventive maintenance'
    conn = get_connection()
    try:
        held = _pm_holders(conn, row['project_id'], df, n, exclude_id=row['id'])
        dups = [{'block': b, 'date': df, 'existing': held[b]} for b in v['blocks'] if b in held]
        if dups:
            raise PMDuplicateError(dups)
        first, rest = v['blocks'][0], v['blocks'][1:]
        conn.execute("""
            UPDATE pm_activities
               SET affected_blocks=?, date_from=?, date_to=?, hours=?, description=?,
                   year=?, month=?
             WHERE id=?""",
            (str(first), df, dt, h, desc, int(df[:4]), int(df[5:7]), row['id']))
        created = []
        for b in rest:
            cur = conn.execute("""
                INSERT INTO pm_activities
                    (project_id, year, month, affected_blocks, date_from, date_to,
                     hours, description, source, source_ref)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (row['project_id'], int(df[:4]), int(df[5:7]), str(b), df, dt, h, desc,
                 row.get('source') or '', row.get('source_ref') or ''))
            created.append(cur.lastrowid)
        conn.commit()
    finally:
        conn.close()
    return {'updated': row['id'], 'created': created}


def add_pm_activity(project_id: int, year: int, month: int,
                    affected_blocks: str = '', date_from: str = '',
                    date_to: str = '', hours: float = 0.0,
                    description: str = '') -> Optional[int]:
    """Old signature, kept for existing callers. Goes through record_pm, so it
    validates and refuses a second record for a block-day; year/month come from
    the date. Returns the first record id."""
    res = record_pm(project_id, affected_blocks, date_from, date_to or None, hours,
                    description, source='desktop')
    ids = res['created'] + res['updated']
    return ids[0] if ids else None


def update_pm_activity(pm_id: int, affected_blocks: str = '', date_from: str = '',
                       date_to: str = '', hours: float = 0.0,
                       description: str = ''):
    """Old signature, kept for existing callers — see update_pm_record."""
    update_pm_record(pm_id, affected_blocks, date_from, date_to or None, hours, description)


def delete_pm_activity(pm_id: int):
    """Delete a PM record. A planner job that pointed at it stays done but no
    longer claims the record."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM pm_activities WHERE id=?", (int(pm_id),))
        conn.execute("UPDATE plan_items SET pm_activity_id=NULL WHERE pm_activity_id=?",
                     (int(pm_id),))
        conn.commit()
    finally:
        conn.close()


def pm_ledger(project_id: int, year: int, month: int) -> List[dict]:
    """The month's PM records as the report counts them, one entry per record:
    {'row', 'blocks' (named), 'counted' (blocks charged), 'duplicate_of'
    ({block: record id}), 'problems' [str]}. A block-day is charged once, to
    its first record; a record with no block or no hours is charged nothing."""
    nblk = int((get_project(project_id) or {}).get('num_blocks') or 0)
    first = {}
    out = []
    for r in get_pm_activities(project_id, year, month):
        hours = float(r.get('hours') or 0)
        blocks = pm_blocks(r.get('affected_blocks'), nblk)
        e = {'row': r, 'blocks': blocks, 'counted': [], 'duplicate_of': {}, 'problems': []}
        if not blocks:
            e['problems'].append('no block - not counted')
        if hours <= 0:
            e['problems'].append('no hours - not counted')
        elif hours > PM_MAX_HOURS:
            e['problems'].append(f'{fmt_hours(hours)} h is more than {PM_MAX_HOURS:g} h')
        for b in blocks:
            key = (b, r.get('date_from') or '')
            if key in first:
                e['duplicate_of'][b] = first[key]
            else:
                first[key] = r['id']
                if hours > 0:
                    e['counted'].append(b)
        if e['duplicate_of']:
            e['problems'].append('duplicate of ' + ', '.join(
                f"#{i} (block {b})" for b, i in sorted(e['duplicate_of'].items()))
                + ' - not counted')
        out.append(e)
    return out


def pm_as_unavailability(project_id: int, year: int, month: int) -> List[dict]:
    """The month's PM records as downtime rows the report engine counts against
    availability — one per counted block-day, whole-block scope. `pm_id` marks
    them as PM records: the Tashkent engine lets a PM record cover its own stop
    in the SCADA data (see tashkent_report_service.pm_stop_windows)."""
    out = []
    for e in pm_ledger(project_id, year, month):
        r = e['row']
        df = r.get('date_from') or ''
        dt = r.get('date_to') or df
        desc = (r.get('description') or 'PM').strip()
        for b in e['counted']:
            out.append({'block': b, 'lc': None, 'date_from': df, 'date_to': dt,
                        'downtime_h': float(r.get('hours') or 0),
                        'cause': f"PM: {desc}" if desc else "PM",
                        'subsystem': 'PM', 'pm_id': r['id']})
    return out


def pm_as_strings(project_id: int, year: int, month: int) -> List[str]:
    """The month's PM records as the report's 3.1 bullet lines — the counted
    blocks only, hours as entered (not rounded)."""
    out = []
    for e in pm_ledger(project_id, year, month):
        if not e['counted']:
            continue
        r = e['row']
        blk = ','.join(map(str, e['counted']))
        desc = r.get('description', '') or 'PM'
        out.append(f"Block(s) {blk}: {desc} ({fmt_hours(r.get('hours'))} h, "
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
