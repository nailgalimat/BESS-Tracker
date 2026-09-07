# BESS Analytics & Reporting — Architecture Extension Plan

**Project:** ACWA RIVERSIDE BESS (1 site · 9 zones · 70 blocks · 140 LC containers)
**Scope:** Extend the monthly analytics/reporting tool with block-level KPIs, RTE/EFC, state-machine, anomaly detection, and richer reports. **No existing logic, formulas, or outputs are removed or rewritten.**

---

## 1. Current State Inventory

### 1.1 Codebase (services & UI, line counts)

| Layer | Module | LoC | Touched recently? |
|---|---|---:|:---:|
| service | `availability_service.py` | 213 | ✓ (technical-availability fix) — **LOCKED, do not touch** |
| service | `scada_report_service.py` | 1076 | ✓ (capacity + cycle metric) — **report orchestrator, extend, do not rewrite** |
| service | `analytics_service.py` | 356 | — |
| service | `kpi_service.py` | 226 | — |
| service | `report_service.py` | 88 | — |
| service | `asset_service.py` / `lifecycle_service.py` / `checklist_service.py` / `material_service.py` / `project_service.py` / `stock_service.py` / `work_log_service.py` / `log_service.py` / `edit_serials_service.py` | various | — |
| ui | `scada_report_page.py` | 539 | ✓ (file pickers + cycle target) |
| ui | `analytics_view.py`, `reports_view.py`, `dashboard_page.py`, `kpi_page.py`, `lifecycle_view.py` | various | — |
| db | `database/db_manager.py` | — | Schema migrations (e.g. `availability_exclusions` time columns) — careful with backward compat |

### 1.2 DB schema (existing, **not to be modified destructively**)

- `projects` (1 row: ACWA RIVERSIDE BESS, 9 zones × 8 blocks)
- `containers` (420 rows: 279 Battery + 70 PCS / Converter + 70 Communication + 1 Other)
- `availability_exclusions` (with `time_from` / `time_to` migration applied)
- Plus: `daily_logs`, `work_logs`, `checklist_*`, `stock_*`, `materials`, `warehouses`, `asset_details`

The block hierarchy is **already in the data model**: `containers.zone_number` + `containers.block_number` + `containers.container_index` + `container_type`. Per block: ≈4 Battery rows + 1 PCS + 1 Communication. The SCADA channel `LC200 BB.CC` maps **block BB, container CC** — so 70 blocks × 2 SCADA containers = 140 LCs. The "Battery" rows in DB are sub-components (racks/modules) of those SCADA containers.

### 1.3 SCADA exports we currently consume

| File | Granularity | Per-channel parameter | Maps to |
|---|---|---|---|
| `working status.xlsx` | Hourly | `SYSTEM WORKING STATUS` ∈ {RUNNING, STANDBY, FAULT, STOPPED} | per LC container |
| `charge_discharge status.xlsx` | Hourly | `SYSTEM CHARGING/DISCHARGING STATUS` ∈ {CHARGING, DISCHARGING, NON-OPERATING MODE} | per LC container |
| `LC daily charge.xlsx` | 30-min | `DAILY CHARGE ENERGY (kWh)` — resets at midnight | per LC container |
| `LC Daily discharge.xlsx` | 30-min | `DAILY DISCHARGE ENERGY (kWh)` — resets at midnight | per LC container |
| `exported_imported_.xlsx` / `import_export_.xlsx` | Daily | Energy exported/imported | site total |
| `Alarm_report__*.xlsx` | Event log | Communications / Production / Warning sheets | per device |
| `LC to tal discharge.xlsx` / `Total Lc  charge.xlsx` | Single snapshot | Lifetime cumulative counters | per LC, plus grand total |

---

## 2. Missing Data Assessment

### REQUIRED (currently complete enough — keep monitoring)

| Dataset | Status | Notes |
|---|---|---|
| Running status history | ✓ have | Hourly, full month |
| Work status history | ✓ have | Same file, redundant with above |
| Alarm/event logs | ✓ have | Sheets: Communications, Production, Warning |
| Cumulative charge energy | ✓ have | Daily totalizer + lifetime snapshot |
| Cumulative discharge energy | ✓ have | Daily totalizer + lifetime snapshot |
| **SOC history** | **✗ MISSING** | Critical — see below |

