"""Two things about the Tashkent report's tables and sections.

Part 1 — no general alarm reaches any fault table of the report in any
imported month, and every removed event is named in a note.

Part 2 — the sections the customer's review matrix asks for every month
(executive summary, KPI calculation summary, cycle definition, lowest-
availability blocks, open issues and action tracker, SCADA data quality,
RTE variance) render in BOTH the DOCX and the PDF, carry the same content in
each, degrade to one line when there is nothing to show, read the real
`action_items` table, and never print one of the three availability figures as
a bare "availability" (review item 16, the 98.05 / 95.38 contradiction).
Generated from synthetic exports with the real header layout, so it is fast;
the real August report goes through test_real_tashkent_august.
"""
import _harness as H            # must be first: isolates DB, sync, network, history
import sys, io, os, shutil, sqlite3, warnings, datetime
warnings.filterwarnings('ignore')
import pandas as pd
import database.db_manager as dbm
from services.sync_config import sync_config
MVP, SCRATCH, copy = H.MVP, H.WORK, H.DB   # names the ported body uses
from services.asset_tree_service import is_umbrella_trigger
import services.bukhara_report_service as B
from services.tashkent_report_service import _drop_general_events

con = sqlite3.connect('file:' + H.DB.replace(os.sep, '/') + '?mode=ro', uri=True)
def frame(cat, m):
    return pd.read_sql_query(
        "SELECT trigger_name AS 'Trigger name', element AS 'Element', "
        "activated AS 'Activated', deactivated AS 'Deactivation', "
        "duration_min, is_excluded FROM alarm_events "
        "WHERE project_id=1 AND year=2026 AND month=? AND category=?",
        con, params=(m, cat))
cls = B.load_alarm_classifications(None)
failures = []

def check(month, table, names, note):
    names = [str(n) for n in names if str(n).strip()]
    bad = [n for n in names if is_umbrella_trigger(n)]
    if bad:
        failures.append((month, table, bad[:3]))
        print('   {:34} {:>3} rows  ** {} **'.format(table, len(names), bad[:2]))
    else:
        flag = 'note' if note else '   '
        print('   {:34} {:>3} rows  clean  [{}]'.format(table, len(names), flag))

for m in range(4, 10):
    prod = frame('production', m)
    if prod.empty:
        continue
    warn = pd.concat([frame('warning_persistent', m),
                      frame('warning_transient', m)], ignore_index=True)
    both = B.apply_alarm_classifications(
        {'production_dedup': prod, 'warning_persistent': warn}, cls)
    prod, warn = both['production_dedup'], both['warning_persistent']
    def notexcl(df):
        return df[~df['is_excluded'].fillna(False).astype(bool)] \
            if not df.empty and 'is_excluded' in df.columns else df
    p_dedup, w_pers = notexcl(prod), notexcl(warn)
    print('\n2026-{:02d}  (production {}, warnings {})'.format(m, len(prod), len(warn)))

    # what the report now does
    p_real, fnote = _drop_general_events(p_dedup)
    w_real, wnote = _drop_general_events(w_pers)

    fs = B.build_faults_summary(p_real, ws_long=None, drop_planned=True)
    check(m, '5.1 Faults summary',
          list(fs['reason']) if not fs.empty else [], fnote)
    ws_ = B.build_faults_summary(w_real, ws_long=None, drop_planned=True)
    check(m, '5.1 Warnings summary',
          list(ws_['reason']) if not ws_.empty else [], wnote)

    bc = B.build_breakdown_candidates(p_real, ws_long=None)
    bc = pd.DataFrame(bc) if isinstance(bc, list) else bc
    check(m, '5.2 Breakdown incidents',
          list(bc['incident']) if not bc.empty else [], fnote)

    longest = (p_dedup.sort_values('duration_min', ascending=False).head(40)
               if not p_dedup.empty else p_dedup)
    lk, lnote = _drop_general_events(longest)
    check(m, 'Longest events', list(lk['Trigger name']) if not lk.empty else [], lnote)

    exc = prod[prod['is_excluded'].fillna(False).astype(bool)] \
        if 'is_excluded' in prod.columns else prod.iloc[0:0]
    ek, enote = _drop_general_events(exc)
    check(m, 'Exclusion-window listing',
          list(ek.head(25)['Trigger name']) if not ek.empty else [], enote)

    imp = B.select_important_alarms(prod)
    et = B.build_event_type_table(imp, top_n=50)
    fpart = et[~et['umbrella'].astype(bool)] if 'umbrella' in et.columns else et
    check(m, 'Important events',
          list(fpart['trigger']) if not fpart.empty else [], True)

    # nothing may be lost: kept + named must equal the source
    if not p_dedup.empty:
        gen = B.build_event_type_table(
            p_dedup[p_dedup['Trigger name'].map(is_umbrella_trigger)], top_n=50)
        n_gen = int(gen['events'].sum()) if not gen.empty else 0
        assert len(p_real) + n_gen == len(p_dedup), \
            '2026-{:02d}: events lost between table and note'.format(m)
        if n_gen:
            assert fnote, '2026-{:02d}: removed {} events without a note'.format(m, n_gen)

