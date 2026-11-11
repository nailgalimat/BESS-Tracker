"""Exclusion types "KKS work" and "Dispatch instruction" (fresh database).

A window's type never changes the hours it excludes or the availability — only
the "Exclusion Type" row label. KKS work stays out of the PM row ("Scheduled
Maintenance" only). A Dispatch instruction window does not hide alarms from the
fault tables; a KKS window does, like any other. Legacy windows with NULL
project/year/month are still returned for any month, whatever their type.
"""
import _harness as H            # must be first
import datetime as dt

import pandas as pd

import database.db_manager as dbm
import services.availability_service as av
import services.availability_inputs_service as avi
from services.tashkent_report_service import calc_plant_hours_availability_tashkent

H.fresh_db('excl_types.db')
c = dbm.get_connection()
c.execute("INSERT INTO projects (name, num_zones, num_blocks, num_containers) VALUES ('TK', 1, 4, 4)")
PID = c.execute("SELECT id FROM projects").fetchone()[0]
c.commit(); c.close()

ALL_TYPES = ["Scheduled Maintenance", "Grid Outage", "Force Majeure", "Major Fault",
             "KKS work", "Dispatch instruction"]

print('=== the list: appended, old order and default kept ===')
H.check(av.EXCLUSION_TYPES == ALL_TYPES, 'EXCLUSION_TYPES = {}'.format(av.EXCLUSION_TYPES))
H.check(av.ALARM_NEUTRAL_TYPES == {"Dispatch instruction"}, 'only Dispatch is alarm-neutral')

print('\n=== same window under every type: same hours, same availability ===')
# 4 blocks x 2 LCs, one day at 5 min; blocks 1-2 down 08:00-12:00
ts = pd.date_range('2026-10-02 00:00', '2026-10-02 23:55', freq='5min')
rows = []
for t in ts:
    for b in (1, 2, 3, 4):
        down = b in (1, 2) and dt.time(8) <= t.time() < dt.time(12)
        for lc in (1, 2):
            rows.append((t, b, lc, 'FAULT' if down else 'RUNNING'))
ws = pd.DataFrame(rows, columns=['Datetime', 'block_id', 'container_id', 'working_status'])
dates = [dt.date(2026, 10, 2)]


def window(t):
    return {'exclusion_type': t, 'date_from': '2026-10-02', 'date_to': '2026-10-02',
            'time_from': '08:00', 'time_to': '12:00', 'affected_blocks': '1,2'}


results = {}
for t in ALL_TYPES:
    r = av.calculate_plant_excluded_hours([window(t)], dates, [1, 2, 3, 4])
    a = calc_plant_hours_availability_tashkent(ws, 20.0, 5.0, redundancy_threshold_pct=100.0,
                                               excluded_hours=r['excluded_hours'])
    results[t] = (round(r['excluded_hours'], 6), round(a['plant_availability_pct'], 6),
                  r['breakdown'])
print('   ', {t: v[:2] for t, v in results.items()})
hours = {v[0] for v in results.values()}
avail = {v[1] for v in results.values()}
H.check(len(hours) == 1 and hours != {0}, 'excluded hours identical: {}'.format(hours))
H.check(len(avail) == 1, 'availability identical: {}'.format(avail))
H.check(all(t in results[t][2] and results[t][2][t] > 0 for t in ALL_TYPES),
        'breakdown has a key for every type, filled under its own type')
H.check(results['KKS work'][2].get('Scheduled Maintenance') == 0,
        'a KKS window does not enter the PM row (Scheduled Maintenance = 0)')
H.check(results['Dispatch instruction'][2].get('Scheduled Maintenance') == 0,
        'nor does a Dispatch window')

print('\n=== alarm tagging: Dispatch is neutral, KKS covers ===')
alarm_t = pd.Timestamp('2026-10-02 09:00')
H.check(av.match_alarm_to_exclusions(alarm_t, 1, [window('Dispatch instruction')]) is None,
        'alarm inside a Dispatch window: not excluded')
hit = av.match_alarm_to_exclusions(alarm_t, 1, [window('KKS work')])
H.check(hit is not None and hit['exclusion_type'] == 'KKS work', 'alarm inside a KKS window: excluded')
# same window values, different type: the window cache must not mix them up
both = av.match_alarm_to_exclusions(alarm_t, 1, [window('Dispatch instruction'),
                                                 window('Grid Outage')])
H.check(both is not None and both['exclusion_type'] == 'Grid Outage',
        'Dispatch skipped, an overlapping Grid Outage still covers')

print('\n=== validation and storage ===')
for t in ("KKS work", "Dispatch instruction"):
    try:
        avi.validate_window(PID, t, '2026-10-02', '08:00', '2026-10-02', '12:00', '1,2',
                            today=dt.date(2026, 11, 1))
        ok = True
    except avi.InputValidationError as e:
        ok = False; print('   ', e.problems)
    H.check(ok, 'validate_window accepts ' + t)
eid = av.add_exclusion('KKS work', '2026-10-03', '2026-10-03', '07:00', '15:00', '3',
                       'KKS installation', project_id=PID, year=2026, month=10)
H.check(eid > 0, 'add_exclusion stores a KKS work window')
c = dbm.get_connection()
c.execute("INSERT INTO availability_exclusions (project_id, exclusion_type, date_from, date_to, "
          "time_from, time_to, affected_blocks, description, year, month) "
          "VALUES (NULL, 'Dispatch instruction', '2026-10-15', '2026-10-31', '00:00', '23:59', "
          "'', 'legacy unstamped', NULL, NULL)")
c.commit(); c.close()
got = {e['exclusion_type'] for e in av.get_exclusions(project_id=PID, year=2026, month=10)}
H.check(got == {'KKS work', 'Dispatch instruction'},
        'get_exclusions(PID, 2026, 10) returns the NULL-stamped Dispatch window too: {}'.format(got))

print('\n=== the dialog: Scheduled Maintenance first, hint follows the type ===')
from PyQt5.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
import ui.scada_report_page as srp
d = srp.ExclusionDialog(None, blocks_list=[1, 2, 3, 4])
items = [d.type_combo.itemText(i) for i in range(d.type_combo.count())]
H.check(items == ALL_TYPES and d.type_combo.currentText() == 'Scheduled Maintenance',
        'combo lists the six types, default Scheduled Maintenance')
H.check('Scheduled unavailability (PM)' in d.type_hint.text(), 'hint for Scheduled Maintenance')
d.type_combo.setCurrentText('Dispatch instruction')
H.check('alarms inside the window still count' in d.type_hint.text(),
        'hint follows the selection: ' + d.type_hint.text()[:50])
H.check(set(srp.EXCLUSION_TYPE_HINTS) == set(ALL_TYPES), 'every type has a hint')

H.finish()
