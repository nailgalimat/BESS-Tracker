"""Planner: a single job can be added, the empty schedule explains itself,
builtin work types exist exactly once, an admin job costs no availability.
"""
import _harness as H            # must be first: isolates DB, sync, network, history
import sys, io, os, shutil, sqlite3, warnings, datetime
warnings.filterwarnings('ignore')
import pandas as pd
import database.db_manager as dbm
from services.sync_config import sync_config
MVP, SCRATCH, copy = H.MVP, H.WORK, H.DB   # names the ported body uses

# The empty-state assertions below need an empty planner. The live database is
# no longer empty — the user has started planning real work in it — so clear
# the planner tables in this copy rather than assume anything about the source.
import sqlite3 as _sq0
_c0 = _sq0.connect(copy)
_c0.execute('DELETE FROM plan_items')
_c0.execute('DELETE FROM work_plans')
_c0.execute('DELETE FROM plan_item_types WHERE project_id IS NOT NULL')
_c0.commit(); _c0.close()

from PyQt5.QtWidgets import QApplication, QMessageBox, QDialog

# the builtin work types must exist exactly once, whatever a previous version left
import sqlite3 as _sq
_c=_sq.connect(copy)
_dup=_c.execute("SELECT code, COUNT(*) FROM plan_item_types WHERE project_id IS NULL "
                "GROUP BY code HAVING COUNT(*) > 1").fetchall()
_n=_c.execute("SELECT COUNT(*) FROM plan_item_types WHERE project_id IS NULL").fetchone()[0]
_c.close()
print('builtin types after init:', _n, '| duplicated:', _dup)
assert not _dup, _dup
from PyQt5.QtCore import Qt
app = QApplication([])
import ui.main_window as mwmod
import ui.planner_page as pp
import services.planner_service as pl

win = mwmod.MainWindow()
conn = dbm.get_connection()
p = conn.execute('SELECT id,name FROM projects ORDER BY id LIMIT 1').fetchone(); conn.close()
PID = p['id']
win._open_project(PID, p['name'])
win._navigate(mwmod.PAGE_PLANNER)
pg = win.planner_page

print('=== header buttons ===')
from PyQt5.QtWidgets import QPushButton
btns = [b.text() for b in pg.findChildren(QPushButton)][:6]
print('  ', btns)
assert any('Add job' in b for b in btns), 'no way to add a single job'

print('\n=== empty state explains itself ===')
pg._reload()
print('   rows:', pg.tbl.rowCount())
print('   hint shown:', not pg.empty_hint.isHidden())
print('   hint:', pg.empty_hint.text().replace(chr(10), ' | ')[:150])
assert not pg.empty_hint.isHidden()

print('\n=== the same, but with a filter on ===')
i = pg.type_filter.findData('admin')
pg.type_filter.setCurrentIndex(i)
pg.status_filter.setCurrentIndex(pg.status_filter.findData('done'))
print('   hint:', pg.empty_hint.text().replace(chr(10), ' | ')[:170])
assert 'matches' in pg.empty_hint.text()

print('\n=== add an administrative job ===')
QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
today = datetime.date.today()
def fake_exec(self):
    i = self.type_combo.findData('admin')
    self.type_combo.setCurrentIndex(i)
    self.title.setText('Monthly report to the customer')
    self.hours.setValue(3.0)
    self.assignee.setText('Nail')
    self.priority.setCurrentIndex(0)
    self.notes.setPlainText('LTSA September pack')
    return QDialog.Accepted
pp._AddJobDialog.exec_ = fake_exec
pg._add_job()

print('   rows now:', pg.tbl.rowCount(), '| hint hidden:', pg.empty_hint.isHidden())
for r in range(pg.tbl.rowCount()):
    print('      ', ' | '.join(pg.tbl.item(r, c).text() for c in range(9)))
assert pg.tbl.rowCount() == 1, 'the new job must be visible straight away'
print('   status filter reset to:', pg.status_filter.currentText())
print('   summary:', pg.summary.text())

print('\n=== an admin job must NOT touch availability ===')
conn = dbm.get_connection()
pm_before = conn.execute('SELECT COUNT(*) FROM pm_activities').fetchone()[0]
conn.close()
it = pg.tbl.item(0, 0).data(Qt.UserRole)
print('   type:', it['type_code'], '| counts_as_downtime:', it['counts_as_downtime'])
out = pl.complete_item(it['id'], actual_date=today.isoformat(), actual_hours=2.5)
print('   completed:', out)
conn = dbm.get_connection()
pm_after = conn.execute('SELECT COUNT(*) FROM pm_activities').fetchone()[0]
conn.close()
print('   pm_activities {} -> {}'.format(pm_before, pm_after))
assert not out['wrote_downtime'] and pm_after == pm_before

print('\n=== a custom type still works the same way ===')
pl.add_type(PID, 'gsp_creating', 'GSP creating', counts_as_downtime=False)
pg._reload_types()
labels = [pg.type_filter.itemText(i) for i in range(pg.type_filter.count())]
print('   types:', labels)
assert 'GSP creating' in labels

def fake_exec2(self):
    self.type_combo.setCurrentIndex(self.type_combo.findData('gsp_creating'))
    self.title.setText('Prepare GSP submission')
    return QDialog.Accepted
pp._AddJobDialog.exec_ = fake_exec2
pg.type_filter.setCurrentIndex(0)
pg._add_job()
rows = [pg.tbl.item(r, 3).text() for r in range(pg.tbl.rowCount())]
print('   jobs now:', rows)
assert any('GSP' in r for r in rows)

print('\nADD JOB SMOKE OK')

H.finish()