### HIGHLY RECOMMENDED — currently missing

| Dataset | Why it matters | Preferred export | Resolution |
|---|---|---|---|
| **PCS active power** (kW) per block | Needed to (a) validate the energy totalizers (∫P dt ≈ ΔE), (b) detect grid-side curtailment vs battery-side limit, (c) drive state machine when status flags are ambiguous, (d) compute auxiliary losses | xlsx, "PCS - ACTIVE POWER (kW)" per block, signed (positive = export) | 5- or 15-min averages |
| **SOC per container / per block** | Required for: cycle depth (DoD), RTE quality flag (charge/discharge must start/end at comparable SOC for daily RTE to be valid), EFC accuracy, anomaly detection (SOC drift, BMS errors) | xlsx, "BMS - SOC (%)" per container | 15-min average |
| **Block availability status** | Currently inferred from container OR/AND. Explicit block-level flag would remove ambiguity (e.g. one container fault but block kept operating at half power) | xlsx, "BLOCK - AVAILABILITY" per block | Hourly |
| **Downtime / event classification** | Today we count alarms but don't classify outage cause (BMS / PCS / HVAC / Aux / Comm). Without classification, root-cause attribution is impossible | column "Event Class" added to alarm export | event-level |
| **SOH snapshots (first / last day) OR daily SOH** | Degradation tracking. Even just two snapshots per month enables month-over-month delta; daily snapshots enable trend curves and early-warning anomalies | xlsx, "BMS - SOH (%)" per container, monthly minimum | daily preferred, first-and-last-day acceptable |
| **Temperature data** (battery min/avg/max + cabinet) | Required for: thermal fault correlation, derating analysis, HVAC effectiveness, hot-spot detection | xlsx, "BMS - TEMP MAX / MIN / AVG (°C)" per container | 15-min or hourly |
| **HVAC status / power** | Auxiliary consumption (drags RTE down), thermal-management diagnostics, climate-driven aux load | xlsx, "HVAC - STATUS" + "HVAC - POWER (kW)" per block | Hourly |

### OPTIONAL BUT VALUABLE — missing

| Dataset | Value |
|---|---|
| PF (Power Factor) at POI | Grid-code compliance reporting |
| DC voltage / current per rack | BMS imbalance and PCS efficiency diagnostics |
| Auxiliary consumption (lighting, panels, network) | Net energy delivered (true RTE) |
| Rack imbalance (cell ΔV, SOC spread) | Early-warning cell failure |
| Temperature spread per container | Cooling-system uniformity |

### Cross-cutting comments

- **Granularity mismatch is acceptable** for monthly KPIs (existing daily totalizer at 30-min resolution is fine after max-per-day aggregation). For diagnostics and state-machine work we'd ideally have everything at 5- or 15-min.
- **Do not invent fake data.** All new KPIs that depend on missing data must either (a) be gated behind "data available?" checks and shown as "N/A — requires SOC export" in the report, or (b) compute a quality-flagged best-effort number with the flag visible to the reader.

---

## 3. Block-Level Aggregation Mapping

SCADA channel naming → block:

```
'Tashkent - LC200 03.02 - LC - SYSTEM WORKING STATUS'
                  ^^  ^^
                  ||  └─ container index (.01 or .02)
                  └──── block number (1..70)
```

Block-level rollup rules (no existing logic touched — these run **alongside** container-level):

| Container metric | Block-level rule | Reason |
|---|---|---|
| Working status (RUNNING/…) | Block-RUNNING if **both** containers running, Block-PARTIAL if one only, Block-FAULT if either fault | Operational reality — a block delivering half power is still partially available |
| Daily charge energy (kWh) | Sum of both containers | Block energy = container A + container B |
| Daily discharge energy (kWh) | Sum of both containers | Same |
| Alarms | Union, deduplicated by event-id if available | Avoid double-counting fleet-side alarms |
| Availability % | **Reuse the validated container-level engine** then aggregate by simple weighted mean over both containers | Preserves the existing audited formula |

