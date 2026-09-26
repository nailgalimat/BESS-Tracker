"""
ui/user_management_dialog.py
-----------------------------
Dialog for creating, listing and administering sync users.

Creating calls POST /auth/register; changing an account (its role, or whether
it is active at all) calls PATCH /auth/users/{id}. Both are admin-only on the
server, and the account controls here stay disabled until the server has
actually answered the admin-only user list — that answer is the proof of the
role, the desktop does not decide it locally.

There is no delete: an account that leaves is deactivated, because old work
records name their author. After a change the local mirror of the server's
accounts (`sync_users`, the "Assigned to" picker) is refreshed straight away
through sync_client.pull_users(), so the operator does not have to run a sync
and guess whether it took.
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QGroupBox, QDialogButtonBox,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QColor

from services.sync_config import sync_config

# What the server accepts (routers/auth.py ROLES) and what each role may do —
# the wording the confirmation shows, so the operator is told what they are
# about to hand over.
ROLE_LABELS = (
    ("Technician", "technician"),
    ("Engineer",   "engineer"),
    ("Admin",      "admin"),
)
ROLE_MEANING = {
    "technician": "sees only their own records and the jobs assigned to them",
    "engineer":   "plans work, assigns jobs, sees the whole plant",
    "admin":      "everything an engineer may do, plus creating and "
                  "administering accounts",
}


# ── Background threads ────────────────────────────────────────────────────────

class _RegisterThread(QThread):
    success = pyqtSignal(dict)
    failure = pyqtSignal(str)

    def __init__(self, username, password, role):
        super().__init__()
        self._username = username
        self._password = password
        self._role     = role

    def run(self):
        try:
            import requests
            resp = requests.post(
                f"{sync_config.server_url}/auth/register",
                json={"username": self._username,
                      "password": self._password,
                      "role":     self._role},
                headers={"Authorization": f"Bearer {sync_config.access_token}"},
                timeout=10,
            )
            if resp.status_code in (200, 201):
                self.success.emit(resp.json())
            else:
                try:
                    msg = resp.json().get("detail", f"HTTP {resp.status_code}")
                except Exception:
                    msg = f"HTTP {resp.status_code}"
                self.failure.emit(msg)
        except Exception as ex:
            self.failure.emit(str(ex))


class _UpdateUserThread(QThread):
    """PATCH /auth/users/{id} — the role, the active flag, or both.

    Same shape as _RegisterThread: the network never runs on the UI thread, and
    the server's `detail` is what the operator is shown."""

    success = pyqtSignal(dict)
    failure = pyqtSignal(str)

    def __init__(self, user_id, payload):
        super().__init__()
        self._user_id = user_id
        self._payload = dict(payload)

    def run(self):
        try:
            import requests
            resp = requests.patch(
                f"{sync_config.server_url}/auth/users/{self._user_id}",
                json=self._payload,
                headers={"Authorization": f"Bearer {sync_config.access_token}"},
                timeout=10,
            )
            if resp.status_code == 200:
                self.success.emit(resp.json())
                return
            try:
                detail = str(resp.json().get("detail", ""))
            except Exception:
                detail = ""
            if resp.status_code == 405 or (resp.status_code == 404
                                           and "user" not in detail.lower()):
                # The route itself is missing — an older server, where this
                # path is simply not routed. The backend has to be deployed.
                self.failure.emit(
                    "This sync server does not support account changes yet "
                    "(HTTP {}). The newer backend has to be deployed first."
                    .format(resp.status_code))
            else:
                self.failure.emit(detail or f"HTTP {resp.status_code}")
        except Exception as ex:
            self.failure.emit(str(ex))


