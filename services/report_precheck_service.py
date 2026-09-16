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

Generating with open questions or an incomplete data set is allowed after an
explicit confirmation, which the version's manifest records.
"""

import datetime
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


def edge_questions(project_id: int, year: int, month: int, exclusions=None) -> List[dict]:
    """Exclusion windows whose edges the SCADA working status contradicts."""
    f = mds.active_by_type(project_id, year, month).get('working_status')
    exclusions = exclusions if exclusions is not None else \
        (rw.report_inputs(project_id, year, month).get('exclusions') or [])
    if not f or not exclusions:
        return []
    from services.tashkent_report_service import load_lc_working_status, exclusion_edge_report
    ws = load_lc_working_status(f['abs_path'])
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

    if scada and site == 'tashkent':
        try:
            qs += edge_questions(project_id, year, month)
        except Exception as e:                              # noqa: BLE001
            qs.append(_q('edge', 'info', 'Window edges not checked', str(e),
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

    qs.sort(key=lambda q: SEVERITY_ORDER.get(q['severity'], 9))
    return qs


def needs_confirmation(questions: List[dict]) -> List[dict]:
    """The questions a generation must be confirmed over (all but info)."""
    return [q for q in questions if q['severity'] in ('crit', 'warn')]
