"""
ui/sync_settings_dialog.py
---------------------------
Dialog for configuring the sync server connection.
  - Server URL + username + password
  - Test connection button
  - Enable/disable auto-sync toggle
  - Shows last sync time + pending count
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QCheckBox,
    QGroupBox, QDialogButtonBox, QMessageBox,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont

from services.sync_config import sync_config
from database.db_manager import get_connection


# ── Background login thread (avoid freezing UI) ───────────────────────────────

class _LoginThread(QThread):
    success = pyqtSignal(dict)
    failure = pyqtSignal(str)

    def __init__(self, url, username, password):
        super().__init__()
        self._url      = url
        self._username = username
        self._password = password

    def run(self):
        try:
            from services.sync_client import login
            user = login(self._url, self._username, self._password)
            self.success.emit(user)
        except Exception as ex:
            self.failure.emit(str(ex))


class _SyncThread(QThread):
    """Runs a full sync off the UI thread.

    Never call sync_now() inline from a slot: it performs a dozen network
    requests (projects, stock, push, delta pull, write-offs, field events,
    images) at 15 s timeout each, which freezes the window and Windows then
    reports the app as 'Not responding'.
    """
    success = pyqtSignal(object)
    failure = pyqtSignal(str)

    def run(self):
        try:
            from services.sync_client import sync_now
            self.success.emit(sync_now())
        except Exception as ex:
            self.failure.emit(str(ex))


class _PingThread(QThread):
    result = pyqtSignal(bool, str)

    def run(self):
        try:
            from services.sync_client import ping
            ok = ping()
            msg = "Connected ✅" if ok else "Could not authenticate ❌"
            self.result.emit(ok, msg)
        except Exception as ex:
            self.result.emit(False, f"Error: {ex}")


# ── Dialog ────────────────────────────────────────────────────────────────────

class SyncSettingsDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sync Settings")
        self.setMinimumWidth(460)
        self._login_thread = None
        self._ping_thread  = None
        self._build_ui()
        self._populate()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        title = QLabel("🔄  Server Sync")
        title.setFont(QFont("Segoe UI", 13, QFont.Bold))
        lay.addWidget(title)

        # ── Connection group ─────────────────────────────────────────────────
        conn_group = QGroupBox("Server Connection")
        cf = QFormLayout()
        cf.setSpacing(6)

        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText("http://192.168.1.10:8000")
        cf.addRow("Server URL:", self._url_edit)

        self._user_edit = QLineEdit()
        self._user_edit.setPlaceholderText("username")
        cf.addRow("Username:", self._user_edit)

        self._pass_edit = QLineEdit()
        self._pass_edit.setEchoMode(QLineEdit.Password)
        self._pass_edit.setPlaceholderText("password")
        cf.addRow("Password:", self._pass_edit)

        conn_group.setLayout(cf)
        lay.addWidget(conn_group)

        # Button row: login + test
        btn_row = QHBoxLayout()

        self._login_btn = QPushButton("🔑  Log In")
        self._login_btn.setStyleSheet(
            "QPushButton{background:#1976D2;color:white;border-radius:4px;"
            "padding:6px 14px;font-weight:bold;}"
            "QPushButton:hover{background:#1565C0;}"
            "QPushButton:disabled{background:#B0BEC5;}"
        )
        self._login_btn.clicked.connect(self._do_login)
        btn_row.addWidget(self._login_btn)

        self._test_btn = QPushButton("📡  Test Connection")
        self._test_btn.setStyleSheet(
            "QPushButton{border:1px solid #90A4AE;border-radius:4px;padding:6px 14px;}"
            "QPushButton:hover{background:#E3F2FD;}"
        )
        self._test_btn.clicked.connect(self._do_test)
        btn_row.addWidget(self._test_btn)

        btn_row.addStretch()
        lay.addLayout(btn_row)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet("color:#555;font-style:italic;")
        lay.addWidget(self._status_lbl)

        # ── Status group ─────────────────────────────────────────────────────
        status_group = QGroupBox("Sync Status")
        sf = QFormLayout()
        sf.setSpacing(4)

        self._logged_lbl  = QLabel("—")
        self._cursor_lbl  = QLabel("—")
        self._last_lbl    = QLabel("—")
        self._pending_lbl = QLabel("—")

        sf.addRow("Logged in as:",  self._logged_lbl)
        sf.addRow("Last cursor:",   self._cursor_lbl)
        sf.addRow("Last sync:",     self._last_lbl)
        sf.addRow("Pending local:", self._pending_lbl)

        status_group.setLayout(sf)
        lay.addWidget(status_group)

        # ── Auto-sync toggle ──────────────────────────────────────────────────
        self._enable_cb = QCheckBox("Enable automatic sync (every 60 s)")
        lay.addWidget(self._enable_cb)

        # ── Buttons ───────────────────────────────────────────────────────────
        sync_now_btn = QPushButton("⚡  Sync Now")
        sync_now_btn.setStyleSheet(
            "QPushButton{background:#388E3C;color:white;border-radius:4px;padding:6px 14px;}"
            "QPushButton:hover{background:#2E7D32;}"
        )
        sync_now_btn.clicked.connect(self._do_sync_now)
        lay.addWidget(sync_now_btn)

        logout_btn = QPushButton("🚪  Log Out / Disconnect")
        logout_btn.setStyleSheet(
            "QPushButton{border:1px solid #EF9A9A;border-radius:4px;padding:5px 12px;color:#B71C1C;}"
            "QPushButton:hover{background:#FFEBEE;}"
        )
        logout_btn.clicked.connect(self._do_logout)
        lay.addWidget(logout_btn)

        close_btn = QDialogButtonBox(QDialogButtonBox.Close)
        close_btn.rejected.connect(self.accept)
        lay.addWidget(close_btn)

    def _populate(self):
        self._url_edit.setText(sync_config.server_url)
        self._user_edit.setText(sync_config.username)
        self._enable_cb.setChecked(sync_config.enabled)

        if sync_config.username:
            self._logged_lbl.setText(sync_config.username)
        if sync_config.last_cursor and sync_config.last_cursor != "0":
            self._cursor_lbl.setText(sync_config.last_cursor[:16])
        if sync_config.last_sync_at:
            self._last_lbl.setText(sync_config.last_sync_at[:16])

        # Count pending
        try:
            conn = get_connection()
            n = conn.execute(
                "SELECT COUNT(*) FROM work_log_entries WHERE sync_status IN ('local','pending')"
            ).fetchone()[0]
            conn.close()
            self._pending_lbl.setText(str(n))
        except Exception:
            pass

    # ── Actions ───────────────────────────────────────────────────────────────

    def _do_login(self):
        url      = self._url_edit.text().strip()
        username = self._user_edit.text().strip()
        password = self._pass_edit.text()

        if not url or not username or not password:
            QMessageBox.warning(self, "Required", "Fill in URL, username and password.")
            return

        self._login_btn.setEnabled(False)
        self._status_lbl.setText("Logging in…")

        self._login_thread = _LoginThread(url, username, password)
        self._login_thread.success.connect(self._on_login_success)
        self._login_thread.failure.connect(self._on_login_failure)
        self._login_thread.start()

    def _on_login_success(self, user: dict):
        self._login_btn.setEnabled(True)
        self._status_lbl.setStyleSheet("color:#2E7D32;")
        self._status_lbl.setText(
            f"✅  Logged in as {user['username']}  (role: {user['role']})"
        )
        self._logged_lbl.setText(user["username"])
        # Auto-enable sync on successful login
        self._enable_cb.setChecked(True)
        sync_config.enabled = True
        sync_config.save()
        self._pass_edit.clear()

    def _on_login_failure(self, msg: str):
        self._login_btn.setEnabled(True)
        self._status_lbl.setStyleSheet("color:#C62828;")
        self._status_lbl.setText(f"❌  {msg}")

    def _do_test(self):
        if not sync_config.is_configured():
            QMessageBox.information(self, "Not configured", "Log in first.")
            return
        self._status_lbl.setStyleSheet("color:#555;")
        self._status_lbl.setText("Testing…")
        self._test_btn.setEnabled(False)

        self._ping_thread = _PingThread()
        self._ping_thread.result.connect(self._on_ping_result)
        self._ping_thread.start()

    def _on_ping_result(self, ok: bool, msg: str):
        self._test_btn.setEnabled(True)
        color = "#2E7D32" if ok else "#C62828"
        self._status_lbl.setStyleSheet(f"color:{color};")
        self._status_lbl.setText(msg)

    def _do_sync_now(self):
        if not sync_config.is_configured():
            QMessageBox.information(self, "Not configured", "Log in first.")
            return
        # Signal the main window's sync worker to trigger immediately
        mw = self.parent()
        while mw and not hasattr(mw, "sync_worker"):
            mw = mw.parent()
        if mw and hasattr(mw, "sync_worker") and mw.sync_worker:
            mw.sync_worker.trigger_now()
            self._status_lbl.setText("Sync triggered…")
            return

        # No background worker yet (e.g. just logged in — the main window only
        # starts it after this dialog closes). Run in a thread, never inline:
        # a full sync is a dozen network calls and would freeze the window.
        if getattr(self, "_sync_thread", None) and self._sync_thread.isRunning():
            return
        self._status_lbl.setStyleSheet("color:#555;")
        self._status_lbl.setText("Syncing… (this may take a moment)")
        self._sync_thread = _SyncThread()
        self._sync_thread.success.connect(self._on_sync_done)
        self._sync_thread.failure.connect(self._on_sync_failed)
        self._sync_thread.start()

    def _on_sync_done(self, result):
        self._status_lbl.setStyleSheet("color:#2E7D32;")
        self._status_lbl.setText(
            f"✅  Done: ↑{result.pushed} ↓{result.pulled}"
            + (f"  ⚠{result.conflicts} conflicts" if result.conflicts else "")
            + (f"  ({result.errors} errors)" if result.errors else "")
        )
        self._populate()

    def _on_sync_failed(self, msg: str):
        self._status_lbl.setStyleSheet("color:#C62828;")
        self._status_lbl.setText(f"❌  {msg[:120]}")

    def _do_logout(self):
        sync_config.clear_tokens()
        sync_config.enabled = False
        sync_config.save()
        self._logged_lbl.setText("—")
        self._cursor_lbl.setText("—")
        self._last_lbl.setText("—")
        self._status_lbl.setStyleSheet("color:#555;")
        self._status_lbl.setText("Logged out.")

    def done(self, result):
        """Called on every close path: X button, accept(), reject()."""
        sync_config.enabled = self._enable_cb.isChecked()
        sync_config.save()
        super().done(result)
