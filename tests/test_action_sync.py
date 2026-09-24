"""The action-list routes under a real server.

A real uvicorn on localhost, the desktop as the real sync_client logged in as
the office admin, the phones as raw API calls under their own accounts — the
same shape as test_checklist_server.py.

What it has to prove:

  1  an item given to tech1 reaches tech1's phone and **nobody else's**. This
     is the whole feature: an action item is a personal errand, not a job
     board, and the server — not the phone — decides who sees what.
  2  a technician cannot close an item that was given to someone else.
  3  a technician of another plant can neither list nor close this plant's
     items, and an 'engineer' (which is what the desktop's own user dialog
     hands out) cannot publish or wipe the list.
  4  an item finished at 12:30 and uploaded at 14:00 does not beat an office
     correction made at 13:00 — the server keeps the writer's own stamp.
  5  an item the server kept is NOT marked published by the desktop, or the
     office's newer copy would never be offered again.
  6  the pull cursor is (updated_at, uuid): a whole list is published in one
     second, and a timestamp-only cursor stepped over the rest of it.
"""
import _harness as H            # must be first
import datetime
import os
import socket
import subprocess
import sqlite3
import sys
import time
import uuid

import requests

import database.db_manager as dbm
from services.sync_config import sync_config
import services.sync_client as sc
import services.action_list_service as als

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

env = dict(os.environ,
           DATABASE_URL='sqlite:///' + SERVER_DB.replace('\\', '/'),
           UPLOAD_DIR=os.path.join(H.WORK, 'uploads'),
           SECRET_KEY='t-' + uuid.uuid4().hex,
           FIRST_ADMIN_USERNAME='office', FIRST_ADMIN_PASSWORD=ADMIN_PW)
