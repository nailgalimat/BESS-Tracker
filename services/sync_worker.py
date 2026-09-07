"""
services/sync_worker.py
------------------------
Background QThread that runs a sync cycle every INTERVAL_SEC seconds.

Signals:
  sync_started()              — cycle about to begin
  sync_done(SyncResult)       — cycle completed (result is a dict)
  sync_error(str)             — unhandled exception
  status_changed(str)         — short status string for the status bar
"""

from PyQt5.QtCore import QThread, pyqtSignal

from services.sync_config import sync_config
from services.sync_client import sync_now, ping

INTERVAL_SEC = 60


class SyncWorker(QThread):
    sync_started  = pyqtSignal()
    sync_done     = pyqtSignal(dict)     # {"pushed":n, "pulled":n, "conflicts":n, "errors":n}
    sync_error    = pyqtSignal(str)
    status_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running  = True
        self._force    = False           # trigger immediate sync

    def run(self):
        elapsed = INTERVAL_SEC           # run immediately on first tick
        while self._running:
            if elapsed >= INTERVAL_SEC or self._force:
                self._force   = False
                elapsed       = 0

                if not sync_config.is_configured() or not sync_config.enabled:
                    self.msleep(1000)
                    elapsed += 1
                    continue

                self.sync_started.emit()
                self.status_changed.emit("🔄 Syncing…")
                try:
                    result = sync_now()
                    result_dict = result._asdict()
                    self.sync_done.emit(result_dict)

                    if result.conflicts > 0:
                        self.status_changed.emit(
                            f"⚠ {result.conflicts} conflict(s)"
                        )
                    elif result.errors > 0:
                        self.status_changed.emit(
                            f"⚠ Sync error ({result.errors} failed)"
                        )
                    else:
                        last = sync_config.last_sync_at[:16] if sync_config.last_sync_at else ""
                        pushed = result.pushed
                        pulled = result.pulled
                        if pushed or pulled:
                            self.status_changed.emit(
                                f"✅ Synced  ↑{pushed} ↓{pulled}"
                            )
                        else:
                            self.status_changed.emit(
                                f"✅ Up to date  {last}"
                            )

                except Exception as ex:
                    self.sync_error.emit(str(ex))
                    self.status_changed.emit(f"❌ Sync failed: {ex}")

            self.msleep(1000)
            elapsed += 1

    def trigger_now(self):
        """Force an immediate sync on the next loop iteration."""
        self._force = True

    def stop(self):
        self._running = False
        self.wait(3000)
