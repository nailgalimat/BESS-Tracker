"""Editing stock by hand: the Stock Levels dialog and the service under it.

The owner adds the odd material straight into a warehouse instead of waiting
for the next SAP export. That path used to go through `set_stock_level`, which
loses two things:

  * the description, which lives in the `materials` catalogue and not on the
    stock row, so a hand-typed material ended up as a quantity against a bare
    number with nothing to say what it was;
  * the audit trail — it writes the quantity straight onto the row, so the
    History tab shows an unexplained jump, and its `min_quantity=0` default
    silently zeroes the low-stock alert of any row edited for an unrelated
    reason.

`edit_stock_item` is the one hand-edit entry point, and the checks here are
about those two: every quantity move leaves an IN/OUT row measured against what
is on the shelf *now*, and nothing the caller did not ask about is touched.

The dialog is driven offscreen at the end, so the form and the service keep
agreeing about field names.
"""
import _harness as H            # must be first

import database.db_manager as dbm
import services.stock_service as ss
import services.material_service as ms

H.fresh_db('stock_edit.db')
c = dbm.get_connection()
c.execute("INSERT INTO projects (name, num_zones, num_blocks, num_containers) "
          "VALUES ('TK', 1, 8, 6)")
PID = c.execute("SELECT id FROM projects").fetchone()[0]
c.commit(); c.close()
WH = ss.ensure_project_warehouse(PID, 'TK BESS')


def row(material, warehouse=None):
    return next((r for r in ss.get_stock(warehouse or WH)
                 if r['material_number'] == material), None)


def notes(material):
    return [t['notes'] for t in ss.get_transactions(warehouse_id=WH,
                                                    material_number=material)]


def moves(material):
    """(type, qty) of every transaction, as a set — several hand edits in the
    same second come back in no particular order, and this is about what was
    booked, not when."""
    return sorted((t['transaction_type'], t['quantity'])
                  for t in ss.get_transactions(warehouse_id=WH,
                                               material_number=material))


# ── 1. a material typed in by hand arrives with a name ───────────────────────
print('=== 1. add by hand ===')
r1 = ss.edit_stock_item(WH, 'vd000077', quantity=10, unit='pcs',
                        description='Compressor oil filter')
got = row('VD000077')
H.check(r1['created'] and got is not None,
        'the row is created: {}'.format(r1['created']))
H.check(got and got['description'] == 'Compressor oil filter',
        'the description is there — not a bare number: {!r}'.format(
            (got or {}).get('description')))
H.check(ms.get_material('VD000077') is not None and r1['catalogue'] == 'created',
        'it went into the materials catalogue, so every other screen and the '
        'phone see the name too: {}'.format(r1['catalogue']))
H.check(row('vd000077') is None and got['material_number'] == 'VD000077',
        'the material number is stored upper-cased, so a hand-typed '
        'lower-case one cannot become a second row for the same part')
H.check(got['quantity'] == 10 and got['unit'] == 'pcs',
        'quantity and unit: {} {}'.format(got['quantity'], got['unit']))
H.check(moves('VD000077') == [('IN', 10)],
        'the 10 on the shelf arrived as an IN of 10, not an unexplained '
        'jump: {}'.format(moves('VD000077')))

# ── 2. correcting a quantity books the difference ────────────────────────────
print('=== 2. correct the quantity ===')
r2 = ss.edit_stock_item(WH, 'VD000077', quantity=7,
                        reason='recount after PM')
H.check(r2['delta'] == -3 and row('VD000077')['quantity'] == 7,
        '10 -> 7 leaves 7 on the shelf, delta {}'.format(r2['delta']))
H.check(moves('VD000077') == [('IN', 10), ('OUT', 3)],
        'the correction is an OUT of the difference, not of the whole '
        'count: {}'.format(moves('VD000077')))
H.check(any('10 → 7' in (n or '') and 'recount after PM' in (n or '')
            for n in notes('VD000077')),
        'the audit row states the arithmetic and the reason: {}'.format(
            notes('VD000077')))
H.check(all(t['project'] == 'TK' for t in ss.get_transactions(
            warehouse_id=WH, material_number='VD000077')),
        "the audit rows carry the warehouse's project")

