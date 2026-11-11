"""The month's data set: files recognised from their headers, copied next to the
database, coverage by day, and a re-generation that never picks files again.

QA S1 gate G10 (files copied with SHA-256, no re-pick) and WF-WEEK-01/02,
DATA-REPRO-04/05, PERF-06. Synthetic exports with the real header layout; the
real August and Bukhara folders are recognised too when they are on this PC.
"""
import _harness as H            # must be first: isolates DB, sync, network, history
import os
import shutil
import time
import warnings
warnings.filterwarnings('ignore')

import pandas as pd

import database.db_manager as dbm
H.fresh_db('dataset.db')
import _synthetic_scada as S
import services.parse_cache as pc
import services.month_dataset_service as mds
import services.report_workflow_service as rw

c = dbm.get_connection()
c.execute("INSERT INTO projects (name, num_zones, num_blocks, num_containers) VALUES ('TK', 1, 70, 1)")
PID = c.execute("SELECT id FROM projects WHERE name='TK'").fetchone()[0]
c.commit(); c.close()
rw.save_project_config(PID, site_type='tashkent')
Y, M = 2026, 9

SRC = os.path.join(H.WORK, 'exports_sep')
gap = [('2026-09-02 13:55', '2026-09-02 16:05')]
files = S.tashkent_month(SRC, Y, M, days=(1, 2, 3))
# LC working status: a 2.25 h gap on the 2nd, and the 3rd ends at noon
ts = S.stamps('2026-09-01 00:00', '2026-09-03 12:00', drop=gap)
files['working_status'] = S.wide(files['working_status'], ts,
                                 S.lc_columns('SYSTEM WORKING STATUS'), lambda t, c: 'RUNNING')
files['cycles_first_day'] = S.wide(os.path.join(SRC, 'Cycles first day of the month.xlsx'),
                                   S.stamps('2026-08-31 23:00', '2026-09-01 01:00'),
                                   S.cmu_columns(), lambda t, c: 226)
files['cycles_last_day'] = S.wide(os.path.join(SRC, 'Cycles last day of the month.xlsx'),
                                  S.stamps('2026-09-30 00:00', '2026-09-30 02:00'),
                                  S.cmu_columns(), lambda t, c: 250)
files['lc_total_charge'] = S.wide(os.path.join(SRC, 'LC total charge.xlsx'),
                                  S.stamps('2026-09-01', '2026-09-01'),
                                  S.lc_columns('TOTAL CHARGE ENERGY (kWh)'), lambda t, c: 160000)
plan = S.wide(os.path.join(SRC, 'Equalization Plan 90 days.xlsx'),
              S.stamps('2026-09-01', '2026-09-02', minutes=1440), ['Block', 'Target'], lambda t, c: 1)
mid_cycles = S.wide(os.path.join(H.WORK, 'Cycles mid month.xlsx'),
                    S.stamps('2026-09-15 00:00', '2026-09-15 01:00'), S.cmu_columns(), lambda t, c: 240)

print('=== types recognised from the headers ===')
t0 = time.time()
for key, path in sorted(files.items()):
    got = mds.classify(path, 'tashkent', Y, M)
    H.check(got['status'] == 'recognised' and got['type'] == key,
            '{:<34} -> {} {}'.format(os.path.basename(path), got['status'], got['type']))
dt = time.time() - t0
H.check(dt < 5, 'header-only recognition of {} files took {:.2f} s (PERF-06: <= 5 s)'.format(len(files), dt))
u = mds.classify(plan, 'tashkent', Y, M)
H.check(u['status'] == 'unrecognised', 'a foreign workbook is "unrecognised", not ignored silently')
a = mds.classify(mid_cycles, 'tashkent', Y, M)
H.check(a['status'] == 'ambiguous' and set(a['candidates']) == {'cycles_first_day', 'cycles_last_day'},
        'a cycle snapshot from mid-month is "ambiguous": first or last day? {}'.format(a['candidates']))

print('\n=== added: copied next to the database, with SHA-256 ===')
res = mds.add_files(PID, Y, M, sorted(files.values()) + [plan])
by = {r['name']: r for r in res}
H.check(all(r['status'] == 'added' for r in res if r['name'] != os.path.basename(plan)),
        '{} files added'.format(sum(r['status'] == 'added' for r in res)))
H.check(by[os.path.basename(plan)]['status'] == 'unrecognised',
        'the unrecognised file comes back for a type (nothing stored for it)')
