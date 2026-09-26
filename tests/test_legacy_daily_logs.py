"""The old Daily Log, surfaced read-only in two places.

59 rows of `daily_logs` went invisible in the redesign. They are not work
reports: one row is one material issued against one container, with a comment
describing the work. This test pins where they show up and — more importantly —
what they must never do.

  * they appear in work_journal_service.records() for their month, marked not
    editable, with the plant block translated from the container's zone and
    local block through the project's block map. The project here is built so
    that the naive reading (the container's own block_number) gives the WRONG
    answer: zone 6 / block 8 is plant block 48, not 8.
  * a row whose container is gone degrades to no block instead of guessing.
  * they never reach open_faults(), the Today page's Open faults or Needs your
    decision counts, or the stale-fault list — and every Today count still
    equals the length of the list its link opens.
  * the Work card for one of them offers no Save, no Delete, no Mark done and
    no materials write-off, and the service refuses a write to its key.
  * the Spare parts consumption history lists them newest first with the
    material and the quantity.
  * not one stock_transactions row is created by any of it.
"""
import datetime
import _harness as H            # must be first
import database.db_manager as dbm
import services.log_service as ls
import services.today_service as ts
import services.work_journal_service as wj

TODAY = datetime.date(2026, 9, 15)
H.fresh_db('legacy_daily_logs.db')

c = dbm.get_connection()
c.execute("INSERT INTO projects (name, num_zones, num_blocks, num_containers, "
          "project_type) VALUES ('TK', 9, 70, 6, 'BESS')")
PID = c.execute("SELECT id FROM projects").fetchone()[0]

# 70 plant blocks over 9 zones, blocks numbered 1..8 inside each zone — the real
# Tashkent shape, and the one where the block map is NOT the identity. Every
# block carries one LC cabinet (index 1), one PCS (index 2) and four batteries
# (indexes 3..6), exactly as the live project does.
for z in range(1, 10):
    for b in range(1, 9):
        if (z - 1) * 8 + b > 70:
            break
        for idx, ctype in ((1, 'LC Cabinet'), (2, 'PCS / Converter'),
                           (3, 'Battery'), (4, 'Battery'), (5, 'Battery'),
                           (6, 'Battery')):
            c.execute("INSERT INTO containers (project_id, zone_number, "
                      "block_number, container_index, container_type, "
                      "serial_number) VALUES (?,?,?,?,?,?)",
                      (PID, z, b, idx, ctype, 'S%d%d%d' % (z, b, idx)))


def container(zone, block, ctype, index):
    return c.execute("SELECT id FROM containers WHERE project_id=? AND "
                     "zone_number=? AND block_number=? AND container_type=? "
                     "AND container_index=?",
                     (PID, zone, block, ctype, index)).fetchone()[0]


# zone 6 / block 8 → plant block 48. The container's own block_number is 8.
C_PCS_48 = container(6, 8, 'PCS / Converter', 2)
# zone 4 / block 2 → plant block 26, third battery of the block (index 5 →
# BESS 3, the ordinal work_journal_service.container_for_node reads back)
C_BESS_26 = container(4, 2, 'Battery', 5)

WH = c.execute("SELECT id FROM warehouses WHERE is_main=1").fetchone()[0]
c.execute("INSERT INTO materials (material_number, description, unit) "
          "VALUES ('BP008089','Transformer oil temperature sensor','pcs')")


def daily(log_id, date, container_id, material, qty, comment,
          sap='', warehouse_id=None):
    c.execute("INSERT INTO daily_logs (id, project_id, container_id, date, "
              "material_number, quantity, comment, created_at, sap_ticket, "
              "warehouse_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
              (log_id, PID, container_id, date, material, qty, comment,
               date + ' 09:00:00', sap, warehouse_id))


daily(1, '2026-09-04', C_PCS_48, 'BP008089', 1,
      'Transformer oil temperature showed -245C. normal after replacing the sensor',
      sap='SAP-4471', warehouse_id=WH)
daily(2, '2026-09-11', C_BESS_26, 'VH000026', 2,
      'EPC connected a wrong auxiliary power supply during cold commissioning')
# another month, so the month filter has something to leave out
daily(4, '2026-08-20', C_PCS_48, 'BP008089', 3, 'Spare fuses issued')

# one live fault record, so the Today counts are not all zero
c.execute("INSERT INTO work_log_entries (id, project_id, log_date, plant_block, "
          "fault_name, description, status, category, sync_status, site_location, "
          "created_at, updated_at) VALUES ('f1',?,?,32,'BSC-PCS comm fault',"
          "'work done','open','fault','synced','','2026-09-14 08:00:00',"
          "'2026-09-14 08:00:00')", (PID, '2026-09-14'))
c.commit()
TX_BEFORE = c.execute("SELECT COUNT(*) FROM stock_transactions").fetchone()[0]
c.close()

