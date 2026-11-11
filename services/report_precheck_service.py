"""services/report_precheck_service.py — questions before a monthly report is generated

Read-only. `run_precheck` looks at the month's inputs and data set and returns
the things to settle before the report goes out, most important first. Each
question names one place where it is fixed (`target`); the check itself never
changes an input — August 2026's wrong window edges were found by a person
looking at them, and a person corrects them.

  missing   a file the generator needs is not in the data set
  edge      an exclusion window shorter than the SCADA stop it covers
            (tashkent_report_service.exclusion_edge_report, as the generator
            logs it: one per window side of at least 1 LC-h)
  pending   phone events still waiting for the desktop — not in the report
  pm        PM records the report does not count as entered (duplicate of a
            block-day, no block, hours out of range)
  manual    manual downtime rows with a problem
  cm        corrective records kept out of 3.2 for lack of a plant block, a
            sync conflict, or because they are still open
  gap       days of the month without data, part days, gaps over an hour
  month_end month-end files not added yet (snapshots, the customer's POI file)
  customer  the customer's Daily Generation Report / the permit register not
            added (information), or a DGR without this month's row
  permit_*  permits, PM records and SCADA stops compared (permit_questions)

A question may carry `prefill` — the values for the dialog its Fix button
opens (the user still confirms every write).

Generating with open questions or an incomplete data set is allowed after an
explicit confirmation, which the version's manifest records.
"""

import calendar
import datetime
import re
from typing import List

import services.report_workflow_service as rw
import services.availability_inputs_service as avi
import services.month_dataset_service as mds

SEVERITY_ORDER = {'crit': 0, 'warn': 1, 'info': 2}


def _q(kind, severity, title, detail, target, action, ref=None, explain=''):
    return {'kind': kind, 'severity': severity, 'title': title, 'detail': detail,
            'explain': explain, 'target': target, 'action': action, 'ref': ref}


def _day_ranges(days: List[str]) -> str:
    """['2026-08-28', '2026-08-29', '2026-08-31'] -> '28–29.08, 31.08'."""
    ds = sorted(datetime.date.fromisoformat(d) for d in days)
    out, start, prev = [], None, None
    for d in ds + [None]:
        if d is not None and prev is not None and (d - prev).days == 1:
            prev = d
            continue
        if start is not None:
            out.append(f'{start:%d.%m}' if start == prev else f'{start:%d}–{prev:%d.%m}')
        start = prev = d
    return ', '.join(out)


def _load_ws(project_id: int, year: int, month: int):
    """The month's LC working status (through the parse cache), or None."""
    f = mds.active_by_type(project_id, year, month).get('working_status')
    if not f:
        return None
    from services.tashkent_report_service import load_lc_working_status
    return load_lc_working_status(f['abs_path'])


def edge_questions(project_id: int, year: int, month: int, exclusions=None,
                   ws_long=None) -> List[dict]:
    """Exclusion windows whose edges the SCADA working status contradicts.
    `ws_long`: the working status when the caller has already loaded it."""
    f = mds.active_by_type(project_id, year, month).get('working_status')
    exclusions = exclusions if exclusions is not None else \
        (rw.report_inputs(project_id, year, month).get('exclusions') or [])
    if not f or not exclusions:
        return []
    from services.tashkent_report_service import load_lc_working_status, exclusion_edge_report
    ws = ws_long if ws_long is not None else load_lc_working_status(f['abs_path'])
    out = []
    for e in exclusion_edge_report(ws, exclusions):
        if e['lc_hours'] < 1.0:
            continue
        x, (a, z) = e['exclusion'], e['window']
        blocks = sorted(e['blocks'])
        blk = (', '.join(map(str, blocks)) if len(blocks) <= 8
               else f"{len(blocks)} blocks ({blocks[0]}–{blocks[-1]})")
        if e['side'] == 'before':
            hint = f"SCADA: already down from {e['first']:%d.%m %H:%M}, before the window starts"
        else:
            hint = f"SCADA: still down until {e['last']:%d.%m %H:%M}, after the window ends"
        out.append(_q('edge', 'warn', 'Window edge',
                      f"{x.get('exclusion_type', 'Exclusion')} #{x.get('id')} · "
                      f"{a:%d.%m %H:%M} → {z:%d.%m %H:%M} · block {blk}",
                      'inputs.exclusions', 'Open the window', ref=x.get('id'),
                      explain=f"{hint}. {e['lc_hours']:.1f} LC-h outside the window "
                              f"count as fault."))
    return out


