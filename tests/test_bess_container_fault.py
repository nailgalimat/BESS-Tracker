"""A whole BESS container in fault stops its PCS unit without touching the LC
status or the PCS fault flag; event hours are clock time, not the sum of every
channel that reports one condition.
"""
import _harness as H            # must be first: isolates DB, sync, network, history
import pandas as pd
from services.bukhara_report_service import elapsed_hours
import services.tashkent_report_service as T

T0 = pd.Timestamp('2030-12-02')
ts = lambda hm: T0 + pd.Timedelta(hm + ':00')

print('=== elapsed_hours ===')
H.check(abs(elapsed_hours([ts('07:30')] * 16, [ts('14:20')] * 16, [28] * 16) - 41 / 6) < 1e-9,
        'sixteen rows over one 6h50 on one block -> 6.83 h, not 109 h')
H.check(abs(elapsed_hours([ts('08:00'), ts('09:00')], [ts('10:00'), ts('11:00')], [28, 28]) - 3) < 1e-9,
        'overlapping rows on one block merge: 08-10 + 09-11 = 3 h')
H.check(abs(elapsed_hours([ts('08:00'), ts('09:00')], [ts('10:00'), ts('11:00')], [28, 5]) - 4) < 1e-9,
        'the same hours on two blocks add up: 4 h')
H.check(elapsed_hours([ts('08:00'), pd.NaT], [ts('09:00'), ts('10:00')]) == 1.0,
        'a row with no start is ignored')

print('\n=== episodes from the alarm log ===')
prod = pd.DataFrame([
    # the container as a whole: two BSC triggers over the same hours -> one episode
    ('BSC 28.02.01', 'BSC - System Fault Status: DCDC fault', '07:30', '14:20'),
    ('BSC 28.02.01', 'BSC - System Fault Status: CMU fault', '07:30', '14:20'),
    # one module of eight, the container keeps running -> not an episode
    ('DC/DC 28.02.01.05', 'DCDC - Fault Status 2: CMU CAN output fault', '06:00', '15:00'),
    ('BSC 05.01.02', 'BSC - CMU Fault: CMU-SMU communication fault', '06:00', '15:00'),
], columns=['Element', 'Trigger name', 'Activated', 'Deactivation'])
prod['Activated'] = prod['Activated'].map(ts)
prod['Deactivation'] = prod['Deactivation'].map(ts)
eps = T.bess_container_fault_episodes({'production_dedup': prod})
H.check(len(eps) == 1 and (eps[0]['block'], eps[0]['lc'], eps[0]['container']) == (28, 2, 1)
        and eps[0]['start'] == ts('07:30') and eps[0]['end'] == ts('14:20'),
        'one episode, BSC 28.02.01 07:30-14:20: {}'.format(
            [(e['block'], e['lc'], e['container'], str(e['start']), str(e['end'])) for e in eps]))

print('\n=== availability ===')
t = pd.date_range(T0 + pd.Timedelta('06:00:00'), T0 + pd.Timedelta('15:55:00'), freq='5min')
ws = pd.DataFrame([(d, 28, lc, 'RUNNING') for d in t for lc in (1, 2)],
                  columns=['Datetime', 'block_id', 'container_id', 'working_status'])

def status(d, lc, unit):
    if d >= ts('14:30'):
        return 'NON-OPERATING MODE'                       # block idle for dispatch
    if lc == 1:
        return 'CHARGING'
    if unit == 1:                                         # healthy container: runs until full
        return 'CHARGING' if d < ts('11:30') else 'NON-OPERATING MODE'
    return 'NON-OPERATING MODE' if ts('07:30') <= d <= ts('14:20') else 'CHARGING'

pcs = pd.DataFrame([(d, 28, lc, u, status(d, lc, u)) for d in t for lc in (1, 2) for u in (1, 2)],
                   columns=['Datetime', 'block_id', 'container_id', 'unit', 'pcs_status'])
base = T.calc_contractual_availability_tashkent(ws, pcs_long=pcs)
r = T.calc_contractual_availability_tashkent(ws, pcs_long=pcs, bess_fault_episodes=eps)
want = (14 * 60 + 20 - (7 * 60 + 30)) / 60      # 07:30-14:20, end exclusive: 6.83 h
H.check(base['down_unit_hours'] == 0, 'without the rule nothing counts: LC RUNNING, no PCS flag')
H.check(abs(r['bess_fault_unit_hours'] - want) < 1e-9 and len(r['bess_fault_rows']) == 1
        and r['bess_fault_rows'][0]['unit'] == 2,
        'the idle unit of LC2 counts {:.2f} h (want {:.2f}); its sibling, stopped because '
        'its container was full, does not'.format(r['bess_fault_unit_hours'], want))
H.check(abs(r['down_unit_hours'] - want) < 1e-9 and r['availability_pct'] < base['availability_pct'],
        'and it reaches the figure: {:.3f}% -> {:.3f}%'.format(base['availability_pct'], r['availability_pct']))

win = [{'exclusion_type': 'Scheduled Maintenance', 'date_from': '2030-12-02', 'date_to': '2030-12-02',
        'time_from': '07:30', 'time_to': '08:55', 'affected_blocks': '28'}]
rx = T.calc_contractual_availability_tashkent(ws, pcs_long=pcs, exclusions=win, bess_fault_episodes=eps)
H.check(abs(rx['bess_fault_unit_hours'] - (want - 1.5)) < 1e-9,
        'time inside an exclusion window is not counted: {:.2f} h'.format(rx['bess_fault_unit_hours']))

H.finish()
