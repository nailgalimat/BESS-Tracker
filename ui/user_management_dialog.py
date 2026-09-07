"""
ui/user_management_dialog.py
-----------------------------
Dialog for creating and listing sync users (engineers).
Calls POST /auth/register on the configured sync server.
Requires admin role.
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QGroupBox, QDialogButtonBox,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont

from services.sync_config import sync_config


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


class _ListUsersThread(QThread):
    success = pyqtSignal(list)
    failure = pyqtSignal(str)

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
                self.failure.emit(f"HTTP {resp.status_code}")
        except Exception as ex:
            self.failure.emit(str(ex))


# ── Dialog ────────────────────────────────────────────────────────────────────

class UserManagementDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("User Management")
        self.setMinimumSize(560, 480)
        self._reg_thread  = None
        self._list_thread = None
        self._build_ui()
        self._check_configured()

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

        self._role = QComboBox()
        self._role.addItem("Engineer", "engineer")
        self._role.addItem("Admin",    "admin")
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
        lay.addWidget(self._status)

        # ── User list ────────────────────────────────────────────────────────
        list_grp = QGroupBox("Existing Users")
        lg       = QVBoxLayout(list_grp)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Username", "Role", "Email", "Created"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        lg.addWidget(self._table)

        refresh_btn = QPushButton("🔄 Refresh List")
        refresh_btn.clicked.connect(self._load_users)
        lg.addWidget(refresh_btn, alignment=Qt.AlignLeft)

        list_grp.setLayout(lg)
        lay.addWidget(list_grp)

        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.accept)
        lay.addWidget(close)

        # Load users immediately
        self._load_users()

    def _check_configured(self):
        if not sync_config.is_configured():
            return
        # disable if not admin
        # (server will reject anyway, but give early feedback)

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

        self._reg_thread = _RegisterThread(username, password, role)
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

    def _on_create_err(self, msg: str):
        self._create_btn.setEnabled(True)
        self._set_status(f"❌  {msg}", error=True)

    # ── List ──────────────────────────────────────────────────────────────────

    def _load_users(self):
        self._table.setRowCount(0)
        self._list_thread = _ListUsersThread()
        self._list_thread.success.connect(self._on_users_loaded)
        self._list_thread.failure.connect(
            lambda msg: self._set_status(f"Could not load users: {msg}", error=True)
        )
        self._list_thread.start()

    def _on_users_loaded(self, users: list):
        self._table.setRowCount(len(users))
        for i, u in enumerate(users):
            self._table.setItem(i, 0, QTableWidgetItem(u.get("username", "")))
            role_item = QTableWidgetItem(u.get("role", ""))
            if u.get("role") == "admin":
                role_item.setForeground(Qt.darkRed)
            self._table.setItem(i, 1, role_item)
            self._table.setItem(i, 2, QTableWidgetItem(u.get("email") or ""))
            self._table.setItem(i, 3, QTableWidgetItem(
                (u.get("created_at") or "")[:16]
            ))

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _set_status(self, msg: str, error: bool = False):
        color = "#C62828" if error else "#2E7D32"
        self._status.setStyleSheet(f"color:{color};")
        self._status.setText(msg)