# A permit is a PM permit when it says so. "KKS" alone used to count,
# which asked for a PM record against plain KKS installation work — the
# opposite of the "KKS work" exclusion type, which is not PM.
PM_PERMIT_RE = re.compile(r'preventive|\bPM\b', re.I)


def _d(s):
    """'dd.mm.yyyy' or 'yyyy-mm-dd' -> date, or None."""
    s = str(s or '')[:10]
    for f in ('%d.%m.%Y', '%Y-%m-%d'):
        try:
            return datetime.datetime.strptime(s, f).date()
        except ValueError:
            pass
    return None


def _days(a, z):
    z = z if z and z >= a else a
    return [a + datetime.timedelta(days=i) for i in range((z - a).days + 1)]


def _hours_q(h):
    """Clock hours rounded to a quarter hour, inside the PM limits."""
    q = round(float(h or 0) * 4) / 4.0
    return min(max(q, 0.25), float(rw.PM_MAX_HOURS)) if h else 0.0


def permit_questions(project_id: int, year: int, month: int, ws_long=None) -> List[dict]:
    """Permits, PM records and SCADA stops compared (Tashkent). Read-only.

      permit_no_pm   a PM / KKS permit names a block that has no PM record in
                     the permit's dates (issue .. valid till, inclusive)
      pm_no_stop     a PM record whose block did not stop in the working day
                     (the stop a PM record covers, tashkent pm_stop_windows)
      pm_no_ptw      a PM record without a PTW No. that a permit matches
      permit_dates   register rows whose dates cannot be made sense of
      permit_skip    no working status: stops were not compared

    The RMU number in a permit is the plant block (1..num_blocks), compared
    directly with pm_activities.affected_blocks. Whole-plant ('all') records
    are left out. Nothing here without the permit register."""
    from services.tashkent_report_service import (load_cptw_register, permit_blocks,
                                                  pm_stop_windows)
    files = mds.active_by_type(project_id, year, month)
    reg = files.get('permit_register')
    if not reg:
        return []
    if ws_long is None and files.get('working_status'):
        ws_long = _load_ws(project_id, year, month)
    have_ws = ws_long is not None and not getattr(ws_long, 'empty', True)
    cfg = rw.get_project_config(project_id)
    n_blocks = int((rw.get_project(project_id) or {}).get('num_blocks') or 0) or None
    unclear, oor = [], []
    permits = load_cptw_register(reg['abs_path'], year, month,
                                 issued_to=(cfg.get('permit_issued_to') or '').strip() or None,
                                 unclear_out=unclear)
    first = datetime.date(year, month, 1)
    last = datetime.date(year, month, calendar.monthrange(year, month)[1])
    # A permit belongs to the month it was issued in, but its work can fall
    # in the next one. Last month's permits still valid on the 1st are
    # carried in for matching (a record on 1-2 October finds its permit of
    # 30 September); they are asked about in their own month, not here.
    _prev = first - datetime.timedelta(days=1)
    carried = [p for p in load_cptw_register(
                   reg['abs_path'], _prev.year, _prev.month,
                   issued_to=(cfg.get('permit_issued_to') or '').strip() or None)
               if (_d(p.get('valid_till')) or datetime.date.min) >= first]
    # And next month's PM records answer a permit issued at the end of this
    # one, so it is not reported as having no record.
    _next = last + datetime.timedelta(days=1)
    recs_next = []
    for e in rw.pm_ledger(project_id, _next.year, _next.month):
        r = e['row']
        a = _d(r.get('date_from'))
        if not a:
            continue
        z = _d(r.get('date_to')) or a
        for b in e['counted']:
            recs_next.append({'row': r, 'block': b, 'from': a, 'to': max(z, a)})

    # every permit's blocks and days (any permit names a block; PM ones need a record)
    plist = []
    for p, is_carried in ([(p, False) for p in permits]
                          + [(p, True) for p in carried]):
        a = _d(p['date'])
        if not a:
            continue
        z = _d(p.get('valid_till')) or a
        blocks = permit_blocks(p.get('area'), p.get('title'), n_blocks=n_blocks, out_of_range=oor)
        plist.append({'p': p, 'from': a, 'to': max(z, a), 'blocks': blocks,
                      'carried': is_carried,
                      'pm': bool(PM_PERMIT_RE.search(p.get('title') or ''))
                      and 'cancel' not in (p.get('status') or '').lower()})

    def permits_for(block, day_from, day_to):
        return [x for x in plist if block in x['blocks']
                and x['from'] <= day_to and day_from <= x['to']]

    # the month's PM records as (record, block, from, to) — counted blocks only
    recs = []
    for e in rw.pm_ledger(project_id, year, month):
        r = e['row']
        if str(r.get('affected_blocks') or '').strip().lower() in ('all', 'all blocks'):
            continue
        a = _d(r.get('date_from'))
        if not a:
            continue
        z = _d(r.get('date_to')) or a
        for b in e['counted']:
            recs.append({'row': r, 'block': b, 'from': a, 'to': max(z, a)})

    stops = {}            # (block, date) -> pm_stop_windows row
    if have_ws:
        import pandas as pd
        want = [{'pm_id': i, 'block': x['block'], 'date_from': pd.Timestamp(x['from']),
                 'date_to': pd.Timestamp(x['to']), 'hours': 0} for i, x in enumerate(recs)]
        for x in plist:
            if not x['pm'] or x['carried']:
                continue
            for b in x['blocks']:
                want.append({'pm_id': len(want), 'block': b,     # a stand-in record
                             'date_from': pd.Timestamp(max(x['from'], first)),
                             'date_to': pd.Timestamp(min(x['to'], last)), 'hours': 0})
        for w in pm_stop_windows(ws_long, [w for w in want if w['date_from'] <= w['date_to']]):
            stops[(w['block'], w['date'].date())] = w

    def stop_on(block, a, z):
        """The longest stop of the block on any day a..z (inside the month)."""
        best = None
        for d in _days(max(a, first), min(z, last)):
            w = stops.get((block, d))
            if w and w['start'] is not None and (best is None or w['clock_hours'] > best['clock_hours']):
                best = w
        return best

    out = []
    # 1. a PM permit with no PM record
    asked = set()
    for x in plist:
        if not x['pm'] or x['carried']:
            continue
        p = x['p']
        for b in x['blocks']:
            if any(r['block'] == b and r['from'] <= x['to'] and x['from'] <= r['to']
                   for r in recs + recs_next):
                continue
            if (b, x['from']) in asked:
                continue
            asked.add((b, x['from']))
            day, hours, sev, explain = x['from'], 0.0, 'warn', ''
            if have_ws:
                w = stop_on(b, x['from'], x['to'])
                if w:
                    day = w['date'].date()
                    hours = _hours_q(w['clock_hours'])
                    explain = (f"SCADA: block {b} out of operation {w['start']:%H:%M}–"
                               f"{w['end']:%H:%M} ({w['clock_hours']:.2f} h). Without a PM "
                               f"record this stop counts as fault.")
                else:
                    sev = 'info'
                    explain = 'No stop in SCADA that day. The work may not have been done.'
            area = p.get('area') or ''
            out.append(dict(_q('permit_no_pm', sev, 'Permit without PM record',
                               f"{p['no']} · {x['from']:%d.%m} · block {b}"
                               + (f" ({area})" if area else '') + f" · {p.get('title') or ''}",
                               'inputs.pm.add', 'Add PM record…', ref=p['no'], explain=explain),
                            prefill={'date_from': day.isoformat(), 'date_to': day.isoformat(),
                                     'affected_blocks': str(b), 'hours': hours,
                                     'description': p.get('title') or 'Preventive maintenance',
                                     'ptw_no': p['no']}))

    # 2. a PM record with no stop (only with the working status)
    if have_ws:
        def backed(rr):
            """A record that has a permit or a stop — the one a duplicate copies."""
            return bool(permits_for(rr['block'], rr['from'], rr['to'])
                        or stop_on(rr['block'], rr['from'], rr['to']))
        for x in recs:
            if stop_on(x['block'], x['from'], x['to']):
                continue
            r, b = x['row'], x['block']
            src = (r.get('source') or '').strip()
            explain = f"Block {b} did not stop between 07:00 and 19:00"
            if not permits_for(b, x['from'], x['to']):
                explain += f" and no permit names RMU-{b:02d} that day"
            explain += '.'
            other = [o for o in recs if o is not x and o['block'] == b and backed(o)]
            if other:
                o = other[0]
                ptw = (o['row'].get('ptw_no') or '').strip() or next(
                    (y['p']['no'] for y in permits_for(b, o['from'], o['to'])), '')
                explain += (f" Block {b} already has PM #{o['row']['id']} on {o['from']:%d.%m}"
                            + (f" ({ptw})" if ptw else '') + ". Probably a duplicate.")
            explain += f" The {rw.fmt_hours(r.get('hours'))} h still count against availability."
            out.append(_q('pm_no_stop', 'warn', 'PM record without a stop',
                          f"#{r['id']} · block {b} · {x['from']:%d.%m} · "
                          f"{rw.fmt_hours(r.get('hours'))} h" + (f" · {src}" if src else ''),
                          'inputs.pm', 'Open the PM record', ref=r['id'], explain=explain))

    # 3. a PM record without its permit number, where the register has one
    for x in recs:
        r = x['row']
        if (r.get('ptw_no') or '').strip():
            continue
        cand = sorted((y for y in permits_for(x['block'], x['from'], x['to']) if y['pm']),
                      key=lambda y: y['from'])
        if not cand:
            continue
        p = cand[0]['p']
        where = p.get('area') or f"block {x['block']}"
        out.append(dict(_q('pm_no_ptw', 'warn', 'PM record without PTW No.',
                           f"#{r['id']} · block {x['block']} · {x['from']:%d.%m}: the register "
                           f"has {p['no']} ({where}, {cand[0]['from']:%d.%m})",
                           'inputs.pm.ptw', f"Use {p['no']}…", ref=r['id']),
                        prefill={'ptw_no': p['no']}))

    if unclear:
        nos = ', '.join(u['no'] for u in unclear)
        out.append(_q('permit_dates', 'info', 'Permit dates unclear',
                      f"{len(unclear)} permit(s) with an issue date after their "
                      f"\"valid till\": {nos}. Left out of the check and of section 6.",
                      'data', 'Open the data set'))
    if oor:
        out.append(_q('permit_dates', 'info', 'Permit names a block this plant does not have',
                      'RMU ' + ', '.join(str(b) for b in sorted(set(oor)))
                      + f" (the plant has {n_blocks} blocks). Ignored by the check.",
                      'data', 'Open the data set'))
    if not have_ws:
        out.append(_q('permit_skip', 'info', 'Permit check skipped',
                      'LC working status not added. PM records were not compared with SCADA stops.',
                      'data', 'Add files'))
    return out