class _ListUsersThread(QThread):
    # (message, HTTP status) — 403 means "not an admin", which the dialog has
    # to tell apart from "the server is unreachable".
    success = pyqtSignal(list)
    failure = pyqtSignal(str, int)

    def run(self):
        try:
            import requests
            resp = requests.get(
                f"{sync_config.server_url}/auth/users",
                headers={"Authorization": f"Bearer {sync_config.access_token}"},
                timeout=10,
            )
            if resp.status_code == 200:
                self.success.emit(resp.json())
            else:
                self.failure.emit(f"HTTP {resp.status_code}", resp.status_code)
        except Exception as ex:
            self.failure.emit(str(ex), 0)


class _MirrorUsersThread(QThread):
    """Re-pull /auth/assignable into `sync_users` after a change, so the local
    picker agrees with the server without waiting for the next sync."""

    done = pyqtSignal(int)

    def run(self):
        try:
            from services import sync_client
            self.done.emit(sync_client.pull_users())
        except Exception:
            self.done.emit(0)


# ── Dialog ────────────────────────────────────────────────────────────────────

class UserManagementDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("User Management")
        self.setMinimumSize(640, 560)
        self._reg_thread    = None
        self._list_thread   = None
        self._upd_thread    = None
        self._mirror_thread = None
        # Every worker started stays referenced until it has finished: dropping
        # the last reference to a QThread that is still running aborts the whole
        # application ("QThread: Destroyed while thread is still running"), and
        # two clicks in a row used to do exactly that.
        self._threads: list = []
        # None = not known yet, True/False = the server has answered. The
        # account controls are enabled only on True.
        self._is_admin = None
        self._rows     = []          # the user dicts, by table row
        self._combo_for = None       # (user id, role) the role combo was built for
        self._build_ui()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        title = QLabel("👥  Field Engineer Accounts")
        title.setFont(QFont("Segoe UI", 13, QFont.Bold))
        lay.addWidget(title)

        if not sync_config.is_configured():
            warn = QLabel("⚠️  Sync not configured. Go to Sync Settings first.")
            warn.setStyleSheet("color:#C62828; padding:12px;")
            lay.addWidget(warn)
            close = QDialogButtonBox(QDialogButtonBox.Close)
            close.rejected.connect(self.accept)
            lay.addWidget(close)
            return

        # ── Create user ──────────────────────────────────────────────────────
        grp = QGroupBox("Create New User")
        gf  = QFormLayout()
        gf.setSpacing(6)

        self._uname = QLineEdit()
        self._uname.setPlaceholderText("engineer1")
        gf.addRow("Username:", self._uname)

        self._passwd = QLineEdit()
        self._passwd.setEchoMode(QLineEdit.Password)
        self._passwd.setPlaceholderText("min 6 characters")
        gf.addRow("Password:", self._passwd)

        # Technician is what a phone account normally is — the server has always
        # understood the role (a technician sees only their own records and the
        # jobs assigned to them, see routers/sync.py and team_service) but this
        # dialog did not offer it, so there was no way to create one. Default
        # stays Engineer.
        self._role = QComboBox()
        self._role.addItem("Engineer",   "engineer")
        self._role.addItem("Admin",      "admin")
        self._role.addItem("Technician", "technician")
        gf.addRow("Role:", self._role)

        grp.setLayout(gf)
        lay.addWidget(grp)

        btn_row = QHBoxLayout()
        self._create_btn = QPushButton("➕  Create User")
        self._create_btn.setStyleSheet(
            "QPushButton{background:#1976D2;color:white;border-radius:4px;"
            "padding:6px 16px;font-weight:bold;}"
            "QPushButton:hover{background:#1565C0;}"
            "QPushButton:disabled{background:#B0BEC5;}"
        )
        self._create_btn.clicked.connect(self._do_create)
        btn_row.addWidget(self._create_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        self._status = QLabel("")
        self._status.setStyleSheet("color:#555; font-style:italic;")
        self._status.setWordWrap(True)
        lay.addWidget(self._status)

        # ── User list ────────────────────────────────────────────────────────
        list_grp = QGroupBox("Existing Users")
        lg       = QVBoxLayout(list_grp)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["Username", "Role", "Status", "Email", "Created"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setSelectionMode(QTableWidget.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.itemSelectionChanged.connect(self._refresh_controls)
        lg.addWidget(self._table)

        # ── Change the selected account ──────────────────────────────────────
        self._sel_label = QLabel("Select an account above.")
        self._sel_label.setStyleSheet("color:#555;")
        lg.addWidget(self._sel_label)

        act = QHBoxLayout()
        act.addWidget(QLabel("Role:"))
        self._edit_role = QComboBox()
        for label, value in ROLE_LABELS:
            self._edit_role.addItem(label, value)
        act.addWidget(self._edit_role)

        self._apply_role_btn = QPushButton("Change role")
        self._apply_role_btn.clicked.connect(self._do_change_role)
        act.addWidget(self._apply_role_btn)

        self._toggle_btn = QPushButton("Deactivate")
        self._toggle_btn.clicked.connect(self._do_toggle_active)
        act.addWidget(self._toggle_btn)

        act.addStretch()
        refresh_btn = QPushButton("🔄 Refresh List")
        refresh_btn.clicked.connect(self._load_users)
        act.addWidget(refresh_btn)
        lg.addLayout(act)

        self._gate_label = QLabel(
            "Changing an account needs an admin login — checking…")
        self._gate_label.setStyleSheet("color:#555; font-style:italic;")
        self._gate_label.setWordWrap(True)
        lg.addWidget(self._gate_label)

        list_grp.setLayout(lg)
        lay.addWidget(list_grp)

        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.accept)
        lay.addWidget(close)

        self._refresh_controls()
        self._load_users()

    # ── Create ────────────────────────────────────────────────────────────────

    def _do_create(self):
        username = self._uname.text().strip()
        password = self._passwd.text()
        role     = self._role.currentData()

        if not username:
            self._set_status("❌  Username is required.", error=True)
            return
        if len(password) < 6:
            self._set_status("❌  Password must be at least 6 characters.", error=True)
            return

        self._create_btn.setEnabled(False)
        self._set_status("Creating user…")

        self._reg_thread = self._keep(_RegisterThread(username, password, role))
        self._reg_thread.success.connect(self._on_create_ok)
        self._reg_thread.failure.connect(self._on_create_err)
        self._reg_thread.start()

    def _on_create_ok(self, user: dict):
        self._create_btn.setEnabled(True)
        self._set_status(
            f"✅  User '{user['username']}' created with role '{user['role']}'.",
            error=False
        )
        self._uname.clear()
        self._passwd.clear()
        self._load_users()
        self._start_mirror()

    def _on_create_err(self, msg: str):
        self._create_btn.setEnabled(True)
        self._set_status(f"❌  {msg}", error=True)

    # ── List ──────────────────────────────────────────────────────────────────

    def _load_users(self):
        self._table.setRowCount(0)
        self._rows = []
        self._refresh_controls()
        self._list_thread = self._keep(_ListUsersThread())
        self._list_thread.success.connect(self._on_users_loaded)
        self._list_thread.failure.connect(self._on_users_failed)
        self._list_thread.start()

    def _on_users_loaded(self, users: list):
        # Only an admin is served /auth/users, so a 200 here is the role check.
        self._is_admin = True
        me = (sync_config.username or "").strip().lower()
        self._rows = []
        self._table.setRowCount(len(users))
        for i, u in enumerate(users):
            active = bool(u.get("is_active", True))
            own    = bool(me) and (u.get("username") or "").strip().lower() == me
            self._rows.append({
                "id":       str(u.get("id") or ""),
                "username": u.get("username") or "",
                "role":     (u.get("role") or "").strip().lower(),
                "active":   active,
                "own":      own,
            })
            name = (u.get("username") or "") + ("  (you)" if own else "")
            self._table.setItem(i, 0, QTableWidgetItem(name))
            role_item = QTableWidgetItem(u.get("role", ""))
            if (u.get("role") or "") == "admin":
                role_item.setForeground(Qt.darkRed)
            self._table.setItem(i, 1, role_item)
            st = QTableWidgetItem("Active" if active else "Inactive — no access")
            st.setForeground(QColor("#2E7D32") if active else QColor("#C62828"))
            self._table.setItem(i, 2, st)
            self._table.setItem(i, 3, QTableWidgetItem(u.get("email") or ""))
            self._table.setItem(i, 4, QTableWidgetItem(
                (u.get("created_at") or "")[:16]
            ))
            if not active:
                # Shown, never hidden: an admin has to see the account to
                # bring it back, and a hidden row looks deleted.
                for c in range(self._table.columnCount()):
                    item = self._table.item(i, c)
                    f = item.font()
                    f.setItalic(True)
                    item.setFont(f)
        self._refresh_controls()

    def _on_users_failed(self, msg: str, code: int):
        if code == 403:
            self._is_admin = False
        self._refresh_controls()
        self._set_status(f"Could not load users: {msg}", error=True)

    # ── Change role / active ──────────────────────────────────────────────────

    def _selected(self):
        row = self._table.currentRow()
        if row < 0 or row >= len(self._rows):
            return None
        return self._rows[row]

    def _refresh_controls(self):
        if not hasattr(self, "_toggle_btn"):
            return
        u    = self._selected()
        busy = self._upd_thread is not None and self._upd_thread.isRunning()
        can  = (self._is_admin is True) and u is not None and not u["own"] and not busy

        self._apply_role_btn.setEnabled(can)
        self._toggle_btn.setEnabled(can)
        self._edit_role.setEnabled(can)
        self._toggle_btn.setText(
            "Reactivate" if (u and not u["active"]) else "Deactivate")

        if u is None:
            self._combo_for = None       # a reselect rebuilds, never shows a stale pick
            self._sel_label.setText("Select an account above.")
        else:
            self._sel_label.setText(
                "Selected: <b>{}</b> — role '{}', {}.".format(
                    u["username"], u["role"] or "(none)",
                    "active" if u["active"] else "inactive"))
            # Offer the account's own role even if the server holds a role this
            # build does not know, so "Change role" never silently proposes one.
            # Only rebuilt when the selected account changes: a refresh in the
            # middle of choosing must not quietly reset the operator's pick.
            key = (u["id"], u["role"])
            if key != self._combo_for:
                self._combo_for = key
                self._edit_role.blockSignals(True)
                self._edit_role.clear()
                for label, value in ROLE_LABELS:
                    self._edit_role.addItem(label, value)
                if u["role"] and self._edit_role.findData(u["role"]) < 0:
                    self._edit_role.addItem(u["role"], u["role"])
                idx = self._edit_role.findData(u["role"])
                self._edit_role.setCurrentIndex(max(idx, 0))
                self._edit_role.blockSignals(False)

        if self._is_admin is False:
            self._gate_label.setText(
                "⚠️  This login is not an admin — the server refuses account "
                "changes, so the controls stay disabled.")
        elif self._is_admin is None:
            self._gate_label.setText(
                "Changing an account needs an admin login — checking…")
        elif u is not None and u["own"]:
            self._gate_label.setText(
                "This is your own account. You cannot change your own role or "
                "deactivate yourself — the server refuses it, so that the last "
                "admin cannot lock everybody out. Ask another admin.")
        else:
            self._gate_label.setText(
                "Deactivating is how access is revoked — no account is ever "
                "deleted, because old records name their author.")

    def _do_change_role(self):
        u = self._selected()
        if not u:
            return
        new_role = self._edit_role.currentData()
        if not new_role or new_role == u["role"]:
            self._set_status("Role unchanged — pick a different role first.")
            return
        text = (
            "Change '{name}' from '{old}' to '{new}'?\n\n"
            "'{new}' {meaning}.\n\n"
            "The new role applies on their next request to the server — the "
            "server reads the role from the account on every call, so they do "
            "not have to sign in again.\n\n"
            "What they already recorded is untouched, and work already assigned "
            "to them stays assigned."
        ).format(name=u["username"], old=u["role"] or "(none)", new=new_role,
                 meaning=ROLE_MEANING.get(new_role, "has the permissions of that role"))
        if QMessageBox.question(self, "Change role", text,
                                QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No) != QMessageBox.Yes:
            return
        self._send_update(u, {"role": new_role},
                          "Changing role of '{}'…".format(u["username"]))

    def _do_toggle_active(self):
        u = self._selected()
        if not u:
            return
        if u["active"]:
            text = (
                "Deactivate '{name}'?\n\n"
                "• They can no longer log in, on the phone or the desktop.\n"
                "• Every session they hold is revoked, so no device can renew "
                "its login — on this server a phone's login would otherwise "
                "stay valid for a year.\n"
                "• It takes effect at once: the server checks the account on "
                "every request, so the session their phone is holding right "
                "now stops working too.\n"
                "• Anything that phone recorded and has not synced yet will "
                "no longer reach the office. If there may be unsent work on "
                "it, let it sync first.\n"
                "• They disappear from the 'Assigned to' picker, so no new work "
                "can be given to them.\n\n"
                "Nothing they recorded is deleted and old records keep their "
                "name. You can reactivate the account here at any time."
            ).format(name=u["username"])
            title, payload = "Deactivate account", {"is_active": False}
        else:
            text = (
                "Reactivate '{name}'?\n\n"
                "They will be able to log in again with their existing "
                "password, and work can be assigned to them again. They have "
                "to sign in once on each device: the sessions revoked when the "
                "account was deactivated stay revoked."
            ).format(name=u["username"])
            title, payload = "Reactivate account", {"is_active": True}
        if QMessageBox.question(self, title, text,
                                QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No) != QMessageBox.Yes:
            return
        self._send_update(u, payload, "{} '{}'…".format(
            "Deactivating" if u["active"] else "Reactivating", u["username"]))

    def _send_update(self, u: dict, payload: dict, busy_msg: str):
        self._set_status(busy_msg)
        self._upd_thread = self._keep(_UpdateUserThread(u["id"], payload))
        self._upd_thread.success.connect(self._on_update_ok)
        self._upd_thread.failure.connect(self._on_update_err)
        self._upd_thread.finished.connect(self._refresh_controls)
        self._upd_thread.start()
        self._refresh_controls()

    def _on_update_ok(self, user: dict):
        active  = bool(user.get("is_active", True))
        revoked = int(user.get("revoked_sessions") or 0)
        if active:
            msg = "✅  '{}' is active, role '{}'.".format(
                user.get("username", ""), user.get("role", ""))
        else:
            msg = ("✅  '{}' deactivated — {} signed-in session(s) revoked, "
                   "immediately. Unsynced work still on their phone will not "
                   "arrive.".format(user.get("username", ""), revoked))
        self._set_status(msg)
        self._load_users()
        self._start_mirror()

    def _on_update_err(self, msg: str):
        self._set_status("❌  {}".format(msg), error=True)
        self._refresh_controls()

    # ── Local mirror ──────────────────────────────────────────────────────────

    def _start_mirror(self):
        """Bring `sync_users` — what the 'Assigned to' picker reads — in line
        with the server now, instead of at the next sync."""
        self._mirror_thread = self._keep(_MirrorUsersThread())
        self._mirror_thread.done.connect(self._on_mirror_done)
        self._mirror_thread.start()

    def _on_mirror_done(self, n: int):
        if n:
            self._set_status(self._status.text() +
                             "  (local user list updated: {} active account(s))"
                             .format(n))
        else:
            self._set_status(self._status.text() +
                             "  (local user list will update on the next sync)")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _keep(self, thread):
        """Hold a reference to a started worker until it is done."""
        self._threads = [t for t in self._threads if not t.isFinished()]
        self._threads.append(thread)
        return thread

    def _set_status(self, msg: str, error: bool = False):
        color = "#C62828" if error else "#2E7D32"
        self._status.setStyleSheet(f"color:{color};")
        self._status.setText(msg)
