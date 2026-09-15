"""One PM record per (project, plant block, date), whoever writes it.

QA C-1 / DATA-PM-08: block 41 on 16.09 — a planner job closed at 4 h, a phone PM
event at 3 h and a PM-tab row at 4 h used to make three rows, 11 h x 4 PCS =
44 unit-h for one PM. Here every writer goes through record_pm and the report
side charges a block-day once. Also pinned: PM validation (a block — the whole
plant only as 'all' —, 0 < h <= 24, sane dates), per-block split, idempotent
re-application of the same event or job, the PM tab's dialog refusing an empty
block, and the month view's sources and duplicate flags.
"""
import _harness as H            # must be first
import datetime
import json
import os

import database.db_manager as dbm
import services.report_workflow_service as rw
import services.availability_service as av
import services.availability_inputs_service as avi
import services.planner_service as pl

H.fresh_db('pm_single.db')
c = dbm.get_connection()
c.execute("INSERT INTO projects (name, num_zones, num_blocks, num_containers) VALUES ('TK', 9, 70, 1)")
PID = c.execute("SELECT id FROM projects").fetchone()[0]
c.commit(); c.close()
TODAY = datetime.date(2026, 9, 20)       # fixed, so the date rules do not drift


def rows(y=2026, m=9):
    return rw.get_pm_activities(PID, y, m)


def charged(block, date, y=2026, m=9):
    return [r for r in rw.pm_as_unavailability(PID, y, m)
            if r['block'] == block and r['date_from'] == date]


def inputs_json(y=2026, m=9):
    return json.dumps(rw.report_inputs(PID, y, m), sort_keys=True, default=str)


print('=== block 41, 16.09: planner 4 h, phone 3 h, PM tab 4 h, Excel import ===')
job = pl.add_item(PID, type_code='pm', title='PM — block 41', block=41,
                  planned_date='2026-09-16', planned_hours=4)
out = pl.complete_item(job, actual_date='2026-09-16', actual_hours=4)
H.check(out['wrote_downtime'] and len(rows()) == 1, 'planner completion writes 1 record')

ev = {'id': 'ev-41-a', 'project_id': PID, 'kind': 'pm', 'blocks': '41',
      'date_from': '2026-09-16', 'date_to': '2026-09-16', 'hours': 3,
      'exclusion_type': '', 'description': 'PM as per the checklist'}
H.check(avi.route_field_event(ev) == 'queued', 'phone PM on the same block-day waits (duplicate)')
H.check(avi.route_field_event(ev) == 'seen', 're-pulling the same event changes nothing')
try:
    rw.record_pm(PID, '41', '2026-09-16', None, 4, 'tab row', source='desktop', today=TODAY)
    H.check(False, 'PM tab row on the same block-day must be refused')
except rw.PMDuplicateError as e:
    H.check(e.duplicates[0]['existing']['id'] == out['pm_activity_id'],
            'PM tab row refused, the question names the existing record: ' + str(e))

xl = os.path.join(H.WORK, 'plan.xlsx')
import pandas as pd
pd.DataFrame([{'Block': 41, 'Date': '2026-09-16', 'Actual date': '2026-09-16', 'Actual h': 4.5,
               'Title': 'PM 41 (file)'}]).to_excel(xl, index=False)
res = pl.import_from_excel(PID, xl, {'Block': 'block', 'Date': 'planned_date',
                                     'Actual date': 'actual_date', 'Actual h': 'actual_hours',
                                     'Title': 'title'}, default_type='pm')
H.check(res['completed'] == 1 and len(res['notes']) == 1 and not res['problems'],
        'Excel completion links to the existing record and says so: {}'.format(res['notes']))

H.check(len(rows()) == 1 and len(charged(41, '2026-09-16')) == 1
        and charged(41, '2026-09-16')[0]['downtime_h'] == 4.0,
        'result: 1 record, 4 h charged once (was 3 rows, 11 h)')
H.check(len(avi.pending_events(PID, 2026, 9)) == 1, 'and 1 phone question waiting on the desktop')
manual = rw.report_inputs(PID, 2026, 9)['manual_unavailability']
H.check(sum(1 for r in manual if r['block'] == 41) == 1, 'report_inputs: one downtime row for block 41')

print('\n=== the technician corrects 3 -> 4 h with a second event ===')
ev_b = dict(ev, id='ev-41-b', hours=4.5)
H.check(avi.route_field_event(ev_b) == 'queued', 'the correction waits too, no second row')
res = avi.apply_pm_event('ev-41-b', '41', '2026-09-16', '2026-09-16', 4.5, 'corrected',
                         on_duplicate='update')
H.check(len(rows()) == 1 and rows()[0]['hours'] == 4.5,
        'desktop answers "use 4.5 h": still 1 record, now 4.5 h')