# A row whose container is gone. `daily_logs.container_id` is NOT NULL with a
# foreign key, so this state cannot be reached through the app — which is why
# it has to be written with the constraint off. It is still worth pinning: the
# 59 live rows were written over a year, and an unresolvable container must show
# as "no block" rather than as a guessed number if one ever turns up.
import sqlite3
raw = sqlite3.connect(dbm.DB_PATH)                 # foreign_keys off by default
raw.execute("INSERT INTO daily_logs (id, project_id, container_id, date, "
            "material_number, quantity, comment, created_at, sap_ticket) "
            "VALUES (3,?,999999,'2026-09-08','KS000225',1,"
            "'FACP was showing a ground fault, replaced spd, now ok',"
            "'2026-09-08 09:00:00','')", (PID,))
raw.commit(); raw.close()

import services.project_service as psvc
psvc.get_block_map(PID, refresh=True)
H.check(psvc.zone_block_to_plant(PID, 6, 8) == 48,
        'the project maps zone 6 / block 8 to plant block 48 — the naive '
        'reading would say 8')

# ── 1 · the block-numbering trap ────────────────────────────────────────
D1, D2 = wj.month_range(2026, 9)
rows = wj.records(PID, date_from=D1, date_to=D2, today=TODAY)
legacy = [r for r in rows if wj.is_legacy(r)]
H.check(len(legacy) == 3,
        'September shows the three Daily Log rows and not August\'s: {}'.format(
            [r['key'] for r in legacy]))
H.check(len(rows) == 4, 'and the live fault record is still in the same list')

by_key = {r['key']: r for r in legacy}
r48 = by_key['d:1']
H.check(r48['block'] == 48,
        'the PCS row is plant block 48, translated from zone 6 / block 8 — '
        'got {}'.format(r48['block']))
H.check(r48['zone_label'] == 'Z6/B8',
        'and it says which zone block that is: {}'.format(r48['zone_label']))
H.check(r48['node'] == 'Block 48 · Z6/B8 · PCS',
        'the node reads as a person would say it: {}'.format(r48['node']))
H.check(by_key['d:2']['block'] == 26 and by_key['d:2']['device'] == 'BESS 3',
        'the battery row is block 26, BESS 3 (the block\'s third battery): '
        '{} / {}'.format(by_key['d:2']['block'], by_key['d:2']['device']))

H.check(r48['editable'] is False and r48['source'] == wj.SOURCE_DAILY,
        'a Daily Log row is marked not editable and sourced "daily"')
H.check(r48['title'] == 'BP008089 x1',
        'the title is the material and the quantity — what the record IS: '
        '{!r}'.format(r48['title']))
H.check(r48['work_done'].startswith('Transformer oil temperature showed -245C'),
        'the comment is the row\'s text — its only narrative')
H.check(r48['sap'] == 'SAP-4471', 'sap_ticket maps to the record\'s SAP field')
H.check(r48['warehouse'] and r48['quantity'] == 1 and r48['material_number'],
        'the row carries the material, the quantity and the warehouse')

# ── 2 · a missing container degrades honestly ───────────────────────────
gone = by_key['d:3']
H.check(gone['block'] is None and wj.has_no_block(gone),
        'the row whose container is gone has no block — not a guessed one')
H.check(gone['node'].startswith('No block'),
        'and it says so on its row: {!r}'.format(gone['node']))
H.check(not wj.filter_rows(rows, 'noblock'),
        'it is NOT in the "set a block on these" queue — it is read-only '
        'history no report reads: {}'.format(
            [r['key'] for r in wj.filter_rows(rows, 'noblock')]))

# ── 3 · never outstanding work ──────────────────────────────────────────
faults = wj.open_faults(PID, today=TODAY)
H.check(len(faults) == 1 and faults[0]['key'] == 'e:f1',
        'open_faults() holds only the real open fault: {}'.format(
            [r['key'] for r in faults]))
H.check(not [r for r in legacy if wj.is_open(r)],
        'no Daily Log row counts as open')
H.check(not [r for r in wj.filter_rows(rows, 'open') if wj.is_legacy(r)],
        'the Open tab leaves them out')
H.check(not [r for r in ts.stale_faults(faults) if wj.is_legacy(r)],
        'and the stale-fault list cannot hold one')

data = ts.summary(PID, 2026, 9, today=TODAY)
H.check(len(data['faults']) == 1,
        'Today still counts 1 open fault with the old rows in the database')
H.check(not [d for d in data['decisions'] if d['kind'] == 'record'],
        'and raises no "records without a plant block" decision: {}'.format(
            [d['title'] for d in data['decisions']]))
H.check(not [r for r in data['now'] if wj.is_legacy(r)],
        'nothing from the Daily Log is "happening now"')

# the rule the Today page lives by: every count is the length of its own list
opened = wj.filter_rows(wj.records(PID, today=TODAY), tab='open', kind='Fault')
H.check(len(opened) == len(data['faults']),
        'the Open faults link opens {} row(s) for a count of {}'.format(
            len(opened), len(data['faults'])))
H.check(len(ts.no_block_records(PID, 2026, 9, today=TODAY)) ==
        len(wj.filter_rows(wj.records(PID, date_from=D1, date_to=D2,
                                      today=TODAY), 'noblock')),
        'the no-block count and the no-block tab agree')

