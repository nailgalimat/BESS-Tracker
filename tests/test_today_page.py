"""Today: the seven blocks, and the rule that makes them trustworthy — every
count is the length of the very list its link opens. The old Overview said
"Open issues 0" while a dozen open phone records sat in the database; this test
puts records, a phone event, a low stock item and a PM plan in and checks each
block counts them.
"""
import datetime
import _harness as H            # must be first
import database.db_manager as dbm
import services.today_service as ts
import services.work_journal_service as wj
import services.availability_inputs_service as avi
import services.stock_service as ss

TODAY = datetime.date(2026, 9, 15)
H.fresh_db('today.db')
c = dbm.get_connection()
c.execute("INSERT INTO projects (name, num_zones, num_blocks, num_containers, project_type) "
          "VALUES ('TK', 9, 70, 4, 'BESS')")
PID = c.execute("SELECT id FROM projects").fetchone()[0]
# 70 plant blocks over 9 zones, so the plant<->zone block map is the real one
plant = 0
for z in range(1, 10):
    for b in range(1, 9):
        plant += 1
        if plant > 70:
            break
        c.execute("INSERT INTO containers (project_id, zone_number, block_number, "
                  "container_index, container_type, serial_number) "
                  "VALUES (?,?,?,1,'LC Cabinet',?)", (PID, z, b, 'SN%d' % plant))
c.commit(); c.close()

import services.project_service as psvc
psvc.get_block_map(PID, refresh=True)


def entry(eid, date, block, fault, status, category='fault', **kw):
    conn = dbm.get_connection()
    cols = dict(id=eid, project_id=PID, log_date=date, plant_block=block,
                fault_name=fault, status=status, category=category,
                description=kw.pop('description', 'work done'),
                sync_status=kw.pop('sync_status', 'synced'),
                site_location='', created_at=date + ' 08:00:00',
                updated_at=date + ' 08:00:00')
    cols.update(kw)
    conn.execute("INSERT INTO work_log_entries ({}) VALUES ({})".format(
        ','.join(cols), ','.join('?' * len(cols))), list(cols.values()))
    conn.commit(); conn.close()


# 3 open faults (one of them 8 days old), 1 done, 1 conflict, 1 without a block
entry('f1', '2026-09-14', 32, 'BSC-PCS comm fault', 'open')
entry('f2', '2026-09-13', 33, 'LCU alarm', 'open')
entry('f3', '2026-09-07', 16, 'BSP AC SPD abnormal', 'open')
entry('f4', '2026-09-13', 67, 'Antifreeze Low Level', 'done')
entry('f5', '2026-09-10', 33, 'LCU VFD1 board fault', 'done', sync_status='conflict')
entry('p1', '2026-09-10', None, '', 'done', category='maintenance',
      description='PM activity for 3 hours per checklist')

rows = wj.records(PID, today=TODAY)
H.check(len(rows) == 6, 'six records in the journal, phone and desktop in one list')
H.check(len(wj.open_faults(PID, today=TODAY)) == 3,
        'three open faults (the done one and the PM note are not faults)')
H.check(len([r for r in rows if wj.has_no_block(r)]) == 1, 'one record without a plant block')
H.check([r['zone_label'] for r in rows if r['block'] == 32] == ['Z4/B8'],
        'plant block 32 shows its zone/block: {}'.format(
            [r['zone_label'] for r in rows if r['block'] == 32]))

# a phone event waiting, and a low stock item
avi.route_field_event({'id': 'u-grid', 'project_id': PID, 'kind': 'excluded',
                       'blocks': '63-70', 'date_from': '2026-09-14',
                       'date_to': '2026-09-14', 'hours': 2,
                       'exclusion_type': 'Grid Outage', 'description': 'islanding'})
wid = ss.ensure_project_warehouse(PID, 'TK')
ss.set_stock_level(wid, 'ANTIFREEZE-20L', quantity=1, min_quantity=4)

