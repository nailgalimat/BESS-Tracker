# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Windows desktop app (PyQt5) for field service engineers managing PV and BESS sites. Single-user, SQLite-backed, ships as a frozen PyInstaller `.exe` plus an Inno Setup installer. Originally a daily-maintenance tracker (see [README.md](README.md)); has since grown a SCADA-export → monthly PDF/DOCX report pipeline, KPI/availability analytics, checklists, and a spare-parts stock module. See [REBUILD.md](REBUILD.md) for the most recent change set.

## Run / build / package

```powershell
# dev run (from C:\Users\user1\MVP)
pip install -r requirements.txt
python main.py

# standalone Bukhara / Tashkent report runs (no UI)
python run_bukhara_report.py --format pdf --month-label April_2026
python run_tashkent_report.py --format both

# freeze to dist\BESS Tracker.exe
pyinstaller --clean "BESS Tracker.spec"

# build Windows installer (after PyInstaller)
# uses Inno Setup compiler against installer.iss → installer\BESS_Tracker_Setup.exe
```

There is no test suite, no linter config, and no CI. "Verification" in this project means: `python main.py` launches without an import error, the relevant UI page opens, and a report run produces a non-empty PDF/DOCX. Note the requirements pin is loose (`>=`) — when reproducing a user-reported bug, ask which versions they have rather than assuming.

## Architecture

Strict three-layer split — touch the right layer:

- **`database/db_manager.py`** — *single source of truth* for schema. All `CREATE TABLE` / `CREATE INDEX` / lightweight `ALTER TABLE` migrations live in `initialize_database()`, which is called on every app startup and is safe to re-run. **Do not** scatter `ensure_*_table()` helpers across service files — the docstring at the top of `db_manager.py` explicitly calls this out. To add a column, add an `ALTER TABLE … ADD COLUMN` guarded by a `PRAGMA table_info` check, in the same style as the existing `sap_ticket`, `project_type`, and `time_from/time_to` migrations.
- **`services/*.py`** — pure DB / business logic. Each service owns one domain (project, log, work_log, material, stock, checklist, asset, kpi, lifecycle, analytics, availability, plus the three report generators). Services import `database.db_manager.get_connection` and return plain dicts / dataclasses. **No PyQt imports in services.**
- **`ui/*.py`** — PyQt5 widgets only. `ui/main_window.py` is a sidebar + `QStackedWidget`; each sidebar entry maps to one page class. Page indices are constants at the top of `main_window.py` (`PAGE_DASHBOARD`, `PAGE_BLOCK_RPT`, etc.) — adding a new page means adding the constant, the import, an entry in `PAGE_NAMES`, and the nav button wiring.
- **`models/models.py`** — dataclasses (`Project`, `Container`, `LogEntry`) and the `PROJECT_TYPES` / `CONTAINER_TYPES_BY_PROJECT` / `default_container_type()` lookups that the project-creation wizard depends on. Three project types coexist: `BESS`, `PV String`, `PV Central`, each with its own container-type list and default-assignment rule.

### Frozen-vs-dev path resolution

`database/db_manager._get_db_path()` picks the DB location based on `sys.frozen`:

