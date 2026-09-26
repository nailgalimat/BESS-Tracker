"""Account administration: revoking access and changing a role.

Until now the server could only create, log in and list users, so a person who
left the company kept syncing — this deployment's refresh tokens last 365 days
— and the four field accounts were stuck on 'engineer' because nothing could
move them.

The server half runs in a child process (_account_admin_server.py): the
backend's `database` / `services` / `models` packages share their names with the
desktop's, which the harness has already imported, so the two cannot live in one
interpreter. Its checks are replayed here, so a server failure is reported as a
failure of this test.

The desktop half drives ui/user_management_dialog.py with its network threads
replaced by recorders — no socket is opened, and what the dialog *would* PATCH
is asserted instead.
"""
import _harness as H            # must be first: isolates DB, sync, network, history
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# ── Server: TestClient against the backend app, in its own process ───────────

print('=== server: PATCH /auth/users/{id} ===')
OUT = os.path.join(H.WORK, 'server_checks.json')
proc = subprocess.run(
    [sys.executable, os.path.join(HERE, '_account_admin_server.py'), H.WORK, OUT],
    capture_output=True, text=True, encoding='utf-8', errors='replace',
    env=dict(os.environ), timeout=300)
for line in (proc.stdout or '').splitlines():
    print(line)
if os.path.exists(OUT):
    with open(OUT, encoding='utf-8') as f:
        for ok, msg in json.load(f):
            H.check(ok, 'server: ' + msg)
else:
    H.check(False, 'the server checks ran at all')
    for line in ((proc.stdout or '') + (proc.stderr or '')).splitlines()[-25:]:
        print('      ' + line)

# ── Desktop: the dialog's own controls ───────────────────────────────────────

from PyQt5.QtWidgets import QApplication, QMessageBox                # noqa: E402
from PyQt5.QtCore import QThread, pyqtSignal                         # noqa: E402

app = QApplication([])

from services.sync_config import sync_config                         # noqa: E402

# The dialog only builds its controls for a configured sync. Nothing listens on
# this port and every network thread below is replaced, so no request is made.
sync_config.server_url   = 'http://127.0.0.1:9'
sync_config.access_token = 'test-token'
sync_config.username     = 'admin'          # who is logged in -> the "(you)" row

import ui.user_management_dialog as umd                              # noqa: E402

REAL_MIRROR = umd._MirrorUsersThread
SENT = []


class _NoList(QThread):
    success = pyqtSignal(list)
    failure = pyqtSignal(str, int)

    def run(self):
        pass


class _RecordUpdate(QThread):
    success = pyqtSignal(dict)
    failure = pyqtSignal(str)

    def __init__(self, user_id, payload):
        super().__init__()
        SENT.append((user_id, dict(payload)))

    def run(self):
        pass


class _NoMirror(QThread):
    done = pyqtSignal(int)

    def run(self):
        pass


umd._ListUsersThread  = _NoList
umd._UpdateUserThread = _RecordUpdate
umd._MirrorUsersThread = _NoMirror

USERS = [
    {'id': 'u-admin', 'username': 'admin', 'role': 'admin', 'email': 'a@x.com',
     'created_at': '2026-01-01 08:00:00', 'is_active': True},
    {'id': 'u-eng', 'username': 'field1', 'role': 'engineer', 'email': None,
     'created_at': '2026-02-01 08:00:00', 'is_active': True},
    {'id': 'u-gone', 'username': 'field2', 'role': 'engineer', 'email': None,
     'created_at': '2026-03-01 08:00:00', 'is_active': False},
]

print('\n=== desktop: the dialog builds, and is gated until the server says admin ===')
dlg = umd.UserManagementDialog()
H.check(dlg._table.columnCount() == 5,
        'the list has a Status column: {}'.format(
            [dlg._table.horizontalHeaderItem(c).text()
             for c in range(dlg._table.columnCount())]))
H.check(not dlg._apply_role_btn.isEnabled() and not dlg._toggle_btn.isEnabled(),
        'before the admin-only list answers, both controls are disabled')

dlg._on_users_loaded(USERS)
H.check(dlg._is_admin is True and dlg._table.rowCount() == 3,
        'a 200 from the admin-only /auth/users is the admin proof; 3 rows shown')
