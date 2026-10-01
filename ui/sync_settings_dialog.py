"""
ui/sync_settings_dialog.py
---------------------------
Dialog for configuring the sync server connection.
  - Server URL + username + password
  - Test connection button
  - Enable/disable auto-sync toggle
  - Shows last sync time + pending count
  - Photos: how many this PC has archived, how many the server is still holding,
    how full the server's disk is, and the action that frees it
    (see services/photo_retention_service.py)
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


class _StorageThread(QThread):
    """Asks the server what it is still holding and how full its disk is."""
    result = pyqtSignal(dict)

    def run(self):
        try:
            from services.sync_client import server_storage
            self.result.emit(server_storage() or {})
        except Exception:                                    # noqa: BLE001
            self.result.emit({})


class _SweepThread(QThread):
    """Verifies the local copies and lets the server delete its own files.

    Hashes every photo not yet confirmed, so it can read hundreds of megabytes
    on its first run — never on the UI thread.
    """
    done    = pyqtSignal(dict)
    failure = pyqtSignal(str)

    def run(self):
        try:
            from services import photo_retention_service as prs
            self.done.emit(prs.sweep())
        except Exception as ex:                              # noqa: BLE001
            self.failure.emit(str(ex))


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

        # ── Photos: where the files actually are ─────────────────────────────
        # The server's disk is small and the phones fill it; this says how much
        # of it is still holding photos this desktop already has.
        photo_group = QGroupBox("Photos")
        pf = QFormLayout()
        pf.setSpacing(4)

        self._ph_here_lbl   = QLabel("—")
        self._ph_server_lbl = QLabel("—")
        self._ph_disk_lbl   = QLabel("—")
        self._ph_warn_lbl   = QLabel("")
        self._ph_warn_lbl.setWordWrap(True)
        self._ph_warn_lbl.setStyleSheet("color:#C62828;")

        pf.addRow("Archived on this PC:", self._ph_here_lbl)
        pf.addRow("Still on the server:", self._ph_server_lbl)
        pf.addRow("Server disk:",         self._ph_disk_lbl)

        self._sweep_btn = QPushButton("🧹  Free space on the server")
        self._sweep_btn.setToolTip(
            "Check every photo this PC holds against the hash the server stored,\n"
            "then let the server delete the files it no longer needs to keep.\n"
            "A photo that cannot be verified is kept on the server.")
        self._sweep_btn.setStyleSheet(
            "QPushButton{border:1px solid #90A4AE;border-radius:4px;padding:5px 12px;}"
            "QPushButton:hover{background:#E3F2FD;}"
            "QPushButton:disabled{color:#90A4AE;}"
        )
        self._sweep_btn.clicked.connect(self._do_sweep)
        pf.addRow("", self._sweep_btn)

        photo_group.setLayout(pf)
        lay.addWidget(photo_group)
        lay.addWidget(self._ph_warn_lbl)

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

        self._populate_photos()

    # ── Photos ────────────────────────────────────────────────────────────────

    @staticmethod
    def _mb(n) -> str:
        from services.photo_retention_service import human_bytes
        return human_bytes(n)

    def _populate_photos(self):
        """Local figures first — they need no network and are always true."""
        try:
            from services.photo_retention_service import local_status
            st = local_status()
        except Exception:                                    # noqa: BLE001
            return
        # A clip is one of these photos — same table, same sweep, same bytes —
        # but it is ~3 MB against a photo's ~0.5 MB, so how many of the figure
        # are clips is the first thing to ask when the 1 GB disk fills.
        def _vid(n):
            return " ({} video)".format(n) if n else ""

        self._ph_here_lbl.setText("{} photo(s){}, {} — the server has dropped these"
                                  .format(st['archived_count'],
                                          _vid(st.get('video_archived', 0)),
                                          self._mb(st['archived_bytes'])))
        self._ph_server_lbl.setText("{} photo(s){}, {}".format(
            st['on_server_count'], _vid(st.get('video_on_server', 0)),
            self._mb(st['on_server_bytes'])))
        self._sweep_btn.setEnabled(bool(st['on_server_count']))

        bad = st.get('unverified') or []
        if bad:
            self._ph_warn_lbl.setText(
                "⚠  {} photo(s) could not be verified against the stored hash, so "
                "the server still keeps them: {}. Sync again — the copy here is "
                "downloaded afresh; if it keeps failing, say so before anything is "
                "deleted.".format(len(bad),
                                  ", ".join(w['item_id'][:8] for w in bad[:6])))
        else:
            self._ph_warn_lbl.setText("")

        # The server's own figure, including its disk — off the UI thread.
        running = getattr(self, "_storage_thread", None)
        if sync_config.is_configured() and not (running and running.isRunning()):
            self._ph_disk_lbl.setText("asking the server…")
            self._storage_thread = _StorageThread()
            self._storage_thread.result.connect(self._on_storage)
            self._storage_thread.start()

    def _on_storage(self, st: dict):
        disk = (st or {}).get('disk') or {}
        self._ph_disk_lbl.setStyleSheet("")      # a disk that has drained is not red
        if not st:
            self._ph_disk_lbl.setText("could not be read")
            return
        if disk:
            self._ph_disk_lbl.setText(
                "{:.0f} MB used of {:.0f} MB ({:.0f}% full) · "
                "{} photo file(s) there, {}".format(
                    disk.get('used_mb', 0), disk.get('total_mb', 0),
                    disk.get('used_pct', 0), st.get('on_server_count', 0),
                    self._mb(st.get('on_server_bytes', 0))))
            if float(disk.get('used_pct') or 0) >= 85:
                self._ph_disk_lbl.setStyleSheet("color:#C62828;font-weight:bold;")
        else:
            self._ph_disk_lbl.setText("{} photo file(s) there, {}".format(
                st.get('on_server_count', 0), self._mb(st.get('on_server_bytes', 0))))

    def _do_sweep(self):
        if not sync_config.is_configured():
            QMessageBox.information(self, "Not configured", "Log in first.")
            return
        if getattr(self, "_sweep_thread", None) and self._sweep_thread.isRunning():
            return
        self._sweep_btn.setEnabled(False)
        self._status_lbl.setStyleSheet("color:#555;")
        self._status_lbl.setText("Checking photos against their stored hashes…")
        self._sweep_thread = _SweepThread()
        self._sweep_thread.done.connect(self._on_sweep_done)
        self._sweep_thread.failure.connect(self._on_sweep_failed)
        self._sweep_thread.start()

    def _on_sweep_done(self, rep: dict):
        from services.photo_retention_service import describe
        text = describe(rep)
        self._sweep_btn.setEnabled(True)
        self._status_lbl.setStyleSheet("color:#2E7D32;" if not rep.get('skipped')
                                       else "color:#EF6C00;")
        self._status_lbl.setText(text[:160])
        QMessageBox.information(self, "Free space on the server", text)
        self._populate_photos()

    def _on_sweep_failed(self, msg: str):
        self._sweep_btn.setEnabled(True)
        self._status_lbl.setStyleSheet("color:#C62828;")
        self._status_lbl.setText(f"❌  {msg[:120]}")

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
