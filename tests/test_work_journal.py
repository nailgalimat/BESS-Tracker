"""The Work journal: one list of phone records, desktop records and the old
Work Report rows; the filters behind the chips and tabs; the record card that
saves PTW, hours and the plant block and queues the change for sync; the
"needs a record (SCADA)" path; and the rule that `work_logs` is read-only.
"""
import datetime
import _harness as H            # must be first
import database.db_manager as dbm
import services.work_journal_service as wj

TODAY = datetime.date(2026, 9, 15)
H.fresh_db('work_journal.db')
c = dbm.get_connection()
c.execute("INSERT INTO projects (name, num_zones, num_blocks, num_containers, project_type) "
          "VALUES ('TK', 2, 16, 4, 'BESS')")
PID = c.execute("SELECT id FROM projects").fetchone()[0]
for z in (1, 2):
    for b in range(1, 9):
        c.execute("INSERT INTO containers (project_id, zone_number, block_number, "
                  "container_index, container_type, serial_number) "
                  "VALUES (?,?,?,1,'LC Cabinet',?)", (PID, z, b, 'SN%d%d' % (z, b)))
CID = c.execute("SELECT id FROM containers WHERE zone_number=2 AND block_number=1").fetchone()[0]
# an old-format Work Report row: zone 2 / block 1 = plant block 9
c.execute("INSERT INTO work_logs (project_id, container_id, date, zone_number, "
          "block_number, container_index, fault_description, work_performed, status) "
          "VALUES (?,?,?,2,1,1,'Old fault','Old repair','Fixed')",
          (PID, CID, '2026-09-05'))
c.commit(); c.close()

import services.project_service as psvc
psvc.get_block_map(PID, refresh=True)

# ── writing through the journal ─────────────────────────────────────────
key = wj.save(PID, None, date='2026-09-14', kind=wj.KIND_FAULT, block=12,
              lc='LC1', device='PCS 2', title='BSC-PCS comm fault',
              work_done='Reseated RJ-45 on the PCS communication board',
              internal_note='Fourth time on this block — plan a board swap',
              ptw='PTW-2609-121', sap='', status='In progress', impact='none')
H.check(key.startswith('e:'), 'a desktop record is written as a work_log_entries row')

rows = wj.records(PID, today=TODAY)
new = [r for r in rows if r['key'] == key][0]
old = [r for r in rows if r['old_format']][0]
H.check(len(rows) == 2, 'the journal lists the new record and the old Work Report row')
H.check(new['ptw'] == 'PTW-2609-121' and new['block'] == 12
        and new['node'].startswith('Block 12'),
        'PTW and the plant block are stored: {} / {}'.format(new['ptw'], new['node']))
H.check(new['internal_note'] and new['internal_note'] not in new['work_done'],
        'the internal note is kept apart from the customer text')
H.check(old['block'] == 9 and not old['editable'] and old['source'] == 'old',
        'the old row is plant block 9 (zone 2 / block 1) and read-only')

conn = dbm.get_connection()
sync = conn.execute("SELECT sync_status FROM work_log_entries WHERE id=?",
                    (key[2:],)).fetchone()[0]
conn.close()
H.check(sync == 'local', 'a new record is queued for sync ({})'.format(sync))

wj.save(PID, key, date=new['date'], kind=new['kind'], block=12, lc='LC1',
        device='PCS 2', title=new['title'], work_done='Board replaced',
        internal_note=new['internal_note'], ptw='PTW-2609-121', sap='SAP-77',
        status='Done', hours=2.5, time_from='08:05', time_to='10:35',
        impact='counts')
edited = [r for r in wj.records(PID, today=TODAY) if r['key'] == key][0]
H.check(edited['status'] == 'Done' and edited['sap'] == 'SAP-77'
        and edited['hours'] == 2.5 and edited['impact'] == 'counts',
        'an edit saves status, SAP, hours and the availability impact')

try:
    wj.save(PID, old['key'], title='nope')
    H.check(False, 'editing an old-format row must be refused')
except wj.ReadOnlyRecord as e:
    H.check('read-only' in str(e), 'editing an old-format row is refused: {}'.format(
        str(e)[:48]))

# ── filters: the tabs and chips ─────────────────────────────────────────
wj.save(PID, None, date='2026-09-13', kind=wj.KIND_FAULT, block=None,
        title='LCU alarm', work_done='', status='Open')
wj.save(PID, None, date='2026-09-12', kind=wj.KIND_PM, block=5,
        title='', work_done='PM as per checklist', status='Done', ptw='PTW-2609-117')
rows = wj.records(PID, today=TODAY)

# a PM written on the desktop must not reach the customer twice: once as PM
# hours (3.1) and again as corrective work (3.2)
import services.report_workflow_service as rw
pk = wj.save(PID, None, date='2026-09-11', kind=wj.KIND_PM, block=6,
             work_done='Coolant topped up during the visit', status='Done', hours=3)
pm_row = [r for r in wj.records(PID, today=TODAY) if r['key'] == pk][0]
H.check(pm_row['work_done'].startswith('PM: '),
        'a PM record says "PM" in its customer line: {}'.format(pm_row['work_done']))