avi.reject_event('ev-41-a', 'superseded')
H.check(not avi.pending_events(PID, 2026, 9) and len(rows()) == 1, 'rejecting the stale one changes nothing')
H.check(avi.pending_events(PID, 2026, 9, status='rejected')[0]['note'] == 'superseded',
        'the rejection keeps its reason')

print('\n=== reopening a job linked to someone else\'s record keeps that record ===')
ev_c = {'id': 'ev-43', 'project_id': PID, 'kind': 'pm', 'blocks': '43',
        'date_from': '2026-09-10', 'date_to': '2026-09-10', 'hours': 3, 'description': 'PM'}
H.check(avi.route_field_event(ev_c) == 'applied', 'phone PM on a free block-day applies at once')
job43 = pl.add_item(PID, type_code='pm', title='PM 43', block=43, planned_date='2026-09-10',
                    planned_hours=4)
try:
    pl.complete_item(job43, actual_date='2026-09-10', actual_hours=4)
    H.check(False, 'closing the job must ask first')
except rw.PMDuplicateError:
    H.check(True, 'closing the job on that block-day asks first')
o43 = pl.complete_item(job43, actual_date='2026-09-10', actual_hours=4, on_duplicate='link')
rows_for = [r for r in rows() if r['affected_blocks'] == '43']
H.check(bool(o43['linked_existing']), 'linked to the phone record')
H.check(len(rows_for) == 1 and rows_for[0]['hours'] == 3.0, 'hours stay 3 h (the phone\'s)')
pl.reopen_item(job43)
H.check(len([r for r in rows() if r['affected_blocks'] == '43']) == 1,
        'reopening the job does not delete the phone record')
job44 = pl.add_item(PID, type_code='pm', title='PM 44', block=44, planned_date='2026-09-10',
                    planned_hours=4)
pl.complete_item(job44, actual_date='2026-09-10', actual_hours=4)
pl.complete_item(job44, actual_date='2026-09-10', actual_hours=3.5)
H.check([r['hours'] for r in rows() if r['affected_blocks'] == '44'] == [3.5],
        're-completing its own job updates its record (no second row)')
pl.reopen_item(job44)
H.check(not [r for r in rows() if r['affected_blocks'] == '44'], 'reopening its own job removes it')
nob = pl.add_item(PID, type_code='pm', title='PM somewhere', planned_date='2026-09-11', planned_hours=3)
try:
    pl.complete_item(nob, actual_date='2026-09-11', actual_hours=3)
    H.check(False, 'a PM job without a block must not be closed')
except rw.PMValidationError as e:
    H.check(pl.get_item(nob)['status'] == 'planned', 'PM job without a block is refused: ' + str(e))

print('\n=== validation — the same rules for every writer ===')
bad = [('', 3, '2026-09-11', None, 'empty block'),
       ('abc', 3, '2026-09-11', None, 'junk block text'),
       ('0', 3, '2026-09-11', None, 'block 0'),
       ('71', 3, '2026-09-11', None, 'block beyond the plant'),
       ('45', 0, '2026-09-11', None, '0 h'),
       ('45', 24.5, '2026-09-11', None, '24.5 h'),
       ('45', 3, '2027-05-20', None, 'the 2027-05-20 test entry'),
       ('45', 3, '2026-09-11', '2026-09-10', 'end before start'),
       ('45', 3, '18.09.2026', None, 'not an ISO date')]
for blocks, h, d1, d2, why in bad:
    try:
        rw.validate_pm(PID, blocks, d1, d2, h, today=TODAY)
        H.check(False, 'refused: ' + why)
    except rw.PMValidationError as e:
        H.check(True, 'refused: {} ({})'.format(why, e))
v = rw.validate_pm(PID, '45', '2026-09-11', None, 24, today=TODAY)
H.check(v['hours'] == 24 and v['blocks'] == [45], '24 h is allowed')
before = inputs_json()
for i, (blocks, hours) in enumerate([('', 3), ('abc', 3), ('45', 0)]):
    st = avi.route_field_event({'id': f'bad-{i}', 'project_id': PID, 'kind': 'pm', 'blocks': blocks,
                                'date_from': '2026-09-11', 'date_to': '2026-09-11', 'hours': hours})
    H.check(st == 'queued', f'phone PM blocks={blocks!r} h={hours} waits instead of applying')
H.check(inputs_json() == before, 'report_inputs unchanged by them (an empty block was all 70 blocks)')

print('\n=== multi-block, whole plant, hours not rounded ===')
r = rw.record_pm(PID, '50-52', '2026-09-12', None, 3.25, 'campaign', source='desktop', today=TODAY)
H.check(len(r['created']) == 3, 'blocks 50-52 split into 3 records')
H.check(rw.record_pm(PID, '50-52', '2026-09-12', None, 3.25, source='phone', source_ref='same',
                     on_duplicate='skip', today=TODAY)['skipped'], 'a second writer skips them')
lines = rw.pm_as_strings(PID, 2026, 9)
H.check(any('3.25 h' in ln for ln in lines) and not any('(3 h' in ln for ln in lines if '50' in ln),
        '3.1 prints 3.25 h, not 3 h: {}'.format([ln for ln in lines if '3.25' in ln][:1]))
