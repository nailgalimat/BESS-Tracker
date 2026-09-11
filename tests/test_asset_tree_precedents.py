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

from ui.equipment_page import _ImportWorker
A = r'C:\Users\user1\Desktop\ACWA BESS Tashkent\LTSA monthly report\2026\August\SCADA Raw data\Alarms report.XLSX'
res, err = {}, []
w = _ImportWorker(pid['id'], A, 2026, 8); w.done.connect(res.update); w.failed.connect(err.append)
w.run(); assert not err, err
eq = win.equipment_page; eq._rebuild(quiet=True)
print('imported', res['inserted'], 'events')

# ── grouping ──────────────────────────────────────────────────────────────
alarms = ats.get_unreported_faults(pid['id'], 2026, 8, min_hours=1.0)
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

f = win.work_log_form
print('banner :', f.alarm_banner.text()[:150])
print('fault  :', f.fault_input.toPlainText()[:110])
print('alarms carried:', len(f._alarm_ids))
print('precedents panel shown:', not f.prec_group.isHidden(), '|', f.prec_hint.text()[:70])
assert len(f._alarm_ids) == g['units']

QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
f.work_input.setPlainText('Restarted the BSC and re-established PCS comms on all affected units.')
f.root_cause_input.setPlainText('Comms card lock-up after grid disturbance')
f.no_materials.setChecked(True)
f._save_entry()

c = dbm.get_connection()
wl = c.execute('SELECT id, alarm_event_id FROM work_logs ORDER BY id DESC LIMIT 1').fetchone()
c.close()
linked = get_work_log_alarms(wl['id'])
print('\nreport #{} primary alarm #{} linked alarms: {}'.format(
    wl['id'], wl['alarm_event_id'], len(linked)))
assert sorted(linked) == sorted(g['ids'])

# every alarm of the incident must leave the list, not just the primary
alarms2 = ats.get_unreported_faults(pid['id'], 2026, 8, min_hours=1.0)
gone = set(g['ids']) & {a['id'] for a in alarms2}
print('alarms: {} -> {}; still-unreported members of the incident: {}'.format(
    len(alarms), len(alarms2), len(gone)))
assert not gone
assert len(alarms2) == len(alarms) - g['units']

# navigation came back to Equipment on its own
print('page after save:', win.stack.currentIndex(), '(equipment =', mwmod.PAGE_EQUIPMENT, ')')
assert win.stack.currentIndex() == mwmod.PAGE_EQUIPMENT

# ── "seen before?" now finds that report ──────────────────────────────────
prec = ats.find_precedents(pid['id'], trigger_name=g['trigger_name'])
print('\nprecedents for the same trigger:', len(prec))
for p in prec:
    print('   {} [{}] {} -> {}'.format(p['date'], p['match'],
                                       str(p['fault_description'])[:40],
                                       str(p['work_performed'])[:44]))
assert prec and prec[0]['match'] == 'same alarm'

# a second unit hit by the same fault should surface it in the form
other = next((a for a in alarms2 if a['trigger_name'] == g['trigger_name']), None)
if other:
    eq.show_asset(other['asset_code'])
    eq.tabs.setCurrentIndex(idx)
    for r in range(eq.gap_tbl.rowCount()):
        gg = eq.gap_tbl.item(r, 0).data(Qt.UserRole)
        if gg['trigger_name'] == g['trigger_name']:
            eq.gap_tbl.selectRow(r); eq._raise_report(eq.gap_tbl); break
    print('\nsecond occurrence — precedent panel:')
    print('   hint:', f.prec_hint.text())
    print('   rows:', f.prec_list.rowCount())
    assert f.prec_list.rowCount() >= 1
    f.prec_list.selectRow(0); f._show_precedent()
    print('   detail:', f.prec_detail.text()[:130].replace('<br>', ' | '))
    f._copy_precedent()
    print('   copied fix:', f.work_input.toPlainText()[:60])
    assert 'Restarted the BSC' in f.work_input.toPlainText()

# ── tree reveal ───────────────────────────────────────────────────────────
eq.search.setText('49.01.02'); eq._jump_to_search()
cur = eq.tree.currentItem()
print('\ntree reveal -> selected {!r} (code {})'.format(
    cur.text(0) if cur else None, cur.data(0, Qt.UserRole) if cur else None))
assert cur is not None and cur.data(0, Qt.UserRole) == '49.01.02'

print('\nSLICE 3 SMOKE OK')

H.finish()
