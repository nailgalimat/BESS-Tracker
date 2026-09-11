"""Planner: PM campaign generation, completing a job writes pm_activities,
and the customer export carries Block / Date / Duration only.
"""
import _harness as H            # must be first: isolates DB, sync, network, history
import sys, io, os, shutil, sqlite3, warnings, datetime
warnings.filterwarnings('ignore')
import pandas as pd
import database.db_manager as dbm
from services.sync_config import sync_config
MVP, SCRATCH, copy = H.MVP, H.WORK, H.DB   # names the ported body uses

from PyQt5.QtWidgets import QApplication, QMessageBox, QDialog, QFileDialog
from PyQt5.QtCore import Qt
app = QApplication([])
import ui.main_window as mwmod
import services.planner_service as pl
from ui.planner_page import _CampaignDialog, _CompleteDialog

win = mwmod.MainWindow()
print('stack pages:', win.stack.count(), '| planner at',
      win.stack.indexOf(win.planner_page), '(expected', mwmod.PAGE_PLANNER, ')')
assert win.stack.indexOf(win.planner_page) == mwmod.PAGE_PLANNER

conn = dbm.get_connection()
prow = conn.execute('SELECT id,name FROM projects ORDER BY id LIMIT 1').fetchone()
conn.close()
win._open_project(prow['id'], prow['name'])
win._navigate(mwmod.PAGE_PLANNER)
pg = win.planner_page
PID = pg._project_id
print('planner scoped to project', PID, '| blocks', pg._n_blocks())
print('types in filter:', [pg.type_filter.itemText(i) for i in range(pg.type_filter.count())])

print('\n=== PM due tab ===')
pg.tabs.setCurrentIndex(1)
print('   rows:', pg.due_tbl.rowCount())
for r in range(4):
    print('      ', ' | '.join(pg.due_tbl.item(r, c).text() for c in range(4)))

print('\n=== campaign dialog preview ===')
due = pl.pm_due(PID, list(range(1, pg._n_blocks() + 1)))
dlg = _CampaignDialog(PID, pg._n_blocks(), due, pl.get_types(PID))
print('   default mode:', dlg.kind.currentText())
print('   preview:', dlg.preview.text())
dlg.per_day.setValue(3)
print('   at 3/day :', dlg.preview.text())
v = dlg.values()
print('   values: {} blocks, start {}, {}/day, {} h'.format(
    len(v['blocks']), v['start_date'], v['blocks_per_day'], v['hours_per_block']))

print('\n=== generate through the page ===')
import ui.planner_page as pp
pp._CampaignDialog.exec_ = lambda self: QDialog.Accepted
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
pg._new_campaign()
import datetime
today = datetime.date.today()
pg.year.setValue(today.year); pg.month.setCurrentIndex(today.month - 1)
pg._reload()
print('   schedule rows this month:', pg.tbl.rowCount())
print('   summary:', pg.summary.text())
for r in range(min(4, pg.tbl.rowCount())):
    print('      ', ' | '.join(pg.tbl.item(r, c).text() for c in range(9)))
assert pg.tbl.rowCount() > 0

print('\n=== complete the first job through the page ===')
pg.tbl.selectRow(0)
it = pg.tbl.item(0, 0).data(Qt.UserRole)
pp._CompleteDialog.exec_ = lambda self: QDialog.Accepted
pg._complete_selected()
pg._reload()
done = [pg.tbl.item(r, 8).text() for r in range(pg.tbl.rowCount())].count('done')
print('   done rows now:', done)
print('   summary:', pg.summary.text())
assert done >= 1

conn = dbm.get_connection()
n = conn.execute('SELECT COUNT(*) FROM pm_activities').fetchone()[0]
row = conn.execute('SELECT affected_blocks, date_from, hours FROM pm_activities '
                   'ORDER BY id DESC LIMIT 1').fetchone()
conn.close()
print('   pm_activities now:', n, '| newest:', dict(row))

print('\n=== campaigns tab ===')
pg.tabs.setCurrentIndex(2)
print('   plans:', pg.plan_tbl.rowCount())
for r in range(pg.plan_tbl.rowCount()):
    print('      ', ' | '.join(pg.plan_tbl.item(r, c).text() for c in range(7)))

print('\n=== customer export through the page ===')
out = os.path.join(SCRATCH, 'planner_customer.xlsx')
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (out, ''))
pg._export_customer()
import pandas as pd
back = pd.read_excel(out)
print('   columns:', list(back.columns), '| rows:', len(back))
assert list(back.columns) == ['Block', 'Date', 'Duration (h)']

print('\nPLANNER UI SMOKE OK')

H.finish()
