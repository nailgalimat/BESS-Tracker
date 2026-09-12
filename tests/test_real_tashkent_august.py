"""Generate the real Tashkent August 2026 report, PDF and DOCX, from the SCADA
exports, and read both back.

Frame-level tests cannot catch a crash in the real pipeline (one shipped on
2026-09-10: a frame without Deactivation reached build_event_type_table). Only
generating the report does. Several minutes; run with `tests\\run.py --real`.

GOLDEN holds customer-visible numbers that have been accepted. When one changes
on purpose, update it here in the same commit — that is the approval.
"""
import _harness as H            # must be first: isolates DB, sync, network, history
import hashlib
import os
import re
import warnings
warnings.filterwarnings('ignore')

GOLDEN = {
    # contractual method, with every availability input the Monthly Reports page
    # passes. Was 99.54 until 2026-09-11/12, when August's exclusion windows were
    # corrected against the SCADA data — starts moved to when the plant actually
    # went down, restoration of the blocks that did not come back added, and the
    # KKS windows stretched to the stop they covered. Both rounds user-approved;
    # exclusion_edge_report is what found them.
    'availability_pct': 99.81,
    # calendar year to date: counter at 31 Aug minus counter at 1 Mar, plus the
    # January and February records (snapshots begin in March 2026)
    'cycles_in_year': 249.6,
    'cycles_month': 26.6,          # CMU counter delta, August
    'cycles_lifetime': 256.4,      # CMU counter, 31 August
}

SRC = H.require_scada('August', 'SCADA Raw data')


def f(name):
    return os.path.join(SRC, name)


def _digest(p):
    return hashlib.sha256(open(p, 'rb').read()).hexdigest() if os.path.exists(p) else None


real_hist = H.REAL_HISTORY['DEFAULT_HISTORY_PATH']
hist_before = _digest(real_hist)

import services.report_workflow_service as rw                      # noqa: E402
from services.tashkent_report_service import generate_tashkent_report  # noqa: E402
from services.asset_tree_service import UMBRELLA_FOOTNOTE             # noqa: E402

# Exactly what the Monthly Reports page passes — capacities, exclusions, manual
# downtime, PM as downtime, balancing — so the figures checked are the ones the
# customer receives.
INPUTS = rw.report_inputs(1, 2026, 8)
for k in ('exclusions', 'manual_unavailability', 'balancing_periods'):
    print('{:<22} {}'.format(k, len(INPUTS.get(k) or [])))

ARGS = dict(
    INPUTS,
    working_status_path=f('LC working status.xlsx'),
    pcs_cd_path=f('PCS_charge_discharge_status.xlsx'),
    soc_path=f('SOC august.xlsx'),
    soh_snapshot_path=f('Soh of last of the month.xlsx'),
    lc_charge_path=f('LC daily charge.xlsx'),
    lc_discharge_path=f('LC daily discharge.xlsx'),
    hv_meter_daily_path=f('HV meter daily export and import.xlsx'),
    alarm_path=f('Alarms report.XLSX'),
    cycles_first_day_path=f('Cycles first day of the month.xlsx'),
    cycles_last_day_path=f('Cycles last day of the month.xlsx'),
    lc_total_charge_path=f('LC total charge.xlsx'),
    lc_total_discharge_path=f('LC total discharge.xlsx'),
    pcs_fault_path=f('PCS fault status.xlsx'),
    site_name='ACWA BESS Tashkent',
    project_details={'Plant': 'Tashkent BESS'},
    progress_callback=lambda *a, **k: None,
)

# Word only: that is what the report is delivered as. The PDF renderer still
# lays the report out (its story is built either way, so a crash in it still
# shows up here) but writes no pages unless a PDF is asked for.
docx = os.path.join(H.WORK, 'August_2026.docx')
generate_tashkent_report(output_path=docx, output_format='docx', **ARGS)
H.check(os.path.exists(docx) and os.path.getsize(docx) > 50000,
        'DOCX written ({:,} bytes) — a DOCX failure is otherwise only logged'.format(
            os.path.getsize(docx) if os.path.exists(docx) else 0))
H.check(not os.path.exists(docx.rsplit('.', 1)[0] + '.pdf'),
        'no PDF left beside it')

from docx import Document                                           # noqa: E402

