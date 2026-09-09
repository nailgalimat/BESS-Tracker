"""
services/asset_tree_service.py
-------------------------------
The real equipment hierarchy and its history.

Why this exists
---------------
Until now the app modelled the plant only down to the container (LC) and threw
SCADA alarms away after each report run. So the questions an engineer actually
asks in the field had no answer in the app:

    "What is this unit? What has already happened to it? What fixed it
     last time? Has the same fault hit other units?"

Every answer had to be dug out of the monthly Excel by hand.

Two ideas make it cheap:

  1. SCADA already encodes the hierarchy in the alarm `Element` field —
     "BSC 32.01.02" is block 32, LC 1, PCS unit 2; "DC/DC 49.01.02.07" is
     module 7 inside unit 49.01.02. So the tree is *generated*, never typed.
  2. Alarms are persisted per (project, month) with an idempotent key, so
     re-importing a month is a no-op and history accumulates by itself.

Blocks / LCs / units become first-class asset rows. DC/DC and CMU modules stay
sub-components: they always show up in fault history, but only get their own
asset row when something real references them (a replacement), so the tree
does not become thousands of empty placeholders.

Data freshness: everything here is as of the last imported month. Callers must
show that date — see `get_data_freshness`.
"""

import re
from typing import Optional, List

import pandas as pd

from database.db_manager import get_connection

LEVELS = ('block', 'lc', 'unit', 'module')

# "BSC 32.01.02" / "DC/DC 49.01.02.07" / "LC200 02.01" / "PPC 2"
# The type may itself contain digits ("LC200"), so anchor on the trailing
# dotted path instead: everything before it is the equipment type.
_ELEMENT_RE = re.compile(r'^\s*(?:(\S.*?)\s+)?(\d+(?:\.\d+)+)\s*$')


_PARSE_CACHE = {}


def parse_element(element) -> Optional[dict]:
    """Split a SCADA Element string into the hierarchy it encodes.

    Returns None for anything without a numeric path (plant-level tags such as
    "PPC 2" have no asset to attach to).

    >>> parse_element('BSC 32.01.02')['unit_code']
    '32.01.02'
    >>> parse_element('DC/DC 49.01.02.07')['level']
    'module'
    """
    if element is None:
        return None
    key = str(element)
    if key in _PARSE_CACHE:                     # a month of alarms repeats the
        hit = _PARSE_CACHE[key]                 # same few thousand Elements
        return dict(hit) if hit else None       # copy: callers may mutate
    out = _parse_element_uncached(key)
    if len(_PARSE_CACHE) < 50000:
        _PARSE_CACHE[key] = out
    return dict(out) if out else None


def _parse_element_uncached(element: str) -> Optional[dict]:
    m = _ELEMENT_RE.match(str(element))
    if not m:
        return None
    eq_type = (m.group(1) or '').strip()        # bare "32.01.02" has no prefix
    parts = [p for p in m.group(2).split('.') if p != '']
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) < 2:
        return None          # plant-level tag — nothing to attach to

    block = nums[0]
    lc = nums[1]
    unit = nums[2] if len(nums) > 2 else None
    sub = nums[3] if len(nums) > 3 else None

    def _c(n):
        return '.'.join(f'{v:02d}' if i else str(v) for i, v in enumerate(nums[:n]))

    if len(nums) == 2:
        level, code, unit_code = 'lc', _c(2), ''
    elif len(nums) == 3:
        level, code, unit_code = 'unit', _c(3), _c(3)
    else:
        level, code, unit_code = 'module', _c(4), _c(3)

    return {
        'equipment_type': eq_type, 'level': level, 'code': code,
        'unit_code': unit_code, 'block': block, 'lc': lc,
        'unit': unit, 'sub': sub,
    }


def _code(block, lc=None, unit=None, sub=None) -> str:
    out = str(int(block))
    for v in (lc, unit, sub):
        if v is None:
            break
        out += f'.{int(v):02d}'
    return out


# ── Tree ─────────────────────────────────────────────────────────────────────

