"""The shell: nine menu items, every one of them opens with a project open,
the legacy pages are out of the menu but all still reachable from
Project → "Archive of old pages", and a page with a project combo no longer
asks "— Select —" while a project is open.
"""
import _harness as H            # must be first
import database.db_manager as dbm

H.fresh_db('nav_shell.db')
c = dbm.get_connection()
c.execute("INSERT INTO projects (name, num_zones, num_blocks, num_containers, project_type) "
          "VALUES ('TK Shell', 9, 70, 4, 'BESS')")
PID = c.execute("SELECT id FROM projects").fetchone()[0]
for b in range(1, 9):
    c.execute("INSERT INTO containers (project_id, zone_number, block_number, container_index, "
              "container_type, serial_number) VALUES (?, 1, ?, 1, 'LC Cabinet', ?)", (PID, b, 'SN%d' % b))
c.execute("INSERT INTO project_report_config (project_id, site_type) VALUES (?, 'tashkent')", (PID,))
c.commit(); c.close()

from PyQt5.QtWidgets import QApplication, QComboBox
app = QApplication.instance() or QApplication([])
import ui.main_window as mw

win = mw.MainWindow()
win.resize(1400, 900)

# ── the menu ─────────────────────────────────────────────────────────────
labels = [lb for _g, lb, _i in mw.NAV]
H.check(len(mw.NAV) == 9, 'nine menu items: {}'.format(', '.join(labels)))
H.check(labels[0] == 'Today' and labels[-1] == 'Project',
        'first is Today, last is Project')
H.check(len(win._nav_buttons) == 9, 'nine nav buttons built')

legacy = {'Overview', 'Daily Log', 'Work Reports', 'Field Log', 'Field Records',
          'Checklists / PM', 'Asset Register', 'Materials', 'Block Performance',
          'KPI Dashboard', 'Lifecycle', 'SCADA Report', 'Reports',
          'Work Log Report', 'Project Setup'}
arch_labels = {lb for lb, _i, _h in mw.ARCHIVE}
H.check(arch_labels == legacy,
        '15 legacy pages archived, none in the menu ({} archived)'.format(len(arch_labels)))
H.check(not (arch_labels & set(labels)), 'no archived page is also a menu item')

# ── open a project: every menu item must open ────────────────────────────
win._open_project(PID, 'TK Shell')
H.check(win.stack.currentIndex() == mw.PAGE_TODAY and win.page_title_lbl.text() == 'Today',
        'a project opens on Today')

for lb in labels:
    before = win.stack.currentIndex()
    win._go(lb)
    idx = win.stack.currentIndex()
    page = win.stack.currentWidget()
    ok = page is not None and idx == win._nav_by_label[lb][1]
    H.check(ok, 'menu "{}" opens page {} ({})'.format(lb, idx, type(page).__name__))
    active = [l for b, _i, l in win._nav_buttons if b.property('active') == 'true']
    H.check(active == [lb], '"{}" is the highlighted item (active: {})'.format(lb, active))

# Analysis loads its data: a stale second copy of the analytics functions
# (without _df) once shadowed the real ones and a PyQt slot error closed the exe
try:
    win.analytics_view._refresh_all()
    ok = True
except Exception as ex:                                  # noqa: BLE001
    ok = False
    print('   ', type(ex).__name__, ex)
H.check(ok, 'Analysis loads its tables and chart')

# ── every archived page still opens ──────────────────────────────────────
for lb, idx, _hint in mw.ARCHIVE:
    win._navigate(idx)
    H.check(win.stack.currentIndex() == idx,
            'archive "{}" still opens (page {})'.format(lb, idx))

# ── project context ──────────────────────────────────────────────────────
win._go('Today')
missing = []
for attr in mw.PROJECT_SCOPED_PAGES:
    page = getattr(win, attr, None)
    if page is None:
        continue
    for cattr in ('proj_combo', 'project_combo'):
        combo = getattr(page, cattr, None)
        if isinstance(combo, QComboBox) and combo.count():
            if combo.currentData() != PID:
                missing.append('{}.{} = {!r}'.format(attr, cattr, combo.currentText()))
H.check(not missing, 'no page asks "— Select —" with a project open ({})'
        .format('; '.join(missing) or 'all scoped'))

H.check(win.block_report_page.radio_tashkent.isChecked(),
        'Block Performance follows the project type instead of defaulting to Bukhara')

# ── the shared month ─────────────────────────────────────────────────────
win._go('Monthly report')
H.check(win.month_box.isVisibleTo(win), 'the month selector shows on Monthly report')
win._go('Work')
H.check(win.month_box.isVisibleTo(win), "and on Work, which follows it")
win._go("Spare parts")
H.check(not win.month_box.isVisibleTo(win), "and not on Spare parts")
win._go("Work")
win.month_combo.setCurrentIndex(7)          # August
win.year_spin.setValue(2026)
H.check((win.current_year, win.current_month) == (2026, 8), 'header month is August 2026')
H.check(win.monthly_page.year_spin.value() == 2026
        and win.monthly_page.month_combo.currentIndex() == 7,
        'Monthly report follows the header month')
H.check(win.planner_page.year.value() == 2026
        and win.planner_page.month.currentData() == 8,
        'Plan follows the header month')
