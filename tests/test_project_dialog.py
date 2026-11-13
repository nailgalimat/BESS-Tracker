"""New project, BESS: what a block holds is chosen by name, not by position.

"Cont/Blk = 2" used to mean "LC and PCS, no battery" — the first two places of
a block were always LC and PCS, and the user asked why no BESS was added. Now
Transformer, RMU, SCC cabinet, LC cabinet and PCS are options and the battery
containers are a count, so a plant with the PCS inside the battery container
and the controller inside the MV station (SCC) can be entered as it is built.

Also pinned here: one button gives every zone the same number of blocks while
a single zone can still differ; the project is saved with its plant block
count (all zones together) and with the type chosen in the dialog.
"""
import _harness as H            # must be first
import database.db_manager as dbm

H.fresh_db('project_dialog.db')

from PyQt5.QtWidgets import QApplication, QMessageBox
app = QApplication.instance() or QApplication([])
from ui.style import APP_STYLE
app.setStyleSheet(APP_STYLE)

shown = []
QMessageBox.information = staticmethod(lambda *a, **k: shown.append(('info', a[2])) or QMessageBox.Ok)
QMessageBox.warning = staticmethod(lambda *a, **k: shown.append(('warn', a[2])) or QMessageBox.Ok)
QMessageBox.critical = staticmethod(lambda *a, **k: shown.append(('crit', a[2])) or QMessageBox.Ok)

from ui.project_dialog import ProjectDialog
from models.models import CONTAINER_TYPES_BY_PROJECT


def types_of(dlg, plant_block):
    out = []
    for r in dlg._container_rows:
        if r['zone_block'] == plant_block:
            out.append(dlg.container_table.cellWidget(r['table_row'], 2).currentText())
    return out


# ── the same for all zones, one button; one zone can still differ ────────
dlg = ProjectDialog()
dlg.zones_spin.setValue(3)
H.check(len(dlg._zone_spins) == 3, 'the zone cards follow the zone count without a separate Apply')
dlg.all_blocks_spin.setValue(5)
dlg.all_batteries_spin.setValue(2)
dlg.apply_all_btn.click()
H.check([(b.value(), c.value()) for b, c in dlg._zone_spins] == [(5, 2)] * 3,
        '"Apply to all zones" gives every zone 5 blocks and 2 batteries')
dlg._zone_spins[1][0].setValue(7)
dlg.zones_spin.setValue(4)
H.check([(b.value(), c.value()) for b, c in dlg._zone_spins] == [(5, 2), (7, 2), (5, 2), (5, 2)],
        'a zone changed on its card keeps its value; a new zone starts from the common row')

# ── a block the way it is ticked: default LC + PCS, then the batteries ───
dlg.bess_has_transformer.setChecked(True)
dlg.bess_has_rmu.setChecked(True)
dlg._generate_bess_table()
H.check(types_of(dlg, 1) == ['Transformer', 'RMU', 'LC Cabinet', 'PCS / Converter',
                              'Battery', 'Battery'],
        'Transformer, RMU, LC, PCS and two batteries: {}'.format(types_of(dlg, 1)))
H.check(len({r['zone_block'] for r in dlg._container_rows}) == 22,
        '5 + 7 + 5 + 5 blocks are numbered 1..22 across the zones')
H.check('22 block(s)' in dlg.bess_summary.text() and '2 × Battery' in dlg.bess_summary.text(),
        'the summary says what was built: {}'.format(dlg.bess_summary.text()))

# ── PCS inside the battery container, controller in the MV station ──────
dlg.bess_has_lc.setChecked(False)
dlg.bess_has_pcs.setChecked(False)
dlg.bess_has_scc.setChecked(True)
dlg._generate_bess_table()
H.check(types_of(dlg, 1) == ['Transformer', 'RMU', 'SCC Cabinet', 'Battery', 'Battery'],
        'no LC, no separate PCS, an SCC cabinet instead: {}'.format(types_of(dlg, 1)))
H.check('SCC Cabinet' in CONTAINER_TYPES_BY_PROJECT['BESS'],
        'SCC Cabinet is a type of its own in the drop-down')

# a serial typed into the table survives a regenerate, on the right block
row = next(r for r in dlg._container_rows if r['zone_block'] == 8 and r['idx'] == 4)
dlg.container_table.item(row['table_row'], 4).setText('SN-B8-BAT1')
dlg._generate_bess_table()
row = next(r for r in dlg._container_rows if r['zone_block'] == 8 and r['idx'] == 4)
H.check(dlg.container_table.item(row['table_row'], 4).text() == 'SN-B8-BAT1',
        'a serial typed in block 8 is still in block 8 after Generate')
