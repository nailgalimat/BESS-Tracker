"""The check before generating a monthly report: every question fires on a
month built to raise it, each names where it is fixed, and running the check
changes no input (QA S1 gate G6, WF-WEEK-05/08).

Synthetic data, August 2026 of a fresh 70-block Tashkent project: a grid-outage
window entered shorter than the SCADA stop on both sides, a duplicate PM, a PM
without a block, 30 PM hours, a manual row on a block the plant does not have,
a phone event still waiting, three 3.2 records the report holds back, two days
of working status only, and no month-end files.
"""
import _harness as H            # must be first: isolates DB, sync, network, history
import hashlib
import os
import warnings
warnings.filterwarnings('ignore')

import pandas as pd

import database.db_manager as dbm
H.fresh_db('precheck.db')
import _synthetic_scada as S
import services.report_workflow_service as rw
import services.availability_service as av
import services.availability_inputs_service as avi
import services.month_dataset_service as mds
import services.report_precheck_service as rpc

c = dbm.get_connection()
c.execute("INSERT INTO projects (name, num_zones, num_blocks, num_containers) VALUES ('TK', 1, 70, 1)")
PID = c.execute("SELECT id FROM projects WHERE name='TK'").fetchone()[0]
c.commit(); c.close()
rw.save_project_config(PID, site_type='tashkent')
Y, M = 2026, 8

# ── the month ────────────────────────────────────────────────────────────────
fault = (pd.Timestamp('2026-08-10 22:25'), pd.Timestamp('2026-08-11 03:55'))
ws_path = S.wide(os.path.join(H.WORK, 'LC working status.xlsx'),
                 S.stamps('2026-08-10 00:00', '2026-08-11 23:55'),
                 S.lc_columns('SYSTEM WORKING STATUS', blocks=(5, 6)),
                 lambda t, col: 'FAULT' if ('LC200 05.' in col and fault[0] <= t <= fault[1])
                 else 'RUNNING')
mds.add_files(PID, Y, M, [ws_path])
mds.analyse_pending(PID, Y, M)
WIN = av.add_exclusion('Grid Outage', '2026-08-10', '2026-08-11', '23:00', '03:00',
                       affected_blocks='5', description='utility outage', project_id=PID,
                       year=Y, month=M)

rw.record_pm(PID, '7', '2026-08-12', None, 4, 'PM as per checklist')
c = dbm.get_connection()
for blocks, date, hours in (('7', '2026-08-12', 3), ('', '2026-08-13', 4), ('8', '2026-08-14', 30)):
    # records written before the single PM writer existed (legacy duplicates)
    c.execute("INSERT INTO pm_activities (project_id, year, month, affected_blocks, date_from, "
              "date_to, hours, description) VALUES (?,?,?,?,?,?,?,?)",
              (PID, Y, M, blocks, date, date, hours, 'legacy'))
for eid, status, sync, text in (('wle-noblock', 'done', 'synced', 'Antifreeze low level'),
                                ('wle-conflict', 'done', 'conflict', 'LCU VFD1 board replaced'),
                                ('wle-open', 'open', 'synced', 'Q1 breaker to inspect')):
    c.execute("INSERT INTO work_log_entries (id, project_id, category, description, log_date, "
              "sync_status, fault_name, status) VALUES (?,?,?,?,?,?,?,?)",
              (eid, PID, 'repair', text, '2026-08-20', sync, text, status))
c.commit(); c.close()
av.add_manual_unavailability(block=99, date_from='2026-08-15', date_to='2026-08-15',
                             downtime_h=2.0, cause='held in standby', project_id=PID)
H.check(avi.route_field_event({'id': 'ev-counts-1', 'kind': 'counts', 'project_id': PID,
                               'date_from': '2026-08-16', 'blocks': '3', 'hours': 2,
                               'description': 'block 3 tripped'}) == 'queued',
        'a phone downtime event waits for the desktop')

TABLES = ('pm_activities', 'manual_unavailability', 'availability_exclusions', 'balancing_periods',
          'field_event_queue', 'synced_field_events', 'report_months', 'month_files',
          'work_log_entries', 'report_versions')


def inputs_digest():
    h = hashlib.sha256()
    conn = dbm.get_connection()
    try:
        for t in TABLES:
            rows = conn.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall()
            h.update(repr((t, [tuple(r) for r in rows])).encode('utf-8'))
    finally:
        conn.close()
    return h.hexdigest()