# ── 3. the threshold is not collateral damage ────────────────────────────────
# This is the regression `set_stock_level` caused: its min_quantity=0 default
# zeroed the owner's low-stock alert whenever a row was touched for any other
# reason.
print('=== 3. an untouched field stays untouched ===')
ss.set_min_quantity(WH, 'VD000077', 4)
ss.edit_stock_item(WH, 'VD000077', quantity=9)
after = row('VD000077')
H.check(after['min_quantity'] == 4,
        'editing the quantity alone leaves the low-stock threshold at 4: '
        '{}'.format(after['min_quantity']))
H.check(after['description'] == 'Compressor oil filter'
        and after['unit'] == 'pcs',
        'and leaves the description and unit alone: {!r} / {!r}'.format(
            after['description'], after['unit']))

ss.edit_stock_item(WH, 'VD000077', min_quantity=2)
H.check(row('VD000077')['quantity'] == 9
        and row('VD000077')['min_quantity'] == 2,
        'and the other way round: setting the threshold does not move the '
        'quantity ({} on the shelf, threshold {})'.format(
            row('VD000077')['quantity'], row('VD000077')['min_quantity']))

# ── 4. an empty string is an explicit clear, None is silence ─────────────────
print('=== 4. "" clears, None says nothing ===')
ss.edit_stock_item(WH, 'VD000077', unit='')
H.check(row('VD000077')['unit'] == '',
        'a unit typed away is cleared — the dialog sends what the user left '
        'in the box: {!r}'.format(row('VD000077')['unit']))
ss.edit_stock_item(WH, 'VD000077', quantity=9)
H.check(row('VD000077')['description'] == 'Compressor oil filter',
        'a call with no description keeps the one in the catalogue: '
        '{!r}'.format(row('VD000077')['description']))
H.check(ms.get_material('VD000077')['notes'] == '',
        'the catalogue notes are carried through, never blanked')

# ── 5. the quantity is measured against the shelf now ───────────────────────
# A dialog can sit open while a write-off goes through from the phone. The
# target quantity is reached from what is there at the moment of the write.
print('=== 5. no overshoot from a stale read ===')
ss.edit_stock_item(WH, 'STALE-1', quantity=10, description='Fuse 250A')
ss.record_transaction(WH, 'STALE-1', 'OUT', 4, '2026-10-09')   # phone write-off
r5 = ss.edit_stock_item(WH, 'STALE-1', quantity=8)
H.check(r5['before'] == 6 and r5['delta'] == 2
        and row('STALE-1')['quantity'] == 8,
        'the shelf is re-read (6, not the 10 the dialog opened on), so 8 is '
        'reached with an IN of 2: before {} delta {} -> {}'.format(
            r5['before'], r5['delta'], row('STALE-1')['quantity']))

# ── 6. what it refuses ───────────────────────────────────────────────────────
print('=== 6. refusals ===')
for bad, why in ((dict(material_number='  '), 'an empty material number'),
                 (dict(material_number='NEG-1', quantity=-1),
                  'a negative quantity')):
    try:
        ss.edit_stock_item(WH, **bad)
        H.check(False, '{} is refused'.format(why))
    except ValueError as e:
        H.check(True, '{} is refused: {}'.format(why, e))
H.check(row('NEG-1') is None,
        'and the refused row was not half-created')

# ── 7. nothing to do is said plainly ────────────────────────────────────────
r7 = ss.edit_stock_item(WH, 'VD000077', quantity=9, min_quantity=2)
H.check(not r7['changed'] and not r7['delta'],
        'saving a dialog nobody edited changes nothing and says so: '
        '{}'.format(r7))
H.check(moves('VD000077') == [('IN', 2), ('IN', 10), ('OUT', 3)],
        'and writes no transaction — still the three real moves: {}'.format(
            moves('VD000077')))

# ── 8. through the dialog, offscreen ────────────────────────────────────────
print('=== 8. the dialog ===')
from PyQt5.QtWidgets import QApplication, QDialog, QMessageBox
app = QApplication.instance() or QApplication([])
warned = []
QMessageBox.warning = staticmethod(
    lambda _p, t, m, *a, **k: warned.append(m) or QMessageBox.Ok)
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.Ok)

import ui.stock_page as sp

