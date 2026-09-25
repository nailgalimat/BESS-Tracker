"""tools/make_clean_db.py — build the pristine database the installer ships.

A new team must start on an empty plant list, not on a copy of somebody else's
data. So the file is *created from the schema*: BESS_DB is pointed at the target
and `database.db_manager.initialize_database()` is called, exactly as the app
does on first run. Nothing is ever copied from an existing database and emptied
— that is how customer rows leak into an installer.

    python tools\\make_clean_db.py                 -> build\\clean\\pv_bess_tracker.db
    python tools\\make_clean_db.py --out other.db  (used by tests)

build\\clean\\ is disposable and overwritten on every run. A target anywhere
inside dist\\ is refused: that is where the owner's live database lives.

Two tables are deliberately *not* empty, because `initialize_database()` seeds
them and the app relies on them existing: the single "Main Warehouse" row, and
the four builtin plan item types. Everything else must have zero rows, which
this script asserts before it reports success.
"""
import argparse
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(ROOT, 'build', 'clean', 'pv_bess_tracker.db')

# table -> number of rows initialize_database() seeds. Anything absent here must
# be empty.
SEEDED = {'warehouses': 1, 'plan_item_types': 4}


def is_inside_dist(path: str) -> bool:
    """True if path is dist\\ or anything under it (the live database's home)."""
    dist = os.path.join(ROOT, 'dist')
    try:
        rel = os.path.relpath(os.path.abspath(path), dist)
    except ValueError:                      # different drive
        return False
    return not rel.startswith('..') and not os.path.isabs(rel)


def build(out_path: str) -> str:
    out_path = os.path.abspath(out_path)
    if is_inside_dist(out_path):
        raise SystemExit(
            'refusing to write inside dist\\ ({}): that is the live database\'s '
            'folder. Use build\\clean\\ instead.'.format(out_path))

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    for suffix in ('', '-wal', '-shm'):
        try:
            os.remove(out_path + suffix)
        except OSError:
            pass

    # Point the app's own path resolution at the target *before* importing it:
    # DB_PATH is read at import time. Set, not setdefault — a caller (a test)
    # may already have BESS_DB pointing somewhere else.
    os.environ['BESS_DB'] = out_path
    sys.path.insert(0, ROOT)
    import database.db_manager as dbm                                  # noqa: E402

    # Run as a script — the normal case — the env var above is what decided
    # DB_PATH. Called in-process by a test, db_manager was already imported and
    # read BESS_DB long ago, so say it again on the module.
    dbm.DB_PATH = out_path
    dbm.initialize_database()

    # WAL mode leaves -wal/-shm sidecars, and even a read-only open recreates
    # them. Fold them in, leave the shipped file on the plain rollback journal
    # so it is one self-contained file, and compact it. get_connection() puts it
    # back into WAL the first time the app opens it.
    conn = sqlite3.connect(out_path, isolation_level=None)
    try:
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        conn.execute('PRAGMA journal_mode = DELETE')
        conn.execute('VACUUM')
    finally:
        conn.close()
    for suffix in ('-wal', '-shm'):
        try:
            os.remove(out_path + suffix)
        except OSError:
            pass
    return out_path


def verify(out_path: str) -> tuple:
    """(n_tables, offenders, integrity). offenders = [(table, rows), ...]."""
    conn = sqlite3.connect('file:{}?mode=ro'.format(
        out_path.replace('\\', '/')), uri=True)
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        offenders = []
        for t in tables:
            n = conn.execute('SELECT COUNT(*) FROM "{}"'.format(t)).fetchone()[0]
            if n != SEEDED.get(t, 0):
                offenders.append((t, n))
        integrity = conn.execute('PRAGMA integrity_check').fetchone()[0]
    finally:
        conn.close()
    return len(tables), offenders, integrity


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--out', default=DEFAULT_OUT,
                    help='target database (default: build\\clean\\pv_bess_tracker.db)')
    args = ap.parse_args(argv)

    out = build(args.out)
    n_tables, offenders, integrity = verify(out)

    if integrity != 'ok':
        print('FAILED: integrity_check says "{}"'.format(integrity))
        return 1
    if offenders:
        print('FAILED: these tables are not empty: ' +
              ', '.join('{}={}'.format(t, n) for t, n in offenders))
        return 1

    size_kb = os.path.getsize(out) / 1024.0
    print('clean database OK: {} ({:.0f} KB, {} tables, integrity ok, '
          'seeded: {})'.format(out, size_kb, n_tables,
                               ', '.join('{}={}'.format(t, n) for t, n
                                         in sorted(SEEDED.items()))))
    return 0


if __name__ == '__main__':
    sys.exit(main())
