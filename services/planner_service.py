"""
services/planner_service.py
----------------------------
The forward half of the app: what we intend to do, and what actually happened.

Everything else here records the past — an alarm fired, a report was written,
a month was closed. Planning had no home, so the monthly report's PM hours
were retyped by hand every month, which is how an August exclusion ended up
with an empty block list and inflated PM from 1.94 h to 5.90 h.

Two ideas carry this module:

  1. **PM is a campaign, not a calendar.** August 2026 shows one or two blocks
     a day rolling across the plant — block 2 on the 1st, block 3 on the 2nd,
     blocks 31-32 on the 27th — a full round of 70 blocks taking about three
     months. So the schedule is *generated* from a campaign; nobody types 70
     rows.
  2. **Completing a job writes the record the report reads.** Closing a PM
     item with its actual date and hours creates (or updates) the
     `pm_activities` row for that month. The plan stops being a separate list
     and becomes the source.
"""

import calendar
import datetime
import re
from typing import List, Optional

from database.db_manager import get_connection

STATUSES = ('planned', 'in_progress', 'done', 'skipped', 'moved')
DONE = 'done'


# ── Work types ───────────────────────────────────────────────────────────────

def get_types(project_id: Optional[int] = None) -> List[dict]:
    """Built-in types plus any this project added (commissioning, say)."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM plan_item_types
            WHERE project_id IS NULL OR project_id = ?
            ORDER BY sort_order, label
        """, (project_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def add_type(project_id: int, code: str, label: str,
             counts_as_downtime: bool = False, sort_order: int = 50) -> int:
    code = re.sub(r'[^a-z0-9_]+', '_', (code or '').strip().lower()).strip('_')
    if not code:
        raise ValueError('A type needs a code.')
    conn = get_connection()
    try:
        cur = conn.execute("""
            INSERT OR IGNORE INTO plan_item_types
                (project_id, code, label, counts_as_downtime, sort_order)
            VALUES (?, ?, ?, ?, ?)
        """, (project_id, code, label or code, 1 if counts_as_downtime else 0,
              sort_order))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _type_counts_downtime(project_id: int, code: str) -> bool:
    for t in get_types(project_id):
        if t['code'] == code:
            return bool(t['counts_as_downtime'])
    return False


# ── Plans ────────────────────────────────────────────────────────────────────

def create_plan(project_id: int, title: str, kind: str = 'campaign',
                date_from: str = None, date_to: str = None,
                notes: str = '') -> int:
    conn = get_connection()
    try:
        cur = conn.execute("""
            INSERT INTO work_plans (project_id, title, kind, date_from, date_to, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (project_id, title, kind, date_from, date_to, notes))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_plans(project_id: int, status: str = None) -> List[dict]:
    conn = get_connection()
    try:
        q = ("SELECT p.*, "
             "  (SELECT COUNT(*) FROM plan_items i WHERE i.plan_id = p.id) AS n_items, "
             "  (SELECT COUNT(*) FROM plan_items i WHERE i.plan_id = p.id "
             "     AND i.status = 'done') AS n_done "
             "FROM work_plans p WHERE p.project_id = ?")
        params = [project_id]
        if status:
            q += " AND p.status = ?"; params.append(status)
        q += " ORDER BY p.date_from DESC, p.id DESC"
        return [dict(r) for r in conn.execute(q, params)]
    finally:
        conn.close()


def delete_plan(plan_id: int, delete_items: bool = False):
    conn = get_connection()
    try:
        if delete_items:
            conn.execute("DELETE FROM plan_items WHERE plan_id = ?", (plan_id,))
        else:
            conn.execute("UPDATE plan_items SET plan_id = NULL WHERE plan_id = ?",
                         (plan_id,))
        conn.execute("DELETE FROM work_plans WHERE id = ?", (plan_id,))
        conn.commit()
    finally:
        conn.close()


# ── Items ────────────────────────────────────────────────────────────────────

_ITEM_FIELDS = ('plan_id', 'type_code', 'title', 'description', 'block', 'lc',
                'asset_code', 'planned_date', 'planned_hours', 'assignee',
                'priority', 'status', 'actual_date', 'actual_hours',
                'actual_notes', 'moved_from', 'alarm_event_id', 'work_log_id')


def add_item(project_id: int, **fields) -> int:
    data = {k: v for k, v in fields.items() if k in _ITEM_FIELDS}
    data['project_id'] = project_id
    cols = ', '.join(data)
    marks = ', '.join('?' for _ in data)
    conn = get_connection()
    try:
        cur = conn.execute(f"INSERT INTO plan_items ({cols}) VALUES ({marks})",
                           list(data.values()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_item(item_id: int, **fields):
    data = {k: v for k, v in fields.items() if k in _ITEM_FIELDS}
    if not data:
        return
    sets = ', '.join(f'{k}=?' for k in data)
    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE plan_items SET {sets}, updated_at=datetime('now') WHERE id=?",
            [*data.values(), item_id])
        conn.commit()
    finally:
        conn.close()


def get_item(item_id: int) -> Optional[dict]:
    conn = get_connection()
    try:
        r = conn.execute("SELECT * FROM plan_items WHERE id=?", (item_id,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def delete_item(item_id: int):
    """Removes the job and any downtime row it wrote."""
    it = get_item(item_id)
    conn = get_connection()
    try:
        if it and it.get('pm_activity_id'):
            conn.execute("DELETE FROM pm_activities WHERE id=?",
                         (it['pm_activity_id'],))
        conn.execute("DELETE FROM plan_items WHERE id=?", (item_id,))
        conn.commit()
    finally:
        conn.close()


def get_items(project_id: int, date_from: str = None, date_to: str = None,
              type_code: str = None, status: str = None, plan_id: int = None,
              block: int = None, include_undated: bool = False) -> List[dict]:
    """Jobs, newest date first. The month view, the day view and a plan's
    contents are all this one query with different bounds."""
    q = ["SELECT i.*, t.label AS type_label, t.counts_as_downtime, p.title AS plan_title",
         "FROM plan_items i",
         "LEFT JOIN plan_item_types t ON t.code = i.type_code",
         "  AND (t.project_id IS NULL OR t.project_id = i.project_id)",
         "LEFT JOIN work_plans p ON p.id = i.plan_id",
         "WHERE i.project_id = ?"]
    params = [project_id]
    if date_from and date_to:
        if include_undated:
            q.append("AND (i.planned_date IS NULL OR i.planned_date = ''"
                     "     OR i.planned_date BETWEEN ? AND ?)")
        else:
            q.append("AND i.planned_date BETWEEN ? AND ?")
        params += [date_from, date_to]
    elif date_from:
        q.append("AND i.planned_date >= ?"); params.append(date_from)
    elif date_to:
        q.append("AND i.planned_date <= ?"); params.append(date_to)
    if type_code:
        q.append("AND i.type_code = ?"); params.append(type_code)
    if status:
        q.append("AND i.status = ?"); params.append(status)
    if plan_id is not None:
        q.append("AND i.plan_id = ?"); params.append(plan_id)
    if block is not None:
        q.append("AND i.block = ?"); params.append(block)
    q.append("ORDER BY i.planned_date, i.priority, i.block, i.id")

    conn = get_connection()
    try:
        rows = conn.execute('\n'.join(q), params).fetchall()
        # de-duplicate the type join: a project-specific type shadows a builtin
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Doing the work ───────────────────────────────────────────────────────────

def complete_item(item_id: int, actual_date: str = None,
                  actual_hours: float = None, notes: str = '') -> dict:
    """Mark a job done — and, when its type counts as downtime, write the
    `pm_activities` row the monthly report reads.

    Re-completing the same job updates the row it already wrote rather than
    adding a second one, so correcting an entry cannot double-charge
    availability.
    """
    it = get_item(item_id)
    if not it:
        raise ValueError(f'No plan item {item_id}')
    actual_date = actual_date or it.get('planned_date') or _today()
    if actual_hours is None:
        actual_hours = it.get('planned_hours') or 0

    conn = get_connection()
    try:
        pm_id = it.get('pm_activity_id')
        wrote_downtime = False
        if _type_counts_downtime(it['project_id'], it['type_code']) and actual_hours > 0:
            y, m = int(actual_date[:4]), int(actual_date[5:7])
            blocks = str(it['block']) if it.get('block') else ''
            desc = (it.get('title') or '').strip() or 'Planned maintenance'
            if pm_id:
                conn.execute("""
                    UPDATE pm_activities
                       SET year=?, month=?, affected_blocks=?, date_from=?,
                           date_to=?, hours=?, description=?
                     WHERE id=?
                """, (y, m, blocks, actual_date, actual_date,
                      float(actual_hours), desc, pm_id))
            else:
                cur = conn.execute("""
                    INSERT INTO pm_activities
                        (project_id, year, month, affected_blocks, date_from,
                         date_to, hours, description)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (it['project_id'], y, m, blocks, actual_date, actual_date,
                      float(actual_hours), desc))
                pm_id = cur.lastrowid
            wrote_downtime = True

        conn.execute("""
            UPDATE plan_items
               SET status='done', actual_date=?, actual_hours=?, actual_notes=?,
                   pm_activity_id=?, updated_at=datetime('now')
             WHERE id=?
        """, (actual_date, float(actual_hours), notes or it.get('actual_notes') or '',
              pm_id, item_id))
        conn.commit()
    finally:
        conn.close()
    return {'item_id': item_id, 'actual_date': actual_date,
            'actual_hours': float(actual_hours),
            'pm_activity_id': pm_id, 'wrote_downtime': wrote_downtime}


def reopen_item(item_id: int):
    """Undo a completion, taking its downtime row with it."""
    it = get_item(item_id)
    conn = get_connection()
    try:
        if it and it.get('pm_activity_id'):
            conn.execute("DELETE FROM pm_activities WHERE id=?",
                         (it['pm_activity_id'],))
        conn.execute("""
            UPDATE plan_items
               SET status='planned', actual_date=NULL, actual_hours=NULL,
                   pm_activity_id=NULL, updated_at=datetime('now')
             WHERE id=?
        """, (item_id,))
        conn.commit()
    finally:
        conn.close()


def move_item(item_id: int, new_date: str):
    """Reschedule, keeping the date it was first meant for. A job that slips
    must stay visible — deleting it is how plan and actual quietly diverge."""
    it = get_item(item_id)
    if not it:
        return
    first = it.get('moved_from') or it.get('planned_date') or ''
    conn = get_connection()
    try:
        conn.execute("""
            UPDATE plan_items
               SET planned_date=?, moved_from=?, updated_at=datetime('now')
             WHERE id=?
        """, (new_date, first, item_id))
        conn.commit()
    finally:
        conn.close()


# ── Generating a PM campaign ─────────────────────────────────────────────────

def _today() -> str:
    return datetime.date.today().isoformat()


def generate_pm_campaign(project_id: int, title: str, blocks: List[int],
                         start_date: str, blocks_per_day: int = 2,
                         hours_per_block: float = 4.0,
                         skip_weekends: bool = True,
                         type_code: str = 'pm',
                         assignee: str = '') -> dict:
    """Roll a list of blocks out across working days and create the jobs.

    This is how the plant is actually maintained: a couple of blocks a day
    until the round is complete. Returns the plan id and the dates covered.
    """
    blocks = [int(b) for b in blocks if b is not None]
    if not blocks:
        raise ValueError('No blocks given.')
    per_day = max(1, int(blocks_per_day))

    d = datetime.date.fromisoformat(start_date)
    schedule = []
    i = 0
    while i < len(blocks):
        if skip_weekends and d.weekday() >= 5:
            d += datetime.timedelta(days=1)
            continue
        for b in blocks[i:i + per_day]:
            schedule.append((d.isoformat(), b))
        i += per_day
        d += datetime.timedelta(days=1)

    last_day = schedule[-1][0] if schedule else start_date
    plan_id = create_plan(project_id, title, kind='campaign',
                          date_from=start_date, date_to=last_day,
                          notes=f'{len(blocks)} block(s), {per_day}/day')

    conn = get_connection()
    try:
        conn.executemany("""
            INSERT INTO plan_items
                (project_id, plan_id, type_code, title, block, planned_date,
                 planned_hours, assignee, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'planned')
        """, [(project_id, plan_id, type_code, f'{title} — block {b}', b,
               day, float(hours_per_block), assignee) for day, b in schedule])
        conn.commit()
    finally:
        conn.close()
    return {'plan_id': plan_id, 'items': len(schedule),
            'date_from': start_date, 'date_to': last_day}


# ── What is due ──────────────────────────────────────────────────────────────

def last_pm_by_block(project_id: int) -> dict:
    """{block: last PM date} from completed plan items and the PM history."""
    conn = get_connection()
    try:
        out = {}
        for r in conn.execute("""
            SELECT block, MAX(actual_date) AS d FROM plan_items
             WHERE project_id=? AND status='done' AND block IS NOT NULL
               AND type_code='pm' AND actual_date IS NOT NULL
             GROUP BY block
        """, (project_id,)):
            out[int(r['block'])] = r['d']
        # Two older homes for the same fact, both of which predate the planner
        # and list blocks as csv:
        #   pm_activities        — PM entered for the monthly report
        #   availability_exclusions of type 'Scheduled Maintenance' — which is
        #     where August 2026's PM actually lives (21 rows, blocks 2-32).
        # Miss the second and the due-list claims 65 blocks have never been
        # maintained, which is both wrong and useless.
        for sql, params in (
            ("SELECT affected_blocks, date_from FROM pm_activities "
             " WHERE project_id=? AND date_from IS NOT NULL", (project_id,)),
            ("SELECT affected_blocks, date_from FROM availability_exclusions "
             " WHERE (project_id=? OR project_id IS NULL) "
             "   AND exclusion_type='Scheduled Maintenance' "
             "   AND date_from IS NOT NULL", (project_id,)),
        ):
            for r in conn.execute(sql, params):
                for b in _parse_blocks(r['affected_blocks']):
                    if b not in out or (r['date_from'] or '') > out[b]:
                        out[b] = r['date_from']
        return out
    finally:
        conn.close()


def pm_due(project_id: int, all_blocks: List[int], interval_days: int = 90,
           as_of: str = None) -> List[dict]:
    """Blocks ordered by how overdue they are — the sensible start of a
    campaign, instead of always beginning at block 1."""
    as_of_d = datetime.date.fromisoformat(as_of or _today())
    last = last_pm_by_block(project_id)
    out = []
    for b in all_blocks:
        d = last.get(int(b))
        if d:
            try:
                age = (as_of_d - datetime.date.fromisoformat(str(d)[:10])).days
            except ValueError:
                age = None
        else:
            age = None
        out.append({
            'block': int(b), 'last_pm': d, 'days_since': age,
            'overdue_by': (age - interval_days) if age is not None else None,
            'never': d is None,
        })
    out.sort(key=lambda r: (-(r['days_since'] if r['days_since'] is not None else 10**6),
                            r['block']))
    return out


def plan_availability_cost(project_id: int, year: int, month: int,
                           plant_capacity_mwh: float = 770.56,
                           block_capacity_mwh: float = 11.008) -> dict:
    """What this month's plan will cost in availability, before it is worked.

    PM counts as unavailability here, so a month's plan is a known loss — the
    figure that gets argued over when the plan is signed off.
    """
    last_day = calendar.monthrange(int(year), int(month))[1]
    d0 = f'{int(year):04d}-{int(month):02d}-01'
    d1 = f'{int(year):04d}-{int(month):02d}-{last_day:02d}'
    total_hours = last_day * 24.0

    planned = done = 0.0
    for it in get_items(project_id, date_from=d0, date_to=d1):
        if not it.get('counts_as_downtime'):
            continue
        h = float(it.get('planned_hours') or 0)
        planned += h
        if it['status'] == DONE:
            done += float(it.get('actual_hours') or h)
    energy_hours = (planned + 0.0) * block_capacity_mwh
    pct = (energy_hours / (total_hours * plant_capacity_mwh) * 100.0) if total_hours else 0.0
    done_pct = (done * block_capacity_mwh / (total_hours * plant_capacity_mwh) * 100.0)
    return {'planned_hours': round(planned, 2), 'actual_hours': round(done, 2),
            'planned_cost_pct': round(pct, 4), 'actual_cost_pct': round(done_pct, 4),
            'total_hours': total_hours}


# ── Excel in, Excel out ──────────────────────────────────────────────────────

# Header text people actually use, mapped to our fields. Matching is done on
# lowercase words so "Block No.", "block number" and "Блок" all land.
_IMPORT_ALIASES = {
    'block':         ('block', 'блок', 'block no', 'block number', 'cabin'),
    'planned_date':  ('date', 'planned date', 'plan date', 'дата', 'план'),
    'planned_hours': ('hours', 'planned hours', 'duration', 'часы', 'время'),
    'title':         ('task', 'title', 'work', 'description', 'activity',
                      'работа', 'задача', 'описание'),
    'type_code':     ('type', 'kind', 'тип'),
    'assignee':      ('assignee', 'engineer', 'responsible', 'исполнитель'),
    'actual_date':   ('actual date', 'done date', 'completed', 'факт дата',
                      'дата выполнения'),
    'actual_hours':  ('actual hours', 'actual time', 'факт часы',
                      'время выполнения'),
}


def _norm_header(h) -> str:
    return re.sub(r'[^a-zа-я0-9 ]+', ' ', str(h or '').strip().lower()).strip()


def preview_excel(path: str, sheet=0) -> dict:
    """Read the sheet and guess which column is which, without writing
    anything. The caller shows the guess and lets the user correct it."""
    import pandas as pd
    df = pd.read_excel(path, sheet_name=sheet)
    df.columns = [str(c) for c in df.columns]
    guess = {}
    for col in df.columns:
        n = _norm_header(col)
        for field, aliases in _IMPORT_ALIASES.items():
            if field in guess.values():
                continue
            if n in aliases or any(a == n for a in aliases):
                guess[col] = field
                break
    # second pass: substring match for anything still unclaimed
    for col in df.columns:
        if col in guess:
            continue
        n = _norm_header(col)
        for field, aliases in _IMPORT_ALIASES.items():
            if field in guess.values():
                continue
            if any(a in n for a in aliases):
                guess[col] = field
                break
    return {'columns': list(df.columns), 'mapping': guess,
            'rows': len(df), 'preview': df.head(8).fillna('').astype(str).to_dict('records')}


def import_from_excel(project_id: int, path: str, mapping: dict,
                      plan_title: str = None, default_type: str = 'pm',
                      sheet=0, log=print) -> dict:
    """Bring an existing plan in from a spreadsheet.

    `mapping` is {excel column: our field} — from preview_excel, corrected by
    the user. Rows without a usable date or block are reported, not guessed at.
    """
    import pandas as pd
    df = pd.read_excel(path, sheet_name=sheet)
    df.columns = [str(c) for c in df.columns]
    inv = {v: k for k, v in (mapping or {}).items()}

    plan_id = None
    if plan_title:
        plan_id = create_plan(project_id, plan_title, kind='month')

    known_types = {t['code'] for t in get_types(project_id)}
    made, skipped, problems = 0, 0, []

    def _cell(row, field):
        col = inv.get(field)
        if not col or col not in df.columns:
            return None
        v = row.get(col)
        return None if pd.isna(v) else v

    # Both of these must return None, never NaN: a spacer row full of blanks
    # reads as NaN, and `nan is None` is False, so it would sail through the
    # "is this row empty?" check and be imported as a job.
    def _date(v):
        if v is None:
            return None
        try:
            ts = pd.to_datetime(v, errors='coerce')
        except Exception:                             # noqa: BLE001
            return None
        if ts is None or pd.isna(ts):
            return None
        return ts.date().isoformat()

    def _num(v):
        if v is None:
            return None
        n = pd.to_numeric(v, errors='coerce')
        return None if pd.isna(n) else float(n)

    rows = []
    for n, row in df.iterrows():
        block = _num(_cell(row, 'block'))
        pdate = _date(_cell(row, 'planned_date'))
        title = _cell(row, 'title')
        if block is None and pdate is None and not title:
            skipped += 1                              # blank / spacer row
            continue
        if pdate is None:
            problems.append(f'row {n + 2}: no usable date')
            continue
        tcode = str(_cell(row, 'type_code') or default_type).strip().lower()
        if tcode not in known_types:
            tcode = default_type
        adate = _date(_cell(row, 'actual_date'))
        ahours = _num(_cell(row, 'actual_hours'))
        rows.append({
            'project_id': project_id, 'plan_id': plan_id, 'type_code': tcode,
            'title': str(title or '').strip() or (
                f'PM — block {int(block)}' if block else 'Planned work'),
            'block': int(block) if block is not None else None,
            'planned_date': pdate,
            'planned_hours': _num(_cell(row, 'planned_hours')) or 0.0,
            'assignee': str(_cell(row, 'assignee') or '').strip(),
            'actual_date': adate, 'actual_hours': ahours,
        })

    # Insert as planned, then close the ones that arrived already done through
    # complete_item — so an imported completion writes its downtime row exactly
    # like one ticked off by hand. Setting status='done' directly here would
    # leave the monthly report blind to it.
    conn = get_connection()
    try:
        ids = []
        for r in rows:
            cur = conn.execute("""
                INSERT INTO plan_items
                    (project_id, plan_id, type_code, title, block, planned_date,
                     planned_hours, assignee, status)
                VALUES (:project_id, :plan_id, :type_code, :title, :block,
                        :planned_date, :planned_hours, :assignee, 'planned')
            """, r)
            ids.append(cur.lastrowid)
        conn.commit()
        made = len(rows)
    finally:
        conn.close()

    completed = 0
    for item_id, r in zip(ids, rows):
        if r['actual_date'] or r['actual_hours'] is not None:
            try:
                complete_item(item_id, actual_date=r['actual_date'],
                              actual_hours=r['actual_hours'])
                completed += 1
            except Exception as e:                    # noqa: BLE001
                problems.append(f"{r['title']}: could not record completion — {e}")

    log(f'Imported {made} job(s), {completed} already done; '
        f'{skipped} blank row(s); {len(problems)} problem(s).')
    return {'imported': made, 'completed': completed, 'skipped': skipped,
            'problems': problems, 'plan_id': plan_id}


def export_customer_excel(project_id: int, year: int, month: int, path: str,
                          type_code: str = 'pm') -> dict:
    """The sheet that goes to the customer: block, date, hours. Nothing else.

    Everything else the planner holds — assignees, priorities, what slipped and
    why — is ours, and stays here.
    """
    import pandas as pd
    last_day = calendar.monthrange(int(year), int(month))[1]
    d0 = f'{int(year):04d}-{int(month):02d}-01'
    d1 = f'{int(year):04d}-{int(month):02d}-{last_day:02d}'

    rows = []
    for it in get_items(project_id, date_from=d0, date_to=d1,
                        type_code=type_code, status=DONE):
        rows.append({
            'Block': it.get('block'),
            'Date': (it.get('actual_date') or it.get('planned_date') or '')[:10],
            'Duration (h)': it.get('actual_hours') if it.get('actual_hours') is not None
                            else it.get('planned_hours'),
        })
    df = pd.DataFrame(rows, columns=['Block', 'Date', 'Duration (h)'])
    df = df.sort_values(['Date', 'Block'], na_position='last')

    with pd.ExcelWriter(path, engine='openpyxl') as xl:
        df.to_excel(xl, sheet_name='PM', index=False)
        ws = xl.sheets['PM']
        for col, width in zip('ABC', (10, 14, 14)):
            ws.column_dimensions[col].width = width
    return {'rows': len(df), 'path': path,
            'total_hours': round(float(pd.to_numeric(
                df['Duration (h)'], errors='coerce').fillna(0).sum()), 2)}


def _parse_blocks(spec) -> List[int]:
    """'1,2,5-7' → [1,2,5,6,7]. Empty means the whole plant, which we cannot
    attribute to one block, so it yields nothing."""
    s = (spec or '').strip()
    if not s or s.lower() in ('all', 'all blocks'):
        return []
    out = []
    for tok in s.split(','):
        tok = tok.strip()
        if not tok:
            continue
        try:
            if '-' in tok:
                a, z = (int(x) for x in tok.split('-', 1))
                out.extend(range(min(a, z), max(a, z) + 1))
            else:
                out.append(int(tok))
        except ValueError:
            continue
    return sorted(set(out))