other = next(r for r in dlg._container_rows if r['zone_block'] == 3 and r['idx'] == 4)
H.check(dlg.container_table.item(other['table_row'], 4).text() == '',
        'and has not leaked into a block of another zone')

# a type changed by hand is kept too — while the place means the same thing
dlg.container_table.cellWidget(row['table_row'], 2).setCurrentText('Auxiliary')
dlg._generate_bess_table()
row = next(r for r in dlg._container_rows if r['zone_block'] == 8 and r['idx'] == 4)
H.check(dlg.container_table.cellWidget(row['table_row'], 2).currentText() == 'Auxiliary',
        'a type changed by hand survives Generate')
# when an option shifts the places, the unit keeps its own type and serial
dlg.bess_has_rmu.setChecked(False)
dlg._generate_bess_table()
H.check(types_of(dlg, 8) == ['Transformer', 'SCC Cabinet', 'Auxiliary', 'Battery'],
        'unticking RMU shifts the places; Battery 1 keeps its hand-set type: {}'.format(types_of(dlg, 8)))
row = next(r for r in dlg._container_rows if r['zone_block'] == 8 and r['idx'] == 3)
H.check(dlg.container_table.item(row['table_row'], 4).text() == 'SN-B8-BAT1',
        'and its serial moved with it, not onto another unit')
dlg.bess_has_rmu.setChecked(True)
dlg._generate_bess_table()
row = next(r for r in dlg._container_rows if r['zone_block'] == 8 and r['idx'] == 4)
dlg.container_table.cellWidget(row['table_row'], 2).setCurrentText('Battery')

# nothing ticked and no batteries: refused, not an empty project
empty = ProjectDialog()
for cb, _t in empty._bess_options:
    cb.setChecked(False)
empty.all_batteries_spin.setValue(0)
empty.apply_all_btn.click()
shown.clear()
empty._generate_bess_table()
H.check(shown and shown[-1][0] == 'warn' and not empty._container_rows,
        'a block with nothing in it is refused with a message')

# ── saved as built ──────────────────────────────────────────────────────
dlg.name_input.setText('String BESS')
shown.clear()
dlg._save_project()
H.check(shown and shown[-1][0] == 'info', 'the project is saved: {}'.format(shown[-1:]))
c = dbm.get_connection()
proj = dict(c.execute("SELECT * FROM projects WHERE name='String BESS'").fetchone())
rows = c.execute("SELECT zone_number, block_number, container_index, container_type, "
                 "serial_number FROM containers WHERE project_id=? "
                 "ORDER BY zone_number, block_number, container_index", (proj['id'],)).fetchall()
c.close()
H.check(proj['num_zones'] == 4 and proj['num_blocks'] == 22,
        'num_blocks is the plant total, 22, not the largest zone: {}'.format(proj['num_blocks']))
H.check(proj['project_type'] == 'BESS', 'the project type is saved')
H.check(len(rows) == 22 * 5, '22 blocks x 5 items = {} rows'.format(len(rows)))
z2b7 = [r['container_type'] for r in rows if r['zone_number'] == 2 and r['block_number'] == 7]
H.check(z2b7 == ['Transformer', 'RMU', 'SCC Cabinet', 'Battery', 'Battery'],
        'zone 2 keeps its 7th block, built like the others')
H.check(any(r['serial_number'] == 'SN-B8-BAT1' and r['zone_number'] == 2
            and r['block_number'] == 3 for r in rows),
        'the serial is on plant block 8 = zone 2 / block 3')

# a PV project keeps its own type instead of being stored as BESS
pv = ProjectDialog()
pv._on_type_changed('PV String')
pv._generate_pvs_table()
pv.name_input.setText('PV one')
pv._save_project()
c = dbm.get_connection()
t = c.execute("SELECT project_type FROM projects WHERE name='PV one'").fetchone()[0]
c.close()
H.check(t == 'PV String', 'a PV String project is saved as PV String, not BESS ({})'.format(t))

# ── editing: a new block is built like the plant's other blocks ─────────
from ui.edit_project_dialog import EditProjectDialog
from services.project_service import get_containers_for_project
ed = EditProjectDialog()
ed._existing = get_containers_for_project(proj['id'])
H.check(ed._block_template() == ['Transformer', 'RMU', 'SCC Cabinet', 'Battery', 'Battery'],
        'a block added later copies the usual block: {}'.format(ed._block_template()))

H.finish()