---

## 4. Proposed New Modules

All new modules sit **alongside** existing services. Nothing existing is removed or modified beyond pure additive plumbing in `scada_report_service.py` (one extra optional section, gated behind data availability — same pattern we already used for capacity availability).

```
services/
├─ availability_service.py        ← UNCHANGED (locked)
├─ scada_report_service.py        ← extended only with optional new sections,
│                                    each gated behind a feature flag
│
├─ block_topology_service.py      ← NEW: container ↔ block mapping; reads
│                                    project DB and SCADA channel names
├─ scada_data_layer.py            ← NEW: thin "raw → cleaned" adapter that
│                                    standardises timestamps and column names
│                                    without touching the original files
├─ state_machine_service.py       ← NEW: per-block operational state
│                                    {Charging, Discharging, Idle, Standby,
│                                     Fault, Maintenance, Offline}
├─ rte_engine_service.py          ← NEW: daily/monthly RTE, throughput, EFC,
│                                    cycle depth (operational + nameplate)
├─ kpi_quality_service.py         ← NEW: VALID / ESTIMATED / INCOMPLETE /
│                                    EXCLUDED tagging engine
├─ block_kpi_service.py           ← NEW: assembles the Daily Block KPI table
│                                    (date, block, charge, discharge,
│                                    throughput, RTE, EFC, availability,
│                                    operating hours, alarms count, flag)
├─ anomaly_detection_service.py   ← NEW: fleet-mean deviation flags
│                                    (RTE drop, low throughput, thermal,
│                                    chronic underperformer)
└─ event_correlation_service.py   ← NEW: link alarms to RTE drops /
                                       availability incidents / temperature
                                       anomalies via time-window overlap
```

UI additions (new pages, not replacements):

```
ui/
├─ scada_report_page.py           ← UNCHANGED interface; new optional toggles
│                                    "Generate block KPI section",
│                                    "Generate RTE/EFC section"
└─ block_performance_page.py      ← NEW: daily heatmap, RTE/throughput
                                       trends, worst-performing block ranking
```

DB additions (new tables, no schema changes to existing ones):

```
kpi_daily_block         (date, block_id, charge_kwh, discharge_kwh,
                          throughput_kwh, rte_pct, efc, availability_pct,
                          operating_hours, alarms_count, quality_flag,
                          state_distribution_json)

kpi_monthly_block       (month, block_id, avg_rte, avg_throughput,
                          total_efc, availability_pct, est_soh_delta,
                          worst_day, anomaly_count)

state_history           (timestamp_start, timestamp_end, block_id,
                          state, source, evidence)

anomaly_events          (date, block_id, kind, severity, value, threshold,
                          ref_value, context_json)
```

---

## 5. Safe Refactoring Plan (preserves existing behaviour)

**Phase 0 — preparation (no code changes)**
1. Lock `availability_service.py` (regression tests below).
2. Snapshot the current monthly PDF as a "golden file" for one historical period.

**Phase 1 — additive scaffolding**
1. Add `block_topology_service.py` — pure mapping helpers, zero side effects.
2. Add `scada_data_layer.py` — wraps existing loaders; no changes to loader signatures.
3. Add `state_machine_service.py` — runs only when both working_status and charge_discharge_status are available (already the case today).
4. Add `rte_engine_service.py` — runs only when LC daily charge **and** discharge files are present (already the gating condition for the capacity section).

**Phase 2 — KPI assembly**
5. Add `kpi_quality_service.py`.
6. Add `block_kpi_service.py` — depends on phases 1+2.
7. Add `anomaly_detection_service.py` — runs after `block_kpi_service`.

**Phase 3 — report extension**
8. Extend `scada_report_service.generate_scada_report()` with **new optional sections** (each guarded by `if cap_kpis is not None` style). Existing sections render identically when the new inputs are absent.
9. Persist new tables to DB via additive migrations (`CREATE TABLE IF NOT EXISTS`).

