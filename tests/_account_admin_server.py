"""Server half of test_account_admin.py — runs in its own process.

The backend's top-level packages (database, services, models) share their names
with the desktop's, so the two cannot live in one interpreter: the harness
imports the desktop ones. The parent test therefore launches this file as a
child process, and it reports its checks back as JSON:

    python tests/_account_admin_server.py <work dir> <results.json>

Everything is local: DATABASE_URL is a fresh file in the parent's work dir,
SECRET_KEY is random per run, and TestClient never opens a socket. The live
server is never contacted.
"""
import io
import json
import os
import sys
import uuid

WORK, OUT = sys.argv[1], sys.argv[2]
BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backend')
ADMIN_PW = 'pw-' + uuid.uuid4().hex[:16]
FIELD_PW = 'fp-' + uuid.uuid4().hex[:16]

# Set before importing the app: config.py reads os.environ at import time, and
# load_dotenv() does not override what is already set.
os.environ.update(
    DATABASE_URL='sqlite:///' + os.path.join(WORK, 'accounts.db').replace('\\', '/'),
    UPLOAD_DIR=os.path.join(WORK, 'uploads'),
    SECRET_KEY='test-' + uuid.uuid4().hex,
    FIRST_ADMIN_USERNAME='admin', FIRST_ADMIN_PASSWORD=ADMIN_PW)
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace',
                                  line_buffering=True)
sys.path.insert(0, BACKEND)
os.chdir(WORK)                      # keeps bess_server.log out of the repo

from fastapi.testclient import TestClient                            # noqa: E402
import main                                                          # noqa: E402

results = []


def check(cond, msg):
    cond = bool(cond)
    print(('   ok    ' if cond else '   FAIL  ') + msg)
    results.append([cond, msg])
    return cond


def hdr(tok):
    return {'Authorization': 'Bearer ' + tok}


