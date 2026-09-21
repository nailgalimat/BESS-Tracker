"""A job the office gives to a named technician.

Covered here (the desktop half — the server and the phone have their own
tests):
  * the assignment is stored on the record itself, with the technician's name
    cached so an offline list still shows a person;
  * the Work page: the "Assigned to" field on the card, the filter chip and
    the column, so the office sees at a glance what is with whom;
  * a PM campaign published from the Plan page becomes one work record per
    block-job — open, category maintenance, the campaign name in the text —
    and publishing twice does not double them;
  * a published campaign job does NOT reach the customer's section 3.2, before
    or after the technician closes it: PM is reported once, as PM hours.
"""
import _harness as H            # must be first

import database.db_manager as dbm
import services.planner_service as pl
import services.report_workflow_service as rw
import services.team_service as team
import services.work_journal_service as wj

H.fresh_db('assignment.db')
c = dbm.get_connection()
c.execute("INSERT INTO projects (name, num_zones, num_blocks, num_containers, "
          "project_type) VALUES ('TK', 2, 16, 4, 'BESS')")
PID = c.execute("SELECT id FROM projects").fetchone()[0]
for z in (1, 2):
    for b in range(1, 9):
        c.execute("INSERT INTO containers (project_id, zone_number, block_number, "
                  "container_index, container_type, serial_number) "
                  "VALUES (?,?,?,1,'LC Cabinet',?)", (PID, z, b, 'SN%d%d' % (z, b)))
c.commit(); c.close()
import services.project_service as psvc
psvc.get_block_map(PID, refresh=True)

# ── the team, as the server publishes it ────────────────────────────────
n = team.save_users([
    {'id': 'u-tech1', 'username': 'tech1', 'role': 'technician'},
    {'id': 'u-tech2', 'username': 'tech2', 'role': 'technician'},
    {'id': 'u-office', 'username': 'office', 'role': 'admin'},
])
H.check(n == 3 and [u['username'] for u in team.users()][:2] == ['tech1', 'tech2'],
        'the server accounts are cached for the offline picker: {}'.format(
            [u['username'] for u in team.users()]))
team.save_users([{'id': 'u-tech1', 'username': 'tech1', 'role': 'technician'},
                 {'id': 'u-office', 'username': 'office', 'role': 'admin'}])
H.check([u['id'] for u in team.users()] == ['u-tech1', 'u-office']
        and team.name_for('u-tech2') == 'tech2',
        'an account that leaves the server is hidden but still names old records')
team.save_users([{'id': 'u-tech1', 'username': 'tech1', 'role': 'technician'},
                 {'id': 'u-tech2', 'username': 'tech2', 'role': 'technician'},
                 {'id': 'u-office', 'username': 'office', 'role': 'admin'}])

# ── assigning one record ────────────────────────────────────────────────
key = wj.save(PID, None, date='2026-09-21', kind=wj.KIND_FAULT, block=12,
              lc='LC1', device='PCS 2', title='BSC-PCS comm fault',
              work_done='Check the communication board', status='Open',
              assignee='u-tech1', assignee_name='tech1', assigned_by='office',
              due='2026-09-21')
row = [r for r in wj.records(PID) if r['key'] == key][0]
H.check(row['assignee'] == 'u-tech1' and row['assignee_name'] == 'tech1'
        and row['assigned_by'] == 'office' and row['due'] == '2026-09-21',
        'the record carries who it is for, who gave it and when it is due')

conn = dbm.get_connection()
st = conn.execute("SELECT sync_status, assigned_to FROM work_log_entries WHERE id=?",
                  (key[2:],)).fetchone()
conn.close()
H.check(st['sync_status'] == 'local' and st['assigned_to'] == 'u-tech1',
        'and it is queued for the phones ({})'.format(st['sync_status']))

# an edit that says nothing about the assignment must not drop it — the
# technician would silently lose the job
wj.save(PID, key, date=row['date'], kind=row['kind'], block=12, lc='LC1',
        device='PCS 2', title=row['title'], work_done='Board reseated',
        status='In progress')