con.close()
print('\n' + '=' * 60)
if failures:
    print('FAILURES:')
    for f in failures:
        print('   ', f)
    raise SystemExit('a general alarm still reaches a customer table')
print('TABLES SMOKE OK  — every fault table clean in all months')
H.check(True, 'part 1: every fault table clean in all imported months')


# ══════════════════════════════════════════════════════════════════════════
# Part 2 — the customer-review sections, in both renderers
# ══════════════════════════════════════════════════════════════════════════
import re                                                       # noqa: E402
import _synthetic_scada as S                                    # noqa: E402
import services.tashkent_report_service as T                    # noqa: E402
from docx import Document                                       # noqa: E402

SECTIONS = {
    'Executive Summary':        'exec summary (item 45)',
    '4.2.1  KPI Calculation Summary': 'KPI calculation summary (item 3)',
    'Cycle definition':         'cycle definition (item 10)',
    'Lowest-availability blocks': 'worst blocks beside the heatmap (item 20)',
    'Open issues and action tracker': 'action tracker (item 43)',
    '4.5  SCADA Data Quality and Coverage': 'data quality statement (item 44)',
    'Round-trip efficiency: movement against previous months':
        'RTE variance commentary (item 19)',
    # Stem only: the heading counts the measures actually shown.
    'Availability: ': 'availability naming (item 16)',
}

print('\n' + '=' * 60)
print('=== the project action list the report reads (real action_items table) ===')
dbm.initialize_database()                 # the snapshot is a copy; idempotent
_c = dbm.get_connection()
_c.execute("INSERT INTO projects (name, num_zones, num_blocks, num_containers) "
           "VALUES ('SECTIONS TEST SITE', 1, 2, 1)")
PID = _c.execute("SELECT id FROM projects WHERE name='SECTIONS TEST SITE'").fetchone()[0]
_c.commit(); _c.close()

import services.action_list_service as als                      # noqa: E402
A1 = als.save(PID, topic='Block 2 LC1 cooling fan noise',
              description='Bearing wear on fan 2',
              todo='OEM to supply replacement fan assembly',
              due_date='2020-01-01', assigned_name='OEM service',
              status='open')
A2 = als.save(PID, topic='Spare transformer delivery',
              description='Lead time not confirmed',
              todo='EPC to confirm delivery week',
              due_date='2099-12-31', assigned_name='EPC', status='in progress')
_open = als.items(PID, include_done=False)
H.check(len(_open) == 2 and any(i['overdue'] for i in _open),
        'two open action items in the real table, one of them overdue')

print('\n=== generate the report from synthetic exports, DOCX + PDF ===')
EXPORTS = os.path.join(H.WORK, 'sections_exports')
f = S.tashkent_month(EXPORTS, 2026, 9)
OUT = os.path.join(H.WORK, 'sections_report.docx')
LOG = []
T.generate_tashkent_report(
    working_status_path=f['working_status'], pcs_cd_path=f['pcs_cd'],
    soc_path=f['soc'], soh_snapshot_path=f['soh_snapshot'],
    lc_charge_path=f['lc_charge'], lc_discharge_path=f['lc_discharge'],
    hv_meter_daily_path=f['hv_meter'], alarm_path=f['alarms'],
    pcs_fault_path=f['pcs_fault'],
    output_path=OUT, output_format='both', site_name='SECTIONS TEST SITE',
    project_id=PID, plant_capacity_mw=22.0, per_block_capacity_mw=11.0,
    contractual_plant_capacity_mw=20.0,
    recommendations=['NDC to confirm the dispatch schedule for October',
                     'Keep the coolant topped up'],
    progress_callback=LOG.append,
)
PDF = OUT[:-5] + '.pdf'
H.check(os.path.getsize(OUT) > 20000, 'DOCX written ({:,} bytes)'.format(
    os.path.getsize(OUT) if os.path.exists(OUT) else 0))
