"""services/today_service.py — what the day starts with.

The seven blocks of the Today screen, each as a **list** rather than a number:
the page shows `len(...)` and its link opens exactly that list, so a counter
and the rows behind it cannot drift apart (the old Overview said "Open issues
0" with a dozen open phone records in the database).

Everything is read from services that already exist — no new tables, nothing
recomputed here:

  decisions   phone events waiting (availability_inputs_service) + sync
              conflicts (work_journal_service) + the month's open questions
              (report_precheck_service, without the SCADA-heavy edge check)
  faults      unfinished fault records of both stores
  pm          the month's PM campaign: planned / done / overdue (planner)
  month       data coverage and the four report steps (month_dataset_service,
              report_versions_service)
  phones      what is still unsent and what the server refused
  stock       items at or below their minimum for this project's warehouse
  now         what is open right now: downtime events and "needs visit"
  actions     the office's action list: overdue or due within a week
"""
import datetime
from typing import List, Optional

import services.availability_inputs_service as avi
import services.report_workflow_service as rw
import services.work_journal_service as wj

# Targets a Today row can open. The page maps them to menu items.
T_AVAILABILITY = 'availability'
T_WORK = 'work'
T_REPORT = 'report'
T_PLAN = 'plan'
T_STOCK = 'stock'


# Pre-generation questions that are decisions about an input. Missing files,
# data gaps and month-end files are progress, shown in the month block; 3.2
# records without a block are counted from the journal instead (below).
DECISION_KINDS = ('pm', 'manual', 'edge')


def _row(kind, severity, title, detail, target, ref=None, action='', flt=None):
    return {'kind': kind, 'severity': severity, 'title': title, 'detail': detail,
            'target': target, 'ref': ref, 'action': action, 'filter': flt}


def no_block_records(project_id: int, year: int, month: int,
                     today: datetime.date = None) -> List[dict]:
    """The month's records with no plant block — the same rows Work shows on
    its "No block" tab for that month."""
    d1, d2 = wj.month_range(year, month)
    return wj.filter_rows(wj.records(project_id, date_from=d1, date_to=d2,
                                     today=today), 'noblock')


# ── 1 · Needs your decision ─────────────────────────────────────────────────
def decisions(project_id: int, year: int, month: int,
              today: datetime.date = None) -> List[dict]:
    """Everything that waits for a person, most serious first. Nothing here
    changes a number by itself — each row is a question."""
    out = []
    try:
        pending = avi.pending_events(project_id, year, month)
    except Exception:                                    # noqa: BLE001
        pending = []
    for q in pending:
        out.append(_row('event', 'crit', 'Phone event waiting',
                        avi.event_summary(q['event']), T_AVAILABILITY,
                        ref=q['event_id'], action='Confirm with SCADA times'))

    for r in wj.records(project_id, today=today):
        if wj.is_conflict(r):
            out.append(_row('conflict', 'crit', 'Sync conflict',
                            f"{r['node']} · {r['title'] or r['work_done']}",
                            T_WORK, ref=r['key'], action='Resolve',
                            flt={'tab': 'conflict', 'period': 'all'}))

    # Records the customer's report cannot place: one row for the lot, and its
    # number is exactly what Work → No block shows for the month.
    no_block = no_block_records(project_id, year, month, today=today)
    if no_block:
        out.append(_row('record', 'warn',
                        f'{len(no_block)} record(s) without a plant block',
                        'they stay out of section 3.2 until a block is set',
                        T_WORK, action='Set blocks',
                        flt={'tab': 'noblock', 'period': 'month'}))

    # From the pre-generation check: only what a person must decide about an
    # input. A file not uploaded yet in the middle of the month is not a
    # decision — the month block says how much data is in.
    try:
        import services.report_precheck_service as pre
        for q in pre.run_precheck(project_id, year, month, today=today, scada=False):
            if q['kind'] not in DECISION_KINDS:
                continue
            if q['severity'] not in ('crit', 'warn'):
                continue
            out.append(_row('question', q['severity'], q['title'], q['detail'],
                            T_AVAILABILITY, ref=q.get('ref'),
                            action=q.get('action', '')))
    except Exception:                                    # noqa: BLE001
        pass

    order = {'crit': 0, 'warn': 1, 'info': 2}
    out.sort(key=lambda r: order.get(r['severity'], 9))
    return out


# ── 2 · Open faults ─────────────────────────────────────────────────────────
def open_faults(project_id: int, today: datetime.date = None) -> List[dict]:
    return wj.open_faults(project_id, today=today)


def stale_faults(rows, days: int = 7) -> List[dict]:
    return [r for r in rows if (r.get('age_days') or 0) >= days]


# ── 3 · PM campaign ─────────────────────────────────────────────────────────
def pm_progress(project_id: int, year: int, month: int,
                today: datetime.date = None) -> dict:
    """Plan against fact for the month's PM, plus what is due today."""
    today = today or datetime.date.today()
    d1, d2 = wj.month_range(year, month)
    try:
        import services.planner_service as ps
        items = ps.get_items(project_id, date_from=d1, date_to=d2)
    except Exception:                                    # noqa: BLE001
        items = []
    done = [i for i in items if (i.get('status') or '') == 'done']
    overdue = [i for i in items
               if (i.get('status') or '') != 'done'
               and (i.get('planned_date') or '9999') < today.isoformat()]
    todays = [i for i in items if (i.get('planned_date') or '') == today.isoformat()]
    try:
        pm_rows = rw.get_pm_activities(project_id, year, month)
    except Exception:                                    # noqa: BLE001
        pm_rows = []
    hours = sum(float(r.get('hours') or 0) for r in pm_rows)
    return {'items': items, 'done': done, 'overdue': overdue, 'today': todays,
            'pm_records': pm_rows, 'hours': hours,
            'blocks_done': len({(r.get('affected_blocks') or '') for r in pm_rows})}


