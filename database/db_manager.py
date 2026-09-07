"""
database/db_manager.py
-----------------------
Single source of truth for all table creation and indexes.
All new tables added here — no more ensure_*_table() scattered across services.

TABLES:
  projects, containers, daily_logs, materials,
  work_logs, asset_details,
  checklist_templates, checklist_items, checklist_runs, checklist_results,
  warehouses, stock_items, stock_transactions
"""

import sqlite3
import os
import sys


def _get_db_path() -> str:
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, 'pv_bess_tracker.db')


DB_PATH = _get_db_path()


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA cache_size = -8000")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def initialize_database():
    """Creates all tables and indexes. Safe to call on every startup."""
    conn = get_connection()
    try:
        c = conn.cursor()

        # ── PROJECTS ──────────────────────────────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                name           TEXT    NOT NULL UNIQUE,
                description    TEXT,
                num_zones      INTEGER NOT NULL DEFAULT 1,
                num_blocks     INTEGER NOT NULL DEFAULT 1,
                num_containers INTEGER NOT NULL DEFAULT 1,
                project_type   TEXT    NOT NULL DEFAULT 'BESS',
                created_at     TEXT    DEFAULT (datetime('now'))
            )
        """)

        # Migration: add project_type to existing databases that don't have it
        existing_cols = [r[1] for r in c.execute("PRAGMA table_info(projects)").fetchall()]
        if "project_type" not in existing_cols:
            c.execute("ALTER TABLE projects ADD COLUMN project_type TEXT NOT NULL DEFAULT 'BESS'")

        # ── CONTAINERS ────────────────────────────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS containers (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id      INTEGER NOT NULL,
                zone_number     INTEGER NOT NULL,
                block_number    INTEGER NOT NULL,
                container_index INTEGER NOT NULL,
                container_type  TEXT    NOT NULL,
                serial_number   TEXT    DEFAULT '',
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
                UNIQUE(project_id, zone_number, block_number, container_index)
            )
        """)

        # ── ASSET DETAILS ─────────────────────────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS asset_details (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                container_id     INTEGER NOT NULL UNIQUE,
                manufacturer     TEXT    DEFAULT '',
                model            TEXT    DEFAULT '',
                firmware_version TEXT    DEFAULT '',
                updated_at       TEXT    DEFAULT (datetime('now')),
                FOREIGN KEY (container_id) REFERENCES containers(id) ON DELETE CASCADE
            )
        """)

        # ── DAILY LOGS ────────────────────────────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS daily_logs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id      INTEGER NOT NULL,
                container_id    INTEGER NOT NULL,
                date            TEXT    NOT NULL,
                material_number TEXT    NOT NULL,
                quantity        REAL    NOT NULL,
                comment         TEXT    DEFAULT '',
                sap_ticket      TEXT    DEFAULT '',
                warehouse_id    INTEGER,           -- NULL = no stock deduction
                created_at      TEXT    DEFAULT (datetime('now')),
                FOREIGN KEY (project_id)   REFERENCES projects(id)   ON DELETE CASCADE,
                FOREIGN KEY (container_id) REFERENCES containers(id) ON DELETE CASCADE
            )
        """)

        # Migration: add sap_ticket / warehouse_id to existing daily_logs tables
        existing_log_cols = [r[1] for r in c.execute(
            "PRAGMA table_info(daily_logs)").fetchall()]
        if "sap_ticket" not in existing_log_cols:
            c.execute("ALTER TABLE daily_logs ADD COLUMN sap_ticket TEXT DEFAULT ''")
        if "warehouse_id" not in existing_log_cols:
            c.execute("ALTER TABLE daily_logs ADD COLUMN warehouse_id INTEGER")

        # ── MATERIALS ─────────────────────────────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS materials (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                material_number TEXT    NOT NULL UNIQUE,
                description     TEXT    DEFAULT '',
                unit            TEXT    DEFAULT '',
                notes           TEXT    DEFAULT '',
                created_at      TEXT    DEFAULT (datetime('now'))
            )
        """)

        # ── WORK LOGS ─────────────────────────────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS work_logs (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id        INTEGER NOT NULL,
                container_id      INTEGER NOT NULL,
                date              TEXT    NOT NULL,
                zone_number       INTEGER NOT NULL,
                block_number      INTEGER NOT NULL,
                container_index   INTEGER NOT NULL,
                serial_number     TEXT    DEFAULT '',
                fault_description TEXT    DEFAULT '',
                work_performed    TEXT    DEFAULT '',
                status            TEXT    NOT NULL DEFAULT 'Fixed',
                sap_ticket        TEXT    DEFAULT '',
                comments          TEXT    DEFAULT '',
                created_at        TEXT    DEFAULT (datetime('now')),
                FOREIGN KEY (project_id)   REFERENCES projects(id)   ON DELETE CASCADE,
                FOREIGN KEY (container_id) REFERENCES containers(id) ON DELETE CASCADE
            )
        """)

        # ── CHECKLIST TEMPLATES ───────────────────────────────────────────
        # A template defines what checks to perform for a given container type
        # or a custom named scope (e.g. "Block commissioning - LC+PCS+4xBESS")
        c.execute("""
            CREATE TABLE IF NOT EXISTS checklist_templates (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                name           TEXT    NOT NULL UNIQUE,
                description    TEXT    DEFAULT '',
                container_type TEXT    DEFAULT '',  -- e.g. 'Battery', 'PCS / Converter', or '' for multi
                scope          TEXT    DEFAULT 'container', -- 'container' or 'block'
                created_at     TEXT    DEFAULT (datetime('now'))
            )
        """)

        # ── CHECKLIST ITEMS ───────────────────────────────────────────────
        # Each item is one check step within a template
        c.execute("""
            CREATE TABLE IF NOT EXISTS checklist_items (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                template_id INTEGER NOT NULL,
                order_num   INTEGER NOT NULL DEFAULT 0,
                category    TEXT    DEFAULT '',   -- e.g. 'Electrical', 'Mechanical'
                description TEXT    NOT NULL,
                expected    TEXT    DEFAULT '',   -- expected value / condition
                FOREIGN KEY (template_id) REFERENCES checklist_templates(id) ON DELETE CASCADE
            )
        """)

        # ── CHECKLIST RUNS ────────────────────────────────────────────────
        # One run = one execution of a template for a specific container/block
        c.execute("""
            CREATE TABLE IF NOT EXISTS checklist_runs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                template_id  INTEGER NOT NULL,
                project_id   INTEGER NOT NULL,
                container_id INTEGER,            -- NULL if scope=block
                zone_number  INTEGER NOT NULL,
                block_number INTEGER NOT NULL,
                run_date     TEXT    NOT NULL,
                engineer     TEXT    DEFAULT '',
                status       TEXT    DEFAULT 'In Progress', -- 'In Progress','Passed','Failed'
                notes        TEXT    DEFAULT '',
                created_at   TEXT    DEFAULT (datetime('now')),
                FOREIGN KEY (template_id) REFERENCES checklist_templates(id),
                FOREIGN KEY (project_id)  REFERENCES projects(id) ON DELETE CASCADE
            )
        """)

        # ── CHECKLIST RESULTS ─────────────────────────────────────────────
        # One result row per checklist item per run
        c.execute("""
            CREATE TABLE IF NOT EXISTS checklist_results (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id      INTEGER NOT NULL,
                item_id     INTEGER NOT NULL,
                result      TEXT    DEFAULT 'Pending', -- 'Pass','Fail','N/A','Pending'
                measured    TEXT    DEFAULT '',        -- actual measured value
                comment     TEXT    DEFAULT '',
                FOREIGN KEY (run_id)  REFERENCES checklist_runs(id)  ON DELETE CASCADE,
                FOREIGN KEY (item_id) REFERENCES checklist_items(id) ON DELETE CASCADE
            )
        """)

        # ── WAREHOUSES ────────────────────────────────────────────────────
        # Main warehouse (project_id=NULL) + one per project
        c.execute("""
            CREATE TABLE IF NOT EXISTS warehouses (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT    NOT NULL,
                project_id INTEGER,           -- NULL = main/central warehouse
                is_main    INTEGER DEFAULT 0, -- 1 = main warehouse
                notes      TEXT    DEFAULT '',
                created_at TEXT    DEFAULT (datetime('now')),
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
            )
        """)

        # ── STOCK ITEMS ───────────────────────────────────────────────────
        # Each warehouse has its own stock level per material
        c.execute("""
            CREATE TABLE IF NOT EXISTS stock_items (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                warehouse_id    INTEGER NOT NULL,
                material_number TEXT    NOT NULL,
                quantity        REAL    NOT NULL DEFAULT 0,
                min_quantity    REAL    NOT NULL DEFAULT 0,  -- alert threshold
                unit            TEXT    DEFAULT '',
                updated_at      TEXT    DEFAULT (datetime('now')),
                FOREIGN KEY (warehouse_id) REFERENCES warehouses(id) ON DELETE CASCADE,
                UNIQUE(warehouse_id, material_number)
            )
        """)

        # ── STOCK TRANSACTIONS ────────────────────────────────────────────
        # Audit trail of every stock movement
        c.execute("""
            CREATE TABLE IF NOT EXISTS stock_transactions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                warehouse_id    INTEGER NOT NULL,
                material_number TEXT    NOT NULL,
                transaction_type TEXT   NOT NULL, -- 'IN','OUT','TRANSFER'
                quantity        REAL    NOT NULL,
                project_id      INTEGER,           -- which project consumed it
                reference       TEXT    DEFAULT '', -- work order / log reference
                notes           TEXT    DEFAULT '',
                date            TEXT    NOT NULL,
                created_at      TEXT    DEFAULT (datetime('now')),
                FOREIGN KEY (warehouse_id) REFERENCES warehouses(id)
            )
        """)

        # ── AVAILABILITY EXCLUSIONS ───────────────────────────────────────
        # Manual entries for periods to exclude from technical availability:
        #   Scheduled Maintenance, Grid Outage, Force Majeure, Major Fault
        # time_from / time_to are HH:MM strings ('00:00' and '23:59' = full day)
        c.execute("""
            CREATE TABLE IF NOT EXISTS availability_exclusions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id      INTEGER,
                exclusion_type  TEXT    NOT NULL,
                date_from       TEXT    NOT NULL,
                date_to         TEXT    NOT NULL,
                time_from       TEXT    DEFAULT '00:00',
                time_to         TEXT    DEFAULT '23:59',
                affected_blocks TEXT    DEFAULT '',
                description     TEXT    DEFAULT '',
                created_at      TEXT    DEFAULT (datetime('now')),
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
            )
        """)

        # Migration: add time columns to existing tables
        existing_excl_cols = [r[1] for r in c.execute(
            "PRAGMA table_info(availability_exclusions)").fetchall()]
        if "time_from" not in existing_excl_cols:
            c.execute("ALTER TABLE availability_exclusions ADD COLUMN time_from TEXT DEFAULT '00:00'")
        if "time_to" not in existing_excl_cols:
            c.execute("ALTER TABLE availability_exclusions ADD COLUMN time_to TEXT DEFAULT '23:59'")

        # ── MANUAL UNAVAILABILITY (operator-recorded downtime, Tashkent) ─────
        # Feeds the 4.4.1 table, the availability heatmap and the contractual
        # availability of the Tashkent monthly report. lc NULL = whole block.
        c.execute("""
            CREATE TABLE IF NOT EXISTS manual_unavailability (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                block       INTEGER NOT NULL,
                lc          INTEGER,
                date_from   TEXT    NOT NULL,
                date_to     TEXT    NOT NULL,
                downtime_h  REAL    NOT NULL DEFAULT 0,
                cause       TEXT    DEFAULT '',
                created_at  TEXT    DEFAULT (datetime('now'))
            )
        """)

        # ── CYCLE-BALANCING / RESTED BLOCKS (operator-recorded, Tashkent) ────
        # Blocks intentionally held out of dispatch to balance equivalent full
        # cycles across the fleet (annual 365-cycle budget). Purely informational
        # for the report: low cycles / low RTE on these blocks are DELIBERATE and
        # must not be flagged as underperformance. Does NOT affect the 770 MWh
        # contractual availability. affected_blocks = comma-separated block ids.
        c.execute("""
            CREATE TABLE IF NOT EXISTS balancing_periods (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id      INTEGER,
                date_from       TEXT    NOT NULL,
                date_to         TEXT    NOT NULL,
                affected_blocks TEXT    DEFAULT '',
                note            TEXT    DEFAULT '',
                created_at      TEXT    DEFAULT (datetime('now')),
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
            )
        """)

        # ── REPORT WORKFLOW: per-project static config (entered once) ─────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS project_report_config (
                project_id      INTEGER PRIMARY KEY,
                site_type       TEXT    DEFAULT 'tashkent',   -- 'tashkent' | 'bukhara'
                customer        TEXT    DEFAULT '',
                om_company      TEXT    DEFAULT '',
                oem             TEXT    DEFAULT '',
                equipment       TEXT    DEFAULT '',
                project_capacity_str          TEXT DEFAULT '',
                plant_capacity_mw             REAL,
                per_block_capacity_mw         REAL,
                contractual_plant_capacity_mw REAL,
                availability_basis_mwh        REAL,
                redundancy_threshold_pct      REAL DEFAULT 100,
                yearly_cycle_target           REAL DEFAULT 365,
                prepared_by     TEXT    DEFAULT '',
                reviewed_by     TEXT    DEFAULT '',
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
            )
        """)

        # ── REPORT WORKFLOW: one row per (project, year, month) ───────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS report_months (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id      INTEGER NOT NULL,
                year            INTEGER NOT NULL,
                month           INTEGER NOT NULL,
                report_number   TEXT    DEFAULT '',
                status          TEXT    DEFAULT 'draft',
                cm_activities   TEXT    DEFAULT '',
                site_visits     TEXT    DEFAULT '',
                recommendations TEXT    DEFAULT '',
                planned_next    TEXT    DEFAULT '',
                safety_incidents TEXT   DEFAULT '',
                notes           TEXT    DEFAULT '',
                created_at      TEXT    DEFAULT (datetime('now')),
                UNIQUE(project_id, year, month),
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
            )
        """)

        # ── REPORT WORKFLOW: PM activities (per block, with duration) ─────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS pm_activities (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id  INTEGER NOT NULL,
                year        INTEGER NOT NULL,
                month       INTEGER NOT NULL,
                affected_blocks TEXT DEFAULT '',    -- csv block ids, '' = whole plant
                date_from   TEXT,
                date_to     TEXT,
                hours       REAL    DEFAULT 0,
                description TEXT    DEFAULT '',
                created_at  TEXT    DEFAULT (datetime('now')),
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
            )
        """)

        # Migration: scope the three unavailability tables to (project, month)
        # so they can be entered per report-month instead of globally. Existing
        # rows keep NULL year/month (the report still date-filters them).
        for _t in ("availability_exclusions", "manual_unavailability",
                   "balancing_periods"):
            _cols = [r[1] for r in c.execute(f"PRAGMA table_info({_t})").fetchall()]
            if "year" not in _cols:
                c.execute(f"ALTER TABLE {_t} ADD COLUMN year INTEGER")
            if "month" not in _cols:
                c.execute(f"ALTER TABLE {_t} ADD COLUMN month INTEGER")
            if "project_id" not in _cols:      # manual_unavailability lacked it
                c.execute(f"ALTER TABLE {_t} ADD COLUMN project_id INTEGER")

        # ── DAILY FIELD LOGS (work entries with photos) ───────────────────────
        # Separate from existing work_logs (fault/SAP tickets).
        # These are narrative daily activity logs with attached photos.
        c.execute("""
            CREATE TABLE IF NOT EXISTS work_log_entries (
                id              TEXT    PRIMARY KEY,        -- UUID4 as TEXT
                project_id      INTEGER,                    -- NULL for mobile/unassigned entries
                container_id    INTEGER,                    -- NULL = site-wide entry
                equipment_serial TEXT   DEFAULT '',
                site_location   TEXT   NOT NULL DEFAULT '',
                category        TEXT   NOT NULL DEFAULT 'maintenance',
                description     TEXT   NOT NULL DEFAULT '',
                log_date        TEXT   NOT NULL,            -- YYYY-MM-DD
                created_at      TEXT   DEFAULT (datetime('now')),
                updated_at      TEXT   DEFAULT (datetime('now')),
                deleted_at      TEXT,                       -- soft delete (NULL = active)
                version         INTEGER NOT NULL DEFAULT 1,
                sync_status     TEXT   NOT NULL DEFAULT 'local',
                fault_name      TEXT   DEFAULT '',           -- optional: fault / problem
                status          TEXT   DEFAULT '',           -- optional: '' | open | done
                sap_ticket      TEXT   DEFAULT '',           -- optional
                spare_parts     TEXT   DEFAULT '',           -- optional: parts used
                FOREIGN KEY (project_id)   REFERENCES projects(id)   ON DELETE CASCADE,
                FOREIGN KEY (container_id) REFERENCES containers(id) ON DELETE SET NULL
            )
        """)

        # Migration: optional technical-work fields on work_log_entries
        _wle_cols = [r[1] for r in c.execute(
            "PRAGMA table_info(work_log_entries)").fetchall()]
        for _col in ("fault_name", "status", "sap_ticket", "spare_parts"):
            if _col not in _wle_cols:
                c.execute(f"ALTER TABLE work_log_entries "
                          f"ADD COLUMN {_col} TEXT DEFAULT ''")

        c.execute("""
            CREATE TABLE IF NOT EXISTS work_log_tags (
                work_log_id TEXT NOT NULL,
                tag         TEXT NOT NULL,
                PRIMARY KEY (work_log_id, tag),
                FOREIGN KEY (work_log_id) REFERENCES work_log_entries(id) ON DELETE CASCADE
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS work_log_images (
                id             TEXT    PRIMARY KEY,         -- UUID4 as TEXT
                work_log_id    TEXT    NOT NULL,
                file_path      TEXT    NOT NULL,            -- absolute path in app storage
                thumbnail_path TEXT,
                filename       TEXT    DEFAULT '',
                size_bytes     INTEGER DEFAULT 0,
                sha256         TEXT    NOT NULL DEFAULT '',
                width          INTEGER,
                height         INTEGER,
                taken_at       TEXT,                        -- EXIF datetime if available
                uploaded_at    TEXT    DEFAULT (datetime('now')),
                upload_status  TEXT    NOT NULL DEFAULT 'local',
                FOREIGN KEY (work_log_id) REFERENCES work_log_entries(id) ON DELETE CASCADE
            )
        """)

        # ── FIELD-LOG SPARE PARTS (structured, stock-linked) ───────────────
        # Optional structured spare parts attached to a work_log_entry.
        # The free-text work_log_entries.spare_parts stays as a quick summary;
        # this table holds pickable material + qty rows that can be DEDUCTED
        # from a warehouse (separate, explicit confirmation — never automatic).
        #   deducted = 0  → pending  (nothing removed from stock yet)
        #   deducted = 1  → an OUT stock_transaction was recorded (tx_id)
        c.execute("""
            CREATE TABLE IF NOT EXISTS worklog_spare_parts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                work_log_id     TEXT    NOT NULL,
                material_number TEXT    NOT NULL,
                description     TEXT    DEFAULT '',   -- snapshot of material desc
                quantity        REAL    NOT NULL DEFAULT 1,
                unit            TEXT    DEFAULT '',
                deducted        INTEGER NOT NULL DEFAULT 0,
                warehouse_id    INTEGER,              -- warehouse deducted from
                tx_id           INTEGER,              -- stock_transactions.id (audit / reverse)
                deducted_at     TEXT,
                created_at      TEXT    DEFAULT (datetime('now')),
                FOREIGN KEY (work_log_id) REFERENCES work_log_entries(id) ON DELETE CASCADE
            )
        """)

        # ── ENSURE MAIN WAREHOUSE EXISTS ──────────────────────────────────
        existing = c.execute(
            "SELECT id FROM warehouses WHERE is_main=1"
        ).fetchone()
        if not existing:
            c.execute("""
                INSERT INTO warehouses (name, project_id, is_main, notes)
                VALUES ('Main Warehouse', NULL, 1, 'Central spare parts warehouse')
            """)

        # ── INDEXES ───────────────────────────────────────────────────────
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_containers_project        ON containers(project_id)",
            "CREATE INDEX IF NOT EXISTS idx_containers_zone_block      ON containers(project_id,zone_number,block_number)",
            "CREATE INDEX IF NOT EXISTS idx_daily_logs_project         ON daily_logs(project_id)",
            "CREATE INDEX IF NOT EXISTS idx_daily_logs_date            ON daily_logs(date)",
            "CREATE INDEX IF NOT EXISTS idx_daily_logs_container       ON daily_logs(container_id)",
            "CREATE INDEX IF NOT EXISTS idx_work_logs_project          ON work_logs(project_id)",
            "CREATE INDEX IF NOT EXISTS idx_work_logs_date             ON work_logs(date)",
            "CREATE INDEX IF NOT EXISTS idx_work_logs_container        ON work_logs(container_id)",
            "CREATE INDEX IF NOT EXISTS idx_work_logs_status           ON work_logs(status)",
            "CREATE INDEX IF NOT EXISTS idx_materials_number           ON materials(material_number)",
            "CREATE INDEX IF NOT EXISTS idx_stock_items_warehouse      ON stock_items(warehouse_id)",
            "CREATE INDEX IF NOT EXISTS idx_stock_items_material       ON stock_items(material_number)",
            "CREATE INDEX IF NOT EXISTS idx_stock_tx_warehouse         ON stock_transactions(warehouse_id)",
            "CREATE INDEX IF NOT EXISTS idx_stock_tx_date              ON stock_transactions(date)",
            "CREATE INDEX IF NOT EXISTS idx_checklist_runs_project     ON checklist_runs(project_id)",
            "CREATE INDEX IF NOT EXISTS idx_checklist_results_run      ON checklist_results(run_id)",
            "CREATE INDEX IF NOT EXISTS idx_asset_details_container    ON asset_details(container_id)",
            "CREATE INDEX IF NOT EXISTS idx_wle_project_date          ON work_log_entries(project_id, log_date DESC)",
            "CREATE INDEX IF NOT EXISTS idx_wle_updated_at            ON work_log_entries(updated_at)",
            "CREATE INDEX IF NOT EXISTS idx_wle_category              ON work_log_entries(category)",
            "CREATE INDEX IF NOT EXISTS idx_wle_deleted               ON work_log_entries(deleted_at)",
            "CREATE INDEX IF NOT EXISTS idx_wli_worklog               ON work_log_images(work_log_id)",
            "CREATE INDEX IF NOT EXISTS idx_wlt_worklog               ON work_log_tags(work_log_id)",
            "CREATE INDEX IF NOT EXISTS idx_wlsp_worklog              ON worklog_spare_parts(work_log_id)",
        ]
        for idx in indexes:
            c.execute(idx)

        conn.commit()
        print(f"[DB] Initialized: {DB_PATH}")
    finally:
        conn.close()