def rebuild_asset_tree(project_id: int, unit_capacity_kwh: float = None,
                       lc_capacity_kwh: float = None) -> dict:
    """(Re)generate blocks / LCs / units for a project from the alarm history
    already imported. Idempotent — existing rows are kept (so serial numbers
    and notes entered by hand survive), missing ones are added.

    Returns counts per level.
    """
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT DISTINCT block, lc, unit FROM alarm_events
            WHERE project_id=? AND block IS NOT NULL
        """, (project_id,)).fetchall()
        seen = [(r['block'], r['lc'], r['unit']) for r in rows]

        # Every block the project declares should exist even with no alarms
        prow = conn.execute("SELECT num_blocks FROM projects WHERE id=?",
                            (project_id,)).fetchone()
        n_blocks = int((prow['num_blocks'] if prow else 0) or 0)

        blocks = {b for b, _, _ in seen} | set(range(1, n_blocks + 1))
        lcs = {(b, l) for b, l, _ in seen if l is not None}
        units = {(b, l, u) for b, l, u in seen if u is not None}

        existing = {r['code']: r['id'] for r in conn.execute(
            "SELECT id, code FROM assets WHERE project_id=?", (project_id,))}
        made = {'block': 0, 'lc': 0, 'unit': 0}

        def _add(level, code, name, parent_id, cap=None, eq=''):
            if code in existing:
                return existing[code]
            cur = conn.execute("""
                INSERT INTO assets (project_id, parent_id, level, code, name,
                                    equipment_type, capacity_kwh)
                VALUES (?,?,?,?,?,?,?)
            """, (project_id, parent_id, level, code, name, eq, cap))
            existing[code] = cur.lastrowid
            made[level] += 1
            return cur.lastrowid

        for b in sorted(blocks):
            bid = _add('block', _code(b), f'Block {b}', None, eq='BLOCK')
            for (bb, l) in sorted(x for x in lcs if x[0] == b):
                lid = _add('lc', _code(bb, l), f'Block {bb} · LC {l}', bid,
                           lc_capacity_kwh, 'LC')
                for (_, _, u) in sorted(x for x in units
                                        if x[0] == bb and x[1] == l):
                    _add('unit', _code(bb, l, u),
                         f'Block {bb} · LC {l} · Unit {u}', lid,
                         unit_capacity_kwh, 'PCS')
        conn.commit()
        return made
    finally:
        conn.close()


def ensure_module_asset(project_id: int, code: str, equipment_type: str = '',
                        serial_number: str = '') -> Optional[int]:
    """Materialise a DC/DC or CMU module as a real asset — called when
    something references it for real (a replacement), not up front."""
    parts = code.split('.')
    if len(parts) != 4:
        return None
    conn = get_connection()
    try:
        row = conn.execute("SELECT id FROM assets WHERE project_id=? AND code=?",
                           (project_id, code)).fetchone()
        if row:
            return row['id']
        parent = conn.execute("SELECT id FROM assets WHERE project_id=? AND code=?",
                              (project_id, '.'.join(parts[:3]))).fetchone()
        cur = conn.execute("""
            INSERT INTO assets (project_id, parent_id, level, code, name,
                                equipment_type, serial_number)
            VALUES (?,?,'module',?,?,?,?)
        """, (project_id, parent['id'] if parent else None, code,
              f'{equipment_type or "Module"} {parts[3]}', equipment_type,
              serial_number))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_children(project_id: int, parent_code: Optional[str] = None) -> List[dict]:
    """Immediate children of a node (top level when parent_code is None)."""
    conn = get_connection()
    try:
        if parent_code:
            q = """SELECT a.* FROM assets a
                   JOIN assets p ON a.parent_id = p.id
                   WHERE a.project_id=? AND p.code=? ORDER BY a.id"""
            rows = conn.execute(q, (project_id, parent_code)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM assets WHERE project_id=? AND parent_id IS NULL "
                "ORDER BY CAST(code AS INTEGER)", (project_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_asset(project_id: int, code: str) -> Optional[dict]:
    conn = get_connection()
    try:
        r = conn.execute("SELECT * FROM assets WHERE project_id=? AND code=?",
                         (project_id, code)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def update_asset(project_id: int, code: str, **fields):
    allowed = {'serial_number', 'name', 'notes', 'equipment_type', 'capacity_kwh'}
    data = {k: v for k, v in fields.items() if k in allowed}
    if not data:
        return
    conn = get_connection()
    try:
        sets = ', '.join(f'{k}=?' for k in data)
        conn.execute(f"UPDATE assets SET {sets} WHERE project_id=? AND code=?",
                     [*data.values(), project_id, code])
        conn.commit()
    finally:
        conn.close()


# ── Alarm history ────────────────────────────────────────────────────────────

# Which frames of the report pipeline become history, and under what label.
# Only the deduplicated / exclusion-tagged frames are stored: the raw
# 'production' and 'warning' frames hold the same events without the
# is_excluded flag, and since the table's uniqueness key is
# (element, trigger, activated) they would win the INSERT OR IGNORE race and
# silently drop every exclusion mark.
IMPORT_CATEGORIES = {
    'production_dedup':   'production',
    'warning_persistent': 'warning_persistent',
    'warning_transient':  'warning_transient',
}


def find_alarm_exports(root: str) -> List[str]:
    """Every SCADA alarm export under a folder tree, deepest-first.

    The monthly folders are not named consistently — "Alarm report.XLSX" in
    one month, "Alarms report.XLSX" in another, and Excel leaves `~$` lock
    files behind — so match on the word rather than an exact name.
    """
    import os
    out = []
    for dirpath, _dirs, files in os.walk(root or ''):
        for f in files:
            low = f.lower()
            if f.startswith('~') or low.startswith('.'):
                continue                      # Excel lock / temp files
            if not low.endswith(('.xlsx', '.xls')):
                continue
            if 'alarm' not in low:
                continue
            out.append(os.path.join(dirpath, f))
    return sorted(out)


def detect_alarm_period(alarms: dict):
    """(year, month) a set of alarms belongs to, taken from the data.

    Folder names lie — "June 2026", "August", "LC data march" — while the
    Activated timestamps do not. Uses the most common month among production
    alarms, so a handful of events spilling over a month boundary cannot
    misfile the whole export.
    """
    best = None
    for key in ('production_dedup', 'production', 'warning_persistent'):
        df = (alarms or {}).get(key)
        if df is None or not hasattr(df, 'empty') or df.empty:
            continue
        if 'Activated' not in df.columns:
            continue
        ts = pd.to_datetime(df['Activated'], errors='coerce').dropna()
        if ts.empty:
            continue
        counts = ts.dt.to_period('M').value_counts()
        if counts.empty:
            continue
        top = counts.index[0]
        best = (int(top.year), int(top.month), int(counts.iloc[0]))
        break
    return best


def import_alarm_events(project_id: int, year: int, month: int,
                        alarms: dict, log=print) -> dict:
    """Persist a month of classified SCADA alarms.

    `alarms` is the dict produced by the report pipeline
    (load_alarms → apply_alarm_classifications → tag_alarms_with_exclusions).
    See IMPORT_CATEGORIES for which of its frames are stored.

    Idempotent: re-importing the same month inserts nothing new, so it is safe
    to hook this into every report generation.
    """
    inserted = skipped = unparsed = 0
    conn = get_connection()
    try:
        for key, category in IMPORT_CATEGORIES.items():
            df = (alarms or {}).get(key)
            if df is None or not hasattr(df, 'empty') or df.empty:
                continue
            if 'Element' not in df.columns:
                continue
            d = df.copy()
            # Vectorised conversions — a per-row pd.to_datetime on 20k alarms
            # turns a two-second import into minutes.
            fmt = '%Y-%m-%d %H:%M:%S'
            act = pd.to_datetime(d.get('Activated'), errors='coerce')
            act_s = act.dt.strftime(fmt).where(act.notna(), None)
            if 'Deactivation' in d.columns:
                de = pd.to_datetime(d['Deactivation'], errors='coerce')
                de_s = de.dt.strftime(fmt).where(de.notna(), None)
            else:
                de_s = pd.Series([None] * len(d), index=d.index)
            dur = pd.to_numeric(d.get('duration_min'), errors='coerce').fillna(0.0) \
                if 'duration_min' in d.columns else pd.Series(0.0, index=d.index)

            def _col(name):
                return (d[name].astype(str) if name in d.columns
                        else pd.Series([''] * len(d), index=d.index))
            trig, rea = _col('Trigger name'), _col('cls_reason')
            subs, sev = _col('cls_subsystem'), _col('cls_severity')
            exby = _col('excluded_by')
            isx = (d['is_excluded'].fillna(False).astype(bool).astype(int)
                   if 'is_excluded' in d.columns
                   else pd.Series(0, index=d.index))

            payload = []
            for i, el in zip(d.index, d['Element']):
                info = parse_element(el)
                if not info:
                    unparsed += 1
                    continue
                payload.append((
                    project_id, str(el), info['code'], info['unit_code'],
                    info['block'], info['lc'], info['unit'], info['sub'],
                    info['equipment_type'], str(category), trig.at[i], rea.at[i],
                    subs.at[i], sev.at[i], act_s.at[i], de_s.at[i],
                    float(dur.at[i]), int(isx.at[i]), exby.at[i],
                    int(year), int(month),
                ))
            if not payload:
                continue
            before = conn.total_changes
            conn.executemany("""
                INSERT OR IGNORE INTO alarm_events
                  (project_id, element, asset_code, unit_code, block, lc,
                   unit, sub, equipment_type, category, trigger_name,
                   cls_reason, cls_subsystem, cls_severity, activated,
                   deactivated, duration_min, is_excluded, excluded_by,
                   year, month)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, payload)
            inserted += conn.total_changes - before
        conn.commit()
    finally:
        conn.close()
    log(f"Alarm history: stored {inserted} event(s) for {year}-{month:02d} "
        f"({unparsed} without an asset code, {skipped} skipped).")
    return {'inserted': inserted, 'unparsed': unparsed, 'skipped': skipped}


