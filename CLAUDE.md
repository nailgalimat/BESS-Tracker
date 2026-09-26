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

# release: version file + exe + clean DB + Inno Setup, in one command
python tools\build_release.py              # → installer\BESS_Tracker_Setup_<ver>.exe
python tools\build_release.py --skip-exe   # re-make the installer from the existing exe
```

`services/version.py` holds `APP_VERSION` — the window title, the generated
`installer/version.iss` and the installer's file name all read it. The installer
ships the exe, a schema-only database from `tools\make_clean_db.py`
(`build\clean\pv_bess_tracker.db`, never a copy of the live one) and
[docs/SETUP_FOR_A_NEW_TEAM.md](docs/SETUP_FOR_A_NEW_TEAM.md), the guide for a
second team standing the app up on their own PC and their own server. Two tests
pin that: `test_installer_manifest.py` (what the .iss ships, plus the build
script's refusal to compile an .iss naming the live DB or `sync_config.json`) and
`test_clean_db.py`.

### Tests

```powershell
python tests\run.py                  # fast suite, ~1-2 min
python tests\run.py --real           # + generates the real August report from SCADA exports (~10 min)
python tests\run.py planner cycles   # only tests whose name contains a word
```

Run `--real` before every exe build and before a report goes to a customer: frame-level tests cannot catch a crash in the full report pipeline, and one shipped that way. No linter config and no CI.

**Every test imports `tests/_harness.py` first**, before any project module. It points `BESS_DB` at a SQLite-backup snapshot of `dist/pv_bess_tracker.db`, points `BESS_SYNC_CONFIG` at a config with sync off (the real `sync_config.json` is enabled with live tokens, and `MainWindow` starts a sync worker when it is), makes any non-localhost HTTP request raise, and redirects `data/monthly_history*.json` to copies (the report generator writes its month back, and that file is bundled into the exe). `test_harness_isolation.py` proves each of these — if it fails, run nothing else. Outcome is the final `RESULT PASS|FAIL|SKIP` line, not the exit code: PyQt can fault while tearing down the QApplication after every check has run. Tests needing SCADA exports read `BESS_SCADA_DIR` (default: the Desktop LTSA folder) and SKIP when absent — customer data is never committed.

`GOLDEN` in `test_real_tashkent_august.py` holds accepted customer-visible numbers. That test computes them from **frozen** inputs — `frozen_report_inputs_2026-08.json` in the August SCADA folder, or the file `BESS_FROZEN_INPUTS` names (a version manifest works too) — written on the first run and never committed, and it checks the live snapshot still matches them. Changing one on purpose means updating it in the same commit.

Note the requirements pin is loose (`>=`) — when reproducing a user-reported bug, ask which versions they have rather than assuming.

## Architecture

Strict three-layer split — touch the right layer:

- **`database/db_manager.py`** — *single source of truth* for schema. All `CREATE TABLE` / `CREATE INDEX` / lightweight `ALTER TABLE` migrations live in `initialize_database()`, which is called on every app startup and is safe to re-run. **Do not** scatter `ensure_*_table()` helpers across service files — the docstring at the top of `db_manager.py` explicitly calls this out. To add a column, add an `ALTER TABLE … ADD COLUMN` guarded by a `PRAGMA table_info` check, in the same style as the existing `sap_ticket`, `project_type`, and `time_from/time_to` migrations.
- **`services/*.py`** — pure DB / business logic. Each service owns one domain (project, log, work_log, material, stock, checklist, asset, kpi, lifecycle, analytics, availability, plus the three report generators). Services import `database.db_manager.get_connection` and return plain dicts / dataclasses. **No PyQt imports in services.**
- **`ui/*.py`** — PyQt5 widgets only. `ui/main_window.py` is a sidebar + `QStackedWidget`. Page indices are constants at the top of `main_window.py` (`PAGE_DASHBOARD`, `PAGE_BLOCK_RPT`, etc.) and equal the stack index — tests rely on that, so new pages are **appended**, never inserted. The menu is the `NAV` table (nine items: Today · Work · Plan · Equipment · Availability · Monthly report · Analysis · Spare parts · Project); every other page is listed in `ARCHIVE` and reached from Project → "Archive of old pages" (nothing deleted). Adding a page: constant, import, `PAGE_NAMES`, the stack list, and a `NAV` or `ARCHIVE` row. A page gets the open project either through `set_current_project(pid[, name])` or simply by owning a `proj_combo` / `project_combo` (the shell selects the project in it); pages in `MONTH_PAGES` follow the header's month through `set_month(year, month)`.

### Work records and the Today screen

- **`services/work_journal_service.py`** reads `work_log_entries` (the live record, phone and desktop), `work_logs` (old Work Reports — shown as "Old format", **read-only**) and `daily_logs` (the old Daily Log — shown as "Daily Log", **read-only history**) as one list.
- **Old Daily Log rows are history, not work.** A `daily_logs` row is one material issued against one container with a comment describing the work — not a work report despite the table name. `services/log_service.consumption_history()` is the one query behind both views: the Work list (the work's side) and Spare parts → Consumption history (the material's side); do not re-query `daily_logs` in a page. The rows carry `source='daily'`, `editable=False`, `kind='Other'` and `status='Done'`, so they can never reach `open_faults()`, the Today counts or the stale-fault list, and the Work card offers no Save, Delete, Mark done or write-off. They never feed `corrective_rows` / `pm_activities` / the DOCX-PDF sections — materials consumption is internal. **Nothing may move stock from these rows**: whatever they deducted was booked when they were written, so re-deducting or reversing would double it. The `noblock` tab predicate is `needs_block` (no block **and** not legacy) — a read-only row cannot be given a block, and counting it would inflate the Today counter it must equal. `test_legacy_daily_logs.py` pins all of this. New desktop records are written as `work_log_entries` rows so they sync like phone ones. Record fields added for the redesign — `plant_block`, `node_lc`, `node_device`, `ptw_no`, `time_from/time_to`, `hours`, `internal_note`, `availability_impact` — are optional on both server and client; `sync_client` writes them on pull **only when the server sent them**, so an older server cannot blank them. `plant_block` wins over the container's zone/block in `corrective_rows`. A record is PM only when its text says PM (`\bPM\b|preventive`, the report's own 3.2 test); a PM saved from the desktop or the phone gets "PM: " in its customer line so it is reported once, as PM hours.
- **`services/today_service.py`** returns every Today block as a list; the page shows `len()` and its link opens the same filter in Work (`filter_rows`) — keep counts and lists on one query.
- The Monthly report's availability-inputs tab is **hosted** by `ui/availability_page.py` (`take_inputs_tab` / `adopt_editor`): one editor, shown on the Availability page. The page never computes the availability percentage — the report generator owns that number.
- **`models/models.py`** — dataclasses (`Project`, `Container`, `LogEntry`) and the `PROJECT_TYPES` / `CONTAINER_TYPES_BY_PROJECT` / `default_container_type()` lookups that the project-creation wizard depends on. Three project types coexist: `BESS`, `PV String`, `PV Central`, each with its own container-type list and default-assignment rule.

### Frozen-vs-dev path resolution

`database/db_manager._get_db_path()` picks the DB location based on `sys.frozen`:

- dev: `<repo>/pv_bess_tracker.db`
- frozen exe: `<dir of exe>/pv_bess_tracker.db` (i.e. `dist/pv_bess_tracker.db`; the installer puts the app in `{localappdata}\Programs\BESS Tracker` and copies the clean DB there with `onlyifdoesntexist` so user data survives upgrades)
- frozen exe whose own folder is **not writable** (someone copied it into `C:\Program Files`): `%LOCALAPPDATA%\BESS Tracker\pv_bess_tracker.db`. Writability is a real write probe (`_dir_writable`), because `os.access` lies on Windows; a writable folder is unaffected, so existing installations keep their file. `test_db_path_fallback.py` pins all three. `image_service._get_field_images_dir()` follows the database rather than the exe (`dirname(DB_PATH)` when frozen), so photos never end up in a folder the app cannot write to while the database itself has moved.

Any new code that needs a runtime-writable file next to the app must follow the same `sys.frozen` pattern. Read-only bundled resources (e.g. `data/alarm_classifications.csv`) are loaded via `os.path.dirname(__file__)`-relative paths and bundled into the exe through the `datas` list in `BESS Tracker.spec` — see that file's `_hist` block for the "bundle if it exists" pattern.

### SQLite pragmas

`get_connection()` sets `foreign_keys=ON`, `journal_mode=WAL`, `cache_size=-8000`, `synchronous=NORMAL`. WAL means `pv_bess_tracker.db-wal` / `-shm` sidecar files are normal; back up the trio, not just the `.db`. The `db_backups/` folder holds timestamped snapshots — preserve them.

### Availability exclusions are shared across all reports

There is **one** exclusions store: the `availability_exclusions` table, fronted by [services/availability_service.py](services/availability_service.py). Both the SCADA Report page ([ui/scada_report_page.py](ui/scada_report_page.py)) and the Block Performance page ([ui/block_report_page.py](ui/block_report_page.py)) call `get_exclusions()` with **no project filter**, so an exclusion added on either page is automatically applied by both. The `ExclusionDialog` widget lives in `ui/scada_report_page.py` and is imported by the block page — do not duplicate it.

The math is applied in two slightly different ways:

- **SCADA** uses **device-hours** via `calculate_excluded_hours()` → subtracted from the denominator of `calc_availability()` in `scada_report_service.py`.
- **Bukhara / Tashkent** use **plant-wall-clock hours** via `calculate_plant_excluded_hours()` (weighted by `n_affected_blocks / total_blocks`) → fed into `calc_plant_hours_availability[_tashkent]()` as the optional `excluded_hours=` kwarg.

Both follow the same "cap-then-remove-from-denominator" pattern: `excluded_effective = min(excluded, plant_outage)`, then `adjusted = (scheduled − plant_outage) / (scheduled − excluded_effective)`. This guarantees `excluded_hours=0` produces byte-identical output to the pre-exclusion code path — so any new report that doesn't use exclusions still calculates exactly as before.

### PM records and the month's availability inputs

- **One PM record per (project, plant block, date).** Every PM write — phone event, planner completion, Excel import, the Monthly Reports PM table — goes through `report_workflow_service.record_pm` / `update_pm_record`, which validate (`validate_pm`: a block, the whole plant only as `'all'`; 0 < h ≤ 24; dates 2020-01-01..tomorrow), split multi-block entries, and raise `PMDuplicateError` for a block-day that already has a record (callers answer `update` / `skip`, the planner `link`). `pm_as_unavailability` charges a block-day once and never treats an empty block as the whole plant. `pm_activities.source/source_ref` say who wrote a record. Do not insert into `pm_activities` directly.
- **Phone events** go through `availability_inputs_service.route_field_event`: PM applies unless it needs a decision; `counts` / `excluded` always wait in `field_event_queue` until confirmed on the Monthly Reports → Availability inputs tab (real times and blocks) or rejected. Nothing in the queue counts.
- **A PM record covers its own stop** in the Tashkent engine (`pm_stop_windows`: on the PM date, the longest run of the block out of operation that overlaps 07:00–19:00); its SCADA downtime is not counted, the PM hours are, and its alarms are tagged like a Scheduled Maintenance window's (`tag_alarms_with_exclusions`), so they leave 3.2 / 5.1 / Faults-Warnings / 5.2. A stop with no PM record for that block and day is never covered. Logged as "PM stop … / PM-covered"; `test_pm_covers_stop.py` pins the definition. Only `pm_activities` rows (marked `pm_id` in `report_inputs`) trigger it.
- The PWA shell is cached under the exact `?v=` URLs: bump `V` in `backend/static/sw.js` and the `?v=` in `index.html` together. Manual check: `tests/PWA_OFFLINE_CHECK.md`.

### The monthly report is made in four steps (Data · Check · Customer text · Release)

`ui/monthly_reports_page.py` is one tab per step, over four services:

- **`month_dataset_service`** — the month owns its SCADA files. `add_files` recognises each file's type from its sheet names and column headers (header rows only, via openpyxl read-only), copies it into `report_data/p<project>/<YYYY-MM>/inputs/` **next to the database** (`parse_cache.data_root()` derives it from `db_manager.DB_PATH`, so the exe keeps it beside `dist\pv_bess_tracker.db` and a test keeps it in its work folder), and stores its SHA-256. Nothing is guessed: an unknown file comes back `unrecognised` / `ambiguous` for the user to choose or skip. `analyse_file` reads each copy once and stores coverage (days with data, part days, gaps > 1 h). `generation_paths` hands the generator the stored copies — **a re-generation never picks files again**. Add a new export type in `TYPES` (its `param` is the generator's keyword) and a rule in `recognise`.
- **`parse_cache`** — `read_excel(path, **kw)` caches what `pd.read_excel` returned, keyed on the file's SHA-256 + read arguments + pandas/openpyxl versions, but **only for files inside `report_data/`**; every other path (the Block Performance pickers, the tests' SCADA folder) reads as before. The loaders in the Tashkent/Bukhara services call it instead of `pd.read_excel`, and they still run on every generation — so a fix in a loader needs no cache invalidation. August 2026: 94 s → ~30 s.
- **`report_precheck_service`** — `run_precheck` is **read-only** and returns the questions before generating (missing files, exclusion window edges vs SCADA, waiting phone events, PM and manual-downtime flags, 3.2 records held back, data gaps, month-end files), each with one action and the tab that fixes it. Generating with open questions needs an explicit confirmation, which the manifest records.
- **`report_versions_service`** — every generation writes a new `<Project>_LTSA_<YYYY-MM>_v<N>.docx` (never overwritten; both it and its manifest are made read-only) plus a manifest: input file hashes, the exact `report_inputs`, PM records, customer text, KPI history, the alarm-classification CSV hash, app/commit and a fingerprint of the report code, the confirmations, and the key numbers with the 3.1/3.2/4.4.1/5.2 row counts. `mark_sent` stores a copy of the Word-edited file and its SHA-256 with the version it is based on.

**A sent month is locked.** `mark_sent` sets `report_months.locked_at`; `report_workflow_service.assert_month_open` is called by *every* writer of a month's inputs (exclusions, manual downtime, balancing, PM, narrative, data set), so no page, import or phone event can change a sent month — a phone PM for it waits in `field_event_queue` with reason `locked`. `report_versions_service.unlock_month` needs a reason and logs it in `report_month_log`. Callers see `MonthLockedError` (a `RuntimeError`).

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
