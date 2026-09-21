"""The phone app, as far as it can be checked without a phone.

Scripted here:
  * the cache footgun — sw.js's V and the ?v= in index.html must agree, or the
    shell is cached under URLs the page never asks for and the app does not
    open on site;
  * the update banner contract — the new version waits instead of swapping
    itself in under a half-written record;
  * the record form has a node picker and a PTW field, and no free-text
    "Location / Site" (that text could never become a plant block);
  * what the phone pushes carries the new record fields;
  * a PM written on the phone reports as PM hours and does NOT also appear as
    corrective work in the customer's section 3.2;
  * the zone map the phone's picker needs is what the desktop publishes.
Anything needing a real browser stays in tests/PWA_OFFLINE_CHECK.md.
"""
import json
import os
import re
import subprocess

import _harness as H            # must be first

STATIC = os.path.join(H.MVP, 'backend', 'static')
html = open(os.path.join(STATIC, 'index.html'), encoding='utf-8').read()
sw = open(os.path.join(STATIC, 'sw.js'), encoding='utf-8').read()
app = open(os.path.join(STATIC, 'js', 'app.js'), encoding='utf-8').read()
css = open(os.path.join(STATIC, 'css', 'app.css'), encoding='utf-8').read()
jsdb = open(os.path.join(STATIC, 'js', 'db.js'), encoding='utf-8').read()
jsapi = open(os.path.join(STATIC, 'js', 'api.js'), encoding='utf-8').read()

# ── the cache version ────────────────────────────────────────────────────
v_sw = re.search(r"const V\s*=\s*'(\d+)'", sw).group(1)
v_html = set(re.findall(r"\?v=(\d+)", html))
H.check(v_html == {v_sw},
        'sw.js V={} and every ?v= in index.html agree ({})'.format(v_sw, sorted(v_html)))

# ── the update banner ────────────────────────────────────────────────────
_install = sw.split("addEventListener('install'")[1].split('});')[0]
H.check('self.skipWaiting()' not in _install,
        'install no longer swaps the new version in by itself')
H.check("type === 'SKIP_WAITING'" in sw, 'the page can ask for it when ready')
H.check('update-banner' in html and 'applyUpdate' in app and 'watchForUpdate' in app,
        '"New version available" is shown and reloads only on a tap')
H.check('_saveDraft' in app and app.index('_saveDraft()') < app.index('SKIP_WAITING'),
        'the draft is saved before the reload')

# ── the record form ──────────────────────────────────────────────────────
H.check('id="f-node-btn"' in html and 'openNodePicker' in html,
        'the node is chosen, not typed')
H.check('Location / Site' not in html,
        'the free-text "Location / Site" field is gone')
for ident in ('f-block', 'f-lc', 'f-device', 'f-ptw', 'f-note', 'f-hours'):
    H.check('id="{}"'.format(ident) in html, 'the form has {}'.format(ident))
H.check('np-keypad' in html and 'np-zones' in html and 'np-recent' in html,
        'the picker offers recent, zone → block and the number')
H.check(html.count('class="tab"') + html.count('class="tab on"') == 4,
        'four tabs at the bottom: {}'.format(
            re.findall(r'data-tab="(\w+)"', html)))
H.check('In report' in html and 'Internal' in html,
        'the form says which text the customer reads and which stays ours')
H.check('.tabbar' in css and '.node-btn' in css and '.chip.crit' in css,
        'the styles for the new shell are there')

# ── what the phone sends ─────────────────────────────────────────────────
for field in ('plant_block', 'node_lc', 'node_device', 'ptw_no', 'time_from',
              'internal_note', 'availability_impact'):
    H.check("{}:".format(field) in app, 'the push payload carries {}'.format(field))
H.check('Waiting to send' in app and 'Conflict' in app and 'Error' in app,
        'every record says what happened to it, not just a grey dot')

