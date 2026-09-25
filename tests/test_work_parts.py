"""Materials on a Work record: booked in the office, written off from stock
only when somebody asks for it — and the warehouse reconciles either way.

The owner's problem: a record says "fuses in LVDB A and C phase blown and
replaced" and there was nowhere to say which fuses, let alone take them out of
stock. This test puts a record with the phone's own note on it, books materials
against it, writes them off, undoes that, and checks after each step that the
warehouse quantity and the stock_transactions trail still explain each other.

Three rules it pins:
  * nothing leaves stock until the explicit action is invoked — building the
    card, selecting the record and saving it all move zero;
  * a saved card never blanks the free-text `spare_parts` the phone wrote (the
    card does not show every column, and a missing key means "no opinion");
  * more than the stock on hand is refused, not clamped — stock_service floors
    an item at 0 while recording the full quantity, so a clamped OUT could not
    be reversed back to where it started.
"""
import _harness as H            # must be first
import database.db_manager as dbm
import services.stock_service as ss
import services.work_journal_service as wj
import services.worklog_parts_service as wparts

H.fresh_db('work_parts.db')

c = dbm.get_connection()
c.execute("INSERT INTO projects (name, num_zones, num_blocks, num_containers, "
          "project_type) VALUES ('TK', 9, 70, 4, 'BESS')")
PID = c.execute("SELECT id FROM projects").fetchone()[0]
c.execute("INSERT INTO containers (project_id, zone_number, block_number, "
          "container_index, container_type, serial_number) "
          "VALUES (?,1,1,1,'LC Cabinet','SN1')", (PID,))
for num, desc, unit in (('FUSE-63A', 'Fuse 63 A gG', 'pcs'),
                        ('FUSE-100A', 'Fuse 100 A gG', 'pcs')):
    c.execute("INSERT INTO materials (material_number, description, unit) "
              "VALUES (?,?,?)", (num, desc, unit))
c.commit(); c.close()

import services.project_service as psvc
psvc.get_block_map(PID, refresh=True)

PHONE_NOTE = '2 fuses, 63 A'
EID = 'w-lvdb'


def entry(eid, date, block, fault, **kw):
    conn = dbm.get_connection()
    cols = dict(id=eid, project_id=PID, log_date=date, plant_block=block,
                fault_name=fault, status='open', category='fault',
                description=kw.pop('description', 'Fuses in LVDB A phase and C '
                                                  'phase was blown and replaced '
                                                  'to new one'),
                spare_parts=kw.pop('spare_parts', ''),
                sync_status=kw.pop('sync_status', 'synced'),
                site_location='', created_at=date + ' 08:00:00',
                updated_at=date + ' 08:00:00')
    cols.update(kw)
    conn.execute("INSERT INTO work_log_entries ({}) VALUES ({})".format(
        ','.join(cols), ','.join('?' * len(cols))), list(cols.values()))
    conn.commit(); conn.close()


entry(EID, '2026-09-22', 33, 'LVDB fuse blown', spare_parts=PHONE_NOTE)

WID = ss.ensure_project_warehouse(PID, 'TK')
ss.set_stock_level(WID, 'FUSE-63A', quantity=10, unit='pcs')
ss.set_stock_level(WID, 'FUSE-100A', quantity=1, unit='pcs')


def qty(material, warehouse=None):
    s = ss.get_stock_quantity(warehouse or WID, material)
    return float(s['quantity']) if s else 0.0


def log_tx(material):
    return [t for t in ss.get_transactions(warehouse_id=WID,
                                           material_number=material)
            if (t['reference'] or '').startswith('LOG-')]


START = qty('FUSE-63A')

# ── the phone's own words reach the card ────────────────────────────────
row = [r for r in wj.records(PID) if r['key'] == 'e:' + EID][0]
H.check(row['parts'] == PHONE_NOTE,
        'the record carries what the field typed: "{}"'.format(row['parts']))

# ── booking materials moves no stock ────────────────────────────────────
wh = wparts.resolve_warehouse_for_entry(EID)
H.check(wh['id'] == WID, "the record's warehouse is its project's ({})".format(wh['name']))

p1 = wparts.add_part(EID, 'FUSE-63A', 2)
p2 = wparts.add_part(EID, 'FUSE-63A', 1)
parts = wparts.get_parts_for_entry(EID)
H.check(len(parts) == 2 and all(p['description'] == 'Fuse 63 A gG'
                                and p['unit'] == 'pcs' for p in parts),
        'two rows, described and united from the catalogue')
H.check(parts[0]['available'] == START
        and parts[0]['target_warehouse_name'] == wh['name'],
        'each row shows the stock on hand ({}) and the warehouse it would leave'
        .format(parts[0]['available']))