def get_tree_stats(project_id: int) -> dict:
    """Fault load per asset code, rolled up the tree — {code: {...}}.

    `events`/`hours` count only alarms that are *not* inside an availability
    exclusion window, so a block does not look broken because the grid went
    down; `all_events`/`all_hours` keep the unfiltered figures.
    """
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT block, lc, unit, is_excluded, COUNT(*) AS n,
                   SUM(duration_min)/60.0 AS h
            FROM alarm_events
            WHERE project_id=? AND block IS NOT NULL
            GROUP BY block, lc, unit, is_excluded
        """, (project_id,)).fetchall()
    finally:
        conn.close()

    out = {}

    def _add(code, n, h, excluded):
        s = out.setdefault(code, {'events': 0, 'hours': 0.0,
                                  'all_events': 0, 'all_hours': 0.0})
        s['all_events'] += n
        s['all_hours'] += h
        if not excluded:
            s['events'] += n
            s['hours'] += h

    for r in rows:
        n, h = int(r['n']), float(r['h'] or 0.0)
        ex = bool(r['is_excluded'])
        b, l, u = r['block'], r['lc'], r['unit']
        _add(_code(b), n, h, ex)                       # block
        if l is not None:
            _add(_code(b, l), n, h, ex)                # LC
            if u is not None:
                _add(_code(b, l, u), n, h, ex)         # unit
    for s in out.values():
        s['hours'] = round(s['hours'], 2)
        s['all_hours'] = round(s['all_hours'], 2)
    return out


def get_data_freshness(project_id: int) -> Optional[dict]:
    """Latest imported month — every asset screen must show this so nobody
    mistakes last month's picture for a live one."""
    conn = get_connection()
    try:
        r = conn.execute("""
            SELECT year, month, MAX(imported_at) AS imported_at, COUNT(*) AS n
            FROM alarm_events WHERE project_id=?
            GROUP BY year, month ORDER BY year DESC, month DESC LIMIT 1
        """, (project_id,)).fetchone()
        return dict(r) if r and r['year'] else None
    finally:
        conn.close()