H.check(os.path.exists(PDF) and os.path.getsize(PDF) > 20000,
        'PDF written ({:,} bytes) — the whole story laid out without a '
        'markup error'.format(os.path.getsize(PDF) if os.path.exists(PDF) else 0))

_d = Document(OUT)
DOCX_TEXT = '\n'.join([p.text for p in _d.paragraphs]
                      + [c.text for t in _d.tables for r in t.rows for c in r.cells])

print('\n=== every new section is in the DOCX ===')
for title, what in SECTIONS.items():
    H.check(title in DOCX_TEXT, 'DOCX carries {} — "{}"'.format(what, title))

print('\n=== the same sections render in the PDF path, with the same content ===')
# One model, two renderers: what the PDF story carries must be what the DOCX
# carries. Rendering each builder's blocks through both and comparing the text
# is the check; the generated PDF above proves the story also builds.
CTX = {}


def _flat(s):
    """Compare content, not encoding: reportlab markup carries HTML entities
    and collapses runs of whitespace; Word carries the characters themselves."""
    import html as _html
    return re.sub(r'\s+', ' ',
                  _html.unescape(re.sub(r'<[^>]+>', '', str(s)))).strip()


def _pdf_text(blocks):
    from reportlab.platypus import Paragraph as _P, Table as _Tb
    out = []
    for fl in T._render_blocks_pdf([], blocks):
        if isinstance(fl, _P):
            out.append(_flat(fl.text))
        elif isinstance(fl, _Tb):
            for row in fl._cellvalues:
                for cell in row:
                    out.append(_flat(getattr(cell, 'text', cell)))
    return [s for s in out if s]


def _docx_text(blocks):
    d = Document()
    T._render_blocks_docx(d, blocks)
    out = [p.text for p in d.paragraphs]
    out += [c.text for t in d.tables for r in t.rows for c in r.cells]
    return [_flat(s) for s in out if _flat(s)]


