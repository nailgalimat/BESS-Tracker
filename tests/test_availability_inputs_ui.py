"""Monthly Reports → Availability inputs, driven through the page (offscreen,
fresh database): phone events waiting for the desktop are confirmed or rejected
there, PM records and manual downtime are added / edited / deleted with the
shared validation, a duplicate PM asks instead of adding a row, and the Work
Report's "excluded" window takes the report's real start and end time.
"""
import _harness as H            # must be first
import database.db_manager as dbm
import services.report_workflow_service as rw
import services.availability_service as av
import services.availability_inputs_service as avi

H.fresh_db('inputs_ui.db')
c = dbm.get_connection()
c.execute("INSERT INTO projects (name, num_zones, num_blocks, num_containers) VALUES ('TK', 1, 8, 6)")
PID = c.execute("SELECT id FROM projects").fetchone()[0]
for b in range(1, 9):
    c.execute("INSERT INTO containers (project_id, zone_number, block_number, container_index, "
              "container_type, serial_number) VALUES (?, 1, ?, 1, 'LC Cabinet', ?)", (PID, b, f'SN{b}'))
c.commit(); c.close()

from PyQt5.QtWidgets import QApplication, QDialog, QMessageBox, QInputDialog
from PyQt5.QtCore import QDate, QTime
app = QApplication.instance() or QApplication([])
import ui.monthly_reports_page as mrp
import ui.scada_report_page as srp

answers = {}
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
warned = []
QMessageBox.warning = staticmethod(lambda _p, t, m, *a, **k: warned.append(m) or QMessageBox.Ok)
QInputDialog.getText = staticmethod(lambda *a, **k: ('not ours', True))


def run_dialog(cls, fill):
    """Make the next dialog of `cls` fill itself and press Save."""
    def _exec(self):
        fill(self)
        self.accept()
        return self.result()
    cls.exec_ = _exec


page = mrp.MonthlyReportsPage()
page._pid, page._year, page._month, page._site_type = PID, 2026, 9, 'tashkent'
page.tabs.setEnabled(True)

for ev in ({'id': 'u-grid', 'project_id': PID, 'kind': 'excluded', 'blocks': '', 'date_from': '2026-09-12',
            'date_to': '2026-09-13', 'hours': 3, 'exclusion_type': 'Grid Outage', 'description': '14-17'},
           {'id': 'u-cnt', 'project_id': PID, 'kind': 'counts', 'blocks': 'abc', 'date_from': '2026-09-12',
            'date_to': '2026-09-12', 'hours': 5, 'exclusion_type': '', 'description': 'LC trip'},
           {'id': 'u-pm', 'project_id': PID, 'kind': 'pm', 'blocks': '', 'date_from': '2026-09-11',
            'date_to': '2026-09-11', 'hours': 3, 'exclusion_type': '', 'description': 'PM'},
           {'id': 'u-rej', 'project_id': PID, 'kind': 'counts', 'blocks': '2', 'date_from': '2026-09-12',
            'date_to': '2026-09-12', 'hours': 1, 'exclusion_type': '', 'description': 'dup'}):
    avi.route_field_event(ev)
page._load_all()
H.check(page.pend_table.rowCount() == 4 and 'waiting' in page.tabs.tabText(0),
        'four phone events listed; tab reads "{}"'.format(page.tabs.tabText(0)))


def select(table, key):
    for r in range(table.rowCount()):
        if table.item(r, 0).text() == str(key):
            table.setCurrentCell(r, 1)
            return True
    return False


print('=== confirm the grid outage with its real window ===')
def fill_excl(d):
    d.time_from.setTime(QTime(14, 0)); d.time_to.setTime(QTime(17, 0))
    d.date_to.setDate(QDate(2026, 9, 12))
    d._select_all_blocks()
run_dialog(srp.ExclusionDialog, fill_excl)
select(page.pend_table, 'u-grid'); page._review_pending()
ex = av.get_exclusions(project_id=PID, year=2026, month=9)
H.check(len(ex) == 1 and (ex[0]['time_from'], ex[0]['time_to']) == ('14:00', '17:00'),
        'exclusion 14:00-17:00 written: {}'.format([(e['time_from'], e['time_to'], e['affected_blocks']) for e in ex]))
H.check(page.excl_table.rowCount() == 1, 'and shown in the exclusions table')

print('\n=== a window left at 00:00-00:00 is not accepted ===')
d = srp.ExclusionDialog(None, blocks_list=list(range(1, 9)),
                        existing={'exclusion_type': 'Grid Outage', 'date_from': '2026-09-12',
                                  'date_to': '2026-09-12', 'time_from': '00:00', 'time_to': '00:00'})
srp.ExclusionDialog.accept(d)
H.check(d.result() != QDialog.Accepted and warned and 'ends before it starts' in warned[-1],
        'ExclusionDialog refuses an end not after the start')

print('\n=== confirm the downtime with a real block; junk blocks are refused first ===')
warned.clear()
def fill_cnt(d):
    d.blocks.setText('3'); d.lc.setCurrentIndex(2)
run_dialog(mrp.ManualDowntimeDialog, fill_cnt)
select(page.pend_table, 'u-cnt'); page._review_pending()
man = av.get_manual_unavailability(project_id=PID, year=2026, month=9)
H.check(len(man) == 1 and man[0]['block'] == 3 and man[0]['lc'] == 2 and man[0]['source'] == 'phone',
        'block 3 LC2, 5 h, source phone: {}'.format([(m['block'], m['lc'], m['downtime_h']) for m in man]))
jd = mrp.ManualDowntimeDialog(project_id=PID, existing={'block': 'abc', 'date_from': '2026-09-12',
                                                        'downtime_h': 5})
