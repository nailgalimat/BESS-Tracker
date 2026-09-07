"""
services/tashkent_report_service.py
------------------------------------
Block-level monthly operations report for the Tashkent BESS site (70 blocks).

Self-contained — does NOT modify the existing scada_report_service.py
(container-level legacy report) or bukhara_report_service.py. Shares the
classification lookup, history-persistence helpers, PDF-section style, and
chart functions of the Bukhara module by importing them.

Inputs (xlsx exports from the SCADA):
  - working_status_path:    "LC working status.xlsx"        — 5-min, 140 LCs
  - pcs_cd_path:            "PCS Charge_Discharge status.xlsx" — 5-min,
                                                                 280 PCS converters
  - soc_path:               "SOC March.xlsx"                  — 5-min, 140 LCs
  - soh_snapshot_path:      "SOH last day of month.xlsx"      — 5-min last day
  - lc_charge_path:         "LC daily charge.xlsx"            — 30-min totalizer
  - lc_discharge_path:      "LC Daily discharge.xlsx"         — 30-min totalizer
  - hv_meter_daily_path:    "HV meter daily import and export.xlsx"
  - alarm_path:             "Alarm report.XLSX"
  - lc_alarm_state_path:    "LC  alarm and fault status.xlsx"  (optional bitmask)

Output: PDF report.
"""

from __future__ import annotations
import os, re, io, warnings
warnings.filterwarnings('ignore')

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image, PageBreak, HRFlowable,
)

# Share styling + classification + history persistence + PDF helpers
# with the Bukhara service. These have no Bukhara-specific data assumptions.
from services.bukhara_report_service import (
    NAVY, BLUE, LIGHT_BLU, GREEN, ORANGE, RED, PURPLE, GREY_BG, GREY_LINE,
    TEXT_MUTE, WHITE,
    STYLE_H1, STYLE_H2, STYLE_H3, STYLE_BODY, STYLE_SMALL, STYLE_CAP,
    STYLE_KVAL, STYLE_KLBL,
    _hr, _section, _kpi_row, _fig_to_image, _styled_table,
    _build_cover,
    load_alarms, load_alarm_classifications, apply_alarm_classifications,
    tag_alarms_with_exclusions,
    summarize_alarms, detect_anomalies, build_monthly_comparison,
    load_history_records, save_history_record,
    _chart_alarm_by_class, _chart_alarm_pie_by_trigger,
    select_important_alarms, IMPORTANT_DURATION_THRESHOLD_MIN,
    build_container_day_status,
    build_unavailability_reasons,
    build_faults_summary,
    build_breakdown_candidates,
    mark_planned_stop_events,
    NOISE_TRIGGER_PATTERNS,
    _chart_availability_heatmap_categorical,
    _chart_single_daily_bar,
    format_exclusion_narrative,
    AVAILABLE_STATES, RUNNING_STATES, STANDBY_STATES, FAULT_STATES,
    FAULT_SHUTDOWN_STATES,
    STOPPED_STATES,
    calc_block_availability,   # operates on a df with 'working_status' column
)


LC_RE  = re.compile(r'LC200\s*(\d+)\.(\d+)')         # captures block, container
CMU_RE = re.compile(r'CMU\s+(\d+)\.(\d+)\.(\d+)\.(\d+)')  # block.container.rack.cmu


# ── LOADERS ───────────────────────────────────────────────────────────────────

def _parse_date(df):
    if 'Time' in df.columns:
        ts = pd.to_datetime(df['Date'].astype(str) + ' ' + df['Time'].astype(str),
                            errors='coerce')
    else:
        ts = pd.to_datetime(df['Date'], errors='coerce')
    df = df.copy()
    df['Datetime'] = ts
    df['Date_only'] = ts.dt.date
    return df


def load_lc_working_status(path):
    """5-min working status per LC. Returns long-format DataFrame with columns:
    Datetime, Date_only, block_id, container_id, working_status."""
    df = pd.read_excel(path)
    df = _parse_date(df)
    long = []
    for c in df.columns:
        m = LC_RE.search(str(c))
        if not m or 'WORKING STATUS' not in str(c).upper():
            continue
        block_id, container_id = int(m.group(1)), int(m.group(2))
        sub = df[['Datetime', 'Date_only', c]].copy()
        sub.columns = ['Datetime', 'Date_only', 'working_status']
        sub['block_id'] = block_id
        sub['container_id'] = container_id
        long.append(sub)
    if not long:
        return pd.DataFrame()
    return pd.concat(long, ignore_index=True)


def load_lc_soc(path):
    """5-min SOC per LC, long-format: Datetime, Date_only, block_id, container_id, soc_pct."""
    df = pd.read_excel(path)
    df = _parse_date(df)
    long = []
    for c in df.columns:
        m = LC_RE.search(str(c))
        if not m or 'SOC' not in str(c).upper():
            continue
        block_id, container_id = int(m.group(1)), int(m.group(2))
        sub = df[['Datetime', 'Date_only', c]].copy()
        sub.columns = ['Datetime', 'Date_only', 'soc_pct']
        sub['block_id'] = block_id
        sub['container_id'] = container_id
        long.append(sub)
    if not long:
        return pd.DataFrame()
    return pd.concat(long, ignore_index=True)


def load_lc_soh_snapshot(path):
    """SOH last-day snapshot per LC, averaged across the day. Returns:
    DataFrame[block_id, container_id, soh_pct, snapshot_date]."""
    df = pd.read_excel(path)
    df = _parse_date(df)
    rows = []
    snapshot_date = df['Date_only'].iloc[0] if not df['Date_only'].empty else None
    for c in df.columns:
        m = LC_RE.search(str(c))
        if not m or 'SOH' not in str(c).upper():
            continue
        block_id, container_id = int(m.group(1)), int(m.group(2))
        values = pd.to_numeric(df[c], errors='coerce')
        valid = values[values > 0]
        if len(valid) == 0:
            continue
        rows.append({
            'block_id': block_id, 'container_id': container_id,
            'soh_pct': float(valid.mean()),
            'snapshot_date': snapshot_date,
        })
    return pd.DataFrame(rows)


def load_lc_daily_energy(charge_path, discharge_path):
    """Same logic as Bukhara module, but for Tashkent's 'LC200 BB.CC' columns.
    Returns df indexed by date with 'LC NN_charge_kwh' / 'LC NN_discharge_kwh'
    where NN concatenates block.container."""
    def _read(path, kind):
        df = pd.read_excel(path)
        df['Date_parsed'] = pd.to_datetime(df['Date'], format='%m/%d/%Y',
                                            errors='coerce')
        device_cols = [c for c in df.columns if 'LC200' in str(c)]
        # Per-day max of cumulative-within-day daily totalizer
        d = df.groupby(df['Date_parsed'].dt.date)[device_cols].max()
        # Normalise column names: "LC200 01.02" → "LC 01.02"
        renames = {}
        for c in device_cols:
            m = LC_RE.search(str(c))
            if m:
                renames[c] = f"LC {int(m.group(1)):02d}.{int(m.group(2)):02d}_{kind}_kwh"
        d = d.rename(columns=renames)
        return d
    try:
        chg = _read(charge_path, 'charge')
    except Exception as e:
        print(f"Warning: could not load charge file: {e}")
        chg = pd.DataFrame()
    try:
        dis = _read(discharge_path, 'discharge')
    except Exception as e:
        print(f"Warning: could not load discharge file: {e}")
        dis = pd.DataFrame()
    common_dates = chg.index.intersection(dis.index) if not chg.empty and not dis.empty else []
    if len(common_dates):
        chg = chg.loc[common_dates]
        dis = dis.loc[common_dates]
        joined = chg.join(dis, how='outer')
        joined.index.name = 'date'
        return joined
    return chg.join(dis, how='outer') if not chg.empty or not dis.empty else pd.DataFrame()


def load_hv_meter_daily(path):
    """30-row daily POI meter (cumulative kWh at midnight)."""
    df = pd.read_excel(path)
    df['Date_parsed'] = pd.to_datetime(df['Date'], errors='coerce')
    return df


def load_lc_total_monthly(charge_path, discharge_path):
    """
    Load the authoritative monthly charge/discharge totalizer from the
    'LC total charge.xlsx' / 'LC total discharge.xlsx' SCADA snapshots.

    Each file is a single-row export with 140 columns of the form
        "Tashkent - LC200 BB.CC - LC - TOTAL {CHARGE|DISCHARGE} ENERGY (kWh)"
    holding the canonical month-total per LC (matches the BMS HMI value).
    The daily totalizer files used by the rest of the pipeline can drift
    from these by 8-12% on some blocks, so when present these are the
    trustworthy monthly aggregates.

    Returns DataFrame[block_id, charge_kwh, discharge_kwh] aggregated to
    block level (sum of the block's two LCs). Empty DataFrame if either
    path is missing / unreadable — caller falls back to daily-sum.
    """
    def _read(path, direction, col_name):
        """direction: 'CHARGE' or 'DISCHARGE' (substring matched in header).
        col_name: output column name."""
        if not path or not os.path.exists(path):
            return None
        try:
            df = pd.read_excel(path)
        except Exception as e:
            print(f"Warning: could not read LC total {direction} file {path}: {e}")
            return None
        rows = []
        needle = f'TOTAL {direction}'
        for c in df.columns:
            col_str = str(c).upper()
            m = LC_RE.search(str(c))
            if not m or needle not in col_str:
                continue
            blk = int(m.group(1))
            val = pd.to_numeric(df[c], errors='coerce').dropna()
            if val.empty:
                continue
            rows.append({'block_id': blk, col_name: float(val.iloc[0])})
        return pd.DataFrame(rows)

    chg = _read(charge_path,    'CHARGE',    'charge_kwh')
    dis = _read(discharge_path, 'DISCHARGE', 'discharge_kwh')
    if chg is None or dis is None or chg.empty or dis.empty:
        return pd.DataFrame()

    # Sum the two LCs per block (file has one row per LC; multiple rows
    # share block_id when both LCs are present)
    chg_block = chg.groupby('block_id', as_index=False)['charge_kwh'].sum()
    dis_block = dis.groupby('block_id', as_index=False)['discharge_kwh'].sum()
    out = chg_block.merge(dis_block, on='block_id', how='outer')
    return out.sort_values('block_id').reset_index(drop=True)


def load_cmu_cycle_snapshot(first_day_path, last_day_path,
                              period_start=None, period_end=None):
    """
    Per-block charge/discharge cycle counter delta, computed from two CMU-level
    snapshot xlsx exports.

      • first_day_path → cycle counter around the START of the reporting period
                         (typically contains 2 days straddling the boundary)
      • last_day_path  → same shape, around the END of the period

    Each file has Date | Time | <2240 columns of>
        "Tashkent - CMU NN.MM.PP.QQ - BSC - CMU - CHARGE AND DISCHARGE CYCLES"
    where NN.MM.PP.QQ = block.container.rack.cmu (70 × 2 × 2 × 8 = 2240 CMUs).

    Reading rules per CMU:
      A literal 0 means "no communication" (typically scheduled maintenance),
      NOT zero cycles — the cumulative counter cannot go down. So:

      • Begin value (per CMU) = reading at period_start if it's >0, else the
        next valid (>0) reading walking forward in time. This corresponds to
        the operator's habit of "if Mar 1 shows 0, use Mar 5 when comms came
        back" — best-effort estimate using the earliest available reading
        once the device is online.
      • End value (per CMU) = reading at period_end if it's >0, else the
        previous valid (>0) reading walking backward in time.

      If period_start / period_end are not given, the loader falls back to
      the file's first / last non-zero reading (chronological order).

    Returns DataFrame[block_id, cycle_begin, cycle_end, cycle_delta]
    with one row per block (each value = MEAN across that block's 32 CMUs).

    Returns empty DataFrame if either file is missing / unreadable, so the
    caller can use the daily-totalizer EFC estimate as fallback.
    """
    def _parse_df(path):
        if not path or not os.path.exists(path):
            return None
        try:
            df = pd.read_excel(path)
        except Exception as e:
            print(f"Warning: could not read cycle snapshot {path}: {e}")
            return None
        # Build a Datetime column for time-based anchoring
        if 'Time' in df.columns:
            ts = pd.to_datetime(df['Date'].astype(str) + ' ' + df['Time'].astype(str),
                                 errors='coerce')
        else:
            ts = pd.to_datetime(df['Date'], errors='coerce')
        df = df.copy()
        df['_dt'] = ts
        df = df.sort_values('_dt').reset_index(drop=True)
        return df

    def _snapshot(df, position, anchor_dt):
        """anchor_dt: pd.Timestamp or None. position: 'first' or 'last'."""
        if df is None:
            return None
        out: dict = {}
        for c in df.columns:
            col_str = str(c)
            m = CMU_RE.search(col_str)
            if not m or 'CHARGE AND DISCHARGE CYCLES' not in col_str.upper():
                continue
            block_id = int(m.group(1))
            series = pd.to_numeric(df[c], errors='coerce')
            valid_mask = series > 0
            if not valid_mask.any():
                continue

            if anchor_dt is not None and df['_dt'].notna().any():
                if position == 'first':
                    # walk FORWARD from anchor: first valid sample at or after anchor_dt
                    eligible = valid_mask & (df['_dt'] >= anchor_dt)
                    if eligible.any():
                        val = float(series[eligible].iloc[0])
                    else:
                        # anchor is past everything we have → fall back to last valid
                        val = float(series[valid_mask].iloc[-1])
                else:   # 'last'
                    # walk BACKWARD from anchor: last valid sample at or before anchor_dt
                    eligible = valid_mask & (df['_dt'] <= anchor_dt)
                    if eligible.any():
                        val = float(series[eligible].iloc[-1])
                    else:
                        val = float(series[valid_mask].iloc[0])
            else:
                # No anchor → first / last valid sample in chronological order
                val = float(series[valid_mask].iloc[0] if position == 'first'
                            else series[valid_mask].iloc[-1])
            out.setdefault(block_id, []).append(val)
        return out

    df_first = _parse_df(first_day_path)
    df_last  = _parse_df(last_day_path)

    # Auto-infer period boundaries if not supplied: latest date in first file
    # is the canonical "first day of month"; earliest date in last file is
    # the canonical "last day of month". (This matches how SCADA tends to
    # bracket the snapshot with one context day on either side.)
    anchor_start = pd.to_datetime(period_start) if period_start is not None else None
    anchor_end   = pd.to_datetime(period_end)   if period_end   is not None else None
    if anchor_start is None and df_first is not None and df_first['_dt'].notna().any():
        anchor_start = pd.Timestamp(df_first['_dt'].dt.date.max()).normalize()
    if anchor_end is None and df_last is not None and df_last['_dt'].notna().any():
        d_first_in_last = pd.Timestamp(df_last['_dt'].dt.date.min())
        anchor_end = d_first_in_last.normalize() + pd.Timedelta(hours=23, minutes=55)

    begin = _snapshot(df_first, 'first', anchor_start) or {}
    end   = _snapshot(df_last,  'last',  anchor_end)   or {}
    if not begin and not end:
        return pd.DataFrame()

    # Outer join: include every block that appears in EITHER file. Missing
    # values are NaN — the per-block render renders them as "—" so the
    # customer sees the data gap per-block instead of finding affected
    # blocks omitted entirely.
    all_blocks = sorted(set(begin.keys()) | set(end.keys()))
    rows = []
    for b in all_blocks:
        cb = float(np.mean(begin[b])) if b in begin else np.nan
        ce = float(np.mean(end[b]))   if b in end   else np.nan
        cd = (ce - cb) if (pd.notna(cb) and pd.notna(ce)) else np.nan
        rows.append({
            'block_id':    b,
            'cycle_begin': cb,
            'cycle_end':   ce,
            'cycle_delta': cd,
        })
    return pd.DataFrame(rows)


# ── KPI ENGINE ────────────────────────────────────────────────────────────────

def _unavail_status_label(status: str) -> str:
    """Short, customer-facing label for an unavailability incident status."""
    return {'FAULT': 'Fault', 'PARTIAL': 'Partial'}.get(status, status)


def calc_daily_block_kpis_tashkent(lc_daily, ws_long, soc_long,
                                    cycles_per_block_target=2,
                                    full_cycle_top_soc=90.0,
                                    full_cycle_bottom_soc=10.0):
    """
    Build daily-block KPI table for Tashkent. Aggregates LC NN.MM rows into
    block NN by summing both containers.

    Cycle count is computed locally from the daily totalizer (Tashkent has no
    pre-aggregated Battery-Unit cycle delta sheet like Bukhara does).
    cycles_completed = min(charge, discharge) / 4954 kWh per LC, then summed
    across both containers per block, divided by 2 (so a "block cycle" =
    average of its two containers' cycle progress).

    Full-cycle gate for RTE: a daily round-trip efficiency is only physically
    meaningful when the block actually performed a deep charge AND a deep
    discharge that day. A (block, day) is marked `full_cycle=True` only when the
    day's SOC reached the top band (max SOC >= full_cycle_top_soc) and the
    bottom band (min SOC <= full_cycle_bottom_soc). Nominal window is 5–95% SOC;
    the 90/10 defaults allow the real SOC to fall a little short of the rails.
    Shallow-cycling days still carry their energy/throughput but are excluded
    from RTE downstream.
    """
    chg_cols = [c for c in lc_daily.columns if c.endswith('_charge_kwh')]
    dis_cols = [c for c in lc_daily.columns if c.endswith('_discharge_kwh')]
    # Map "LC NN.MM_charge_kwh" → block NN
    def _block_of(col):
        m = re.search(r'LC (\d+)\.', col)
        return int(m.group(1)) if m else None
    chg_by_block, dis_by_block = {}, {}
    for c in chg_cols:
        b = _block_of(c)
        if b: chg_by_block.setdefault(b, []).append(c)
    for c in dis_cols:
        b = _block_of(c)
        if b: dis_by_block.setdefault(b, []).append(c)

    all_dates = sorted(lc_daily.index.unique())
    rows = []
    # Per-day availability (mean across both containers of the block) — built from
    # ws_long per (block, date)
    ws_day_block_avail = {}
    if not ws_long.empty:
        for (b, d), g in ws_long.groupby(['block_id', 'Date_only']):
            avail = (g['working_status'].astype(str).isin(AVAILABLE_STATES)
                     ).mean() * 100
            ws_day_block_avail[(b, d)] = avail

    # Per-day SOC stats (mean / min / max across the block's containers per day,
    # valid samples only — zero samples are idle/disconnected periods).
    # start/end = SOC at the first / last sample of the day, used to tell a
    # self-contained cycle from a day that mostly charged (SOC carried over).
    soc_day_block = {}
    soc_day_block_min = {}
    soc_day_block_max = {}
    soc_day_block_start = {}
    soc_day_block_end = {}
    if not soc_long.empty:
        valid = soc_long.copy()
        valid['soc_pct'] = pd.to_numeric(valid['soc_pct'], errors='coerce')
        valid = valid[(valid['soc_pct'] > 0)]
        if 'Datetime' in valid.columns:
            valid = valid.sort_values('Datetime')
            soc_day_block_start = (valid.groupby(['block_id', 'Date_only'])
                                        ['soc_pct'].first().to_dict())
            soc_day_block_end   = (valid.groupby(['block_id', 'Date_only'])
                                        ['soc_pct'].last().to_dict())
        for (b, d), g in valid.groupby(['block_id', 'Date_only']):
            soc_day_block[(b, d)]     = float(g['soc_pct'].mean())
            soc_day_block_min[(b, d)] = float(g['soc_pct'].min())
            soc_day_block_max[(b, d)] = float(g['soc_pct'].max())

    LC_CAP_OP_KWH = 5504.0 * 0.90    # 4953.6 — current 5-95% operational
    for d in all_dates:
        for b in sorted(set(list(chg_by_block.keys()) + list(dis_by_block.keys()))):
            chg = lc_daily.loc[d, chg_by_block.get(b, [])].sum() if chg_by_block.get(b) else 0
            dis = lc_daily.loc[d, dis_by_block.get(b, [])].sum() if dis_by_block.get(b) else 0
            throughput = chg + dis
            rte = (dis / chg * 100) if chg > 0 else np.nan
            # Block cycles: mean of the two containers' cycle progress
            n_containers = max(len(chg_by_block.get(b, [])), len(dis_by_block.get(b, [])))
            cycles = (min(chg, dis) / (LC_CAP_OP_KWH * n_containers)) if chg > 0 and dis > 0 else 0
            avail = ws_day_block_avail.get((b, d), np.nan)
            soc = soc_day_block.get((b, d), np.nan)
            min_soc = soc_day_block_min.get((b, d), np.nan)
            max_soc = soc_day_block_max.get((b, d), np.nan)
            start_soc = soc_day_block_start.get((b, d), np.nan)
            end_soc   = soc_day_block_end.get((b, d), np.nan)
            # Full-cycle gate: SOC reached both the top and bottom band today.
            # NaN SOC → cannot confirm a full cycle → False (excluded from RTE).
            full_cycle = bool(
                pd.notna(max_soc) and pd.notna(min_soc)
                and max_soc >= full_cycle_top_soc
                and min_soc <= full_cycle_bottom_soc
            )
            # Quality flag
            if chg == 0 and dis == 0:
                qflag = 'EXCLUDED'
            elif chg < 100 or pd.isna(rte):
                qflag = 'INCOMPLETE'
            elif rte < 50 or rte > 100:
                qflag = 'ESTIMATED'
            else:
                qflag = 'VALID'
            rows.append({
                'date':             d,
                'block':            b,
                'charge_kwh':       round(chg, 1),
                'discharge_kwh':    round(dis, 1),
                'throughput_kwh':   round(throughput, 1),
                'rte_pct':          round(rte, 2) if pd.notna(rte) else None,
                'efc':              round(cycles, 3) if cycles else None,
                'availability_pct': round(avail, 2) if pd.notna(avail) else None,
                'avg_soc_pct':      round(soc, 2) if pd.notna(soc) else None,
                'min_soc_pct':      round(min_soc, 2) if pd.notna(min_soc) else None,
                'max_soc_pct':      round(max_soc, 2) if pd.notna(max_soc) else None,
                'start_soc_pct':    round(start_soc, 2) if pd.notna(start_soc) else None,
                'end_soc_pct':      round(end_soc, 2) if pd.notna(end_soc) else None,
                'full_cycle':       full_cycle,
                'operating_hours':  None,    # could be filled from ws_long if needed
                'fault_pct':        None,
                'alarms_count':     0,       # filled below
                'quality_flag':     qflag,
            })
    return pd.DataFrame(rows)


