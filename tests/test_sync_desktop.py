"""Desktop sync against a real server, started here on localhost.

The phone is played by raw API calls under another device id; the desktop is
the real sync_client over a fresh database. Covers the failure sequences found
in the 2026-09-11 architecture review:

  S1  a desktop edit of a synced entry never reached the server (skipped
      forever), and a desktop delete was never sent at all
  S2  a pull — and every log-out/log-in, which re-pulls everything — deleted
      the entry's spare-parts rows (their stock-transaction links), photos and
      tags, via INSERT OR REPLACE cascading
  S3  an edit waiting to be pushed was overwritten by a pull from another device
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
import services.worklog_entry_service as wes

# ── a real server, isolated ──────────────────────────────────────────────────
with socket.socket() as s:
    s.bind(('127.0.0.1', 0))
    PORT = s.getsockname()[1]
URL = 'http://127.0.0.1:{}'.format(PORT)
ADMIN_PW = 'pw-' + uuid.uuid4().hex[:16]
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

    H.fresh_db()
    conn = dbm.get_connection()
    conn.execute("INSERT INTO projects (name, num_zones, num_blocks, num_containers) "
                 "VALUES ('Test BESS', 1, 4, 8)")
    conn.commit(); conn.close()

    sc.login(URL, 'admin', ADMIN_PW)                  # save() is neutered by the harness
    ptok = requests.post(URL + '/auth/login', json={
        'username': 'admin', 'password': ADMIN_PW, 'device_id': 'phone-P'}).json()['access_token']
    PH = {'Authorization': 'Bearer ' + ptok}

    def phone_push(eid, version, desc, tags=()):
        r = requests.post(URL + '/sync/push', headers=PH, json={
            'device_id': 'phone-P', 'idempotency_key': str(uuid.uuid4()),
            'changes': [{'entity': 'work_log', 'id': eid, 'action': 'upsert',
                         'version': version,
                         'payload': {'project_id': None, 'category': 'fault',
                                     'description': desc, 'log_date': '2026-09-11',
                                     'tags': list(tags)}}]})
        return r.json()['results'][0]

    def server(eid):
        return requests.get(URL + '/sync/entry/' + eid, headers=PH).json()

    SETTLE = 2.3      # the server serves a row once its second is 2 s past

    def pull(wait=True):
        if wait:
            time.sleep(SETTLE)
        return sc.pull_delta()

    def local(eid):
        c = dbm.get_connection()
        try:
            r = c.execute('SELECT description, version, sync_status, deleted_at '
                          'FROM work_log_entries WHERE id=?', (eid,)).fetchone()
            return dict(r) if r else None
        finally:
            c.close()

    def spare_parts(eid):
        c = dbm.get_connection()
        try:
            return c.execute('SELECT COUNT(*) FROM worklog_spare_parts WHERE work_log_id=?',
                             (eid,)).fetchone()[0]
        finally:
            c.close()

    print('=== S1: a desktop edit and a delete reach the server ===')
    e = str(uuid.uuid4())
    phone_push(e, 1, 'from the phone')
    pull()
    H.check(local(e) and local(e)['sync_status'] == 'synced', 'phone entry pulled')
    wes.update_worklog_entry(e, description='corrected on the desktop')
    H.check(local(e)['version'] == 1, 'a local edit leaves the version alone (was bumped)')
    st = sc.push_pending()
    H.check(st['pushed'] == 1 and local(e)['sync_status'] == 'synced',
            'edit pushed and applied: {}'.format(st))
    H.check(server(e)['description'] == 'corrected on the desktop', 'the server has the edit')
    H.check(local(e)['version'] == server(e)['version'],
            'local version now matches the server ({})'.format(local(e)['version']))
    wes.delete_worklog_entry(e)
    sc.push_pending()
    H.check(bool(server(e).get('deleted_at')), 'a desktop delete reaches the server (was never sent)')

    print('\n=== S2: a pull keeps the entry\'s spare parts, photos and tags ===')
    f = str(uuid.uuid4())
    phone_push(f, 1, 'pump seal', tags=['cooling'])
    pull()
    c = dbm.get_connection()
    c.execute("INSERT INTO worklog_spare_parts (work_log_id, material_number, quantity, tx_id) "
              "VALUES (?, 'MAT-1', 2, 999)", (f,))
    c.commit(); c.close()
    phone_push(f, 1, 'pump seal replaced')           # the phone edits it: server v2
    pull()
    H.check(local(f)['description'] == 'pump seal replaced', "the phone's edit arrives")
    H.check(spare_parts(f) == 1, 'spare-parts row survives the pull (was cascade-deleted)')
    sync_config.last_cursor = '0'                     # what log-out / log-in does
    pull()
    H.check(spare_parts(f) == 1, 'and survives a full re-pull')

    print('\n=== S3: an unpushed edit is not overwritten; the user settles it ===')
    for keep, g_desc in (('mine', 'desktop wins'), ('server', 'server wins')):
        g = str(uuid.uuid4())
        phone_push(g, 1, 'original')
        pull()
        wes.update_worklog_entry(g, description='desktop text')      # pending on base v1
        phone_push(g, 1, 'phone text')                              # server v2 meanwhile
        pull()
        H.check(local(g)['sync_status'] == 'conflict' and local(g)['description'] == 'desktop text',
                '[{}] pull marks a conflict and keeps the desktop text'.format(g_desc))
        msg = sc.resolve_conflict(g, keep)
        sc.push_pending()
        want = 'desktop text' if keep == 'mine' else 'phone text'
        H.check(server(g)['description'] == want and local(g)['description'] == want
                and local(g)['sync_status'] == 'synced',
                '[{}] both sides now read "{}" ({})'.format(g_desc, want, msg))

    print('\n=== the entry stuck in the live DB: old local bump, last writer the phone ===')
    k = str(uuid.uuid4())
    phone_push(k, 1, 'phone original')
    pull()
    c = dbm.get_connection()
    c.execute("UPDATE work_log_entries SET description='desktop edit', version=3, "
              "sync_status='pending' WHERE id=?", (k,))
    c.commit(); c.close()
    st = sc.push_pending()
    H.check(local(k)['sync_status'] == 'conflict' and st['conflicts'] == 1,
            'becomes a visible conflict instead of re-sending every minute')

    print('\n=== S4: a row written while the desktop pulls still arrives ===')
    n1 = str(uuid.uuid4())
    phone_push(n1, 1, 'written during the pull')
    pull(wait=False)                                   # same second as the write
    H.check(local(n1) is None, 'a row from the current second is not served yet')
    pull()
    H.check(local(n1) is not None, 'it arrives on the next pull (was lost for good)')

    print('\n=== the conflict shows on the card and is settled from it ===')
    from PyQt5.QtWidgets import QApplication, QPushButton, QMessageBox, QDialog
    app = QApplication.instance() or QApplication([])
    import ui.worklog_entry_form as wf
    q = str(uuid.uuid4())
    phone_push(q, 1, 'original')
    pull()
    wes.update_worklog_entry(q, description='desktop text')
    phone_push(q, 1, 'phone text')
    pull()
    entry = wes.get_worklog_entry(q)
    card = wf.LogCard(entry, [])
    H.check(any(b.text() == 'Resolve…' for b in card.findChildren(QPushButton)),
            'the card of a conflicted entry carries a Resolve… button')
    shown = {}
    def fake_exec(self):
        shown['texts'] = [w.text() for w in self.findChildren(wf.QLabel)]
        self.choice = 'server'
        return QDialog.Accepted
    wf.ConflictDialog.exec_ = fake_exec
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
    page = wf.FieldLogRecordsPage() if hasattr(wf, 'FieldLogRecordsPage') else None
    target = page if page is not None and hasattr(page, '_on_conflict') else None
    H.check(target is not None, 'the records page handles the Resolve… request')
    if target is not None:
        target._on_conflict(q)
        H.check('phone text' in shown.get('texts', []) and 'desktop text' in shown.get('texts', []),
                'the dialog shows both versions side by side')
        H.check(local(q)['description'] == 'phone text' and local(q)['sync_status'] == 'synced',
                'choosing the server\'s copy settles it')

    print('\n=== a full re-pull does not invent conflicts ===')
    m = str(uuid.uuid4())
    phone_push(m, 1, 'm original')
    pull()
    wes.update_worklog_entry(m, description='m edited, not pushed yet')
    sync_config.last_cursor = '0'
    pull()
    H.check(local(m)['sync_status'] == 'pending',
            'pending edit on the current server version stays pending')
finally:
    srv.terminate()
    try:
        srv.wait(timeout=10)
    except subprocess.TimeoutExpired:
        srv.kill()
    log.close()

H.finish()