row = [r for r in wj.records(PID) if r['key'] == key][0]
H.check(row['assignee'] == 'u-tech1' and row['work_done'] == 'Board reseated',
        'an edit that does not mention the assignment keeps it')

other = wj.save(PID, None, date='2026-09-21', kind=wj.KIND_FAULT, block=3,
                title='Fan noise', work_done='Look at the fan', status='Open',
                assignee='u-tech2', assignee_name='tech2', assigned_by='office')
loose = wj.save(PID, None, date='2026-09-20', kind=wj.KIND_FAULT, block=4,
                title='Door seal', work_done='', status='Open')
rows = wj.records(PID)
H.check(len(wj.filter_rows(rows, assignee='u-tech1')) == 1
        and len(wj.filter_rows(rows, assignee='u-tech2')) == 1
        and len(wj.filter_rows(rows, assignee=wj.ASSIGNED_NOBODY)) == 1
        and len(wj.filter_rows(rows, assignee=None)) == 3,
        'the chip filters by person, and "nobody yet" is a real question')

# ── a PM campaign, planned and sent to a phone ──────────────────────────
res = pl.generate_pm_campaign(PID, 'PM round — September 2026', [5, 6, 7],
                              '2026-09-22', blocks_per_day=1,
                              hours_per_block=4.0, assignee='tech1',
                              assignee_id='u-tech1')
H.check(res['items'] == 3 and len(res['item_ids']) == 3,
        'the campaign generates one job per block: {}'.format(res['items']))

pub = pl.publish_jobs(PID, res['item_ids'], assignee_id='u-tech1',
                      assignee_name='tech1', assigned_by='office')
H.check(pub['published'] == 3, 'every job becomes a work record: {}'.format(pub))
jobs = [r for r in wj.records(PID) if r['key'] in pub['keys']]
H.check(len(jobs) == 3 and all(j['assignee'] == 'u-tech1' for j in jobs),
        'all three are assigned to the technician')
H.check(all(j['status'] == 'Open' and j['category'] == 'maintenance'
            for j in jobs),
        'open, and category maintenance: {}'.format(
            sorted({(j['status'], j['category']) for j in jobs})))
H.check(all('PM round — September 2026' in j['work_done'] for j in jobs)
        and {j['block'] for j in jobs} == {5, 6, 7},
        'the campaign name is in the text and the block is the plant block')
H.check(all(j['due'] for j in jobs),
        'each job says when it is wanted: {}'.format(sorted(j['due'] for j in jobs)))

again = pl.publish_jobs(PID, res['item_ids'], assignee_id='u-tech2',
                        assignee_name='tech2', assigned_by='office')
H.check(again['published'] == 0 and again['reassigned'] == 3,
        'sending the same campaign again reassigns, never duplicates: {}'.format(again))
H.check(len([r for r in wj.records(PID) if 'PM round' in r['work_done']]) == 3,
        'still three records in the journal')
H.check(all(r['assignee'] == 'u-tech2'
            for r in wj.records(PID) if 'PM round' in r['work_done']),
        'and they are with tech2 now')
item = pl.get_item(res['item_ids'][0])
H.check(item['record_uuid'] == pub['keys'][0][2:] and item['assignee_id'] == 'u-tech2',
        'the plan item remembers the record it was published as')

# ── the customer's report: a published PM job counts once, as PM hours ──
cm = {r['id']: r for r in rw.corrective_rows(PID, 2026, 9)}
published = [cm[k[2:]] for k in pub['keys'] if k[2:] in cm]
H.check(len(published) == 3
        and all(rw.cm_skip_reason(r) in ('pm', 'open') for r in published),
        'a published job is held out of section 3.2: {}'.format(
            sorted({rw.cm_skip_reason(r) for r in published})))
# and once the technician has closed it, PM is still the reason
conn = dbm.get_connection()
conn.execute("UPDATE work_log_entries SET status='done', "
             "description='PM: done, all checks passed' WHERE id=?",
             (pub['keys'][0][2:],))
conn.commit(); conn.close()
done_row = {r['id']: r for r in rw.corrective_rows(PID, 2026, 9)}[pub['keys'][0][2:]]
H.check(rw.cm_skip_reason(done_row) == 'pm',
        'and still after it is finished — PM is reported as hours, not twice '
        '({})'.format(rw.cm_skip_reason(done_row)))
