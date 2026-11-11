"""Section 2 "How the plant was run" (fresh database).

The Customer text tab's note is saved with the month (refused when the month is
locked), passed to the generator only when it has text — so a month without a
note hashes as before — and printed in section 2 after the data paragraphs,
one paragraph per line, escaped. Without a note section 2 is unchanged.
"""
import _harness as H            # must be first
import pandas as pd

import database.db_manager as dbm
import services.report_workflow_service as rw
import services.tashkent_report_service as T

H.fresh_db('opnote.db')
c = dbm.get_connection()
c.execute("INSERT INTO projects (name, num_zones, num_blocks, num_containers) VALUES ('TK', 1, 4, 4)")
PID = c.execute("SELECT id FROM projects").fetchone()[0]
c.commit(); c.close()
rw.save_project_config(PID, site_type='tashkent')
Y, M = 2026, 10

print('=== the column exists after the migration (and re-running it is safe) ===')
dbm.initialize_database()
c = dbm.get_connection()
cols = [r[1] for r in c.execute("PRAGMA table_info(report_months)")]
c.close()
H.check(cols.count('operation_note') == 1, 'report_months.operation_note added once')

print('\n=== section 2 without a note is the data paragraphs, unchanged ===')
days = pd.date_range('2026-10-01', '2026-10-31')
rows = []
for d in days:
    for b in (1, 2):
        run = d.day <= 14
        rows.append({'date': d, 'block': b, 'charge_kwh': 1000.0 if d.day <= 31 else 0,
                     'discharge_kwh': 900.0 if run else 0.0,
                     'start_soc_pct': 10.0 if d.day == 1 else 50.0,
                     'end_soc_pct': 90.0 if d.day == 31 else 50.0})
g = {'daily_kpi': pd.DataFrame(rows), 'contractual_avail': {'installed_kwh': 770000.0}}
base = T.build_operation_summary(g)
H.check(base[0] == ('p', 'The plant was discharged on <b>14</b> of 31 days and charged on <b>31</b>.'
                         ' The last discharge of the month was on 14 October. For the 17 days after it'
                         ' the plant was charged or on standby and was not discharged.'),
        'first paragraph as before: ' + base[0][1][:60])
H.check(len(base) == 2 and 'State of charge was about <b>10%</b>' in base[1][1]
        and '616 MWh' in base[1][1], 'SOC paragraph as before')
H.check(T.build_operation_summary(dict(g, operation_note=None)) == base
        and T.build_operation_summary(dict(g, operation_note=[])) == base
        and T.build_operation_summary(dict(g, operation_note='  \n ')) == base,
        'None / [] / blank note: identical block list')

print('\n=== with a note: escaped paragraphs after the data ===')
note = ['From 15 to 31 October the plant was not dispatched, by instruction of the NDC.',
        'Blocks 1 & 2 <standby>.']
out = T.build_operation_summary(dict(g, operation_note=note))
H.check(out[:2] == base and out[2:] == [('verbatim', note[0]), ('verbatim', 'Blocks 1 &amp; 2 &lt;standby&gt;.')],
        'two note paragraphs after the data, & and < escaped')
out_s = T.build_operation_summary(dict(g, operation_note='line one\n\nline two'))
H.check(out_s[2:] == [('verbatim', 'line one'), ('verbatim', 'line two')], 'a string note splits on lines')
empty = T.build_operation_summary({'daily_kpi': None, 'operation_note': ['Not dispatched.']})
H.check(empty == [('p', 'No daily operating data is available for this period.'),
                  ('verbatim', 'Not dispatched.')], 'the note still prints when there is no daily data')
import inspect
H.check('operation_note' in inspect.signature(T.generate_tashkent_report).parameters,
        'the generator takes operation_note')

print('\n=== the page: save, load, params, lock ===')
from PyQt5.QtWidgets import QApplication, QMessageBox
app = QApplication.instance() or QApplication([])
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
import ui.monthly_reports_page as mrp
page = mrp.MonthlyReportsPage()
page._pid, page._year, page._month = PID, Y, M
rw.ensure_report_month(PID, Y, M)
H.check('operation_note' not in page._report_params(), 'empty note: no operation_note key in the params')
page.operation_note.setPlainText('From 15 to 31 October the plant was not dispatched.\n\nSecond line.')
H.check(page._save_narrative(), 'saved')
H.check(rw.get_report_month(PID, Y, M)['operation_note'].startswith('From 15 to 31 October'),
        'stored in report_months.operation_note')
page.operation_note.setPlainText('')
page._load_all()
if page._an_worker:
    page._an_worker.wait(30000)
H.check(page.operation_note.toPlainText().startswith('From 15 to 31'), 'loaded back into the field')
H.check(page._report_params().get('operation_note') ==
        ['From 15 to 31 October the plant was not dispatched.', 'Second line.'],
        'params carry the lines')

c = dbm.get_connection()
c.execute("UPDATE report_months SET locked_at=datetime('now') WHERE project_id=? AND year=? AND month=?",
          (PID, Y, M))
c.commit(); c.close()
try:
    rw.save_report_month(PID, Y, M, operation_note='changed')
    H.check(False, 'locked month refused')
except rw.MonthLockedError:
    H.check(True, 'saving on a locked month -> MonthLockedError')
page._apply_lock()
H.check(page.operation_note.isReadOnly(), 'the field is read-only while locked')
H.check(rw.get_report_month(PID, Y, M)['operation_note'].startswith('From 15'), 'note unchanged')

H.finish()