def _scope(code: str):
    """SQL fragment + params selecting every alarm at or below `code`."""
    p = code.split('.')
    if len(p) == 1:
        return "block=?", [int(p[0])]
    if len(p) == 2:
        return "block=? AND lc=?", [int(p[0]), int(p[1])]
    if len(p) == 3:
        return "block=? AND lc=? AND unit=?", [int(p[0]), int(p[1]), int(p[2])]
    return "block=? AND lc=? AND unit=? AND sub=?", [int(x) for x in p[:4]]


def get_asset_history(project_id: int, code: str, months: int = 12) -> dict:
    """Everything known about one asset — the screen an engineer opens before
    touching the equipment.

    Returns: asset, freshness, by_month, top_faults, recent, work_reports,
    pm, exclusions, totals.
    """
    where, params = _scope(code)
    conn = get_connection()
    try:
        asset = conn.execute(
            "SELECT * FROM assets WHERE project_id=? AND code=?",
            (project_id, code)).fetchone()
        base = [project_id] + params

        # `hours` counts everything; `own_hours` drops alarms that fell inside
        # an availability exclusion (grid outage / PM) — the figure that
        # actually reflects the equipment rather than the grid.
        by_month = [dict(r) for r in conn.execute(f"""
            SELECT year, month, COUNT(*) AS events,
                   ROUND(SUM(duration_min)/60.0, 2) AS hours,
                   ROUND(SUM(CASE WHEN is_excluded=1 THEN 0
                                  ELSE duration_min END)/60.0, 2) AS own_hours,
                   SUM(CASE WHEN is_excluded=1 THEN 1 ELSE 0 END) AS excluded
            FROM alarm_events WHERE project_id=? AND {where}
            GROUP BY year, month ORDER BY year DESC, month DESC LIMIT ?
        """, base + [months])]

        top_faults = [dict(r) for r in conn.execute(f"""
            SELECT trigger_name, cls_reason, cls_subsystem,
                   COUNT(*) AS events,
                   ROUND(SUM(duration_min)/60.0, 2) AS hours,
                   MAX(activated) AS last_seen
            FROM alarm_events WHERE project_id=? AND {where}
            GROUP BY trigger_name ORDER BY hours DESC LIMIT 15
        """, base)]

        recent = [dict(r) for r in conn.execute(f"""
            SELECT a.id, a.element, a.block, a.lc, a.unit, a.equipment_type,
                   a.trigger_name, a.cls_reason, a.cls_severity,
                   a.activated, a.deactivated,
                   ROUND(a.duration_min/60.0, 2) AS hours,
                   a.is_excluded, a.excluded_by, a.year, a.month,
                   COALESCE(
                     (SELECT w.id FROM work_logs w
                       WHERE w.alarm_event_id = a.id LIMIT 1),
                     (SELECT la.work_log_id FROM work_log_alarms la
                       WHERE la.alarm_event_id = a.id LIMIT 1)
                   ) AS work_log_id
            FROM alarm_events a WHERE a.project_id=? AND {where}
            ORDER BY a.activated DESC LIMIT 40
        """, base)]

        tot = conn.execute(f"""
            SELECT COUNT(*) AS events, ROUND(SUM(duration_min)/60.0,2) AS hours,
                   ROUND(SUM(CASE WHEN is_excluded=1 THEN 0
                                  ELSE duration_min END)/60.0,2) AS own_hours,
                   ROUND(SUM(CASE WHEN is_excluded=1 THEN duration_min
                                  ELSE 0 END)/60.0,2) AS excluded_hours,
                   SUM(CASE WHEN is_excluded=1 THEN 1 ELSE 0 END) AS excluded_events,
                   MIN(activated) AS first_seen, MAX(activated) AS last_seen
            FROM alarm_events WHERE project_id=? AND {where}
        """, base).fetchone()

        # Work reports on this block. work_logs numbers blocks per zone, the
        # asset code is plant-wide — translate, or a report on plant block 57
        # never shows up. See project_service.get_block_map.
        p = code.split('.')
        plant_block = int(p[0])
        from services.project_service import plant_block_to_zone
        zone, local = plant_block_to_zone(project_id, plant_block)
        wl_sql = ("SELECT id, date, zone_number, block_number, container_index, "
                  "fault_description, work_performed, root_cause, status, "
                  "sap_ticket, engineer, alarm_event_id FROM work_logs "
                  "WHERE project_id=? AND ")
        if zone is None:                     # no container inventory yet
            wl_sql += "block_number=?"
            wl_params = [project_id, plant_block]
        else:
            wl_sql += "zone_number=? AND block_number=?"
            wl_params = [project_id, zone, local]
        wl_sql += " ORDER BY date DESC LIMIT 30"
        work_reports = [dict(r) for r in conn.execute(wl_sql, wl_params)]

        pm = [dict(r) for r in conn.execute("""
            SELECT year, month, date_from, date_to, hours, description,
                   affected_blocks FROM pm_activities
            WHERE project_id=? ORDER BY date_from DESC LIMIT 30
        """, (project_id,))]
        pm = [r for r in pm if _touches_block(r.get('affected_blocks'), int(p[0]))]

        exc = [dict(r) for r in conn.execute("""
            SELECT exclusion_type, date_from, time_from, date_to, time_to,
                   affected_blocks, description FROM availability_exclusions
            WHERE project_id=? OR project_id IS NULL
            ORDER BY date_from DESC LIMIT 40
        """, (project_id,))]
        exc = [r for r in exc if _touches_block(r.get('affected_blocks'), int(p[0]))]

        return {
            'asset': dict(asset) if asset else {'code': code, 'level': '?'},
            'freshness': get_data_freshness(project_id),
            'totals': dict(tot) if tot else {},
            'by_month': by_month, 'top_faults': top_faults, 'recent': recent,
            'work_reports': work_reports, 'pm': pm, 'exclusions': exc,
        }
    finally:
        conn.close()