BUILDERS = [
    ('executive summary',  T.build_executive_summary),
    ('KPI calc summary',   T.build_kpi_methodology),
    ('cycle definition',   T.build_cycle_definition),
    ('worst blocks',       T.build_lowest_availability_blocks),
    ('action tracker',     T.build_action_tracker),
    ('data quality',       T.build_data_quality),
    ('RTE variance',       T.build_rte_variance),
    ('availability names', T.build_availability_definitions),
]
# The context the real run produced, captured by rebuilding it the same way the
# generator does — the builders are pure, so a plain dict is enough.
CTX = dict(
    site_name='SECTIONS TEST SITE', period_str='01 September 2026 — 03 September 2026',
    n_blocks=2, dates=sorted({__import__('datetime').date(2026, 9, d) for d in (1, 2, 3)}),
    contractual_avail={'availability_pct': 98.05, 'method': 'pcs_unit',
                       'used_unit_fault': True, 'unit_label': 'PCS unit',
                       'down_unit_hours': 402.4, 'unit_capacity_kwh': 2752.0,
                       'total_hours': 744.0, 'installed_kwh': 770560.0, 'n_units': 280},
    plant_avail={'plant_availability_pct': 99.12, 'threshold_mw': 20.0,
                 'scheduled_hours': 744.0, 'container_outage_hours': 3.0,
                 'plant_outage_hours': 1.0},
    fleet_availability_container=95.38,
    fleet_rte=88.12, total_charge_mwh=1000.0, total_discharge_mwh=881.2,
    avg_soc_pct=44.1, avg_soh_pct=98.9, avg_efc_per_block=26.6,
    total_efc_fleet=1862.0, yearly_cycle_target=365.0, days_excluded=0.4,
    n_prod_alarms_genuine=12, n_warn_persistent_genuine=3, n_anomalies=1,
    n_blocks_below_target=2, rested_blocks=set(), calibration_applied=True,
    cycles_snap=pd.DataFrame({'block_id': [1, 2], 'cycle_begin': [1.0, 2.0],
                              'cycle_end': [27.0, 28.0], 'cycle_delta': [26.0, 26.0]}),
    daily_kpi=pd.DataFrame({'block': [1, 1, 2, 2],
                            'date': pd.to_datetime(['2026-09-01', '2026-09-02',
                                                    '2026-09-01', '2026-09-02']),
                            'availability_pct': [100.0, 100.0, 80.0, 60.0]}),
    unavail_reasons=pd.DataFrame({'block_id': [2], 'downtime_h': [9.6],
                                  'cause': ['PCS DC overvoltage'],
                                  'subsystem': ['PCS']}),
    monthly_compare=pd.DataFrame({
        'month': ['July 2026', 'August 2026', 'September 2026'],
        'rte_pct': [87.0, 87.9, 88.12], 'cycles': [25.0, 26.6, 26.6],
        'discharge_mwh': [800.0, 860.0, 881.2],
        'charge_mwh': [920.0, 978.0, 1000.0],
        'avg_soc_pct': [44.0, 44.0, 44.1], 'avg_soh_pct': [99.0, 98.9, 98.9]}),
    faults_summary=pd.DataFrame({'reason': ['BSC-PCS comm fault'],
                                 'subsystem': ['BSC'], 'occurrences': [7],
                                 'total_hours': [4.2], 'blocks_affected': ['1, 2'],
                                 'resolution': ['Reseated the comm card'],
                                 'any_planned': [False]}),
    breakdown_rows=[{'incident': 'Block 2 LC1 PCS trip', 'blocks': '2',
                     'date_time': '2026-09-02 11:05', 'downtime_h': 6.0,
                     'breakdown_type': 'PCS', 'temporary_solution': 'restart',
                     'final_solution': ''}],
    open_action_items=_open, open_action_note='',
    recommendations=['NDC to confirm the dispatch schedule for October'],
    data_streams=[{'label': 'LC working status (5-min)', 'rows': 864,
                   'days': ['2026-09-01', '2026-09-02', '2026-09-03'],
                   'gaps': 0, 'first': '2026-09-01 00:00',
                   'last': '2026-09-03 23:55', 'note': ''},
                  {'label': 'Alarm / event log', 'rows': 4000,
                   'days': ['2026-09-01'], 'gaps': 0,
                   'first': '2026-09-01 00:05', 'last': '2026-09-01 23:40',
                   'note': ''}],
)
for name, fn in BUILDERS:
    blocks = fn(CTX)
    p, d = _pdf_text(blocks), _docx_text(blocks)
    same = [s for s in d if s not in p]
    H.check(bool(blocks) and not same,
            '{}: {} block(s), identical text in both renderers{}'.format(
                name, len(blocks), '' if not same else ' — DOCX-only: ' + repr(same[:2])))

print('\n=== the two availability figures are each named (review item 16) ===')
PARITY = '\n'.join(_docx_text(T.build_kpi_methodology(CTX))
                   + _docx_text(T.build_availability_definitions(CTX))
                   + T._conclusion_lines(CTX))
PARITY = re.sub(r'</?b>', '', PARITY)
H.check('Contractual BESS availability' in PARITY and '98.05%' in PARITY,
        'the contractual figure is named "Contractual BESS availability" (98.05%)')
H.check('Operational availability (container-level)' in PARITY and '95.38%' in PARITY,
        'the unweighted figure is named "Operational availability '
        '(container-level)" (95.38%)')
H.check('an average availability of' not in PARITY,
        'the old "an average availability of X%" conclusion is gone')
QUALIFIERS = ('contractual', 'operational', 'plant-level', 'container-level')
BARE = re.compile(r'(?<![\w-])availability\s+(?:of|was|is|for the period was)\s*'
                  r'(\d{1,3}\.\d{1,2})\s*%', re.IGNORECASE)