res2 = mds.add_files(PID, Y, M, [plan], {plan: 'customer_poi'})
H.check(res2[0]['status'] == 'added' and res2[0]['type'] == 'customer_poi',
        'the user chose "Customer POI file" for it -> stored')

root = mds.data_root()
H.check(os.path.normcase(root).startswith(os.path.normcase(H.WORK)),
        'data folder derives from DB_PATH -> inside the test work folder: {}'.format(root))
H.check('_MEI' not in root, 'never the PyInstaller temp folder')
stored = mds.list_files(PID, Y, M)
H.check(len(stored) == len(files) + 1, '{} active files in the month'.format(len(stored)))
ok_copy = all(os.path.isfile(f['abs_path'])
              and os.path.normcase(f['abs_path']).startswith(os.path.normcase(mds.month_dir(PID, Y, M)))
              and pc.file_sha256(f['abs_path']) == f['sha256'] for f in stored)
H.check(ok_copy, 'every copy is in report_data/p{}/2026-09/inputs and matches its SHA-256'.format(PID))

again = mds.add_files(PID, Y, M, [files['soc']])
H.check(again[0]['status'] == 'duplicate' and len(mds.list_files(PID, Y, M)) == len(stored),
        'the same file again -> "duplicate", nothing added')

print('\n=== coverage by day ===')
n = mds.analyse_pending(PID, Y, M)
H.check(n == len(stored), '{} files read for coverage'.format(n))
grid = {g['type']: g for g in mds.coverage_grid(PID, Y, M)}
ws = grid['working_status']
print('   working status days:', ws['days'], 'gaps:', ws['gaps'])
H.check(ws['days'].get('2026-09-01') == 'full', '01.09 full')
H.check(ws['days'].get('2026-09-02') == 'partial', '02.09 part day (2.25 h missing)')
H.check(ws['days'].get('2026-09-03') == 'partial', '03.09 part day (ends at noon)')
H.check('2026-09-04' not in ws['days'], '04.09 no data')
H.check(any(g[0] == '2026-09-02 13:55' and g[1] == '2026-09-02 16:10' and abs(g[2] - 2.25) < 0.01
            for g in ws['gaps']), 'the gap 02.09 13:55 -> 16:10 (2.25 h) is found')
H.check(grid['soc']['days'].get('2026-09-03') == 'full', 'SOC 03.09 full')
H.check(set(grid['alarms']['days']) == {'2026-09-01', '2026-09-02', '2026-09-03'},
        'alarms cover 01-03.09')
H.check(grid['pcs_fault']['file'] is not None and grid['lc_charge']['days'].get('2026-09-02') == 'full',
        'hourly LC daily charge counts a full day')
ends = {e['type']: e for e in mds.month_end(PID, Y, M)}
H.check(ends['cycles_first_day']['present'] and ends['cycles_last_day']['present']
        and ends['customer_poi']['present'] and not ends['lc_total_discharge']['present'],
        'month-end checklist: cycles first/last, POI present; LC total discharge not')
H.check(mds.missing_required(PID, Y, M) == [], 'every required file is in: {}'.format(
    mds.missing_required(PID, Y, M)))

print('\n=== the parse cache: a re-generation reads no xlsx ===')
from services.tashkent_report_service import load_lc_working_status, load_pcs_unit_fault
from services.bukhara_report_service import load_alarms
stored_ws = mds.active_by_type(PID, Y, M)['working_status']['abs_path']
before = dict(pc.STATS)
f1 = load_lc_working_status(stored_ws)
f2 = load_pcs_unit_fault(mds.active_by_type(PID, Y, M)['pcs_fault']['abs_path'])
al = load_alarms(mds.active_by_type(PID, Y, M)['alarms']['abs_path'])
H.check(pc.STATS['misses'] == before['misses'] and pc.STATS['hits'] >= before['hits'] + 4,
        'loaders over the stored copies hit the cache: {} -> {}'.format(before, pc.STATS))
ref = load_lc_working_status(files['working_status'])        # original, not in the data set
H.check(pc.STATS['bypass'] > before['bypass'], 'a file outside the data set is read as before')
try:
    pd.testing.assert_frame_equal(f1, ref)
    same = True
except AssertionError as e:
    same = False
    print('   ', str(e)[:300])
H.check(same, 'cached frame gives the loader the same result as reading the xlsx')
f1['working_status'] = 'CHANGED'
H.check((load_lc_working_status(stored_ws)['working_status'] == 'RUNNING').all(),
        'a caller changing a frame does not change the cache')