def _touches_block(spec, block: int) -> bool:
    """Does a csv/range block spec cover `block`? Empty means the whole plant."""
    s = (spec or '').strip()
    if not s or s.lower() in ('all', 'all blocks'):
        return True
    for tok in s.split(','):
        tok = tok.strip()
        if not tok:
            continue
        if '-' in tok:
            try:
                a, z = (int(x) for x in tok.split('-', 1))
                if min(a, z) <= block <= max(a, z):
                    return True
            except ValueError:
                continue
        else:
            try:
                if int(tok) == block:
                    return True
            except ValueError:
                continue
    return False


def group_faults_into_incidents(rows: List[dict],
                                window_min: int = 30) -> List[dict]:
    """Collapse one fault hitting many units at once into a single incident.

    A BSC-PCS comm fault tripped 25 units within the same minute in August
    2026. That is one thing that happened and one work report, not 25 — so
    alarms sharing a trigger name and starting within `window_min` of each
    other become one row carrying every alarm id.

    Each incident: trigger_name, reason, subsystem, first/last activated,
    hours (worst single unit), total_hours, assets (codes), blocks, ids.
    """
    if not rows:
        return []

    def _ts(v):
        try:
            return pd.to_datetime(v)
        except Exception:                            # noqa: BLE001
            return None

    buckets = {}
    for r in sorted(rows, key=lambda x: str(x.get('activated') or '')):
        trig = str(r.get('trigger_name') or '')
        t = _ts(r.get('activated'))
        placed = False
        for key in list(buckets):
            if key[0] != trig:
                continue
            b = buckets[key]
            if t is not None and b['_last_ts'] is not None:
                if abs((t - b['_last_ts']).total_seconds()) <= window_min * 60:
                    b['rows'].append(r)
                    b['_last_ts'] = t
                    placed = True
                    break
        if not placed:
            buckets[(trig, len(buckets))] = {'rows': [r], '_last_ts': t}

    out = []
    for b in buckets.values():
        rs = b['rows']
        first = rs[0]
        hours = [float(x.get('hours') or 0) for x in rs]
        acts = sorted(str(x.get('activated') or '') for x in rs)
        out.append({
            'trigger_name':  first.get('trigger_name'),
            'cls_reason':    first.get('cls_reason'),
            'cls_subsystem': first.get('cls_subsystem'),
            'activated':     acts[0],
            'last_activated': acts[-1],
            'hours':         max(hours) if hours else 0.0,
            'total_hours':   round(sum(hours), 2),
            'units':         len(rs),
            'assets':        sorted({str(x.get('asset_code') or '') for x in rs}),
            'blocks':        sorted({int(x['block']) for x in rs
                                     if x.get('block') is not None}),
            'ids':           [x['id'] for x in rs],
            'primary':       max(rs, key=lambda x: float(x.get('hours') or 0)),
        })
    out.sort(key=lambda x: (-x['total_hours'], x['activated']))
    return out


