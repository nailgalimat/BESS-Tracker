# BESS Tracker — Desktop Rebuild Guide

## What changed since the last build

| Area | Change |
|---|---|
| **New sidebar tab "Block Performance"** | Unified UI for the new Bukhara and Tashkent block-level monthly reports. Pick site type, fill in capacity + narrative fields, choose output format. Calls `services.bukhara_report_service.generate_bukhara_report` or `services.tashkent_report_service.generate_tashkent_report` based on the selection. |
| **SCADA Report tab** | Block-selection checkbox grid in the Add Exclusion dialog (replaces the hidden text field). Plant Total / Per-block / **Contractual** / Redundancy threshold capacity inputs in the Site Info group. Project's real block IDs now flow through to `calculate_excluded_hours` (the hardcoded `[1]` is gone). |
| **Availability engine** | `contractual_plant_capacity_mw` added — when set, the plant is "available" when block-aggregate capacity ≥ contractual MW (SLA-based). Falls back to `redundancy_threshold_pct × plant_capacity_mw` when contractual is 0. Both numbers shown in the report. |
| **DOCX export** | All new reports can now emit `.docx` alongside `.pdf`. Output format selector in the Block Performance page: PDF / DOCX / Both. Same 8-section Sungrow-style layout, embedded charts as PNG. |
| **Alarm classification** | Lookup CSV (`data/alarm_classifications.csv`, 88 patterns) plus dedup heuristic — production-event coverage >99%, warnings >99%. Echo duplicates (BSC 01.02XXX glued-prefix variants) are folded. |
| **Monthly history persistence** | `data/monthly_history.json` — each run appends the current month's KPIs so next month's report shows a 5-month trend table automatically. |

## Files added or changed

```
ui/block_report_page.py            NEW   (508 lines — unified block-level report UI)
ui/main_window.py                  edit  (sidebar entry "Block Performance" at index 13)
ui/scada_report_page.py            edit  (block-selection widget, contractual capacity)
services/bukhara_report_service.py edit  (contractual capacity, docx export)
services/tashkent_report_service.py edit  (contractual capacity, docx export)
services/docx_renderer.py          NEW   (318 lines — shared Word-doc helpers)
services/scada_report_service.py   edit  (project_blocks + contractual params)
services/availability_service.py   edit  (already had period clipping + technical-availability fix)
data/alarm_classifications.csv     NEW   (88 trigger-pattern entries)
data/monthly_history.json          NEW   (5-month history bootstrap)
run_bukhara_report.py              edit  (--format flag)
run_tashkent_report.py             edit  (--format flag)
requirements.txt                   edit  (added matplotlib, reportlab, python-docx, pyinstaller)
BESS Tracker.spec                  edit  (hidden imports + data file bundling)
```

## Database safety net

Five timestamped DB snapshots under `db_backups/`. The newest is
`pv_bess_tracker_DIST_final_<timestamp>.db` — that's the live DB from
`dist/pv_bess_tracker.db` just before this rebuild. PyInstaller will not
touch `dist/pv_bess_tracker.db`; if anything goes wrong, copy any of the
snapshots back to `dist\pv_bess_tracker.db`.

## Rebuild command (run on the Windows machine)

```
cd C:\Users\user1\MVP
pip install -r requirements.txt
pyinstaller --clean "BESS Tracker.spec"
```

When PyInstaller finishes, the new exe is at `dist\BESS Tracker.exe`. The
data folder is bundled inside the exe — no extra files to copy.

## First-run checklist (in the rebuilt app)

1. Click **Block Performance** in the sidebar (under REPORTS & KPI).
2. Pick **Bukhara** or **Tashkent** at the top.
3. Fill in:
   - Site / Project Name (required)
   - Customer, O&M Company, Project Capacity (text), Equipment — optional
     but they populate the Project Details table on page 1 of the report.
4. Capacity row:
   - **Plant total**: nameplate, e.g. 63 MW
   - **Per-block**: e.g. 4.2 MW for Bukhara, 11 MW for Tashkent
   - **Contractual**: SLA-guaranteed MW (if you set this, the plant
     is "available" when block-aggregate capacity ≥ Contractual)
   - **Redundancy threshold**: only used if Contractual is left at 0
5. Pick the input files (5–8 file pickers, depending on site type).
6. Fill in operator narrative — PM, CM, Site Visits, Recommendations,
   Planned Activities (one entry per line). Leave blank to skip — CM
   auto-generates from alarm classification.
7. Pick an output path and a format (PDF / DOCX / Both).
8. Click **Generate Report**. Progress shows in the log panel.

## Add Availability Exclusion dialog (existing SCADA Report tab)

The block-selection widget now lists all blocks known to the project
(queried from the `containers` table). For 70 blocks, items flow-wrap
across the dialog so they don't overflow.

- **Select all** / **Clear** helper buttons
- Live summary label: "5 of 70 blocks selected" / "None selected →
  applies to all 70 blocks"
- The exclusions table on the page shows the count + block IDs ("3
  block(s): 1,5,12")

## Known limitation

The 28 MB multi-sheet Excel files (Bukhara Battery Unit data) take 25–30
seconds to read on a typical laptop. PDF generation is fast (5–8s); DOCX
adds another 3–5s. Expect a total wall-clock of about 30–45 seconds per
report. The progress log streams updates throughout.

---

*All Python modules parse cleanly, all services import without errors,
and all changes are additive — no existing behaviour is removed or
modified beyond the fixes documented above.*
