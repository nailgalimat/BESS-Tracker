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

# ── the serial follows the node; an edit keeps what the card does not show ──
c = dbm.get_connection()
for idx, typ, sn in ((2, 'PCS / Converter', 'PCS-24'), (3, 'Battery', 'BAT-24-1'),
                     (4, 'Battery', 'BAT-24-2'), (5, 'Battery', 'BAT-24-3'),
                     (6, 'Battery', 'BAT-24-4')):       # plant block 12 = zone 2 / block 4
    c.execute("INSERT INTO containers (project_id, zone_number, block_number, "
              "container_index, container_type, serial_number) VALUES (?,2,4,?,?,?)",
              (PID, idx, typ, sn))
c.execute("INSERT INTO work_log_entries (id, project_id, container_id, site_location, "
          "spare_parts, category, description, fault_name, status, log_date, created_at, "
          "updated_at, version, sync_status) VALUES ('ph1', ?, ?, '2zone 1block', "
          "'fuse 10A', 'fault', 'Fuse replaced', 'Fuse blown', 'done', '2026-09-10', "
          "'2026-09-10', '2026-09-10', 3, 'synced')", (PID, CID))
c.commit(); c.close()

for dev, want in (('BESS 3', 'BAT-24-3'), ('BESS 1', 'BAT-24-1'), ('PCS 2', 'PCS-24'),
                  ('LC cabinet', 'SN24'), ('MV station', ''), ('BESS', ''),
                  ('BESS 5', ''), ('', '')):
    got = wj.container_for_node(PID, 12, dev)[1]
    H.check(got == want, 'node Block 12 · {!r} → serial {!r} (want {!r})'.format(dev, got, want))
H.check(wj.container_for_node(PID, None, 'BESS 1') == (None, ''), 'no block, no serial')


def entry(eid):
    conn = dbm.get_connection()
    try:
        return dict(conn.execute("SELECT * FROM work_log_entries WHERE id=?", (eid,)).fetchone())
    finally:
        conn.close()


card = dict(date='2026-09-10', kind=wj.KIND_FAULT, lc='', title='Fuse blown',
            work_done='Fuse replaced', status='Done')
wj.save(PID, 'e:ph1', block=9, device='', **card)
e = entry('ph1')
H.check(e['site_location'] == '2zone 1block' and e['spare_parts'] == 'fuse 10A'
        and e['container_id'] == CID,
        'a desktop edit keeps the phone location, parts and container link ({!r}, {!r}, {})'
        .format(e['site_location'], e['spare_parts'], e['container_id']))
wj.save(PID, 'e:ph1', block=12, device='BESS 3', **card)
e = entry('ph1')
H.check(e['equipment_serial'] == 'BAT-24-3' and e['container_id'] != CID
        and e['sync_status'] == 'pending',
        'naming BESS 3 on block 12 links that battery and takes its serial ({})'
        .format(e['equipment_serial']))
wj.save(PID, 'e:ph1', block=12, device='BESS 3', serial='SWAP-001', **card)
H.check(entry('ph1')['equipment_serial'] == 'SWAP-001', 'a typed serial is kept')
wj.save(PID, 'e:ph1', block=12, device='BESS 3', serial='BAT-24-3',
        **dict(card, date='2026-09-09'))
H.check(entry('ph1')['log_date'] == '2026-09-09', 'the date can be corrected')

# the card: the serial fills in as the node is chosen; delete asks, then removes
page._set_tab('all')
page._select_key('e:ph1')
H.check(page._fields['serial'].text() == 'BAT-24-3', 'the card shows the serial')
page._fields['device'].setCurrentText('PCS 2')
H.check(page._fields['serial'].text() == 'PCS-24',
        'changing the device refills it ({})'.format(page._fields['serial'].text()))
page._fields['serial'].setText('TYPED-1')
page._fields['device'].setCurrentText('BESS 4')
H.check(page._fields['serial'].text() == 'TYPED-1', 'but never over a typed number')
page._new_record()
page._fields['block'].setText('12')
page._fields['device'].setCurrentText('BESS 4')
H.check(page._fields['serial'].text() == 'BAT-24-4', 'a new record fills it too')