def customer_file_questions(project_id: int, year: int, month: int) -> List[dict]:
    """The optional customer and site files: information when one is not
    added (the report falls back on our figures / no permit list), a warning
    when the customer's report has no row for this month."""
    out = []
    month_name = f"{rw.MONTHS_EN[month]} {year}"
    for c in mds.customer_files(project_id, year, month):
        if c['type'] == 'customer_dgr':
            if not c['present']:
                out.append(_q('customer', 'info', 'Customer file not added',
                              f"{c['label']} for {month_name}", 'data', 'Add files',
                              explain='The report uses our own meter figures.'))
                continue
            cov = c['coverage']
            row = cov.get('customer_row') if cov.get('analysed_at') else None
            if row is None:
                try:
                    from services.tashkent_report_service import load_customer_monthly_summary
                    row = load_customer_monthly_summary(c['file']['abs_path'], year, month)
                except Exception:                           # noqa: BLE001
                    row = {}
            if not row:
                out.append(_q('customer', 'warn', 'Customer file has no row for the month',
                              f"{c['file']['original_name']} · no {month_name} row in "
                              f"\"Monthly Summary\"", 'data', 'Replace the file',
                              explain="The file you added is not this month's. "
                                      "The report uses our own meter figures."))
        elif c['type'] == 'permit_register' and not c['present']:
            out.append(_q('customer', 'info', 'Permit register not added',
                          f"{c['label']} for {month_name}", 'data', 'Add files',
                          explain='Section 6 has no permit list and the permit check is skipped.'))
    return out