def _tashkent_exclusion_mask(dt_s, blk_s, exclusions):
    """Boolean mask: which (Datetime, block_id) rows fall inside an exclusion
    window (planned maintenance / restoration). Mirrors the semantics of
    availability_service.match_alarm_to_exclusions, vectorised."""
    mask = pd.Series(False, index=dt_s.index)
    for exc in (exclusions or []):
        try:
            d_from = pd.to_datetime(exc['date_from']).normalize()
            d_to   = pd.to_datetime(exc['date_to']).normalize()
        except Exception:
            continue
        t_from = exc.get('time_from') or '00:00'
        t_to   = exc.get('time_to')   or '23:59'
        try:
            hf, mf = map(int, t_from.split(':'))
            ht, mt = map(int, t_to.split(':'))
        except ValueError:
            hf = mf = 0; ht, mt = 23, 59
        start_dt = d_from + pd.Timedelta(hours=hf, minutes=mf)
        end_dt   = d_to + pd.Timedelta(hours=ht, minutes=mt)
        in_time = (dt_s >= start_dt) & (dt_s <= end_dt)
        aff = (exc.get('affected_blocks') or '').strip()
        if not aff or aff.lower() in ('all', 'all blocks'):
            in_blk = pd.Series(True, index=dt_s.index)
        else:
            try:
                ids = {int(b.strip()) for b in aff.split(',') if b.strip()}
            except ValueError:
                continue
            in_blk = blk_s.isin(ids)
        mask |= (in_time & in_blk)
    return mask


_SEV_RANK = {'CRITICAL': 3, 'FAULT': 2, 'WARNING': 1, 'INFO': 0}


def build_unavailability_reasons_tashkent(ws_long, alarms=None, exclusions=None,
                                          fault_states=None, min_hours=0.5,
                                          top_n=20):
    """
    Genuine fault-downtime incidents per LC for section 4.4.1.

    Built from the LC working-status fault-shutdown time (same basis as the
    contractual availability), so it lists only LCs that were really in a fault
    shutdown — NOT planned maintenance/restoration (those fall inside exclusion
    windows and are dropped). Consecutive fault days per LC are grouped into one
    incident; the dominant cause is the fault-class alarm reason with the most
    total duration in that window, taken from BOTH production faults and
    persistent warnings (the LC fault-status events live in the latter).

    Returns DataFrame[block_id, container_id, status, date_from, date_to,
    n_days, cause, subsystem, severity, fault_events, downtime_h] sorted by
    downtime desc (top_n rows). Empty when nothing genuine.
    """
    if ws_long is None or ws_long.empty:
        return pd.DataFrame()
    states = {s.upper() for s in (fault_states or FAULT_SHUTDOWN_STATES)}
    ws = ws_long[['Datetime', 'block_id', 'container_id', 'working_status']].copy()
    ws['Datetime'] = pd.to_datetime(ws['Datetime'], errors='coerce')
    ws = ws.dropna(subset=['Datetime', 'block_id', 'container_id'])
    ws = ws[ws['working_status'].astype(str).str.strip().str.upper().isin(states)]
    if ws.empty:
        return pd.DataFrame()
    if exclusions is not None and len(exclusions):
        ws = ws[~_tashkent_exclusion_mask(ws['Datetime'], ws['block_id'], exclusions)]
        if ws.empty:
            return pd.DataFrame()
    ws['date'] = ws['Datetime'].dt.date
    ws['block_id'] = ws['block_id'].astype(int)
    ws['container_id'] = ws['container_id'].astype(int)
    perday = (ws.groupby(['block_id', 'container_id', 'date']).size()
              .reset_index(name='samples'))
    perday['hours'] = perday['samples'] * 5 / 60.0

    # ── fault-class alarm index (production + persistent warnings) ──────────
    a = pd.DataFrame()
    frames = []
    for key in ('production_dedup', 'warning_persistent'):
        df = (alarms or {}).get(key)
        if df is not None and not df.empty:
            frames.append(df)
    if frames:
        a = pd.concat(frames, ignore_index=True, sort=False)
        if 'is_excluded' in a.columns:
            a = a[~a['is_excluded'].astype(bool)]
        a = a.copy()
        a['date'] = pd.to_datetime(a.get('Activated'), errors='coerce').dt.date
        elem = a['Element'].astype(str).str.extract(r'(\d+)\.(\d+)')
        a['block_id']     = pd.to_numeric(elem[0], errors='coerce')
        a['container_id'] = pd.to_numeric(elem[1], errors='coerce')
        a = a.dropna(subset=['date', 'block_id', 'container_id'])
        if not a.empty:
            a['block_id'] = a['block_id'].astype(int)
            a['container_id'] = a['container_id'].astype(int)
            a['duration_min'] = pd.to_numeric(a.get('duration_min'),
                                              errors='coerce').fillna(0.0)
            sev = a.get('cls_severity', pd.Series('', index=a.index))
            a['sev_rank'] = sev.astype(str).str.upper().map(_SEV_RANK).fillna(0)
            if 'cls_reason' not in a.columns:
                a['cls_reason'] = 'Unclassified'
            if 'cls_subsystem' not in a.columns:
                a['cls_subsystem'] = 'OTHER'

    def _cause(b, lc, d_from, d_to):
        if a.empty:
            return ('Unclassified', 'OTHER', '', 0)
        sub = a[(a['block_id'] == b) & (a['container_id'] == lc)
                & (a['date'] >= d_from) & (a['date'] <= d_to)]
        faults = sub[sub['sev_rank'] >= _SEV_RANK['FAULT']]
        pick = faults if not faults.empty else sub
        if pick.empty:
            return ('Unclassified', 'OTHER', '', 0)
        ranked = (pick.groupby(['cls_reason', 'cls_subsystem'])
                      .agg(total=('duration_min', 'sum'), n=('duration_min', 'size'),
                           sev=('sev_rank', 'max')).reset_index()
                      .sort_values(['total', 'n', 'sev'], ascending=False))
        top = ranked.iloc[0]
        sev_label = {3: 'Critical', 2: 'Fault', 1: 'Warning', 0: ''}.get(
            int(top['sev']), '')
        return (top['cls_reason'], top['cls_subsystem'], sev_label, int(len(faults)))

    # ── group consecutive fault days per (block, LC) into incidents ─────────
    rows = []
    for (b, lc), g in perday.sort_values('date').groupby(['block_id', 'container_id']):
        days = list(g['date']); hrs = dict(zip(g['date'], g['hours']))
        run = [days[0]]
        runs = []
        for d in days[1:]:
            if (d - run[-1]).days == 1:
                run.append(d)
            else:
                runs.append(run); run = [d]
        runs.append(run)
        for run in runs:
            downtime_h = sum(hrs[d] for d in run)
            if downtime_h < min_hours:
                continue
            cause, subsystem, severity, n_ev = _cause(b, lc, run[0], run[-1])
            rows.append({
                'block_id': b, 'container_id': lc, 'status': 'FAULT',
                'date_from': pd.Timestamp(run[0]), 'date_to': pd.Timestamp(run[-1]),
                'n_days': len(run), 'cause': cause, 'subsystem': subsystem,
                'severity': severity, 'fault_events': n_ev,
                'downtime_h': downtime_h,
            })
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows).sort_values('downtime_h', ascending=False)
    return out.head(top_n).reset_index(drop=True) if top_n else out


def _load_pcs_wide_file(path, value_name):
    """
    Parse a wide per-PCS-unit export (Date + Time + one column per converter
    unit, headed '... PCS BB.LL.UU ...' where BB=block, LL=LC, UU=unit) into a
    long frame [Datetime, block_id, container_id (=LC), unit, <value_name>]
    (value upper/stripped). Each block has 2 LCs x 2 units = 4 PCS units
    (= 4 BESS containers). Empty frame on any failure.
    """
    if not path or not os.path.exists(path):
        return pd.DataFrame()
    try:
        pcs = pd.read_excel(path, sheet_name=0)
    except Exception:
        return pd.DataFrame()
    if 'Date' not in pcs.columns or 'Time' not in pcs.columns:
        return pd.DataFrame()
    dt = pd.to_datetime(pcs['Date'].astype(str) + ' ' + pcs['Time'].astype(str),
                        errors='coerce')
    scols = [c for c in pcs.columns
             if re.search(r'PCS\s+\d+\.\d+\.\d+', str(c))]
    if not scols:
        return pd.DataFrame()
    long = pcs[scols].copy()
    long.insert(0, 'Datetime', dt)
    m = long.melt(id_vars='Datetime', var_name='_col', value_name=value_name)
    ex = m['_col'].str.extract(r'PCS\s+(\d+)\.(\d+)\.(\d+)')
    m['block_id']     = pd.to_numeric(ex[0], errors='coerce')
    m['container_id'] = pd.to_numeric(ex[1], errors='coerce')   # LC index
    m['unit']         = pd.to_numeric(ex[2], errors='coerce')
    m[value_name]     = m[value_name].astype(str).str.strip().str.upper()
    m = m.dropna(subset=['Datetime', 'block_id', 'container_id', 'unit'])
    for c in ('block_id', 'container_id', 'unit'):
        m[c] = m[c].astype(int)
    return m[['Datetime', 'block_id', 'container_id', 'unit', value_name]]


def load_pcs_unit_status(pcs_cd_path):
    """
    Load the wide 'PCS Charge_Discharge status' export. Status values seen:
    CHARGING / DISCHARGING / NON-OPERATING MODE (no explicit fault flag).
    Returns DataFrame[Datetime, block_id, container_id, unit, pcs_status].
    """
    return _load_pcs_wide_file(pcs_cd_path, 'pcs_status')


def load_pcs_unit_fault(pcs_fault_path):
    """
    Load the wide 'PCS fault status' export ('CONVERTER UNIT n FAULT STATUS 1'
    per unit). Value is '0' when healthy, otherwise the active fault text
    (e.g. ISLAND PROTECTION, FAN 1 FAULT, DC OVER VOLTAGE) — a direct per-unit
    fault flag. Returns DataFrame[Datetime, block_id, container_id, unit,
    pcs_fault_text].
    """
    return _load_pcs_wide_file(pcs_fault_path, 'pcs_fault_text')


def _parse_manual_unavail(manual_unavailability):
    """
    Normalise operator-entered unavailability dicts (block, lc, date_from,
    date_to, cause, subsystem, downtime_h — all strings from the UI textarea).

    Returns a list of {block:int, lc:int|None, d_from/d_to:normalized
    Timestamp, hours:float, cause:str, subsystem:str}. Unparseable rows are
    skipped; reversed dates are swapped; the block/LC fields tolerate noise
    ('Block 17', 'LC2', '02'...). Single source of truth for the contractual
    availability, the 4.4.1 table and the heatmap so they can't drift apart.
    """
    out = []
    for m in (manual_unavailability or []):
        try:
            d_from = pd.to_datetime(m.get('date_from'), dayfirst=True)
            d_to   = pd.to_datetime(m.get('date_to') or m.get('date_from'),
                                    dayfirst=True)
            if pd.isna(d_from) or pd.isna(d_to):
                continue
            bm = re.search(r'\d+', str(m.get('block') or ''))
            if not bm:
                continue
            blk = int(bm.group(0))
        except (TypeError, ValueError):
            continue
        if d_from > d_to:
            d_from, d_to = d_to, d_from
        lc_m = re.search(r'[12]', str(m.get('lc') or ''))
        try:
            hours = float(str(m.get('downtime_h') or 0).replace(',', '.'))
        except ValueError:
            hours = 0.0
        out.append({
            'block': blk,
            'lc': int(lc_m.group(0)) if lc_m else None,
            'd_from': d_from.normalize(), 'd_to': d_to.normalize(),
            'hours': hours,
            'cause': (m.get('cause') or 'Operator-reported').strip(),
            'subsystem': (m.get('subsystem') or '—').strip(),
        })
    return out


def calc_contractual_availability_tashkent(ws_long, pcs_long=None,
                                            container_capacity_kwh=2752.0,
                                            fault_states=None, exclusions=None,
                                            pcs_fault_long=None,
                                            manual_unavailability=None):
    """
    Contractual BESS availability (capacity-weighted, fault-shutdown only):

        Avail = 1 - Sum(unavailable_time * unavailable_capacity)
                    / (total_hours * installed_nameplate_capacity)

    Only genuine fault-shutdown time counts as unavailable — standby, startup,
    manual stops, data gaps and alarms-while-running do NOT. When `exclusions`
    are supplied, fault-shutdown samples that fall inside an exclusion window
    (planned maintenance / restoration, e.g. the "System not ready" warm-up)
    are removed from the unavailable time — the same exclusions the operator
    enters for the availability-hours section.

    `pcs_fault_long` (optional, from load_pcs_unit_fault): direct per-unit
    fault flags. A unit then counts as down when it was NOT operating AND
    there is fault evidence at either level (its LC in a fault shutdown OR its
    own fault flag active) — catching unit-level faults (e.g. islanding on one
    converter) that the LC status alone misses.

    `manual_unavailability` (optional): operator-entered incidents (same dicts
    as the 4.4.1 manual rows: block, lc, date_from/to, downtime_h). Each adds
    downtime_h x the affected capacity (LC given -> 2 PCS units; whole block
    -> 4 units) to the unavailable time, reported separately for transparency.

    Granularity (hierarchy: block = 2 LC = 4 PCS units = 4 BESS containers;
    1 PCS unit = 1 container = `container_capacity_kwh`, default 2752 kWh):

      • PCS-refined (preferred, when `pcs_long` is supplied): a PCS unit is
        unavailable only when its LC was in a fault shutdown AND that unit was
        NON-OPERATING. A unit still charging/discharging during the LC fault is
        NOT counted — so a partially-failed LC only loses the capacity that
        actually stopped (2752 kWh per unit).
      • LC-level (fallback, no PCS data): the whole LC (2 units = 5504 kWh) is
        counted down for the LC's fault time.

    Returns a dict with the percentage and formula components, or None.
    """
    if ws_long is None or ws_long.empty:
        return None
    states = {s.upper() for s in (fault_states or FAULT_SHUTDOWN_STATES)}
    ws = ws_long[['Datetime', 'block_id', 'container_id', 'working_status']].copy()
    ws['Datetime'] = pd.to_datetime(ws['Datetime'], errors='coerce')
    ws['lc_fault'] = (ws['working_status'].astype(str).str.strip().str.upper()
                      .isin(states))
    n_ts = ws['Datetime'].nunique()
    if n_ts == 0:
        return None
    total_hours = n_ts * 5 / 60.0

    # Operator-entered incidents -> extra down unit-hours. `per_lc_units` is
    # how many capacity units one LC represents in the active method (2 PCS
    # units in the refined method, 1 LC in the fallback); a whole-block entry
    # (no LC given) covers twice that. Entries whose dates fall outside the
    # data period are ignored — a stale row left in the form from another
    # month must not leak into this report.
    period_start = ws['Datetime'].min().normalize()
    period_end   = ws['Datetime'].max().normalize()

    def _manual_unit_hours(per_lc_units):
        tot = 0.0
        for m in _parse_manual_unavail(manual_unavailability):
            if m['hours'] <= 0:
                continue
            if m['d_to'] < period_start or m['d_from'] > period_end:
                continue
            n = per_lc_units if m['lc'] is not None else per_lc_units * 2
            tot += m['hours'] * n
        return tot

    # ── PCS-refined method ────────────────────────────────────────────────
    if pcs_long is not None and not pcs_long.empty:
        p = pcs_long.copy()
        p['Datetime'] = pd.to_datetime(p['Datetime'], errors='coerce')
        p['nonop'] = p['pcs_status'].astype(str).str.upper().eq('NON-OPERATING MODE')
        p = p.merge(ws[['Datetime', 'block_id', 'container_id', 'lc_fault']],
                    on=['Datetime', 'block_id', 'container_id'], how='left')
        p['lc_fault'] = p['lc_fault'].fillna(False)
        used_unit_fault = False
        if pcs_fault_long is not None and not pcs_fault_long.empty:
            f = pcs_fault_long.copy()
            f['Datetime'] = pd.to_datetime(f['Datetime'], errors='coerce')
            ftxt = f['pcs_fault_text'].astype(str).str.strip().str.upper()
            f['unit_fault'] = ~ftxt.isin(('0', '0.0', '', 'NAN', 'NONE'))
            p = p.merge(
                f[['Datetime', 'block_id', 'container_id', 'unit', 'unit_fault']],
                on=['Datetime', 'block_id', 'container_id', 'unit'], how='left')
            p['unit_fault'] = p['unit_fault'].fillna(False)
            p['down'] = (p['lc_fault'] | p['unit_fault']) & p['nonop']
            used_unit_fault = True
        else:
            p['down'] = p['lc_fault'] & p['nonop']
        excluded_unit_hours = 0.0
        if exclusions:
            em = _tashkent_exclusion_mask(p['Datetime'], p['block_id'], exclusions) & p['down']
            excluded_unit_hours = float(em.sum()) * 5 / 60.0
            p['down'] = p['down'] & ~em
        n_units = p.groupby(['block_id', 'container_id', 'unit']).ngroups
        if n_units > 0:
            manual_unit_hours = _manual_unit_hours(per_lc_units=2)
            down_unit_hours  = float(p['down'].sum()) * 5 / 60.0 + manual_unit_hours
            installed_kwh    = n_units * container_capacity_kwh
            unavail_caphours = down_unit_hours * container_capacity_kwh
            total_caphours   = total_hours * installed_kwh
            avail_pct = ((1 - unavail_caphours / total_caphours) * 100
                         if total_caphours > 0 else None)
            return {
                'availability_pct':  avail_pct,
                'method':            'pcs_unit',
                'unit_label':        'PCS unit',
                'down_unit_hours':   down_unit_hours,
                'excluded_unit_hours': excluded_unit_hours,
                'manual_unit_hours': manual_unit_hours,
                'used_unit_fault':   used_unit_fault,
                'unit_capacity_kwh': container_capacity_kwh,
                'n_units':           n_units,
                'total_hours':       total_hours,
                'installed_kwh':     installed_kwh,
            }

    # ── LC-level fallback ─────────────────────────────────────────────────
    n_lc = ws.dropna(subset=['container_id']).groupby(
        ['block_id', 'container_id']).ngroups
    if n_lc == 0:
        return None
    excluded_unit_hours = 0.0
    if exclusions:
        em = _tashkent_exclusion_mask(ws['Datetime'], ws['block_id'], exclusions) & ws['lc_fault']
        excluded_unit_hours = float(em.sum()) * 5 / 60.0
        ws['lc_fault'] = ws['lc_fault'] & ~em
    lc_capacity_kwh  = container_capacity_kwh * 2     # 1 LC = 2 units
    manual_unit_hours = _manual_unit_hours(per_lc_units=1)
    down_unit_hours  = float(ws['lc_fault'].sum()) * 5 / 60.0 + manual_unit_hours
    installed_kwh    = n_lc * lc_capacity_kwh
    unavail_caphours = down_unit_hours * lc_capacity_kwh
    total_caphours   = total_hours * installed_kwh
    avail_pct = ((1 - unavail_caphours / total_caphours) * 100
                 if total_caphours > 0 else None)
    return {
        'availability_pct':  avail_pct,
        'method':            'lc',
        'unit_label':        'LC',
        'down_unit_hours':   down_unit_hours,
        'excluded_unit_hours': excluded_unit_hours,
        'manual_unit_hours': manual_unit_hours,
        'used_unit_fault':   False,
        'unit_capacity_kwh': lc_capacity_kwh,
        'n_units':           n_lc,
        'total_hours':       total_hours,
        'installed_kwh':     installed_kwh,
    }


