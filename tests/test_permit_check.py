"""Permit × PM record × SCADA stop check on 2 · Check (fresh database).

A made-up October like the real one: permits for blocks 6, 21, 22, 7, 45, 12;
PM records on 6 (02.10, no PTW), 7 (03.10, no PTW), 7 (05.10, no stop) and 12
(05.10, no PTW); each permit day has its block's stop in the working status.
Expected: 3 "Permit without PM record" (21, 22, 45), 1 "PM record without a
stop" (#7 05.10, a probable duplicate), 3 "without PTW No." (616, 624, 629).
The check writes nothing; the Fix buttons open the PM dialog pre-filled.
"""
import _harness as H            # must be first
import datetime as dt
import os
import warnings
warnings.filterwarnings('ignore')

import openpyxl
import pandas as pd

import database.db_manager as dbm
H.fresh_db('permit_check.db')
import _synthetic_scada as S
import services.report_workflow_service as rw
import services.month_dataset_service as mds
import services.report_precheck_service as rpc

c = dbm.get_connection()
c.execute("INSERT INTO projects (name, num_zones, num_blocks, num_containers) VALUES ('TK', 1, 70, 1)")
PID = c.execute("SELECT id FROM projects").fetchone()[0]
c.commit(); c.close()
rw.save_project_config(PID, site_type='tashkent', permit_issued_to='Sungrow')
Y, M = 2026, 10
BLOCKS = (6, 7, 12, 21, 22, 30, 45)
STOPS = {6: [('2026-10-02 09:00', '2026-10-02 13:00')],
         21: [('2026-10-02 09:35', '2026-10-02 15:20')],
         7: [('2026-10-03 08:00', '2026-10-03 12:00')],
         22: [('2026-10-03 10:00', '2026-10-03 14:00')],
         45: [('2026-10-04 08:00', '2026-10-04 11:00')],
         12: [('2026-10-05 08:00', '2026-10-05 12:30')],
         30: [('2026-10-04 08:00', '2026-10-04 10:00')]}
STOPS = {b: [(pd.Timestamp(a), pd.Timestamp(z)) for a, z in v] for b, v in STOPS.items()}


def status(t, col):
    b = int(col.split('LC200 ')[1][:2])
    return 'STOPPED' if any(a <= t < z for a, z in STOPS.get(b, [])) else 'RUNNING'


folder = os.path.join(H.WORK, 'exports')
os.makedirs(folder, exist_ok=True)
ws_path = S.wide(os.path.join(folder, 'LC working status.xlsx'),
                 S.stamps('2026-10-01 00:00', '2026-10-06 23:55'),
                 S.lc_columns('SYSTEM WORKING STATUS', blocks=BLOCKS), status)

reg_path = os.path.join(folder, '(CPTW) Register.xlsx')
wb = openpyxl.Workbook()
ws = wb.active
ws.title = 'CPTW Register'
ws.append(['Commissioning Permit To Work Register'])
ws.append(['Sr. No', 'CPTW No', 'Issued To', 'Area', 'Discription', 'Issue Date', 'Valid till', 'PTW Status'])
for i, r in enumerate([
        ('CPTW-APRS-616', 'SUNGROW', 'RMU-06', 'BESS Preventive Maintenance', '02/10/2026', '02/10/2026', 'Closed'),
        ('CPTW-APRS-619', 'SUNGROW', 'RMU-21', 'BESS Preventive Maintenance and KKS Installation',
         '02/10/2026', '02/10/2026', 'Closed'),
        ('CPTW-APRS-624', 'SUNGROW', 'RMU-07', 'BESS Preventive Maintenance', '03/10/2026', '03/10/2026', 'Closed'),
        ('CPTW-APRS-625', 'SUNGROW', 'BESS Zone3 RMU-22', 'BESS PM', '03/10/2026', '03/10/2026', 'Closed'),
        ('CPTW-APRS-627', 'SUNGROW', 'RMU-45', 'Preventive maintenance', '04/10/2026', '04/10/2026', 'Closed'),
        ('CPTW-APRS-628', 'SUNGROW', 'RMU-30', 'BESS Preventive Maintenance', '04/10/2026', '04/10/2026',
         'Cancelled'),
        ('CPTW-APRS-629', 'SUNGROW', 'RMU-12', 'BESS Preventive Maintenance', '05/10/2026', '05/10/2026', 'Closed'),
        ('CPTW-APRS-630', 'HEEC', 'RMU-33', 'BESS Preventive Maintenance', '05/10/2026', '05/10/2026', 'Closed'),
        ], start=1):
    ws.append([i] + list(r))