**Phase 4 — UI**
10. Add optional toggles to `scada_report_page.py` (default ON when data is present).
11. Add new `block_performance_page.py` as an extra tab — does not replace `analytics_view.py`.

**Regression guard rails**
- For each phase, run the existing test month through the report with new modules **disabled**. Diff the PDFs page-by-page; only the new optional pages should appear. Existing pages must be byte-equivalent.
- Add a small pytest suite around `availability_service.calc_availability()` to lock the technical-availability math we recently fixed.

---

## 6. Example KPI Workflow (one day, one block)

```
INPUTS for date=2026-03-15, block=03:
  working_status        24 rows × 2 containers  → BLOCK state series
  charge_discharge      24 rows × 2 containers  → BLOCK CD state series
  LC daily charge       48 rows × 2 containers  → max-per-day per container
  LC daily discharge    48 rows × 2 containers  → max-per-day per container
  cycle target          row from cycle_targets  → target=2
  exclusions            any covering this day   → planned-maintenance hours
  alarms                events on this day      → count + classification

STEP 1 — state_machine_service:
  for each hour in {0..23}:
    determine block state from container-level inputs
    record into state_history
  Result: state_distribution = {Charging: 5h, Discharging: 5h, Idle: 13h,
                                Fault: 1h}

STEP 2 — block_topology_service.rollup_energy():
  charge_block    = container.01 + container.02   = 9,840 kWh
  discharge_block = container.01 + container.02   = 9,210 kWh
  throughput      = charge + discharge            = 19,050 kWh

STEP 3 — rte_engine_service.daily_rte():
  rte = 9210 / 9840 * 100                         = 93.6%
  efc = throughput / (2 × block_nameplate_kWh)
      = 19050 / (2 × 11008)                       = 0.865 EFC
  cycle_count_op  = min(charge, discharge) / 4954 = 1.86

STEP 4 — availability_service (UNCHANGED):
  Reuse calc_availability() at the container level, then weighted-mean to block.
  Block availability = 96.4%

STEP 5 — kpi_quality_service.flag():
  has_full_24h_data:   yes
  throughput_min_met:  yes (> 5000 kWh)
  no_excluded_overlap: yes
  no_state_machine_gap: yes
  → quality_flag = VALID

STEP 6 — anomaly_detection_service:
  fleet_avg_rte = 92.1%
  block_rte     = 93.6%   delta = +1.5pp  → no flag
  fleet_avg_throughput = 19,200 kWh
  block_throughput     = 19,050 kWh        → no flag

STEP 7 — event_correlation_service:
  Fault hour 14:00–15:00 — search alarms in [13:45, 15:15]:
    matched: 1 Production alarm "BMS HIGH TEMP" on container 03.02
  → write anomaly_event(kind='fault_correlated', severity='medium')

STEP 8 — persist:
  kpi_daily_block row appended with quality_flag=VALID
  state_history rows appended
```

---

## 7. Report Structure Enhancements

Existing PDF sections (kept as-is):

1. Cover
2. Executive Summary
3. System Availability  (with optional Exclusions sub-section)
4. Capacity Availability (per LC, per cycle)
5. Charge / Discharge Operations
6. Energy Performance
7. Alarm & Fault Analysis
8. Conclusions

New sections (all optional — render only when data is present):

| # | New section | Depends on |
|---|---|---|
| 3a | **Site Overview** (single page with block topology and active LCs) | block_topology |
| 4a | **Daily Block Performance** (one row per block × day matrix, sorted by RTE) | block_kpi |
| 4b | **RTE & EFC Trends** (daily RTE per block + fleet average band) | rte_engine |
| 4c | **Operational State Distribution** (stacked-bar per block, monthly hours per state) | state_machine |
| 7a | **Major Incidents** (event-correlated outages with root-cause class) | event_correlation |
| 8a | **Battery Health** (SOH delta if SOH data available, otherwise "N/A — SOC export needed") | future, gated |
| 9 | **Maintenance Activities** (driven by daily_logs / work_logs tables — already in DB) | log_service |
| 10 | **Recommendations & Risks** (auto-generated from anomaly_events) | anomaly_detection |
| 11 | **Appendix** (raw KPI tables, definitions, formulas, data-quality notes) | always |

