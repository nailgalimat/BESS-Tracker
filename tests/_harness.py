"""Test harness — every test imports this FIRST, before any project module.

It redirects everything a test must never touch, so a test cannot damage the
user's data or reach the live server no matter what it does:

  database     a snapshot of dist/pv_bess_tracker.db (SQLite's online backup,
               consistent even while the exe has it open), via BESS_DB
  sync         a config with sync switched off, via BESS_SYNC_CONFIG — the real
               sync_config.json is enabled and holds live tokens, and opening
               the main window starts a sync worker
  network      any HTTP request to a host other than localhost raises
  KPI history  data/monthly_history*.json are copied and the copies used — the
               report generator writes its month back, and that file is
               bundled into the exe

Results are reported as a final `RESULT PASS|FAIL|SKIP` line rather than the
exit code: PyQt can fault while tearing the QApplication down at interpreter
exit, after every check has already run. `finish()` leaves via os._exit.

Everything a run produces lands in WORK (printed at the top); it is kept, so a
failure can be inspected.
"""
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time

MVP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE_DB = os.path.join(MVP, 'dist', 'pv_bess_tracker.db')
# Monthly SCADA exports. Customer data — never committed; tests that need it skip.
SCADA_DIR = os.environ.get(
    'BESS_SCADA_DIR',
    r'C:\Users\user1\Desktop\ACWA BESS Tashkent\LTSA monthly report\2026')

WORK = os.path.join(tempfile.gettempdir(), 'bess_tests',
                    '{}-{}-{}'.format(time.strftime('%Y%m%d-%H%M%S'),
                                      os.path.splitext(os.path.basename(sys.argv[0]))[0],
                                      os.getpid()))
os.makedirs(WORK, exist_ok=True)

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8',
                                  errors='replace', line_buffering=True)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('MPLBACKEND', 'Agg')

# ── sync off ─────────────────────────────────────────────────────────────────
SYNC_CONFIG = os.path.join(WORK, 'sync_config.json')
with open(SYNC_CONFIG, 'w') as _f:
    json.dump({'enabled': False}, _f)
os.environ['BESS_SYNC_CONFIG'] = SYNC_CONFIG


# ── database snapshot ────────────────────────────────────────────────────────
def snapshot(src, dst):
    """Consistent copy of a (possibly open, WAL-mode) SQLite database."""
    s = sqlite3.connect('file:{}?mode=ro'.format(src.replace('\\', '/')), uri=True)
    d = sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


DB = os.path.join(WORK, 'bess.db')
if os.path.exists(LIVE_DB):
    snapshot(LIVE_DB, DB)
os.environ['BESS_DB'] = DB

sys.path.insert(0, MVP)
os.chdir(MVP)

# ── network tripwire ─────────────────────────────────────────────────────────
import requests                                                    # noqa: E402

_real_request = requests.Session.request


def _guarded_request(self, method, url, *a, **k):
    from urllib.parse import urlparse
    host = urlparse(str(url)).hostname or ''
    if host not in ('localhost', '127.0.0.1', 'testserver'):
        raise RuntimeError('test tried to reach {} — the network is off in tests'
                           .format(url))
    return _real_request(self, method, url, *a, **k)


requests.Session.request = _guarded_request     # requests.get/post go through here

# ── project imports, now that the redirects are in place ─────────────────────
import database.db_manager as dbm                                  # noqa: E402

if os.path.normcase(os.path.abspath(dbm.DB_PATH)) != os.path.normcase(DB):
    raise SystemExit('harness: database not redirected ({}) — refusing to run'
                     .format(dbm.DB_PATH))
dbm.initialize_database()

from services.sync_config import sync_config                       # noqa: E402

if sync_config.enabled or sync_config.is_configured():
    raise SystemExit('harness: the test sync config is live — refusing to run')
sync_config.save = lambda *a, **k: None          # belt and braces

import services.bukhara_report_service as _B                       # noqa: E402

REAL_HISTORY = {}
for _attr in ('DEFAULT_HISTORY_PATH', 'DEFAULT_HISTORY_PATH_BUKHARA'):
    _src = getattr(_B, _attr)
    REAL_HISTORY[_attr] = _src
    _dst = os.path.join(WORK, os.path.basename(_src))
    if os.path.exists(_src):
        shutil.copy2(_src, _dst)
    setattr(_B, _attr, _dst)

print('[harness] work dir:', WORK)


# ── reporting ────────────────────────────────────────────────────────────────
_failures = []


def check(cond, msg):
    """Record a check without stopping the test."""
    print(('   ok    ' if cond else '   FAIL  ') + msg)
    if not cond:
        _failures.append(msg)
    return bool(cond)


def skip(reason):
    print('RESULT SKIP', reason)
    sys.stdout.flush()
    os._exit(0)


def finish():
    sys.stdout.flush()
    if _failures:
        print('RESULT FAIL ({} check(s))'.format(len(_failures)))
        sys.stdout.flush()
        os._exit(1)
    print('RESULT PASS')
    sys.stdout.flush()
    os._exit(0)


def require_scada(*parts):
    """Path inside the SCADA export folder, or skip the test if it isn't here."""
    p = os.path.join(SCADA_DIR, *parts)
    if not os.path.exists(p):
        skip('SCADA data not available: ' + p)
    return p


def fresh_db(name='fresh.db'):
    """Point the app at a new, empty database in WORK and create the schema."""
    path = os.path.join(WORK, name)
    for s in ('', '-wal', '-shm'):
        try:
            os.remove(path + s)
        except OSError:
            pass
    dbm.DB_PATH = path
    dbm.initialize_database()
    return path
