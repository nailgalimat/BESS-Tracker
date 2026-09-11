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

H.finish()
