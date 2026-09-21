"""A job travels: office desktop -> server -> the technician's phone -> back.

A real server on localhost, the desktop as the real sync_client (logged in as
the office admin), the technician's phone played by raw API calls under its own
account and device id — the same shape as test_sync_desktop.py.

  A1  the office assigns a record to tech1: tech1's phone pulls it, tech2's
      does not (the pull used to send only what a phone wrote itself, so a
      record written in the office reached nobody)
  A2  tech1 fills that very record in and closes it; the office pulls the
      answer back into the same row — one journal, not two
  A3  tech1 cannot hand the job to somebody else, and cannot delete it
  A4  the desktop learns who can be assigned from the server, and a technician
      may not ask for that list
"""
import _harness as H            # must be first
import os
import socket
import subprocess
import sys
import time
import uuid

import requests

import database.db_manager as dbm
from services.sync_config import sync_config
import services.sync_client as sc
import services.team_service as team
import services.work_journal_service as wj

with socket.socket() as s:
    s.bind(('127.0.0.1', 0))
    PORT = s.getsockname()[1]
URL = 'http://127.0.0.1:{}'.format(PORT)
ADMIN_PW = 'pw-' + uuid.uuid4().hex[:16]
TECH_PW = 'tp-' + uuid.uuid4().hex[:16]
env = dict(os.environ,
           DATABASE_URL='sqlite:///' + os.path.join(H.WORK, 'server.db').replace('\\', '/'),
           UPLOAD_DIR=os.path.join(H.WORK, 'uploads'), SECRET_KEY='t-' + uuid.uuid4().hex,
           FIRST_ADMIN_USERNAME='admin', FIRST_ADMIN_PASSWORD=ADMIN_PW)
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

    H.fresh_db('assign_sync.db')
    conn = dbm.get_connection()
    conn.execute("INSERT INTO projects (name, num_zones, num_blocks, num_containers) "
                 "VALUES ('TK', 1, 8, 4)")
    PID = conn.execute("SELECT id FROM projects").fetchone()[0]
    conn.commit(); conn.close()

    sc.login(URL, 'admin', ADMIN_PW)              # the office desktop
    AD = {'Authorization': 'Bearer ' + sync_config.access_token}
    for name in ('tech1', 'tech2'):
        requests.post(URL + '/auth/register', headers=AD,
                      json={'username': name, 'password': TECH_PW,
                            'role': 'technician'})

    def phone(username, device):
        d = requests.post(URL + '/auth/login', json={
            'username': username, 'password': TECH_PW, 'device_id': device}).json()
        return {'h': {'Authorization': 'Bearer ' + d['access_token']},
                'id': d['user']['id'], 'device': device}

    P1 = phone('tech1', 'phone-1')
    P2 = phone('tech2', 'phone-2')

    def pull_phone(p, since='0'):
        r = requests.get(URL + '/sync/pull', headers=p['h'],
                         params={'since': since, 'device_id': p['device']})
        return r.json()

    def push_phone(p, entry_id, version, payload):
        r = requests.post(URL + '/sync/push', headers=p['h'], json={
            'device_id': p['device'], 'idempotency_key': str(uuid.uuid4()),
            'changes': [{'entity': 'work_log', 'id': entry_id, 'action': 'upsert',
                         'version': version, 'payload': payload}]})
        return r.json()['results'][0]

    def local(eid):
        c = dbm.get_connection()
        try:
            r = c.execute("SELECT * FROM work_log_entries WHERE id=?", (eid,)).fetchone()
            return dict(r) if r else None
        finally:
            c.close()

    SETTLE = 2.3      # the server serves a row once its second is 2 s past

    print('=== A4: who the office may assign work to ===')
    n = sc.pull_users()
    names = {u['username'] for u in team.users()}
    H.check(n >= 3 and {'tech1', 'tech2', 'admin'} <= names,
            'the desktop caches the server accounts: {}'.format(sorted(names)))
    r = requests.get(URL + '/auth/assignable', headers=P1['h'])
    H.check(r.status_code == 403,
            'a technician does not get the account list ({})'.format(r.status_code))

    print('\n=== A1: the office gives a job to tech1 ===')
    key = wj.save(PID, None, date='2026-09-21', kind=wj.KIND_FAULT, block=5,
                  lc='LC1', device='BESS 3', title='Antifreeze low level',
                  work_done='Top up the coolant on BESS 3', status='Open',
                  ptw='PTW-2609-140', assignee=P1['id'], assignee_name='tech1',
                  assigned_by='admin', due='2026-09-21')
    eid = key[2:]
    st = sc.push_pending()
    H.check(st['pushed'] == 1, 'the office pushes it: {}'.format(st))
    srow = sc.get_server_entry(eid)
    H.check(srow.get('assigned_to') == P1['id'] and srow.get('assigned_name') == 'tech1'
            and srow.get('assigned_by') == 'admin' and srow.get('due_date') == '2026-09-21',
            'the server stores who it is for, who gave it and when it is due')

    time.sleep(SETTLE)
    got1 = [c for c in pull_phone(P1)['changes'] if c['id'] == eid]
    got2 = [c for c in pull_phone(P2)['changes'] if c['id'] == eid]
    H.check(len(got1) == 1,
            "tech1's phone pulls the job the office wrote (it used to reach nobody)")
    H.check(got1 and got1[0]['data']['assigned_by'] == 'admin'
            and got1[0]['data']['plant_block'] == 5,
            'with who it is from and where: {}'.format(
                got1 and (got1[0]['data']['assigned_by'], got1[0]['data']['plant_block'])))
    H.check(not got2, "tech2's phone does not see a job assigned to tech1")
    H.check(requests.get(URL + '/sync/entry/' + eid, headers=P2['h']).status_code == 404,
            'and cannot fetch it by id either')
    H.check(requests.get(URL + '/sync/entry/' + eid, headers=P1['h']).status_code == 200,
            'while tech1 can — it is their job')

    print('\n=== A2: tech1 does the work, in that same record ===')
    version = got1[0]['data']['version']
    done = push_phone(P1, eid, version, {
        'description': 'PM: coolant topped up, level checked',
        'status': 'done', 'hours': 1.5, 'time_from': '09:10', 'time_to': '10:40',
        'internal_note': 'pump was warm again', 'ptw_no': 'PTW-2609-140',
        'log_date': '2026-09-21',
    })
    H.check(done['outcome'] == 'applied',
            'the technician may fill in the job they were given: {}'.format(done))
    time.sleep(SETTLE)
    sc.pull_delta()
    back = local(eid)
    H.check(back['status'] == 'done' and back['hours'] == 1.5
            and 'coolant topped up' in back['description'],
            'the office gets the answer back: {} / {} h'.format(back['status'], back['hours']))
    H.check(back['assigned_to'] == P1['id'] and back['assigned_by'] == 'admin',
            'and the record is still tech1\'s job — the same row, not a second one')
    c = dbm.get_connection()
    n_rows = c.execute("SELECT COUNT(*) FROM work_log_entries WHERE deleted_at IS NULL"
                       ).fetchone()[0]
    c.close()
    H.check(n_rows == 1, 'one record in the journal, not two ({})'.format(n_rows))

    print('\n=== A3: the technician does the work, the office says who does it ===')
    srow = sc.get_server_entry(eid)
    steal = push_phone(P1, eid, srow['version'], {
        'description': 'PM: coolant topped up, level checked',
        'assigned_to': P2['id'], 'assigned_name': 'tech2', 'assigned_by': 'tech1',
        'log_date': '2026-09-21',
    })
    srow = sc.get_server_entry(eid)
    H.check(steal['outcome'] == 'applied' and srow['assigned_to'] == P1['id']
            and srow['assigned_name'] == 'tech1',
            'a reassignment from a phone is ignored, the edit still applies ({})'
            .format(srow['assigned_to'] == P1['id']))
    # The deadline is part of the same decision. A technician who could move
    # their own due_date could simply move a late job out of the office's view.
    moved = push_phone(P1, eid, srow['version'], {
        'description': 'PM: coolant topped up, level checked',
        'due_date': '2026-12-31', 'log_date': '2026-09-21'})
    srow = sc.get_server_entry(eid)
    H.check(moved['outcome'] == 'applied' and srow['due_date'] == '2026-09-21',
            'a technician cannot move their own deadline ({})'.format(srow['due_date']))

    dele = requests.post(URL + '/sync/push', headers=P1['h'], json={
        'device_id': P1['device'], 'idempotency_key': str(uuid.uuid4()),
        'changes': [{'entity': 'work_log', 'id': eid, 'action': 'delete',
                     'version': srow['version'], 'payload': {}}]}).json()['results'][0]
    H.check(dele['outcome'] == 'error' and not sc.get_server_entry(eid).get('deleted_at'),
            'and the job cannot be deleted from the phone: {}'.format(dele['message']))

    print('\n=== the office hands it on, and tech1 loses it ===')
    time.sleep(SETTLE)
    sc.pull_delta()                     # the office sees the phone's edit first
    wj.save(PID, key, date='2026-09-21', kind=wj.KIND_FAULT, block=5, lc='LC1',
            device='BESS 3', title='Antifreeze low level',
            work_done='PM: coolant topped up, level checked', status='Open',
            assignee=P2['id'], assignee_name='tech2', assigned_by='admin',
            due='2026-09-22')
    sc.push_pending()
    srow = sc.get_server_entry(eid)
    H.check(srow['assigned_to'] == P2['id'],
            'the office reassigns it to tech2 ({})'.format(srow['assigned_name']))
    time.sleep(SETTLE)
    later = [c for c in pull_phone(P2)['changes'] if c['id'] == eid]
    H.check(len(later) == 1, "it lands on tech2's phone")
    blocked = push_phone(P1, eid, srow['version'], {'description': 'not mine any more',
                                                    'log_date': '2026-09-21'})
    H.check(blocked['outcome'] == 'error',
            'and tech1 can no longer change it: {}'.format(blocked['message']))

    print('\n=== an older phone that knows nothing of assignment ===')
    old_id = str(uuid.uuid4())
    r = push_phone(P2, old_id, 1, {'project_id': None, 'category': 'fault',
                                   'description': 'written on an old build',
                                   'log_date': '2026-09-21'})
    H.check(r['outcome'] == 'applied', 'still writes its own records unchanged')
    srow = sc.get_server_entry(old_id)
    H.check(srow['assigned_to'] == '' and srow['due_date'] == '',
            'with no assignment on them')
    # and a technician cannot hand themselves somebody else's work
    mine = str(uuid.uuid4())
    push_phone(P2, mine, 1, {'project_id': None, 'category': 'fault',
                             'description': 'a job I gave myself',
                             'assigned_to': P1['id'], 'assigned_name': 'tech1',
                             'log_date': '2026-09-21'})
    H.check(sc.get_server_entry(mine)['assigned_to'] == '',
            'a technician cannot assign work, not even on a new record')

    print('\n=== the office is told when the server drops the assignment ===')
    # A new exe against an old server: the record is accepted and assigned_to
    # is silently dropped, so "sent to tech1" is a lie and no phone ever shows
    # the job. In the harness sync is off, so switch it on in memory only —
    # sync_config.save is neutered and nothing reaches the real config file.
    sync_config.enabled = True
    ok_key = wj.save(PID, None, date='2026-09-23', kind=wj.KIND_FAULT, block=6,
                     title='Check the coolant pump',
                     work_done='Check the coolant pump', status='Open',
                     assignee=P1['id'], assignee_name='tech1',
                     assigned_by='admin', due='2026-09-23')
    H.check(sc.verify_assignment([ok_key[2:]], P1['id']) == '',
            'a server that keeps who the job is for raises nothing')

    old_key = wj.save(PID, None, date='2026-09-24', kind=wj.KIND_FAULT, block=7,
                      title='Replace the door seal',
                      work_done='Replace the door seal', status='Open',
                      assignee=P1['id'], assignee_name='tech1',
                      assigned_by='admin', due='2026-09-24')
    sc.push_pending()
    import sqlite3
    _s = sqlite3.connect(os.path.join(H.WORK, 'server.db'))
    _s.execute("UPDATE work_log_entries SET assigned_to='' WHERE id=?",
               (old_key[2:],))
    _s.commit(); _s.close()
    msg = sc.verify_assignment([old_key[2:]], P1['id'])
    H.check('older version' in msg and 'no phone' in msg,
            'an older server that drops it is reported in plain words: "{}"'
            .format(msg))
    sync_config.enabled = False
finally:
    srv.terminate()
    try:
        srv.wait(timeout=10)
    except subprocess.TimeoutExpired:
        srv.kill()
    log.close()

H.finish()