def run_precheck(project_id: int, year: int, month: int, today: datetime.date = None,
                 scada: bool = True) -> List[dict]:
    """All open questions for a project-month, most important first. Every
    question: {kind, severity, title, detail, explain, target, action, ref}.
    Reads only."""
    qs: List[dict] = []
    site = mds.site_type(project_id)
    month_name = f"{rw.MONTHS_EN[month]} {year}"

    for label in mds.missing_required(project_id, year, month):
        qs.append(_q('missing', 'crit', 'File missing', f'{label} is not in the data set',
                     'data', 'Add files'))

    inputs = avi.month_inputs(project_id, year, month)
    for q in inputs['pending']:
        qs.append(_q('pending', 'crit', 'Phone event waiting', q['summary'],
                     'inputs.pending', 'Review the event', ref=q['event_id']))

    ws_long = None
    if scada and site == 'tashkent':
        # the working status is read once for every check that needs it
        have = mds.active_by_type(project_id, year, month)
        if 'working_status' in have and (
                'permit_register' in have
                or rw.report_inputs(project_id, year, month).get('exclusions')):
            try:
                ws_long = _load_ws(project_id, year, month)
            except Exception:                               # noqa: BLE001
                ws_long = None      # each check below reads it again and says why
        try:
            qs += edge_questions(project_id, year, month, ws_long=ws_long)
        except Exception as e:                              # noqa: BLE001
            qs.append(_q('edge', 'info', 'Window edges not checked', str(e),
                         'data', 'Open the data set'))
        try:
            qs += permit_questions(project_id, year, month, ws_long=ws_long)
        except Exception as e:                              # noqa: BLE001
            qs.append(_q('permit', 'info', 'Permit check not run', str(e),
                         'data', 'Open the data set'))

    for r in inputs['pm']:
        if r['flags']:
            qs.append(_q('pm', 'warn', 'PM record',
                         f"#{r['id']} · block {r.get('affected_blocks') or '—'} · "
                         f"{r.get('date_from') or '?'} · {rw.fmt_hours(r.get('hours'))} h — "
                         + '; '.join(r['flags']), 'inputs.pm', 'Open the PM record', ref=r['id']))
    for r in inputs['manual']:
        if r['flags']:
            qs.append(_q('manual', 'warn', 'Manual downtime',
                         f"#{r['id']} · block {r.get('block')} · {r.get('date_from')} · "
                         f"{rw.fmt_hours(r.get('downtime_h'))} h — " + '; '.join(r['flags']),
                         'inputs.manual', 'Open the downtime row', ref=r['id']))

    try:
        rows = rw.corrective_rows(project_id, year, month)
    except Exception:                                       # noqa: BLE001
        rows = []
    for r in rows:
        why = rw.cm_skip_reason(r)
        if why not in ('no_block', 'conflict', 'open'):
            continue
        text = (r.get('fault') or r.get('action') or '').strip()[:70]
        title = {'no_block': '3.2 record without a plant block',
                 'conflict': '3.2 record in sync conflict',
                 'open': '3.2 record still open'}[why]
        qs.append(_q('cm', 'warn' if why != 'open' else 'info', title,
                     f"{r.get('date') or '?'} · “{text}”", 'text.works',
                     'Open corrective works', ref=r.get('id')))

    expected = mds.expected_days(year, month, today)
    for row in mds.coverage_grid(project_id, year, month):
        if not row['file']:
            continue                                        # 'missing' asks for it
        if not row['analysed']:
            qs.append(_q('gap', 'info', 'Coverage not read yet', row['label'],
                         'data', 'Open the data set'))
            continue
        none = [d for d in expected if d not in row['days']]
        part = [d for d in expected if row['days'].get(d) == 'partial']
        bits = []
        if none:
            bits.append(f'no data {_day_ranges(none)}')
        if part:
            bits.append(f'part days {_day_ranges(part)}')
        gaps = [g for g in row['gaps'] if g[0][:7] == f'{year:04d}-{month:02d}']
        if gaps:
            bits.append('gaps ' + ', '.join(
                f"{g[0][8:10]}.{g[0][5:7]} {g[0][11:]} → {g[1][11:]} ({g[2]:g} h)" for g in gaps[:3])
                + (f' and {len(gaps) - 3} more' if len(gaps) > 3 else ''))
        if bits:
            qs.append(_q('gap', 'warn', 'Data gap', f"{row['label']} · " + '; '.join(bits),
                         'data', 'Add the file again'))

    for m in mds.month_end(project_id, year, month):
        if not m['present'] and not m['required']:        # required: 'missing' above
            qs.append(_q('month_end', 'warn' if m['type'] != 'customer_poi' else 'info',
                         'Month-end file not added', f"{m['label']} for {month_name}",
                         'data', 'Add files'))

    if site == 'tashkent':
        qs += customer_file_questions(project_id, year, month)

    qs.sort(key=lambda q: SEVERITY_ORDER.get(q['severity'], 9))
    return qs


def needs_confirmation(questions: List[dict]) -> List[dict]:
    """The questions a generation must be confirmed over (all but info)."""
    return [q for q in questions if q['severity'] in ('crit', 'warn')]
