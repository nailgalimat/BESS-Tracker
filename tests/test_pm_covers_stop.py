"""A PM record covers its own stop (user decision 2026-09-15).

Synthetic day, block 41, 16.09.2026: the PM isolation 10:00-13:00 shows in the
LC working status as FAULT / '0' / FAULT with the PCS units non-operating, and a
genuine fault follows at 15:00-16:00. The technician entered 3 h of PM.

  without the rule   8 unit-h (PM stop, FAULT samples) + 4 (later fault)
                     + 12 (3 h x 4 PCS entered)                      = 24
  with the rule      the PM stop is covered by the record            = 16
                     and the later fault still counts (4 unit-h)

Also pinned: a manual row typed as PM does not trigger it, a PM record whose
block did not stop covers nothing, an exclusion window over the stop comes
first, the longest stop in the working day is the one taken, 4.4.1 drops the
covered stop, the LC-level fallback applies the same rule, and report_inputs
marks PM records so the generator can find them.
"""
import _harness as H            # must be first
import pandas as pd

import services.report_workflow_service as rw
import database.db_manager as dbm
from services.tashkent_report_service import (
    calc_contractual_availability_tashkent, pm_stop_windows, pm_records_from_manual,
    build_unavailability_reasons_tashkent, PM_WORKDAY)

DAY = pd.Timestamp('2026-09-16')
TS = pd.date_range(DAY, DAY + pd.Timedelta(hours=23, minutes=55), freq='5min')


def _between(t, a, z):
    return DAY + pd.Timedelta(hours=a) <= t < DAY + pd.Timedelta(hours=z)


def frames(stop=(10, 13), fault=(15, 16)):
    """ws_long and pcs_long for blocks 41 and 42 (2 LCs x 2 PCS units)."""
    ws, pcs = [], []
    for b in (41, 42):
        for lc in (1, 2):
            for t in TS:
                st, nonop = 'RUNNING', False
                if b == 41 and _between(t, *stop):
                    # FAULT at the isolation, '0' while de-energised, FAULT at restart
                    mid = _between(t, stop[0] + 1, stop[1] - 1)
                    st, nonop = ('0' if mid else 'FAULT'), True
                if b == 41 and fault and _between(t, *fault):
                    st, nonop = 'FAULT', True
                ws.append({'Datetime': t, 'block_id': b, 'container_id': lc, 'working_status': st})
                for u in (1, 2):
                    pcs.append({'Datetime': t, 'block_id': b, 'container_id': lc, 'unit': u,
                                'pcs_status': 'NON-OPERATING MODE' if nonop else 'CHARGING'})
    return pd.DataFrame(ws), pd.DataFrame(pcs)


PM_ROW = {'block': 41, 'lc': None, 'date_from': '2026-09-16', 'date_to': '2026-09-16',
          'downtime_h': 3.0, 'cause': 'PM: checklist', 'subsystem': 'PM', 'pm_id': 7}

print('=== the rule changes the result ===')
ws, pcs = frames()
recs = pm_records_from_manual([PM_ROW])
stops = pm_stop_windows(ws, recs)
H.check(len(stops) == 1 and stops[0]['start'] == DAY + pd.Timedelta(hours=10)
        and stops[0]['end'] == DAY + pd.Timedelta(hours=13),
        'PM stop found 10:00-13:00: {}'.format(
            [(str(s['start']), str(s['end'])) for s in stops]))
H.check(stops and [(str(a.time()), str(z.time())) for a, z in stops[0]['other_stops']]
        == [('15:00:00', '16:00:00')],
        'the later fault is named as a second stop in the working day')

before = calc_contractual_availability_tashkent(ws, pcs_long=pcs, manual_unavailability=[PM_ROW])
after = calc_contractual_availability_tashkent(ws, pcs_long=pcs, manual_unavailability=[PM_ROW],
                                               pm_stops=stops)
print('   without the rule: {:.2f} unit-h, {:.4f}%'.format(before['down_unit_hours'],
                                                          before['availability_pct']))
print('   with the rule:    {:.2f} unit-h, {:.4f}%'.format(after['down_unit_hours'],
                                                          after['availability_pct']))