_doc = Document(docx)
txt = '\n'.join([p.text for p in _doc.paragraphs]
                + [c.text for t in _doc.tables for r in t.rows for c in r.cells])

print('\n=== general alarms ===')
H.check(txt.count(UMBRELLA_FOOTNOTE[:60]) >= 1, 'the general-alarm note is printed')
H.check('Input dry node fault' in txt, 'the held-back trigger is named in a note')
for gone in ('Dry Node Input Fault', 'LC System Alarm State', 'LCU Other Alarm'):
    H.check(gone not in txt, '"{}" names no table row'.format(gone))
H.check('Unclassified' not in txt, 'no blank-resolution "Unclassified" row')

print('\n=== availability ===')
m = re.search(r'availability for the period was ([\d.]+)%', txt)
avail = float(m.group(1)) if m else None
H.check(avail is not None and abs(avail - GOLDEN['availability_pct']) < 0.005,
        'BESS availability {}% (accepted {}%)'.format(avail, GOLDEN['availability_pct']))

print('\n=== fault names are printed whole ===')
# The tables used to slice names at 38-50 characters, so the customer read
# "PCS - Converter Unit 1 Fault Status 1: AC" and never learned it was an AC
# over-voltage. Both renderers wrap cell text, so nothing needs cutting.
import sqlite3                                                     # noqa: E402
_c = sqlite3.connect('file:{}?mode=ro'.format(H.DB.replace(os.sep, '/')), uri=True)
triggers = [t for (t,) in _c.execute(
    "SELECT DISTINCT trigger_name FROM alarm_events WHERE project_id=1 "
    "AND year=2026 AND month=8") if t and len(t) > 40]
_c.close()
cells = [c.text.strip() for t in Document(docx).tables for r in t.rows for c in r.cells]
# a cell that is the *start* of a known fault name, but not the whole name
cut = sorted({c for c in cells if len(c) >= 25
              and any(t.startswith(c) and t != c for t in triggers)})
H.check(not cut, 'no cell holds a half-written name ({} long names in the month){}'.format(
    len(triggers), '' if not cut else ': ' + repr(cut[:2])))
docx_text = '\n'.join(cells)
shown = [t for t in triggers if t in docx_text]
H.check(bool(shown), 'long names do appear in full, e.g. "{}"'.format(
    max(shown, key=len) if shown else '-'))

print('\n=== 4.4.1 unavailability reasons: no grid-outage edges on Aug 7-12 ===')
edge = []
for t in Document(docx).tables:
    hdr = [c.text.strip() for c in t.rows[0].cells]
    if 'Dominant Cause' in hdr:
        for row in t.rows[1:]:
            cells = [c.text.strip() for c in row.cells]
            print('   ' + ' | '.join(cells))
            if any(d in cells[1] for d in ('08-Aug', '09-Aug', '10-Aug', '11-Aug', '12-Aug')):
                edge.append(cells[0] + ' ' + cells[1])
H.check(not edge, 'no incident spans the corrected outage nights: {}'.format(edge or 'none'))

print('\n=== cycles ===')


def docx_value(path, label):
    for t in Document(path).tables:
        for row in t.rows:
            cells = [c.text.strip() for c in row.cells]
            if len(cells) > 2 and cells[1] == label:
                return float(cells[2])
    return None


for key, label in (('cycles_month', 'Number of Cycles in Reported Month'),
                   ('cycles_in_year', 'Accumulative Number of Cycles in one Year'),
                   ('cycles_lifetime', 'Accumulative Number of Cycles')):
    v = docx_value(docx, label)
    H.check(v is not None and abs(v - GOLDEN[key]) < 0.05,
            '{}: {} (accepted {})'.format(label, v, GOLDEN[key]))

print('\n=== a second run of the same month changes nothing ===')
docx2 = os.path.join(H.WORK, 'August_2026_rerun.docx')
generate_tashkent_report(output_path=docx2, output_format='docx', **ARGS)
H.check(docx_value(docx2, 'Accumulative Number of Cycles in one Year')
        == docx_value(docx, 'Accumulative Number of Cycles in one Year'),
        'Cycles in one Year stable on re-run (it used to grow by a month per run)')

H.check(_digest(real_hist) == hist_before, 'the real KPI history file was not touched')

H.finish()