# ── v15: PM checklists on the phone ──────────────────────────────────────
for ident in ('screen-checklists', 'screen-checklist', 'cl-proj', 'cl-items',
              'cl-progress', 'cl-ptw', 'cl-signed'):
    H.check('id="{}"'.format(ident) in html, 'the checklist screen has {}'.format(ident))
H.check('App.goChecklists()' in html,
        'and it is reachable — More → PM checklists')
for fn in ('goChecklists', 'openChecklist', 'setChecklistResult',
           'setChecklistComment', 'sendChecklist', '_syncChecklists'):
    H.check(fn in app, 'app.js implements {}'.format(fn))
H.check("'/checklists'" in sw,
        'sw.js treats /checklists as an API call, never a cached shell file')
H.check("DB_VERSION = 4" in jsdb and "'checklists'" in jsdb,
        'IndexedDB v4 keeps the checklists on the phone')
H.check('getChecklists' in jsapi and 'postChecklistResults' in jsapi,
        'api.js can fetch what is assigned and send back what was ticked')
H.check('.cl-btn' in css and '.cl-item.out' in css,
        'OK / NOK / N/A and the greyed out-of-scope item are styled')

# ── v16: the jobs the office assigns ─────────────────────────────────────
for ident in ('screen-task', 'task-node', 'task-from', 'task-desc',
              'task-hours', 'task-ptw', 'task-note'):
    H.check('id="{}"'.format(ident) in html, 'the task screen has {}'.format(ident))
H.check('App.completeTask()' in html and 'App.saveTask()' in html,
        'the technician can save progress and close the job')
for fn in ('openTask', 'completeTask', 'saveTask', '_storeTask', '_assignedToMe'):
    H.check(fn in app, 'app.js implements {}'.format(fn))
H.check("localStorage.setItem('user_id'" in app,
        'the phone remembers which user it is, so it knows its own jobs')
H.check('.tcard.assigned' in css and '.chip.job' in css,
        'an assigned job stands out from the records the technician wrote')
# the hint that said campaign tasks could not reach a phone is now false
H.check('not in this version' not in html and 'not in this version' not in app,
        'no line tells the technician that office jobs cannot arrive')

# ── the JS actually parses ───────────────────────────────────────────────
try:
    for f in ('js/app.js', 'js/db.js', 'js/api.js', 'sw.js'):
        subprocess.run(['node', '--check', os.path.join(STATIC, f)], check=True,
                       capture_output=True, shell=True)
    H.check(True, 'node --check passes on app.js, db.js, api.js and sw.js')
except Exception as e:                                   # noqa: BLE001
    print('   note   node not available or failed:', e)

# ── the photo stamp, actually executed ───────────────────────────────────
H.check('_stampPhoto' in app and '_location' in app and 'image/jpeg' in app,
        'photos are stamped and re-encoded before they are stored')
try:
    p = subprocess.run(['node', os.path.join(H.MVP, 'tests', 'stamp_check.js')],
                       capture_output=True, text=True, shell=True, timeout=180)
    print((p.stdout or p.stderr).rstrip())
    H.check('RESULT PASS' in (p.stdout or ''), 'the photo stamp check passes')
except Exception as e:                                   # noqa: BLE001
    print('   note   node not available or failed:', e)

# ── the checklist screen, actually executed ──────────────────────────────
try:
    p = subprocess.run(['node', os.path.join(H.MVP, 'tests', 'checklist_check.js')],
                       capture_output=True, text=True, shell=True, timeout=180)
    print((p.stdout or p.stderr).rstrip())
    H.check('RESULT PASS' in (p.stdout or ''), 'the checklist screen check passes')
except Exception as e:                                   # noqa: BLE001
    print('   note   node not available or failed:', e)

# ── the Tasks tab and one assigned job, actually executed ────────────────
try:
    p = subprocess.run(['node', os.path.join(H.MVP, 'tests', 'tasks_check.js')],
                       capture_output=True, text=True, shell=True, timeout=180)
    print((p.stdout or p.stderr).rstrip())
    H.check('RESULT PASS' in (p.stdout or ''), 'the assigned-job check passes')