def calc_plant_hours_availability_tashkent(ws_long, plant_capacity_mw,
                                            per_block_capacity_mw,
                                            redundancy_threshold_pct=100.0,
                                            contractual_plant_capacity_mw=None,
                                            excluded_hours: float = 0.0):
    """
    Tashkent variant of plant-level-hours availability — vectorised version.

    Block is AVAILABLE at a given timestamp if BOTH its containers are in
    an available state (RUNNING/STANDBY/...). Threshold for "plant available":
      - contractual_plant_capacity_mw if supplied (SLA-based)
      - else redundancy_threshold_pct × plant_capacity_mw (nameplate-based)
    """
    if ws_long.empty:
        return None

    df = ws_long[['Datetime', 'block_id', 'container_id', 'working_status']].copy()
    df['container_available'] = df['working_status'].astype(str).isin(AVAILABLE_STATES)

    # Per (Datetime, block_id): block_available = all containers available
    block_avail_long = (df.groupby(['Datetime', 'block_id'])['container_available']
                         .all()
                         .reset_index(name='block_available'))

    # Scheduled hours = number of distinct timestamps * 5 / 60
    n_samples = block_avail_long['Datetime'].nunique()
    scheduled_hours = n_samples * 5 / 60.0

    # Per-block outage hours
    per_block = (block_avail_long.groupby('block_id')['block_available']
                                  .apply(lambda x: (~x).sum() * 5 / 60.0)
                                  .to_dict())
    container_outage_hours = sum(per_block.values())

    plant_outage_hours = None
    threshold_mw = None
    if per_block_capacity_mw and (contractual_plant_capacity_mw or plant_capacity_mw):
        if contractual_plant_capacity_mw:
            threshold_mw = float(contractual_plant_capacity_mw)
        else:
            threshold_mw = redundancy_threshold_pct / 100.0 * float(plant_capacity_mw)
        # Per timestamp: number of available blocks
        n_avail_per_ts = (block_avail_long.groupby('Datetime')['block_available']
                                            .sum())
        capacity_avail_mw = n_avail_per_ts * per_block_capacity_mw
        unavail = capacity_avail_mw < threshold_mw
        plant_outage_hours = unavail.sum() * 5 / 60.0

    # Apply availability exclusions — mirrors bukhara service:
    # cap at plant_outage_hours (never credit more than was lost), then
    # remove from the denominator. excluded_hours=0 → original behaviour.
    excluded_effective = 0.0
    plant_availability_pct = None
    if plant_outage_hours is not None and scheduled_hours > 0:
        excluded_effective = min(float(excluded_hours or 0.0),
                                   float(plant_outage_hours))
        eff_scheduled = max(1e-9, scheduled_hours - excluded_effective)
        plant_availability_pct = (
            (scheduled_hours - plant_outage_hours) / eff_scheduled * 100
        )

    return {
        'scheduled_hours':           scheduled_hours,
        'container_outage_hours':    container_outage_hours,
        'plant_outage_hours':        plant_outage_hours,
        'plant_availability_pct':    plant_availability_pct,
        'per_block_outage_hours':    per_block,
        'threshold_mw':              threshold_mw,
        'plant_capacity_mw':         plant_capacity_mw,
        'contractual_plant_capacity_mw': contractual_plant_capacity_mw,
        'excluded_hours':            float(excluded_hours or 0.0),
        'excluded_effective':        excluded_effective,
    }


def calc_site_soc_soh(soc_long, soh_snapshot):
    """Aggregated SOC and SOH across all containers, excluding zero samples."""
    out = {'avg_soc_pct': None, 'avg_soh_pct': None, 'soc_series': None}
    if not soc_long.empty:
        valid = soc_long.copy()
        valid['soc_pct'] = pd.to_numeric(valid['soc_pct'], errors='coerce')
        valid = valid[valid['soc_pct'] > 0]
        if len(valid):
            out['avg_soc_pct'] = float(valid['soc_pct'].mean())
            out['soc_series'] = valid.groupby('Date_only')['soc_pct'].mean()
    if soh_snapshot is not None and not soh_snapshot.empty:
        out['avg_soh_pct'] = float(soh_snapshot['soh_pct'].mean())
    return out


# ── CHARTS ────────────────────────────────────────────────────────────────────

def _cycles_block_chunked(cycles_df, month_name, chunk_size=10,
                            energy_by_block=None, projection_factor=None):
    """
    Build the customer-facing "Cycles per block" tables (PDF flowables).
    Layout mirrors the customer template — groups of `chunk_size` blocks,
    one row per metric:

        | (label)              | Block N | Block N+1 | … |
        | Accumulative cycles  | xx.xx   | …
        | Cycles in <Month>    | xx.xx   | …
        | Charged Energy (kWh) | NNN     | …    (if energy_by_block supplied)
        | Discharged Energy (kWh)| NNN   | …

    Args:
      cycles_df:        DataFrame[block_id, cycle_begin, cycle_end, cycle_delta]
      month_name:       e.g. 'March'
      chunk_size:       blocks per table
      energy_by_block:  optional dict[block_id -> {'charge_kwh': X,
                                                     'discharge_kwh': Y}].
                         When supplied, two extra rows are emitted.

    Returns a list of reportlab flowables ready to .extend() into `story`.
    """
    flowables = []
    if cycles_df is None or cycles_df.empty:
        return flowables
    cdf = cycles_df.sort_values('block_id').reset_index(drop=True)
    n = len(cdf)
    label_w = 38 * mm
    cell_w  = (170 * mm - label_w) / chunk_size   # fixed width keeps tables visually aligned

    def _fmt(v):
        return f'{v:.2f}' if pd.notna(v) else '—'

    def _fmt_e(v):
        return f'{v:.0f}' if (v is not None and pd.notna(v)) else '—'

    for start in range(0, n, chunk_size):
        chunk = cdf.iloc[start:start+chunk_size]
        headers = [''] + [f'Block {int(r.block_id)}' for r in chunk.itertuples()]
        row_acc = ['Accumulative\ncycles'] + [_fmt(r.cycle_end)   for r in chunk.itertuples()]
        row_dlt = [f'Cycles in {month_name}'] + [_fmt(r.cycle_delta) for r in chunk.itertuples()]
        rows_to_emit = [row_acc, row_dlt]
        if projection_factor:
            row_prj = ['Projected Cycles\nfor the Month'] + [
                _fmt(r.cycle_delta * projection_factor
                     if pd.notna(r.cycle_delta) else float('nan'))
                for r in chunk.itertuples()]
            rows_to_emit.append(row_prj)
        if energy_by_block:
            row_chg = ['Charged Energy (kWh)']
            row_dis = ['Discharged Energy (kWh)']
            for r in chunk.itertuples():
                b = int(r.block_id)
                e = energy_by_block.get(b, {})
                row_chg.append(_fmt_e(e.get('charge_kwh')))
                row_dis.append(_fmt_e(e.get('discharge_kwh')))
            rows_to_emit.extend([row_chg, row_dis])
        # If the last chunk is short, pad headers/rows with blanks so column
        # widths stay the same.
        pad = chunk_size - len(chunk)
        if pad > 0:
            headers += [''] * pad
            for r in rows_to_emit:
                r += [''] * pad
        col_widths = [label_w] + [cell_w] * chunk_size
        flowables.append(_styled_table(headers, rows_to_emit,
                                        col_widths=col_widths))
        flowables.append(Spacer(1, 3*mm))
    return flowables


def _chart_daily_rte_per_block_t(daily_kpi):
    fig, ax = plt.subplots(figsize=(11, 4))
    # Only plot days flagged VALID — INCOMPLETE (tiny charge) and ESTIMATED
    # (rte outside 50–100% band) days otherwise blow up the y-axis to
    # nonsensical values like 250 000%. The fleet mean was being pulled
    # along too. Also require a full cycle (deep charge + deep discharge) so
    # the daily RTE is a physically meaningful round-trip figure.
    valid = daily_kpi[(daily_kpi['quality_flag'] == 'VALID')
                        & daily_kpi['rte_pct'].notna()]
    if 'full_cycle' in valid.columns:
        full = valid[valid['full_cycle']]
        # Fall back to all valid days if nothing is a full cycle, so the chart
        # isn't blank (mirrors the headline fleet-RTE fallback).
        valid = full if not full.empty else valid
    for block, grp in valid.groupby('block'):
        ax.plot(grp['date'], grp['rte_pct'], lw=0.5, alpha=0.25)
    fleet = valid.groupby('date')['rte_pct'].mean()
    ax.plot(fleet.index, fleet.values, color='#1A2B45', lw=2.5, label='Fleet mean')
    ax.axhline(85, color='#34C759', ls='--', lw=1, alpha=0.7, label='Target 85%')
    ax.set_ylim(0, 110)   # RTE > 100% is physically impossible; cap defensively
    ax.set_ylabel('Round-trip efficiency (%)', fontsize=8)
    ax.set_title('Daily round-trip efficiency — each block and the site average',
                 fontsize=10, fontweight='bold', color='#1A2B45')
    ax.legend(fontsize=7, loc='lower right')
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    ax.grid(axis='y', ls='--', alpha=0.3)
    fig.autofmt_xdate(rotation=30); fig.tight_layout()
    return fig


