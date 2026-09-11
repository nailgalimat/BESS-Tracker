"""Internal fault notes: saved per trigger, cleared by emptying, and never
referenced by any customer report module.
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

TRIG = 'BSC - System Fault Status: BSC-PCS comm fault'
win = mwmod.MainWindow()
conn = dbm.get_connection()
p = conn.execute('SELECT id,name FROM projects ORDER BY id LIMIT 1').fetchone()
conn.close()
win._open_project(p['id'], p['name'])
PID = p['id']
eq = win.equipment_page

print('=== service level ===')
print('   before:', ats.get_fault_note(PID, TRIG))
ats.set_fault_note(PID, TRIG,
                   'Reset the BSC first — comms usually come back on their own. '
                   'If not, check the fibre at the LC end; twice it was a loose '
                   'SFP, not the card.', updated_by='Nail')
rec = ats.get_fault_note(PID, TRIG)
print('   after :', rec['note'][:60], '| by', rec['updated_by'])
print('   handbook so far:', len(ats.get_fault_notes(PID)), 'note(s)')

print('\n=== Equipment page: open a unit, pick that fault ===')
win._navigate(mwmod.PAGE_EQUIPMENT)
eq.show_asset('32.01.02')
print('   note box enabled before picking a fault:', eq.note_edit.isEnabled())
rows = [eq.faults_tbl.item(r, 0).text() for r in range(eq.faults_tbl.rowCount())]
i = rows.index(TRIG)
eq.faults_tbl.selectRow(i)
print('   selected fault:', eq.faults_tbl.item(i, 0).text()[:50])
print('   note shown    :', eq.note_edit.toPlainText()[:60])
print('   state         :', eq.note_state.text())
assert 'Reset the BSC first' in eq.note_edit.toPlainText()

print('\n=== edit it through the page ===')
eq.note_edit.setPlainText('Reset the BSC. If it repeats on the same unit twice '
                          'in a week, swap the comms card.')
eq._save_note()
print('   stored:', ats.get_fault_note(PID, TRIG)['note'][:70])
print('   state :', eq.note_state.text())

print('\n=== switching asset clears the box ===')
eq.show_asset('3')
print('   enabled:', eq.note_edit.isEnabled(), '| text:', repr(eq.note_edit.toPlainText()))
assert not eq.note_edit.isEnabled()

print('\n=== it surfaces in the Work Report form ===')
f = win.work_log_form
f.proj_combo.setCurrentIndex(f.proj_combo.findData(PID))
f._load_precedents(TRIG)
print('   note visible:', not f.prec_note.isHidden())
print('   note text   :', f.prec_note.text()[:80])
assert not f.prec_note.isHidden() and 'Reset the BSC' in f.prec_note.text()
f._load_precedents('Some fault nobody has written about')
print('   for an unknown fault, hidden:', f.prec_note.isHidden())
assert f.prec_note.isHidden()

print('\n=== it must NOT reach the customer report ===')
import ui.monthly_reports_page as mp
src = open('ui/monthly_reports_page.py', encoding='utf-8').read()
for bad in ('fault_notes', 'get_fault_note'):
    print('   monthly_reports_page mentions {}: {}'.format(bad, bad in src))
    assert bad not in src
for svc in ('services/tashkent_report_service.py', 'services/bukhara_report_service.py'):
    s = open(svc, encoding='utf-8').read()
    assert 'fault_notes' not in s and 'get_fault_note' not in s
    print('   {} clean'.format(os.path.basename(svc)))

print('\n=== clearing a note deletes it ===')
ats.set_fault_note(PID, TRIG, '   ')
print('   now:', ats.get_fault_note(PID, TRIG))
assert ats.get_fault_note(PID, TRIG) is None

print('\nFAULT NOTES SMOKE OK')

H.finish()