lines = rw.cm_lines(rw.corrective_rows(PID, 2026, 9))
H.check(not any('PM round' in ln for ln in lines),
        'no 3.2 line carries a campaign job: {}'.format(lines))

# ── the Work page ───────────────────────────────────────────────────────
from PyQt5.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
import ui.work_page as wp

page = wp.WorkPage()
page.set_month(2026, 9)
page.set_current_project(PID, 'TK')
H.check('Assigned to' in wp.COLUMNS
        and page.table.columnCount() == len(wp.COLUMNS),
        'the list has an "Assigned to" column')
page.apply_filter({'tab': 'all'})
col = wp.COLUMNS.index('Assigned to')
names = {page.table.item(i, col).text() for i in range(page.table.rowCount())}
H.check('tech2' in names and '' in names,
        'it shows who each record is with: {}'.format(sorted(names)))

page.assignee_cb.setCurrentIndex(page.assignee_cb.findData('u-tech2'))
H.check(page.table.rowCount() == 4,
        'the chip narrows the list to that person: {} row(s)'.format(
            page.table.rowCount()))
page.assignee_cb.setCurrentIndex(page.assignee_cb.findData(wj.ASSIGNED_NOBODY))
H.check(page.table.rowCount() == 1, 'and "Nobody yet" to the unassigned one')
page.assignee_cb.setCurrentIndex(0)

page._select_key(loose)
H.check('assignee' in page._fields, 'the card has an "Assigned to" field')
page._fields['assignee'].setCurrentIndex(
    page._fields['assignee'].findData('u-tech1'))
page._save_card()
after = [r for r in wj.records(PID) if r['key'] == loose][0]
H.check(after['assignee'] == 'u-tech1' and after['assignee_name'] == 'tech1',
        'the card hands the job over ({} / {})'.format(after['assignee'],
                                                       after['assignee_name']))
H.check(after['due'] == after['date'],
        'an assigned record is a work order: its date is the due date ({})'
        .format(after['due']))
conn = dbm.get_connection()
st = conn.execute("SELECT sync_status FROM work_log_entries WHERE id=?",
                  (loose[2:],)).fetchone()[0]
conn.close()
H.check(st in ('local', 'pending'), 'and the change is queued for the phone')

page._select_key(loose)
page._fields['assignee'].setCurrentIndex(0)          # — nobody —
page._save_card()
back = [r for r in wj.records(PID) if r['key'] == loose][0]
H.check(back['assignee'] == '' and back['due'] == '',
        'taking it back leaves nobody holding it')

# ── the Plan page sends a campaign to a phone ───────────────────────────
from PyQt5.QtWidgets import QMessageBox, QDialog
import ui.planner_page as pp

QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
plan = pp.PlannerPage()
plan.set_current_project(PID)
plan.month.setCurrentIndex(8)                        # September
plan.year.setValue(2026)
plan._reload()
H.check(plan.tbl.rowCount() >= 3, 'the schedule lists the campaign jobs: {}'
        .format(plan.tbl.rowCount()))

new = pl.generate_pm_campaign(PID, 'PM round — October 2026', [9, 10],
                              '2026-09-28', blocks_per_day=2,
                              hours_per_block=4.0)
plan._reload()
sent = plan._publish([{'id': i} for i in new['item_ids']], 'u-tech1', 'tech1')
H.check(sent['published'] == 2,
        'the page publishes the selected jobs: {}'.format(sent))
oct_rows = [r for r in wj.records(PID) if 'PM round — October 2026' in r['work_done']]
H.check(len(oct_rows) == 2 and all(r['assignee'] == 'u-tech1' for r in oct_rows),
        'and they are on tech1\'s list')
plan._reload()
marks = [plan.tbl.item(i, 4).text() for i in range(plan.tbl.rowCount())]
H.check(sum(1 for m in marks if m.startswith('📱')) == 5,
        'the schedule marks what has been sent: {}'.format(marks))

H.finish()