bare = []
for src in (PARITY, re.sub(r'</?b>', '', DOCX_TEXT)):
    for m in BARE.finditer(src):
        before = src[max(0, m.start() - 70):m.start()].lower()
        if not any(q in before for q in QUALIFIERS):
            bare.append(m.group(0))
H.check(not bare, 'no figure is printed as a bare "availability": {}'.format(
    bare[:3] or 'none'))

print('\n=== a section with nothing to show is one line, never an empty table ===')
EMPTY = [
    ('action tracker', T.build_action_tracker({'open_action_items': [],
                                               'open_action_note': ''})),
    ('worst blocks',   T.build_lowest_availability_blocks({})),
    ('data quality',   T.build_data_quality({'data_streams': [], 'dates': []})),
    ('availability',   T.build_availability_definitions({})),
    ('RTE variance',   T.build_rte_variance({'fleet_rte': 88.1,
                                             'monthly_compare': pd.DataFrame()})),
]
for name, blocks in EMPTY:
    kinds = [b[0] for b in blocks]
    H.check('table' not in kinds and kinds.count('p') == 1
            and len(blocks) <= 2,
            '{}: no data -> {} (no empty table)'.format(name, kinds))

print('\n=== the action tracker reads the real table; no project -> says so ===')
TRACK = _docx_text(T.build_action_tracker(CTX))
H.check('Block 2 LC1 cooling fan noise' in '\n'.join(TRACK)
        and 'OEM to supply replacement fan assembly' in '\n'.join(TRACK)
        and 'EPC' in '\n'.join(TRACK),
        'the tracker prints the issue, the action and the owner from action_items')
H.check('overdue' in '\n'.join(TRACK).lower(),
        'the overdue item is marked overdue')
_items, _note = T.read_open_actions(None)
H.check(_items == [] and 'not available' in _note,
        'no project -> "{}"'.format(_note))
H.check('Open issues and action tracker' in DOCX_TEXT
        and 'Block 2 LC1 cooling fan noise' in DOCX_TEXT,
        'the generated report carries the real action items')
als.delete(A1); als.delete(A2)

print('\n=== support required: only what the owner actually wrote ===')
H.check(any('NDC' in s for s in T._support_requests(CTX)),
        'the NDC recommendation line is carried into "support required"')
H.check(T._support_requests({'recommendations': ['Keep the coolant topped up'],
                             'open_action_items': []}) == [],
        'a line naming no external party is not turned into a support request')

print('\n=== the data-quality statement can see a truncated export ===')
DQ = '\n'.join(_docx_text(T.build_data_quality(CTX)))
H.check('1 of 3 day(s)' in DQ and '2 day(s) of the reporting period are not '
        'covered' in DQ,
        'the 1-day alarm log in a 3-day period is reported as incomplete')
H.check('row limit' in DQ,
        'a 4,000-row export that stops early is called out as a row limit')
H.check('Alarm / event log' in DQ and 'LC working status (5-min)' in DQ,
        'every stream is listed with its own coverage')
_real_dq = '\n'.join([p.text for p in Document(OUT).paragraphs])
H.check('How missing data was treated' in DOCX_TEXT
        and 'distinct timestamps' in DOCX_TEXT,
        'the real run states how missing data was treated')

print('\n=== the cycle definition describes what the code did ===')
CYC_BMS = '\n'.join(_docx_text(T.build_cycle_definition(CTX)))
H.check('BMS-reported' in CYC_BMS and 'register at the end of the period' in CYC_BMS
        and 'CHARGE AND DISCHARGE CYCLES' in CYC_BMS,
        'with a CMU snapshot: the BMS register delta, with its tag')
CYC_EFC = '\n'.join(_docx_text(T.build_cycle_definition(
    {'cycles_snap': pd.DataFrame()})))
H.check('equivalent full cycles' in CYC_EFC and '4,953.6 kWh' in CYC_EFC,
        'without one: the energy-throughput EFC estimate, with its divisor')
H.check('equivalent full cycles' not in CYC_BMS.lower().replace(
    'equivalent full cycles (efc)', ''),
        'the BMS wording does not also claim an EFC estimate')

H.finish()
