"""Storing the month's alarms as equipment history — its own step on 1 · Data
(fresh database, synthetic exports).

The month's alarms only (an export's previous-month backlog is never stamped
as this month), tagged like the report tags them (exclusion windows and the
PM stop windows), idempotent, re-tagging the stored rows on "Store again",
and allowed on a locked month (equipment history is not a report input).
"""
import _harness as H            # must be first
import os
import warnings
warnings.filterwarnings('ignore')

import pandas as pd

import database.db_manager as dbm
H.fresh_db('alarm_step.db')
import _synthetic_scada as S
import services.report_workflow_service as rw
import services.month_dataset_service as mds
import services.asset_tree_service as ats
import services.tashkent_report_service as T

c = dbm.get_connection()
c.execute("INSERT INTO projects (name, num_zones, num_blocks, num_containers) VALUES ('TK', 1, 70, 1)")
PID = c.execute("SELECT id FROM projects").fetchone()[0]
c.commit(); c.close()
rw.save_project_config(PID, site_type='tashkent', per_block_capacity_mw=5.5)
Y, M = 2026, 10
PM_A, PM_Z = pd.Timestamp('2026-10-02 09:00'), pd.Timestamp('2026-10-02 12:00')


def status(t, col):
    return 'STOPPED' if ' 01.' in col and PM_A <= t < PM_Z else 'RUNNING'


files = S.tashkent_month(os.path.join(H.WORK, 'exports'), Y, M, days=(1, 2, 3), status=status)
TRIG = 'BSC - System Fault Status: BSC-PCS comm fault'
S.alarms(files['alarms'], [
    ('2026-09-28 10:00', '2026-09-28 11:00', TRIG, 'BSC 01.01.02', 'Production'),     # September backlog
    ('2026-10-02 10:00', '2026-10-02 10:30', TRIG, 'BSC 01.01.01', 'Production'),     # inside the PM stop
    ('2026-10-03 03:00', '2026-10-03 04:00', TRIG, 'BSC 02.01.02', 'Production'),     # a real fault
    ('2026-10-03 21:55', '2026-10-03 22:55', 'LC - SYSTEM ALARM STATE1', 'LC200 02.01', 'Warning')])
mds.add_files(PID, Y, M, list(files.values()))
rw.record_pm(PID, '1', '2026-10-02', None, 3, 'PM as per checklist')

print('=== nothing stored yet ===')
st = ats.alarm_history_status(PID, Y, M)
H.check(st['stored'] == 0 and st['last_activated_overall'] is None, 'status: none stored')

print('\n=== store ===')
res = ats.store_month_alarms(PID, Y, M)
print('   ', res)
c = dbm.get_connection()
rows = [dict(r) for r in c.execute("SELECT * FROM alarm_events WHERE project_id=? ORDER BY activated", (PID,))]
c.close()
H.check(res['inserted'] == len(rows) and rows and all((r['year'], r['month']) == (Y, M) for r in rows),
        '{} alarms stored, every one stamped 2026-10'.format(len(rows)))
H.check(not any(r['activated'].startswith('2026-09') for r in rows) and res['outside_month'] >= 1,
        'the September alarm in the export is left out ({} outside the month)'.format(res['outside_month']))
pm_row = next(r for r in rows if r['activated'].startswith('2026-10-02 10:00'))
H.check(pm_row['is_excluded'] == 1 and pm_row['excluded_by'] == 'Preventive maintenance',
        'the alarm inside the PM stop is tagged "Preventive maintenance"')
real = next(r for r in rows if r['activated'].startswith('2026-10-03 03:00'))
H.check(real['is_excluded'] == 0, 'a fault outside it is not excluded')
H.check(res['last_activated'] == '2026-10-03 21:55' and res['pm_tagging'], 'last activated 03.10 21:55')
st = ats.alarm_history_status(PID, Y, M)
H.check(st['stored'] == len(rows) and st['last_activated_overall'].startswith('2026-10-03 21:55'),
        'status: {} stored, history ends {}'.format(st['stored'], st['last_activated_overall']))

