"""services/availability_inputs_service.py — the month's availability inputs in one place

Everything that moves a project-month's availability besides the SCADA data:
PM records (whatever wrote them), manual downtime, and the phone events still
waiting for the desktop. The Monthly Reports page's "Availability inputs" tab
is a view over `month_inputs`; the sync client hands every pulled phone event
to `route_field_event`.

Phone events, by kind:
  pm        applied straight away through report_workflow_service.record_pm
            (one record per block-day, source 'phone', keyed on the event id so
            a re-pull cannot add a second row). One that cannot be applied as
            it is — no block, hours out of range, a bad date, or a PM record
            already on that block-day — waits in field_event_queue instead.
  counts    always waits. Applied as manual downtime only when confirmed on the
            desktop with real blocks, dates and hours.
  excluded  always waits. Applied as an exclusion window only when confirmed
            with real start and end times and blocks.
They used to be applied on arrival with invented times: a 3-h grid outage
became a window from 00:00 to 03:00 the next day on all 70 blocks, and junk in
the blocks field became block 0.

Nothing in field_event_queue counts towards availability. Only rows in
pm_activities / manual_unavailability / availability_exclusions do.
"""

import datetime
import json
import re
from typing import List, Optional

from database.db_manager import get_connection
import services.report_workflow_service as rw
import services.availability_service as av

SOURCE_LABELS = {
    'phone': '📱 phone', 'planner': '🗓 planner', 'import': '📄 import',
    'desktop': '🖥 desktop', 'work_report': '🖥 work report',
}
KINDS = ('pm', 'counts', 'excluded')
_HHMM = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')


