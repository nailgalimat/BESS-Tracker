"""Safety incidents typed on the Narrative tab reach the report generator.

QA H-8: the text was saved with the month but never passed on, so the DOCX
always said "No safety incidents in the reporting period". Pinned here: the
text is parsed into the rows the generators print, the page puts them in the
generator's arguments, and the worker hands them to both the Tashkent and the
Bukhara generator. That the DOCX prints them is checked on the real August
report (test_real_tashkent_august).
"""
import _harness as H            # must be first
import database.db_manager as dbm
import services.report_workflow_service as rw

print('=== the text as report rows ===')
from ui.monthly_reports_page import safety_incident_rows as rows
H.check(rows('') == [] and rows('None') == [] and rows('  no incidents. ') == [],
        'empty / "None" -> no rows (the report says there were none)')
got = rows('Near miss: ladder slipped at block 12\n\nCable burn | 1 cable | minor | replaced, toolbox talk')
H.check(len(got) == 2 and got[0] == {'incident': 'Near miss: ladder slipped at block 12',
                                     'equipment_loss': '-', 'weight': '', 'countermeasure': ''},
        'one incident per line: {}'.format(got[0]))
H.check(got[1] == {'incident': 'Cable burn', 'equipment_loss': '1 cable', 'weight': 'minor',
                   'countermeasure': 'replaced, toolbox talk'}, "'|' fills the table columns")

print('\n=== the page passes them to the generator ===')
H.fresh_db('safety.db')
c = dbm.get_connection()
c.execute("INSERT INTO projects (name, num_zones, num_blocks, num_containers) VALUES ('TK', 1, 70, 1)")
pid = c.execute("SELECT id FROM projects").fetchone()[0]
c.commit(); c.close()
from PyQt5.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
from ui.monthly_reports_page import MonthlyReportsPage
page = MonthlyReportsPage()
page._pid, page._year, page._month, page._site_type = pid, 2026, 9, 'tashkent'
page.safety_incidents.setPlainText('Fire alarm drill interrupted by real smoke at LC 12.01 | none | low | investigated')
page._save_narrative()
H.check(rw.get_report_month(pid, 2026, 9)['safety_incidents'].startswith('Fire alarm drill'),
        'saved with the month')
params = page._report_params()
H.check(params.get('safety_incidents') and params['safety_incidents'][0]['incident'].startswith('Fire alarm drill'),
        'in the generator arguments (was missing): {}'.format(params.get('safety_incidents')))
page.safety_incidents.setPlainText('')
H.check(page._report_params().get('safety_incidents') is None, 'empty -> None -> "No safety incidents"')

print('\n=== the worker hands them to both generators ===')
import services.tashkent_report_service as T
import services.bukhara_report_service as B
from ui.block_report_page import BlockReportWorker
seen = {}
T.generate_tashkent_report = lambda **k: seen.__setitem__('tashkent', k) or 'x.docx'
B.generate_bukhara_report = lambda **k: seen.__setitem__('bukhara', k) or 'y.docx'
inc = [{'incident': 'X', 'equipment_loss': '-', 'weight': '', 'countermeasure': ''}]
base = dict(output_path='o.docx', site_name='S', project_details={}, safety_incidents=inc)
tk = dict(base, **{k: k for k in ('working_status', 'pcs_cd', 'soc', 'soh_snapshot', 'lc_charge',
                                  'lc_discharge', 'hv_meter', 'alarms')})
bk = dict(base, **{k: k for k in ('site_kpi', 'overall_lc', 'battery_unit', 'meter_daily', 'alarms')})
errs = []
for site, prm in (('tashkent', tk), ('bukhara', bk)):
    w = BlockReportWorker(site, prm)
    w.error.connect(errs.append)
    w.run()                                  # synchronously, no thread
H.check(not errs, 'worker ran without error {}'.format(errs[:1]))
H.check(seen.get('tashkent', {}).get('safety_incidents') == inc, 'Tashkent generator receives them')
H.check(seen.get('bukhara', {}).get('safety_incidents') == inc, 'Bukhara generator receives them')

H.finish()