def _chart_throughput_per_day_t(daily_kpi):
    fig, ax = plt.subplots(figsize=(11, 4))
    daily = daily_kpi.groupby('date').agg(
        chg=('charge_kwh', 'sum'),
        dis=('discharge_kwh', 'sum'),
    ).reset_index()
    x = range(len(daily))
    ax.bar([i-0.2 for i in x], daily['chg']/1000, 0.4, color='#0071E3',
           alpha=0.85, label='Charge (MWh)')
    ax.bar([i+0.2 for i in x], daily['dis']/1000, 0.4, color='#AF52DE',
           alpha=0.85, label='Discharge (MWh)')
    labels = [str(d)[5:] for d in daily['date']]
    step = max(1, len(labels)//12)
    ax.set_xticks(list(range(0, len(labels), step)))
    ax.set_xticklabels([labels[i] for i in range(0, len(labels), step)],
                       fontsize=7, rotation=30)
    ax.set_ylabel('Energy (MWh)', fontsize=8)
    ax.set_title('Daily energy charged and discharged — whole site',
                 fontsize=10, fontweight='bold', color='#1A2B45')
    ax.legend(fontsize=7)
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    ax.grid(axis='y', ls='--', alpha=0.3)
    fig.tight_layout()
    return fig


def _chart_availability_heatmap_t(daily_kpi, exclusions=None):
    """Tashkent variant — same stepped colormap + exclusion overlay as the
    Bukhara heatmap, but sized for ~70 blocks. See `_chart_availability_heatmap`
    in bukhara_report_service for design rationale."""
    import matplotlib.colors as mcolors

    pivot = daily_kpi.pivot_table(
        index='block', columns='date', values='availability_pct', aggfunc='mean'
    )

    # Operationally meaningful bands (matches Bukhara)
    bounds = [0, 50, 80, 95, 99, 100.001]
    band_colors = ['#8B0000', '#DC143C', '#FF8C00', '#9ACD32', '#22AA22']
    cmap = mcolors.ListedColormap(band_colors)
    norm = mcolors.BoundaryNorm(bounds, cmap.N)

    fig, ax = plt.subplots(figsize=(11, max(4, 0.13*len(pivot)+1.5)))
    im = ax.imshow(pivot.values, aspect='auto', cmap=cmap, norm=norm)

    # Exclusion-window overlay
    if exclusions:
        all_dates_list = list(pivot.columns)
        intervals = []
        for exc in exclusions:
            try:
                d_from = pd.to_datetime(exc['date_from']).date()
                d_to   = pd.to_datetime(exc['date_to']).date()
            except Exception:
                continue
            aff = (exc.get('affected_blocks') or '').strip()
            if not aff or aff.lower() in ('all', 'all blocks'):
                blk_set = None
            else:
                try:
                    blk_set = {int(b.strip()) for b in aff.split(',') if b.strip()}
                except ValueError:
                    blk_set = None
            intervals.append((d_from, d_to, blk_set))
        for yi, blk in enumerate(pivot.index):
            blk_int = int(blk)
            for xi, day in enumerate(all_dates_list):
                day_date = pd.to_datetime(day).date()
                for d_from, d_to, blk_set in intervals:
                    if d_from <= day_date <= d_to and (
                            blk_set is None or blk_int in blk_set):
                        ax.add_patch(plt.Rectangle(
                            (xi - 0.5, yi - 0.5), 1, 1,
                            fill=False, hatch='////',
                            edgecolor='white', linewidth=0))
                        break

    # Show every block label — fits at 70 blocks × ~0.13in row height
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels([f'B{b}' for b in pivot.index], fontsize=5)
    cols = [str(c)[5:] for c in pivot.columns]
    step = max(1, len(cols)//15)
    ax.set_xticks(range(0, len(cols), step))
    ax.set_xticklabels([cols[i] for i in range(0, len(cols), step)],
                       fontsize=6, rotation=30)
    ax.set_title('Daily Availability Heatmap — Block × Day (%)',
                 fontsize=10, fontweight='bold', color='#1A2B45')

    cb = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02,
                       ticks=[25, 65, 87.5, 97, 99.5])
    cb.ax.set_yticklabels(['<50%', '50–80%', '80–95%', '95–99%', '≥99%'],
                           fontsize=6)

    if exclusions:
        ax.text(0.0, -0.12,
                'Hatched cells = inside a planned availability exclusion window.',
                transform=ax.transAxes, fontsize=6,
                color='#6B7A8D', style='italic')

    fig.tight_layout()
    return fig


def _chart_soc_trend(socsoh):
    fig, ax = plt.subplots(figsize=(11, 3.5))
    s = socsoh.get('soc_series')
    if s is None or s.empty:
        ax.text(0.5, 0.5, 'SOC trend unavailable', ha='center', va='center',
                transform=ax.transAxes, color='#6B7A8D')
        ax.axis('off'); return fig
    ax.plot(s.index, s.values, color='#0071E3', lw=2, marker='o', ms=3)
    ax.fill_between(s.index, s.values, alpha=0.15, color='#0071E3')
    ax.set_ylim(0, 100)
    ax.set_ylabel('Average SOC (%)', fontsize=8)
    ax.set_title('Site average state of charge, day by day',
                 fontsize=10, fontweight='bold', color='#1A2B45')
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    ax.grid(axis='y', ls='--', alpha=0.3)
    fig.autofmt_xdate(rotation=30); fig.tight_layout()
    return fig


# ── REPORT BUILDER ────────────────────────────────────────────────────────────

def generate_tashkent_report(
    working_status_path,
    pcs_cd_path,
    soc_path,
    soh_snapshot_path,
    lc_charge_path,
    lc_discharge_path,
    hv_meter_daily_path,
    alarm_path,
    output_path,
    site_name='Tashkent BESS',
    cycles_first_day_path=None,    # optional: CMU cycle counter at start of period
    cycles_last_day_path=None,     # optional: CMU cycle counter at end of period
    lc_total_charge_path=None,     # optional: canonical monthly charge totalizer
    lc_total_discharge_path=None,  # optional: canonical monthly discharge totalizer
    pcs_fault_path=None,           # optional: 'PCS fault status.xlsx' — per-unit fault flags
    progress_callback=None,
    alarm_classifications_path=None,
    project_details=None,
    plant_capacity_mw=None,
    per_block_capacity_mw=None,
    redundancy_threshold_pct=100.0,
    contractual_plant_capacity_mw=None,
    scheduled_unavail_hours=0.0,
    yearly_cycle_target=365.0,
    report_number=None,
    prepared_by=None,
    reviewed_by=None,
    breakdown_incidents=None,
    manual_unavailability=None,   # operator-entered 4.4.1 rows (list of dicts)
    cycles_accum_avg=None,
    pm_activities=None,
    cm_activities=None,
    safety_incidents=None,
    site_visits=None,
    recommendations=None,
    planned_next_period=None,
    history_records=None,
    annexes=None,
    output_format='pdf',
    exclusions=None,               # list of dicts from availability_service.get_exclusions()
    balancing_periods=None,        # list of dicts from availability_service.get_balancing_periods()
):
    def log(msg):
        if progress_callback: progress_callback(msg)
        print(f"[Tashkent Report] {msg}")

    log("Loading LC working status (5-min)...")
    ws_long = load_lc_working_status(working_status_path)

    # Cycle-balancing: blocks deliberately rested this month (informational —
    # does NOT affect the 770 MWh contractual availability; the report uses it
    # to mark these blocks' low cycles / RTE as intentional, not a fault).
    rested_blocks = set()
    if balancing_periods:
        try:
            from services.availability_service import rested_blocks_for_period
            _dt = pd.to_datetime(ws_long['Datetime'], errors='coerce')
            if _dt.notna().any():
                rested_blocks = rested_blocks_for_period(
                    balancing_periods, _dt.min(), _dt.max())
                if rested_blocks:
                    log(f"Cycle-balancing: {len(rested_blocks)} block(s) marked "
                        f"intentionally rested: {sorted(rested_blocks)}")
        except Exception as e:
            log(f"Note: could not apply balancing periods: {e}")

    log("Loading SOC (5-min)...")
    soc_long = load_lc_soc(soc_path)

    log("Loading SOH snapshot...")
    soh_snap = load_lc_soh_snapshot(soh_snapshot_path)

    log("Loading LC daily energy (charge + discharge)...")
    lc_daily = load_lc_daily_energy(lc_charge_path, lc_discharge_path)

    log("Loading HV meter daily...")
    meter_daily = load_hv_meter_daily(hv_meter_daily_path)

    # Optional CMU cycle snapshot (first-day + last-day pair). When supplied,
    # this overrides the daily-totalizer EFC estimate with the actual SCADA
    # cycle-counter delta, mirroring how Bukhara uses its Battery Unit file.
    cycles_snap = pd.DataFrame()
    if cycles_first_day_path and cycles_last_day_path:
        log("Loading CMU cycle snapshot (first + last day)...")
        # Anchor the snapshot to the reporting period derived from the daily
        # energy data we just loaded — gives the loader the right Mar 1 00:00 /
        # Mar 31 23:55 targets so it can walk forward / backward to the
        # nearest non-zero reading when those exact rows are 0 (no comms).
        cycle_period_start = cycle_period_end = None
        if not lc_daily.empty:
            days = sorted(lc_daily.index.unique())
            if days:
                cycle_period_start = pd.Timestamp(days[0])
                cycle_period_end   = (pd.Timestamp(days[-1])
                                       + pd.Timedelta(hours=23, minutes=55))
        cycles_snap = load_cmu_cycle_snapshot(
            cycles_first_day_path, cycles_last_day_path,
            period_start=cycle_period_start,
            period_end=cycle_period_end,
        )
        if cycles_snap.empty:
            log("  ⚠  cycle snapshot files yielded no usable data; "
                "falling back to daily-totalizer EFC estimate.")

    log("Loading alarms...")
    alarms = load_alarms(alarm_path)
    cls = load_alarm_classifications(alarm_classifications_path)
    alarms = apply_alarm_classifications(alarms, cls)

    # Tag alarms inside availability-exclusion windows (no-op if none)
    alarms = tag_alarms_with_exclusions(alarms, exclusions or [])

    log("Summarising alarms...")
    alarm_sum = summarize_alarms(alarms)

    log("Computing daily block KPIs...")
    daily_kpi = calc_daily_block_kpis_tashkent(lc_daily, ws_long, soc_long)

    # Inject alarm counts per (block, day)
    if not alarms['production_dedup'].empty:
        a = alarms['production_dedup'].copy()
        a['block_id'] = a['Element'].astype(str).str.extract(r'(\d+)\.\d+')[0]
        a['day'] = pd.to_datetime(a['Activated'], errors='coerce').dt.date
        cnt = (a.dropna(subset=['block_id', 'day'])
                 .groupby(['block_id', 'day']).size()
                 .reset_index(name='alarms_count'))
        cnt['block'] = cnt['block_id'].astype(int)
        cnt['date']  = cnt['day']
        merged = daily_kpi.merge(cnt[['block', 'date', 'alarms_count']],
                                  on=['block', 'date'], how='left',
                                  suffixes=('', '_new'))
        merged['alarms_count'] = merged['alarms_count_new'].fillna(0).astype(int)
        daily_kpi = merged.drop(columns=['alarms_count_new'])

    # ── Calibrate daily charge/discharge to canonical monthly totals ──────
    # The 'LC daily charge.xlsx' / 'LC Daily discharge.xlsx' files we sum
    # for monthly aggregates can drift ~8-12% high relative to the BMS's
    # own monthly totalizer (see "LC total charge.xlsx" / "LC total
    # discharge.xlsx"). When the operator supplies the total files, scale
    # each block's daily values so their monthly sums match the canonical
    # number — preserving daily SHAPE while making monthly totals, fleet
    # RTE, and anomaly detection trustworthy.
    lc_total_block = pd.DataFrame()
    calibration_applied = False
    if lc_total_charge_path and lc_total_discharge_path:
        log("Loading canonical monthly totals (LC total charge/discharge)...")
        lc_total_block = load_lc_total_monthly(
            lc_total_charge_path, lc_total_discharge_path
        )
    if not lc_total_block.empty and not daily_kpi.empty:
        log(f"Calibrating daily values to canonical monthly totals "
            f"({len(lc_total_block)} blocks)...")
        # Per-block scale factors. Skip a block when either side has zero
        # daily-sum (can't compute factor) or zero canonical (would zero
        # everything out incorrectly).
        daily_sums = (daily_kpi.groupby('block')
                                .agg(chg_sum=('charge_kwh', 'sum'),
                                     dis_sum=('discharge_kwh', 'sum'))
                                .reset_index())
        merged_scale = daily_sums.merge(
            lc_total_block, left_on='block', right_on='block_id', how='inner'
        )
        chg_scales: dict = {}
        dis_scales: dict = {}
        n_chg_overstate = 0
        cumulative_chg_old = cumulative_chg_new = 0.0
        for _, r in merged_scale.iterrows():
            b = int(r['block'])
            if r['chg_sum'] > 1.0 and r['charge_kwh'] > 1.0:
                chg_scales[b] = float(r['charge_kwh']) / float(r['chg_sum'])
                cumulative_chg_old += float(r['chg_sum'])
                cumulative_chg_new += float(r['charge_kwh'])
                if r['chg_sum'] > r['charge_kwh']:
                    n_chg_overstate += 1
            if r['dis_sum'] > 1.0 and r['discharge_kwh'] > 1.0:
                dis_scales[b] = float(r['discharge_kwh']) / float(r['dis_sum'])
        if cumulative_chg_old > 0:
            overall_overstate_pct = ((cumulative_chg_old - cumulative_chg_new)
                                      / cumulative_chg_new * 100)
            log(f"  daily-sum overstates charge by {overall_overstate_pct:+.2f}% "
                f"plant-wide ({n_chg_overstate}/{len(merged_scale)} blocks "
                f"overstated)")
        # Apply per-row scaling, then recompute the derived fields
        chg_scale_arr = daily_kpi['block'].map(chg_scales).fillna(1.0)
        dis_scale_arr = daily_kpi['block'].map(dis_scales).fillna(1.0)
        daily_kpi['charge_kwh']    = (daily_kpi['charge_kwh']
                                       * chg_scale_arr).round(1)
        daily_kpi['discharge_kwh'] = (daily_kpi['discharge_kwh']
                                       * dis_scale_arr).round(1)
        daily_kpi['throughput_kwh'] = (daily_kpi['charge_kwh']
                                        + daily_kpi['discharge_kwh']).round(1)
        # RTE = discharge / charge × 100 (only where charge > 0)
        rte_new = pd.Series(np.nan, index=daily_kpi.index)
        mask_pos = daily_kpi['charge_kwh'] > 0
        rte_new.loc[mask_pos] = (daily_kpi.loc[mask_pos, 'discharge_kwh']
                                  / daily_kpi.loc[mask_pos, 'charge_kwh']
                                  * 100).round(2)
        daily_kpi['rte_pct'] = rte_new
        # EFC = min(chg, dis) / (LC_CAP_OP_KWH × n_containers_per_block) — but
        # we don't carry that constant here per-row; the calc happens inside
        # calc_daily_block_kpis_tashkent. Re-derive using the same constant.
        LC_CAP_OP_KWH = 5504.0 * 0.90  # mirrors KPI engine
        n_cont = 2  # Tashkent: 2 LCs per block
        eff_min = daily_kpi[['charge_kwh', 'discharge_kwh']].min(axis=1)
        efc_new = (eff_min / (LC_CAP_OP_KWH * n_cont)).round(3)
        efc_new[(daily_kpi['charge_kwh'] <= 0)
                | (daily_kpi['discharge_kwh'] <= 0)] = None
        daily_kpi['efc'] = efc_new
        # Quality flag: re-evaluate using the calibrated values
        def _qflag(r):
            chg = r['charge_kwh']; rte = r['rte_pct']
            if chg == 0 and r['discharge_kwh'] == 0:    return 'EXCLUDED'
            if chg < 100 or pd.isna(rte):                return 'INCOMPLETE'
            if rte < 50 or rte > 100:                    return 'ESTIMATED'
            return 'VALID'
        daily_kpi['quality_flag'] = daily_kpi.apply(_qflag, axis=1)
        calibration_applied = True

    log("Detecting anomalies...")
    anomalies = detect_anomalies(daily_kpi)

    # When canonical monthly totals are available, override the per-block RTE
    # in the anomaly frame to use them directly. detect_anomalies filters to
    # quality_flag == 'VALID' before aggregating, so its per-block sums are a
    # subset of the canonical monthly value — Jensen-style biases stack with
    # the VALID-only filter. The canonical RTE matches what the customer's
    # BMS HMI reports and is the right baseline for "underperformer" calls.
    if calibration_applied and not lc_total_block.empty and not anomalies.empty:
        canon_rte = {
            int(r['block_id']): (r['discharge_kwh'] / r['charge_kwh'] * 100
                                  if r['charge_kwh'] > 0 else np.nan)
            for _, r in lc_total_block.iterrows()
        }
        mapped = anomalies['block'].astype(int).map(canon_rte)
        anomalies['avg_rte'] = mapped.where(mapped.notna(), anomalies['avg_rte'])
        # Recompute z-scores + flag against the new fleet mean / σ
        rte_mean = anomalies['avg_rte'].mean()
        rte_std  = anomalies['avg_rte'].std()
        if rte_std and rte_std > 0:
            anomalies['rte_z'] = (anomalies['avg_rte'] - rte_mean) / rte_std
        else:
            anomalies['rte_z'] = 0.0
        anomalies['anomaly_rte']  = anomalies['rte_z'] < -1.0
        anomalies['anomaly_flag'] = anomalies[['anomaly_rte', 'anomaly_throughput']].any(axis=1)
        # Re-apply the absolute floor: a block at/above the 85% target is not an
        # underperformer, however it sits relative to the (recomputed) fleet.
        _meets = (anomalies['avg_rte'] >= 85).fillna(False)
        anomalies.loc[_meets, 'anomaly_flag'] = False
        # Re-derive likely_cause using the corrected flags
        fleet_min_soc = (anomalies['avg_min_soc'].mean()
                          if 'avg_min_soc' in anomalies.columns else np.nan)
        def _re_diagnose(r):
            if not r['anomaly_flag']:
                return ''
            parts = []
            if (pd.notna(r.get('min_soc_delta_pp'))
                    and r['min_soc_delta_pp'] > 5.0):
                parts.append(f"shallow cycling (min SOC ~{r['avg_min_soc']:.0f}%, "
                             f"fleet ~{fleet_min_soc:.0f}%)")
            if r.get('anomaly_throughput', False):
                parts.append(f"reduced throughput "
                             f"({r['avg_throughput']/1000:.1f} MWh/day)")
            if not parts:
                return ''
            return 'Likely cause: ' + ' and '.join(parts) + '.'
        anomalies['likely_cause'] = anomalies.apply(_re_diagnose, axis=1)

    # How many blocks finished the month with an average efficiency below the
    # 85% target — computed from the final (calibrated, all-days) per-block RTE.
    n_blocks_below_target = (
        int((anomalies['avg_rte'].notna() & (anomalies['avg_rte'] < 85)).sum())
        if not anomalies.empty and 'avg_rte' in anomalies.columns else 0)

    log("Computing SOC / SOH aggregates...")
    socsoh = calc_site_soc_soh(soc_long, soh_snap)

    # ── Pre-compute exclusion hours (if any) — uses the same block list /
    # period as the working-status frame so the cap (excluded ≤ outage) is
    # honest when exclusions straddle the month boundary.
    excl_result = None
    if exclusions:
        log(f"Applying {len(exclusions)} availability exclusion(s)...")
        from services.availability_service import calculate_plant_excluded_hours
        if not ws_long.empty:
            block_ids = sorted({int(b) for b in
                                  ws_long['block_id'].dropna().unique()})
            all_dates = sorted(set(
                pd.to_datetime(ws_long['Datetime']).dt.date.dropna()
            ))
        else:
            block_ids, all_dates = [], []
        excl_result = calculate_plant_excluded_hours(
            exclusions, all_dates, block_ids
        )

    excluded_hours_value = (excl_result['excluded_hours']
                             if excl_result else 0.0)

    # Build the container-level daily availability frame used by the new
    # categorical heatmap. Mean of (working_status in AVAILABLE_STATES) per
    # (date, block, container) — independent of the block-level metric in
    # daily_kpi.
    container_day_status = pd.DataFrame()
    if not ws_long.empty:
        ws = ws_long[['Date_only', 'block_id', 'container_id',
                       'working_status']].copy()
        st = ws['working_status'].astype(str)
        ws['avail'] = st.isin(AVAILABLE_STATES)
        ws['fault'] = st.isin(FAULT_STATES)
        cda = (ws.groupby(['Date_only', 'block_id', 'container_id'])
                 .agg(availability_pct=('avail', lambda x: x.mean() * 100),
                      fault_pct=('fault', lambda x: x.mean() * 100))
                 .reset_index())
        cda = cda.rename(columns={'Date_only': 'date'})

        # A block that completed (essentially) a full charge/discharge cycle
        # that day was demonstrably operating, so both its LCs are marked
        # available even if the working-status clock dipped below the threshold
        # (brief fault-state blips or short data gaps). The bar is 0.95
        # equivalent full cycles — a day at 0.95–0.99 efc is a full cycle for
        # practical purposes (counter line-up / rounding), so it shouldn't be
        # painted partial. efc is block-level, so it requires both LCs to have
        # carried real throughput before the day turns green.
        CYCLE_DONE_EFC = 0.95
        if 'efc' in daily_kpi.columns:
            bc = daily_kpi[['date', 'block', 'efc']].copy()
            bc['date'] = pd.to_datetime(bc['date']).dt.date
            bc['cycle_done'] = bc['efc'].fillna(0) >= CYCLE_DONE_EFC
            cda['date'] = pd.to_datetime(cda['date']).dt.date
            cda = cda.merge(
                bc[['date', 'block', 'cycle_done']],
                left_on=['date', 'block_id'], right_on=['date', 'block'],
                how='left',
            ).drop(columns=['block'])
            cda['cycle_done'] = cda['cycle_done'].fillna(False)

        # Operator-reported unavailability (manual entries) is painted onto
        # the heatmap too, so Graph 4 stays consistent with table 4.4.1 and
        # the contractual figure: the declared downtime caps the LC-day's
        # availability fraction and counts as fault evidence. A full-day entry
        # therefore shows red; a partial one at least yellow. The declared
        # downtime also beats the completed-cycle override for those days.
        if manual_unavailability:
            cda['date'] = pd.to_datetime(cda['date']).dt.date
            for m in _parse_manual_unavail(manual_unavailability):
                if m['hours'] <= 0:
                    continue
                days = pd.date_range(m['d_from'], m['d_to'], freq='D')
                frac = min(1.0, m['hours'] / (len(days) * 24.0))
                sel = (cda['block_id'] == m['block']) \
                      & cda['date'].isin({d.date() for d in days})
                if m['lc'] is not None:
                    sel &= (cda['container_id'] == m['lc'])
                if not sel.any():
                    continue
                cda.loc[sel, 'availability_pct'] = np.minimum(
                    cda.loc[sel, 'availability_pct'].fillna(100.0),
                    (1.0 - frac) * 100.0)
                cda.loc[sel, 'fault_pct'] = np.maximum(
                    cda.loc[sel, 'fault_pct'].fillna(0.0), frac * 100.0)
                if 'cycle_done' in cda.columns:
                    cda.loc[sel, 'cycle_done'] = False

        container_day_status = build_container_day_status(
            cda, alarms_df=alarms.get('production_dedup'),
        )

    # Section 4.4.1 — genuine fault-downtime incidents per LC, built from the
    # working-status fault-shutdown time (same basis as contractual avail), with
    # planned maintenance/restoration (exclusion windows) removed and the real
    # downtime + dominant cause attributed. Replaces the old heatmap-red scan
    # that reported 0.0 h / OTHER because it only saw production alarms.
    unavail_reasons = build_unavailability_reasons_tashkent(
        ws_long, alarms=alarms, exclusions=exclusions,
    )

    # Merge operator-entered unavailability rows (manual_unavailability) into
    # the auto-detected incidents. Manual rows complement — they never replace
    # the data-derived ones — and are flagged so the table can mark them.
    if manual_unavailability:
        # Period bounds so stale rows from another month don't leak in.
        _mp = pd.to_datetime(daily_kpi['date'], errors='coerce').dropna() \
                  if not daily_kpi.empty else pd.Series(dtype='datetime64[ns]')
        _mp_start = _mp.min().normalize() if len(_mp) else None
        _mp_end   = _mp.max().normalize() if len(_mp) else None
        man_rows = []
        for m in _parse_manual_unavail(manual_unavailability):
            if _mp_start is not None and (m['d_to'] < _mp_start
                                           or m['d_from'] > _mp_end):
                continue
            man_rows.append({
                'block_id': m['block'], 'container_id': m['lc'],
                'status': 'FAULT',
                'date_from': m['d_from'], 'date_to': m['d_to'],
                'n_days': (m['d_to'] - m['d_from']).days + 1,
                'cause': m['cause'], 'subsystem': m['subsystem'],
                'severity': '', 'fault_events': None,
                'downtime_h': m['hours'], 'manual': True,
            })
        if man_rows:
            mdf = pd.DataFrame(man_rows)
            if unavail_reasons is None or unavail_reasons.empty:
                unavail_reasons = mdf
            else:
                unavail_reasons = pd.concat(
                    [unavail_reasons.assign(manual=False), mdf],
                    ignore_index=True, sort=False,
                ).sort_values('downtime_h', ascending=False).reset_index(drop=True)

    log("Computing plant-level hours availability...")
    plant_avail = calc_plant_hours_availability_tashkent(
        ws_long,
        plant_capacity_mw=plant_capacity_mw,
        per_block_capacity_mw=per_block_capacity_mw,
        redundancy_threshold_pct=redundancy_threshold_pct,
        contractual_plant_capacity_mw=contractual_plant_capacity_mw,
        excluded_hours=excluded_hours_value,
    )

    # Contractual availability — capacity-weighted, counts only genuine
    # fault-shutdown time (the headline SLA figure). The PCS charge/discharge
    # status refines it to the PCS-unit level so a partially-failed LC only
    # loses the capacity that actually stopped.
    log("Loading PCS unit status for contractual availability...")
    pcs_unit_status = load_pcs_unit_status(pcs_cd_path)
    pcs_unit_fault = pd.DataFrame()
    if pcs_fault_path:
        log("Loading PCS per-unit fault flags...")
        pcs_unit_fault = load_pcs_unit_fault(pcs_fault_path)
    contractual_avail = calc_contractual_availability_tashkent(
        ws_long, pcs_long=pcs_unit_status, exclusions=exclusions,
        pcs_fault_long=pcs_unit_fault,
        manual_unavailability=manual_unavailability)

    # Per-reason summary for the Services Provision section
    p = alarms.get('production_dedup', pd.DataFrame())
    if not p.empty and 'cls_reason' in p.columns:
        cls_summary = (p.groupby(['cls_reason', 'cls_resolution'])
                       .size().reset_index(name='n_events')
                       .sort_values('n_events', ascending=False))
    else:
        cls_summary = pd.DataFrame()

    # ── 5.1 Faults / Alarms summaries with affected blocks + planned-stop flag ─
    # Drop exclusion-window events first (they're listed separately), then group.
    _p_all = alarms.get('production_dedup', pd.DataFrame())
    p_dedup = (_p_all[~_p_all.get('is_excluded', False)]
               if not _p_all.empty and 'is_excluded' in _p_all.columns else _p_all)
    _w_all = alarms.get('warning_persistent', pd.DataFrame())
    w_persistent = (_w_all[~_w_all.get('is_excluded', False)]
                    if not _w_all.empty and 'is_excluded' in _w_all.columns else _w_all)
    # Faults/alarms on blocks that were under a planned/manual stop are dropped
    # entirely (not just marked '*') — a block taken offline on purpose should
    # not show its shutdown-related faults as if they affected production. The
    # filter is per-event, so the same fault type occurring outside a planned
    # stop still appears.
    faults_summary = build_faults_summary(p_dedup, ws_long=ws_long, drop_planned=True)
    warn_summary   = build_faults_summary(w_persistent, ws_long=ws_long, drop_planned=True)

    # Major faults (important events) + persistent-warning detail. Each is tagged
    # with the planned-stop flag, then events on planned/manual-stopped blocks
    # are removed so only genuine operational faults remain.
    important_events = select_important_alarms(_p_all)
    important_events = mark_planned_stop_events(important_events, ws_long=ws_long)
    if (important_events is not None and not important_events.empty
            and 'on_planned_stop' in important_events.columns):
        important_events = important_events[
            ~important_events['on_planned_stop'].astype(bool)]
    _wd = w_persistent.copy()
    if not _wd.empty:
        _tu = _wd['Trigger name'].astype(str).str.upper()
        for _pat in NOISE_TRIGGER_PATTERNS:
            _wd = _wd[~_tu.str.contains(_pat.upper(), na=False)]
            _tu = _wd['Trigger name'].astype(str).str.upper()
        _wd = _wd.sort_values('duration_min', ascending=False)
    warn_detail = mark_planned_stop_events(_wd, ws_long=ws_long) if not _wd.empty else _wd
    if (warn_detail is not None and not warn_detail.empty
            and 'on_planned_stop' in warn_detail.columns):
        warn_detail = warn_detail[~warn_detail['on_planned_stop'].astype(bool)]

    # 5.2 — semi-automatic breakdowns. Operator-entered incidents win; otherwise
    # auto-pull candidates from the longest genuine faults (planned stops out),
    # leaving solution columns blank for the operator to complete.
    breakdown_candidates = build_breakdown_candidates(p_dedup, ws_long=ws_long)
    breakdown_rows    = breakdown_incidents if breakdown_incidents else breakdown_candidates
    breakdown_is_auto = (not breakdown_incidents) and bool(breakdown_candidates)

    # Period stats
    dates = sorted(daily_kpi['date'].unique())
    if not dates:
        raise RuntimeError("No valid days found in the data")
    period_str = f"{dates[0].strftime('%d %B %Y')} — {dates[-1].strftime('%d %B %Y')}"
    report_month = dates[0].strftime('%B %Y')
    n_blocks = daily_kpi['block'].nunique()

    # Projection factor for "Projected Cycles for the Month": scale the cycles
    # accrued so far by (days in the calendar month / days of data present).
    # Equals 1.0 for a complete month → projected == actual.
    import calendar as _calendar
    _days_in_month = _calendar.monthrange(dates[0].year, dates[0].month)[1]
    _days_span = (dates[-1] - dates[0]).days + 1
    cycle_projection_factor = (_days_in_month / _days_span) if _days_span > 0 else 1.0

    valid = daily_kpi[daily_kpi['quality_flag'] == 'VALID']
    total_charge_mwh    = daily_kpi['charge_kwh'].sum() / 1000
    total_discharge_mwh = daily_kpi['discharge_kwh'].sum() / 1000

    # Monthly round-trip efficiency = total discharge / total charge over the
    # WHOLE month (every day). Over a month the day-to-day SOC carry-over (a
    # cycle that runs across midnight) nets out, so this is the unbiased figure.
    # Restricting it to full-cycle days would bias it low, because the matching
    # net-discharge days read >100% and get filtered out while the net-charge
    # days stay in. The daily chart still uses full-cycle days only — that is the
    # right place to drop noisy daily values, not the monthly total.
    fleet_rte = (total_discharge_mwh / total_charge_mwh * 100) if total_charge_mwh > 0 else 0

    # Counters for the daily-chart note: full cycles vs partial among VALID days.
    full_days = (valid[valid['full_cycle']]
                 if 'full_cycle' in valid.columns else valid)
    n_fullcycle_blockdays = int(len(full_days))
    n_shallow_excluded    = int(len(valid) - len(full_days))
    fleet_availability_container = valid['availability_pct'].mean()
    total_efc_fleet    = daily_kpi.groupby('block')['efc'].sum().sum()
    avg_efc_per_block  = daily_kpi.groupby('block')['efc'].sum().mean()

    # When a CMU cycle snapshot is available, prefer the SCADA counter delta —
    # it's the same number the customer's BMS reports, so it lines up with
    # what they see on the HMI. The daily-totalizer estimate stays as the
    # fallback when no snapshot is provided. Blocks with NaN delta (begin or
    # end couldn't be recovered) are excluded from the fleet aggregate so
    # they don't drag it toward zero.
    if not cycles_snap.empty and cycles_snap['cycle_delta'].notna().any():
        valid_d = cycles_snap['cycle_delta'].dropna()
        total_efc_fleet   = float(valid_d.sum())
        avg_efc_per_block = float(valid_d.mean())
        valid_e = cycles_snap['cycle_end'].dropna()
        # Use the snapshot's accumulative cycles as the "accumulative" series
        # (overrides the history-derived cycles_accum_avg only if the latter
        # wasn't passed explicitly).
        if cycles_accum_avg is None and not valid_e.empty:
            cycles_accum_avg = float(valid_e.mean())
    # Keep the fleet total and the per-block average reconcilable against the
    # block count shown in the report. When some blocks' cycle counters are
    # unreadable (snapshot deltas were NaN), the measured sum covers fewer
    # blocks than the per-block average's basis, so a reader doing
    # total / n_blocks wouldn't land on the stated average. Re-derive the
    # fleet total from the per-block average across all blocks so the two
    # figures line up on the page (total = average x blocks). For the
    # no-snapshot path this is a no-op (sum already equals mean x n_blocks).
    if n_blocks:
        total_efc_fleet = avg_efc_per_block * n_blocks
    days_excluded = (daily_kpi['quality_flag'] == 'EXCLUDED').sum() / max(n_blocks, 1)
    n_prod_alarms = len(alarms['production_dedup'])
    n_warn_persistent = len(alarms['warning_persistent'])
    n_warn_transient  = len(alarms['warning_transient'])

    # Exclusion-window subcounts (zero when no exclusions were supplied)
    def _excluded_count(df):
        if df is None or df.empty or 'is_excluded' not in df.columns:
            return 0
        return int(df['is_excluded'].sum())
    n_prod_alarms_excluded     = _excluded_count(alarms.get('production_dedup'))
    n_warn_persistent_excluded = _excluded_count(alarms.get('warning_persistent'))

    # Planned/manual-stop subcounts — faults & alarms that coincided with a
    # planned or manual stop (Manual/Key Stop, exclusion window, or STOPPED
    # state). These are removed from the 5.1 tables and from the headline
    # tallies; a note tells the reader they were set aside, not missed.
    def _planned_count(df):
        if df is None or df.empty:
            return 0
        m = mark_planned_stop_events(df, ws_long=ws_long)
        return (int(m['on_planned_stop'].astype(bool).sum())
                if 'on_planned_stop' in getattr(m, 'columns', []) else 0)
    n_prod_alarms_planned     = _planned_count(alarms.get('production_dedup'))
    n_warn_persistent_planned = _planned_count(alarms.get('warning_persistent'))
    n_prod_alarms_genuine     = max(0, n_prod_alarms - n_prod_alarms_planned)
    n_warn_persistent_genuine = max(0, n_warn_persistent - n_warn_persistent_planned)

    avg_soc_pct = socsoh.get('avg_soc_pct') or 0
    avg_soh_pct = socsoh.get('avg_soh_pct') or 0
    n_anomalies = int(anomalies['anomaly_flag'].sum()) if not anomalies.empty else 0

    # Monthly comparison
    if history_records is None:
        history_records = load_history_records()
    current_month_record = {
        'month':         report_month,
        'avg_soc_pct':   avg_soc_pct,
        'avg_soh_pct':   avg_soh_pct,
        'rte_pct':       fleet_rte,
        'cycles_total':  avg_efc_per_block,
        'discharge_mwh': total_discharge_mwh,
        'charge_mwh':    total_charge_mwh,
    }
    monthly_compare = build_monthly_comparison(
        current_kpis=current_month_record,
        history_records=history_records,
    )

    log("Building PDF...")
    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        leftMargin=20*mm, rightMargin=20*mm,
        topMargin=20*mm, bottomMargin=20*mm,
        title=f'BESS Operations Report — {site_name} {report_month}',
        author='BESS Tracker Field Service',
    )
    story = []
    _build_cover(story, report_month, site_name, n_blocks, period_str,
                  report_number=report_number, prepared_by=prepared_by,
                  reviewed_by=reviewed_by)

    # ── 1. PROJECT DETAILS ───────────────────────────────────────────────
    story.append(_section('1.  Project Details', ''))
    story.append(Spacer(1, 3*mm))
    project_rows = [
        ['Project name',     site_name],
        ['Reporting period', period_str],
        ['Number of blocks', f'{n_blocks} blocks'],
    ]
    if project_details:
        for k, v in project_details.items():
            safe_k = str(k).replace('&', '&amp;')
            safe_v = str(v).replace('&', '&amp;')
            project_rows.append([safe_k, safe_v])
    story.append(_styled_table(['Parameter', 'Value'], project_rows,
                                col_widths=[60*mm, 110*mm]))
    story.append(_hr())

    # ── 2. SUMMARY ───────────────────────────────────────────────────────
    story.append(_section('2.  Summary', ''))
    story.append(Spacer(1, 3*mm))
    avail_status = 'above' if fleet_availability_container >= 95 else 'below'
    rte_status   = 'above' if fleet_rte >= 85 else 'below'
    summary = (
        f"During <b>{period_str}</b>, the {site_name} site ({n_blocks} blocks) "
        f"discharged <b>{total_discharge_mwh:,.1f} MWh</b> from "
        f"<b>{total_charge_mwh:,.1f} MWh</b> of charging. The site's average "
        f"round-trip efficiency was <b>{fleet_rte:.2f}%</b> ({rte_status} the "
        f"85% target). "
    )
    if contractual_avail and contractual_avail.get('availability_pct') is not None:
        summary += (
            f"BESS availability for the period was "
            f"<b>{contractual_avail['availability_pct']:.2f}%</b> "
            f"(contractual method — capacity-weighted, counting only genuine "
            f"fault-shutdown time). "
        )
    if plant_avail and plant_avail.get('plant_availability_pct') is not None:
        summary += (
            f"Allowing for spare capacity the site met its contracted output "
            f"<b>{plant_avail['plant_availability_pct']:.2f}%</b> of the time, "
            f"and the simple average across individual blocks was "
            f"<b>{fleet_availability_container:.2f}%</b>. "
        )
    elif not contractual_avail:
        summary += (
            f"Average availability across blocks was "
            f"<b>{fleet_availability_container:.2f}%</b> ({avail_status} the 95% "
            f"target). "
        )
    summary += (
        f"Average state of charge was <b>{avg_soc_pct:.2f}%</b> and average "
        f"state of health (at month end) <b>{avg_soh_pct:.2f}%</b>. The blocks "
        f"completed about <b>{total_efc_fleet:.0f}</b> full charge / discharge "
        f"cycles in total (roughly {avg_efc_per_block:.1f} each). On average "
        f"<b>{days_excluded:.1f}</b> day(s) per block had no usable data and "
        f"were left out of the efficiency and availability figures."
    )
    story.append(Paragraph(summary, STYLE_BODY))
    story.append(_hr())

    # ── 3. SERVICES PROVISION ────────────────────────────────────────────
    story.append(_section('3.  Services Provision', ''))
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph('<b>3.1  Preventative Maintenance (PM)</b>', STYLE_H3))
    if pm_activities:
        for act in pm_activities: story.append(Paragraph(f'•  {act}', STYLE_BODY))
    else:
        story.append(Paragraph('No PM activities in the reporting period.',
                                STYLE_BODY))
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph('<b>3.2  Corrective Maintenance</b>', STYLE_H3))
    if cm_activities:
        for act in cm_activities: story.append(Paragraph(f'•  {act}', STYLE_BODY))
    elif not cls_summary.empty:
        story.append(Paragraph(
            'Automated summary based on alarm classification — recurring '
            'corrective items observed this period:', STYLE_BODY))
        top_cm = cls_summary.head(7)
        cm_rows = [[r['cls_reason'], str(int(r['n_events'])),
                     r['cls_resolution'] or '—']
                    for _, r in top_cm.iterrows()]
        story.append(_styled_table(
            ['Item', 'Occurrences', 'Resolution applied'],
            cm_rows, col_widths=[70*mm, 30*mm, 70*mm]
        ))
    else:
        story.append(Paragraph('No corrective maintenance activities recorded.',
                                STYLE_BODY))
    story.append(_hr())

    # ── 4. PLANT PERFORMANCE ─────────────────────────────────────────────
    story.append(_section("4.  Plant's Performance", ''))
    story.append(Spacer(1, 3*mm))

    # 4.1 Performance data + cycles
    story.append(Paragraph('<b>4.1  Plant Performance Data</b>', STYLE_H3))
    story.append(_styled_table(
        ['No', 'Item', 'Total (MWh)'],
        [['1', 'Imported Energy (Charge)',  f'{total_charge_mwh:,.2f}'],
         ['2', 'Exported Energy (Discharge)', f'{total_discharge_mwh:,.2f}']],
        col_widths=[20*mm, 110*mm, 40*mm]
    ))
    if calibration_applied:
        story.append(Paragraph(
            '<i>Monthly energy figures calibrated to the canonical SCADA '
            'LC total charge/discharge counter; matches the value the '
            'customer\'s BMS HMI reports.</i>', STYLE_SMALL))
    story.append(Spacer(1, 4*mm))
    story.append(_fig_to_image(_chart_single_daily_bar(
        daily_kpi, 'charge_kwh', 'Daily Imported Power', '#0071E3'), 170, 60))
    story.append(Paragraph('Graph 1: Daily Imported Power (MWh)', STYLE_CAP))
    story.append(Spacer(1, 2*mm))
    story.append(_fig_to_image(_chart_single_daily_bar(
        daily_kpi, 'discharge_kwh', 'Daily Exported Power', '#AF52DE'), 170, 60))
    story.append(Paragraph('Graph 2: Daily Exported Power (MWh)', STYLE_CAP))
    story.append(Spacer(1, 3*mm))

    # Cycles
    story.append(Paragraph('<b>Number of Cycles</b>', STYLE_H3))
    yt = float(yearly_cycle_target) or 365.0
    accum_lifetime = cycles_accum_avg if cycles_accum_avg else avg_efc_per_block
    # "Accumulative in one Year" = sum of last 12 months of cycles_total in
    # monthly_history.json (the current month included). Falls back to the
    # current month's value when no prior history exists.
    annual_accum = avg_efc_per_block
    try:
        if history_records:
            recent = sorted(
                [r for r in history_records if r.get('cycles_total') is not None],
                key=lambda r: str(r.get('month','')))[-11:]   # 11 prior months
            annual_accum = sum(float(r.get('cycles_total') or 0)
                                for r in recent) + avg_efc_per_block
    except Exception:
        pass
    story.append(_styled_table(
        ['No', 'Item', 'Total', f'% of yearly cycles ({yt:.0f})'],
        [['1', 'Number of Cycles in Reported Month',
          f'{avg_efc_per_block:.1f}', f'{avg_efc_per_block/yt*100:.2f}%'],
         ['2', 'Accumulative Number of Cycles in one Year',
          f'{annual_accum:.1f}',     f'{annual_accum/yt*100:.2f}%'],
         ['3', 'Accumulative Number of Cycles',
          f'{accum_lifetime:.1f}',    f'{accum_lifetime/yt*100:.2f}%']],
        col_widths=[15*mm, 95*mm, 30*mm, 30*mm]
    ))
    story.append(Spacer(1, 3*mm))

    # Cycles block-wise (from CMU snapshot if supplied)
    if not cycles_snap.empty:
        month_name_only = dates[0].strftime('%B')   # e.g. "March"
        # Per-block charge/discharge totals for the period (in kWh) — sourced
        # from the same daily_kpi the fleet aggregates use, so the numbers
        # reconcile with section 4.1's "Imported / Exported Energy" totals.
        per_block_energy_kwh = daily_kpi.groupby('block').agg(
            charge_kwh=('charge_kwh', 'sum'),
            discharge_kwh=('discharge_kwh', 'sum'),
        ).to_dict('index')
        story.append(Paragraph('<b>Cycles per block</b>', STYLE_H3))
        story.append(Paragraph(
            'For each block: the total number of charge / discharge cycles it '
            'has accumulated to date, and how many of those were completed this '
            f'month ({month_name_only}). Cycle figures are the average across '
            'the battery modules in the block; the energy figures are the '
            'block\'s total charging and discharging for the month.',
            STYLE_SMALL))
        story.append(Spacer(1, 2*mm))
        for fl in _cycles_block_chunked(cycles_snap, month_name_only,
                                          chunk_size=10,
                                          energy_by_block=per_block_energy_kwh,
                                          projection_factor=cycle_projection_factor):
            story.append(fl)
        # Transparency note: blocks where the snapshot couldn't fill every
        # field (typically no comms across the snapshot day → "—" in the
        # tables above). The closest non-zero in the available rows was
        # used wherever possible, so this only fires when the file genuinely
        # has no usable data for that block at that boundary.
        partial = cycles_snap[
            cycles_snap[['cycle_begin','cycle_end','cycle_delta']].isna().any(axis=1)
        ]
        if not partial.empty:
            ids = ', '.join(str(int(b)) for b in partial['block_id'])
            story.append(Paragraph(
                f'<i>Note: block(s) {ids} had no reading at the start or end of '
                f'the month — usually because of maintenance or a communication '
                f'gap on those days. The nearest available reading was used '
                f'where possible; a "—" means the gap could not be filled.</i>',
                STYLE_SMALL))
            story.append(Spacer(1, 2*mm))

        # Cycle-balancing note: blocks deliberately rested this month.
        if rested_blocks:
            _rb = ', '.join(str(b) for b in sorted(rested_blocks))
            story.append(Paragraph(
                f'<i>Note: block(s) {_rb} were intentionally kept out of '
                f'operation this month to balance the number of cycles across '
                f'the fleet — keeping all blocks near an equal cycle count and '
                f'within the annual 365-cycle budget. Their lower cycle count '
                f'and RTE this month are deliberate and do not represent a fault '
                f'or reduced availability.</i>',
                STYLE_SMALL))
            story.append(Spacer(1, 2*mm))

    # Contractual availability (primary SLA metric)
    if contractual_avail and contractual_avail.get('availability_pct') is not None:
        ca = contractual_avail
        _refined = (ca.get('method') == 'pcs_unit')
        story.append(Paragraph('<b>Contractual Availability</b>', STYLE_H3))
        story.append(Paragraph(
            'This is the contractual measure of BESS availability. A unit '
            'counts as unavailable only for the time it spent in a genuine '
            'fault shutdown — standby, startup, manual stops and data gaps do '
            'not count against it — and every outage is weighted by the '
            'nameplate capacity taken out of service. ' +
            ('Resolution is at the PCS-unit level: during a fault we count only '
             'the converter units that actually stopped (the charge/discharge '
             'data confirms units that kept running), so a partly-failed LC '
             'loses only the capacity that genuinely went off-line.'
             if _refined else
             'Resolution is at the LC level (the whole LC is counted down for '
             'its fault time).') +
            (' Per-unit fault flags from the PCS fault-status export are also '
             'taken into account, so a fault on a single converter unit is '
             'captured even when its LC reads healthy.'
             if ca.get('used_unit_fault') else ''),
            STYLE_BODY))
        story.append(Paragraph(
            f'Availability = 1 &#8722; (fault-shutdown time &#215; capacity out '
            f'of service) &#247; (hours in period &#215; installed nameplate) '
            f'= 1 &#8722; ({ca["down_unit_hours"]:,.1f} {ca["unit_label"]}-h '
            f'&#215; {ca["unit_capacity_kwh"]/1000:.3f} MWh) &#247; '
            f'({ca["total_hours"]:,.0f} h &#215; {ca["installed_kwh"]/1000:,.1f} '
            f'MWh) = <b>{ca["availability_pct"]:.2f}%</b>.',
            STYLE_SMALL))
        _excl_note = (
            f' Planned maintenance / restoration time inside exclusion windows '
            f'(e.g. post-service "system not ready" warm-up) is not counted: '
            f'{ca["excluded_unit_hours"]:,.1f} {ca["unit_label"]}-h excluded.'
            if ca.get('excluded_unit_hours', 0) > 0 else '')
        _excl_note += (
            f' Operator-reported unavailability adds '
            f'{ca["manual_unit_hours"]:,.1f} {ca["unit_label"]}-h to the '
            f'fault-shutdown time.'
            if ca.get('manual_unit_hours', 0) > 0 else '')
        story.append(Paragraph(
            f'<i>Hierarchy: 1 block = 2 LC = 4 PCS units = 4 BESS containers; '
            f'1 PCS unit = 1 container = 2,752 kWh. Installed nameplate across '
            f'{ca["n_units"]} {ca["unit_label"]}s = {ca["installed_kwh"]/1000:,.1f} '
            f'MWh. Fault-shutdown time is the total across all units.{_excl_note}</i>',
            STYLE_SMALL))
        story.append(Spacer(1, 4*mm))

    # Plant-level availability hours
    story.append(Paragraph('<b>Availability Hours (plant-level redundancy method)</b>',
                            STYLE_H3))
    story.append(Paragraph(
        'These are supplementary views of availability; the contractual figure '
        'above is the headline SLA number. '
        'This measures availability for the plant as a whole, rather than '
        'counting every individual block-hour lost. A block is treated as '
        'available at any moment only when both of its containers are in a '
        'working state. Because the plant carries spare (redundant) capacity, '
        'it is considered available for as long as the combined output of the '
        'blocks that are up stays at or above the agreed threshold — the '
        'contracted capacity where one is defined, otherwise a set share of '
        'nameplate capacity (shown in the table below). The plant is counted '
        'as unavailable only during the time its available capacity drops '
        'below that threshold; planned-maintenance hours are removed from the '
        'clock so they do not count against the figure. The <b>plant-level '
        'availability</b> row applies this redundancy credit, while '
        '<b>container-level availability</b> is the stricter view that adds up '
        'every block-hour of downtime with no credit for spare capacity — so '
        'it is always the lower of the two.',
        STYLE_BODY))
    story.append(Spacer(1, 2*mm))
    if plant_avail:
        sched = plant_avail['scheduled_hours']
        plant_out = plant_avail.get('plant_outage_hours')
        cont_out  = plant_avail['container_outage_hours']
        rows = [
            ['Scheduled hours',                              f'{sched:,.1f} h'],
            ['Scheduled unavailability (PM)',                f'{scheduled_unavail_hours:,.1f} h'],
            ['Unscheduled outage (container-aggregate)',
                f'{cont_out:,.1f} h ({cont_out/sched*100:.2f}%)'],
            ['Plant-level outage (redundancy adjusted)',
                f'{plant_out:,.1f} h ({plant_out/sched*100:.2f}%)'
                if plant_out is not None else
                'n/a (plant capacity not configured)'],
        ]
        if plant_avail.get('plant_availability_pct') is not None:
            rows.append(['<b>Plant-level availability</b>',
                f"<b>{plant_avail['plant_availability_pct']:.2f}%</b>"])
        rows.append(['Container-level availability (current engine)',
                f"{fleet_availability_container:.2f}%"])
        if plant_avail.get('excluded_effective', 0) > 0:
            rows.append([
                'Excluded hours (applied to availability)',
                f"{plant_avail['excluded_effective']:.1f} h "
                f"(of {plant_avail.get('excluded_hours', 0):.1f} h requested)"
            ])
        story.append(_styled_table(['Item', 'Value'], rows,
            col_widths=[110*mm, 60*mm]))
    story.append(Spacer(1, 4*mm))

    # ── Applied Exclusions (only when at least one was provided) ─────────
    if exclusions and excl_result and excl_result.get('events'):
        story.append(Paragraph('<b>Scheduled Unavailability</b>', STYLE_H3))
        story.append(Spacer(1, 2*mm))
        # Customer-style narrative bullets (matches APRS template)
        lead, bullets = format_exclusion_narrative(exclusions)
        if lead:
            story.append(Paragraph(lead, STYLE_BODY))
            for b in bullets:
                story.append(Paragraph(f"•  {b}", STYLE_BODY))
            story.append(Spacer(1, 3*mm))
        story.append(Paragraph('<b>Applied Exclusions (Detail)</b>', STYLE_BODY))
        story.append(Spacer(1, 2*mm))
        total_excl = excl_result['excluded_hours']
        applied    = (plant_avail.get('excluded_effective', 0.0)
                       if plant_avail else 0.0)
        summary = (
            f"During the period there were <b>{len(excl_result['events'])}</b> "
            f"planned exclusion period(s) — agreed downtime that does not count "
            f"against availability. Together they added up to "
            f"<b>{total_excl:,.1f} h</b> (counting how many blocks each one "
            f"affected). Of that, only <b>{applied:,.1f} h</b> were removed "
            f"from the availability figure, because we set aside only the time "
            f"the plant was genuinely down for this work — never more than was "
            f"actually lost."
        )
        story.append(Paragraph(summary, STYLE_BODY))
        story.append(Spacer(1, 2*mm))

        brk_rows = [
            [t, f"{h:.1f} h",
             f"{(h/total_excl*100 if total_excl else 0):.1f}%"]
            for t, h in excl_result['breakdown'].items() if h > 0
        ]
        if brk_rows:
            story.append(_styled_table(
                ['Exclusion Type', 'Weighted Hours', '% of Excluded'],
                brk_rows,
                col_widths=[80*mm, 45*mm, 45*mm]
            ))
            story.append(Spacer(1, 2*mm))

        detail_rows = []
        for ev in excl_result['events']:
            exc = ev['exclusion']
            t_from = exc.get('time_from', '00:00') or '00:00'
            t_to   = exc.get('time_to',   '23:59') or '23:59'
            blk_val = (exc.get('affected_blocks') or '').strip()
            blk_disp = blk_val if blk_val else 'All blocks'
            detail_rows.append([
                exc.get('exclusion_type', ''),
                f"{exc.get('date_from','')} {t_from}",
                f"{exc.get('date_to','')} {t_to}",
                f"{ev['hours_in_period']:.1f}",
                blk_disp,
                exc.get('description', ''),
            ])
        story.append(_styled_table(
            ['Type', 'From', 'To', 'Hours', 'Blocks', 'Description'],
            detail_rows,
            col_widths=[32*mm, 28*mm, 28*mm, 18*mm, 22*mm, 42*mm]
        ))
        story.append(Spacer(1, 4*mm))

    # 4.2 KPIs
    story.append(Paragraph('<b>4.2  Key Performance Indicators</b>', STYLE_H3))
    story.append(_kpi_row([
        (f"{avg_soc_pct:.2f}%", 'Average state of charge', '#0071E3'),
        (f"{avg_soh_pct:.2f}%", 'Average state of health', '#34C759'),
        (f"{fleet_rte:.2f}%",    'Round-trip efficiency', '#FF9500'),
    ]))
    story.append(Spacer(1, 1*mm))
    story.append(Paragraph(
        f'Round-trip efficiency = total energy discharged &#247; total energy '
        f'charged over the month = '
        f'<b>{total_discharge_mwh:,.1f} MWh &#247; {total_charge_mwh:,.1f} MWh '
        f'= {fleet_rte:.2f}%</b>.',
        STYLE_SMALL))
    story.append(Spacer(1, 4*mm))
    story.append(_fig_to_image(_chart_soc_trend(socsoh), 170, 55))
    story.append(Paragraph('Graph 3: Site-wide average SOC trend (daily)', STYLE_CAP))
    story.append(Spacer(1, 4*mm))

    # 4.3 Monthly comparison
    story.append(Paragraph('<b>4.3  Monthly Performance Comparison</b>', STYLE_H3))
    if not monthly_compare.empty and len(monthly_compare) > 1:
        cmp_rows = []
        for _, r in monthly_compare.iterrows():
            cmp_rows.append([
                str(r['month']),
                f"{r['avg_soc_pct']:.1f}%" if pd.notna(r['avg_soc_pct']) else '—',
                f"{r['avg_soh_pct']:.1f}%" if pd.notna(r['avg_soh_pct']) else '—',
                f"{r['rte_pct']:.1f}%"     if pd.notna(r['rte_pct']) else '—',
                f"{r['cycles']:.1f}"        if pd.notna(r['cycles']) else '—',
                f"{r['discharge_mwh']:,.0f}" if pd.notna(r['discharge_mwh']) else '—',
                f"{r['charge_mwh']:,.0f}"    if pd.notna(r['charge_mwh']) else '—',
            ])
        story.append(_styled_table(
            ['Month', 'SOC', 'SOH', 'RTE', 'Cycles',
             'Discharge (MWh)', 'Charge (MWh)'],
            cmp_rows,
            col_widths=[22*mm, 18*mm, 18*mm, 18*mm, 22*mm, 36*mm, 36*mm]
        ))
    else:
        story.append(Paragraph(
            'Only current month available — subsequent runs will populate '
            'this trend automatically (history stored in '
            'data/monthly_history.json).', STYLE_SMALL))
    story.append(Spacer(1, 4*mm))

    # 4.4 System availability
    story.append(Paragraph('<b>4.4  System Availability</b>', STYLE_H3))
    story.append(_fig_to_image(
        _chart_availability_heatmap_categorical(
            container_day_status, exclusions=exclusions, fig_width=18),
        170, 130))
    story.append(Paragraph(
        'Graph 4: Daily availability status per container (Block N - LC1 / LC2). '
        '<b>Green</b> = available: the block completed at least one full '
        'charge/discharge cycle that day, or was on standby and ready the '
        'whole day; <b>orange</b> = available but with alarms; <b>yellow</b> = '
        'partially available — operated only part of the day and did not '
        'complete a full cycle; <b>red</b> = did not operate at all and faults '
        'were recorded. Hatched cells = inside a planned exclusion window.' +
        (' Unavailability reported by the operator (see 4.4.1) is also '
         'reflected here.' if manual_unavailability else ''),
        STYLE_CAP))
    story.append(Spacer(1, 3*mm))

    # 4.4.1 Unavailability reasons — only the red (did-not-operate) days, kept
    # compact so the table stays readable.
    if not unavail_reasons.empty:
        _MAX_REASON_ROWS = 20
        ur = unavail_reasons.sort_values('downtime_h', ascending=False)
        story.append(Paragraph(
            '<b>4.4.1  Unavailability Reasons</b>', STYLE_H3))
        story.append(Paragraph(
            'Containers that were in a genuine fault shutdown are listed here, '
            'with the time lost and the dominant fault recorded during the '
            'outage. Planned maintenance and restoration (including the '
            'post-service warm-up) are excluded, so this reflects only real, '
            'unplanned downtime.',
            STYLE_BODY))
        rows = []
        any_manual = False
        for _, r in ur.head(_MAX_REASON_ROWS).iterrows():
            d_from = r['date_from'].strftime('%d-%b')
            d_to   = r['date_to'].strftime('%d-%b')
            period = d_from if r['n_days'] == 1 else f"{d_from} – {d_to}"
            cause  = r['cause'] or 'Unclassified'
            if r['severity']:
                cause = f"{cause} ({r['severity']})"
            is_manual = bool(r.get('manual', False))
            if is_manual:
                cause += ' *'; any_manual = True
            lc_val = r['container_id']
            cont = (f"Block {int(r['block_id'])} - LC{int(lc_val)}"
                    if pd.notna(lc_val) else f"Block {int(r['block_id'])}")
            ev = r['fault_events']
            rows.append([
                cont,
                period,
                str(int(r['n_days'])),
                cause,
                str(r['subsystem'] or '—'),
                (str(int(ev)) if pd.notna(ev) else '—'),
                f"{r['downtime_h']:.1f} h",
            ])
        story.append(_styled_table(
            ['Container', 'Period', 'Days', 'Dominant Cause',
             'Subsystem', 'Events', 'Downtime'],
            rows,
            col_widths=[28*mm, 24*mm, 12*mm, 44*mm, 22*mm, 16*mm, 18*mm]
        ))
        if any_manual:
            story.append(Paragraph(
                '<i>* entered manually by the operator.</i>', STYLE_SMALL))
        if len(ur) > _MAX_REASON_ROWS:
            story.append(Paragraph(
                f'<i>Showing the {_MAX_REASON_ROWS} longest of {len(ur)} '
                f'downtime incidents.</i>', STYLE_SMALL))
        story.append(Spacer(1, 3*mm))
    story.append(_fig_to_image(_chart_daily_rte_per_block_t(daily_kpi), 170, 70))
    story.append(Paragraph(
        'Graph 5: Daily round-trip efficiency — light lines are individual '
        'blocks, the dark line is the site average. Target: 85%.', STYLE_CAP))
    story.append(Paragraph(
        'The daily values above include only days with a full charge / discharge '
        'cycle, so the chart stays readable. The headline efficiency figure is '
        'based on the whole month\'s charging and discharging together, which '
        'evens out the day-to-day swings (a single cycle often runs across '
        'midnight) and is the dependable number.',
        STYLE_SMALL))
    story.append(Spacer(1, 3*mm))
    if not anomalies.empty:
        flagged = anomalies[anomalies['anomaly_flag']].sort_values('rte_z')
        if not flagged.empty:
            story.append(Paragraph(
                f'<b>Blocks performing below the rest of the site</b> '
                f'({len(flagged)} block(s) noticeably below both the 85% target '
                f'and the site average):', STYLE_BODY))
            rows = []
            for _, r in flagged.iterrows():
                rows.append([
                    f"Block {int(r['block'])}",
                    f"{r['avg_rte']:.2f}%",
                    f"{r['avg_throughput']/1000:,.1f} MWh",
                    f"{r['avg_availability']:.2f}%" if pd.notna(r['avg_availability']) else '—',
                    str(int(r['total_alarms'])),
                    str(r.get('likely_cause', '') or ''),
                ])
            story.append(_styled_table(
                ['Block', 'Round-trip efficiency', 'Energy per day',
                 'Availability', 'Faults', 'Likely cause'],
                rows,
                col_widths=[18*mm, 28*mm, 24*mm, 22*mm, 16*mm, 54*mm]
            ))
            story.append(Spacer(1, 3*mm))

    # Round-trip efficiency — a short, honest note. Daily readings on this data
    # are noisy (a cycle spans midnight and the daily energy counters don't align
    # exactly to the calendar day), so we don't break the daily dips down; the
    # monthly figure is the reliable one.
    if n_blocks_below_target:
        story.append(Paragraph(
            f'{n_blocks_below_target} block(s) finished the month with an average '
            f'round-trip efficiency below the 85% target — these are listed '
            f'above. Individual daily readings are noisy (a charge / discharge '
            f'cycle often runs across midnight and the daily energy counters do '
            f'not line up exactly with it), so efficiency is best judged on the '
            f'monthly figure.', STYLE_BODY))
    else:
        story.append(Paragraph(
            f'Every block met the 85% round-trip efficiency target over the month '
            f'(site average <b>{fleet_rte:.1f}%</b>). Some individual daily '
            f'readings fall below 85%, but that reflects where the calendar day '
            f'falls within a charge / discharge cycle and how the daily energy '
            f'counters line up — not a real loss of efficiency. Efficiency is '
            f'read from the monthly figure, which balances all of the month\'s '
            f'charging and discharging.', STYLE_BODY))
    story.append(Spacer(1, 3*mm))
    story.append(_hr())

    # ── 5. SYSTEM OPERATION (Faults + Alarms) ────────────────────────────
    story.append(_section('5.  System Operation', ''))
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph('<b>5.1  Summary of Faults and Alarms</b>', STYLE_H3))

    p_dedup_all = alarms.get('production_dedup', pd.DataFrame())
    p_dedup = (p_dedup_all[~p_dedup_all.get('is_excluded', False)]
                if not p_dedup_all.empty and 'is_excluded' in p_dedup_all.columns
                else p_dedup_all)
    if not faults_summary.empty:
        story.append(Paragraph('<b>Faults that affected production</b>', STYLE_BODY))
        if n_prod_alarms_planned > 0:
            story.append(Paragraph(
                f"<i>{n_prod_alarms_planned} of {n_prod_alarms} production "
                f"event(s) occurred while the affected block was under a "
                f"planned or manual stop and are <b>not counted</b> here, "
                f"because the block was intentionally offline. The table below "
                f"reflects the remaining {n_prod_alarms_genuine} genuine "
                f"production fault(s); any that fell inside a formal exclusion "
                f"window are also listed at the end of this section.</i>",
                STYLE_SMALL))
            story.append(Spacer(1, 1*mm))
        rows = []
        for _, r in faults_summary.iterrows():
            rows.append([
                r['subsystem'], r['reason'],
                str(int(r['occurrences'])),
                f"{r['total_hours']:.1f} h",
                r['blocks_affected'],
                (r['resolution'] or '—')[:38],
            ])
        story.append(_styled_table(
            ['Equipment', 'Reason', 'Occurrences',
             'Total Hours', 'Blocks Affected', 'Resolution'],
            rows,
            col_widths=[22*mm, 34*mm, 18*mm, 18*mm, 40*mm, 30*mm]
        ))
        if faults_summary['any_planned'].any():
            story.append(Paragraph(
                '<i>* affected block was under a planned or manual stop '
                '(STOPPED state, Manual/Key Stop, or exclusion window) during '
                'the event — likely not a genuine fault.</i>', STYLE_SMALL))
        story.append(Spacer(1, 4*mm))

    if not warn_summary.empty:
        story.append(Paragraph('<b>Standing warnings</b>', STYLE_BODY))
        if n_warn_persistent_planned > 0:
            story.append(Paragraph(
                f"<i>{n_warn_persistent_planned} of {n_warn_persistent} "
                f"persistent warning(s) occurred during a planned or manual "
                f"stop and are <b>not counted</b> here. The table below shows "
                f"the remaining {n_warn_persistent_genuine} warning(s).</i>",
                STYLE_SMALL))
            story.append(Spacer(1, 1*mm))
        rows = []
        for _, r in warn_summary.iterrows():
            rows.append([
                r['subsystem'], r['reason'],
                str(int(r['occurrences'])),
                f"{r['total_hours']:.1f} h",
                r['blocks_affected'],
            ])
        story.append(_styled_table(
            ['Equipment', 'Reason', 'Occurrences', 'Total Hours',
             'Blocks Affected'],
            rows,
            col_widths=[26*mm, 44*mm, 24*mm, 24*mm, 44*mm]
        ))
        if warn_summary['any_planned'].any():
            story.append(Paragraph(
                '<i>* affected block was under a planned or manual stop during '
                'the event.</i>', STYLE_SMALL))
        story.append(Spacer(1, 4*mm))

    # Events During Exclusion Windows (informational only)
    if (n_prod_alarms_excluded + n_warn_persistent_excluded) > 0:
        story.append(Paragraph(
            '<b>Events During Exclusion Windows (Informational)</b>',
            STYLE_BODY))
        story.append(Paragraph(
            'The events below occurred inside a planned availability exclusion '
            'window (e.g. scheduled maintenance). They are reported here for '
            'transparency but are <b>not counted</b> in the fault / alarm '
            'tallies above, because the equipment was intentionally taken out '
            'of service.',
            STYLE_SMALL))
        story.append(Spacer(1, 1*mm))
        excl_rows = []
        for src_key, label in (('production_dedup', 'Production'),
                                 ('warning_persistent', 'Warning')):
            src_df = alarms.get(src_key, pd.DataFrame())
            if src_df.empty or 'is_excluded' not in src_df.columns:
                continue
            sub = src_df[src_df['is_excluded']]
            for _, r in sub.head(25).iterrows():
                excl_rows.append([
                    label,
                    str(r.get('Activated', ''))[:16],
                    str(r.get('Element', ''))[:18],
                    str(r.get('Trigger name', ''))[:46],
                    (f"{r['duration_min']/60:.1f} h"
                       if pd.notna(r.get('duration_min')) else '—'),
                    str(r.get('excluded_by', ''))[:22],
                ])
        if excl_rows:
            story.append(_styled_table(
                ['Class', 'Activated', 'Element', 'Trigger',
                 'Duration', 'During'],
                excl_rows,
                col_widths=[18*mm, 28*mm, 22*mm, 60*mm, 18*mm, 32*mm]
            ))
            story.append(Spacer(1, 4*mm))

    story.append(_fig_to_image(_chart_alarm_by_class(alarm_sum), 170, 70))
    story.append(Paragraph(
        'Graph 6: Number of faults and total downtime by type of equipment.',
        STYLE_CAP))
    story.append(Spacer(1, 4*mm))

    # Trigger-level pie chart (customer-facing distribution view)
    story.append(_fig_to_image(_chart_alarm_pie_by_trigger(p_dedup_all),
                                170, 75))
    story.append(Paragraph(
        'Graph 7: Most common faults — the 12 most frequent, with the rest '
        'grouped as "Other".',
        STYLE_CAP))
    story.append(Spacer(1, 4*mm))

    # Faults table — customer-template column layout
    important = important_events
    story.append(Paragraph('<b>Faults</b>', STYLE_BODY))
    story.append(Paragraph(
        f'The faults that mattered: critical events and any fault lasting '
        f'{int(IMPORTANT_DURATION_THRESHOLD_MIN)} minutes or more. Brief '
        f'flickers, routine manual stops and events during planned maintenance '
        f'are left out.',
        STYLE_SMALL))
    story.append(Spacer(1, 1*mm))
    if important.empty:
        story.append(Paragraph(
            'No critical events during the reporting period.', STYLE_BODY))
    else:
        rows = []
        any_planned = False
        for i, (_, r) in enumerate(important.head(40).iterrows(), start=1):
            blk = '' if pd.isna(r.get('_blk')) else str(int(r['_blk']))
            if blk and bool(r.get('on_planned_stop')):
                blk += '*'; any_planned = True
            try:
                d = pd.to_datetime(r.get('Activated'))
                date_str = d.strftime('%d.%m.%Y') if pd.notna(d) else ''
            except Exception:
                date_str = ''
            rows.append([
                str(i),
                str(r.get('Trigger name', ''))[:42],
                blk,
                str(r.get('cls_subsystem', '') or ''),
                str(r.get('cls_reason', '') or ''),
                date_str,
                str(r.get('cls_resolution', '') or '—')[:40],
            ])
        story.append(_styled_table(
            ['No.', 'Fault Name', 'Block #', 'Equipment',
             'Reason', 'Date / Period', 'Resolution'],
            rows,
            col_widths=[10*mm, 38*mm, 14*mm, 22*mm, 32*mm, 22*mm, 32*mm]
        ))
        if any_planned:
            story.append(Paragraph(
                '<i>* block was under a planned or manual stop at the time of '
                'the event.</i>', STYLE_SMALL))
        if len(important) > 40:
            story.append(Paragraph(
                f'<i>Showing first 40 of {len(important)} important events.</i>',
                STYLE_SMALL))
    story.append(Spacer(1, 3*mm))

    # Alarms table (persistent warnings) — same layout minus Date column
    story.append(Paragraph('<b>Warnings</b>', STYLE_BODY))
    if warn_detail is None or warn_detail.empty:
        story.append(Paragraph('No standing warnings of note during the month.',
                                STYLE_BODY))
    else:
        rows = []
        any_planned = False
        for i, (_, r) in enumerate(warn_detail.head(25).iterrows(), start=1):
            blk = '' if pd.isna(r.get('_blk')) else str(int(r['_blk']))
            if blk and bool(r.get('on_planned_stop')):
                blk += '*'; any_planned = True
            rows.append([
                str(i),
                str(r.get('Trigger name', ''))[:42],
                blk,
                str(r.get('cls_subsystem', '') or ''),
                str(r.get('cls_reason', '') or ''),
                str(r.get('cls_resolution', '') or '—')[:40],
            ])
        story.append(_styled_table(
            ['No.', 'Fault Name', 'Block #', 'Equipment',
             'Reason', 'Resolution'],
            rows,
            col_widths=[10*mm, 42*mm, 14*mm, 24*mm, 36*mm, 44*mm]
        ))
        if any_planned:
            story.append(Paragraph(
                '<i>* block was under a planned or manual stop at the time of '
                'the event.</i>', STYLE_SMALL))
    story.append(Spacer(1, 4*mm))

    # 5.2 Major Incidents and Breakdowns (semi-automatic)
    story.append(Paragraph('<b>5.2  Major Incidents and Breakdowns</b>', STYLE_H3))
    story.append(Paragraph(
        'Summary of breakdowns, incidents and their weight affecting '
        'availability.', STYLE_BODY))
    story.append(Spacer(1, 1*mm))
    if breakdown_rows:
        if breakdown_is_auto:
            story.append(Paragraph(
                '<i>Auto-detected from the longest production faults '
                '(planned / manual stops excluded). Review and complete the '
                'temporary / final solution columns.</i>', STYLE_SMALL))
            story.append(Spacer(1, 1*mm))
        rows = []
        for i, inc in enumerate(breakdown_rows, start=1):
            dt = (f"{inc['downtime_h']:.1f} h"
                  if inc.get('downtime_h') is not None else '')
            rows.append([
                str(i),
                str(inc.get('incident', ''))[:40],
                str(inc.get('blocks', ''))[:14],
                str(inc.get('date_time', ''))[:16],
                str(inc.get('breakdown_type', ''))[:18],
                dt,
                str(inc.get('temporary_solution', ''))[:30],
                str(inc.get('final_solution', ''))[:30],
            ])
        story.append(_styled_table(
            ['No.', 'Breakdown incident', 'Block(s)', 'Date and time',
             'Breakdown type', 'Downtime', 'Temporary solution',
             'Final solution'],
            rows,
            col_widths=[8*mm, 32*mm, 16*mm, 22*mm, 22*mm, 16*mm, 27*mm, 27*mm]
        ))
    else:
        story.append(Paragraph(
            'No breakdowns in the reported period.', STYLE_BODY))
        story.append(Spacer(1, 2*mm))
        # Keep the longest-events table as additional informational content
        if not alarm_sum['longest'].empty:
            story.append(Paragraph(
                '<i>Longest events (informational):</i>', STYLE_SMALL))
            rows = []
            for _, r in alarm_sum['longest'].iterrows():
                trig = str(r['Trigger name'])[:50]
                if bool(r.get('is_excluded', False)):
                    excl_lbl = str(r.get('excluded_by', '') or 'exclusion')
                    trig = f"{trig}  (during {excl_lbl})"
                rows.append([
                    str(r['Activated'])[:16],
                    str(r['Element'])[:20],
                    trig,
                    f"{r['duration_min']/60:.1f} h" if pd.notna(r['duration_min']) else '—',
                ])
            story.append(_styled_table(
                ['Activated', 'Element', 'Trigger', 'Duration'], rows,
                col_widths=[34*mm, 30*mm, 80*mm, 26*mm]
            ))
    story.append(_hr())

    # ── 6. SITE OPERATION ────────────────────────────────────────────────
    story.append(_section('6.  Site Operation and Facility Management', ''))
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph('<b>Incidents affecting safety</b>', STYLE_H3))
    if safety_incidents:
        rows = [[s.get('incident',''), s.get('equipment_loss','-'),
                  s.get('weight',''), s.get('countermeasure','')]
                 for s in safety_incidents]
        story.append(_styled_table(
            ['Incident', 'Equipment Loss', 'Weight', 'Countermeasure'],
            rows, col_widths=[60*mm, 35*mm, 35*mm, 40*mm]
        ))
    else:
        story.append(Paragraph('No safety incidents in the reporting period.',
                                STYLE_BODY))
    story.append(Spacer(1, 4*mm))
    story.append(Paragraph('<b>Site visits and inspections</b>', STYLE_H3))
    if site_visits:
        for v in site_visits: story.append(Paragraph(f'•  {v}', STYLE_BODY))
    else:
        story.append(Paragraph('No site visits recorded.', STYLE_BODY))
    story.append(_hr())

    # ── 7. CONCLUSIONS ───────────────────────────────────────────────────
    story.append(_section('7.  Conclusions and Recommendations', ''))
    story.append(Spacer(1, 3*mm))
    auto = [
        (f"The site averaged a round-trip efficiency of <b>{fleet_rte:.2f}%</b> "
         f"({rte_status} the 85% target), with an average availability of "
         f"<b>{fleet_availability_container:.2f}%</b>."),
        (f"Across {n_blocks} blocks the site discharged "
         f"<b>{total_discharge_mwh:,.1f} MWh</b> from "
         f"<b>{total_charge_mwh:,.1f} MWh</b> of charging — about "
         f"<b>{total_efc_fleet:.0f} full cycles</b> in total "
         f"(roughly {avg_efc_per_block:.1f} per block)."),
        (f"<b>{n_prod_alarms_genuine:,}</b> faults that affected production were "
         f"recorded, alongside {n_warn_persistent_genuine:,} standing warnings "
         f"(brief, self-clearing alarms are not counted; faults and alarms on "
         f"blocks under a planned or manual stop are also excluded)."),
    ]
    if n_anomalies:
        auto.append(f"<b>{n_anomalies}</b> block(s) performed below the rest of "
                     f"the site and are worth keeping an eye on.")
    if days_excluded > 0:
        auto.append(f"On average <b>{days_excluded:.1f}</b> day(s) per block had "
                     f"no usable charge/discharge data and were left out of the "
                     f"figures.")
    for i, t in enumerate(auto, 1):
        story.append(Paragraph(f'{i}.  {t}', STYLE_BODY))
        story.append(Spacer(1, 2*mm))
    if recommendations:
        story.append(Spacer(1, 3*mm))
        story.append(Paragraph('<b>Recommendations & Mitigation Strategies</b>',
                                STYLE_H3))
        for r in recommendations: story.append(Paragraph(f'•  {r}', STYLE_BODY))
    if planned_next_period:
        story.append(Spacer(1, 3*mm))
        story.append(Paragraph('<b>Planned Activities for Next Reporting Period</b>',
                                STYLE_H3))
        for p in planned_next_period: story.append(Paragraph(f'•  {p}', STYLE_BODY))
    story.append(_hr())

    # ── 8. ANNEXES ───────────────────────────────────────────────────────
    story.append(_section('8.  List of Annexes', ''))
    story.append(Spacer(1, 3*mm))
    for line in [
        'Annex 1: Daily performance per block for the full month (spreadsheet)',
        'Annex 2: List of fault types and how they were classified',
        'Annex 3: Days set aside as incomplete or unusable',
        'Annex 4: Blocks performing below the site average',
    ]:
        story.append(Paragraph(f'•  {line}', STYLE_BODY))
    if annexes:
        for a in annexes: story.append(Paragraph(f'•  {a}', STYLE_BODY))

    story.append(Spacer(1, 6*mm))
    story.append(Paragraph(
        f'Generated by BESS Tracker — Tashkent module  |  '
        f'{datetime.now().strftime("%Y-%m-%d %H:%M")}',
        STYLE_SMALL))

    doc.build(story)
    log(f"PDF saved: {output_path}")
    pdf_path = output_path

    docx_path = None
    if output_format in ('docx', 'both'):
        docx_path = output_path.rsplit('.', 1)[0] + '.docx'
        log("Building DOCX...")
        try:
            _build_tashkent_docx(docx_path, locals())
            log(f"DOCX saved: {docx_path}")
        except Exception as e:
            log(f"Note: DOCX build failed: {e}")

    try:
        path = save_history_record(current_month_record)
        log(f"Saved month record to {path}")
    except Exception as e:
        log(f"Note: could not save history record: {e}")

    if output_format == 'docx' and docx_path:
        # User asked for docx only — return docx path and skip PDF? We
        # actually still produced the PDF (simpler), so return both.
        return [pdf_path, docx_path]
    if output_format == 'both' and docx_path:
        return [pdf_path, docx_path]
    return pdf_path


