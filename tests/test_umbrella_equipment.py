"""General ("umbrella") alarms are held out of the Equipment page fault tables
and stated in a sentence instead; the "general alarm" override works.
"""
import _harness as H            # must be first: isolates DB, sync, network, history
import sys, io, os, shutil, sqlite3, warnings, datetime
warnings.filterwarnings('ignore')
import pandas as pd
import database.db_manager as dbm
from services.sync_config import sync_config
MVP, SCRATCH, copy = H.MVP, H.WORK, H.DB   # names the ported body uses

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt
app = QApplication([])
import ui.main_window as mwmod
import services.asset_tree_service as ats

DRY = 'BSC - System Fault Status: Input dry node fault'
print('=== the rule itself ===')
print('   dry node    ->', ats.is_umbrella_trigger(DRY))
print('   comm fault  ->', ats.is_umbrella_trigger('BSC - System Fault Status: BSC-PCS comm fault'))

win = mwmod.MainWindow()
conn = dbm.get_connection()
p = conn.execute('SELECT id,name FROM projects ORDER BY id LIMIT 1').fetchone()
conn.close()
PID = p['id']
win._open_project(PID, p['name'])
eq = win.equipment_page

print('\n=== worst faults on a block that has both ===')
h = ats.get_asset_history(PID, '3')
for f in h['top_faults'][:6]:
    print('   {:>9} h  {}{}'.format(f['hours'], '※ ' if f['is_umbrella'] else '  ',
                                    str(f['trigger_name'])[:56]))
umb_pos = [i for i, f in enumerate(h['top_faults']) if f['is_umbrella']]
real_pos = [i for i, f in enumerate(h['top_faults']) if not f['is_umbrella']]
if umb_pos and real_pos:
    print('   umbrella rows sit after every specific one:', min(umb_pos) > max(real_pos))
    assert min(umb_pos) > max(real_pos)

print('\n=== the queue of jobs ===')
# Built on alarms planted in this copy of the database, not on whatever August
# still holds: once the month's exclusion windows were corrected, the real
# shadowed dry-node alarms moved inside them and there was nothing left to drop.
# Month 12 of 2030 so no real alarm or window can touch it.
conn = dbm.get_connection()
def plant(element, block, trigger, start, minutes):
    conn.execute("""INSERT INTO alarm_events
        (project_id, element, block, category, trigger_name, activated, deactivated,
         duration_min, is_excluded, year, month)
        VALUES (?, ?, ?, 'production', ?, ?, datetime(?, ?), ?, 0, 2030, 12)""",
        (PID, element, block, trigger, start, start, '+{} minutes'.format(minutes), minutes))
plant('BSC 50.01', 50, 'BSC - System Fault Status: Input dry node fault', '2030-12-05 10:00:00', 120)
plant('BSC 50.01', 50, 'BSC - System Fault Status: BSC-PCS comm fault', '2030-12-05 10:05:00', 90)
plant('BSC 51.01', 51, 'BSC - System Fault Status: Input dry node fault', '2030-12-05 10:00:00', 120)
conn.commit(); conn.close()

queue = ats.get_unreported_faults(PID, 2030, 12, min_hours=0.25, limit=5000)
dry = sorted(r['block'] for r in queue if 'dry node' in str(r['trigger_name']).lower())
real = [r['block'] for r in queue if 'BSC-PCS' in str(r['trigger_name'])]
print('   planted: dry node + BSC-PCS on block 50; dry node alone on block 51')
print('   queue keeps dry node on blocks {} and the real fault on {}'.format(dry, real))
assert real == [50], 'the specific fault must stay in the queue'
assert dry == [51], 'dry node beside a real fault is its shadow; alone it is the only evidence'

# and the month as it stands: every dry node still queued has nothing beside it
aug = ats.get_unreported_faults(PID, 2026, 8, min_hours=0.25, limit=5000)
print('   Aug backlog: {} alarm(s), dry-node in it: {}'.format(
    len(aug), sum(1 for r in aug if 'dry node' in str(r['trigger_name']).lower())))

print('\n=== the page shows the footnote ===')
win._navigate(mwmod.PAGE_EQUIPMENT)
eq.show_asset('3')
print('   footnote shown:', not eq.umbrella_note.isHidden())
print('   text          :', eq.umbrella_note.text()[:110])
rows = [eq.faults_tbl.item(r, 0).text() for r in range(eq.faults_tbl.rowCount())]
print('   rows in the fault table:', len(rows))
for r in rows[:4]:
    print('      ', r[:56])
leaked = [r for r in rows
          if any(k in r.upper() for k in ('SYSTEM ALARM', 'FAULT RESET', 'DRY NODE'))]
print('   status words leaked into it:', leaked)
assert not leaked
assert not eq.umbrella_note.isHidden()

print('\n=== a status word can still be reached, via the alarm log ===')
eq.tabs.setCurrentIndex(1)
alarm_rows = [(r, eq.alarm_tbl.item(r, 0).data(Qt.UserRole) or {})
              for r in range(eq.alarm_tbl.rowCount())]
cand = [(r, ev) for r, ev in alarm_rows
        if ats.is_umbrella_trigger(ev.get('trigger_name', ''),
                                   ats.get_umbrella_overrides(PID))]
print('   general alarms visible in the log:', len(cand))
if cand:
    r, ev = cand[0]
    eq.alarm_tbl.selectRow(r)
    print('   picked:', str(ev['trigger_name'])[:50])
    print('   checkbox reflects it:', eq.umb_chk.isChecked())
    assert eq.umb_chk.isChecked()
ats.set_fault_umbrella(PID, DRY, False)
print('   after clearing  ->', ats.is_umbrella_trigger(DRY, ats.get_umbrella_overrides(PID)))
assert not ats.is_umbrella_trigger(DRY, ats.get_umbrella_overrides(PID))
ats.set_fault_umbrella(PID, DRY, True)

print('\n=== the report table marks and sorts it ===')
import pandas as pd, sqlite3
con = sqlite3.connect(copy)
ev = pd.read_sql_query(
    "SELECT element AS Element, trigger_name AS 'Trigger name', "
    "cls_subsystem, cls_reason, cls_severity, activated AS Activated, "
    "deactivated AS Deactivation, duration_min "
    "FROM alarm_events WHERE year=2026 AND month=8 AND category='production'", con)
con.close()
from services.bukhara_report_service import build_event_type_table
t = build_event_type_table(ev, top_n=20)
print('   columns include umbrella:', 'umbrella' in t.columns)
for _, r in t.head(8).iterrows():
    print('   {:>8.1f} h  {}{}'.format(r['total_h'], '※ ' if r['umbrella'] else '  ',
                                       str(r['trigger'])[:52]))
assert 'umbrella' in t.columns
if t['umbrella'].any() and (~t['umbrella']).any():
    assert t['umbrella'].tolist() == sorted(t['umbrella'].tolist()), \
        'umbrella rows must come last'
    print('   umbrella rows are last: yes')

print('\nUMBRELLA SMOKE OK')

H.finish()
