"""Monthly Reports pulls the month's work reports into 3.2, keeps July out,
and holds open items back from the customer.
"""
import _harness as H            # must be first: isolates DB, sync, network, history
import sys, io, os, shutil, sqlite3, warnings, datetime
warnings.filterwarnings('ignore')
import pandas as pd
import database.db_manager as dbm
from services.sync_config import sync_config
MVP, SCRATCH, copy = H.MVP, H.WORK, H.DB   # names the ported body uses

import database.db_manager as dbm
tmp=os.path.join(SCRATCH,"mr_test.db")
for s in ("","-wal","-shm"):
    try: os.remove(tmp+s)
    except OSError: pass
dbm.DB_PATH=tmp; dbm.initialize_database()

conn=dbm.get_connection()
conn.execute("INSERT INTO projects (name,num_zones,num_blocks,num_containers) VALUES ('TK BESS',1,5,10)")
pid=conn.execute("SELECT id FROM projects").fetchone()[0]
conn.execute("INSERT INTO containers (project_id,zone_number,block_number,container_index,container_type,serial_number) VALUES (?,1,3,1,'Battery','SN-3A')", (pid,))
cid=conn.execute("SELECT id FROM containers").fetchone()[0]
conn.commit(); conn.close()

import services.stock_service as ss
wh=ss.ensure_project_warehouse(pid,'TK BESS'); ss.set_stock_level(wh,'MOD',10,1,'pcs')
import services.work_log_service as wls
import services.availability_service as av

# two work reports in Aug 2026 (one with downtime), one in Jul (should NOT appear)
w1=wls.save_work_log(pid,cid,'2026-08-15',1,3,1,'SN-3A','DCDC fault','Replaced module',
    'Fixed','SAP-1','', root_cause='HW', affects_availability=1)
tx=ss.record_transaction(wh,'MOD','OUT',1,'2026-08-15',pid,f'WL-{w1}','m')
wls.add_work_log_material(w1,'MOD','Module',1,'pcs',wh,tx)
u=av.add_manual_unavailability(3,'2026-08-15','2026-08-15',4.0,None,'DCDC fault',pid,2026,8)
wls.set_work_log_unavailability(w1,u)
wls.save_work_log(pid,cid,'2026-08-20',1,3,1,'SN-3A','Comms drop','Reseated fibre','Fixed','','')
wls.save_work_log(pid,cid,'2026-07-10',1,3,1,'SN-3A','Old July fault','n/a','Fixed','')

from PyQt5.QtWidgets import QApplication
app=QApplication(sys.argv)
from ui.style import APP_STYLE; app.setStyleSheet(APP_STYLE)
from ui.monthly_reports_page import MonthlyReportsPage
page=MonthlyReportsPage()
page._pid=pid; page._year=2026; page._month=8; page._site_type='tashkent'
page._refresh_work_reports(); page._refresh_man()

print("work reports pulled for Aug:", len(page._wr_rows), "(expected 2)")
assert len(page._wr_rows)==2, "should pull only Aug work reports"
lines=page._collect_cm_lines()
print("CM lines (include ON):")
for l in lines: print("   •", l)
assert any('DCDC fault' in l and 'Replaced module' in l for l in lines)
assert any('Comms drop' in l for l in lines)
assert not any('July' in l for l in lines), "July report must not leak into Aug"

# add a manual extra note → merged
page.cm_text.setPlainText("Extra: quarterly inspection walk-down")
lines2=page._collect_cm_lines()
assert any('quarterly inspection' in l for l in lines2)
print("with manual note:", len(lines2), "lines total")

# include OFF → only manual note
page.cm_include.setChecked(False)
lines3=page._collect_cm_lines()
print("CM lines (include OFF):", lines3)
assert lines3==["Extra: quarterly inspection walk-down"]

# downtime from the work report shows in Unavailability tab
print("manual unavailability rows in Aug:", page.man_table.rowCount(), "(expected 1)")
assert page.man_table.rowCount()==1, "work report downtime should appear under Unavailability"

# Outstanding work must not go to the customer as maintenance performed.
# Printed as a plain bullet, an open item read exactly like a finished one —
# August 2026 would have sent "Antifreeze LOW LEVEL." as completed work.
page.cm_include.setChecked(True)
page.cm_text.setPlainText("")
import ui.monthly_reports_page as mrp
page._wr_rows = page._wr_rows + [
    {'src':'📱','date':'2026-08-22','block':4,'cont':4,
     'fault':'LCU alarm','action':'Liquid cooling LOW LEVEL!','sap':'',
     'status':'open'},
    {'src':'📱','date':'2026-08-23','block':5,'cont':1,
     'fault':'Vent panel','action':'Replaced','sap':'','status':'done'},
    {'src':'📱','date':'2026-08-24','block':6,'cont':2,
     'fault':'Legacy entry','action':'No status recorded','sap':'','status':''},
]
lines4 = page._collect_cm_lines()
print("\nCM lines with an open item present:")
for l in lines4: print("   •", l)
assert not any('LOW LEVEL' in l for l in lines4), "open item reached the customer"
assert any('Vent panel' in l for l in lines4), "a finished item was dropped"
assert any('Legacy entry' in l for l in lines4), "blank status must count as done"
assert mrp._is_open({'status':'Open'}) and not mrp._is_open({'status':'Fixed'})
page._refresh_work_reports()
print("count label:", page.wr_count_lbl.text())

print("\nMONTHLY AUTO-PULL SMOKE OK")

H.finish()