def _build_tashkent_docx(output_path, _ctx):
    """Render the same 8-section Tashkent report as a Word .docx file."""
    from services.docx_renderer import (
        make_doc, add_cover, add_section_banner, add_heading, add_paragraph,
        add_kpi_row, add_styled_table, add_image_from_fig, add_caption,
        add_hr, save_doc,
    )
    import pandas as _pd
    g = _ctx
    site_name = g['site_name']; report_month = g['report_month']
    n_blocks = g['n_blocks']; period_str = g['period_str']
    project_details = g.get('project_details'); cycles_accum_avg = g.get('cycles_accum_avg')
    scheduled_unavail_hours = g.get('scheduled_unavail_hours', 0.0)
    avg_soc_pct = g['avg_soc_pct']; avg_soh_pct = g['avg_soh_pct']
    fleet_rte = g['fleet_rte']
    fleet_availability_container = g['fleet_availability_container']
    total_charge_mwh = g['total_charge_mwh']; total_discharge_mwh = g['total_discharge_mwh']
    total_efc_fleet = g['total_efc_fleet']; avg_efc_per_block = g['avg_efc_per_block']
    days_excluded = g['days_excluded']
    n_prod_alarms = g['n_prod_alarms']; n_warn_persistent = g['n_warn_persistent']
    n_warn_transient = g['n_warn_transient']; n_anomalies = g['n_anomalies']
    n_prod_alarms_excluded     = g.get('n_prod_alarms_excluded', 0)
    n_warn_persistent_excluded = g.get('n_warn_persistent_excluded', 0)
    n_prod_alarms_planned     = g.get('n_prod_alarms_planned', 0)
    n_warn_persistent_planned = g.get('n_warn_persistent_planned', 0)
    n_prod_alarms_genuine     = g.get('n_prod_alarms_genuine', n_prod_alarms)
    n_warn_persistent_genuine = g.get('n_warn_persistent_genuine', n_warn_persistent)
    plant_avail = g.get('plant_avail'); daily_kpi = g['daily_kpi']
    contractual_avail = g.get('contractual_avail')
    anomalies = g['anomalies']; alarms = g['alarms']; alarm_sum = g['alarm_sum']
    cls_summary = g['cls_summary']; monthly_compare = g['monthly_compare']
    socsoh = g['socsoh']
    pm_activities = g.get('pm_activities'); cm_activities = g.get('cm_activities')
    safety_incidents = g.get('safety_incidents'); site_visits = g.get('site_visits')
    recommendations = g.get('recommendations'); planned_next_period = g.get('planned_next_period')
    annexes = g.get('annexes')
    rte_status = 'above' if fleet_rte >= 85 else 'below'
    avail_status = 'above' if fleet_availability_container >= 95 else 'below'

    doc = make_doc()
    add_cover(doc, report_month, site_name, n_blocks, period_str)

    # 1. Project Details
    add_section_banner(doc, '1.  Project Details')
    rows = [['Project name', site_name],
            ['Reporting period', period_str],
            ['Number of blocks', f'{n_blocks} blocks']]
    if project_details:
        for k, v in project_details.items():
            rows.append([str(k), str(v)])
    add_styled_table(doc, ['Parameter', 'Value'], rows)
    add_hr(doc)

    # 2. Summary
    add_section_banner(doc, '2.  Summary')
    sm = (f"During {period_str}, the {site_name} site ({n_blocks} blocks) "
           f"discharged <b>{total_discharge_mwh:,.1f} MWh</b> from "
           f"<b>{total_charge_mwh:,.1f} MWh</b> of charging. The site's average "
           f"round-trip efficiency was <b>{fleet_rte:.2f}%</b> "
           f"({rte_status} the 85% target). ")
    if contractual_avail and contractual_avail.get('availability_pct') is not None:
        sm += (f"BESS availability for the period was "
                f"<b>{contractual_avail['availability_pct']:.2f}%</b> "
                f"(contractual method — capacity-weighted, counting only "
                f"genuine fault-shutdown time). ")
    if plant_avail and plant_avail.get('plant_availability_pct') is not None:
        sm += (f"Allowing for spare capacity the site met its contracted output "
                f"<b>{plant_avail['plant_availability_pct']:.2f}%</b> of the time, "
                f"and the simple average across blocks was "
                f"<b>{fleet_availability_container:.2f}%</b>. ")
        if plant_avail.get('contractual_plant_capacity_mw'):
            sm += (f"Contracted capacity: "
                    f"<b>{plant_avail['contractual_plant_capacity_mw']:.1f} MW</b>. ")
    elif not contractual_avail:
        sm += (f"Average availability across blocks was "
                f"<b>{fleet_availability_container:.2f}%</b> "
                f"({avail_status} the 95% target). ")
    sm += (f"Average state of charge was <b>{avg_soc_pct:.2f}%</b> and average "
            f"state of health (at month end) <b>{avg_soh_pct:.2f}%</b>. The "
            f"blocks completed about <b>{total_efc_fleet:.0f}</b> full charge / "
            f"discharge cycles in total (roughly {avg_efc_per_block:.1f} each). "
            f"On average <b>{days_excluded:.1f}</b> day(s) per block had no "
            f"usable data and were left out of the figures.")
    add_paragraph(doc, sm)
    add_hr(doc)

    # 3. Services Provision
    add_section_banner(doc, '3.  Services Provision')
    add_heading(doc, '3.1  Preventative Maintenance (PM)', level=3)
    if pm_activities:
        for act in pm_activities: add_paragraph(doc, f'•  {act}')
    else:
        add_paragraph(doc, 'No PM activities in the reporting period.')
    add_heading(doc, '3.2  Corrective Maintenance', level=3)
    if cm_activities:
        for act in cm_activities: add_paragraph(doc, f'•  {act}')
    elif cls_summary is not None and not cls_summary.empty:
        add_paragraph(doc,
            'Automated summary based on alarm classification — recurring '
            'corrective items observed:')
        cm_rows = [[r['cls_reason'], str(int(r['n_events'])),
                     r['cls_resolution'] or '—']
                    for _, r in cls_summary.head(7).iterrows()]
        add_styled_table(doc, ['Item', 'Occurrences', 'Resolution applied'], cm_rows)
    add_hr(doc)

    # 4. Plant Performance
    add_section_banner(doc, "4.  Plant's Performance")
    add_heading(doc, '4.1  Plant Performance Data', level=3)
    add_styled_table(doc, ['No', 'Item', 'Total (MWh)'], [
        ['1', 'Imported Energy (Charge)',  f'{total_charge_mwh:,.2f}'],
        ['2', 'Exported Energy (Discharge)', f'{total_discharge_mwh:,.2f}']])
    add_image_from_fig(doc, _chart_single_daily_bar(
        daily_kpi, 'charge_kwh', 'Daily Imported Power', '#0071E3'))
    add_caption(doc, 'Graph 1: Daily Imported Power (MWh)')
    add_image_from_fig(doc, _chart_single_daily_bar(
        daily_kpi, 'discharge_kwh', 'Daily Exported Power', '#AF52DE'))
    add_caption(doc, 'Graph 2: Daily Exported Power (MWh)')

    add_heading(doc, 'Number of Cycles', level=3)
    yt = float(g.get('yearly_cycle_target') or 365.0)
    accum_lifetime = cycles_accum_avg if cycles_accum_avg else avg_efc_per_block
    annual_accum = avg_efc_per_block
    history_records_local = g.get('history_records')
    try:
        if history_records_local:
            recent = sorted(
                [r for r in history_records_local if r.get('cycles_total') is not None],
                key=lambda r: str(r.get('month','')))[-11:]
            annual_accum = sum(float(r.get('cycles_total') or 0)
                                for r in recent) + avg_efc_per_block
    except Exception:
        pass
    add_styled_table(doc,
        ['No', 'Item', 'Total', f'% of yearly cycles ({yt:.0f})'],
        [['1', 'Number of Cycles in Reported Month',
          f'{avg_efc_per_block:.1f}', f'{avg_efc_per_block/yt*100:.2f}%'],
         ['2', 'Accumulative Number of Cycles in one Year',
          f'{annual_accum:.1f}', f'{annual_accum/yt*100:.2f}%'],
         ['3', 'Accumulative Number of Cycles',
          f'{accum_lifetime:.1f}', f'{accum_lifetime/yt*100:.2f}%']])

    # Cycles block-wise — picture-style chunked layout (10 blocks per table)
    cycles_snap_local = g.get('cycles_snap')
    if cycles_snap_local is not None and not cycles_snap_local.empty:
        month_name_only = g.get('report_month', '').split(' ')[0] or 'period'
        daily_kpi_local = g.get('daily_kpi')
        per_block_energy_kwh = {}
        if daily_kpi_local is not None and not daily_kpi_local.empty:
            per_block_energy_kwh = daily_kpi_local.groupby('block').agg(
                charge_kwh=('charge_kwh', 'sum'),
                discharge_kwh=('discharge_kwh', 'sum'),
            ).to_dict('index')
        add_heading(doc, 'Cycles per block', level=3)
        add_paragraph(doc,
            'For each block: the total number of charge / discharge cycles it '
            'has accumulated to date, and how many of those were completed this '
            f'month ({month_name_only}). Cycle figures are the average across '
            'the battery modules in the block; the energy figures are the '
            'block\'s total charging and discharging for the month.',
            italic=True)
        cdf = cycles_snap_local.sort_values('block_id').reset_index(drop=True)
        chunk_size = 10
        def _fmt_d(v):
            return f'{v:.2f}' if _pd.notna(v) else '—'
        def _fmt_e(v):
            return f'{v:.0f}' if (v is not None and _pd.notna(v)) else '—'
        for start in range(0, len(cdf), chunk_size):
            chunk = cdf.iloc[start:start+chunk_size]
            headers = [''] + [f'Block {int(r.block_id)}' for r in chunk.itertuples()]
            row_acc = ['Accumulative cycles'] + [
                _fmt_d(r.cycle_end) for r in chunk.itertuples()]
            row_dlt = [f'Cycles in {month_name_only}'] + [
                _fmt_d(r.cycle_delta) for r in chunk.itertuples()]
            rows_to_emit = [row_acc, row_dlt]
            _proj_factor = g.get('cycle_projection_factor')
            if _proj_factor:
                row_prj = ['Projected Cycles for the Month'] + [
                    _fmt_d(r.cycle_delta * _proj_factor
                           if _pd.notna(r.cycle_delta) else float('nan'))
                    for r in chunk.itertuples()]
                rows_to_emit.append(row_prj)
            if per_block_energy_kwh:
                row_chg = ['Charged Energy (kWh)']
                row_dis = ['Discharged Energy (kWh)']
                for r in chunk.itertuples():
                    b = int(r.block_id)
                    e = per_block_energy_kwh.get(b, {})
                    row_chg.append(_fmt_e(e.get('charge_kwh')))
                    row_dis.append(_fmt_e(e.get('discharge_kwh')))
                rows_to_emit.extend([row_chg, row_dis])
            add_styled_table(doc, headers, rows_to_emit)
        # Blocks where the snapshot couldn't fill EVERY field (e.g. no comms
        # at the start of the period and no later non-zero in the first-day
        # file). Surface them as a footnote so the customer knows the gap
        # was a data-availability issue, not a missing block.
        partial = cycles_snap_local[
            cycles_snap_local[['cycle_begin','cycle_end','cycle_delta']].isna().any(axis=1)
        ]
        if not partial.empty:
            ids = ', '.join(str(int(b)) for b in partial['block_id'])
            add_paragraph(doc,
                f'Note: block(s) {ids} had no reading at the start or end of the '
                f'month — usually because of maintenance or a communication gap '
                f'on those days. The nearest available reading was used where '
                f'possible; a "—" means the gap could not be filled.',
                italic=True)

        _rested = g.get('rested_blocks') or set()
        if _rested:
            _rb = ', '.join(str(b) for b in sorted(_rested))
            add_paragraph(doc,
                f'Note: block(s) {_rb} were intentionally kept out of operation '
                f'this month to balance the number of cycles across the fleet — '
                f'keeping all blocks near an equal cycle count and within the '
                f'annual 365-cycle budget. Their lower cycle count and RTE this '
                f'month are deliberate and do not represent a fault or reduced '
                f'availability.',
                italic=True)

    if contractual_avail and contractual_avail.get('availability_pct') is not None:
        ca = contractual_avail
        _refined = (ca.get('method') == 'pcs_unit')
        add_heading(doc, 'Contractual Availability', level=3)
        add_paragraph(doc,
            'This is the contractual measure of BESS availability. A unit '
            'counts as unavailable only for the time it spent in a genuine '
            'fault shutdown — standby, startup, manual stops and data gaps do '
            'not count against it — and every outage is weighted by the '
            'nameplate capacity taken out of service. ' +
            ('Resolution is at the PCS-unit level: during a fault we count only '
             'the converter units that actually stopped (the charge/discharge '
             'data confirms units that kept running), so a partly-failed LC '
             'loses only the capacity that genuinely went off-line.'
             if _refined else
             'Resolution is at the LC level (the whole LC is counted down for '
             'its fault time).') +
            (' Per-unit fault flags from the PCS fault-status export are also '
             'taken into account, so a fault on a single converter unit is '
             'captured even when its LC reads healthy.'
             if ca.get('used_unit_fault') else ''))
        add_paragraph(doc,
            f"Availability = 1 − (fault-shutdown time × capacity out "
            f"of service) ÷ (hours in period × installed nameplate) "
            f"= 1 − ({ca['down_unit_hours']:,.1f} {ca['unit_label']}-h × "
            f"{ca['unit_capacity_kwh']/1000:.3f} MWh) ÷ "
            f"({ca['total_hours']:,.0f} h × {ca['installed_kwh']/1000:,.1f} "
            f"MWh) = {ca['availability_pct']:.2f}%.",
            italic=True)
        _excl_note = (
            f" Planned maintenance / restoration time inside exclusion windows "
            f"(e.g. post-service 'system not ready' warm-up) is not counted: "
            f"{ca['excluded_unit_hours']:,.1f} {ca['unit_label']}-h excluded."
            if ca.get('excluded_unit_hours', 0) > 0 else "")
        _excl_note += (
            f" Operator-reported unavailability adds "
            f"{ca['manual_unit_hours']:,.1f} {ca['unit_label']}-h to the "
            f"fault-shutdown time."
            if ca.get('manual_unit_hours', 0) > 0 else "")
        add_paragraph(doc,
            f"Hierarchy: 1 block = 2 LC = 4 PCS units = 4 BESS containers; "
            f"1 PCS unit = 1 container = 2,752 kWh. Installed nameplate across "
            f"{ca['n_units']} {ca['unit_label']}s = {ca['installed_kwh']/1000:,.1f} "
            f"MWh. Fault-shutdown time is the total across all units.{_excl_note}",
            italic=True)

    if plant_avail:
        add_heading(doc, 'Availability Hours (plant-level redundancy method)', level=3)
        add_paragraph(doc,
            'These are supplementary views of availability; the contractual '
            'figure above is the headline SLA number. '
            'This measures availability for the plant as a whole, rather than '
            'counting every individual block-hour lost. A block is treated as '
            'available at any moment only when both of its containers are in a '
            'working state. Because the plant carries spare (redundant) '
            'capacity, it is considered available for as long as the combined '
            'output of the blocks that are up stays at or above the agreed '
            'threshold — the contracted capacity where one is defined, '
            'otherwise a set share of nameplate capacity (shown in the table '
            'below). The plant is counted as unavailable only during the time '
            'its available capacity drops below that threshold; '
            'planned-maintenance hours are removed from the clock so they do '
            'not count against the figure. The "plant-level availability" row '
            'applies this redundancy credit, while "container-level '
            'availability" is the stricter view that adds up every block-hour '
            'of downtime with no credit for spare capacity — so it is always '
            'the lower of the two.')
        sched = plant_avail['scheduled_hours']
        plant_out = plant_avail.get('plant_outage_hours')
        cont_out  = plant_avail['container_outage_hours']
        a_rows = [
            ['Scheduled hours', f'{sched:,.1f} h'],
            ['Scheduled unavailability (PM)', f'{scheduled_unavail_hours:,.1f} h'],
            ['Unscheduled outage (container-aggregate)',
                f'{cont_out:,.1f} h ({cont_out/sched*100:.2f}%)'],
        ]
        if plant_out is not None:
            a_rows.append(['Plant-level outage (redundancy adjusted)',
                f'{plant_out:,.1f} h ({plant_out/sched*100:.2f}%)'])
        else:
            a_rows.append(['Plant-level outage',
                'n/a (plant capacity not configured)'])
        if plant_avail.get('threshold_mw'):
            label = 'Contractual threshold' if plant_avail.get('contractual_plant_capacity_mw') else 'Redundancy threshold'
            a_rows.append([label, f"{plant_avail['threshold_mw']:.1f} MW"])
        if plant_avail.get('plant_availability_pct') is not None:
            a_rows.append(['Plant-level availability',
                f"{plant_avail['plant_availability_pct']:.2f}%"])
        a_rows.append(['Container-level availability',
            f"{fleet_availability_container:.2f}%"])
        if plant_avail.get('excluded_effective', 0) > 0:
            a_rows.append([
                'Excluded hours (applied to availability)',
                f"{plant_avail['excluded_effective']:.1f} h "
                f"(of {plant_avail.get('excluded_hours', 0):.1f} h requested)"
            ])
        add_styled_table(doc, ['Item', 'Value'], a_rows)

    # ── Applied Exclusions (only when at least one was provided) ─────────
    excl_result_local = g.get('excl_result')
    exclusions_local  = g.get('exclusions') or []
    if exclusions_local and excl_result_local and excl_result_local.get('events'):
        add_heading(doc, 'Scheduled Unavailability', level=3)
        lead, bullets = format_exclusion_narrative(exclusions_local)
        if lead:
            add_paragraph(doc, lead)
            for b in bullets:
                add_paragraph(doc, f"•  {b}")
        add_paragraph(doc, 'Applied Exclusions (Detail):', bold=True)
        total_excl = excl_result_local['excluded_hours']
        applied    = (plant_avail.get('excluded_effective', 0.0)
                       if plant_avail else 0.0)
        add_paragraph(
            doc,
            f"During the period there were {len(excl_result_local['events'])} "
            f"planned exclusion period(s) — agreed downtime that does not count "
            f"against availability. Together they added up to "
            f"{total_excl:,.1f} h (counting how many blocks each one affected). "
            f"Of that, only {applied:,.1f} h were removed from the availability "
            f"figure, because we set aside only the time the plant was genuinely "
            f"down for this work — never more than was actually lost."
        )
        brk_rows = [
            [t, f"{h:.1f} h",
             f"{(h/total_excl*100 if total_excl else 0):.1f}%"]
            for t, h in excl_result_local['breakdown'].items() if h > 0
        ]
        if brk_rows:
            add_styled_table(doc,
                ['Exclusion Type', 'Weighted Hours', '% of Excluded'],
                brk_rows)
        detail_rows = []
        for ev in excl_result_local['events']:
            exc = ev['exclusion']
            t_from = exc.get('time_from', '00:00') or '00:00'
            t_to   = exc.get('time_to',   '23:59') or '23:59'
            blk_val = (exc.get('affected_blocks') or '').strip()
            blk_disp = blk_val if blk_val else 'All blocks'
            detail_rows.append([
                exc.get('exclusion_type', ''),
                f"{exc.get('date_from','')} {t_from}",
                f"{exc.get('date_to','')} {t_to}",
                f"{ev['hours_in_period']:.1f}",
                blk_disp,
                exc.get('description', ''),
            ])
        add_styled_table(doc,
            ['Type', 'From', 'To', 'Hours', 'Blocks', 'Description'],
            detail_rows)

    add_heading(doc, '4.2  Key Performance Indicators', level=3)
    add_kpi_row(doc, [
        (f"{avg_soc_pct:.2f}%", 'Average state of charge', '#0071E3'),
        (f"{avg_soh_pct:.2f}%", 'Average state of health', '#34C759'),
        (f"{fleet_rte:.2f}%",   'Round-trip efficiency',   '#FF9500'),
    ])
    add_paragraph(doc,
        f'Round-trip efficiency = total energy discharged ÷ total energy '
        f'charged over the month = {total_discharge_mwh:,.1f} MWh ÷ '
        f'{total_charge_mwh:,.1f} MWh = {fleet_rte:.2f}%.',
        size=8, italic=True)
    add_image_from_fig(doc, _chart_soc_trend(socsoh))
    add_caption(doc, 'Graph 3: Site-wide average SOC trend (daily)')

    add_heading(doc, '4.3  Monthly Performance Comparison', level=3)
    if not monthly_compare.empty and len(monthly_compare) > 1:
        cmp_rows = []
        for _, r in monthly_compare.iterrows():
            cmp_rows.append([
                str(r['month']),
                f"{r['avg_soc_pct']:.1f}%" if _pd.notna(r['avg_soc_pct']) else '—',
                f"{r['avg_soh_pct']:.1f}%" if _pd.notna(r['avg_soh_pct']) else '—',
                f"{r['rte_pct']:.1f}%"     if _pd.notna(r['rte_pct']) else '—',
                f"{r['cycles']:.1f}"        if _pd.notna(r['cycles']) else '—',
                f"{r['discharge_mwh']:,.0f}" if _pd.notna(r['discharge_mwh']) else '—',
                f"{r['charge_mwh']:,.0f}"    if _pd.notna(r['charge_mwh']) else '—',
            ])
        add_styled_table(doc,
            ['Month', 'SOC', 'SOH', 'RTE', 'Cycles',
             'Discharge (MWh)', 'Charge (MWh)'], cmp_rows)

    add_heading(doc, '4.4  System Availability', level=3)
    add_image_from_fig(doc, _chart_availability_heatmap_categorical(
        g.get('container_day_status'),
        exclusions=g.get('exclusions'),
        fig_width=18), width_inches=7.2)
    add_caption(doc,
        'Graph 4: Daily availability per container. Green = available: the '
        'block completed at least one full charge/discharge cycle that day, or '
        'was on standby and ready the whole day; orange = available but with '
        'alarms; yellow = partially available — operated only part of the day '
        'and did not complete a full cycle; red = did not operate at all and '
        'faults were recorded. Hatched = planned exclusion window.' +
        (' Unavailability reported by the operator (see 4.4.1) is also '
         'reflected here.' if g.get('manual_unavailability') else ''))

    # 4.4.1 Unavailability reasons — only red (did-not-operate) days, kept compact
    unavail_reasons = g.get('unavail_reasons')
    if unavail_reasons is not None and not unavail_reasons.empty:
        _MAX_REASON_ROWS = 20
        ur = unavail_reasons.sort_values('downtime_h', ascending=False)
        add_heading(doc, '4.4.1  Unavailability Reasons', level=3)
        add_paragraph(doc,
            'Containers that were in a genuine fault shutdown are listed here, '
            'with the time lost and the dominant fault recorded during the '
            'outage. Planned maintenance and restoration (including the '
            'post-service warm-up) are excluded, so this reflects only real, '
            'unplanned downtime.')
        rows = []
        any_manual = False
        for _, r in ur.head(_MAX_REASON_ROWS).iterrows():
            d_from = r['date_from'].strftime('%d-%b')
            d_to   = r['date_to'].strftime('%d-%b')
            period = d_from if r['n_days'] == 1 else f"{d_from} - {d_to}"
            cause  = r['cause'] or 'Unclassified'
            if r['severity']:
                cause = f"{cause} ({r['severity']})"
            if bool(r.get('manual', False)):
                cause += ' *'; any_manual = True
            lc_val = r['container_id']
            cont = (f"Block {int(r['block_id'])} - LC{int(lc_val)}"
                    if _pd.notna(lc_val) else f"Block {int(r['block_id'])}")
            ev = r['fault_events']
            rows.append([
                cont,
                period, str(int(r['n_days'])), cause,
                str(r['subsystem'] or '—'),
                (str(int(ev)) if _pd.notna(ev) else '—'),
                f"{r['downtime_h']:.1f} h",
            ])
        add_styled_table(doc,
            ['Container', 'Period', 'Days', 'Dominant Cause',
             'Subsystem', 'Events', 'Downtime'], rows)
        if any_manual:
            add_caption(doc, '* entered manually by the operator.')
        if len(ur) > _MAX_REASON_ROWS:
            add_caption(doc,
                f'Showing the {_MAX_REASON_ROWS} longest of {len(ur)} '
                f'downtime incidents.')

    add_image_from_fig(doc, _chart_daily_rte_per_block_t(daily_kpi))
    add_caption(doc, 'Graph 5: Daily round-trip efficiency. Light lines are '
                     'individual blocks, the dark line is the site average.')
    add_caption(doc,
        'The daily values above include only days with a full charge / discharge '
        'cycle, so the chart stays readable. The headline efficiency figure is '
        'based on the whole month\'s charging and discharging together, which '
        'evens out the day-to-day swings (a single cycle often runs across '
        'midnight) and is the dependable number.')
    if not anomalies.empty:
        flagged = anomalies[anomalies['anomaly_flag']].sort_values('rte_z')
        if not flagged.empty:
            add_paragraph(doc,
                f'<b>Blocks performing below the rest of the site</b> '
                f'({len(flagged)} block(s) noticeably below both the 85% target '
                f'and the site average):')
            rows = [[f"Block {int(r['block'])}",
                      f"{r['avg_rte']:.2f}%",
                      f"{r['avg_throughput']/1000:,.1f} MWh",
                      (f"{r['avg_availability']:.2f}%" if _pd.notna(r['avg_availability']) else '—'),
                      str(int(r['total_alarms'])),
                      str(r.get('likely_cause', '') or '')]
                     for _, r in flagged.iterrows()]
            add_styled_table(doc,
                ['Block', 'Round-trip efficiency', 'Energy per day',
                 'Availability', 'Faults', 'Likely cause'], rows)

    # Round-trip efficiency — a short, honest note (daily readings are noisy on
    # this data; the monthly figure is the reliable one).
    _n_below = g.get('n_blocks_below_target', 0)
    if _n_below:
        add_paragraph(doc,
            f'{_n_below} block(s) finished the month with an average round-trip '
            f'efficiency below the 85% target — these are listed above. '
            f'Individual daily readings are noisy (a charge / discharge cycle '
            f'often runs across midnight and the daily energy counters do not '
            f'line up exactly with it), so efficiency is best judged on the '
            f'monthly figure.')
    else:
        add_paragraph(doc,
            f'Every block met the 85% round-trip efficiency target over the month '
            f'(site average <b>{fleet_rte:.1f}%</b>). Some individual daily '
            f'readings fall below 85%, but that reflects where the calendar day '
            f'falls within a charge / discharge cycle and how the daily energy '
            f'counters line up — not a real loss of efficiency. Efficiency is '
            f'read from the monthly figure, which balances all of the month\'s '
            f'charging and discharging.')
    add_hr(doc)

    # 5. System Operation
    add_section_banner(doc, '5.  System Operation')
    add_heading(doc, '5.1  Summary of Faults and Alarms', level=3)
    p_dedup_all = alarms.get('production_dedup', _pd.DataFrame())
    p_dedup = (p_dedup_all[~p_dedup_all.get('is_excluded', False)]
                if not p_dedup_all.empty and 'is_excluded' in p_dedup_all.columns
                else p_dedup_all)
    faults_summary = g.get('faults_summary', _pd.DataFrame())
    warn_summary   = g.get('warn_summary', _pd.DataFrame())
    if faults_summary is not None and not faults_summary.empty:
        add_paragraph(doc, 'Faults that affected production', bold=True)
        if n_prod_alarms_planned > 0:
            add_paragraph(doc,
                f"{n_prod_alarms_planned} of {n_prod_alarms} production "
                f"event(s) occurred while the affected block was under a "
                f"planned or manual stop and are not counted here, because the "
                f"block was intentionally offline. The table reflects the "
                f"remaining {n_prod_alarms_genuine} genuine production fault(s); "
                f"any inside a formal exclusion window are also listed below.",
                italic=True)
        rows = [[r['subsystem'], r['reason'], str(int(r['occurrences'])),
                  f"{r['total_hours']:.1f} h", r['blocks_affected'],
                  (r['resolution'] or '—')[:50]]
                 for _, r in faults_summary.iterrows()]
        add_styled_table(doc,
            ['Equipment', 'Reason', 'Occurrences', 'Hours',
             'Blocks Affected', 'Resolution'], rows)
        if faults_summary['any_planned'].any():
            add_caption(doc,
                '* affected block was under a planned or manual stop (STOPPED '
                'state, Manual/Key Stop, or exclusion window) during the event '
                '— likely not a genuine fault.')
    if warn_summary is not None and not warn_summary.empty:
        add_paragraph(doc, 'Standing warnings', bold=True)
        if n_warn_persistent_planned > 0:
            add_paragraph(doc,
                f"{n_warn_persistent_planned} of {n_warn_persistent} "
                f"persistent warning(s) occurred during a planned or manual "
                f"stop and are not counted here. The table shows the remaining "
                f"{n_warn_persistent_genuine} warning(s).",
                italic=True)
        rows = [[r['subsystem'], r['reason'], str(int(r['occurrences'])),
                  f"{r['total_hours']:.1f} h", r['blocks_affected']]
                 for _, r in warn_summary.iterrows()]
        add_styled_table(doc,
            ['Equipment', 'Reason', 'Occurrences', 'Hours',
             'Blocks Affected'], rows)
        if warn_summary['any_planned'].any():
            add_caption(doc,
                '* affected block was under a planned or manual stop during '
                'the event.')

    # Events During Exclusion Windows (informational)
    if (n_prod_alarms_excluded + n_warn_persistent_excluded) > 0:
        add_paragraph(doc,
            'Events During Exclusion Windows (Informational)', bold=True)
        add_paragraph(doc,
            'The events below occurred inside a planned availability exclusion '
            'window (e.g. scheduled maintenance). They are reported here for '
            'transparency but are NOT counted in the fault / alarm tallies '
            'above, because the equipment was intentionally taken out of '
            'service.',
            italic=True)
        excl_rows = []
        for src_key, label in (('production_dedup', 'Production'),
                                 ('warning_persistent', 'Warning')):
            src_df = alarms.get(src_key, _pd.DataFrame())
            if src_df.empty or 'is_excluded' not in src_df.columns:
                continue
            sub = src_df[src_df['is_excluded']]
            for _, r in sub.head(25).iterrows():
                excl_rows.append([
                    label,
                    str(r.get('Activated', ''))[:16],
                    str(r.get('Element', ''))[:18],
                    str(r.get('Trigger name', ''))[:46],
                    (f"{r['duration_min']/60:.1f} h"
                       if _pd.notna(r.get('duration_min')) else '—'),
                    str(r.get('excluded_by', ''))[:22],
                ])
        if excl_rows:
            add_styled_table(doc,
                ['Class', 'Activated', 'Element', 'Trigger', 'Duration', 'During'],
                excl_rows)

    add_image_from_fig(doc, _chart_alarm_by_class(alarm_sum))
    add_caption(doc, 'Graph 6: Number of faults and total downtime by type of equipment')

    # Trigger-level pie chart
    add_image_from_fig(doc, _chart_alarm_pie_by_trigger(p_dedup_all))
    add_caption(doc, 'Graph 7: Most common faults (12 most frequent, rest grouped as Other)')

    # Faults table — customer-template layout
    add_paragraph(doc, 'Faults', bold=True)
    add_paragraph(doc,
        f'The faults that mattered: critical events and any fault lasting '
        f'{int(IMPORTANT_DURATION_THRESHOLD_MIN)} minutes or more. Brief '
        f'flickers, routine manual stops and events during planned maintenance '
        f'are left out.', italic=True)
    important = g.get('important_events', _pd.DataFrame())
    if important is None or important.empty:
        add_paragraph(doc, 'No critical events during the reporting period.')
    else:
        rows = []
        any_planned = False
        for i, (_, r) in enumerate(important.head(40).iterrows(), start=1):
            blk = '' if _pd.isna(r.get('_blk')) else str(int(r['_blk']))
            if blk and bool(r.get('on_planned_stop')):
                blk += '*'; any_planned = True
            try:
                d = _pd.to_datetime(r.get('Activated'))
                date_str = d.strftime('%d.%m.%Y') if _pd.notna(d) else ''
            except Exception:
                date_str = ''
            rows.append([
                str(i),
                str(r.get('Trigger name', ''))[:42],
                blk,
                str(r.get('cls_subsystem', '') or ''),
                str(r.get('cls_reason', '') or ''),
                date_str,
                str(r.get('cls_resolution', '') or '—')[:40],
            ])
        add_styled_table(doc,
            ['No.', 'Fault Name', 'Block #', 'Equipment',
             'Reason', 'Date / Period', 'Resolution'], rows)
        if any_planned:
            add_caption(doc, '* block was under a planned or manual stop at '
                             'the time of the event.')
        if len(important) > 40:
            add_paragraph(doc,
                f'Showing first 40 of {len(important)} important events.',
                italic=True)

    # Alarms table — persistent warnings, same columns minus Date
    add_paragraph(doc, 'Warnings', bold=True)
    warn_detail = g.get('warn_detail', _pd.DataFrame())
    if warn_detail is None or warn_detail.empty:
        add_paragraph(doc, 'No standing warnings of note during the month.')
    else:
        rows = []
        any_planned = False
        for i, (_, r) in enumerate(warn_detail.head(25).iterrows(), start=1):
            blk = '' if _pd.isna(r.get('_blk')) else str(int(r['_blk']))
            if blk and bool(r.get('on_planned_stop')):
                blk += '*'; any_planned = True
            rows.append([
                str(i),
                str(r.get('Trigger name', ''))[:42],
                blk,
                str(r.get('cls_subsystem', '') or ''),
                str(r.get('cls_reason', '') or ''),
                str(r.get('cls_resolution', '') or '—')[:40],
            ])
        add_styled_table(doc,
            ['No.', 'Fault Name', 'Block #', 'Equipment',
             'Reason', 'Resolution'], rows)
        if any_planned:
            add_caption(doc, '* block was under a planned or manual stop at '
                             'the time of the event.')

    add_heading(doc, '5.2  Major Incidents and Breakdowns', level=3)
    add_paragraph(doc, 'Summary of breakdowns, incidents and their weight '
                       'affecting availability.')
    breakdown_rows_local = g.get('breakdown_rows')
    if breakdown_rows_local:
        if g.get('breakdown_is_auto'):
            add_caption(doc,
                'Auto-detected from the longest production faults (planned / '
                'manual stops excluded). Review and complete the temporary / '
                'final solution columns.')
        rows = []
        for i, inc in enumerate(breakdown_rows_local, start=1):
            dt = (f"{inc['downtime_h']:.1f} h"
                  if inc.get('downtime_h') is not None else '')
            rows.append([
                str(i),
                str(inc.get('incident', ''))[:40],
                str(inc.get('blocks', ''))[:14],
                str(inc.get('date_time', ''))[:16],
                str(inc.get('breakdown_type', ''))[:18],
                dt,
                str(inc.get('temporary_solution', ''))[:30],
                str(inc.get('final_solution', ''))[:30],
            ])
        add_styled_table(doc,
            ['No.', 'Breakdown incident', 'Block(s)', 'Date and time',
             'Breakdown type', 'Downtime', 'Temporary solution',
             'Final solution'], rows)
    else:
        add_paragraph(doc, 'No breakdowns in the reported period.')
        if not alarm_sum['longest'].empty:
            add_paragraph(doc, 'Longest events (informational):', italic=True)
            rows = []
            for _, r in alarm_sum['longest'].iterrows():
                trig = str(r['Trigger name'])[:50]
                if bool(r.get('is_excluded', False)):
                    excl_lbl = str(r.get('excluded_by', '') or 'exclusion')
                    trig = f"{trig}  (during {excl_lbl})"
                rows.append([str(r['Activated'])[:16], str(r['Element'])[:20],
                              trig,
                              (f"{r['duration_min']/60:.1f} h"
                                 if _pd.notna(r['duration_min']) else '—')])
            add_styled_table(doc,
                ['Activated', 'Element', 'Trigger', 'Duration'], rows)
    add_hr(doc)

    # 6. Site Operation
    add_section_banner(doc, '6.  Site Operation and Facility Management')
    add_heading(doc, 'Incidents affecting safety', level=3)
    if safety_incidents:
        rows = [[s.get('incident',''), s.get('equipment_loss','-'),
                  s.get('weight',''), s.get('countermeasure','')]
                 for s in safety_incidents]
        add_styled_table(doc, ['Incident', 'Equipment Loss', 'Weight', 'Countermeasure'], rows)
    else:
        add_paragraph(doc, 'No safety incidents in the reporting period.')
    add_heading(doc, 'Site visits and inspections', level=3)
    if site_visits:
        for v in site_visits: add_paragraph(doc, f'•  {v}')
    else:
        add_paragraph(doc, 'No site visits recorded.')
    add_hr(doc)

    # 7. Conclusions
    add_section_banner(doc, '7.  Conclusions and Recommendations')
    auto = [
        (f"The site averaged a round-trip efficiency of <b>{fleet_rte:.2f}%</b> "
         f"({rte_status} the 85% target), with an average availability of "
         f"<b>{fleet_availability_container:.2f}%</b>."),
        (f"Across {n_blocks} blocks the site discharged "
         f"<b>{total_discharge_mwh:,.1f} MWh</b> from "
         f"<b>{total_charge_mwh:,.1f} MWh</b> of charging — about "
         f"<b>{total_efc_fleet:.0f} full cycles</b> in total "
         f"(roughly {avg_efc_per_block:.1f} per block)."),
        (f"<b>{n_prod_alarms_genuine:,}</b> faults that affected production were "
         f"recorded, alongside {n_warn_persistent_genuine:,} standing warnings "
         f"(brief, self-clearing alarms are not counted; faults and alarms on "
         f"blocks under a planned or manual stop are also excluded)."),
    ]
    if n_anomalies:
        auto.append(f"<b>{n_anomalies}</b> block(s) performed below the rest of "
                     f"the site and are worth keeping an eye on.")
    if days_excluded > 0:
        auto.append(f"On average <b>{days_excluded:.1f}</b> day(s) per block had "
                     f"no usable charge/discharge data and were left out of the "
                     f"figures.")
    for i, t in enumerate(auto, 1):
        add_paragraph(doc, f'{i}.  {t}')
    if recommendations:
        add_heading(doc, 'Recommendations & Mitigation Strategies', level=3)
        for r in recommendations: add_paragraph(doc, f'•  {r}')
    if planned_next_period:
        add_heading(doc, 'Planned Activities for Next Reporting Period', level=3)
        for p in planned_next_period: add_paragraph(doc, f'•  {p}')
    add_hr(doc)

    # 8. Annexes
    add_section_banner(doc, '8.  List of Annexes')
    for line in [
        'Annex 1: Daily performance per block for the full month (spreadsheet)',
        'Annex 2: List of fault types and how they were classified',
        'Annex 3: Days set aside as incomplete or unusable',
        'Annex 4: Blocks performing below the site average',
    ]:
        add_paragraph(doc, f'•  {line}')
    if annexes:
        for a in annexes: add_paragraph(doc, f'•  {a}')

    save_doc(doc, output_path)
    return output_path