cm = [r for r in rw.corrective_rows(PID, 2026, 9) if r['id'] == pk[2:]]
H.check(cm and rw.cm_skip_reason(cm[0]) == 'pm',
        'and so stays out of section 3.2 ({})'.format(cm and rw.cm_skip_reason(cm[0])))
H.check(not any('Coolant topped up during' in l for l in rw.cm_lines(rw.corrective_rows(PID, 2026, 9))),
        'no 3.2 line carries the PM text')
wj.delete(pk)

rows = wj.records(PID, today=TODAY)
H.check(len(wj.filter_rows(rows, 'open')) == 1, 'tab Open: one record')
H.check(len(wj.filter_rows(rows, 'noblock')) == 1, 'tab No block: one record')
H.check(len(wj.filter_rows(rows, kind=wj.KIND_PM)) == 1, 'chip type=PM: one record')
H.check(len(wj.filter_rows(rows, ptw_only=True)) == 2, 'chip "has PTW": two records')
H.check(len(wj.filter_rows(rows, block=12)) == 1, 'chip block=12: one record')
H.check(len(wj.filter_rows(rows, text='rj-45')) == 0
        and len(wj.filter_rows(rows, text='board')) == 1,
        'search looks in the text of the record')
H.check(len(wj.filter_rows(rows, source='old')) == 1, 'chip source=Old format: one row')

# ── a block read from an old phone record's location text ───────────────
for text, want in (('2zone 4block 2bsc', (12, '')), ('1zona 4 block 2bsc', (4, '')),
                   ('2Zone 5b 1lc 2bsc', (13, 'LC1')), ('4zon 5 blok 1bsc', None),
                   ('7', (7, '')), ('near the fence', None), ('', None)):
    s = wj.suggest_block(PID, text)
    got = (s['block'], s['lc']) if s else None
    H.check(got == want, 'location "{}" suggests {} (want {})'.format(text, got, want))

# ── the page ────────────────────────────────────────────────────────────
from PyQt5.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
import ui.work_page as wp

page = wp.WorkPage()
page.set_month(2026, 9)
page.set_current_project(PID, 'TK')
H.check(page.table.rowCount() == len(rows),
        'the page lists all {} records (table has {})'.format(len(rows), page.table.rowCount()))
H.check('4' in page._tab_buttons['all'].text(), 'the All tab shows its count: {}'
        .format(page._tab_buttons['all'].text()))

page.apply_filter({'tab': 'open'})
H.check(page.table.rowCount() == 1, 'the Open tab shows exactly the one open record')
page.apply_filter({'tab': 'noblock'})
H.check(page.table.rowCount() == 1, 'the No block tab shows exactly the one without a block')

page.apply_filter({'tab': 'all'})
page.table.selectRow(0)
H.check(bool(page._fields), 'selecting a row opens the record card')
H.check(page._fields['ptw'].text() in ('PTW-2609-121', 'PTW-2609-117', ''),
        'the card shows the PTW field')

# save from the card
page._select_key(key)
page._fields['ptw'].setText('PTW-2609-999')
page._save_card()
after = [r for r in wj.records(PID, today=TODAY) if r['key'] == key][0]
H.check(after['ptw'] == 'PTW-2609-999', 'the card saves the PTW back ({})'.format(after['ptw']))
conn = dbm.get_connection()
st = conn.execute("SELECT sync_status FROM work_log_entries WHERE id=?",
                  (key[2:],)).fetchone()[0]
conn.close()
H.check(st in ('local', 'pending'), 'and queues the edit for the phones ({})'.format(st))

# old-format row: the card refuses to save
old_row = [r for r in page._shown if r['old_format']]
if old_row:
    i = page._shown.index(old_row[0])
    page.table.selectRow(i)
    H.check(not page._fields['title'].isEnabled(),
            'the old-format card is read-only')

# ── needs a record (SCADA) ──────────────────────────────────────────────
conn = dbm.get_connection()
conn.execute("INSERT INTO alarm_events (project_id, year, month, category, "
             "is_excluded, duration_min, element, asset_code, unit_code, block, lc, "
             "unit, trigger_name, activated, deactivated, cls_reason) "
             "VALUES (?,2026,9,'production',0,320,'PCS','12.01','12.01.01',12,1,1,"
             "'DC-DC Converter Fault','2026-09-11 03:10:00','2026-09-11 08:30:00','Device')",
             (PID,))
conn.commit(); conn.close()
alarms = wj.needs_record(PID, 2026, 9)
H.check(len(alarms) == 1, 'one SCADA fault has no work record: {}'.format(len(alarms)))
page.refresh()
page._set_tab('scada')
H.check(page.table.rowCount() == 1, 'the "Needs a record (SCADA)" tab lists it')
page.table.selectRow(0)
page._record_from_alarm(alarms[0])
H.check(page._fields['title'].text() == 'DC-DC Converter Fault'
        and page._fields['block'].text() == '12'
        and page._fields['impact'].currentData() == 'none',
        'a record made from the fault is pre-filled, and does not count the stop twice')

H.finish()