H.check('Inactive' in dlg._table.item(2, 2).text(),
        'the inactive account is shown, not hidden: "{}"'.format(dlg._table.item(2, 2).text()))

print('\n=== desktop: selection drives the controls ===')
dlg._table.selectRow(1)
H.check(dlg._apply_role_btn.isEnabled() and dlg._toggle_btn.isEnabled(),
        'another account selected -> role and active controls enabled')
H.check(dlg._toggle_btn.text() == 'Deactivate' and
        dlg._edit_role.currentData() == 'engineer',
        'the buttons follow the row: "{}" / role {}'.format(
            dlg._toggle_btn.text(), dlg._edit_role.currentData()))

dlg._table.selectRow(0)
H.check(not dlg._apply_role_btn.isEnabled() and not dlg._toggle_btn.isEnabled(),
        'your own account: you cannot demote or deactivate yourself here')
H.check('own account' in dlg._gate_label.text(), 'and it says why')

dlg._table.selectRow(2)
H.check(dlg._toggle_btn.text() == 'Reactivate',
        'an inactive account offers Reactivate')

print('\n=== desktop: every change is confirmed first ===')
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
dlg._table.selectRow(1)
dlg._do_toggle_active()
dlg._do_change_role()
H.check(SENT == [], 'answering No sends nothing')

QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
dlg._do_toggle_active()
H.check(SENT == [('u-eng', {'is_active': False})],
        'Deactivate -> PATCH is_active=false: {}'.format(SENT))

del SENT[:]
idx = dlg._edit_role.findData('technician')
dlg._edit_role.setCurrentIndex(idx)
dlg._do_change_role()
H.check(SENT == [('u-eng', {'role': 'technician'})],
        'Change role -> PATCH role=technician: {}'.format(SENT))

del SENT[:]
dlg._edit_role.setCurrentIndex(dlg._edit_role.findData('engineer'))
dlg._do_change_role()
H.check(SENT == [] and 'unchanged' in dlg._status.text(),
        'picking the role it already has sends nothing')

del SENT[:]
dlg._table.selectRow(2)
dlg._do_toggle_active()
H.check(SENT == [('u-gone', {'is_active': True})],
        'Reactivate -> PATCH is_active=true: {}'.format(SENT))

print('\n=== desktop: what the operator is told, and the local mirror ===')
dlg._on_update_ok({'username': 'field1', 'role': 'engineer', 'is_active': False,
                   'revoked_sessions': 2})
txt = dlg._status.text()
H.check('2 signed-in session(s) revoked' in txt, 'the status reports the revoked sessions')
H.check('30 min' in txt or '30 minutes' in txt or 'immediate' in txt,
        'and when it takes effect: "{}"'.format(txt))

import services.sync_client as sc                                    # noqa: E402
import services.team_service as ts                                   # noqa: E402

pulled = []
sc.pull_users = lambda: (pulled.append(1), 2)[1]
REAL_MIRROR().run()          # the real mirror thread body, in this thread
H.check(pulled == [1],
        "the dialog's mirror refresh goes through sync_client.pull_users()")

ts.save_users([{'id': 'm-a', 'username': 'mir_a', 'role': 'engineer'},
               {'id': 'm-b', 'username': 'mir_b', 'role': 'engineer'}])
ts.save_users([{'id': 'm-a', 'username': 'mir_a', 'role': 'technician'}])
rows = {r['id']: r for r in ts.users(active_only=False)}
H.check(rows.get('m-a', {}).get('role') == 'technician',
        'a role change on the server reaches sync_users')
H.check(rows.get('m-b', {}).get('is_active') == 0,
        'an account dropped from /auth/assignable becomes inactive locally, not deleted')

print('\n=== desktop: a non-admin login never gets the controls ===')
dlg._is_admin = False
dlg._refresh_controls()
H.check(not dlg._apply_role_btn.isEnabled() and not dlg._toggle_btn.isEnabled()
        and not dlg._edit_role.isEnabled(),
        'a 403 from /auth/users disables role and active controls')
H.check('not an admin' in dlg._gate_label.text(), 'and says so: ' + dlg._gate_label.text())

H.finish()
