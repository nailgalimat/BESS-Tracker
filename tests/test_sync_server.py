"""Server rules for pushed work-log changes — backend only, in-process.

Not on the desktop harness: the backend's top-level packages (database,
services, models) share names with the desktop's, so the two cannot live in one
process. This test touches no desktop state; its server database is a temp file
and TestClient never opens a socket.
"""
import io
import os
import sys
import tempfile
import time
import uuid

WORK = os.path.join(tempfile.gettempdir(), 'bess_tests', '{}-sync_server-{}'.format(
    time.strftime('%Y%m%d-%H%M%S'), os.getpid()))
os.makedirs(WORK, exist_ok=True)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace',
                              line_buffering=True)
BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backend')
ADMIN_PW = 'pw-' + uuid.uuid4().hex[:16]
os.environ.update(
    DATABASE_URL='sqlite:///' + os.path.join(WORK, 'server.db').replace('\\', '/'),
    UPLOAD_DIR=os.path.join(WORK, 'uploads'),
    SECRET_KEY='test-' + uuid.uuid4().hex,
    FIRST_ADMIN_USERNAME='admin', FIRST_ADMIN_PASSWORD=ADMIN_PW)
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)
print('[server test] work dir:', WORK)

from fastapi.testclient import TestClient                           # noqa: E402
import main                                                         # noqa: E402

failures = []


def check(cond, msg):
    print(('   ok    ' if cond else '   FAIL  ') + msg)
    if not cond:
        failures.append(msg)


