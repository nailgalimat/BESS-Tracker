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

H.finish()
