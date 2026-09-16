"""tests/_synthetic_scada.py — small made-up SCADA exports with the real header layout

No site data: the column names follow the export format ("Tashkent - LC200 BB.CC
- LC - SYSTEM WORKING STATUS", ...), the values are invented. Import after
_harness. Every writer returns the file's path.
"""
import os

import pandas as pd


def _date(t):
    return f'{t.month}/{t.day}/{t.year}'


def _time(t):
    return t.strftime('%I:%M %p').lstrip('0')


def stamps(start, end, minutes=5, drop=None):
    """Timestamps from start to end inclusive; `drop` = [(a, b)] removed (inclusive)."""
    ts = pd.date_range(start, end, freq=f'{minutes}min')
    for a, b in (drop or []):
        ts = ts[(ts < pd.Timestamp(a)) | (ts > pd.Timestamp(b))]
    return ts


def wide(path, ts, columns, value, sheet='Data'):
    """Date | Time | one column per name; value(t, name) fills the cells."""
    rows = [[_date(t), _time(t)] + [value(t, c) for c in columns] for t in ts]
    df = pd.DataFrame(rows, columns=['Date', 'Time'] + list(columns))
    tmp = path + '.tmp.xlsx'                 # pandas refuses an upper-case .XLSX
    with pd.ExcelWriter(tmp, engine='openpyxl') as xw:
        df.to_excel(xw, sheet_name=sheet, index=False)
    os.replace(tmp, path)
    return path


def lc_columns(signal, blocks=(1, 2)):
    return [f'Tashkent - LC200 {b:02d}.{lc:02d} - LC - {signal}' for b in blocks for lc in (1, 2)]


def pcs_columns(signal, blocks=(1, 2)):
    return [f'Tashkent - PCS {b:02d}.{lc:02d}.{u:02d} - PCS - CONVERTER UNIT {u} {signal}'
            for b in blocks for lc in (1, 2) for u in (1, 2)]


def cmu_columns(blocks=(1, 2)):
    return [f'Tashkent - CMU {b:02d}.01.01.{c:02d} - BSC - CMU - CHARGE AND DISCHARGE CYCLES'
            for b in blocks for c in (1, 2)]


def alarms(path, events):
    """events: [(activated, deactivated, trigger, element, sheet)]"""
    cols = ['ID', 'Status', 'Activated', 'Trigger name', 'Element', 'Deactivation']
    by = {'Communications': [], 'Production': [], 'Warning': []}
    for i, (a, d, trig, elem, sheet) in enumerate(events, start=1):
        by[sheet].append([i, 'Deactivated', pd.Timestamp(a), trig, elem,
                          pd.Timestamp(d) if d else None])
    tmp = path + '.tmp.xlsx'
    with pd.ExcelWriter(tmp, engine='openpyxl') as xw:
        for sheet, rows in by.items():
            pd.DataFrame(rows, columns=cols).to_excel(xw, sheet_name=sheet, index=False)
    os.replace(tmp, path)
    return path


def tashkent_month(folder, year, month, days=(1, 2, 3), status=None):
    """The Tashkent exports for a few days of a month. `status(t, col)` gives
    the LC working status (RUNNING by default). Returns {type key: path}."""
    os.makedirs(folder, exist_ok=True)
    d0 = pd.Timestamp(year, month, days[0])
    d1 = pd.Timestamp(year, month, days[-1]) + pd.Timedelta(hours=23, minutes=55)
    ts = stamps(d0, d1)
    p = {}
    p['working_status'] = wide(os.path.join(folder, 'LC working status.xlsx'), ts,
                               lc_columns('SYSTEM WORKING STATUS'),
                               status or (lambda t, c: 'RUNNING'))
    p['pcs_cd'] = wide(os.path.join(folder, 'PCS_charge_discharge_status.xlsx'), ts,
                       pcs_columns('CHARGE AND DISCHARGE STATUS'), lambda t, c: 'CHARGING')
    p['pcs_fault'] = wide(os.path.join(folder, 'PCS fault status.xlsx'), ts,
                          pcs_columns('FAULT STATUS 1'), lambda t, c: 0)
    p['soc'] = wide(os.path.join(folder, 'SOC month.xlsx'), ts,
                    lc_columns('SYSTEM SOC (%)'), lambda t, c: 50.0)
    hourly = stamps(d0 + pd.Timedelta(hours=1), d1, minutes=60)
    p['lc_charge'] = wide(os.path.join(folder, 'LC daily charge.xlsx'), hourly,
                          lc_columns('DAILY CHARGE ENERGY (kWh)'), lambda t, c: 100.0 * t.hour)
    p['lc_discharge'] = wide(os.path.join(folder, 'LC daily discharge.xlsx'), hourly,
                             lc_columns('DAILY DISCHARGE ENERGY (kWh)'), lambda t, c: 90.0 * t.hour)
    daily = stamps(d0, pd.Timestamp(year, month, days[-1]), minutes=1440)
    p['hv_meter'] = wide(os.path.join(folder, 'HV meter daily export and import.xlsx'), daily,
                         ['Tashkent - Primary Summation Meter - Total Meters Energy Exported (kWh)',
                          'Tashkent - Primary Summation Meter - Total Meters Energy Imported (kWh)'],
                         lambda t, c: 1000.0)
    p['alarms'] = alarms(os.path.join(folder, 'Alarms report.XLSX'), [
        (d0 + pd.Timedelta(hours=3), d0 + pd.Timedelta(hours=4),
         'BSC - System Fault Status: BSC-PCS comm fault', 'BSC 01.01.02', 'Production'),
        (d1 - pd.Timedelta(hours=2), d1 - pd.Timedelta(hours=1),
         'LC - SYSTEM ALARM STATE1', 'LC200 02.01', 'Warning')])
    snap = stamps(pd.Timestamp(year, month, days[-1]), pd.Timestamp(year, month, days[-1]) + pd.Timedelta(hours=1))
    p['soh_snapshot'] = wide(os.path.join(folder, 'Soh of last of the month.xlsx'), snap,
                             lc_columns('SYSTEM SOH (%)'), lambda t, c: 99.0)
    return p
