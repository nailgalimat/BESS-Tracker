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
H.check(not win.month_box.isVisibleTo(win), 'and not on Work')
win.month_combo.setCurrentIndex(7)          # August
win.year_spin.setValue(2026)
H.check((win.current_year, win.current_month) == (2026, 8), 'header month is August 2026')
H.check(win.monthly_page.year_spin.value() == 2026
        and win.monthly_page.month_combo.currentIndex() == 7,
        'Monthly report follows the header month')

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

H.finish()
