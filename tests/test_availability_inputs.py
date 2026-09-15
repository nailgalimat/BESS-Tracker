"""Availability inputs: one block parser, weighting by the plant's own blocks,
entries stamped with their report month, and one definition of what a report
takes from the database."""
import _harness as H            # must be first
import pandas as pd

import services.availability_service as av
import services.report_workflow_service as rw
from services.tashkent_report_service import _tashkent_exclusion_mask

print('=== parse_block_spec ===')
cases = {'': None, 'all': None, 'All blocks': None, '3': {3}, '23,24': {23, 24},
         '1-8': set(range(1, 9)), '8-1': set(range(1, 9)),
         '1-3,12': {1, 2, 3, 12}, 'x,5': {5}, ' 4 , 6 ': {4, 6}}
for spec, want in cases.items():
    H.check(av.parse_block_spec(spec) == want, '{!r:>12} -> {}'.format(spec, want))

print('\n=== how many of the plant\'s blocks an exclusion covers ===')
tash, buk = list(range(1, 71)), list(range(1, 16))
H.check(av._n_blocks_affected('1-8', tash) == 8, "'1-8' is eight blocks, not one")
H.check(av._n_blocks_affected(','.join(map(str, tash)), buk) == 15,
        "Tashkent's 1..70 on a 15-block plant covers its 15")
H.check(av._n_blocks_affected('16,17', buk) == 0,
        "blocks the plant doesn't have cover nothing")
H.check(av._n_blocks_affected('', buk) == 15, 'empty means the whole plant')

r = av.calculate_plant_excluded_hours(
    [{'exclusion_type': 'Grid Outage', 'date_from': '2026-08-10', 'date_to': '2026-08-10',
      'time_from': '00:00', 'time_to': '23:59', 'affected_blocks': '1-7'}],
    [pd.Timestamp('2026-08-10').date()], tash)
H.check(abs(r['excluded_hours'] - 24 * 7 / 70) < 0.05,
        "a '1-7' exclusion weighs 7/70 of the day ({:.2f} h)".format(r['excluded_hours']))

print('\n=== the Tashkent exclusion mask reads ranges ===')
dt = pd.Series(pd.to_datetime(['2026-08-10 12:00'] * 4))
blk = pd.Series([1, 2, 3, 9])
mask = _tashkent_exclusion_mask(dt, blk, [{
    'date_from': '2026-08-10', 'date_to': '2026-08-10', 'time_from': '00:00',
    'time_to': '23:59', 'affected_blocks': '1-3'}])
H.check(list(mask) == [True, True, True, False],
        "'1-3' masks blocks 1-3 (int('1-3') used to drop the exclusion)")

print('\n=== an alarm raised just before a planned stop belongs to it ===')
# a PCS sees the grid go a few minutes before its LC reports the fault the
# window is set by; its islanding alarm used to stay in the fault tables
W = [{'exclusion_type': 'Grid Outage', 'date_from': '2026-08-12', 'date_to': '2026-08-12',
      'time_from': '09:40', 'time_to': '14:05', 'affected_blocks': ''}]
m = av.match_alarm_to_exclusions
H.check(m('2026-08-12 09:36', 6, W, deactivated_dt='2026-08-12 13:10') is not None,
        '4 min before, still active when the window starts -> covered')
H.check(m('2026-08-12 09:36', 6, W, deactivated_dt=None) is not None,
        '4 min before, not cleared at all -> covered')
H.check(m('2026-08-12 09:36', 6, W, deactivated_dt='2026-08-12 09:38') is None,
        '4 min before but cleared before the window -> not covered')
H.check(m('2026-08-12 09:20', 6, W, deactivated_dt='2026-08-12 13:10') is None,
        '20 min before -> a fault of its own, not covered')
H.check(m('2026-08-12 10:00', 6, W) is not None, 'inside the window -> covered as before')

print('\n=== new entries carry their report month ===')
eid = av.add_exclusion('Grid Outage', '2026-10-05', '2026-10-05', affected_blocks='4')
row = [e for e in av.get_exclusions() if e['id'] == eid][0]
H.check((row['year'], row['month']) == (2026, 10), 'exclusion stamped 2026-10 from its date')
mid = av.add_manual_unavailability(block=4, date_from='2026-10-06', date_to='2026-10-06',
                                   downtime_h=2.0)