---

## 8. Visualisation Roadmap

All new charts use the same matplotlib Agg pattern as existing ones (`fig_to_image()` helper). Reusing the palette already in `scada_report_service.py` keeps the look consistent.

- **Daily RTE trend** — line chart per block + fleet mean band
- **Daily throughput trend** — stacked bar by zone, line for fleet
- **Availability trend** — already exists, add overlay of technical vs raw vs capacity
- **Alarm statistics** — already exists, add classification breakdown
- **Heatmap per block** — 70 blocks × 31 days grid, colour = availability or RTE
- **Performance-deviation chart** — block vs fleet mean with ±1σ band
- **State-distribution stacked bar** — one bar per block, segments per state, monthly hours

---

## 9. Risks in the Current Implementation

| Risk | Where | Why it matters | Suggested mitigation |
|---|---|---|---|
| Cycle metric formula uses `min(charge, discharge)` | `calc_capacity_availability()` | A genuinely discharge-only day (e.g. peak-shaving) shows zero cycles even if discharge hit the target. | Add a configurable mode: `min` / `discharge_only` / `avg`. Default unchanged. |
| 30-min totalizer max-per-day | `load_lc_daily_energy` | Daily totalizer that "resets at midnight" has been observed to read at 12:30 AM with previous-day residual (1,673 kWh discharge). Max-per-day still works because the residual is below the new day's peak, but a near-zero-throughput day could read the residual instead. | Switch to *last-reading-before-midnight* with a sanity fallback to max. Quality-flag suspect days. |
| Affected-Blocks field hidden but stored | `availability_service.calculate_excluded_hours` | If the dialog ever re-enables the field, the calculator still defaults `all_blocks=[1]` (hard-coded). | Plumb real project block info from `projects.num_blocks`. Already on the deferred list. |
| Exclusions stored as date-strings | DB schema | Time-zone ambiguity once we have multiple sites. | Add `tz_offset` column (additive migration). |
| No regression tests on availability math | repo-wide | Future edit could silently re-introduce the proportional-reduction bug. | Add pytest fixtures with the canonical fake-100-hour dataset. |
| Single-file PyInstaller build | `BESS Tracker.spec` | 76 MB exe, cold start 6–10s. Acceptable for desktop tool but limiting for field-laptop deployment. | Consider `onedir` build with shared dependencies for shipping to multiple sites. |

---

## 10. Future Scalability

| Concern | Today | Future direction |
|---|---|---|
| Multi-site | single project | Add `site_id` foreign key on every KPI/state/anomaly table. Existing data backfilled to site=1. |
| Higher granularity | hourly + 30-min | New `kpi_15min_block` table once SOC and PCS power exports arrive |
| Live data | manual xlsx import | SCADA REST or OPC-UA push → ingestion service → same KPI engine |
| Multi-user | desktop SQLite | When ≥3 concurrent users: migrate to Postgres. Keep service layer ORM-agnostic so swap is trivial. |
| Plugin metrics | hard-coded in code | Move per-cycle capacity and divisor choice into project settings (already proposed) |
| Cycle-target source | xlsx file | Eventually pull from contract management module |

---

## 11. What I'd Recommend Doing First (in order)

1. **Get SOC + PCS power exports** from SCADA (highest-impact gap).
2. **Capture SOH snapshots** at the start and end of the reporting month — at least monthly.
3. **Lock the availability engine** with pytest fixtures so we can extend confidently.
4. **Build `block_topology_service` + `block_kpi_service`** (additive, low-risk, high reader value).
5. **Add `rte_engine_service`** once SOC export is available — RTE without SOC quality flagging is misleading.
6. **Add new PDF sections** one at a time, each behind a feature flag.

Each of these is a self-contained PR with backward-compatible behaviour.

---

*Document version 1.0 — written before any code changes.*
*Next step: confirm scope of phase 1 and the missing SCADA exports.*