def flag_incidents_near_exclusions(project_id: int, incidents: List[dict],
                                   margin_min: int = 90) -> List[dict]:
    """Mark incidents that sit just outside an exclusion window.

    Units trip before the operator writes down the outage: in August 2026, 32
    units failed at 22:45 and the grid-outage window was logged from 23:00.
    Those alarms are not excluded, so they land in "needs a report" as if they
    were 32 separate equipment faults — when the real answer is that the
    window starts too late.

    Adds `near_exclusion` (a short note) to each incident it recognises.
    """
    from services.availability_service import get_exclusions, _exclusion_windows
    wins = _exclusion_windows(get_exclusions(project_id=project_id) or [])
    if not wins:
        return incidents
    for inc in incidents:
        try:
            t = pd.to_datetime(inc.get('activated'))
        except Exception:                            # noqa: BLE001
            continue
        if t is None or pd.isna(t):
            continue
        blocks = set(inc.get('blocks') or [])
        best = None
        for start, end, wblocks, exc in wins:
            if wblocks is not None and blocks and not (blocks & wblocks):
                continue
            lead = (start - t).total_seconds() / 60.0     # before the window
            tail = (t - end).total_seconds() / 60.0       # after it closed
            if 0 <= lead <= margin_min:
                cand = (lead, f"{int(round(lead))} min before "
                              f"{exc.get('exclusion_type', 'exclusion')} "
                              f"{str(start)[:16]}")
            elif 0 <= tail <= margin_min:
                cand = (tail, f"{int(round(tail))} min after "
                              f"{exc.get('exclusion_type', 'exclusion')} "
                              f"{str(end)[:16]}")
            else:
                continue
            if best is None or cand[0] < best[0]:
                best = cand
        if best:
            inc['near_exclusion'] = best[1]
    return incidents


def reapply_exclusions(project_id: int, year: int = None, month: int = None,
                       log=print) -> dict:
    """Recompute is_excluded / excluded_by on alarms already stored.

    The flags are written once, at import. Anything that changes the
    exclusion list afterwards — correcting a window's start time, adding a
    grid outage nobody had recorded — would otherwise leave the history
    frozen at what was known on the day it was imported, and re-importing
    fixes nothing because the import is idempotent.
    """
    from services.availability_service import get_exclusions, _exclusion_windows
    wins = _exclusion_windows(get_exclusions(project_id=project_id) or [])

    where = ["project_id=?"]
    params = [project_id]
    if year is not None:
        where.append("year=?"); params.append(int(year))
    if month is not None:
        where.append("month=?"); params.append(int(month))

    conn = get_connection()
    try:
        rows = conn.execute(
            f"SELECT id, block, activated, is_excluded, excluded_by "
            f"FROM alarm_events WHERE {' AND '.join(where)}", params).fetchall()
        ts = pd.to_datetime([r['activated'] for r in rows], errors='coerce')

        updates = []
        for r, t in zip(rows, ts):
            hit_type = ''
            if t is not None and not pd.isna(t):
                blk = r['block']
                for start, end, blocks, exc in wins:
                    if not (start <= t <= end):
                        continue
                    if blocks is not None:
                        if blk is None or int(blk) not in blocks:
                            continue
                    hit_type = exc.get('exclusion_type', '') or ''
                    break
            was = (bool(r['is_excluded']), r['excluded_by'] or '')
            now = (bool(hit_type), hit_type)
            if was != now:
                updates.append((1 if hit_type else 0, hit_type, r['id']))

        if updates:
            conn.executemany(
                "UPDATE alarm_events SET is_excluded=?, excluded_by=? WHERE id=?",
                updates)
            conn.commit()
    finally:
        conn.close()

    log(f"Exclusions reapplied: {len(updates)} alarm(s) changed.")
    return {'checked': len(rows), 'changed': len(updates)}