# editing an existing row: the form opens on what is on the shelf
dlg = sp.StockItemDialog('TK BESS — Stock', WH, row('VD000077'))
H.check(dlg.mat_input.isReadOnly() and dlg.qty_spin.value() == 9
        and dlg.desc_input.text() == 'Compressor oil filter'
        and dlg.min_spin.value() == 2,
        'the edit form is prefilled and the material number is locked: '
        '{} {} {!r}'.format(dlg.mat_input.isReadOnly(), dlg.qty_spin.value(),
                            dlg.desc_input.text()))
dlg.qty_spin.setValue(11)
H.check('IN 2' in dlg.effect.text(),
        'it says what Save will do before anything is written: {!r}'.format(
            dlg.effect.text()))
data = dlg.get_data()
H.check(set(data) == {'material_number', 'description', 'unit', 'quantity',
                      'min_quantity', 'reason'},
        'the form hands over exactly the service keywords: {}'.format(
            sorted(data)))
r8 = ss.edit_stock_item(WH, **data)
H.check(r8['delta'] == 2 and row('VD000077')['quantity'] == 11,
        'and applying it moves the shelf: {}'.format(
            row('VD000077')['quantity']))

# a number with no name is refused — the whole point of the field
add = sp.StockItemDialog('TK BESS — Stock', WH)
add.mat_input.setText('ZE000135')
add.qty_spin.setValue(3)
add._validate_and_accept()
H.check(add.result() != QDialog.Accepted and warned
        and 'description' in warned[-1].lower(),
        'a bare material number is refused: {!r}'.format(
            warned[-1][:60] if warned else ''))

# typing a number already on this shelf shows the count instead of zeroing it
add2 = sp.StockItemDialog('TK BESS — Stock', WH)
add2.mat_input.setText('vd000077')
add2._lookup_material()
H.check(add2.qty_spin.value() == 11 and add2.desc_input.text() ==
        'Compressor oil filter' and 'already stocked' in add2.effect.text(),
        'already stocked here: the form fills from the shelf and warns, so '
        'Save cannot read as "count it as 0": {} / {!r}'.format(
            add2.qty_spin.value(), add2.effect.text()[:48]))

# a number the catalogue knows but this warehouse does not: the name comes free
ms.save_material('ZE000135', 'Grease, high temperature', 'kg')
add3 = sp.StockItemDialog('TK BESS — Stock', WH)
add3.mat_input.setText('ZE000135')
add3._lookup_material()
H.check(add3.desc_input.text() == 'Grease, high temperature'
        and add3.unit_input.text() == 'kg' and add3.qty_spin.value() == 0,
        'the catalogue fills the name and unit, the shelf starts empty: '
        '{!r} / {!r} / {}'.format(add3.desc_input.text(),
                                  add3.unit_input.text(),
                                  add3.qty_spin.value()))

# ── 9. the page wires the two together ──────────────────────────────────────
print('=== 9. the page ===')
page = sp.StockViewTab()
idx = page.wh_combo.findData(WH)
page.wh_combo.setCurrentIndex(idx if idx >= 0 else 0)


def run_dialog(fill):
    def _exec(self):
        fill(self)
        self.accept()
        return self.result()
    sp.StockItemDialog.exec_ = _exec


run_dialog(lambda d: (d.mat_input.setText('6SW00366'),
                      d.desc_input.setText('Cooling fan'),
                      d.unit_input.setText('pcs'),
                      d.qty_spin.setValue(2)))
page._add_item()
added = row('6SW00366')
H.check(added and added['quantity'] == 2
        and added['description'] == 'Cooling fan',
        'Add material on the page creates the row with its name: '
        '{}'.format(added))
H.check(any(r['material_number'] == '6SW00366' for r in page._items)
        and page.wh_combo.currentData() == WH,
        'the table was reloaded and still shows the warehouse that was '
        'written to, not the main one')

sel = next(i for i, r in enumerate(page._items)
           if r['material_number'] == '6SW00366')
page.stock_table.setCurrentCell(sel, 0)
run_dialog(lambda d: d.qty_spin.setValue(5))
page._edit_item()
H.check(row('6SW00366')['quantity'] == 5,
        'Edit on the selected row writes through: {}'.format(
            row('6SW00366')['quantity']))
H.check(page.stock_table.currentRow() >= 0 and page._items[
            page.stock_table.currentRow()]['material_number'] == '6SW00366',
        'and the edited row stays selected after the reload')

H.finish()
