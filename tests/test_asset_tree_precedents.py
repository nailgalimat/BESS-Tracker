"""Asset tree, alarm import, and the "seen before" precedent panel in the
Work Report form.
"""
import _harness as H            # must be first: isolates DB, sync, network, history
import sys, io, os, shutil, sqlite3, warnings, datetime
warnings.filterwarnings('ignore')
import pandas as pd
import database.db_manager as dbm
from services.sync_config import sync_config
MVP, SCRATCH, copy = H.MVP, H.WORK, H.DB   # names the ported body uses

from PyQt5.QtWidgets import QApplication, QMessageBox
from PyQt5.QtCore import Qt
app = QApplication([])
import ui.main_window as mwmod
import services.asset_tree_service as ats
from services.work_log_service import get_work_log_alarms

win = mwmod.MainWindow()
c = dbm.get_connection()
tabs = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
print('work_log_alarms table:', 'work_log_alarms' in tabs)
pid = c.execute('SELECT id,name FROM projects ORDER BY id LIMIT 1').fetchone(); c.close()
win._open_project(pid['id'], pid['name'])

# This test imports August and reasons about August. The Equipment page picks
# whichever month has the freshest data, so any later month in the snapshot
# would silently take over and the fixture would stop matching what the page
# shows. Drop them from this disposable copy — the test owns its own month.
_c = dbm.get_connection()
_c.execute("DELETE FROM alarm_events WHERE (year*100+month) > 202608")
_c.commit(); _c.close()

from ui.equipment_page import _ImportWorker
A = r'C:\Users\user1\Desktop\ACWA BESS Tashkent\LTSA monthly report\2026\August\SCADA Raw data\Alarms report.XLSX'
res, err = {}, []
w = _ImportWorker(pid['id'], A, 2026, 8); w.done.connect(res.update); w.failed.connect(err.append)
w.run(); assert not err, err
eq = win.equipment_page; eq._rebuild(quiet=True)
print('imported', res['inserted'], 'events')

# ── grouping ──────────────────────────────────────────────────────────────
# The page shows the newest month that has data (get_data_freshness), not a
# fixed one, so ask for the same month or the two part company as soon as
# another month is imported.
_f = ats.get_data_freshness(pid['id']) or {'year': 2026, 'month': 8}
alarms = ats.get_unreported_faults(pid['id'], int(_f['year']), int(_f['month']),
                                   min_hours=1.0)
inc = ats.group_faults_into_incidents(alarms)
print('\n{} alarms -> {} incidents'.format(len(alarms), len(inc)))
for g in inc[:6]:
    print('   {:>3} units  worst {:>6} h  total {:>8} h  {}  blocks {}'.format(
        g['units'], g['hours'], g['total_hours'],
        str(g['trigger_name'])[:38], str(g['blocks'])[:34]))
assert sum(g['units'] for g in inc) == len(alarms), 'every alarm must land in exactly one incident'
assert len(inc) < len(alarms)
multi = [g for g in inc if g['units'] > 1]
print('multi-unit incidents:', len(multi), '| biggest:', max(g['units'] for g in inc))

# ── incidents that really belong to a grid-outage window ──────────────────
inc = ats.flag_incidents_near_exclusions(pid['id'], inc)
near = [g for g in inc if g.get('near_exclusion')]
print('\nsitting just outside an exclusion window:', len(near))
for g in near[:6]:
    print('   {:>3} units {:>8} h  {:16}  {}'.format(
        g['units'], g['total_hours'], str(g['activated'])[:16], g['near_exclusion']))
print('alarms they account for:', sum(g['units'] for g in near),
      'of', len(alarms))

# ── the tab uses them ─────────────────────────────────────────────────────
idx = next(i for i in range(eq.tabs.count()) if eq.tabs.tabText(i).startswith('Needs'))
eq.tabs.setCurrentIndex(idx)
print('\ntab   :', eq.tabs.tabText(idx))
print('summary:', eq.gap_summary.text())
print('rows  :', eq.gap_tbl.rowCount())
for r in range(3):
    print('   ', ' | '.join(eq.gap_tbl.item(r, cc).text()[:30]
                            for cc in range(eq.gap_tbl.columnCount())))
assert eq.gap_tbl.rowCount() == len(inc)
eq.gap_group.setChecked(False)
print('ungrouped rows:', eq.gap_tbl.rowCount())
assert eq.gap_tbl.rowCount() == len(alarms)
eq.gap_group.setChecked(True)

# ── raise one report for a whole incident ─────────────────────────────────
row = next(r for r in range(eq.gap_tbl.rowCount())
           if int(eq.gap_tbl.item(r, 1).text()) > 1)
eq.gap_tbl.selectRow(row)
g = eq.gap_tbl.item(row, 0).data(Qt.UserRole)
print('\npicked incident: {} units, {}'.format(g['units'], g['trigger_name'][:44]))
eq._raise_report(eq.gap_tbl)

# The alarm is written up in the Work journal now — not in the archived Work
# Reports form, whose rows cannot be edited there and never reach the phones.
import services.work_journal_service as wj
wp = win.work_page
assert win.stack.currentWidget() is wp
print('title  :', wp._fields['title'].text()[:110])
print('note   :', wp._fields['internal_note'].toPlainText()[:150])
print('alarms carried:', len(wp._alarm_ids))
assert len(wp._alarm_ids) == g['units']
assert wp._fields['title'].text() == g['trigger_name']

QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
wp._fields['work_done'].setPlainText(
    'Restarted the BSC and re-established PCS comms on all affected units.')
saved = []
wp.alarm_record_saved.connect(saved.append)
c = dbm.get_connection()
old_before = c.execute('SELECT COUNT(*) FROM work_logs').fetchone()[0]
c.close()
wp._save_card()
assert len(saved) == 1 and saved[0].startswith('e:')

c = dbm.get_connection()
old_after = c.execute('SELECT COUNT(*) FROM work_logs').fetchone()[0]
c.close()
assert old_after == old_before, 'an old-format row was written'
linked = wj.alarms_of(saved[0])
print('\nrecord {} linked alarms: {}'.format(saved[0], len(linked)))
assert sorted(linked) == sorted(g['ids'])

# every alarm of the incident must leave the list, not just the primary
alarms2 = ats.get_unreported_faults(pid['id'], 2026, 8, min_hours=1.0)
gone = set(g['ids']) & {a['id'] for a in alarms2}
print('alarms: {} -> {}; still-unreported members of the incident: {}'.format(
    len(alarms), len(alarms2), len(gone)))
assert not gone
assert len(alarms2) == len(alarms) - g['units']

# the page stays on Work, on the queue of alarms still to write up
assert win.stack.currentWidget() is wp and wp._tab == 'scada'

# ── "seen before?" finds that record on the next occurrence ───────────────
row = wj.get(saved[0], pid['id'])
again = wj.seen_before(pid['id'], {'key': None, 'title': g['trigger_name'],
                                   'block': row.get('block')})
print('\nseen before for the same fault:', len(again))
assert again and again[0]['key'] == saved[0]
assert 'Restarted the BSC' in again[0]['work_done']

# ── tree reveal ───────────────────────────────────────────────────────────
eq.search.setText('49.01.02'); eq._jump_to_search()
cur = eq.tree.currentItem()
print('\ntree reveal -> selected {!r} (code {})'.format(
    cur.text(0) if cur else None, cur.data(0, Qt.UserRole) if cur else None))
assert cur is not None and cur.data(0, Qt.UserRole) == '49.01.02'

print('\nSLICE 3 SMOKE OK')

H.finish()