print('\n=== a newer file replaces the older one; the old copy stays ===')
old_soc = mds.active_by_type(PID, Y, M)['soc']
new_src = os.path.join(H.WORK, 'SOC september v2.xlsx')
S.wide(new_src, S.stamps('2026-09-01', '2026-09-04 23:55'), S.lc_columns('SYSTEM SOC (%)'), lambda t, c: 51.0)
r = mds.add_files(PID, Y, M, [new_src])[0]
H.check(r['status'] == 'replaced' and 'replaces' in r['note'], 'status replaced: {}'.format(r['note']))
H.check(mds.get_file(old_soc['id'])['status'] == 'replaced' and os.path.isfile(old_soc['abs_path']),
        'the older row is "replaced" and its copy is kept (earlier manifests name it)')
misses = pc.STATS['misses']
mds.analyse_pending(PID, Y, M)
H.check(pc.STATS['misses'] == misses + 1, 'new content -> new cache key -> read once')

print('\n=== generation takes the stored copies: no file is picked again ===')
paths = mds.generation_paths(PID, Y, M)
shutil.rmtree(SRC)                          # the Desktop copies are gone
H.check(all(os.path.isfile(p) for p in paths.values()) and 'working_status_path' in paths
        and 'customer_poi' not in ''.join(paths), '{} generator paths, all present after the '
        'originals were deleted'.format(len(paths)))
H.check(paths['soc_path'] == mds.active_by_type(PID, Y, M)['soc']['abs_path'], 'the newest SOC is used')

print('\n=== a locked month cannot change its data set ===')
rw.set_month_lock(PID, Y, M, True)
try:
    mds.add_files(PID, Y, M, [new_src]); H.check(False, 'add refused')
except rw.MonthLockedError:
    H.check(True, 'adding files to a locked month raises MonthLockedError')
try:
    mds.remove_file(mds.active_by_type(PID, Y, M)['soc']['id']); H.check(False, 'remove refused')
except rw.MonthLockedError:
    H.check(True, 'removing a file from a locked month raises MonthLockedError')
rw.set_month_lock(PID, Y, M, False)

print('\n=== the Data step on the page ===')
from PyQt5.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
import ui.monthly_reports_page as mrp
page = mrp.MonthlyReportsPage()
page.proj_combo.setCurrentIndex(page.proj_combo.findData(PID))
page.year_spin.setValue(Y); page.month_combo.setCurrentIndex(M - 1)
page._open_month()
if page._an_worker:
    page._an_worker.wait(60000)
H.check(page.files_table.rowCount() == len(mds.list_files(PID, Y, M)),
        'files table lists the month ({} rows)'.format(page.files_table.rowCount()))
H.check(page.cov_table.rowCount() == 8 and page.cov_table.columnCount() == 30,
        'coverage grid: 8 data types x 30 days')
H.check('Month end' in page.month_end_lbl.text() and '✓ Cycles first day' in page.month_end_lbl.text(),
        'month-end line: {}'.format(page.month_end_lbl.text()[:90]))
extra = S.wide(os.path.join(H.WORK, 'LC total discharge.xlsx'), S.stamps('2026-09-01', '2026-09-01'),
               S.lc_columns('TOTAL DISCHARGE ENERGY (kWh)'), lambda t, c: 150000)
out = page._add_paths([extra, mid_cycles], answers={mid_cycles: 'skip'}, analyse=False)
H.check([r['status'] for r in out] == ['added', 'skipped'],
        'page adds one, skips the unsure one on the user\'s answer: {}'.format([r['status'] for r in out]))
H.check(page.last_add_group.isVisible() or page.last_add_table.rowCount() == 2, 'last upload listed')

print('\n=== customer and site files: the DGR and the permit register ===')
import datetime as _dt
import openpyxl
import services.report_precheck_service as rpc


def make_dgr(path, months):
    wb = openpyxl.Workbook()
    wb.active.title = 'DAILY ESTIMATION'
    ms = wb.create_sheet('Monthly Summary')
    ms.append(['Month', 'RTE', 'Budget Cycle export', 'Export Energy', 'Import Energy',
               'Auxiliary Consumption (Charge)', 'Auxiliary Consumption (Discharge)'])
    for (y, m, last) in months:
        ms.append([_dt.datetime(y, m, last), 0.7711, 31, 6483.1, 8407.2, 200.0, 119.9])
    wb.create_sheet('PTW Register ')       # the real DGR carries one; it is not the register
    wb.save(path)
    return path