data = ts.summary(PID, 2026, 9, today=TODAY)

# ── the rule: count == rows behind it ───────────────────────────────────
H.check(len(data['faults']) == 3,
        'Open faults counts 3 and open_faults() returns 3')
H.check(len(data['stale']) == 1,
        'one fault older than 7 days is called out: {} d'.format(
            [r['age_days'] for r in data['stale']]))
kinds = [d['kind'] for d in data['decisions']]
H.check('event' in kinds and 'conflict' in kinds,
        'Needs your decision holds the phone event and the sync conflict: {}'.format(kinds))
H.check(len(data['stock']) == 1 and data['stock'][0]['material_number'] == 'ANTIFREEZE-20L',
        'Stock below minimum counts the one item of this project')
H.check(data['phones']['conflicts'] == 1, 'Phones block sees the conflict')
H.check(any(r['kind'] == 'event' for r in data['now']),
        'Happening now shows the open downtime event')

# the filtered list a count opens must hold exactly that many rows
opened = wj.filter_rows(wj.records(PID, today=TODAY), tab='open', kind='Fault')
H.check(len(opened) == len(data['faults']),
        'the "open faults" link opens {} rows for a count of {}'.format(
            len(opened), len(data['faults'])))
conf = wj.filter_rows(wj.records(PID, today=TODAY), tab='conflict')
H.check(len(conf) == data['phones']['conflicts'],
        'the conflict link opens {} row(s) for a count of {}'.format(
            len(conf), data['phones']['conflicts']))

# ── the page itself ─────────────────────────────────────────────────────
from PyQt5.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
import ui.today_page as tp

page = tp.TodayPage()
page.set_current_project(PID, 'TK')
page.set_month(2026, 9)
H.check(page.p_faults.count_lbl.text() == '3',
        'the page prints 3 on Open faults, not 0 like the old Overview')
H.check(page.p_stock.count_lbl.text() == '1', 'and 1 on Stock below minimum')
H.check(int(page.p_decisions.count_lbl.text() or 0) == len(data['decisions']),
        'Needs your decision prints {} for {} rows'.format(
            page.p_decisions.count_lbl.text(), len(data['decisions'])))

asked = []
page.open_filtered.connect(lambda label, flt: asked.append((label, dict(flt))))
page.p_faults.link_btn.click()
H.check(asked and asked[0][0] == 'Work' and asked[0][1].get('tab') == 'open',
        'the Open faults link asks the shell for Work filtered to open: {}'.format(asked))

# ── the lists those links open, on the real Work page ───────────────────
H.check(not any(d['title'] == 'File missing' for d in data['decisions']),
        'a file not uploaded yet mid-month is progress, not a decision')
nb = [d for d in data['decisions'] if d['kind'] == 'record']
H.check(len(nb) == 1 and nb[0]['filter'] == {'tab': 'noblock', 'period': 'month'},
        'the records without a block are one decision row: {}'.format(
            [d['title'] for d in nb]))

import ui.work_page as wp
work = wp.WorkPage()
work.set_month(2026, 9)
work.set_current_project(PID, 'TK')
work.search_edit.setText('something left over')      # a stale chip
work.apply_filter(nb[0]['filter'])
H.check(work.table.rowCount() == int(nb[0]['title'].split()[0]),
        'the no-block row says {} and Work shows {} row(s)'.format(
            nb[0]['title'].split()[0], work.table.rowCount()))
work.apply_filter(asked[0][1])
H.check(work.table.rowCount() == len(data['faults']),
        'Open faults says {} and Work shows {} row(s)'.format(
            len(data['faults']), work.table.rowCount()))
conf = [d for d in data['decisions'] if d['kind'] == 'conflict'][0]
work.apply_filter(conf['filter'])
H.check(work.table.rowCount() == data['phones']['conflicts'],
        'the conflict row opens exactly the conflicting record')

H.finish()