# /auth/login is rate limited to 10/minute per client address and TestClient is
# always one address, failed attempts included — so this file logs in 5 times.
with TestClient(main.app) as c:
    admin = c.post('/auth/login', json={'username': 'admin', 'password': ADMIN_PW,
                                        'device_id': 'desktop-A'}).json()
    A = hdr(admin['access_token'])
    check(admin['user'].get('is_active') is True,
          '/auth/login still returns the user, now with is_active')

    def mk(name, role):
        r = c.post('/auth/register', headers=A, json={
            'username': name, 'password': FIELD_PW, 'role': role})
        return r.json()['id'] if r.status_code == 201 else None

    f1 = mk('field1', 'engineer')
    f2 = mk('field2', 'engineer')
    check(bool(f1 and f2), 'two engineer accounts created')

    # ── the lockout guard, while admin is the only active admin ──────────────
    print('\n=== the last active admin cannot be deactivated or demoted ===')
    r = c.patch('/auth/users/' + admin['user']['id'], headers=A, json={'is_active': False})
    check(r.status_code == 400, 'deactivate the last admin -> 400 (got {})'.format(r.status_code))
    check('own admin account' in r.text or 'last active administrator' in r.text,
          'and says why: {}'.format(r.text[:90]))
    r = c.patch('/auth/users/' + admin['user']['id'], headers=A, json={'role': 'engineer'})
    check(r.status_code == 400, 'demote the last admin -> 400 (got {})'.format(r.status_code))
    me = c.get('/auth/me', headers=A).json()
    check(me['role'] == 'admin' and me['is_active'] is True,
          'the admin is untouched by both refusals')

    # ── deactivation ends the session, not just the next login ──────────────
    print('\n=== deactivating field1 ===')
    lg = c.post('/auth/login', json={'username': 'field1', 'password': FIELD_PW,
                                     'device_id': 'phone-1'}).json()
    tok1, rt1 = lg['access_token'], lg['refresh_token']
    rr = c.post('/auth/refresh', json={'refresh_token': rt1, 'device_id': 'phone-1'})
    check(rr.status_code == 200, 'field1 can renew its session before deactivation')
    rt2 = rr.json()['refresh_token']

    r = c.patch('/auth/users/' + f1, headers=A, json={'is_active': False})
    check(r.status_code == 200, 'PATCH is_active=false -> 200 (got {})'.format(r.status_code))
    body = r.json() if r.status_code == 200 else {}
    check(body.get('is_active') is False, 'the answer says the account is inactive')
    check(int(body.get('revoked_sessions') or 0) >= 1,
          'it revoked {} outstanding session(s)'.format(body.get('revoked_sessions')))

    r = c.post('/auth/login', json={'username': 'field1', 'password': FIELD_PW})
    check(r.status_code == 401, 'field1 can no longer log in (got {})'.format(r.status_code))
    r = c.post('/auth/refresh', json={'refresh_token': rt2, 'device_id': 'phone-1'})
    check(r.status_code == 401,
          "field1's year-long refresh token is rejected (got {})".format(r.status_code))
    # Every authenticated route re-loads the user with is_active == True
    # (dependencies.get_current_user), so the access token it still holds is
    # dead too — the cut-off is immediate, not 30 minutes later.
    r = c.get('/auth/me', headers=hdr(tok1))
    check(r.status_code == 401,
          'the access token it already held is refused as well (got {})'.format(r.status_code))

    print('\n=== an inactive account is still visible, but not assignable ===')
    users = {u['username']: u for u in c.get('/auth/users', headers=A).json()}
    check('field1' in users and users['field1']['is_active'] is False,
          '/auth/users shows field1 as inactive (an admin must see it to bring it back)')
    names = [u['username'] for u in c.get('/auth/assignable', headers=A).json()]
    check('field1' not in names and 'field2' in names,
          '/auth/assignable drops it: {}'.format(names))

    print('\n=== reactivation restores the login ===')
    r = c.patch('/auth/users/' + f1, headers=A, json={'is_active': True})
    check(r.status_code == 200 and r.json()['is_active'] is True, 'PATCH is_active=true -> 200')
    r = c.post('/auth/login', json={'username': 'field1', 'password': FIELD_PW})
    check(r.status_code == 200, 'field1 can log in again (got {})'.format(r.status_code))
    names = [u['username'] for u in c.get('/auth/assignable', headers=A).json()]
    check('field1' in names, 'and is assignable again')

    # ── role change ─────────────────────────────────────────────────────────
    print('\n=== changing a role changes the permissions ===')
    lg2 = c.post('/auth/login', json={'username': 'field2', 'password': FIELD_PW,
                                      'device_id': 'phone-2'}).json()
    F2 = hdr(lg2['access_token'])
    check(c.get('/auth/assignable', headers=F2).status_code == 200,
          'as an engineer, field2 may read /auth/assignable')

    r = c.patch('/auth/users/' + f2, headers=A, json={'role': 'technician'})
    check(r.status_code == 200 and r.json()['role'] == 'technician',
          'PATCH role=technician -> 200 (got {})'.format(r.status_code))
    r = c.get('/auth/assignable', headers=F2)
    check(r.status_code == 403,
          'the same token is now refused by /auth/assignable: {} (403, not 500)'
          .format(r.status_code))

    r = c.patch('/auth/users/' + f2, headers=A, json={'role': 'wizard'})
    check(r.status_code == 400, 'an unknown role -> 400 (got {})'.format(r.status_code))
    check(c.get('/auth/users', headers=A).json() and
          [u for u in c.get('/auth/users', headers=A).json()
           if u['username'] == 'field2'][0]['role'] == 'technician',
          'and the role is unchanged by the refusal')

    r = c.patch('/auth/users/' + f2, headers=A, json={'role': 'engineer'})
    check(r.status_code == 200 and c.get('/auth/assignable', headers=F2).status_code == 200,
          'moved back to engineer, the same token works again '
          '(the role is read from the account, not the token)')

    # ── only an admin may change an account ─────────────────────────────────
    print('\n=== a non-admin cannot change anybody ===')
    r = c.patch('/auth/users/' + f1, headers=F2, json={'is_active': False})
    check(r.status_code == 403, 'engineer -> 403 (got {})'.format(r.status_code))
    c.patch('/auth/users/' + f2, headers=A, json={'role': 'technician'})
    r = c.patch('/auth/users/' + f1, headers=F2, json={'role': 'admin'})
    check(r.status_code == 403, 'technician -> 403 (got {})'.format(r.status_code))
    check(c.get('/auth/users', headers=F2).status_code == 403,
          'and still cannot list the accounts')
    r = c.patch('/auth/users/' + f1, json={'is_active': False})
    check(r.status_code in (401, 403),
          'no token at all -> {} (not 500)'.format(r.status_code))
    check([u for u in c.get('/auth/users', headers=A).json()
           if u['username'] == 'field1'][0]['is_active'] is True,
          'field1 is still active after the three refusals')

    # ── recovery: a second admin can fix the first one's role ───────────────
    print('\n=== with two admins, an admin may be demoted (this is the fix path) ===')
    r = c.patch('/auth/users/' + f2, headers=A, json={'role': 'admin'})
    check(r.status_code == 200 and c.get('/auth/users', headers=F2).status_code == 200,
          'promoted to admin, field2 may now list the accounts')
    r = c.patch('/auth/users/' + admin['user']['id'], headers=F2, json={'role': 'engineer'})
    check(r.status_code == 200, 'field2 demotes the original admin -> 200 (not over-blocked)')
    r = c.patch('/auth/users/' + admin['user']['id'], headers=F2, json={'role': 'admin'})
    check(r.status_code == 200, 'and puts it back')
    r = c.patch('/auth/users/' + f2, headers=F2, json={'is_active': False})
    check(r.status_code == 400, 'an admin still cannot deactivate itself (got {})'
          .format(r.status_code))
    r = c.patch('/auth/users/' + f2, headers=A, json={'role': 'engineer'})
    check(r.status_code == 200, 'the other admin can demote it, though')

    # ── odds and ends ───────────────────────────────────────────────────────
    print('\n=== bad requests ===')
    check(c.patch('/auth/users/no-such-id', headers=A,
                  json={'is_active': False}).status_code == 404, 'unknown user -> 404')
    check(c.patch('/auth/users/' + f1, headers=A, json={}).status_code == 400,
          'an empty change -> 400')
    r = c.patch('/auth/users/' + f1, headers=A, json={'is_active': False})
    r = c.patch('/auth/users/' + f1, headers=A, json={'is_active': False})
    check(r.status_code == 200 and int(r.json()['revoked_sessions']) == 0,
          'deactivating twice is harmless, second time nothing left to revoke')

with open(OUT, 'w', encoding='utf-8') as f:
    json.dump(results, f)
print('\nwrote {} check(s)'.format(len(results)))