def suggest_exclusion_windows(project_id: int, year: int = None,
                              month: int = None, min_blocks: int = 20,
                              pad_min: int = 5) -> List[dict]:
    """Grid outages the alarm history can prove, for the operator to confirm.

    When most of the plant trips within the same minute, that is the grid
    going away, not seventy simultaneous equipment failures. Such events are
    already visible in the data; what is usually missing is the exclusion
    record, without which the whole outage is charged to the equipment. Five
    of the six imported months have no exclusions at all.

    Each suggestion carries the window the *equipment* actually saw — first
    trip to last recovery — which is what makes it useful even for months
    that do have records: in August 2026 the operator logged 23:00 while 32
    units had already tripped at 22:45.

    Nothing is written. Returns candidates: date_from/time_from,
    date_to/time_to, blocks, units, events, hours, trigger, covered.
    """
    where = ["project_id=?", "category='production'", "is_excluded=0",
             "block IS NOT NULL"]
    params = [project_id]
    if year is not None:
        where.append("year=?"); params.append(int(year))
    if month is not None:
        where.append("month=?"); params.append(int(month))

    conn = get_connection()
    try:
        rows = [dict(r) for r in conn.execute(f"""
            SELECT id, asset_code, block, trigger_name, activated, deactivated,
                   ROUND(duration_min/60.0, 2) AS hours
            FROM alarm_events WHERE {' AND '.join(where)}
        """, params)]
    finally:
        conn.close()

    out = []
    for inc in group_faults_into_incidents(rows):
        if len(inc['blocks']) < min_blocks:
            continue
        ends = [x.get('deactivated') for x in
                [r for r in rows if r['id'] in set(inc['ids'])]
                if x.get('deactivated')]
        start = pd.to_datetime(inc['activated'], errors='coerce')
        end = pd.to_datetime(max(ends), errors='coerce') if ends else None
        if start is None or pd.isna(start):
            continue
        if end is None or pd.isna(end) or end < start:
            end = start
        # A couple of minutes either side: the first unit to notice is rarely
        # the first to be affected.
        start = start - pd.Timedelta(minutes=pad_min)
        end = end + pd.Timedelta(minutes=pad_min)
        out.append({'start': start, 'end': end,
                    'blocks': set(inc['blocks']), 'units': inc['units'],
                    'ids': list(inc['ids']), 'hours': float(inc['total_hours']),
                    'triggers': [inc['trigger_name']]})

    # One outage shows up under several trigger names — a DC/DC hardware
    # fault, a bus-voltage alarm and a comm fault are all the same grid event.
    # Merge overlapping windows so the operator confirms one outage, not four.
    out.sort(key=lambda w: w['start'])
    merged = []
    for w in out:
        if merged and w['start'] <= merged[-1]['end']:
            m = merged[-1]
            m['end'] = max(m['end'], w['end'])
            m['blocks'] |= w['blocks']
            m['units'] += w['units']
            m['ids'] += w['ids']
            m['hours'] += w['hours']
            for t in w['triggers']:
                if t not in m['triggers']:
                    m['triggers'].append(t)
        else:
            merged.append(dict(w))

    return [{
        'date_from': m['start'].strftime('%Y-%m-%d'),
        'time_from': m['start'].strftime('%H:%M'),
        'date_to':   m['end'].strftime('%Y-%m-%d'),
        'time_to':   m['end'].strftime('%H:%M'),
        'blocks':    sorted(m['blocks']),
        'units':     m['units'],
        'events':    len(set(m['ids'])),
        'hours':     round(m['hours'], 2),
        'trigger':   ' · '.join(t for t in m['triggers'][:3] if t),
        'ids':       sorted(set(m['ids'])),
    } for m in merged]