# ── 4 · the card offers nothing to press ────────────────────────────────
try:
    wj.save(PID, 'd:1', date='2026-09-04', title='edited')
    H.check(False, 'saving a Daily Log row should have been refused')
except wj.ReadOnlyRecord as e:
    H.check('read-only' in str(e), 'the service refuses a save: {}'.format(e))
try:
    wj.delete('d:1')
    H.check(False, 'deleting a Daily Log row should have been refused')
except wj.ReadOnlyRecord:
    H.check(True, 'and refuses a delete')

from PyQt5.QtWidgets import QApplication, QLabel, QPushButton
app = QApplication.instance() or QApplication([])
import ui.work_page as wp

page = wp.WorkPage()
page.set_month(2026, 9)
page.set_current_project(PID, 'TK')
H.check(page.table.rowCount() == 4,
        'the Work page lists all four September records: {}'.format(
            page.table.rowCount()))
i = next(i for i, r in enumerate(page._shown) if r['key'] == 'd:1')
page.table.selectRow(i)
app.processEvents()

buttons = {b.text(): b for b in page.card.findChildren(QPushButton)}
for name in ('Save', 'Delete', 'Mark done', 'Repeat on another node'):
    b = buttons.get(name)
    H.check(b is not None and not b.isEnabled(),
            '"{}" is on the card but disabled'.format(name))
H.check(not any('Write off' in t for t in buttons),
        'no write-off button anywhere on the card: {}'.format(sorted(buttons)))
H.check(page._parts is None,
        'and no materials section, so nothing can be deducted from stock again')
H.check(not any(w.isEnabled() for w in page._fields.values()),
        'every field on the card is disabled')
labels = ' '.join(l.text() for l in page.card.findChildren(QLabel))
H.check('Daily Log' in labels,
        'the card says where the record comes from, so the grey buttons are '
        'explained')
H.check('Material issued' in labels and 'Comment' in labels,
        'and labels the two texts for what they are, not "Fault"/"What was done"')
H.check('In report' not in labels,
        'nothing on this card is tagged "In report" — consumption is ours')

# ── 5 · the same rows from the material's side ──────────────────────────
hist = ls.consumption_history(PID)
H.check([h['id'] for h in hist] == [2, 3, 1, 4],
        'consumption history is newest first: {}'.format(
            [(h['id'], h['date']) for h in hist]))
H.check(len(hist) == 4, 'all four rows, both months — this is history')
h = next(x for x in hist if x['id'] == 1)
H.check(h['material_number'] == 'BP008089' and h['quantity'] == 1,
        'with the material and the quantity')
H.check(h['description'] == 'Transformer oil temperature sensor',
        'and the catalogue description of the material')
H.check(h['plant_block'] == 48 and h['device'] == 'PCS',
        'and the plant block it went to, same translation as the Work list')
H.check(h['sap_ticket'] == 'SAP-4471' and h['warehouse'],
        'and its SAP ticket and the warehouse it left')
H.check(next(x for x in hist if x['id'] == 3)['plant_block'] is None,
        'the row with no container has no block here either')
H.check(len(ls.consumption_history(PID, material_number='BP008089')) == 2
        and len(ls.consumption_history(PID, text='ground fault')) == 1,
        'the history filters by material and by text')

import ui.stock_page as sp
stock = sp.StockPage()
stock.set_current_project(PID, 'TK')
tab = stock.cons_tab
H.check(tab.cons_table.rowCount() == 4,
        'the Spare parts consumption tab shows the four rows: {}'.format(
            tab.cons_table.rowCount()))
H.check(tab.cons_table.item(0, 0).text() == '2026-09-11',
        'newest first on the page too: {}'.format(tab.cons_table.item(0, 0).text()))
row1 = [tab.cons_table.item(1, col).text() for col in range(6)]
H.check(row1[1] == 'KS000225' and row1[4] == '—',
        'the row with no container prints no block rather than a number: '
        '{}'.format(row1))
r48_row = next(i for i in range(tab.cons_table.rowCount())
               if tab.cons_table.item(i, 1).text() == 'BP008089'
               and tab.cons_table.item(i, 0).text() == '2026-09-04')
cells = [tab.cons_table.item(r48_row, col).text() for col in range(9)]
H.check(cells[3] == '1' and cells[4] == '48' and cells[7] == 'SAP-4471'
        and cells[6], 'material, quantity, block, warehouse and SAP on one '
                      'line: {}'.format(cells))

# ── 6 · not one stock movement ──────────────────────────────────────────
conn = dbm.get_connection()
after = conn.execute("SELECT COUNT(*) FROM stock_transactions").fetchone()[0]
levels = conn.execute("SELECT COUNT(*) FROM stock_items").fetchone()[0]
conn.close()
H.check(after == TX_BEFORE == 0,
        'no stock transaction was created by reading or showing any of it: '
        '{} → {}'.format(TX_BEFORE, after))
H.check(levels == 0, 'and no stock level was touched')

H.finish()