class InputValidationError(ValueError):
    """A downtime or exclusion entry that must not be saved; `problems` says why."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__('; '.join(self.problems))


# ── Validation shared by manual downtime and exclusion windows ───────────────

def _blocks(project_id: int, spec, problems: list) -> List[int]:
    """Plant blocks named by `spec`; an explicit 'all' is the whole plant, an
    empty field is an error, and so is anything that is not a block number."""
    n = int((rw.get_project(project_id) or {}).get('num_blocks') or 0)
    s = str(spec if spec is not None else '').strip()
    if not s:
        problems.append("a block is required (choose 'all' explicitly for the whole plant)")
        return []
    if s.lower() in ('all', 'all blocks'):
        if not n:
            problems.append("the project has no block count - list the blocks")
        return list(range(1, n + 1))
    bad = [t.strip() for t in s.split(',') if t.strip() and not rw._BLOCK_TOKEN.match(t)]
    if bad:
        problems.append("not a block number: " + ', '.join(repr(t) for t in bad[:3]))
    got = av.parse_block_spec(s) or set()
    outside = sorted(b for b in got if b < 1 or (n and b > n))
    if outside:
        problems.append("not a block of this plant (1..{}): {}".format(
            n or '?', ', '.join(map(str, outside[:5]))))
    out = sorted(set(got) - set(outside))
    if not out and not bad and not outside:
        problems.append("a block is required")
    return out


def _dates(date_from, date_to, problems: list, today=None):
    today = today or datetime.date.today()
    latest = today + datetime.timedelta(days=1)
    earliest = datetime.date.fromisoformat(rw.PM_EARLIEST_DATE)
    d1 = rw._iso_date(date_from)
    d2 = rw._iso_date(date_to) if str(date_to or '').strip() else d1
    if d1 is None:
        problems.append("the date is missing or not a date (YYYY-MM-DD)")
        return None, None
    if not (earliest <= d1 <= latest):
        problems.append(f"date {d1} is outside {earliest} .. {latest}")
    if d2 is None:
        problems.append("the end date is not a date (YYYY-MM-DD)")
    elif d2 < d1:
        problems.append("the end date is before the start date")
    elif d2 > latest:
        problems.append(f"end date {d2} is after {latest}")
    return d1, d2


def validate_manual(project_id: int, blocks, date_from, date_to, hours,
                    lc=None, time_from: str = '', time_to: str = '',
                    today=None) -> dict:
    """Manual downtime: blocks of this plant, sane dates, 0 < hours <= 24 per
    day of the entry, LC 1/2 or whole block, and HH:MM times that do not run
    backwards. Returns {blocks, date_from, date_to, hours, lc}."""
    if not rw.get_project(project_id):
        raise RuntimeError(f"project {project_id} is not on this desktop yet")
    problems = []
    blks = _blocks(project_id, blocks, problems)
    d1, d2 = _dates(date_from, date_to, problems, today)
    try:
        h = float(hours)
    except (TypeError, ValueError):
        h = None
    days = ((d2 - d1).days + 1) if (d1 and d2 and d2 >= d1) else 1
    if h is None or not (0 < h <= 24.0 * days):
        problems.append(f"hours must be more than 0 and at most {24 * days} "
                        f"(got {hours if hours not in (None, '') else 'nothing'})")
    if lc not in (None, '', 1, 2, '1', '2'):
        problems.append(f"LC must be 1, 2 or empty for the whole block (got {lc!r})")
    for t in (time_from, time_to):
        if t and not _HHMM.match(str(t)):
            problems.append(f"time {t!r} is not HH:MM")
    if (time_from and time_to and d1 and d2 and d1 == d2
            and _HHMM.match(str(time_from)) and _HHMM.match(str(time_to))
            and str(time_to) <= str(time_from)):
        problems.append("the end time is not after the start time")
    if problems:
        raise InputValidationError(problems)
    return {'blocks': blks, 'date_from': d1.isoformat(), 'date_to': d2.isoformat(),
            'hours': h, 'lc': int(lc) if lc not in (None, '') else None}


def record_manual(project_id: int, blocks, date_from, date_to, hours, lc=None,
                  cause: str = '', time_from: str = '', time_to: str = '',
                  source: str = 'desktop', today=None) -> List[int]:
    """Validated manual downtime, one row per block. Returns the new ids."""
    v = validate_manual(project_id, blocks, date_from, date_to, hours, lc,
                        time_from, time_to, today)
    return [av.add_manual_unavailability(
                block=b, date_from=v['date_from'], date_to=v['date_to'],
                downtime_h=v['hours'], lc=v['lc'],
                cause=(cause or '').strip() or 'Operator-reported',
                project_id=project_id, time_from=time_from or '',
                time_to=time_to or '', source=source)
            for b in v['blocks']]


def update_manual(entry_id: int, project_id: int, block, date_from, date_to,
                  hours, lc=None, cause: str = '', time_from: str = '',
                  time_to: str = '', today=None):
    """Edit one manual downtime row with the same rules (exactly one block)."""
    v = validate_manual(project_id, block, date_from, date_to, hours, lc,
                        time_from, time_to, today)
    if len(v['blocks']) != 1:
        raise InputValidationError(["one row is one block - add the other blocks as new rows"])
    av.update_manual_unavailability(
        entry_id, block=v['blocks'][0], date_from=v['date_from'], date_to=v['date_to'],
        downtime_h=v['hours'], lc=v['lc'], cause=(cause or '').strip() or 'Operator-reported',
        time_from=time_from or '', time_to=time_to or '')


def validate_window(project_id: int, exclusion_type: str, date_from, time_from,
                    date_to, time_to, affected_blocks, today=None) -> dict:
    """An exclusion window: a known type, blocks (whole plant only as an
    explicit 'all'), and an end after its start."""
    problems = []
    if exclusion_type not in av.EXCLUSION_TYPES:
        problems.append(f"unknown exclusion type {exclusion_type!r}")
    blks = _blocks(project_id, affected_blocks, problems)
    d1, d2 = _dates(date_from, date_to, problems, today)
    for t in (time_from, time_to):
        if not _HHMM.match(str(t or '')):
            problems.append(f"time {t!r} is not HH:MM")
    if d1 and d2 and not problems:
        if f"{d2} {time_to}" <= f"{d1} {time_from}":
            problems.append("the window ends before it starts")
    if problems:
        raise InputValidationError(problems)
    spec = str(affected_blocks).strip()
    return {'affected_blocks': 'all' if spec.lower() in ('all', 'all blocks')
            else ','.join(map(str, blks)),
            'date_from': d1.isoformat(), 'date_to': d2.isoformat()}


# ── Phone events ─────────────────────────────────────────────────────────────

def _event_seen(event_id: str) -> bool:
    conn = get_connection()
    try:
        return (conn.execute("SELECT 1 FROM synced_field_events WHERE event_id=?",
                             (event_id,)).fetchone() is not None
                or conn.execute("SELECT 1 FROM field_event_queue WHERE event_id=?",
                                (event_id,)).fetchone() is not None)
    finally:
        conn.close()


def _mark_seen(event_id: str, kind: str):
    conn = get_connection()
    try:
        conn.execute("INSERT OR IGNORE INTO synced_field_events (event_id, kind) VALUES (?, ?)",
                     (event_id, kind or ''))
        conn.commit()
    finally:
        conn.close()


def _queue(ev: dict, reason: str, note: str = ''):
    y, m = av._report_month_of(ev.get('date_from'))
    conn = get_connection()
    try:
        conn.execute("""
            INSERT OR IGNORE INTO field_event_queue
                (event_id, project_id, kind, year, month, payload, reason, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (str(ev['id']), ev.get('project_id'), ev.get('kind') or '', y, m,
              json.dumps(ev, default=str), reason, (note or '')[:500]))
        conn.commit()
    finally:
        conn.close()