wb.save(reg_path)

for b, d in ((6, '2026-10-02'), (7, '2026-10-03'), (7, '2026-10-05'), (12, '2026-10-05')):
    rw.record_pm(PID, str(b), d, None, 4.5, 'PM as per checklist', source='phone')
pm = {(int(r['affected_blocks']), r['date_from']): r['id'] for r in rw.get_pm_activities(PID, Y, M)}


def digest():
    conn = dbm.get_connection()
    try:
        return repr([tuple(r) for t in ('pm_activities', 'availability_exclusions', 'manual_unavailability')
                     for r in conn.execute(f'SELECT * FROM {t} ORDER BY rowid')])
    finally:
        conn.close()


print('=== no register: none of these questions ===')
mds.add_files(PID, Y, M, [ws_path])
H.check(rpc.permit_questions(PID, Y, M) == [], 'no permit register -> no permit questions')

print('\n=== October ===')
res = mds.add_files(PID, Y, M, [reg_path])
H.check(res[0]['type'] == 'permit_register', 'register added')
before = digest()
qs = rpc.permit_questions(PID, Y, M)
by = {}
for q in qs:
    by.setdefault(q['kind'], []).append(q)
for q in qs:
    print('   {:<13} {:<5} {} | {}'.format(q['kind'], q['severity'], q['detail'], q['explain']))
no_pm = by.get('permit_no_pm', [])
H.check(sorted(q['prefill']['affected_blocks'] for q in no_pm) == ['21', '22', '45'],
        'exactly 3 "Permit without PM record": blocks 21, 22, 45')
q21 = next(q for q in no_pm if q['prefill']['affected_blocks'] == '21')
H.check(q21['severity'] == 'warn' and '09:35–15:20 (5.75 h)' in q21['explain']
        and 'counts as fault' in q21['explain'], 'block 21: the SCADA stop is named: ' + q21['explain'])
H.check(q21['prefill'] == {'date_from': '2026-10-02', 'date_to': '2026-10-02', 'affected_blocks': '21',
                           'hours': 5.75, 'description': 'BESS Preventive Maintenance and KKS Installation',
                           'ptw_no': 'CPTW-APRS-619'}, 'pre-fill: date, block, 5.75 h, title, permit no.')
H.check(any('CPTW-APRS-625' in q['detail'] for q in no_pm), '"BESS Zone3 RMU-22" parsed as block 22')
H.check(not any('CPTW-APRS-628' in q['detail'] for q in qs), 'the cancelled permit (block 30) is ignored')
H.check(not any('CPTW-APRS-630' in q['detail'] for q in qs), 'a permit issued to HEEC is filtered out')
nostop = by.get('pm_no_stop', [])
H.check(len(nostop) == 1 and nostop[0]['ref'] == pm[(7, '2026-10-05')],
        'exactly 1 "PM record without a stop": #{} block 7 05.10'.format(pm[(7, '2026-10-05')]))
H.check(nostop and 'no permit names RMU-07' in nostop[0]['explain']
        and f"PM #{pm[(7, '2026-10-03')]} on 03.10 (CPTW-APRS-624)" in nostop[0]['explain']
        and 'duplicate' in nostop[0]['explain'] and '4.5 h still count' in nostop[0]['explain'],
        'it names the probable duplicate: ' + (nostop[0]['explain'] if nostop else ''))
ptw = by.get('pm_no_ptw', [])
H.check(sorted(q['prefill']['ptw_no'] for q in ptw) == ['CPTW-APRS-616', 'CPTW-APRS-624', 'CPTW-APRS-629'],
        '3 "without PTW No." offering 616 / 624 / 629: {}'.format([q['prefill'] for q in ptw]))
H.check(next(q for q in ptw if q['prefill']['ptw_no'] == 'CPTW-APRS-616')['ref'] == pm[(6, '2026-10-02')],
        '"RMU-06" parsed as block 6 and matched to its record')
H.check(not by.get('permit_skip'), 'working status present: not skipped')
H.check(digest() == before, 'the check wrote nothing')
allq = rpc.run_precheck(PID, Y, M)
H.check(sum(q['kind'].startswith('p') and q['kind'] != 'pending' and q['kind'] != 'pm' for q in allq) == len(qs),
        'run_precheck carries the same permit questions')

