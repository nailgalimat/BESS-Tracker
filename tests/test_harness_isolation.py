"""The harness's promises, checked. If this fails, no other test is safe to run.

A test must never write the user's database, never touch the real KPI history
(bundled into the exe), and never reach the live sync server — the real
sync_config.json is enabled with tokens, and opening the main window starts a
sync worker when it is.
"""
import _harness as H            # must be first
import os

import requests
import database.db_manager as dbm
import services.bukhara_report_service as B
from services.sync_config import sync_config, _config_path


def same(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


H.check(same(os.path.dirname(dbm.DB_PATH), H.WORK), 'database is the snapshot in the work dir')
H.check(not same(dbm.DB_PATH, H.LIVE_DB), 'database is not the live one')
H.check(same(_config_path(), H.SYNC_CONFIG), 'sync config is the test one')
H.check(not sync_config.enabled and not sync_config.is_configured(), 'sync is off, no tokens')
for attr in ('DEFAULT_HISTORY_PATH', 'DEFAULT_HISTORY_PATH_BUKHARA'):
    H.check(same(os.path.dirname(getattr(B, attr)), H.WORK), attr + ' redirected')

try:
    requests.get('https://bess-tracker-api.onrender.com/health', timeout=5)
    reached = True
except RuntimeError:
    reached = False
H.check(not reached, 'the live server cannot be reached from a test')

from PyQt5.QtWidgets import QApplication                            # noqa: E402
app = QApplication([])
import ui.main_window as mw                                         # noqa: E402
win = mw.MainWindow()
H.check(getattr(win, 'sync_worker', None) is None,
        'opening the main window starts no sync worker')

H.finish()
