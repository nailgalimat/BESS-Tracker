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

print()
print('RESULT FAIL ({} check(s))'.format(len(failures)) if failures else 'RESULT PASS')
sys.stdout.flush()
os._exit(1 if failures else 0)
