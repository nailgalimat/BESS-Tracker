""""Accumulative Number of Cycles in one Year": calendar year, current month
counted once, counter-based across months with no report.
"""
import _harness as H            # must be first: isolates DB, sync, network, history
import sys, io, os, shutil, sqlite3, warnings, datetime
warnings.filterwarnings('ignore')
import pandas as pd
import database.db_manager as dbm
from services.sync_config import sync_config
MVP, SCRATCH, copy = H.MVP, H.WORK, H.DB   # names the ported body uses
from services.bukhara_report_service import cycles_in_year, history_key

def rec(label, cyc, start=None, end=None):
    return {'month': label, 'cycles_total': cyc, 'cycles_start': start, 'cycles_end': end}

ok = True
def check(name, got, want, missing_got=None, missing_want=None):
    global ok
    good = abs(got - want) < 1e-6 and (missing_want is None or missing_got == missing_want)
    ok &= good
    print('   {:4} {:58} got {:8.2f} want {:8.2f}{}'.format(
        'ok' if good else 'FAIL', name, got, want,
        '' if missing_want is None else '  missing {} / {}'.format(missing_got, missing_want)))

# the real file as it stood, August already inside it
hist = [rec('December 2025', 19), rec('January 2026', 45), rec('February 2026', 39),
        rec('April 2026', 46.983333), rec('March 2026', 37.137906),
        rec('June 2026', 25.683259), rec('August 2026', 26.631962)]

print('=== sum path (no counters on record) ===')
v, miss = cycles_in_year(hist, 2026, 8, 26.631962)
check('August not counted twice; Dec 2025 not in 2026',
      v, 45 + 39 + 37.137906 + 46.983333 + 25.683259 + 26.631962, miss, [5, 7])

v2, _ = cycles_in_year(hist, 2026, 8, 26.631962)
check('a second run gives the same figure', v2, v)

v, miss = cycles_in_year([], 2026, 8, 26.6)
check('no history -> just this month, all earlier missing', v, 26.6, miss, [1, 2, 3, 4, 5, 6, 7])

v, miss = cycles_in_year(hist, 2026, 1, 45)
check('January: this month only, nothing missing', v, 45, miss, [])

print('\n=== ordering: labels must not be sorted as strings ===')
print('   history_key("August 2026") =', history_key({'month': 'August 2026'}),
      '| numeric fields win:', history_key({'month': 'x', 'year': 2026, 'month_num': 3}))

print('\n=== counter path ===')
h2 = [rec('January 2026', 45), rec('February 2026', 39),
      rec('March 2026', 37.1, start=500.0, end=537.1)]
v, miss = cycles_in_year(h2, 2026, 8, 26.6, month_start=640.0, month_end=666.6)
check('Jan+Feb from records, Mar->Aug from the counter (May/Jul absent)',
      v, 45 + 39 + (666.6 - 500.0), miss, [])

h3 = [rec('December 2025', 19, start=380, end=400.0)]
v, miss = cycles_in_year(h3, 2026, 8, 26.6, month_start=640.0, month_end=666.6)
check('last December closing counter anchors the whole year', v, 666.6 - 400.0, miss, [])

v, miss = cycles_in_year(h2, 2026, 8, 26.6, month_start=640.0, month_end=450.0)
check('counter went backwards (CMU replaced) -> falls back to sums',
      v, 45 + 39 + 37.1 + 26.6, miss, [4, 5, 6, 7])

h4 = [rec('January 2026', 45), rec('March 2026', 37.1, start=500.0, end=537.1)]
v, miss = cycles_in_year(h4, 2026, 8, 26.6, month_start=640.0, month_end=666.6)
check('a month missing before the anchor is still reported', v, 45 + 166.6, miss, [2])

print('\n=== year boundary ===')
hy = [rec('November 2026', 30, end=900.0), rec('December 2026', 28, start=900.0, end=928.0)]
v, miss = cycles_in_year(hy, 2027, 2, 31.0, month_start=955.0, month_end=986.0)
check('Feb 2027 = counter now - Dec 2026 close', v, 986.0 - 928.0, miss, [])

print('\nCYCLES YEAR UNIT ' + ('SMOKE OK' if ok else 'FAILED'))
H.check(ok, 'every cycles_in_year case')

print('\n=== the month-over-month table ===')
from services.bukhara_report_service import build_monthly_comparison
stored = [rec('August 2026', 26.6), rec('June 2026', 25.7), rec('July 2026', 28.5)]
cur = {'month': 'August 2026', 'year': 2026, 'month_num': 8, 'cycles_total': 26.63,
       'avg_soc_pct': 48.7, 'avg_soh_pct': 99.0, 'rte_pct': 90.5,
       'discharge_mwh': 20023, 'charge_mwh': 22125}
tbl = build_monthly_comparison(cur, stored)
H.check(list(tbl['month']) == ['June 2026', 'July 2026', 'August 2026'],
        'months in date order, the current month once: {}'.format(list(tbl['month'])))
H.check(list(tbl['cycles'].round(1)) == [25.7, 28.5, 26.6],
        'past months show their cycles (they read "—" before)')

H.finish()