H.check(qty('FUSE-63A') == START and not log_tx('FUSE-63A'),
        'booking them touched no stock and wrote no transaction')

# ── the explicit write-off ──────────────────────────────────────────────
res = wparts.deduct_all_pending(EID)
H.check(res['deducted'] == 2 and not res['failed'],
        'both rows written off: {}'.format(res))
H.check(qty('FUSE-63A') == START - 3,
        'the warehouse dropped by exactly 3: {} → {}'.format(START, qty('FUSE-63A')))
out = [t for t in log_tx('FUSE-63A') if t['transaction_type'] == 'OUT']
H.check(len(out) == 2 and sum(t['quantity'] for t in out) == 3
        and all(t['reference'] == 'LOG-' + EID[:8] for t in out),
        'two OUT transactions of 3 in total, referenced {}: {}'.format(
            'LOG-' + EID[:8], [(t['transaction_type'], t['quantity'],
                                t['reference']) for t in out]))
H.check(wparts.get_parts_count(EID) == {'total': 2, 'pending': 0, 'deducted': 2},
        'the badge counts them written off: {}'.format(wparts.get_parts_count(EID)))

# ── a written-off row is not editable or removable ──────────────────────
try:
    wparts.update_part(p1, quantity=5)
    H.check(False, 'editing a written-off row was allowed')
except ValueError as e:
    H.check('reverse' in str(e).lower(),
            'editing a written-off row is refused: {}'.format(e))
try:
    wparts.delete_part(p1)
    H.check(False, 'deleting a written-off row was allowed')
except ValueError as e:
    H.check('reverse' in str(e).lower(),
            'and so is deleting it: {}'.format(e))
H.check(qty('FUSE-63A') == START - 3 and len(log_tx('FUSE-63A')) == 2,
        'neither refusal moved stock')

# ── undo, and the books balance ─────────────────────────────────────────
H.check(wparts.reverse_part(p1)['ok'] and wparts.reverse_part(p2)['ok'],
        'both write-offs undone')
H.check(qty('FUSE-63A') == START,
        'the warehouse is back where it started: {} (was {})'.format(
            qty('FUSE-63A'), START))
trail = log_tx('FUSE-63A')
ins = [t for t in trail if t['transaction_type'] == 'IN']
H.check(len(trail) == 4 and len(ins) == 2
        and sum(t['quantity'] for t in ins) == 3,
        'and the trail explains it: 2 OUT + 2 IN, 3 each way ({})'.format(
            sorted((t['transaction_type'], t['quantity']) for t in trail)))
H.check(wparts.get_parts_count(EID) == {'total': 2, 'pending': 2, 'deducted': 0},
        'the rows are pending again, still attached to the record')

# ── more than there is: refused, not driven negative ────────────────────
# stock_service floors an item at 0 but records the full quantity, so a clamped
# OUT could never be reversed back to the starting quantity. The write-off is
# refused instead; the office books the delivery in, or lowers the quantity.
p3 = wparts.add_part(EID, 'FUSE-100A', 4)
plan = wparts.plan_deduction(EID)
H.check([s['material_number'] for s in plan['short']] == ['FUSE-100A']
        and plan['short'][0]['short'] == 3,
        'the plan names the shortfall before anything moves: {}'.format(plan['short']))
H.check(len(plan['ok']) == 2,
        'and the rows that do fit are still offered ({})'.format(len(plan['ok'])))
bad = wparts.deduct_part(p3)
H.check(not bad['ok'] and '1' in bad['message'] and 'short' in bad['message'],
        'the write-off is refused with the numbers in it: {}'.format(bad['message']))
H.check(qty('FUSE-100A') == 1.0 and not log_tx('FUSE-100A'),
        'nothing was written off and nothing went negative: {}'.format(
            qty('FUSE-100A')))
# two rows of one material are short together even when each looks affordable
wparts.update_part(p3, quantity=1)
p4 = wparts.add_part(EID, 'FUSE-100A', 1)
plan = wparts.plan_deduction(EID)
H.check(plan['short'] and plan['short'][0]['wanted'] == 2,
        'the shortfall is summed per material over the record: {}'.format(
            plan['short']))
wparts.delete_part(p4)
wparts.delete_part(p3)

# ── a save must not blank what the phone wrote ──────────────────────────
def stored_note():
    conn = dbm.get_connection()
    try:
        return conn.execute("SELECT spare_parts FROM work_log_entries WHERE id=?",
                            (EID,)).fetchone()[0]
    finally:
        conn.close()


wj.save(PID, 'e:' + EID, date='2026-09-22', block=33, title='LVDB fuse blown',
        work_done='replaced', status='Open', kind=wj.KIND_FAULT)
H.check(stored_note() == PHONE_NOTE,
        'a save without a parts key leaves the phone note alone: "{}"'.format(
            stored_note()))
