"""The checklist routes under a real server, against the things a QA pass
found wrong with them.

A real uvicorn on localhost, the desktop as the real sync_client logged in as
the office admin, the phones as raw API calls under their own accounts — the
same shape as test_checklist_sync.py, which covers the happy path. This one
covers the failures:

  H1  a checklist filled at 12:30 and uploaded at 14:00 used to beat an office
      correction made at 13:00, because the server stamped it at upload time.
      And a run the server kept was marked published anyway, so the office
      copy was never offered again.
  H2  /checklists/assigned stopped at 200 runs with a bare .limit(): three
      checklists on each of 70 blocks is 210, so whole blocks reached no phone
      and nothing said so.
  H3  no authorisation at all: a technician of another plant could read and
      write this plant's checklists, and 'engineer' — which is what the
      desktop's user dialog hands out — could delete a whole campaign.
  M4  the pull cursor moved strictly by updated_at, and a publish stamps a
      whole batch with one second: everything else written in that second was
      stepped over and never reached the office.
  L5  idx_wle_assigned_updated_at was only ever created on a fresh table, so
      no existing server had it. The server here starts against a
      work_log_entries table that predates the assignment work.
"""
import _harness as H            # must be first
import datetime
import os
import socket
import sqlite3
import subprocess
import sys
import time
import uuid

import requests

import database.db_manager as dbm
from services.sync_config import sync_config
import services.sync_client as sc
import services.checklist_pm_service as cs

SETTLE = 2.3      # the server serves a row once its second is 2 s past


def utc(offset=0):
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(seconds=offset)).strftime('%Y-%m-%d %H:%M:%S')


with socket.socket() as s:
    s.bind(('127.0.0.1', 0))
    PORT = s.getsockname()[1]
URL = 'http://127.0.0.1:{}'.format(PORT)
ADMIN_PW = 'pw-' + uuid.uuid4().hex[:16]
USER_PW = 'tp-' + uuid.uuid4().hex[:16]
SERVER_DB = os.path.join(H.WORK, 'server.db')

# ── L5: the server starts against a table that predates the assignment ──
# create_all only makes indexes for tables it creates, so on every server that
# already had work_log_entries the technician's half of the delta pull —
# WHERE assigned_to=? AND updated_at > ? — ran unindexed.
_legacy = sqlite3.connect(SERVER_DB)
_legacy.executescript("""
    CREATE TABLE work_log_entries (
        id               VARCHAR NOT NULL PRIMARY KEY,
        user_id          VARCHAR NOT NULL,
        project_id       INTEGER,
        container_id     INTEGER,
        equipment_serial VARCHAR,
        site_location    VARCHAR,
        category         VARCHAR,
        description      TEXT,
        log_date         VARCHAR NOT NULL,
        created_at       VARCHAR NOT NULL,
        updated_at       VARCHAR NOT NULL,
        deleted_at       VARCHAR,
        version          INTEGER NOT NULL DEFAULT 1,
        origin_device    VARCHAR,
        sync_status      VARCHAR
    );
""")
_legacy.commit(); _legacy.close()

env = dict(os.environ,
           DATABASE_URL='sqlite:///' + SERVER_DB.replace('\\', '/'),
           UPLOAD_DIR=os.path.join(H.WORK, 'uploads'), SECRET_KEY='t-' + uuid.uuid4().hex,
           FIRST_ADMIN_USERNAME='office', FIRST_ADMIN_PASSWORD=ADMIN_PW)
log = open(os.path.join(H.WORK, 'server.log'), 'w')
srv = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1',
                        '--port', str(PORT)], cwd=os.path.join(H.MVP, 'backend'),
                       env=env, stdout=log, stderr=subprocess.STDOUT)

