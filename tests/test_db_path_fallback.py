"""Where a frozen app puts its database when its own folder is read-only.

The installer targets %LOCALAPPDATA%\\Programs\\BESS Tracker, which the user can
write to. A hand-copied exe under C:\\Program Files cannot write beside itself,
and SQLite's only complaint is "unable to open database file". So the frozen app
falls back to %LOCALAPPDATA%\\BESS Tracker.

Everything else must be unchanged: a writable folder keeps its own file (an
existing installation must not move its data), and BESS_DB still wins.

Freezing is simulated — sys.frozen / sys.executable set, and the writability
probe replaced. Nothing is written outside the work folder: LOCALAPPDATA points
there for the duration.
"""
import _harness as H            # must be first

import os
import sys

import database.db_manager as dbm

SAVED = {'db': os.environ.get('BESS_DB'),
         'lad': os.environ.get('LOCALAPPDATA'),
         'exe': sys.executable,
         'frozen': getattr(sys, 'frozen', None),
         'probe': dbm._dir_writable}

LOCAL = os.path.join(H.WORK, 'localappdata')
EXE_DIR = os.path.join(H.WORK, 'program', 'BESS Tracker')
os.makedirs(EXE_DIR, exist_ok=True)

try:
    # ── the probe itself, against the real filesystem ────────────────────────
    H.check(dbm._dir_writable(EXE_DIR), 'a real writable folder probes writable')
    H.check(not dbm._dir_writable(os.path.join(EXE_DIR, 'no', 'such', 'dir')),
            'a folder that does not exist probes unwritable')
    H.check(not os.listdir(EXE_DIR), 'the probe leaves no file behind')

    os.environ.pop('BESS_DB', None)          # the harness sets it; test the rest
    os.environ['LOCALAPPDATA'] = LOCAL
    sys.frozen = True                        # simulate the packaged exe
    sys.executable = os.path.join(EXE_DIR, 'BESS Tracker.exe')

    # ── frozen, writable folder: unchanged behaviour ─────────────────────────
    path = dbm._get_db_path()
    H.check(path == os.path.join(EXE_DIR, 'pv_bess_tracker.db'),
            'frozen + writable folder: the database stays beside the exe ({})'
            .format(path))
    H.check(not os.path.exists(LOCAL),
            'and nothing is created in %LOCALAPPDATA%')

    # ── frozen, read-only folder: %LOCALAPPDATA% ─────────────────────────────
    dbm._dir_writable = lambda p: False      # e.g. C:\Program Files
    path = dbm._get_db_path()
    H.check(path == os.path.join(LOCAL, 'BESS Tracker', 'pv_bess_tracker.db'),
            'frozen + read-only folder: falls back to %LOCALAPPDATA% ({})'
            .format(path))
    H.check(os.path.isdir(os.path.join(LOCAL, 'BESS Tracker')),
            'the fallback folder is created, so SQLite can open the file')

    # ── BESS_DB wins over both ───────────────────────────────────────────────
    forced = os.path.join(H.WORK, 'forced.db')
    os.environ['BESS_DB'] = forced
    H.check(dbm._get_db_path() == forced,
            'BESS_DB wins while the folder is read-only')
    dbm._dir_writable = lambda p: True
    H.check(dbm._get_db_path() == forced, 'and while it is writable')
    del sys.frozen
    H.check(dbm._get_db_path() == forced, 'and when not frozen at all')

    # ── a source run is untouched ────────────────────────────────────────────
    os.environ.pop('BESS_DB', None)
    dbm._dir_writable = SAVED['probe']
    H.check(dbm._get_db_path() == os.path.join(H.MVP, 'pv_bess_tracker.db'),
            'a source run still uses the project root')
finally:
    dbm._dir_writable = SAVED['probe']
    sys.executable = SAVED['exe']
    if SAVED['frozen'] is None:
        if hasattr(sys, 'frozen'):
            del sys.frozen
    else:
        sys.frozen = SAVED['frozen']
    for key, value in (('BESS_DB', SAVED['db']), ('LOCALAPPDATA', SAVED['lad'])):
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

H.check(os.environ.get('BESS_DB') == SAVED['db'],
        'the harness environment is restored')

H.finish()