H.check(abs(before['down_unit_hours'] - 24.0) < 1e-9, 'without: 8 (stop) + 4 (fault) + 12 (PM h) = 24')
H.check(abs(after['down_unit_hours'] - 16.0) < 1e-9, 'with: the PM is charged once, by its hours: 16')
H.check(abs(after['pm_covered_unit_hours'] - 8.0) < 1e-9, 'PM-covered unit-h reported: 8')
H.check(after['availability_pct'] > before['availability_pct'], 'availability rises accordingly')
H.check(abs(after['pm_stop_rows'][0]['covered_unit_hours'] - 8.0) < 1e-9,
        'and per stop, for the log / pre-generation check')

print('\n=== a fault later that day still counts ===')
no_pm = calc_contractual_availability_tashkent(ws, pcs_long=pcs, manual_unavailability=[],
                                               pm_stops=stops)
H.check(abs(no_pm['down_unit_hours'] - 4.0) < 1e-9,
        '15:00-16:00 fault: 4 unit-h counted ({:.2f})'.format(no_pm['down_unit_hours']))

print('\n=== what does not trigger it ===')
typed = dict(PM_ROW); typed.pop('pm_id')
H.check(pm_records_from_manual([typed]) == [], 'a manual row typed as PM is not a PM record')
quiet = pm_stop_windows(ws, pm_records_from_manual([dict(PM_ROW, block=42, pm_id=8)]))
H.check(len(quiet) == 1 and quiet[0]['start'] is None,
        'block 42 never stopped: the record covers nothing, only its hours count')
r42 = calc_contractual_availability_tashkent(ws, pcs_long=pcs, pm_stops=quiet,
                                             manual_unavailability=[dict(PM_ROW, block=42, pm_id=8)])
H.check(abs(r42['down_unit_hours'] - (12 + 12)) < 1e-9 and r42['pm_covered_unit_hours'] == 0,
        'block 41 stop + fault still counted, 42 charged 12 by its hours')
other_day = pm_stop_windows(ws, pm_records_from_manual([dict(PM_ROW, date_from='2026-09-17',
                                                              date_to='2026-09-17')]))
H.check(all(s['start'] is None for s in other_day), 'a PM dated another day covers nothing here')

print('\n=== an exclusion window over the stop comes first ===')
win = [{'exclusion_type': 'Scheduled Maintenance', 'date_from': '2026-09-16', 'time_from': '09:30',
        'date_to': '2026-09-16', 'time_to': '13:30', 'affected_blocks': '41'}]
ex = calc_contractual_availability_tashkent(ws, pcs_long=pcs, exclusions=win,
                                            manual_unavailability=[PM_ROW], pm_stops=stops)
H.check(abs(ex['excluded_unit_hours'] - 8.0) < 1e-9 and ex['pm_covered_unit_hours'] == 0
        and abs(ex['down_unit_hours'] - 16.0) < 1e-9,
        'window excludes 8, PM covers 0 more, total 16 — as in August (windows + PM rows)')

print('\n=== the longest stop in the working day is the PM stop ===')
ws2, pcs2 = frames(stop=(10, 12), fault=(14, 18))
s2 = pm_stop_windows(ws2, recs)
H.check(s2 and s2[0]['start'] == DAY + pd.Timedelta(hours=14),
        'a 4-h stop beats a 2-h one (documented; the log names the other): {}'.format(
            s2 and str(s2[0]['start'])))
night_ws, _ = frames(stop=(1, 4), fault=None)
H.check(pm_stop_windows(night_ws, recs)[0]['start'] is None,
        'a stop wholly outside {:02d}:00-{:02d}:00 is not a PM stop'.format(*PM_WORKDAY))

print('\n=== 4.4.1 drops the covered stop, keeps the fault ===')
r_all = build_unavailability_reasons_tashkent(ws, exclusions=None, min_hours=0.1)
r_pm = build_unavailability_reasons_tashkent(ws, exclusions=[stops[0]['exclusion']], min_hours=0.1)
h_all = float(r_all[r_all['block_id'] == 41]['downtime_h'].sum())
h_pm = float(r_pm[r_pm['block_id'] == 41]['downtime_h'].sum())
H.check(abs(h_all - 6.0) < 1e-9 and abs(h_pm - 2.0) < 1e-9,
        'LC-hours of FAULT for block 41: {} -> {} (the 15:00 fault on both LCs)'.format(h_all, h_pm))