try:
    for _ in range(60):
        try:
            if requests.get(URL + '/healthz', timeout=1).status_code == 200:
                break
        except requests.RequestException:
            time.sleep(0.5)
    else:
        H.check(False, 'test server did not start (see server.log)')
        H.finish()

    sdb = sqlite3.connect(SERVER_DB)
    sdb.row_factory = sqlite3.Row
    idx = [r['name'] for r in sdb.execute("PRAGMA index_list(work_log_entries)")]
    H.check('idx_wle_assigned_updated_at' in idx,
            'the assignment index is added to a table that already existed: {}'
            .format(idx))

    # ── the office desktop ───────────────────────────────────────────────
    H.fresh_db('cl_server.db')
    conn = dbm.get_connection()
    for nm in ('Tashkent', 'Bukhara'):
        conn.execute("INSERT INTO projects (name, num_zones, num_blocks, "
                     "num_containers, project_type) VALUES (?,1,8,4,'BESS')", (nm,))
    TK, BK = [r[0] for r in conn.execute("SELECT id FROM projects ORDER BY id")]
    cur = conn.execute(
        "INSERT INTO checklist_templates (uuid, name, description, container_type, "
        "scope, project_id, kind, source_file, source_sha, progress_row, updated_at) "
        "VALUES (?,'01) PCS Checklist','','PCS','block',?,'PCS','(none)','',20,"
        "datetime('now'))", (str(uuid.uuid4()), TK))
    TID = cur.lastrowid
    for n, (no, text) in enumerate((('1', 'Check the cabinet door seals'),
                                    ('2', 'Check the cooling fans'),
                                    ('3', 'Check the RMU'))):
        conn.execute(
            "INSERT INTO checklist_items (template_id, order_num, category, "
            "description, expected, s_no, equipment, excel_row, added) "
            "VALUES (?,?,'Visual',?,'',?,'PCS',?,0)", (TID, n, text, no, 8 + n))
    conn.commit(); conn.close()
    ITEMS = cs.items(TID)

    sc.login(URL, 'office', ADMIN_PW)              # save() is neutered by the harness
    AH = {'Authorization': 'Bearer ' + sync_config.access_token}

    def account(name, role):
        requests.post(URL + '/auth/register', headers=AH, json={
            'username': name, 'password': USER_PW, 'role': role})
        d = requests.post(URL + '/auth/login', json={
            'username': name, 'password': USER_PW,
            'device_id': 'dev-' + name}).json()
        return {'h': {'Authorization': 'Bearer ' + d['access_token']},
                'id': d['user']['id'], 'device': 'dev-' + name, 'name': name}

    TECH_TK = account('tech_tk', 'technician')
    TECH_BK = account('tech_bk', 'technician')
    ENGINEER = account('eng1', 'engineer')

    def own_record(p, project_id):
        """The account writes one work record of its own, which is what places
        it at a plant (routers/sync.py decides the same way)."""
        return requests.post(URL + '/sync/push', headers=p['h'], json={
            'device_id': p['device'], 'idempotency_key': str(uuid.uuid4()),
            'changes': [{'entity': 'work_log', 'id': str(uuid.uuid4()),
                         'action': 'upsert', 'version': 1,
                         'payload': {'project_id': project_id, 'category': 'fault',
                                     'description': 'a record of my own',
                                     'log_date': '2026-09-21'}}]}).json()

    own_record(TECH_TK, TK)
    own_record(TECH_BK, BK)
    own_record(ENGINEER, BK)

    # ── H1: the office correction wins over a late upload ────────────────
    print('\n=== H1: filled at 12:30, uploaded at 14:00 ===')
    u12 = cs.plan_runs(TK, TID, [12], '2026-09-21', campaign='PM Sep 2026')[0]
    st = sc.push_checklists()
    H.check(st['pushed'] == 1 and st['errors'] == 0,
            'the desktop publishes the checklist: {}'.format(st))

    time.sleep(1.1)                       # the stamps are per second
    filled_at = utc()                     # ← when the technician ticked it
    answers = {str(ITEMS[0]['id']): {'result': cs.NOK,
                                     'comment': 'BESS 3: door seal torn'}}

    time.sleep(1.1)
    cs.save_results(u12, {ITEMS[0]['id']: {'result': cs.NOK,
                                           'comment': 'BESS 3: seal replaced, closed'}},
                    source='desktop')     # ← the office corrects it at 13:00

    time.sleep(1.1)                       # ← the phone finds signal at 14:00
    r = requests.post(URL + '/checklists/runs/' + u12 + '/results',
                      headers=TECH_TK['h'],
                      json={'results': answers, 'status': 'Done',
                            'filled_by': 'tech_tk',
                            # the phone sends ISO, the server stores its own
                            # format — the two have to compare as text
                            'updated_at': filled_at.replace(' ', 'T') + '.123Z'})
    H.check(r.status_code == 200 and r.json()['updated_at'] == filled_at,
            'the server keeps the phone\'s own stamp, not the upload time: {}'
            .format(r.json().get('updated_at')))

    time.sleep(SETTLE)
    sc.pull_checklists()
    nok = [i for i in cs.run_detail(u12)['items'] if i['result'] == cs.NOK]
    H.check(len(nok) == 1 and 'seal replaced' in nok[0]['comment'],
            'the office correction survives a phone that synced later: "{}"'
            .format(nok[0]['comment'] if nok else '-'))

    # and the office copy does go back up, because the run was never marked
    # published while the server was holding its own newer one
    st = sc.push_checklists()
    back = requests.get(URL + '/checklists/assigned', headers=TECH_TK['h'],
                        params={'project_id': TK}).json()
    srv_run = [x for x in back['runs'] if x['uuid'] == u12][0]
    H.check('seal replaced' in srv_run['results'][str(ITEMS[0]['id'])]['comment'],
            'and reaches the phone on its next refresh ({})'.format(st))

    print('\n=== H1: a run the server keeps is not marked published ===')
    u13 = cs.plan_runs(TK, TID, [13], '2026-09-21', campaign='PM Sep 2026')[0]
    sc.push_checklists()
    time.sleep(1.1)
    cs.save_results(u13, {ITEMS[1]['id']: {'result': cs.OK, 'comment': 'office'}},
                    source='desktop')     # the office writes, unsynced
    time.sleep(1.1)
    requests.post(URL + '/checklists/runs/' + u13 + '/results',
                  headers=TECH_TK['h'],
                  json={'results': {str(ITEMS[1]['id']): {'result': cs.NOK,
                                                          'comment': 'from site'}},
                        'filled_by': 'tech_tk', 'updated_at': utc()})
    st = sc.push_checklists()
    waiting = {r['uuid'] for r in cs.runs_for_sync(TK)}
    H.check(st.get('kept') == 1 and u13 in waiting,
            'the server keeps its newer copy and the desktop leaves the run '
            'unsynced: {}'.format(st))
    time.sleep(SETTLE)
    sc.pull_checklists()
    H.check(u13 not in {r['uuid'] for r in cs.runs_for_sync(TK)},
            'the next pull settles it, so it does not loop for ever')

    # ── H3: who may read and write a plant's checklists ──────────────────
    print('\n=== H3: authorisation ===')
    r = requests.get(URL + '/checklists/assigned', headers=TECH_BK['h'],
                     params={'project_id': TK})
    H.check(r.status_code == 403,
            "a technician of the other plant cannot list this plant's "
            "checklists: HTTP {}".format(r.status_code))
    r = requests.post(URL + '/checklists/runs/' + u12 + '/results',
                      headers=TECH_BK['h'],
                      json={'results': {str(ITEMS[0]['id']):
                                        {'result': cs.OK, 'comment': 'not mine'}}})
    H.check(r.status_code == 403,
            'nor write on one of them: HTTP {}'.format(r.status_code))
    pulled = requests.get(URL + '/checklists/runs', headers=TECH_BK['h'],
                          params={'since': '0'}).json()
    H.check(r.status_code == 403 and not pulled['runs'],
            'nor pull every run of every plant: {} run(s)'.format(len(pulled['runs'])))
    H.check(requests.get(URL + '/checklists/assigned', headers=TECH_TK['h'],
                         params={'project_id': TK}).status_code == 200,
            'while the technician who works here still gets them')

    r = requests.put(URL + '/checklists/runs', headers=ENGINEER['h'], json={
        'runs': [{'uuid': u12, 'project_id': TK, 'template_uuid': 'x',
                  'plant_block': 12, 'results': {}, 'updated_at': utc(60),
                  'deleted_at': utc()}]})
    H.check(r.status_code == 403,
            'an "engineer" — what the desktop\'s user dialog hands out — '
            'cannot delete a campaign: HTTP {}'.format(r.status_code))
    H.check(requests.put(URL + '/checklists/templates', headers=ENGINEER['h'],
                         json={'templates': []}).status_code == 403,
            'nor publish templates')
    H.check(requests.put(URL + '/checklists/templates', headers=AH,
                         json={'templates': []}).status_code == 200,
            'the office desktop still can — it signs in as the admin account')
    H.check(requests.get(URL + '/checklists/assigned', headers=TECH_TK['h'],
                         params={'project_id': TK}).json()['runs'],
            'and the campaign the engineer tried to delete is still there')

    # ── H2: every checklist reaches the phone, not the first 200 ─────────
    print('\n=== H2: 280 checklists, one phone ===')
    many = cs.plan_runs(TK, TID, list(range(20, 300)), '2026-09-22',
                        campaign='PM Sep 2026 — all blocks')
    st = sc.push_checklists()
    H.check(len(many) == 280 and st['errors'] == 0,
            'the office publishes {} checklists: {}'.format(len(many), st))

    first = requests.get(URL + '/checklists/assigned', headers=TECH_TK['h'],
                         params={'project_id': TK}).json()
    H.check(len(first['runs']) == 200 and first['has_more'] is True
            and first['cursor'],
            'one page is 200 and the answer SAYS there is more: {} / {}'
            .format(len(first['runs']), first['has_more']))

    seen, after, pages = set(), '', 0
    while pages < 20:
        pages += 1
        got = requests.get(URL + '/checklists/assigned', headers=TECH_TK['h'],
                           params={'project_id': TK, 'after': after}).json()
        seen.update(r['uuid'] for r in got['runs'])
        after = got['cursor']
        if not got['has_more']:
            break
    H.check(set(many) <= seen,
            'the phone loops the pages and gets every block: {} of {} run(s) '
            'in {} page(s)'.format(len(seen), len(many) + 2, pages))

    # ── M4: a whole publish shares one second ────────────────────────────
    print('\n=== M4: the pull cursor over one second ===')
    time.sleep(3.2)
    one_second = utc(-3)                  # settled, and newer than our copies
    # every row is made to differ from the desktop's copy, so a run that
    # reaches the office is one that was actually applied
    sdb.execute("UPDATE checklist_runs SET updated_at=?, client_updated_at=?, "
                "filled_by='tech_tk', ptw_no='PTW-ALL' WHERE project_id=?",
                (one_second, one_second, TK))
    sdb.commit()
    n_rows = sdb.execute("SELECT COUNT(*) FROM checklist_runs WHERE project_id=?",
                         (TK,)).fetchone()[0]
    sync_config.checklist_cursor = '0'
    sync_config.checklist_cursor_uuid = ''
    st = sc.pull_checklists()
    H.check(st['applied'] == n_rows and st['errors'] == 0,
            'every one of the {} runs stamped with the same second reaches the '
            'office: {}'.format(n_rows, st))
    H.check(sync_config.checklist_cursor == one_second
            and sync_config.checklist_cursor_uuid,
            'and the cursor remembers both halves: {} / {}'.format(
                sync_config.checklist_cursor,
                (sync_config.checklist_cursor_uuid or '')[:8]))
    sdb.close()
finally:
    srv.terminate()
    try:
        srv.wait(timeout=10)
    except subprocess.TimeoutExpired:
        srv.kill()
    log.close()

H.finish()