win._step_month(1)
H.check((win.current_year, win.current_month) == (2026, 9)
        and win.monthly_page.month_combo.currentIndex() == 8,
        'the month stepper moves the whole application one month')
win._step_month(-1)

# ── one place for the project, the month and the sync state ─────────────
for page, attr in ((win.monthly_page, 'proj_combo'), (win.monthly_page, 'month_combo'),
                   (win.planner_page, 'project_combo'), (win.planner_page, 'month'),
                   (win.equipment_page, 'project_combo')):
    win.stack.setCurrentWidget(page)
    H.check(not getattr(page, attr).isVisibleTo(win),
            '{}.{} is not shown — the top bar owns it'.format(type(page).__name__, attr))
win._go('Work')
H.check(win.project_btn.isVisibleTo(win) and 'TK Shell' in win.project_btn.text(),
        'the top bar names the open project')
H.check(win._sync_state_of('❌ Sync failed: timeout') == ('crit', 'Sync failed: timeout'),
        'a failed sync is a red chip that says so')
H.check(win._sync_state_of('✅ Up to date  2026-11-11T10:00')[0] == 'ok',
        'a good sync is a green chip')
H.check(win._sync_state_of('⚠ 2 conflict(s)') == ('warn', '2 conflict(s)'),
        'conflicts are an amber chip')
win._on_sync_status_changed('❌ Sync failed: timeout')
H.check(win.sync_chip.text() == 'Sync failed: timeout' and win.sync_chip.isVisibleTo(win),
        'and the chip is in the top bar of every page')

# ── an alarm on Equipment is written up in the Work journal ─────────────
win.equipment_page.work_report_requested.emit(
    {'id': 987654, 'ids': [987654, 987655], 'block': 5, 'lc': 1,
     'trigger_name': 'PCS fault', 'activated': '2026-08-03 10:00:00',
     'deactivated': '2026-08-03 12:00:00', 'hours': 2.0, 'project_id': PID})
H.check(win.stack.currentWidget() is win.work_page,
        'Equipment "needs a report" opens the Work journal, not the archived form')
H.check(win.work_page._alarm_ids == [987654, 987655]
        and win.work_page._fields['title'].text() == 'PCS fault',
        'with the record pre-filled and tied to every alarm of the incident')

# ── the availability inputs moved out of the report ──────────────────────
tabs = [win.monthly_page.tabs.tabText(i) for i in range(win.monthly_page.tabs.count())]
H.check(len(tabs) == 4 and [t.split(' ')[0] for t in tabs] == ['1', '2', '3', '4'],
        'the Monthly report is the four steps only: {}'.format(tabs))
editor = win.monthly_page._inputs_tab
parents = []
w = editor
while w is not None:
    parents.append(w)
    w = w.parentWidget()
H.check(win.availability_page in parents,
        'the inputs editor itself now lives on the Availability page')
win._go('Availability')
H.check(win.monthly_page._pid == PID and win.monthly_page._year == 2026
        and win.monthly_page._month == 8,
        'opening Availability opens the header month — no "Open month" first')
H.check(win.availability_page.k_pm.value.text() != '—',
        'and the page summarises what the month charges ({})'.format(
            win.availability_page.k_pm.value.text()))

# ── switching the project closes the other plant's month ────────────────
c = dbm.get_connection()
c.execute("INSERT INTO projects (name, num_zones, num_blocks, num_containers, project_type) "
          "VALUES ('Other plant', 1, 4, 4, 'BESS')")
OTHER = c.execute("SELECT id FROM projects WHERE name='Other plant'").fetchone()[0]
c.commit(); c.close()
win._refresh_all()
win._open_project(OTHER, 'Other plant')
win._go('Monthly report')
# Availability reopens the month for the new plant; what must never happen is
# the page still holding the FIRST plant's month under the new plant's name
H.check(win.monthly_page._pid == OTHER,
        'after a project switch the open month belongs to the new plant, not the first one')
H.check(win.monthly_page.proj_combo.currentData() == win.monthly_page._pid,
        'the hidden project combo and the open month agree')
# and with no Availability refresh in between, the first plant's month is closed
win.monthly_page._pid = PID
win.monthly_page.set_current_project(OTHER)
H.check(win.monthly_page._pid is None and not win.monthly_page.tabs.isEnabled()
        and win.monthly_page.open_btn.text().startswith('Open '),
        'switching the project alone closes the other plant\'s month: {}'.format(
            win.monthly_page.open_btn.text()))
win._open_project(OTHER, 'Other plant')

# a serial typed into a block of zone 2 stays with that zone when zone 1 grows
from ui.project_dialog import ProjectDialog
dlg = ProjectDialog()
dlg.zones_spin.setValue(2)
dlg.all_blocks_spin.setValue(2)
dlg.apply_all_btn.click()
dlg._generate_bess_table()
r = next(x for x in dlg._container_rows if x['zone'] == 2 and x['block'] == 1 and x['idx'] == 1)
dlg.container_table.item(r['table_row'], 4).setText('SN-Z2B1')
dlg._zone_spins[0][0].setValue(3)
dlg._generate_bess_table()
r = next(x for x in dlg._container_rows if x['zone'] == 2 and x['block'] == 1 and x['idx'] == 1)
H.check(dlg.container_table.item(r['table_row'], 4).text() == 'SN-Z2B1',
        'a serial follows its zone and block when an earlier zone changes size')

H.finish()