def make_register(path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'CPTW Register'
    ws.append(['Commissioning Permit To Work Register'])
    ws.append(['Sr. No', 'CPTW No', 'Issued To', 'Area', 'Discription', 'Issue Date',
               'Valid till', 'PTW Status'])
    for i, r in enumerate(rows, start=1):
        ws.append([i] + list(r))
    wb.save(path)
    return path


qs0 = {q['title'] for q in rpc.customer_file_questions(PID, Y, M)}
H.check(qs0 == {'Customer file not added', 'Permit register not added'}
        and all(q['severity'] == 'info' for q in rpc.customer_file_questions(PID, Y, M)),
        'neither file: two information lines, nothing to confirm')
dgr = make_dgr(os.path.join(H.WORK, 'Customer Daily Generation Report.xlsx'), [(2026, 8, 31), (2026, 9, 30)])
reg = make_register(os.path.join(H.WORK, '(CPTW) Register.xlsx'), [
    ('CPTW-APRS-601', 'SUNGROW', 'RMU-06', 'BESS Preventive Maintenance', '02/09/2026', '02/09/2026', 'Closed'),
    ('CPTW-APRS-602', 'HEEC', 'BESS', 'Painting', '03/09/2026', '05/09/2026', 'Closed'),
    ('CPTW-APRS-603', 'Sungrow', 'BESS Zone3 RMU-22', 'KKS installation', '04/09/2026', '04/09/2026', 'Closed'),
    ('CPTW-APRS-500', 'SUNGROW', 'BESS', 'Old work', '03/08/2026', '05/08/2026', 'Closed')])
for p, key in ((dgr, 'customer_dgr'), (reg, 'permit_register')):
    got = mds.classify(p, 'tashkent', Y, M)
    H.check(got['status'] == 'recognised' and got['type'] == key,
            '{} -> {} {}'.format(os.path.basename(p), got['status'], got['type']))
H.check(mds.classify(dgr, 'bukhara', Y, M)['status'] == 'unrecognised', 'not taken for a Bukhara month')
ends_before = [e['type'] for e in mds.month_end(PID, Y, M)]
res = mds.add_files(PID, Y, M, [dgr, reg])
H.check([r['status'] for r in res] == ['added', 'added'], 'both added automatically')
H.check(mds.missing_required(PID, Y, M) == [], 'neither is required')
H.check([e['type'] for e in mds.month_end(PID, Y, M)] == ends_before, 'month-end checklist unchanged')
H.check(not any(g['type'] in ('customer_dgr', 'permit_register') for g in mds.coverage_grid(PID, Y, M)),
        'not in the coverage grid')
paths = mds.generation_paths(PID, Y, M)
H.check(paths.get('customer_report_path', '').endswith('Customer Daily Generation Report.xlsx')
        and paths.get('permit_register_path', '').endswith('(CPTW) Register.xlsx'),
        'generation_paths has customer_report_path and permit_register_path')
mds.analyse_pending(PID, Y, M)
cf = {c['type']: c for c in mds.customer_files(PID, Y, M)}
row = cf['customer_dgr']['coverage'].get('customer_row') or {}
H.check(abs(row.get('rte_pct', 0) - 77.11) < 1e-6 and row.get('export_mwh') == 6483.1,
        'DGR analysed: September row, RTE 77.11 %, export 6,483.1 MWh')
pr = cf['permit_register']['coverage']
H.check(pr.get('permits') == 3 and pr.get('with_block') == 2 and pr.get('unclear') == []
        and mds.permits_for_party(pr['by_party'], 'sungrow') == 2,
        'register analysed: 3 September permits, 2 name a block, 2 issued to Sungrow: {}'.format(pr))
H.check(rpc.customer_file_questions(PID, Y, M) == [], 'both present and good: no question')

old = make_dgr(os.path.join(H.WORK, 'DGR august only.xlsx'), [(2026, 8, 31)])
mds.add_files(PID, Y, M, [old])
mds.analyse_pending(PID, Y, M)
w = rpc.customer_file_questions(PID, Y, M)
H.check(len(w) == 1 and w[0]['severity'] == 'warn' and 'no row' in w[0]['title'],
        'a DGR without the month row -> a warning: {}'.format([q['title'] for q in w]))
H.check(any(q['kind'] == 'customer' and q['severity'] == 'warn' for q in rpc.run_precheck(PID, Y, M)),
        'and run_precheck carries it')

print('\n=== the Data step shows them; "Permits issued to" is saved ===')
page._refresh_data()
st = {k: w['status'].text() for k, w in page._cust_rows.items()}
print('   ', st)
H.check('no September row' in st['customer_dgr'] and page._cust_rows['customer_dgr']['button'].text() == 'Replace…',
        'DGR row warns and offers Replace…')
H.check(st['permit_register'].startswith('3 permits issued in September'), 'register row counts the permits')
page.permit_issued_to.setText('Sungrow')
page._save_permit_issued_to()
H.check(rw.get_project_config(PID).get('permit_issued_to') == 'Sungrow', 'saved to the project')
H.check('2 issued to Sungrow' in page._cust_rows['permit_register']['status'].text(),
        'status: ' + page._cust_rows['permit_register']['status'].text())
page._pid, page._year, page._month = PID, Y, M
H.check(page._report_params().get('permit_issued_to') == 'Sungrow', 'passed to the generator when set')
rw.save_project_config(PID, permit_issued_to='')
H.check('permit_issued_to' not in page._report_params(), 'and left out when empty')

print('\n=== the real exports on this PC ===')
for p, key in ((os.path.join(H.SCADA_DIR, 'October', 'Raw data', 'Customer Daily Generation Report.xlsx'),
                'customer_dgr'),
               (os.path.join(H.SCADA_DIR, 'September', '(CPTW) Register Transition Phase (APRS BESS).xlsx'),
                'permit_register')):
    if os.path.isfile(p):
        got = mds.classify(p, 'tashkent', 2026, 10)
        H.check(got['type'] == key, 'real {} -> {} {}'.format(key, got['status'], got['type']))
    else:
        print('   (not on this PC: {})'.format(os.path.basename(p)))
aug = os.path.join(H.SCADA_DIR, 'August', 'SCADA Raw data')
if os.path.isdir(aug):
    expect = {'Alarms report.XLSX': 'alarms', 'Cycles first day of the month.xlsx': 'cycles_first_day',
              'Cycles last day of the month.xlsx': 'cycles_last_day',
              'HV meter daily export and import.xlsx': 'hv_meter', 'LC daily charge.xlsx': 'lc_charge',
              'LC daily discharge.xlsx': 'lc_discharge', 'LC total charge.xlsx': 'lc_total_charge',
              'LC total discharge.xlsx': 'lc_total_discharge', 'LC working status.xlsx': 'working_status',
              'PCS fault status.xlsx': 'pcs_fault', 'PCS_charge_discharge_status.xlsx': 'pcs_cd',
              'SOC august.xlsx': 'soc', 'Soh of last of the month.xlsx': 'soh_snapshot'}
    t0 = time.time()
    got = {os.path.basename(p): mds.classify(p, 'tashkent', 2026, 8) for p in mds.folder_files(aug)}
    dt = time.time() - t0
    wrong = {n: (g['status'], g['type']) for n, g in got.items() if expect.get(n) != g['type']}
    H.check(not wrong and len(got) == 13, 'August 2026: 13 of 13 recognised in {:.2f} s{}'.format(
        dt, '' if not wrong else ' — wrong: {}'.format(wrong)))
else:
    print('   (August SCADA folder not on this PC — skipped)')
buk = os.environ.get('BESS_BUKHARA_DIR', r'C:\Users\user1\Desktop\Bukhara BESS Masdar'
                                         r'\Bukhara_BESS_SCADA data_July_2026')
if os.path.isdir(buk):
    got = {os.path.basename(p): mds.classify(p, 'bukhara', 2026, 7) for p in mds.folder_files(buk)}
    for n, g in sorted(got.items()):
        print('   {:<44} {:<12} {}'.format(n, g['status'], g['type'] or g['note']))
    used = {g['type'] for g in got.values() if g['status'] == 'recognised'}
    H.check(used == {'bk_site_kpi', 'bk_overall_lc', 'bk_battery_unit', 'bk_meter_daily', 'bk_alarms'},
            'Bukhara July: the 5 files the report reads are recognised: {}'.format(sorted(used)))
    H.check(got.get('System_availiability_July_2026.xlsx', {}).get('status') == 'ignored',
            'the 80 MB availability export is recognised as not needed (not copied)')
    t = mds.classify(os.path.join(buk, 'Main KPI_5min_data_July_2026.xlsx'), 'tashkent', 2026, 7)
    H.check(t['status'] == 'unrecognised', 'a Bukhara export in a Tashkent month is not taken')
else:
    print('   (Bukhara folder not on this PC — skipped)')

H.finish()