def route_field_event(ev: dict) -> str:
    """Take one phone event from the server. Returns 'applied' (a PM record was
    written), 'queued' (waits for the desktop), 'seen' (handled before) or
    'ignored' (a kind this desktop does not know). Raises when it cannot be
    handled yet — the project is not on this desktop — so the sync inbox keeps
    it and retries.

    Old phone builds send exactly the same payload; nothing new is required of
    them. Their PM events without a block, or with 0 hours, now wait instead of
    charging the whole plant or nothing."""
    eid = str(ev['id'])
    if _event_seen(eid):
        return 'seen'
    kind = ev.get('kind')
    if kind not in KINDS:
        return 'ignored'
    pid = ev.get('project_id')
    if not rw.get_project(pid):
        raise RuntimeError(f"project {pid} is not on this desktop yet")
    if kind == 'pm':
        try:
            rw.record_pm(pid, ev.get('blocks'), ev.get('date_from'), ev.get('date_to'),
                         ev.get('hours'), ev.get('description') or '',
                         source='phone', source_ref=eid)
            _mark_seen(eid, kind)
            return 'applied'
        except rw.PMDuplicateError as ex:
            _queue(ev, 'duplicate', str(ex))
        except rw.PMValidationError as ex:
            _queue(ev, 'invalid', str(ex))
    else:
        _queue(ev, 'confirm', '')
    _mark_seen(eid, kind)
    return 'queued'


def get_queued_event(event_id: str) -> Optional[dict]:
    conn = get_connection()
    try:
        r = conn.execute("SELECT * FROM field_event_queue WHERE event_id=?",
                         (str(event_id),)).fetchone()
    finally:
        conn.close()
    if not r:
        return None
    d = dict(r)
    try:
        d['event'] = json.loads(d.get('payload') or '{}')
    except ValueError:
        d['event'] = {}
    return d


def pending_events(project_id: int, year: int = None, month: int = None,
                   status: str = 'pending') -> List[dict]:
    """Queued phone events of a project-month (an undated one shows in every
    month rather than nowhere), oldest first, each with its `event` dict."""
    q = "SELECT event_id FROM field_event_queue WHERE project_id=?"
    p = [project_id]
    if status:
        q += " AND status=?"; p.append(status)
    if year is not None:
        q += " AND (year=? OR year IS NULL)"; p.append(year)
    if month is not None:
        q += " AND (month=? OR month IS NULL)"; p.append(month)
    q += " ORDER BY received_at, event_id"
    conn = get_connection()
    try:
        ids = [r[0] for r in conn.execute(q, p).fetchall()]
    finally:
        conn.close()
    return [get_queued_event(i) for i in ids]


