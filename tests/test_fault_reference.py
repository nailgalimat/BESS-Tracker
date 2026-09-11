"""The manufacturer's fault reference shows beside a fault on the Equipment
page and in the Work Report form, and is hidden for an unknown fault.
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

print('=== the reference file loads ===')
ref = ats.load_fault_reference()
print('   entries:', len(ref))
assert len(ref) >= 12

print('\n=== every trigger in six months that has an entry ===')
import sqlite3
con = sqlite3.connect(copy)
names = [r[0] for r in con.execute(
    'SELECT DISTINCT trigger_name FROM alarm_events')]
con.close()
hit = [(n, ats.get_fault_reference(n)) for n in names]
hit = [(n, r) for n, r in hit if r]
for n, r in sorted(hit)[:20]:
    print('   {:<56} <- {}'.format(str(n)[:56], r['trigger_pattern']))
print('   {} of {} trigger names covered'.format(len(hit), len(names)))

print('\n=== the SOC answer is attached to the right alarm ===')
r = ats.get_fault_reference('LC - SYSTEM ALARM STATE1')
assert r, 'the LC status word must have an entry'
print('   source :', r['source'])
print('   cause  :', r['cause'][:150])
print('   remedy :', r['remedy'][:190])
assert 'SOC' in r['cause']

print('\n=== the comm-fault repair carries the site knowledge ===')
r = ats.get_fault_reference('BSC - System Fault Status: BSC-PCS comm fault')
print('   source :', r['source'])
print('   remedy :', r['remedy'][:230])
assert 'RJ-45' in r['remedy'] and 'other' in r['remedy']

print('\n=== an echo tells you to go find the specific fault ===')
r = ats.get_fault_reference('LC - Fault Status 1: PCS fault')
print('   remedy :', r['remedy'][:200])
assert 'which PCS' in r['remedy']

print('\n=== it shows on the Equipment page ===')
win = mwmod.MainWindow()
con = sqlite3.connect(copy); con.row_factory = sqlite3.Row
p = con.execute('SELECT id,name FROM projects ORDER BY id LIMIT 1').fetchone(); con.close()
win._open_project(p['id'], p['name'])
eq = win.equipment_page
win._navigate(mwmod.PAGE_EQUIPMENT)
eq.show_asset('3')
eq._load_note('LC - Fault Status 1: LC-PCS comm fault')
print('   reference box shown:', not eq.ref_box.isHidden())
print('   text starts:', eq.ref_box.text()[:120].replace('<br>', ' | '))
assert not eq.ref_box.isHidden()
eq._load_note('Some fault nobody documented')
print('   hidden for an unknown fault:', eq.ref_box.isHidden())
assert eq.ref_box.isHidden()

print('\n=== and in the Work Report form ===')
f = win.work_log_form
f.proj_combo.setCurrentIndex(f.proj_combo.findData(p['id']))
f._load_precedents('BSC - System Fault Status: BSC-PCS comm fault')
print('   reference shown:', not f.prec_ref.isHidden())
print('   text:', f.prec_ref.text()[:150].replace('<br>', ' | '))
assert not f.prec_ref.isHidden()
f._load_precedents('Nothing documented here')
assert f.prec_ref.isHidden()
print('   hidden for an unknown fault: True')

print('\nREFERENCE SMOKE OK')

H.finish()