row = [e for e in av.get_manual_unavailability() if e['id'] == mid][0]
H.check((row['year'], row['month']) == (2026, 10), 'manual downtime stamped 2026-10')
H.check(len(av.get_manual_unavailability(year=2026, month=10)) >= 1,
        'and a month-scoped read finds it')

print('\n=== report_inputs: what the report takes from the database ===')
inp = rw.report_inputs(1, 2026, 8)
H.check(len(inp['exclusions'] or []) == len(av.get_exclusions(project_id=1, year=2026, month=8)),
        'exclusions: {}'.format(len(inp['exclusions'] or [])))
n_manual = len(av.get_manual_unavailability(project_id=1, year=2026, month=8))
n_pm = len(rw.pm_as_unavailability(1, 2026, 8))
H.check(len(inp['manual_unavailability'] or []) == n_manual + n_pm,
        'manual downtime {} + PM rows {}'.format(n_manual, n_pm))
for k in ('plant_capacity_mw', 'yearly_cycle_target', 'redundancy_threshold_pct'):
    H.check(k in inp, k + ' comes from the project config')

print('\n=== manual downtime dates: ISO from the database, day-first when typed ===')
from services.tashkent_report_service import _parse_manual_unavail
got = [(r['d_from'].date().isoformat(), r['d_to'].date().isoformat()) for r in _parse_manual_unavail([
    {'block': 2, 'date_from': '2026-08-01', 'downtime_h': 4},
    {'block': 3, 'date_from': '2026-08-03', 'date_to': '2026-08-04', 'downtime_h': 4},
    {'block': 5, 'date_from': '05.08.2026', 'downtime_h': 4},
    {'block': 6, 'date_from': '06/08/2026', 'downtime_h': 4}])]
H.check(got == [('2026-08-01', '2026-08-01'), ('2026-08-03', '2026-08-04'),
                ('2026-08-05', '2026-08-05'), ('2026-08-06', '2026-08-06')],
        'the 1st-12th stay in August (ISO was read as 8 January): {}'.format(got))
aug = [r for r in _parse_manual_unavail(inp['manual_unavailability'] or [])]
H.check(all(r['d_from'].month == 8 for r in aug), 'every August manual row parses into August')

print('\n=== PM records as report inputs (live snapshot) ===')
import json
import services.availability_inputs_service as avi
H.check(rw.pm_as_unavailability(1, 2026, 8) == [],
        'August has no PM records: its PM is the 24 manual rows, so the PM-stop rule '
        'and the single-writer dedupe leave the golden month alone')
sep_ledger = rw.pm_ledger(1, 2026, 9)
sep_rows = rw.pm_as_unavailability(1, 2026, 9)
H.check(len(sep_rows) == sum(len(e['counted']) for e in sep_ledger)
        and all(r.get('pm_id') for r in sep_rows),
        'September: {} PM record(s) -> {} downtime row(s), each marked with its record, {} h'.format(
            len(sep_ledger), len(sep_rows), sum(r['downtime_h'] for r in sep_rows)))
H.check(not any(e['problems'] for e in sep_ledger),
        'no September record is flagged (no block / duplicate / hours): {}'.format(
            [(e['row']['id'], e['problems']) for e in sep_ledger if e['problems']]))

print('\n=== manual downtime and exclusion windows are checked before they are saved ===')
T = __import__('datetime').date(2026, 9, 20)
for args, why in [((1, '0', '2026-09-12', None, 3), 'block 0'),
                  ((1, 'abc', '2026-09-12', None, 3), 'junk block text'),
                  ((1, '', '2026-09-12', None, 3), 'empty block'),
                  ((1, '24', '2026-09-12', None, 25), '25 h in one day'),
                  ((1, '24', '2026-09-12', '2026-09-11', 3), 'end before start')]:
    try:
        avi.validate_manual(*args, today=T)
        H.check(False, 'manual downtime refused: ' + why)
    except avi.InputValidationError as e:
        H.check(True, 'manual downtime refused: {} ({})'.format(why, e))
try:
    avi.validate_manual(1, '24', '2026-09-12', None, 3, lc=3, today=T)
    H.check(False, 'LC 3 refused')
except avi.InputValidationError:
    H.check(True, 'LC 3 refused')
try:
    avi.validate_manual(1, '24', '2026-09-12', None, 3, time_from='14:00', time_to='13:00', today=T)
    H.check(False, 'times running backwards refused')