print('\n=== RMU parsing ===')
import services.tashkent_report_service as T
H.check(T.permit_blocks('RMU-21/22') == [21, 22] and T.permit_blocks('RMU 3, 4 & 5') == [3, 4, 5]
        and T.permit_blocks('BESS Zone3 RMU-022') == [22] and T.permit_blocks('BESS') == [],
        'RMU-21/22, RMU 3, 4 & 5, Zone3 RMU-022')
oor = []
H.check(T.permit_blocks('RMU-75', n_blocks=70, out_of_range=oor) == [] and oor == [75],
        'a number beyond the plant is dropped and reported')

print('\n=== no working status: stops not compared ===')
f = mds.active_by_type(PID, Y, M)['working_status']
mds.remove_file(f['id'])
qs2 = rpc.permit_questions(PID, Y, M)
k2 = [q['kind'] for q in qs2]
H.check('permit_skip' in k2 and 'pm_no_stop' not in k2, 'information "Permit check skipped"; no stop questions')
H.check(sum(k == 'permit_no_pm' for k in k2) == 3 and all(
    q['severity'] == 'warn' and not q['explain'] for q in qs2 if q['kind'] == 'permit_no_pm'),
    'permits without a record still asked, without stop text')
mds.add_files(PID, Y, M, [ws_path])

print('\n=== the Fix buttons open the PM dialog pre-filled ===')
from PyQt5.QtWidgets import QApplication, QDialog, QMessageBox
app = QApplication.instance() or QApplication([])
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
import ui.monthly_reports_page as mrp
seen = []


def _exec(self):
    seen.append({'title': self.windowTitle(), 'ptw': self.ptw.text(), 'blocks': self.blocks.text(),
                 'hours': self.hours.value(), 'date': self.date_from.date().toString('yyyy-MM-dd')})
    self.accept()
    return self.result()


mrp.PMDialog.exec_ = _exec
page = mrp.MonthlyReportsPage()
page.proj_combo.setCurrentIndex(page.proj_combo.findData(PID))
page.year_spin.setValue(Y); page.month_combo.setCurrentIndex(M - 1)
page._open_month()
if page._an_worker:
    page._an_worker.wait(60000)
q616 = next(q for q in rpc.permit_questions(PID, Y, M) if q['kind'] == 'pm_no_ptw'
            and q['prefill']['ptw_no'] == 'CPTW-APRS-616')
page._goto(q616)
H.check(seen and seen[-1]['ptw'] == 'CPTW-APRS-616' and seen[-1]['blocks'] == '6',
        'PTW Fix: dialog for #{} with the PTW field filled: {}'.format(q616['ref'], seen[-1] if seen else ''))
H.check(rw.get_pm_record(q616['ref'])['ptw_no'] == 'CPTW-APRS-616', 'record updated with the permit number')
q21 = next(q for q in rpc.permit_questions(PID, Y, M) if q['kind'] == 'permit_no_pm'
           and q['prefill']['affected_blocks'] == '21')
page._goto(q21)
new = [r for r in rw.get_pm_activities(PID, Y, M) if r['affected_blocks'] == '21']
H.check(seen[-1]['title'] == 'Add PM record from permit' and abs(seen[-1]['hours'] - 5.75) < 1e-9
        and seen[-1]['date'] == '2026-10-02', 'add Fix: "Add PM record from permit", 02.10, 5.75 h')
H.check(new and new[0]['ptw_no'] == 'CPTW-APRS-619' and new[0]['hours'] == 5.75,
        'PM record for block 21 written through record_pm with its permit')
left = rpc.permit_questions(PID, Y, M)
H.check(not any(q['kind'] == 'permit_no_pm' and q['prefill']['affected_blocks'] == '21' for q in left)
        and not any(q['kind'] == 'pm_no_ptw' and q['prefill']['ptw_no'] == 'CPTW-APRS-616' for q in left),
        'the check run again: both questions gone')
H.check(page.check_table.rowCount() > 0, 'and the Check tab was refilled ({} rows)'.format(
    page.check_table.rowCount()))

rw.set_month_lock(PID, Y, M, True)
n = len(seen)
page._goto(next(q for q in left if q['kind'] == 'pm_no_ptw'))
H.check(len(seen) == n, 'a locked month: the dialog does not open')
rw.set_month_lock(PID, Y, M, False)

H.finish()
