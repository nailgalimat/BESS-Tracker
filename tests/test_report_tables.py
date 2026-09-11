"""No general alarm reaches any fault table of the Tashkent report in any
imported month, and every removed event is named in a note.
"""
import _harness as H            # must be first: isolates DB, sync, network, history
import sys, io, os, shutil, sqlite3, warnings, datetime
warnings.filterwarnings('ignore')
import pandas as pd
import database.db_manager as dbm
from services.sync_config import sync_config
MVP, SCRATCH, copy = H.MVP, H.WORK, H.DB   # names the ported body uses
from services.asset_tree_service import is_umbrella_trigger
import services.bukhara_report_service as B
from services.tashkent_report_service import _drop_general_events

con = sqlite3.connect('file:' + H.DB.replace(os.sep, '/') + '?mode=ro', uri=True)
def frame(cat, m):
    return pd.read_sql_query(
        "SELECT trigger_name AS 'Trigger name', element AS 'Element', "
        "activated AS 'Activated', deactivated AS 'Deactivation', "
        "duration_min, is_excluded FROM alarm_events "
        "WHERE project_id=1 AND year=2026 AND month=? AND category=?",
        con, params=(m, cat))
cls = B.load_alarm_classifications(None)
failures = []

def check(month, table, names, note):
    names = [str(n) for n in names if str(n).strip()]
    bad = [n for n in names if is_umbrella_trigger(n)]
    if bad:
        failures.append((month, table, bad[:3]))
        print('   {:34} {:>3} rows  ** {} **'.format(table, len(names), bad[:2]))
    else:
        flag = 'note' if note else '   '
        print('   {:34} {:>3} rows  clean  [{}]'.format(table, len(names), flag))

for m in range(4, 10):
    prod = frame('production', m)
    if prod.empty:
        continue
    warn = pd.concat([frame('warning_persistent', m),
                      frame('warning_transient', m)], ignore_index=True)
    both = B.apply_alarm_classifications(
        {'production_dedup': prod, 'warning_persistent': warn}, cls)
    prod, warn = both['production_dedup'], both['warning_persistent']
    def notexcl(df):
        return df[~df['is_excluded'].fillna(False).astype(bool)] \
            if not df.empty and 'is_excluded' in df.columns else df
    p_dedup, w_pers = notexcl(prod), notexcl(warn)
    print('\n2026-{:02d}  (production {}, warnings {})'.format(m, len(prod), len(warn)))

    # what the report now does
    p_real, fnote = _drop_general_events(p_dedup)
    w_real, wnote = _drop_general_events(w_pers)

    fs = B.build_faults_summary(p_real, ws_long=None, drop_planned=True)
    check(m, '5.1 Faults summary',
          list(fs['reason']) if not fs.empty else [], fnote)
    ws_ = B.build_faults_summary(w_real, ws_long=None, drop_planned=True)
    check(m, '5.1 Warnings summary',
          list(ws_['reason']) if not ws_.empty else [], wnote)

    bc = B.build_breakdown_candidates(p_real, ws_long=None)
    bc = pd.DataFrame(bc) if isinstance(bc, list) else bc
    check(m, '5.2 Breakdown incidents',
          list(bc['incident']) if not bc.empty else [], fnote)

    longest = (p_dedup.sort_values('duration_min', ascending=False).head(40)
               if not p_dedup.empty else p_dedup)
    lk, lnote = _drop_general_events(longest)
    check(m, 'Longest events', list(lk['Trigger name']) if not lk.empty else [], lnote)

    exc = prod[prod['is_excluded'].fillna(False).astype(bool)] \
        if 'is_excluded' in prod.columns else prod.iloc[0:0]
    ek, enote = _drop_general_events(exc)
    check(m, 'Exclusion-window listing',
          list(ek.head(25)['Trigger name']) if not ek.empty else [], enote)

    imp = B.select_important_alarms(prod)
    et = B.build_event_type_table(imp, top_n=50)
    fpart = et[~et['umbrella'].astype(bool)] if 'umbrella' in et.columns else et
    check(m, 'Important events',
          list(fpart['trigger']) if not fpart.empty else [], True)

    # nothing may be lost: kept + named must equal the source
    if not p_dedup.empty:
        gen = B.build_event_type_table(
            p_dedup[p_dedup['Trigger name'].map(is_umbrella_trigger)], top_n=50)
        n_gen = int(gen['events'].sum()) if not gen.empty else 0
        assert len(p_real) + n_gen == len(p_dedup), \
            '2026-{:02d}: events lost between table and note'.format(m)
        if n_gen:
            assert fnote, '2026-{:02d}: removed {} events without a note'.format(m, n_gen)

con.close()
print('\n' + '=' * 60)
if failures:
    print('FAILURES:')
    for f in failures:
        print('   ', f)
    raise SystemExit('a general alarm still reaches a customer table')
print('TABLES SMOKE OK  — every fault table clean in all months')

H.finish()