with TestClient(main.app) as client:
    tok = client.post('/auth/login', json={'username': 'admin', 'password': ADMIN_PW,
                                           'device_id': 'desktop-A'}).json()['access_token']
    H = {'Authorization': 'Bearer ' + tok}

    def push(device, *changes):
        r = client.post('/sync/push', headers=H, json={
            'device_id': device, 'idempotency_key': str(uuid.uuid4()),
            'changes': list(changes)})
        return r.status_code, ({x['id']: x for x in r.json()['results']}
                               if r.status_code == 200 else r.text[:200])

    def change(eid, version, tags=(), desc='x'):
        return {'entity': 'work_log', 'id': eid, 'action': 'upsert', 'version': version,
                'payload': {'project_id': None, 'category': 'fault', 'description': desc,
                            'log_date': '2026-09-11', 'tags': list(tags)}}

    def server_row(eid):
        return client.get('/sync/entry/' + eid, headers=H).json()

    print('=== one entry with "BMS, bms" must not sink the batch ===')
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    code, res = push('phone-P', change(a, 1, tags=['BMS', 'bms']), change(b, 1))
    check(code == 200, 'push answered {} (was HTTP 500 for the whole batch)'.format(code))
    if code == 200:
        check(res[a]['outcome'] == 'applied', 'the duplicate-tag entry is stored')
        check(res[b]['outcome'] == 'applied', 'the entry beside it is stored too')

    print('\n=== a desktop edit must reach the server ===')
    # Desktop creates X (server v1, last writer desktop-A). The old desktop then
    # bumped its local version on every edit, so it re-sent X as version 3.
    x = str(uuid.uuid4())
    push('desktop-A', change(x, 1, desc='created on desktop'))
    code, res = push('desktop-A', change(x, 3, desc='edited on desktop'))
    check(res[x]['outcome'] == 'applied',
          'same writer, client ahead -> applied (was "skipped" forever): {}'.format(res[x]['outcome']))
    check(server_row(x).get('description') == 'edited on desktop', 'the edit is on the server')

    print('\n=== but not over someone else\'s change ===')
    y = str(uuid.uuid4())
    push('phone-P', change(y, 1, desc='created on phone'))
    code, res = push('desktop-A', change(y, 3, desc='desktop edit, stale base'))
    check(res[y]['outcome'] == 'conflict',
          'other writer, client ahead -> conflict (was "skipped"): {}'.format(res[y]['outcome']))
    check(bool(res[y].get('server_row')), 'the conflict carries the server row')
    check(server_row(y).get('description') == 'created on phone', 'the phone\'s text is intact')

    print('\n=== ordinary fast-forward and server-ahead still behave ===')
    code, res = push('desktop-A', change(y, 1, desc='desktop edit, right base'))
    check(res[y]['outcome'] == 'applied' and res[y].get('server_version') == 2,
          'equal versions -> applied, server v2')
    code, res = push('phone-P', change(y, 1, desc='phone edit, stale base'))
    check(res[y]['outcome'] == 'conflict', 'server ahead -> conflict')

    print('\n=== reading one entry back ===')
    r = client.get('/worklogs/' + y, headers=H)
    check(r.status_code == 200, 'GET /worklogs/{{id}} of a phone entry (no project): {} (was 500)'
          .format(r.status_code))
    row = server_row(y)
    check(all(k in row for k in ('fault_name', 'status', 'sap_ticket', 'spare_parts', 'tags',
                                 'version')), '/sync/entry carries the full pull shape')
    check(client.get('/sync/entry/nope', headers=H).status_code == 404, 'unknown id -> 404')

    print('\n=== phone events: old payloads still accepted, the desktop decides ===')
    # PWA v11 and older send these shapes; the new desktop queues or refuses
    # them, but the server must keep taking them during the rollout.
    old = [
        {'id': str(uuid.uuid4()), 'project_id': 1, 'kind': 'pm', 'blocks': '',
         'date_from': '2026-09-10', 'date_to': '2026-09-10', 'hours': 0,
         'exclusion_type': '', 'description': 'PM without block', 'created_at': '2026-09-10T08:00:00.000Z'},
        {'id': str(uuid.uuid4()), 'project_id': 1, 'kind': 'excluded', 'blocks': '1-70',
         'date_from': '2026-09-19', 'date_to': '2026-09-20', 'hours': 3,
         'exclusion_type': 'Grid Outage', 'description': ''},
        {'id': str(uuid.uuid4()), 'project_id': 1, 'kind': 'counts', 'blocks': 'abc',
         'date_from': '2026-09-12', 'date_to': '2026-09-12', 'hours': 5},
    ]
    codes = [client.post('/events', headers=H, json=e).status_code for e in old]
    check(codes == [200, 200, 200], 'POST /events with v11 payloads: {}'.format(codes))
    again = client.post('/events', headers=H, json=old[0])
    check(again.status_code == 200 and again.json()['id'] == old[0]['id'], 'idempotent by id')
    time.sleep(2.2)
    got = {e['id']: e for e in client.get('/events', params={'since': '0'}, headers=H).json()['events']}
    check(all(e['id'] in got for e in old) and got[old[0]['id']]['blocks'] == ''
          and got[old[2]['id']]['blocks'] == 'abc',
          'GET /events returns them as sent (fields unchanged)')

    print('\n=== a partial payload MERGES: an absent key is "no opinion" ===')
    # This is what lets a phone correct one field of its own record without
    # wiping the lines the office wrote on it while the phone was offline.
    # A QA pass once found exactly that loss on a version conflict.
    m = str(uuid.uuid4())
    push('desktop-A', {
        'entity': 'work_log', 'id': m, 'action': 'upsert', 'version': 1,
        'payload': {'project_id': None, 'category': 'fault',
                    'description': 'written by the office',
                    'fault_name': 'Fuse blown', 'status': 'open',
                    'sap_ticket': 'SAP-1', 'spare_parts': 'office: FU-400A x2',
                    'site_location': 'Z2', 'equipment_serial': 'SER-9',
                    'log_date': '2026-09-11', 'plant_block': 5,
                    'node_lc': 'LC1', 'node_device': 'BESS 2',
                    'ptw_no': 'PTW-1', 'internal_note': 'office: spare ordered',
                    'hours': 2.5, 'availability_impact': 'none',
                    'tags': ['cooling'], 'deleted_at': None}})
    before = server_row(m)
    # the phone corrects one field and sends that field alone
    code, res = push('phone-P', {
        'entity': 'work_log', 'id': m, 'action': 'upsert',
        'version': before['version'],
        'payload': {'fault_name': 'Fuse blown — 400 A DC'}})
    check(res[m]['outcome'] == 'applied',
          'a one-field payload is applied: {}'.format(res[m]['outcome']))
    after = server_row(m)
    check(after['fault_name'] == 'Fuse blown — 400 A DC',
          'the corrected field is stored: {}'.format(after['fault_name']))
    kept = {k: (before[k], after[k]) for k in before
            if k not in ('fault_name', 'updated_at', 'version', 'origin_device')
            and before[k] != after[k]}
    check(not kept, 'and NOTHING else moved: {}'.format(kept))
    check(after['internal_note'] == 'office: spare ordered'
          and after['spare_parts'] == 'office: FU-400A x2'
          and after['description'] == 'written by the office'
          and after['hours'] == 2.5 and after['tags'] == ['cooling'],
          "every line the office wrote is still there")
    check(after['deleted_at'] is None,
          'a payload with no deleted_at key does not un-delete or delete')

    # and a soft delete with no other key still deletes
    code, res = push('phone-P', {'entity': 'work_log', 'id': m,
                                 'action': 'delete',
                                 'version': after['version'], 'payload': {}})
    check(res[m]['outcome'] == 'applied' and bool(server_row(m).get('deleted_at')),
          'a delete is still a delete')

    print('\n=== the closed months: sent AND settled with the customer ===')
    # report_months.locked_at exists only on the desktop, so the desktop
    # publishes the list with the project and the phone reads the mirror.
    # "" is "nobody has told me", "[]" is "told: none closed". A month whose
    # report has been SENT but not yet settled with the customer is not on the
    # list — corrections are expected during that gap and nothing warns.
    def put_projects(projects):
        return client.put('/projects', headers=H, json={'projects': projects})

    r = put_projects([{'id': 41, 'name': 'TK', 'project_type': 'BESS',
                       'num_blocks': 16, 'zones': '[[1,1,8],[2,9,16]]',
                       'locked_months': '["2026-08"]'}])
    check(r.status_code == 200, 'PUT /projects with locked_months: {}'.format(r.status_code))
    got = {p['id']: p for p in client.get('/projects', headers=H).json()}
    check(got[41]['locked_months'] == '["2026-08"]',
          'the phone is told which months are closed: {}'.format(got[41]['locked_months']))

    # an OLDER desktop that knows nothing about the field must still work, and
    # must not blank what a newer one published: absent key = no opinion
    r = put_projects([{'id': 41, 'name': 'TK', 'project_type': 'BESS',
                       'num_blocks': 16, 'zones': '[[1,1,8],[2,9,16]]'}])
    got = {p['id']: p for p in client.get('/projects', headers=H).json()}
    check(r.status_code == 200 and got[41]['name'] == 'TK',
          'an older desktop can still publish the project list')
    check(got[41]['locked_months'] == '["2026-08"]',
          'and does not erase the months it does not know about: {}'
          .format(got[41]['locked_months']))

    # a project nobody has published months for reads as "" — unknown, which
    # the phone stays quiet about rather than warning on everything
    put_projects([{'id': 41, 'name': 'TK'}, {'id': 42, 'name': 'Bukhara'}])
    got = {p['id']: p for p in client.get('/projects', headers=H).json()}
    check(got[42]['locked_months'] == '',
          'a project never told about them reads "" — not "[]", and not a '
          'month list: {!r}'.format(got[42]['locked_months']))
    put_projects([{'id': 41, 'name': 'TK', 'locked_months': '[]'}])
    got = {p['id']: p for p in client.get('/projects', headers=H).json()}
    check(got[41]['locked_months'] == '[]',
          'and "[]" survives as itself — the two answers stay apart')

print()
print('RESULT FAIL ({} check(s))'.format(len(failures)) if failures else 'RESULT PASS')
sys.stdout.flush()
os._exit(1 if failures else 0)