mrp.ManualDowntimeDialog.accept(jd)
H.check(jd.result() != QDialog.Accepted and 'not a block number' in jd.err.text(),
        'block "abc" refused in the dialog: ' + jd.err.text()[:60])

print('\n=== apply the phone PM after adding its block ===')
def fill_pm(d):
    d.blocks.setText('5')
run_dialog(mrp.PMDialog, fill_pm)
select(page.pend_table, 'u-pm'); page._review_pending()
pm = rw.get_pm_activities(PID, 2026, 9)
H.check(len(pm) == 1 and pm[0]['affected_blocks'] == '5' and pm[0]['source'] == 'phone'
        and pm[0]['source_ref'] == 'u-pm', 'PM record block 5, source phone, keyed on the event')

print('\n=== reject the last one ===')
select(page.pend_table, 'u-rej'); page._reject_pending()
H.check(page.pend_table.rowCount() == 0 and not page.pend_group.isVisibleTo(page)
        and page.tabs.tabText(0) == 'Availability inputs', 'nothing waiting; the group hides')

print('\n=== PM added on the desktop on the same block-day asks first ===')
def fill_dup(d):
    d.date_from.setDate(QDate(2026, 9, 11)); d.blocks.setText('5,6'); d.hours.setValue(4.25)
run_dialog(mrp.PMDialog, fill_dup)
asked = []
class _Box(QMessageBox):
    def exec_(self):
        asked.append(self.text())
        for b in self.buttons():
            if b.text().startswith('Keep'):
                self._clicked = b
        return 0
    def clickedButton(self):
        return self._clicked
orig_box = mrp.QMessageBox
mrp.QMessageBox = _Box
page._add_pm()
mrp.QMessageBox = orig_box
pm = {r['affected_blocks']: r['hours'] for r in rw.get_pm_activities(PID, 2026, 9)}
H.check(asked and 'block 5' in asked[0] and pm == {'5': 3.0, '6': 4.25},
        'asked about block 5; "keep" left 3 h there and added block 6 at 4.25 h: {}'.format(pm))
H.check(page.pm_table.rowCount() == 2 and page.pm_table.item(0, 4).text().startswith('📱'),
        'PM table lists both, with their source')

print('\n=== edit and delete ===')
row6 = [r for r in rw.get_pm_activities(PID, 2026, 9) if r['affected_blocks'] == '6'][0]
def fill_edit(d):
    d.hours.setValue(2.5)
run_dialog(mrp.PMDialog, fill_edit)
select(page.pm_table, row6['id']); page._edit_pm()
H.check(rw.get_pm_record(row6['id'])['hours'] == 2.5, 'PM record edited to 2.5 h')
select(page.pm_table, row6['id']); page._del_pm()
H.check(rw.get_pm_record(row6['id']) is None, 'deleted after confirmation')

def fill_man(d):
    d.blocks.setText('7'); d.use_times.setChecked(True)
    d.date_from.setDate(QDate(2026, 9, 14)); d.time_from.setTime(QTime(8, 0))
    d.date_to.setDate(QDate(2026, 9, 14)); d.time_to.setTime(QTime(11, 30))
    d.cause.setText('STANDBY after gas alarm, waiting for inspection')
run_dialog(mrp.ManualDowntimeDialog, fill_man)
page._add_man()
m7 = [m for m in av.get_manual_unavailability(project_id=PID, year=2026, month=9) if m['block'] == 7]
H.check(m7 and m7[0]['downtime_h'] == 3.5 and (m7[0]['time_from'], m7[0]['time_to']) == ('08:00', '11:30'),
        'manual downtime from clock times: 08:00-11:30 = 3.5 h')
def fill_man_edit(d):
    d.use_times.setChecked(False); d.hours.setValue(2)
run_dialog(mrp.ManualDowntimeDialog, fill_man_edit)
select(page.man_table, m7[0]['id']); page._edit_man()
m7b = [m for m in av.get_manual_unavailability(project_id=PID, year=2026, month=9) if m['block'] == 7][0]
H.check(m7b['downtime_h'] == 2 and m7b['time_from'] == '', 'edited to 2 h without times')
select(page.man_table, m7[0]['id']); page._del_man()
H.check(not [m for m in av.get_manual_unavailability(project_id=PID, year=2026, month=9) if m['block'] == 7],
        'manual row deleted')

print('\n=== Work Report "excluded" takes the report\'s start and end time ===')
import ui.work_log_form as wlf
form = wlf.WorkLogForm()
form.proj_combo.setCurrentIndex(form.proj_combo.findData(PID)); form._on_project_changed()
form.zone_combo.setCurrentIndex(form.zone_combo.findData(1)); form._on_zone_changed()
form.block_combo.setCurrentIndex(form.block_combo.findData(4)); form._on_block_changed()
form.container_combo.setCurrentIndex(1)
form.date_edit.setDate(QDate(2026, 9, 15))
form.fault_input.setPlainText('Grid dip'); form.work_input.setPlainText('Restarted')
form._set_impact('excluded')
form.start_time.setTime(QTime(14, 5)); form.end_time.setTime(QTime(14, 5))
warned.clear()
n0 = len(av.get_exclusions(project_id=PID))
form._save_entry()
H.check(warned and 'Start and End' in warned[-1] and len(av.get_exclusions(project_id=PID)) == n0,
        'start = end: refused before anything is saved ({})'.format(warned[-1][:50] if warned else ''))
form.end_time.setTime(QTime(16, 40))
form._save_entry()
new = [e for e in av.get_exclusions(project_id=PID) if e['date_from'] == '2026-09-15']
H.check(new and (new[0]['time_from'], new[0]['time_to']) == ('14:05', '16:40'),
        'window 14:05-16:40 (was 00:00 -> hours): {}'.format([(e['time_from'], e['time_to']) for e in new]))

H.finish()