- dev: `<repo>/pv_bess_tracker.db`
- frozen exe: `<dir of exe>/pv_bess_tracker.db` (i.e. `dist/pv_bess_tracker.db`, which the Inno installer copies to `{app}\` with `onlyifdoesntexist` so user data survives upgrades)

Any new code that needs a runtime-writable file next to the app must follow the same `sys.frozen` pattern. Read-only bundled resources (e.g. `data/alarm_classifications.csv`) are loaded via `os.path.dirname(__file__)`-relative paths and bundled into the exe through the `datas` list in `BESS Tracker.spec` — see that file's `_hist` block for the "bundle if it exists" pattern.

### SQLite pragmas

`get_connection()` sets `foreign_keys=ON`, `journal_mode=WAL`, `cache_size=-8000`, `synchronous=NORMAL`. WAL means `pv_bess_tracker.db-wal` / `-shm` sidecar files are normal; back up the trio, not just the `.db`. The `db_backups/` folder holds timestamped snapshots — preserve them.

### Availability exclusions are shared across all reports

There is **one** exclusions store: the `availability_exclusions` table, fronted by [services/availability_service.py](services/availability_service.py). Both the SCADA Report page ([ui/scada_report_page.py](ui/scada_report_page.py)) and the Block Performance page ([ui/block_report_page.py](ui/block_report_page.py)) call `get_exclusions()` with **no project filter**, so an exclusion added on either page is automatically applied by both. The `ExclusionDialog` widget lives in `ui/scada_report_page.py` and is imported by the block page — do not duplicate it.

The math is applied in two slightly different ways:

- **SCADA** uses **device-hours** via `calculate_excluded_hours()` → subtracted from the denominator of `calc_availability()` in `scada_report_service.py`.
- **Bukhara / Tashkent** use **plant-wall-clock hours** via `calculate_plant_excluded_hours()` (weighted by `n_affected_blocks / total_blocks`) → fed into `calc_plant_hours_availability[_tashkent]()` as the optional `excluded_hours=` kwarg.

Both follow the same "cap-then-remove-from-denominator" pattern: `excluded_effective = min(excluded, plant_outage)`, then `adjusted = (scheduled − plant_outage) / (scheduled − excluded_effective)`. This guarantees `excluded_hours=0` produces byte-identical output to the pre-exclusion code path — so any new report that doesn't use exclusions still calculates exactly as before.

### Report generators (`services/{scada,bukhara,tashkent}_report_service.py`)

Three coexisting generators, **kept deliberately independent** — the Bukhara module's docstring explicitly says it does not import or modify the older `scada_report_service.py`. UI dispatches by site type. All three:

- read multi-sheet xlsx SCADA exports with pandas/openpyxl (28 MB Battery Unit files take 25–30 s — expected),
- render charts with `matplotlib.use('Agg')`,
- emit PDF via `reportlab.platypus`, and DOCX via `services/docx_renderer.py`,
- look up alarm patterns in `data/alarm_classifications.csv` (88 trigger-pattern entries) and append the run's KPIs to `data/monthly_history.json` (last 12 months kept) for the trend table on the next run.

Availability uses `contractual_plant_capacity_mw` when set (plant is "available" when block-aggregate capacity ≥ contractual MW, i.e. SLA-based), otherwise falls back to `redundancy_threshold_pct × plant_capacity_mw`. The Bukhara and Tashkent UIs are unified in `ui/block_report_page.py`; the older Tashkent-format flow lives in `ui/scada_report_page.py` which also owns the availability-exclusion dialog (writes to the `availability_exclusions` table — block IDs come from the project's real `containers` rows, not a hardcoded list).

### PyInstaller specifics

`BESS Tracker.spec` carries explicit `hiddenimports` for every `services/*` and `ui/*` module, plus matplotlib/reportlab/docx/openpyxl/pandas internals that the auto-analyser drops. **Any new file under `services/` or `ui/` must be added to that list**, otherwise the frozen exe will `ImportError` at runtime even though `python main.py` works. Same goes for bundled non-code resources — append to `datas` in the spec.

## Footguns

- **Stale top-level duplicates.** `edit_project_dialog.py`, `project_service.py`, and `generate_report.py` sit at the repo root *and* have canonical copies at `ui/edit_project_dialog.py`, `services/project_service.py`, and (refactored into) `services/{scada,bukhara,tashkent}_report_service.py`. Nothing imports the top-level ones, and they have already drifted from the real files (`diff` shows differences). Always edit the `ui/` / `services/` copies; if you're confident the top-level files are dead, ask before deleting — they may be intentional pre-refactor backups.
- **Live DB in `dist/`.** `dist/pv_bess_tracker.db` is the *user's actual data* — PyInstaller does not touch it on rebuild, and the Inno installer copies it with `onlyifdoesntexist` so upgrades preserve it. Before any destructive DB experiment, copy a snapshot from `db_backups/` (the latest `*_DIST_final_*.db` is the live DB at the time of the most recent rebuild).
- **WAL sidecar files.** `pv_bess_tracker.db-wal` / `.db-shm` next to the main `.db` are normal SQLite WAL artefacts — don't delete them while the app is running, and back the trio up together.

## Conventions worth knowing

- File headers are docstrings of the form `"""path/filename.py — one-line purpose"""` with a short paragraph. Match this style for new files.
- Migrations are forward-only and idempotent; the app must always boot against an older DB.
- The `materials` table is the canonical material-number catalogue; the `material_number` column on `daily_logs` / `stock_*` / `work_logs` is a free text key into it, not a FK — don't add a FK constraint without a data-migration plan.
- The "Main Warehouse" row is auto-created by `initialize_database()` on first run (`is_main=1`, `project_id=NULL`). Stock logic relies on it existing — keep that seed.