except avi.InputValidationError:
    H.check(True, 'times running backwards refused')
H.check(avi.validate_manual(1, '24', '2026-09-12', '2026-09-13', 30, today=T)['hours'] == 30,
        '30 h over two days is fine')
for args, why in [(('Grid Outage', '2026-09-19', '17:00', '2026-09-19', '14:00', '1-4'), 'window ends before it starts'),
                  (('Grid Outage', '2026-09-19', '14:00', '2026-09-19', '17:00', ''), 'no blocks'),
                  (('Outage', '2026-09-19', '14:00', '2026-09-19', '17:00', '1'), 'unknown type')]:
    try:
        avi.validate_window(1, *args, today=T)
        H.check(False, 'window refused: ' + why)
    except avi.InputValidationError:
        H.check(True, 'window refused: ' + why)
H.check(avi.validate_window(1, 'Grid Outage', '2026-09-19', '14:00', '2026-09-19', '17:00', 'all',
                            today=T)['affected_blocks'] == 'all', "'all' is the explicit whole plant")

print('\n=== phone counts / excluded wait, and count only once confirmed (snapshot) ===')


def sep_json():
    return json.dumps(rw.report_inputs(1, 2026, 9), sort_keys=True, default=str)


s0 = sep_json()
grid = {'id': 'test-grid-1', 'project_id': 1, 'kind': 'excluded', 'blocks': '',
        'date_from': '2026-09-12', 'date_to': '2026-09-13', 'hours': 3,
        'exclusion_type': 'Grid Outage', 'description': 'grid lost 14:00-17:00'}
down = {'id': 'test-counts-1', 'project_id': 1, 'kind': 'counts', 'blocks': 'abc',
        'date_from': '2026-09-12', 'date_to': '2026-09-12', 'hours': 5,
        'exclusion_type': '', 'description': 'LC trip'}
junk = dict(down, id='test-counts-2', blocks='7')
for e in (grid, down, junk):
    H.check(avi.route_field_event(e) == 'queued', '{} event waits for the desktop'.format(e['kind']))
H.check(sep_json() == s0, 'report_inputs identical before and after (were 00:00 -> 03:00 next day, block 0)')
waiting = {q['event_id'] for q in avi.pending_events(1, 2026, 9)}
H.check({'test-grid-1', 'test-counts-1', 'test-counts-2'} <= waiting, 'all three listed as waiting')
n_ex = len(av.get_exclusions(project_id=1, year=2026, month=9))
eid = avi.confirm_excluded_event('test-grid-1', 'Grid Outage', '2026-09-12', '14:00',
                                 '2026-09-12', '17:00', 'all', 'grid lost')
ex = [e for e in av.get_exclusions(project_id=1, year=2026, month=9) if e['id'] == eid][0]
H.check((ex['time_from'], ex['time_to'], ex['affected_blocks'], ex['project_id'], ex['month'])
        == ('14:00', '17:00', 'all', 1, 9) and len(av.get_exclusions(project_id=1, year=2026, month=9)) == n_ex + 1,
        'confirmed with its real window 14:00-17:00, whole plant by explicit choice, stamped 1/2026/9')
ids = avi.confirm_counts_event('test-counts-1', '24,25', '2026-09-12', '2026-09-12', 5, None, 'LC trip')
H.check(len(ids) == 2 and sep_json() != s0, 'counts confirmed on blocks 24, 25 -> 2 rows, inputs change')
avi.reject_event('test-counts-2', 'duplicate of the work report')
H.check(not ({'test-grid-1', 'test-counts-1', 'test-counts-2'}
             & {q['event_id'] for q in avi.pending_events(1, 2026, 9)}), 'nothing left waiting')
try:
    avi.confirm_counts_event('test-counts-2', '7', '2026-09-12', None, 5)
    H.check(False, 'a rejected event cannot be applied later')
except ValueError:
    H.check(True, 'a rejected event cannot be applied later')
mid = ids[0]
avi.update_manual(mid, 1, '26', '2026-09-12', '2026-09-12', 2.5, 1, 'LC trip (LC1)', '10:00', '12:30')
row = [r for r in av.get_manual_unavailability(project_id=1, year=2026, month=9) if r['id'] == mid][0]
H.check((row['block'], row['lc'], row['downtime_h'], row['time_from'], row['time_to'])
        == (26, 1, 2.5, '10:00', '12:30'), 'a manual row is edited in place')

H.finish()