print('\n=== store again: nothing added twice, PM tag kept ===')
res2 = ats.store_month_alarms(PID, Y, M)
c = dbm.get_connection()
pm_after = dict(c.execute("SELECT * FROM alarm_events WHERE id=?", (pm_row['id'],)).fetchone())
n_after = c.execute("SELECT COUNT(*) FROM alarm_events WHERE project_id=?", (PID,)).fetchone()[0]
c.close()
H.check(res2['inserted'] == 0 and n_after == len(rows), 'second store inserts 0')
H.check(pm_after['excluded_by'] == 'Preventive maintenance', 'PM-covered alarm still tagged after store again')

print('\n=== a window added later is applied on store again ===')
import services.availability_service as av
av.add_exclusion('Grid Outage', '2026-10-03', '2026-10-03', '02:50', '05:00', affected_blocks='2',
                 project_id=PID, year=Y, month=M)
ats.store_month_alarms(PID, Y, M)
c = dbm.get_connection()
r2 = dict(c.execute("SELECT * FROM alarm_events WHERE id=?", (real['id'],)).fetchone())
c.close()
H.check(r2['is_excluded'] == 1 and r2['excluded_by'] == 'Grid Outage', 'the fault now inside a Grid Outage window')

print('\n=== a locked month: still stored ===')
rw.set_month_lock(PID, Y, M, True)
try:
    ats.store_month_alarms(PID, Y, M)
    H.check(True, 'store on a locked month works')
except Exception as e:                                  # noqa: BLE001
    H.check(False, 'store on a locked month: {}'.format(e))
rw.set_month_lock(PID, Y, M, False)

print('\n=== the report helper is the one the generator uses ===')
dropped = {}
inp = rw.report_inputs(PID, Y, M)
al, stops = T.prepare_month_alarms(files['alarms'], T.load_lc_working_status(files['working_status']),
                                   inp['exclusions'], inp['manual_unavailability'],
                                   pd.Period('2026-10', 'M'), dropped=dropped)
H.check(len(stops) == 1 and stops[0]['start'] == PM_A and sum(dropped.values()) >= 1,
        'prepare_month_alarms: one PM stop 09:00, the September row dropped')
import inspect
src = inspect.getsource(T.generate_tashkent_report)
H.check('prepare_month_alarms(' in src and 'load_alarms(' not in src,
        'generate_tashkent_report calls prepare_month_alarms (no second copy of the steps)')

print('\n=== the Data step strip ===')
c = dbm.get_connection()
c.execute("DELETE FROM alarm_events WHERE project_id=?", (PID,))
c.commit(); c.close()
from PyQt5.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
import ui.monthly_reports_page as mrp
page = mrp.MonthlyReportsPage()
page.proj_combo.setCurrentIndex(page.proj_combo.findData(PID))
page.year_spin.setValue(Y); page.month_combo.setCurrentIndex(M - 1)
page._open_month()
for w in (page._an_worker, page._al_worker):
    if w:
        w.wait(120000)
app.processEvents()
if page._al_worker:
    page._al_worker.wait(120000)
app.processEvents()
H.check(ats.alarm_history_status(PID, Y, M)['stored'] > 0,
        'opening the month stored the alarms by itself (nothing was stored)')
H.check(page.alarm_status.text().startswith('✓') and 'October alarms stored' in page.alarm_status.text()
        and page.store_alarms_btn.text().endswith('Store again'),
        'strip: "{}" / button "{}"'.format(page.alarm_status.text(), page.store_alarms_btn.text()))
H.check('alarms dated October' in page.alarm_hint.text() and 'other months are left out' in page.alarm_hint.text(),
        'hint: ' + page.alarm_hint.text()[:90])

H.finish()