# ── 4 · Month data and report ───────────────────────────────────────────────
def month_state(project_id: int, year: int, month: int,
                today: datetime.date = None) -> dict:
    """Coverage of the month's data set and where the four steps stand."""
    out = {'files': [], 'days_expected': 0, 'days_covered': 0, 'missing': [],
           'versions': [], 'sent': None, 'locked': False, 'questions': 0}
    try:
        import services.month_dataset_service as mds
        expected = mds.expected_days(year, month, today)
        out['days_expected'] = len(expected)
        grid = mds.coverage_grid(project_id, year, month)
        out['files'] = grid
        covered = [len([d for d in expected if d in row['days']])
                   for row in grid if row.get('file')]
        out['days_covered'] = min(covered) if covered else 0
        out['missing'] = mds.missing_required(project_id, year, month)
    except Exception:                                    # noqa: BLE001
        pass
    try:
        import services.report_versions_service as rvs
        st = rvs.month_status(project_id, year, month) or {}
        out.update(versions=rvs.list_versions(project_id, year, month),
                   sent=st.get('sent'), locked=bool(st.get('locked_at')))
    except Exception:                                    # noqa: BLE001
        pass
    return out


# ── 5 · Phones and sync ─────────────────────────────────────────────────────
def phones(project_id: int) -> dict:
    """What the phones have not handed over yet. The desktop knows what is
    unsent and what the server refused; per-device detail lives on the server."""
    from database.db_manager import get_connection
    conn = get_connection()
    try:
        waiting = conn.execute(
            "SELECT COUNT(*) FROM work_log_entries WHERE deleted_at IS NULL "
            "AND sync_status IN ('local','pending') AND (project_id=? OR project_id IS NULL)",
            (project_id,)).fetchone()[0]
        conflicts = conn.execute(
            "SELECT COUNT(*) FROM work_log_entries WHERE deleted_at IS NULL "
            "AND sync_status='conflict' AND (project_id=? OR project_id IS NULL)",
            (project_id,)).fetchone()[0]
        try:
            stuck = [dict(r) for r in conn.execute(
                "SELECT kind, item_id, error, attempts FROM sync_inbox "
                "WHERE error <> '' ORDER BY last_try DESC LIMIT 20").fetchall()]
        except Exception:                                # noqa: BLE001
            stuck = []
    finally:
        conn.close()
    last = ''
    enabled = False
    try:
        from services.sync_config import sync_config
        last = sync_config.last_sync_at or ''
        enabled = bool(sync_config.enabled and sync_config.is_configured())
    except Exception:                                    # noqa: BLE001
        pass
    return {'waiting': waiting, 'conflicts': conflicts, 'stuck': stuck,
            'last_sync': last, 'enabled': enabled}


# ── 6 · Stock below minimum ─────────────────────────────────────────────────
def low_stock(project_id: int) -> List[dict]:
    try:
        import services.stock_service as ss
        wid = ss.get_project_warehouse_id(project_id)
        names = {w['id']: w['name'] for w in ss.get_all_warehouses()}
        mine = names.get(wid)
        rows = ss.get_low_stock_items()
    except Exception:                                    # noqa: BLE001
        return []
    if mine:
        rows = [r for r in rows if r.get('warehouse') == mine]
    return rows


# ── 7 · Happening now ───────────────────────────────────────────────────────
def happening_now(project_id: int, year: int, month: int,
                  today: datetime.date = None) -> List[dict]:
    """Open downtime events and the records that say someone must go out."""
    out = []
    try:
        for q in avi.pending_events(project_id, year, month):
            ev = q['event']
            if (q.get('kind') or ev.get('kind')) == 'pm':
                continue
            out.append(_row('event', 'crit', avi.event_summary(ev),
                            'Sent from a phone · waiting for your confirmation',
                            T_AVAILABILITY, ref=q['event_id']))
    except Exception:                                    # noqa: BLE001
        pass
    for r in wj.open_faults(project_id, today=today):
        if r['status'] == 'Needs visit' or (r.get('age_days') or 0) == 0:
            out.append(_row('fault', 'warn' if r['status'] != 'Needs visit' else 'crit',
                            f"{r['node']} · {r['title'] or r['work_done']}",
                            f"{r['status']} · {r['date']}", T_WORK, ref=r['key']))
    return out


# ── 8 · Action list ─────────────────────────────────────────────────────────
def actions(project_id: int, today: datetime.date = None,
            days: int = 7) -> List[dict]:
    """Open action items already overdue or due within a week — the
    organisational list, not plant work. One call into the service that owns
    the table, and the Plan page's own filter calls the same one, so the count
    here is exactly the list its link opens."""
    try:
        import services.action_list_service as als
        return als.due_soon(project_id, today=today, days=days)
    except Exception:                                    # noqa: BLE001
        return []


def summary(project_id: int, year: int, month: int,
            today: datetime.date = None) -> dict:
    """Everything the Today page draws, in one call."""
    faults = open_faults(project_id, today=today)
    return {
        'actions': actions(project_id, today=today),
        'decisions': decisions(project_id, year, month, today=today),
        'faults': faults,
        'stale': stale_faults(faults),
        'pm': pm_progress(project_id, year, month, today=today),
        'month': month_state(project_id, year, month, today=today),
        'phones': phones(project_id),
        'stock': low_stock(project_id),
        'now': happening_now(project_id, year, month, today=today),
    }