from PyQt5.QtWidgets import QMessageBox
page._select_key('e:ph1')
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
page._delete_card()
H.check(entry('ph1')['deleted_at'] is None, 'Delete answered "No" keeps the record')
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
page._delete_card()
e = entry('ph1')
H.check(e['deleted_at'] and e['sync_status'] == 'pending'
        and 'e:ph1' not in [r['key'] for r in page._shown],
        'Delete removes it from the list and queues the delete for the phones ({}, {}, {})'.format(e["deleted_at"], e["sync_status"], page._key))

# ── photos: shown on the card, and laid out in folders a person can find ────
import os
from PyQt5.QtGui import QImage, QColor
from PyQt5.QtWidgets import QLabel
jpg = os.path.join(H.WORK, 'IMG_0001.jpg')
im = QImage(40, 30, QImage.Format_RGB32)
im.fill(QColor('#3366aa'))
im.save(jpg, 'JPG')
c = dbm.get_connection()
c.execute("INSERT INTO work_log_images (id, work_log_id, file_path, thumbnail_path, "
          "filename, size_bytes, upload_status) VALUES ('img1', ?, ?, ?, 'IMG_0001.jpg', ?, "
          "'uploaded')", (key[2:], jpg, jpg, os.path.getsize(jpg)))
c.commit(); c.close()

H.check(wj.photos_root().startswith(H.WORK), 'tests keep photo folders in the work dir')
row = [r for r in wj.records(PID, today=TODAY) if r['key'] == key][0]
name = wj.photo_folder_name(row)
H.check(name == '2026-09-14 Block 12 LC1 PCS 2 - BSC-PCS comm fault',
        'folder name: date, block, LC, device, fault ({})'.format(name))
H.check(wj.photo_folder_name(dict(row, title='A/B: "x"?', block=None, lc='', device=''))
        == '2026-09-14 No block - A B x', 'characters Windows refuses are dropped')
res = wj.mirror_photos()
folder = os.path.join(wj.photos_root(), 'TK', name)
H.check(os.path.isfile(os.path.join(folder, 'IMG_0001.jpg')) and res['copied'] == 1,
        'a sync copies the photo into its folder ({})'.format(res))
H.check(wj.mirror_photos()['copied'] == 0, 'a second pass copies nothing')
# phones name many photos alike: another "IMG_0001.jpg" is another photo
jpg2 = os.path.join(H.WORK, 'other', 'IMG_0001.jpg')
os.makedirs(os.path.dirname(jpg2))
im = QImage(120, 90, QImage.Format_RGB32)
im.fill(QColor('#aa3366'))
im.save(jpg2, 'JPG')
c = dbm.get_connection()
c.execute("INSERT INTO work_log_images (id, work_log_id, file_path, thumbnail_path, "
          "filename, size_bytes, upload_status) VALUES ('img2abcdef', ?, ?, ?, 'IMG_0001.jpg', ?, "
          "'uploaded')", (key[2:], jpg2, jpg2, os.path.getsize(jpg2)))
c.commit(); c.close()
res = wj.mirror_photos()
H.check(res['copied'] == 1 and os.path.isfile(os.path.join(folder, 'IMG_0001_img2abcd.jpg')),
        'a same-named photo is kept under its own name ({})'.format(sorted(os.listdir(folder))))
H.check(wj.mirror_photos()['copied'] == 0, 'and is not copied again')
wj.save(PID, key, date=row['date'], kind=row['kind'], block=12, lc='LC1', device='PCS 2',
        title='PCS comm board replaced', work_done=row['work_done'], status='Done')
wj.mirror_photos()
renamed = os.path.join(wj.photos_root(), 'TK',
                       '2026-09-14 Block 12 LC1 PCS 2 - PCS comm board replaced')
H.check(os.path.isfile(os.path.join(renamed, 'IMG_0001.jpg')) and not os.path.exists(folder),
        'an edited record\'s folder is renamed, not duplicated')

page.refresh()
page._select_key(key)
texts = [w.text() for w in page.card.findChildren(QLabel)]
H.check(any('Photos · 2' in t for t in texts), 'the card shows the record\'s photos')

H.finish()