c = dbm.get_connection()
c.execute("INSERT INTO projects (name, num_zones, num_blocks, num_containers) VALUES ('small', 1, 5, 1)")
SMALL = c.execute("SELECT id FROM projects WHERE name='small'").fetchone()[0]
c.commit(); c.close()
r = rw.record_pm(SMALL, 'all', '2026-09-12', None, 2, source='desktop', today=TODAY)
H.check(len(r['created']) == 5, "'all' is the whole plant — 5 records on a 5-block plant")
twice = [rw.record_pm(PID, '60', '2026-09-12', None, h, source='phone', source_ref='ev-60',
                      today=TODAY) for h in (2, 2.5)]
H.check(len([x for x in rows() if x['affected_blocks'] == '60']) == 1
        and twice[1]['updated'], 'the same event applied twice updates its own record')

print('\n=== rows that already exist: legacy duplicates and empty blocks ===')
c = dbm.get_connection()
c.execute("INSERT INTO pm_activities (project_id, year, month, affected_blocks, date_from, date_to, hours, description) "
          "VALUES (?, 2026, 10, '61', '2026-10-02', '2026-10-02', 4, 'first')", (PID,))
c.execute("INSERT INTO pm_activities (project_id, year, month, affected_blocks, date_from, date_to, hours, description) "
          "VALUES (?, 2026, 10, '61,62', '2026-10-02', '2026-10-02', 3, 'second')", (PID,))
c.execute("INSERT INTO pm_activities (project_id, year, month, affected_blocks, date_from, date_to, hours, description) "
          "VALUES (?, 2026, 10, '', '2026-10-03', '2026-10-03', 3, 'no block')", (PID,))
c.commit(); c.close()
oct_rows = rw.pm_as_unavailability(PID, 2026, 10)
H.check(sorted((x['block'], x['downtime_h']) for x in oct_rows) == [(61, 4.0), (62, 3.0)],
        'block 61 charged once (first record), 62 from the second, empty block not at all: {}'.format(
            [(x['block'], x['downtime_h']) for x in oct_rows]))
view = avi.month_inputs(PID, 2026, 10)
flags = {x['description']: x['flags'] for x in view['pm']}
H.check(any('duplicate of' in f for f in flags['second']) and any('no block' in f for f in flags['no block'])
        and not flags['first'], 'the month view flags them: {}'.format(flags))

print('\n=== month view: sources and PM vs manual downtime on one block-day ===')
av.add_manual_unavailability(block=41, date_from='2026-09-16', date_to='2026-09-16', downtime_h=4,
                             cause='Preventive maintenance', project_id=PID, source='desktop')
view = avi.month_inputs(PID, 2026, 9)
src = {x['affected_blocks']: x['source_key'] for x in view['pm']}
H.check(src.get('41') == 'planner' and src.get('43') == 'phone' and src.get('50') == 'desktop',
        'source shown per record: {}'.format({k: src[k] for k in ('41', '43', '50') if k in src}))
m41 = [x for x in view['manual'] if x['block'] == 41][0]
p41 = [x for x in view['pm'] if x['affected_blocks'] == '41'][0]
H.check(m41['flags'] and p41['flags'], 'a manual PM row on a PM record\'s block-day is flagged on both')
c = dbm.get_connection()
c.execute("INSERT INTO synced_field_events (event_id, kind, applied_at) VALUES ('old', 'pm', '2026-09-09 09:03:03')")
c.execute("INSERT INTO pm_activities (project_id, year, month, affected_blocks, date_from, date_to, hours, description, created_at) "
          "VALUES (?, 2026, 9, '39', '2026-09-07', '2026-09-07', 6, 'legacy phone', '2026-09-09 09:03:03')", (PID,))
c.commit(); c.close()
legacy = [x for x in avi.month_inputs(PID, 2026, 9)['pm'] if x['affected_blocks'] == '39'][0]
H.check(legacy['source_key'] == 'phone', 'a record from before `source` existed is shown as the phone\'s')

print('\n=== the PM tab dialog refuses an empty block ===')
from PyQt5.QtWidgets import QApplication, QDialog, QMessageBox
app = QApplication.instance() or QApplication([])
from ui.monthly_reports_page import PMDialog
dlg = PMDialog(project_id=PID)
dlg.hours.setValue(3)
dlg.accept()
H.check(dlg.result() != QDialog.Accepted and dlg.err.isVisibleTo(dlg) and 'block' in dlg.err.text(),
        'no block: the dialog stays open and says why ({})'.format(dlg.err.text()[:60]))
dlg2 = PMDialog(project_id=PID)
dlg2.blocks.setText('45'); dlg2.hours.setValue(0)
dlg2.accept()
H.check(dlg2.result() != QDialog.Accepted, '0 h: the dialog stays open')

H.finish()