def get_alarm_event(project_id: int, event_id: int) -> Optional[dict]:
    conn = get_connection()
    try:
        r = conn.execute("SELECT * FROM alarm_events WHERE project_id=? AND id=?",
                         (project_id, event_id)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def get_reported_alarm_ids(project_id: int) -> set:
    """Alarm events that already have a work report attached."""
    conn = get_connection()
    try:
        return {r[0] for r in conn.execute(
            "SELECT alarm_event_id FROM work_logs "
            " WHERE project_id=? AND alarm_event_id IS NOT NULL "
            "UNION "
            "SELECT alarm_event_id FROM work_log_alarms", (project_id,))}
    finally:
        conn.close()


def get_unreported_faults(project_id: int, year: int = None, month: int = None,
                          min_hours: float = 1.0, code: str = None,
                          limit: int = 200) -> List[dict]:
    """Faults that cost availability but have no work report against them.

    This is the month-end reconciliation an O&M manager does by hand today:
    the customer asks "what did you do about this outage?" and the answer has
    to exist somewhere.

    Scope is deliberately narrow, or the list is useless:
      * only the 'production' frame — the deduplicated faults the availability
        figure is actually computed from. Persistent LC warnings are latched
        states, not outages: one of them reads as 866 h across five weeks and
        would sit at the top of the list forever.
      * only alarms *outside* an exclusion window — a grid outage is not ours
        to explain.
      * only those long enough to be worth writing up.

    Pass `code` to narrow to one block / LC / unit.
    """
    where = ["project_id=?", "category='production'", "is_excluded=0",
             "duration_min >= ?",
             "id NOT IN (SELECT alarm_event_id FROM work_logs "
             "WHERE alarm_event_id IS NOT NULL)",
             "id NOT IN (SELECT alarm_event_id FROM work_log_alarms)"]
    params = [project_id, float(min_hours) * 60.0]
    if year is not None:
        where.append("year=?"); params.append(int(year))
    if month is not None:
        where.append("month=?"); params.append(int(month))
    if code:
        frag, cp = _scope(code)
        where.append(frag); params.extend(cp)

    conn = get_connection()
    try:
        rows = conn.execute(f"""
            SELECT id, element, asset_code, unit_code, block, lc, unit,
                   equipment_type, trigger_name, cls_reason, cls_subsystem,
                   cls_severity, activated, deactivated,
                   ROUND(duration_min/60.0, 2) AS hours, year, month
            FROM alarm_events
            WHERE {' AND '.join(where)}
            ORDER BY duration_min DESC LIMIT ?
        """, params + [int(limit)]).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def find_precedents(project_id: int, trigger_name: str = '',
                    fault_text: str = '', exclude_work_log: int = None,
                    limit: int = 10) -> List[dict]:
    """"Have we seen this before, and what fixed it?"

    Looks for earlier work reports about the same thing, most recent first:
      1. reports linked to an alarm with the same trigger name — the reliable
         match, because the link is a real id, not a guess;
      2. reports whose own fault text looks like this one, for everything
         written before alarms were linked at all.

    Returns the fix, not just the fact: work_performed, root_cause, status,
    materials used and who did it.
    """
    trig = (trigger_name or '').strip()
    text = (fault_text or '').strip()
    if not trig and not text:
        return []

    conn = get_connection()
    try:
        seen, out = set(), []

        def _take(rows, how):
            for r in rows:
                if r['id'] in seen or r['id'] == exclude_work_log:
                    continue
                seen.add(r['id'])
                d = dict(r)
                d['match'] = how
                d['materials'] = [
                    f"{m['material_number']} ×{m['quantity']:g}"
                    for m in conn.execute(
                        "SELECT material_number, quantity FROM work_log_materials "
                        "WHERE work_log_id=?", (r['id'],))]
                out.append(d)

        cols = ("w.id, w.date, w.zone_number, w.block_number, w.status, "
                "w.fault_description, w.work_performed, w.root_cause, "
                "w.engineer, w.sap_ticket")
        if trig:
            _take(conn.execute(f"""
                SELECT {cols} FROM work_logs w
                WHERE w.project_id=? AND w.id IN (
                    SELECT wl.id FROM work_logs wl
                      JOIN alarm_events a ON a.id = wl.alarm_event_id
                     WHERE a.trigger_name = ?
                    UNION
                    SELECT la.work_log_id FROM work_log_alarms la
                      JOIN alarm_events a2 ON a2.id = la.alarm_event_id
                     WHERE a2.trigger_name = ?)
                ORDER BY w.date DESC LIMIT ?
            """, (project_id, trig, trig, limit)), 'same alarm')

        if len(out) < limit:
            # Fall back to the words themselves. Strip the "[BSC 32.01.02]"
            # element tag first — it is unique to one unit and would match
            # nothing.
            probe = re.sub(r'\[[^\]]*\]', '', text or trig).strip()
            probe = re.sub(r'\s+', ' ', probe)[:60]
            if len(probe) >= 6:
                _take(conn.execute(f"""
                    SELECT {cols} FROM work_logs w
                    WHERE w.project_id=? AND w.fault_description LIKE ?
                    ORDER BY w.date DESC LIMIT ?
                """, (project_id, f'%{probe}%', limit - len(out))), 'similar text')
        return out[:limit]
    finally:
        conn.close()


def find_similar_failures(project_id: int, trigger_name: str,
                          exclude_code: str = '', limit: int = 20) -> List[dict]:
    """Where else has this same fault happened, and what was done about it —
    the 'have we seen this before?' lookup used while troubleshooting."""
    conn = get_connection()
    try:
        # Grouped by asset_code, not unit_code: LC- and block-level alarms have
        # no PCS unit, and would all collapse into one empty bucket.
        rows = conn.execute("""
            SELECT asset_code, unit_code, element, block, equipment_type,
                   COUNT(*) AS events,
                   ROUND(SUM(duration_min)/60.0,2) AS hours,
                   MAX(activated) AS last_seen
            FROM alarm_events
            WHERE project_id=? AND trigger_name=? AND asset_code<>?
            GROUP BY asset_code ORDER BY hours DESC LIMIT ?
        """, (project_id, trigger_name, exclude_code or '', limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