log = open(os.path.join(H.WORK, 'server.log'), 'w')
srv = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'main:app',
                        '--host', '127.0.0.1', '--port', str(PORT)],
                       cwd=os.path.join(H.MVP, 'backend'), env=env,
                       stdout=log, stderr=subprocess.STDOUT)

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
    idx = [r['name'] for r in sdb.execute("PRAGMA index_list(action_items)")]
    H.check('idx_action_items_assigned' in idx,
            'the "what is assigned to me here" index exists: {}'.format(idx))

    # ── the office desktop ───────────────────────────────────────────────
    H.fresh_db('action_server.db')
    conn = dbm.get_connection()
    for nm in ('Tashkent', 'Bukhara'):
        conn.execute("INSERT INTO projects (name, num_zones, num_blocks, "
                     "num_containers, project_type) VALUES (?,1,8,4,'BESS')", (nm,))
    TK, BK = [r[0] for r in conn.execute("SELECT id FROM projects ORDER BY id")]
    conn.commit(); conn.close()

    sc.login(URL, 'office', ADMIN_PW)          # save() is neutered by the harness
    AH = {'Authorization': 'Bearer ' + sync_config.access_token}

    def account(name, role):
        requests.post(URL + '/auth/register', headers=AH, json={
            'username': name, 'password': USER_PW, 'role': role})
        d = requests.post(URL + '/auth/login', json={
            'username': name, 'password': USER_PW,
            'device_id': 'dev-' + name}).json()
        return {'h': {'Authorization': 'Bearer ' + d['access_token']},
                'id': d['user']['id'], 'device': 'dev-' + name, 'name': name}

    TECH1 = account('tech1', 'technician')
    TECH2 = account('tech2', 'technician')
    TECH_BK = account('tech_bk', 'technician')
    ENGINEER = account('eng1', 'engineer')

    def own_record(p, project_id):
        """The account writes one work record of its own, which is what places
        it at a plant (routers/sync.py decides the same way)."""
        return requests.post(URL + '/sync/push', headers=p['h'], json={
            'device_id': p['device'], 'idempotency_key': str(uuid.uuid4()),
            'changes': [{'entity': 'work_log', 'id': str(uuid.uuid4()),
                         'action': 'upsert', 'version': 1,
                         'payload': {'project_id': project_id,
                                     'category': 'fault',
                                     'description': 'a record of my own',
                                     'log_date': '2026-09-21'}}]}).json()

    own_record(TECH1, TK)
    own_record(TECH2, TK)
    own_record(TECH_BK, BK)
    own_record(ENGINEER, BK)

    # ── 1: an errand is personal ─────────────────────────────────────────
    print('\n=== 1: the item reaches the person it was given to ===')
    u_gas = als.save(TK, None, seq=8, topic='Gas sensors',
                     description='How often need to replace?',
                     todo='Confirm frequency of replacement',
                     due_date='2026-09-28',
                     assigned_to=TECH1['id'], assigned_name='tech1')
    u_hvac = als.save(TK, None, seq=5, topic='HVAC',
                      description='Need BOM and order critical spares',
                      todo='Get the BOM\nPlace order', due_date='2026-09-30',
                      assigned_to=TECH2['id'], assigned_name='tech2')
    u_none = als.save(TK, None, seq=9, topic='List of alarms for LAR',
                      todo='Submit list to NOMAC', due_date='2026-09-28')
    st = sc.push_action_items()
    H.check(st['pushed'] == 3 and st['errors'] == 0,
            'the office publishes three items: {}'.format(st))

    mine = requests.get(URL + '/action-items/assigned', headers=TECH1['h'],
                        params={'project_id': TK}).json()
    got = {i['uuid'] for i in mine['items']}
    H.check(got == {u_gas},
            "tech1's phone gets the one item that is tech1's: {}".format(
                [i['topic'] for i in mine['items']]))
    H.check(u_hvac not in got and u_none not in got,
            "and neither tech2's item nor the unassigned one")
    theirs = requests.get(URL + '/action-items/assigned', headers=TECH2['h'],
                          params={'project_id': TK}).json()
    H.check({i['uuid'] for i in theirs['items']} == {u_hvac},
            "tech2 gets exactly their own: {}".format(
                [i['topic'] for i in theirs['items']]))
    item = mine['items'][0]
    H.check(item['topic'] == 'Gas sensors' and item['due_date'] == '2026-09-28'
            and item['assigned_name'] == 'tech1' and item['seq'] == 8,
            'with the topic, the date and who it is from: {}'.format(item))

    # ── 2: closing somebody else's item ──────────────────────────────────
    print('\n=== 2: only the person it was given to may close it ===')
    r = requests.post(URL + '/action-items/' + u_hvac + '/done',
                      headers=TECH1['h'],
                      json={'done_note': 'not mine to close',
                            'updated_at': utc()})
    H.check(r.status_code == 403,
            'tech1 cannot close the item given to tech2: HTTP {}'.format(
                r.status_code))
    r = requests.post(URL + '/action-items/' + u_gas + '/done',
                      headers=TECH2['h'], json={'done_note': 'nor this way'})
    H.check(r.status_code == 403,
            'and tech2 cannot close tech1\'s: HTTP {}'.format(r.status_code))

    # ── 3: the other plant, and who may publish ──────────────────────────
    print('\n=== 3: authorisation ===')
    r = requests.get(URL + '/action-items/assigned', headers=TECH_BK['h'],
                     params={'project_id': TK})
    H.check(r.status_code == 403,
            "a technician of the other plant cannot list this plant's action "
            "list: HTTP {}".format(r.status_code))
    pulled = requests.get(URL + '/action-items', headers=TECH_BK['h'],
                          params={'since': '0'}).json()
    H.check(not pulled['items'],
            'nor pull every item of every plant: {}'.format(len(pulled['items'])))
    r = requests.put(URL + '/action-items', headers=ENGINEER['h'], json={
        'items': [{'uuid': u_gas, 'project_id': TK, 'topic': 'wiped',
                   'updated_at': utc(60), 'deleted_at': utc()}]})
    H.check(r.status_code == 403,
            'an "engineer" cannot publish or wipe the list: HTTP {}'.format(
                r.status_code))
    H.check(requests.put(URL + '/action-items', headers=AH,
                         json={'items': []}).status_code == 200,
            'the office desktop can — it signs in as the admin account')
    still = requests.get(URL + '/action-items/assigned', headers=TECH1['h'],
                         params={'project_id': TK}).json()
    H.check([i['topic'] for i in still['items']] == ['Gas sensors'],
            'and the item the engineer tried to wipe is untouched')

    # ── 4: finished at 12:30, uploaded at 14:00 ──────────────────────────
    print('\n=== 4: the office correction survives a late upload ===')
    time.sleep(1.1)
    finished_at = utc()                    # ← when the technician settled it
    time.sleep(1.1)
    als.save(TK, u_gas, due_date='2026-10-12',
             todo='Confirm frequency of replacement\nNeed the sensor code')
    sc.push_action_items()                 # ← the office corrects it at 13:00
    time.sleep(1.1)
    r = requests.post(URL + '/action-items/' + u_gas + '/done',
                      headers=TECH1['h'],
                      json={'done_note': 'Honeywell: every 2 years, code 30110',
                            'done_by': 'tech1', 'done_at': finished_at,
                            # the phone sends ISO, the server stores its own
                            # format — the two have to compare as text
                            'updated_at': finished_at.replace(' ', 'T') + '.123Z'})
    H.check(r.status_code == 200 and r.json()['updated_at'] == finished_at,
            "the server keeps the phone's own stamp, not the upload time: {}"
            .format(r.json().get('updated_at')))

    time.sleep(SETTLE)
    sc.pull_action_items()
    here = als.get(u_gas)
    H.check(here['due_date'] == '2026-10-12'
            and 'Need the sensor code' in here['todo'],
            'the office correction is still here after the phone synced later: '
            '{}'.format(here['due_date']))
    H.check(here['status'] != 'done',
            'and the late upload did not close an item the office had just '
            'rewritten (status {})'.format(here['status']))

    # ── 5: an item the server keeps is not marked published ──────────────
    print('\n=== 5: a kept item stays unsynced ===')
    u_rca = als.save(TK, None, seq=7, topic='LCU RCA', todo='Submit RCA',
                     due_date='2026-09-28', assigned_to=TECH1['id'],
                     assigned_name='tech1')
    sc.push_action_items()
    time.sleep(1.1)
    als.save(TK, u_rca, todo='Submit RCA to the customer')   # office, unsynced
    time.sleep(1.1)
    requests.post(URL + '/action-items/' + u_rca + '/done', headers=TECH1['h'],
                  json={'done_note': 'sent by mail', 'done_by': 'tech1',
                        'updated_at': utc()})
    st = sc.push_action_items()
    waiting = {i['uuid'] for i in als.items_for_sync(TK)}
    H.check(st.get('kept') == 1 and u_rca in waiting,
            'the server keeps its newer copy and the desktop leaves the item '
            'unsynced: {}'.format(st))
    time.sleep(SETTLE)
    sc.pull_action_items()
    H.check(u_rca not in {i['uuid'] for i in als.items_for_sync(TK)},
            'the next pull settles it, so it does not loop for ever')
    H.check(als.get(u_rca)['done_note'] == 'sent by mail',
            'and the completion the technician wrote is here: {!r}'.format(
                als.get(u_rca)['done_note']))

    # ── 6: a whole list shares one second ────────────────────────────────
    print('\n=== 6: the pull cursor over one second ===')
    time.sleep(3.2)
    one_second = utc(-3)                  # settled, and newer than our copies
    sdb.execute("UPDATE action_items SET updated_at=?, client_updated_at=?, "
                "status='done', done_by='tech1', done_note='closed in bulk' "
                "WHERE project_id=?", (one_second, one_second, TK))
    sdb.commit()
    n_rows = sdb.execute("SELECT COUNT(*) FROM action_items WHERE project_id=?",
                         (TK,)).fetchone()[0]
    sync_config.action_cursor = '0'
    sync_config.action_cursor_uuid = ''
    st = sc.pull_action_items()
    H.check(st['applied'] == n_rows and st['errors'] == 0,
            'every one of the {} items stamped with the same second reaches '
            'the office: {}'.format(n_rows, st))
    H.check(sync_config.action_cursor == one_second
            and sync_config.action_cursor_uuid,
            'and the cursor remembers both halves: {} / {}'.format(
                sync_config.action_cursor,
                (sync_config.action_cursor_uuid or '')[:8]))
    H.check(all(i['status'] == 'done' for i in als.items(TK)),
            'the office now sees them all closed')
    sdb.close()
finally:
    srv.terminate()
    try:
        srv.wait(timeout=10)
    except subprocess.TimeoutExpired:
        srv.kill()
    log.close()

H.finish()