before = inputs_digest()
qs = rpc.run_precheck(PID, Y, M)
by = {}
for q in qs:
    by.setdefault(q['kind'], []).append(q)
    print('   {:<5} {:<10} {:<34} {}'.format(q['severity'], q['kind'], q['title'], q['detail'][:90]))

print('\n=== every question fires ===')
H.check(len(by.get('missing', [])) == 7, 'missing files: {} (7 required besides working status)'.format(
    [q['detail'] for q in by.get('missing', [])]))
H.check(len(by.get('pending', [])) == 1 and by['pending'][0]['ref'] == 'ev-counts-1',
        'the waiting phone event')
edges = by.get('edge', [])
H.check({q['explain'].split(',')[0] for q in edges} ==
        {'SCADA: already down from 10.08 22:25', 'SCADA: still down until 11.08 04:00'},
        'window edges, before and after: {}'.format([q['explain'] for q in edges]))
H.check(all(q['ref'] == WIN and q['target'] == 'inputs.exclusions' for q in edges),
        'both point at exclusion #{} on the Availability inputs tab'.format(WIN))
pm = by.get('pm', [])
texts = ' | '.join(q['detail'] for q in pm)
H.check(len(pm) == 3 and 'duplicate of' in texts and 'no block' in texts and 'more than 24' in texts,
        'PM: duplicate, no block, 30 h ({} questions)'.format(len(pm)))
H.check(len(by.get('manual', [])) == 1 and 'not a block of this plant' in by['manual'][0]['detail'],
        'manual row on block 99')
cm = {q['title']: q for q in by.get('cm', [])}
H.check(set(cm) == {'3.2 record without a plant block', '3.2 record in sync conflict',
                    '3.2 record still open'}, '3.2: no block, conflict, open')
gap = by.get('gap', [])
H.check(len(gap) == 1 and 'no data 01–09.08, 12–31.08' in gap[0]['detail'],
        'data gap on LC working status: {}'.format(gap[0]['detail'] if gap else '-'))
ends = [q['detail'] for q in by.get('month_end', [])]
H.check(len(ends) == 5 and any('Customer POI' in e for e in ends),
        'month-end files not added: {}'.format(ends))
H.check(qs[0]['severity'] == 'crit' and [q['severity'] for q in qs] ==
        sorted([q['severity'] for q in qs], key=rpc.SEVERITY_ORDER.get), 'most important first')
H.check(all(q['action'] and q['target'] for q in qs), 'every question has one action and a target')
H.check(len(rpc.needs_confirmation(qs)) == sum(q['severity'] != 'info' for q in qs),
        'generation must be confirmed over the crit/warn questions')

print('\n=== the check changes nothing ===')
rpc.run_precheck(PID, Y, M)
H.check(inputs_digest() == before, 'inputs, queue, data set and versions identical after two runs')

print('\n=== the Check step opens the place a question is fixed ===')
from PyQt5.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
import ui.monthly_reports_page as mrp
page = mrp.MonthlyReportsPage()
page.proj_combo.setCurrentIndex(page.proj_combo.findData(PID))
page.year_spin.setValue(Y); page.month_combo.setCurrentIndex(M - 1)
page._open_month()                  # opening a month creates its report_months row
before = inputs_digest()
got = page._run_check()
H.check(page.check_table.rowCount() == len(got) == len(qs), 'check table: {} rows'.format(len(got)))
H.check('open' in page.tabs.tabText(page.tabs.indexOf(page._check_tab)),
        'tab reads "{}"'.format(page.tabs.tabText(page.tabs.indexOf(page._check_tab))))
page._goto(edges[0])
sel = page.excl_table.currentRow()
H.check(page.tabs.currentWidget() is page._inputs_tab and sel >= 0
        and page.excl_table.item(sel, 0).text() == str(WIN), 'edge -> exclusion row #{}'.format(WIN))
page._goto(pm[0])
sel = page.pm_table.currentRow()
H.check(sel >= 0 and page.pm_table.item(sel, 0).text() == str(pm[0]['ref']), 'PM -> its row')
page._goto(cm['3.2 record in sync conflict'])
H.check(page.tabs.currentWidget() is page._text_tab
        and page._wr_rows[page.wr_table.currentRow()]['id'] == 'wle-conflict', '3.2 -> Customer text row')
page._goto(gap[0])
H.check(page.tabs.currentWidget() is page._data_tab, 'gap -> Data step')
H.check(inputs_digest() == before, 'running the check and following its questions changed nothing')

H.finish()
