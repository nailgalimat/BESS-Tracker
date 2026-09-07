"""
migrate_project_id_nullable.py
--------------------------------
Makes work_log_entries.project_id nullable in the existing backend.db.

SQLite doesn't support ALTER COLUMN, so we recreate the table.
Run once: python migrate_project_id_nullable.py
"""

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent / "backend.db"

if not DB_PATH.exists():
    print(f"[migrate] Database not found at {DB_PATH} — nothing to do.")
    sys.exit(0)

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Check current schema
cur.execute("PRAGMA table_info(work_log_entries)")
cols = cur.fetchall()
project_id_col = next((c for c in cols if c["name"] == "project_id"), None)

if project_id_col is None:
    print("[migrate] Column project_id not found — skipping.")
    conn.close()
    sys.exit(0)

if project_id_col["notnull"] == 0:
    print("[migrate] project_id is already nullable — nothing to do.")
    conn.close()
    sys.exit(0)

print(f"[migrate] Migrating {DB_PATH} ...")

try:
    conn.execute("BEGIN")

    # 1. Rename old table
    conn.execute("ALTER TABLE work_log_entries RENAME TO _work_log_entries_old")

    # 2. Create new table with project_id nullable
    conn.execute("""
        CREATE TABLE work_log_entries (
            id               TEXT PRIMARY KEY,
            user_id          TEXT NOT NULL,
            project_id       INTEGER,
            container_id     INTEGER,
            equipment_serial TEXT NOT NULL DEFAULT '',
            site_location    TEXT NOT NULL DEFAULT '',
            category         TEXT NOT NULL DEFAULT 'other',
            description      TEXT NOT NULL DEFAULT '',
            log_date         TEXT NOT NULL DEFAULT '',
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL,
            deleted_at       TEXT,
            version          INTEGER NOT NULL DEFAULT 1,
            origin_device    TEXT,
            sync_status      TEXT NOT NULL DEFAULT 'synced',
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    # 3. Copy all data (NULL out invalid project_id = 0)
    conn.execute("""
        INSERT INTO work_log_entries
        SELECT
            id, user_id,
            CASE WHEN project_id = 0 THEN NULL ELSE project_id END,
            container_id, equipment_serial, site_location,
            category, description, log_date,
            created_at, updated_at, deleted_at,
            version, origin_device, sync_status
        FROM _work_log_entries_old
    """)

    # 4. Recreate indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wle_user_updated_at ON work_log_entries (user_id, updated_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wle_sync_status    ON work_log_entries (sync_status)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_work_log_entries_project_id ON work_log_entries (project_id)")

    # 5. Drop old table
    conn.execute("DROP TABLE _work_log_entries_old")

    conn.execute("COMMIT")

    # Verify
    cur.execute("PRAGMA table_info(work_log_entries)")
    new_cols = {c["name"]: c for c in cur.fetchall()}
    assert new_cols["project_id"]["notnull"] == 0, "Migration failed — still NOT NULL"

    cur.execute("SELECT COUNT(*) FROM work_log_entries")
    count = cur.fetchone()[0]
    print(f"[migrate] Done. {count} rows migrated. project_id is now nullable.")

except Exception as e:
    conn.execute("ROLLBACK")
    # Try to restore if rename already happened
    try:
        cur.execute("SELECT 1 FROM _work_log_entries_old LIMIT 1")
        conn.execute("ALTER TABLE _work_log_entries_old RENAME TO work_log_entries")
        conn.commit()
        print("[migrate] Rolled back — original table restored.")
    except Exception:
        pass
    print(f"[migrate] ERROR: {e}")
    sys.exit(1)
finally:
    conn.close()
