"""Section 3.2 Corrective Maintenance for August 2026: exclusion windows out,
general alarms out, one row per fault, no Unclassified row.
"""
import _harness as H            # must be first: isolates DB, sync, network, history
import sys, io, os, shutil, sqlite3, warnings, datetime
warnings.filterwarnings('ignore')
import pandas as pd
import database.db_manager as dbm
from services.sync_config import sync_config
MVP, SCRATCH, copy = H.MVP, H.WORK, H.DB   # names the ported body uses
from services.asset_tree_service import is_umbrella_trigger
from services.bukhara_report_service import (load_alarm_classifications,
                                             apply_alarm_classifications,
                                             build_event_type_table)
from services.tashkent_report_service import _general_sentence, _xml

con = sqlite3.connect('file:' + H.DB.replace(os.sep, '/') + '?mode=ro', uri=True)
raw = pd.read_sql_query(
    "SELECT trigger_name AS 'Trigger name', element AS 'Element', "
    "activated AS 'Activated', deactivated AS 'Deactivation', is_excluded "
    "FROM alarm_events WHERE project_id=1 AND year=2026 AND month=8 "
    "AND category='production'", con)
con.close()

cls = load_alarm_classifications(None)
raw = apply_alarm_classifications({'production_dedup': raw},
                                  cls)['production_dedup']

# --- exactly the report's code path ---
if not raw.empty and 'is_excluded' in raw.columns:
    p = raw[~raw['is_excluded'].fillna(False).astype(bool)]
else:
    p = raw
umb = p['Trigger name'].map(is_umbrella_trigger)

def _by_reason(frame):
    if frame.empty:
        return pd.DataFrame()
    g = (frame.groupby('cls_reason')
         .agg(n_events=('cls_reason', 'size'),
              cls_resolution=('cls_resolution',
                              lambda s: (s.astype(str).replace('', pd.NA)
                                         .dropna().mode().iloc[0]
                                         if s.astype(str).replace('', pd.NA)
                                            .dropna().size else '')))
         .reset_index())
    return g.sort_values('n_events', ascending=False)

cls_summary = _by_reason(p[~umb])
note = _general_sentence(build_event_type_table(p[umb], top_n=50))

print('source production events     :', len(raw))
print('inside an exclusion window   :',
      int(raw['is_excluded'].fillna(False).astype(bool).sum()))
print('carried into section 3.2     :', len(p))

print('\n=== 3.2 Corrective Maintenance — Item / Occurrences / Resolution ===')
for _, r in cls_summary.head(7).iterrows():
    print('   {:32} {:>5}  {}'.format(str(r['cls_reason'])[:32],
                                      int(r['n_events']),
                                      (r['cls_resolution'] or '—')[:44]))
print('\n   rows in full: {} | "Unclassified" present: {}'.format(
    len(cls_summary), 'Unclassified' in set(cls_summary['cls_reason'])))
assert 'Unclassified' not in set(cls_summary['cls_reason']), \
    'a blank-resolution row still reaches the customer'
assert cls_summary['cls_reason'].duplicated().sum() == 0

print('\n=== the sentence beneath it ===')
print('   ' + note[:400])
gen_names = set(build_event_type_table(p[umb], top_n=50)['trigger'])
assert not (gen_names & set(cls_summary['cls_reason'])), 'name repeated in both'
assert '&' not in _xml(note) or '&amp;' in _xml(note)

kept = int(cls_summary['n_events'].sum())
gone = int(build_event_type_table(p[umb], top_n=50)['events'].sum())
print('\n   arithmetic: table {:,} + sentence {:,} = {:,} (carried {:,})'.format(
    kept, gone, kept + gone, len(p)))
assert kept + gone == len(p)
print('\nCM AFTER SMOKE OK')

# ── 3.2 bullet lines from the month's work records (QA H-3 / G7) ────────────
# September 2026 on the live snapshot: 17 of 18 lines used to print without a
# block (" — Antifreeze Low Level!"), the PM entry "PM activity for 3 hours per
# checklist" read as a repair, "Block 33/C3" printed a container index as if it
# were an LC, and the entry in sync conflict printed from the local copy.
import re
print('\n=== 3.2 lines from September work records (snapshot) ===')
from PyQt5.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
import ui.monthly_reports_page as mrp
page = mrp.MonthlyReportsPage()
page._pid, page._year, page._month, page._site_type = 1, 2026, 9, 'tashkent'
page._refresh_work_reports()
lines = page._wr_as_cm_lines()
rows = page._wr_rows
why = {}
for r in rows:
    k = mrp.cm_skip_reason(r)
    why[k] = why.get(k, 0) + 1
print('   records {} | printed {} | held back {}'.format(len(rows), len(lines),
                                                       {k: v for k, v in why.items() if k}))
for ln in lines[:6]:
    print('   •', ln[:110])
print('   label:', page.wr_count_lbl.text())
H.check(all(re.match(r'^Block \d+(:| —|$)', ln) for ln in lines),
        'every printed line starts with a plant block number')
H.check(not any(re.search(r'/C\d', ln) for ln in lines), 'no container index printed as "/C3"')
H.check(not any(mrp.PM_TEXT.search(ln) for ln in lines), 'no PM entry printed as corrective work')
conflicted = [r for r in rows if r.get('sync_status') == 'conflict']
H.check(all(mrp.cm_skip_reason(r) == 'conflict' for r in conflicted)
        and not any(r.get('action') and r['action'] in ln for r in conflicted for ln in lines
                    if not any(r2 is not r and r2.get('action') == r['action'] and not mrp.cm_skip_reason(r2)
                               for r2 in rows)),
        '{} record(s) in sync conflict held back'.format(len(conflicted)))
H.check(sum(why.get(k, 0) for k in ('no_block', 'pm', 'conflict', 'open')) + len(lines) == len(rows),
        'printed + held back = all records (none silently lost)')
H.check('kept out of the report' in page.wr_count_lbl.text() or not any(why.get(k) for k in why if k),
        'the page says how many were kept out and why')

print('\n=== the rule itself ===')
S = mrp.cm_skip_reason
H.check(S({'status': 'done', 'block': 33, 'sync_status': 'conflict'}) == 'conflict', 'conflict held back')
H.check(S({'status': 'done', 'block': None, 'action': 'Antifreeze Low Level!'}) == 'no_block', 'no block held back')
H.check(S({'status': 'done', 'block': 45, 'action': 'PM activity for 3 hours per checklist'}) == 'pm', 'PM held back')
H.check(S({'status': 'open', 'block': 12}) == 'open', 'open held back')
H.check(S({'status': '', 'block': 6, 'action': 'No status recorded'}) is None,
        'blank status still counts as done (user decision 2026-09-10)')
page._wr_rows = [{'src': '📱', 'block': 57, 'cont': 3, 'fault': 'LCU alarm', 'action': 'Replaced VFD1 board',
                  'sap': 'SAP-9', 'status': 'done', 'sync_status': 'synced'}]
H.check(page._wr_as_cm_lines() == ['Block 57: LCU alarm — Replaced VFD1 board [SAP-9]'],
        'line format: {}'.format(page._wr_as_cm_lines()))

H.finish()