wj.save(PID, 'e:' + EID, date='2026-09-22', block=33, title='LVDB fuse blown',
        work_done='replaced', status='Open', kind=wj.KIND_FAULT, parts='')
H.check(stored_note() == '',
        'and an explicit empty parts key does clear it (the rule is "no key = '
        'no opinion", not "never write")')
conn = dbm.get_connection()
conn.execute("UPDATE work_log_entries SET spare_parts=? WHERE id=?", (PHONE_NOTE, EID))
conn.commit(); conn.close()

# ── the page ────────────────────────────────────────────────────────────
from PyQt5.QtWidgets import QApplication, QMessageBox
app = QApplication.instance() or QApplication([])

shown = []
QMessageBox.question = staticmethod(
    lambda *a, **k: (shown.append((a[1], a[2])), QMessageBox.Yes)[1])
QMessageBox.information = staticmethod(
    lambda *a, **k: (shown.append((a[1], a[2])), QMessageBox.Ok)[1])
QMessageBox.warning = staticmethod(
    lambda *a, **k: (shown.append((a[1], a[2])), QMessageBox.Ok)[1])

import ui.work_page as wp

page = wp.WorkPage()
page.set_month(2026, 9)
page.set_current_project(PID, 'TK')
page._select_key('e:' + EID)

H.check(page._parts is not None
        and page._parts.table.rowCount() == len(wparts.get_parts_for_entry(EID)),
        'the card builds a materials section listing the {} row(s)'.format(
            page._parts.table.rowCount() if page._parts else 0))
H.check(page._parts.table.item(0, 0).text() == 'FUSE-63A'
        and 'Pending' in page._parts.table.item(0, 4).text(),
        'with the material and its state: {} / {}'.format(
            page._parts.table.item(0, 0).text(),
            page._parts.table.item(0, 4).text()))
H.check(wh['name'] in page._parts.wh_lbl.text(),
        'and names the warehouse on screen: "{}"'.format(page._parts.wh_lbl.text()))
page._parts.mat_cb.setCurrentIndex(page._parts.mat_cb.findData('FUSE-100A'))
H.check('1' in page._parts.avail_lbl.text()
        and wh['name'] in page._parts.avail_lbl.text(),
        'picking a material shows what is on hand before it is booked: "{}"'
        .format(page._parts.avail_lbl.text()))
page._parts.mat_cb.setCurrentIndex(0)
col = wp.COLUMNS.index('Materials')
H.check(page.table.columnCount() == len(wp.COLUMNS)
        and page.table.item(0, col).text() == '2 pending',
        'the list shows the office it is unfinished: "{}"'.format(
            page.table.item(0, col).text()))
H.check(qty('FUSE-63A') == START,
        'opening the card wrote nothing to stock ({})'.format(qty('FUSE-63A')))

page._save_card()
H.check(stored_note() == PHONE_NOTE and qty('FUSE-63A') == START,
        'saving the card from the page keeps the phone note ("{}") and still '
        'moves no stock'.format(stored_note()))

shown.clear()
page._parts._write_off()
H.check(any(wh['name'] in t and 'LOG-' in t for _title, t in shown),
        'the confirmation says what leaves which warehouse, and the reference')
H.check(qty('FUSE-63A') == START - 3,
        'confirmed, the write-off happens: {} → {}'.format(START, qty('FUSE-63A')))
H.check(page.table.item(0, col).text() == '2 written off',
        'and the list follows without reloading: "{}"'.format(
            page.table.item(0, col).text()))

page._parts.table.selectRow(0)
H.check(not page._parts.edit_btn.isEnabled()
        and not page._parts.del_btn.isEnabled()
        and page._parts.undo_btn.isEnabled(),
        'a written-off row cannot be edited or removed, only undone')
page._parts._undo()                       # the first row goes back
page._parts.table.selectRow(1)            # the second is still written off
page._parts._undo()
H.check(qty('FUSE-63A') == START,
        'undone from the card, the warehouse is back to {}'.format(qty('FUSE-63A')))

# a shortfall is visible before confirming, and refuses on its own
wparts.add_part(EID, 'FUSE-100A', 4)
page._parts.refresh()
shown.clear()
page._parts._write_off()
H.check(shown and shown[0][0] == 'Not enough in stock'
        and 'short 3' in shown[0][1],
        'the shortfall is the FIRST thing on screen, before the confirmation: '
        '{}'.format(shown[0] if shown else None))
H.check(qty('FUSE-100A') == 1.0,
        'and FUSE-100A never went below the 1 in stock ({})'.format(qty('FUSE-100A')))

# an unsaved record has no id to hang parts on, and says so
page._new_record()
H.check(page._parts is None,
        'a record that is not saved yet offers no materials controls')

H.finish()