def _resolve(event_id: str, status: str, applied_ref: str = '', note: str = None):
    conn = get_connection()
    try:
        if note is None:
            conn.execute("UPDATE field_event_queue SET status=?, applied_ref=?, "
                         "resolved_at=datetime('now') WHERE event_id=?",
                         (status, applied_ref, str(event_id)))
        else:
            conn.execute("UPDATE field_event_queue SET status=?, applied_ref=?, note=?, "
                         "resolved_at=datetime('now') WHERE event_id=?",
                         (status, applied_ref, note[:500], str(event_id)))
        conn.commit()
    finally:
        conn.close()


def _pending_or_raise(event_id: str, kind: str = None) -> dict:
    q = get_queued_event(event_id)
    if not q:
        raise ValueError(f"no queued phone event {event_id}")
    if q['status'] != 'pending':
        raise ValueError(f"phone event {event_id} is already {q['status']}")
    if kind and q['kind'] != kind:
        raise ValueError(f"phone event {event_id} is {q['kind']}, not {kind}")
    return q


def apply_pm_event(event_id: str, affected_blocks, date_from, date_to, hours,
                   description: str = '', on_duplicate: str = 'raise') -> dict:
    """Apply a waiting PM event as corrected on the desktop (record_pm rules;
    raises PMDuplicateError / PMValidationError for the dialog to answer)."""
    q = _pending_or_raise(event_id, 'pm')
    res = rw.record_pm(q['project_id'], affected_blocks, date_from, date_to, hours,
                       description, source='phone', source_ref=str(event_id),
                       on_duplicate=on_duplicate)
    ref = 'pm:' + ','.join(map(str, res['created'] + res['updated']))
    _resolve(event_id, 'applied', ref)
    return res


def confirm_counts_event(event_id: str, blocks, date_from, date_to, hours,
                         lc=None, cause: str = '', time_from: str = '',
                         time_to: str = '') -> List[int]:
    """Confirm a phone 'counts' event as manual downtime (validate_manual rules)."""
    q = _pending_or_raise(event_id, 'counts')
    ids = record_manual(q['project_id'], blocks, date_from, date_to, hours, lc,
                        cause, time_from, time_to, source='phone')
    _resolve(event_id, 'applied', 'manual:' + ','.join(map(str, ids)))
    return ids


def confirm_excluded_event(event_id: str, exclusion_type: str, date_from, time_from,
                           date_to, time_to, affected_blocks,
                           description: str = '') -> int:
    """Confirm a phone 'excluded' event as an exclusion window with its real
    start and end (validate_window rules)."""
    q = _pending_or_raise(event_id, 'excluded')
    v = validate_window(q['project_id'], exclusion_type, date_from, time_from,
                        date_to, time_to, affected_blocks)
    y, m = av._report_month_of(v['date_from'])
    eid = av.add_exclusion(exclusion_type=exclusion_type, date_from=v['date_from'],
                           date_to=v['date_to'], time_from=time_from, time_to=time_to,
                           affected_blocks=v['affected_blocks'],
                           description=(description or '').strip(),
                           project_id=q['project_id'], year=y, month=m)
    _resolve(event_id, 'applied', f'exclusion:{eid}')
    return eid


def reject_event(event_id: str, note: str = ''):
    """Drop a waiting phone event: it never reaches the report inputs."""
    _pending_or_raise(event_id)
    _resolve(event_id, 'rejected', '', note or 'rejected on the desktop')


def event_summary(ev: dict) -> str:
    """One line for a queued event: what the phone said."""
    bits = []
    if ev.get('exclusion_type'):
        bits.append(ev['exclusion_type'])
    when = ev.get('date_from') or '?'
    if ev.get('date_to') and ev.get('date_to') != ev.get('date_from'):
        when += ' → ' + ev['date_to']
    bits.append(when)
    bits.append('blocks ' + (ev.get('blocks') or '(none)'))
    bits.append(f"{rw.fmt_hours(ev.get('hours'))} h")
    if ev.get('description'):
        bits.append(str(ev['description'])[:60])
    return ' · '.join(bits)


# ── The month view ───────────────────────────────────────────────────────────