print('\n=== LC-level fallback (no PCS data) ===')
lc_b = calc_contractual_availability_tashkent(ws, manual_unavailability=[PM_ROW])
lc_a = calc_contractual_availability_tashkent(ws, manual_unavailability=[PM_ROW], pm_stops=stops)
H.check(lc_a['method'] == 'lc' and abs(lc_b['down_unit_hours'] - lc_a['down_unit_hours']
                                       - lc_a['pm_covered_unit_hours']) < 1e-9
        and abs(lc_a['pm_covered_unit_hours'] - 4.0) < 1e-9,
        'same rule on LC fault time: covered {} LC-h'.format(lc_a['pm_covered_unit_hours']))

print('\n=== the PM stop\'s alarms leave the tables; the same alarm later stays ===')
# User decision 2026-09-15: a PM stop is treated like a planned stop for alarm
# purposes, through the same is_excluded tagging the Scheduled Maintenance
# windows use (tag_alarms_with_exclusions), which every table then drops.
from services.bukhara_report_service import (tag_alarms_with_exclusions, build_faults_summary,
                                             select_important_alarms, build_breakdown_candidates,
                                             mark_planned_stop_events)


def _alarm(ts, dur_h, elem='PCS 41.01.01'):
    a = DAY + pd.Timedelta(hours=ts)
    return {'Trigger name': 'PCS - Converter Unit 1 Fault Status 1: Islanding protection',
            'Element': elem, 'Activated': a, 'Deactivation': a + pd.Timedelta(hours=dur_h),
            'duration_min': dur_h * 60, 'category': 'production', 'cls_reason': 'Islanding Protection',
            'cls_subsystem': 'PCS', 'cls_severity': 'Critical', 'cls_resolution': 'Check grid'}


prod = pd.DataFrame([_alarm(10.5, 2.0), _alarm(15.25, 0.5), _alarm(10.5, 2.0, 'PCS 42.01.01')])
pm_windows = [w['exclusion'] for w in stops if w['exclusion']]
tagged = tag_alarms_with_exclusions({'production_dedup': prod.copy(),
                                     'warning_persistent': pd.DataFrame(),
                                     'warning_transient': pd.DataFrame()}, pm_windows)
p = tagged['production_dedup']
H.check(list(p['is_excluded']) == [True, False, False]
        and p.loc[0, 'excluded_by'] == 'Preventive maintenance',
        'block 41 10:30 (inside the PM stop) tagged; 41 at 15:15 and block 42 at 10:30 not')
p_dedup = p[~p['is_excluded']]
fs = build_faults_summary(p_dedup, ws_long=ws, drop_planned=True)
H.check(not fs.empty and int(fs['occurrences'].sum()) == 2,
        '5.1 faults summary keeps 2 occurrences, not 3 ({})'.format(
            int(fs['occurrences'].sum()) if not fs.empty else 0))
imp = mark_planned_stop_events(select_important_alarms(p), ws_long=ws)
imp = imp[~imp['on_planned_stop'].astype(bool)]
H.check(sorted(zip(imp['Element'], imp['Activated'].dt.strftime('%H:%M')))
        == [('PCS 41.01.01', '15:15'), ('PCS 42.01.01', '10:30')],
        'important events: the PM-stop alarm is gone, the later one on block 41 stays')
bd = build_breakdown_candidates(p_dedup, ws_long=ws)
H.check([(r['blocks'], r['date_time']) for r in (bd or []) if r['date_time'].endswith('10:30')]
        == [('42', '16.09.2026 10:30')],
        '5.2 candidates: the 2-h alarm on block 42 is offered, the same one in 41\'s PM stop is not')
untagged = tag_alarms_with_exclusions({'production_dedup': prod.copy()}, [])
H.check(not untagged['production_dedup']['is_excluded'].any(),
        'no PM record, no window: nothing tagged (a stop without a PM record is a fault)')

print('\n=== report_inputs marks PM records for the generator ===')
H.fresh_db('pm_stop.db')
c = dbm.get_connection()
c.execute("INSERT INTO projects (name, num_zones, num_blocks, num_containers) VALUES ('T', 1, 70, 1)")
pid = c.execute("SELECT id FROM projects").fetchone()[0]
c.commit(); c.close()
rw.record_pm(pid, '41', '2026-09-16', None, 3, 'PM as per the checklist', source='phone', source_ref='ev-1')
inp = rw.report_inputs(pid, 2026, 9)
recs2 = pm_records_from_manual(inp['manual_unavailability'])
H.check(len(recs2) == 1 and recs2[0]['block'] == 41 and recs2[0]['hours'] == 3.0,
        'the PM record reaches the rule through report_inputs: {}'.format(recs2))

H.finish()
