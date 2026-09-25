"""The database the installer ships: schema, no records, and safe to rebuild.

A new team must start empty, so tools/make_clean_db.py creates the file from
`initialize_database()` instead of copying and emptying an existing database.
Two tables are seeded on purpose (the Main Warehouse row the stock module needs,
and the four builtin plan item types); everything else must have zero rows.

Also checks the two things that would be expensive to get wrong: running the
schema over the result again changes nothing (the app does exactly that on every
start), and the script refuses to write into dist\\, where the live database is.
"""
import _harness as H            # must be first

import importlib.util
import os
import sqlite3
import subprocess
import sys

MVP = H.MVP
SCRIPT = os.path.join(MVP, 'tools', 'make_clean_db.py')

spec = importlib.util.spec_from_file_location('make_clean_db', SCRIPT)
mkdb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mkdb)

# Tables that must exist — a spot check of the ones a first run depends on,
# not the whole list (db_manager owns that).
EXPECTED = ('projects', 'containers', 'work_log_entries', 'work_logs',
            'daily_logs', 'checklist_templates', 'checklist_items',
            'checklist_runs', 'checklist_results', 'action_items',
            'stock_items', 'stock_transactions', 'warehouses', 'sync_users',
            'pm_activities', 'availability_exclusions', 'report_months',
            'plan_items', 'plan_item_types', 'assets')


def tables(path):
    conn = sqlite3.connect(path)
    try:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'")}
    finally:
        conn.close()


# ── the script, run the way the owner runs it ────────────────────────────────
out = os.path.join(H.WORK, 'clean', 'pv_bess_tracker.db')
p = subprocess.run([sys.executable, SCRIPT, '--out', out],
                   capture_output=True, text=True, cwd=MVP,
                   env=dict(os.environ, PYTHONIOENCODING='utf-8'))
H.check(p.returncode == 0, 'make_clean_db.py exits 0: {}'.format(
    (p.stdout + p.stderr).strip().splitlines()[-1:]))
H.check(os.path.isfile(out), 'it wrote {}'.format(out))
H.check('clean database OK' in p.stdout, 'and reports one summary line')
for sidecar in ('-wal', '-shm'):
    H.check(not os.path.exists(out + sidecar),
            'no {} sidecar is shipped'.format(sidecar))

# ── what is in it ────────────────────────────────────────────────────────────
present = tables(out)
missing = [t for t in EXPECTED if t not in present]
H.check(not missing, 'every expected table is there ({} tables, missing: {})'
        .format(len(present), missing or 'none'))

n_tables, offenders, integrity = mkdb.verify(out)
H.check(integrity == 'ok', 'PRAGMA integrity_check: {}'.format(integrity))
H.check(not offenders, 'no data table holds rows: {}'.format(offenders or 'none'))
H.check(mkdb.SEEDED == {'warehouses': 1, 'plan_item_types': 4},
        'the only seeded rows are the Main Warehouse and the builtin plan types')

conn = sqlite3.connect(out)
try:
    wh = conn.execute('SELECT name, project_id, is_main FROM warehouses').fetchall()
finally:
    conn.close()
H.check(wh == [('Main Warehouse', None, 1)],
        'the warehouse seed is exactly the main one: {}'.format(wh))

# ── idempotent: the app runs the schema over it on every start ───────────────
before = sorted(present)
import database.db_manager as dbm                                      # noqa: E402

dbm.DB_PATH = out                    # as make_clean_db does; harness DB is done
dbm.initialize_database()
n2, offenders2, integrity2 = mkdb.verify(out)
H.check(sorted(tables(out)) == before, 'a second initialize_database() adds no table')
H.check(not offenders2 and integrity2 == 'ok',
        'and no row: {} / {}'.format(offenders2 or 'none', integrity2))

# ── it refuses dist\ ────────────────────────────────────────────────────────
H.check(mkdb.is_inside_dist(os.path.join(MVP, 'dist', 'x.db')),
        'a target in dist\\ is recognised')
H.check(mkdb.is_inside_dist(os.path.join(MVP, 'dist', 'sub', 'x.db')),
        'so is one below it')
H.check(not mkdb.is_inside_dist(out), 'the work-folder target is not')

# The live database is named on purpose: that is the mistake worth catching.
# Its own timestamp is not checked — the running app writes to it all day, so
# the refusal (and the fact the guard runs before anything is opened) is the
# guarantee, not an mtime.
live = os.path.join(MVP, 'dist', 'pv_bess_tracker.db')
p = subprocess.run([sys.executable, SCRIPT, '--out', live],
                   capture_output=True, text=True, cwd=MVP)
H.check(p.returncode != 0, 'and the script refuses it (exit {})'.format(p.returncode))
H.check('refusing to write inside dist' in (p.stdout + p.stderr),
        'saying why: {}'.format((p.stdout + p.stderr).strip()[:80]))

H.finish()