def _inferred_pm_sources(project_id: int) -> dict:
    """{pm id: source} for records written before `source` existed: a planner
    job pointing at it, or created in the same second a phone PM event was
    applied."""
    conn = get_connection()
    try:
        plan = {r[0] for r in conn.execute(
            "SELECT pm_activity_id FROM plan_items WHERE project_id=? "
            "AND pm_activity_id IS NOT NULL", (project_id,))}
        applied = {r[0] for r in conn.execute(
            "SELECT applied_at FROM synced_field_events WHERE kind='pm'")}
        out = {}
        for pid, created in conn.execute(
                "SELECT id, created_at FROM pm_activities WHERE project_id=? "
                "AND COALESCE(source,'')=''", (project_id,)):
            out[pid] = ('planner' if pid in plan else
                        'phone' if created in applied else 'desktop')
        return out
    finally:
        conn.close()


def month_inputs(project_id: int, year: int, month: int) -> dict:
    """Everything the Availability inputs view lists for a project-month:

      pm       PM records with source, the blocks the report charges, and flags
               (no block, duplicate of an earlier record, hours out of range,
               same block-day as a manual downtime row)
      manual   manual downtime rows with source and flags (not a plant block,
               hours out of range, same block-day as a PM record)
      pending  phone events waiting for the desktop
      totals   counts for the tab title and the summary line
    """
    n = int((rw.get_project(project_id) or {}).get('num_blocks') or 0)
    inferred = _inferred_pm_sources(project_id)
    ledger = rw.pm_ledger(project_id, year, month)
    manual = av.get_manual_unavailability(project_id=project_id, year=year, month=month)

    def _days(r):
        d1 = rw._iso_date(r.get('date_from'))
        d2 = rw._iso_date(r.get('date_to')) or d1
        if not d1:
            return set()
        return {(d1 + datetime.timedelta(days=i)).isoformat()
                for i in range(max(0, min(62, (d2 - d1).days)) + 1)}

    manual_keys = {}
    for r in manual:
        for d in _days(r):
            manual_keys.setdefault((int(r.get('block') or 0), d), []).append(r['id'])

    pm_out, pm_keys = [], {}
    for e in ledger:
        r = dict(e['row'])
        src = r.get('source') or inferred.get(r['id'], 'desktop')
        flags = list(e['problems'])
        both = sorted({i for b in e['counted']
                       for i in manual_keys.get((b, r.get('date_from') or ''), [])})
        if both:
            flags.append('same block-day as manual downtime ' + ', '.join(f'#{i}' for i in both)
                         + ' - check it is not counted twice')
        for b in e['counted']:
            pm_keys.setdefault((b, r.get('date_from') or ''), r['id'])
        r.update(source_label=SOURCE_LABELS.get(src, src), source_key=src,
                 blocks=e['blocks'], counted=e['counted'], flags=flags)
        pm_out.append(r)

    man_out = []
    for r in manual:
        r = dict(r)
        flags = []
        b = int(r.get('block') or 0)
        if b < 1 or (n and b > n):
            flags.append(f'block {b} is not a block of this plant - the report still counts it')
        days = max(1, len(_days(r)))
        if float(r.get('downtime_h') or 0) > 24 * days:
            flags.append(f"{rw.fmt_hours(r.get('downtime_h'))} h is more than {24 * days} h")
        pms = sorted({pm_keys[(b, d)] for d in _days(r) if (b, d) in pm_keys})
        if pms:
            flags.append('same block-day as PM record ' + ', '.join(f'#{i}' for i in pms)
                         + ' - check it is not counted twice')
        src = r.get('source') or ''
        r.update(source_label=SOURCE_LABELS.get(src, src or '🖥 desktop'), flags=flags)
        man_out.append(r)

    pend = pending_events(project_id, year, month)
    for q in pend:
        q['summary'] = event_summary(q['event'])

    return {
        'pm': pm_out, 'manual': man_out, 'pending': pend,
        'totals': {
            'pm_records': len(pm_out),
            'pm_block_days': sum(len(r['counted']) for r in pm_out),
            'pm_hours': sum(float(r.get('hours') or 0) * len(r['counted']) for r in pm_out),
            'manual_rows': len(man_out),
            'pending': len(pend),
            'flags': sum(1 for r in pm_out + man_out if r['flags']),
        },
    }