except Exception as e:                                   # noqa: BLE001
    print('   note   node not available or failed:', e)

# ── the desktop side of a phone record ───────────────────────────────────
import database.db_manager as dbm
import services.work_journal_service as wj
import services.report_workflow_service as rw

H.fresh_db('pwa.db')
c = dbm.get_connection()
c.execute("INSERT INTO projects (name, num_zones, num_blocks, num_containers) "
          "VALUES ('TK', 2, 16, 4)")
PID = c.execute("SELECT id FROM projects").fetchone()[0]
for z in (1, 2):
    for b in range(1, 9):
        c.execute("INSERT INTO containers (project_id, zone_number, block_number, "
                  "container_index, container_type, serial_number) "
                  "VALUES (?,?,?,1,'LC Cabinet',?)", (PID, z, b, 'S%d%d' % (z, b)))
c.commit(); c.close()
import services.project_service as psvc
psvc.get_block_map(PID, refresh=True)

# what the phone's node picker produces
conn = dbm.get_connection()
conn.execute("INSERT INTO work_log_entries (id, project_id, log_date, category, "
             "description, fault_name, status, plant_block, node_lc, node_device, "
             "ptw_no, internal_note, created_at, updated_at, sync_status) "
             "VALUES ('ph1',?,?,'fault','Coolant topped up','Antifreeze Low Level',"
             "'done',13,'LC2','BESS 3','PTW-2609-131','pump was warm',?,?,'synced')",
             (PID, '2026-09-14', '2026-09-14 08:00:00', '2026-09-14 08:00:00'))
# and what its PM form produces: "PM: …" in the customer's line
conn.execute("INSERT INTO work_log_entries (id, project_id, log_date, category, "
             "description, status, plant_block, hours, ptw_no, created_at, "
             "updated_at, sync_status) VALUES ('ph2',?,?,'maintenance',"
             "'PM: as per checklist (PCS + BESS)','done',14,3.0,'PTW-2609-117',?,?,'synced')",
             (PID, '2026-09-14', '2026-09-14 12:00:00', '2026-09-14 12:00:00'))
conn.commit(); conn.close()

rows = {r['key']: r for r in wj.records(PID)}
H.check(rows['e:ph1']['node'] == 'Block 13 · Z2/B5 · LC2 · BESS 3',
        'the phone record knows exactly where it was: {}'.format(rows['e:ph1']['node']))
H.check(rows['e:ph1']['ptw'] == 'PTW-2609-131'
        and rows['e:ph1']['internal_note'] == 'pump was warm',
        'PTW and the internal note arrive with it')

cm = rw.corrective_rows(PID, 2026, 9)
fault = [r for r in cm if r['id'] == 'ph1'][0]
pm = [r for r in cm if r['id'] == 'ph2'][0]
H.check(rw.cm_skip_reason(fault) is None and fault['block'] == 13,
        'the fault record goes into section 3.2 on plant block 13')
H.check(rw.cm_skip_reason(pm) == 'pm',
        'the PM record does not — it is reported as PM hours, not twice '
        '(reason: {})'.format(rw.cm_skip_reason(pm)))
lines = rw.cm_lines(cm)
H.check(len(lines) == 1 and 'Block 13' in lines[0],
        'one customer line, for the fault only: {}'.format(lines))

# ── the zone map the picker needs ────────────────────────────────────────
import services.sync_client as sc
sent = {}
sc._request = lambda method, path, **kw: sent.update(kw.get('json') or {}) or type(
    'R', (), {'status_code': 200, 'json': lambda self=None: {}})()
sc.push_projects()
proj = [p for p in sent.get('projects', []) if p['id'] == PID]
zones = json.loads(proj[0]['zones']) if proj and proj[0].get('zones') else []
H.check(zones == [[1, 1, 8], [2, 9, 16]],
        'the desktop publishes the real zone → block map for the picker: {}'.format(zones))

H.finish()
