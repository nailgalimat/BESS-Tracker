"""
services/bukhara_report_service.py
-----------------------------------
Block-level monthly operations report for the Bukhara BESS site (15 blocks).

This module is fully self-contained and DOES NOT modify or import the existing
Tashkent-format scada_report_service.py. The two report generators co-exist
side by side; the UI will pick the right one based on file naming.

Inputs (xlsx files from the SCADA export):
  - "Bukhara_Main KPI_5 minute data_*.xlsx"        site totals @ 5 min
  - "Bukhara_Overall_LC data_*.xlsx"               multi-sheet: LC_Energy,
                                                    PCS_Energy, Block 1..15
  - "Overall_Battery Unit Data_*.xlsx"             monthly cycle delta per block
  - "Bukhara_Blocks 1-15_System_availiability_*.xlsx"  per-block availability
  - "Main_Meter_daily_*.xlsx"                      POI meter daily totals
  - "Monthly Alarm Report_*.XLSX"                  Production + Warning events

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

# ── Palette ───────────────────────────────────────────────────────────────────
NAVY      = colors.HexColor('#1A2B45')
BLUE      = colors.HexColor('#0071E3')
LIGHT_BLU = colors.HexColor('#E8F0FD')
GREEN     = colors.HexColor('#34C759')
ORANGE    = colors.HexColor('#FF9500')
RED       = colors.HexColor('#FF3B30')
PURPLE    = colors.HexColor('#AF52DE')
GREY_BG   = colors.HexColor('#F5F7FA')
GREY_LINE = colors.HexColor('#E0E4EA')
TEXT_MUTE = colors.HexColor('#6B7A8D')
WHITE     = colors.white

STYLE_H1   = ParagraphStyle('H1', fontName='Helvetica-Bold', fontSize=18,
                textColor=NAVY, spaceAfter=6, spaceBefore=0)
STYLE_H2   = ParagraphStyle('H2', fontName='Helvetica-Bold', fontSize=13,
                textColor=NAVY, spaceAfter=4, spaceBefore=12, leading=16)
STYLE_H3   = ParagraphStyle('H3', fontName='Helvetica-Bold', fontSize=11,
                textColor=NAVY, spaceAfter=4, spaceBefore=8)
STYLE_BODY = ParagraphStyle('Body', fontName='Helvetica', fontSize=10,
                textColor=NAVY, spaceAfter=4, leading=14)
STYLE_SMALL= ParagraphStyle('Small', fontName='Helvetica', fontSize=8,
                textColor=TEXT_MUTE, spaceAfter=2, leading=11)
STYLE_CAP  = ParagraphStyle('Cap', fontName='Helvetica-Oblique', fontSize=8,
                textColor=TEXT_MUTE, alignment=TA_CENTER, spaceAfter=6)
STYLE_KVAL = ParagraphStyle('KVal', fontName='Helvetica-Bold', fontSize=22,
                textColor=NAVY, alignment=TA_CENTER, leading=26)
STYLE_KLBL = ParagraphStyle('KLbl', fontName='Helvetica', fontSize=8,
                textColor=TEXT_MUTE, alignment=TA_CENTER, leading=10)

# ── Helpers (private, scoped to this module to avoid touching Tashkent code) ──

def _hr():
    return HRFlowable(width='100%', thickness=0.5, color=GREY_LINE,
                      spaceAfter=6, spaceBefore=4)


def _section(title, icon=''):
    label = f'{icon}  {title}' if icon else title
    t = Table([[Paragraph(label, STYLE_H2)]], colWidths=['100%'])
    t.setStyle(TableStyle([
        ('BACKGROUND',    (0,0), (-1,-1), LIGHT_BLU),
        ('LEFTPADDING',   (0,0), (-1,-1), 10),
        ('RIGHTPADDING',  (0,0), (-1,-1), 10),
        ('TOPPADDING',    (0,0), (-1,-1), 7),
        ('BOTTOMPADDING', (0,0), (-1,-1), 7),
    ]))
    return t


def _kpi_row(items):
    n = len(items); col_w = 170 / n * mm
    cells = []
    for val, lbl, accent in items:
        inner = Table([
            [Paragraph(str(val), STYLE_KVAL)],
            [Paragraph(lbl, STYLE_KLBL)],
        ], colWidths=[col_w - 8*mm])
        inner.setStyle(TableStyle([
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('TOPPADDING', (0,0), (-1,-1), 4),
            ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ]))
        cells.append(inner)
    t = Table([cells], colWidths=[col_w]*n)
    cmds = [
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BACKGROUND', (0,0), (-1,-1), WHITE),
        ('BOX', (0,0), (-1,-1), 0.5, GREY_LINE),
        ('INNERGRID', (0,0), (-1,-1), 0.5, GREY_LINE),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
    ]
    for i, (_, _, accent) in enumerate(items):
        cmds.append(('LINEBELOW', (i,0), (i,0), 3, colors.HexColor(accent)))
    t.setStyle(TableStyle(cmds))
    return t


def _fig_to_image(fig, w_mm=170, h_mm=65):
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    buf.seek(0); plt.close(fig)
    return Image(buf, width=w_mm*mm, height=h_mm*mm)


def _styled_table(headers, rows, col_widths=None):
    hdr = [Paragraph(f'<b>{h}</b>', ParagraphStyle(
        'TH', fontName='Helvetica-Bold', fontSize=8,
        textColor=WHITE, alignment=TA_CENTER)) for h in headers]
    data = [hdr]
    for row in rows:
        data.append([Paragraph(str(c) if c is not None else '',
            ParagraphStyle('TD', fontName='Helvetica', fontSize=8,
                textColor=NAVY, alignment=TA_CENTER, leading=10))
            for c in row])
    if col_widths is None:
        col_widths = [170*mm / len(headers)] * len(headers)
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), NAVY),
        ('TEXTCOLOR', (0,0), (-1,0), WHITE),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('GRID', (0,0), (-1,-1), 0.3, GREY_LINE),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [WHITE, GREY_BG]),
    ]))
    return t


# ── DATA LOADERS ──────────────────────────────────────────────────────────────

LC_RE         = re.compile(r'System Info (\d+)')
BLOCK_RE      = re.compile(r'\bBlock\s*(\d+)\b', re.IGNORECASE)
ELEM_PREFIX_RE= re.compile(r'^(BSC|PCS|Battery|System|LC)\b', re.IGNORECASE)


def _parse_date(df, date_col='Date', time_col='Time'):
    """Add Datetime column from Date+Time string columns."""
    if time_col in df.columns:
        ts = pd.to_datetime(df[date_col].astype(str) + ' ' + df[time_col].astype(str),
                            errors='coerce')
    else:
        ts = pd.to_datetime(df[date_col], errors='coerce')
    out = df.copy()
    out['Datetime'] = ts
    out['Date_only'] = ts.dt.date
    return out


def load_site_kpi_5min(path):
    """Site-total 5-min telemetry (avg SOC, avg SOH, cycles, total energy, PF)."""
    df = pd.read_excel(path)
    df = _parse_date(df)
    return df


def load_lc_daily_energy(path):
    """
    Read 'LC_Energy' sheet → daily per-LC cumulative charge/discharge counters
    at midnight, plus daily fleet totals already provided.

    Returns:
        df_daily:    DataFrame indexed by date with one charge col + one discharge
                     col per LC (col names normalised to 'LC NN charge_kwh' /
                     'LC NN discharge_kwh') and the existing fleet totals
                     ('fleet_charge_kwh', 'fleet_discharge_kwh').
    """
    df = pd.read_excel(path, sheet_name='LC_Energy')
    # Strip trailing label rows ('Total Charge Energy (kWh) - Blockwise' etc.)
    df = df[pd.to_datetime(df['Date'], errors='coerce').notna()].copy()
    df['Date_only'] = pd.to_datetime(df['Date']).dt.date
    df = df.set_index('Date_only')

    norm = {}
    for c in df.columns:
        m = LC_RE.search(str(c))
        if not m:
            continue
        lc = f"LC {int(m.group(1)):02d}"
        if 'TOTAL CHARGE ENERGY' in c:
            norm[c] = f'{lc}_charge_kwh'
        elif 'TOTAL DISCHARGE ENERGY' in c:
            norm[c] = f'{lc}_discharge_kwh'
    df = df.rename(columns=norm)
    # Pre-existing fleet totals (last two columns)
    for c in df.columns:
        if 'TOTAL DAILY CHARGE' in str(c).upper():
            df = df.rename(columns={c: 'fleet_charge_kwh'})
        elif 'TOTAL DAILY DISCHARGE' in str(c).upper():
            df = df.rename(columns={c: 'fleet_discharge_kwh'})
    keep = [c for c in df.columns if isinstance(c, str) and (c.startswith('LC ') or c.startswith('fleet_'))]
    return df[keep].copy()


def load_block_5min_data(path):
    """
    From 'Overall_LC data' multi-sheet workbook, read Block 1..N sheets into a
    dict keyed by block_id (1..N). Each value is a DataFrame with columns:
        Datetime, Date_only, active_power_kw, power_factor, reactive_power_kvar,
        soc_pct, soh_pct, total_charge_kwh, total_discharge_kwh,
        cd_status, working_status

    Reads all matching sheets in a single open/parse pass (much faster than
    re-opening the file per sheet on large multi-sheet workbooks).
    """
    xl = pd.ExcelFile(path)
    block_sheets = [s for s in xl.sheet_names if BLOCK_RE.search(s)]
    raw = pd.read_excel(path, sheet_name=block_sheets)
    out = {}
    for sheet, df in raw.items():
        m = BLOCK_RE.search(sheet)
        if not m:
            continue
        block_id = int(m.group(1))
        df = _parse_date(df)
        renames = {}
        for c in df.columns:
            cs = str(c)
            if 'ACTIVE POWER (kW)' in cs:           renames[c] = 'active_power_kw'
            elif 'POWER FACTOR' in cs:               renames[c] = 'power_factor'
            elif 'REACTIVE POWER' in cs:             renames[c] = 'reactive_power_kvar'
            elif 'SYSTEM SOC' in cs:                 renames[c] = 'soc_pct'
            elif 'SYSTEM SOH' in cs:                 renames[c] = 'soh_pct'
            elif 'TOTAL CHARGE ENERGY' in cs:        renames[c] = 'total_charge_kwh'
            elif 'TOTAL DISCHARGE ENERGY' in cs:     renames[c] = 'total_discharge_kwh'
            elif 'CHARGING/DISCHARGING STATUS' in cs:renames[c] = 'cd_status'
            elif 'WORKING STATUS FEEDBACK' in cs:    renames[c] = 'working_status'
        df = df.rename(columns=renames)
        out[block_id] = df
    return out


def load_battery_unit_monthly_cycles(path):
    """Read 'Total' sheet → DataFrame block / cycle_begin / cycle_end / cycle_delta."""
    df = pd.read_excel(path, sheet_name='Total')
    # Clean up trailing whitespace in column names
    df.columns = [str(c).strip() for c in df.columns]
    # Standardise
    cycle_cols = [c for c in df.columns if 'CHARGE/DISCHARGE CYCLES' in c.upper()]
    if len(cycle_cols) >= 3:
        df = df.rename(columns={
            cycle_cols[0]: 'cycle_begin',
            cycle_cols[1]: 'cycle_end',
            cycle_cols[2]: 'cycle_delta',
        })
    # Extract block number
    df['block_id'] = df['BLOCK NUMBER'].astype(str).str.extract(r'(\d+)').astype(int)
    return df[['block_id', 'cycle_begin', 'cycle_end', 'cycle_delta']].copy()


def load_block_availability_statuses(path):
    """
    From the System_availability workbook (one sheet per block), pull just the
    per-block PCS Overall Working Status channels (most reliable signal for
    block-level availability). Returns dict {block_id -> DataFrame[Datetime, pcs01_status, pcs02_status]}.
    """
    xl = pd.ExcelFile(path)
    block_sheets = [s for s in xl.sheet_names if BLOCK_RE.search(s)]
    raw = pd.read_excel(path, sheet_name=block_sheets)
    out = {}
    for sheet, df in raw.items():
        m = BLOCK_RE.search(sheet)
        if not m:
            continue
        block_id = int(m.group(1))
        df = _parse_date(df)
        # Find the PCS Overall Working Status channels for this block
        pcs_cols = sorted([c for c in df.columns
                            if 'PCS' in str(c)
                            and 'OVERALL WORKING STATUS' in str(c)])
        renames = {}
        for i, c in enumerate(pcs_cols):
            renames[c] = f'pcs{i+1:02d}_status'
        df = df.rename(columns=renames)
        keep = ['Datetime', 'Date_only'] + list(renames.values())
        out[block_id] = df[keep].copy()
    return out


def load_meter_daily(path):
    """POI meter daily totals (already 30 rows × 56 cols, one per day)."""
    df = pd.read_excel(path)
    df = _parse_date(df)
    return df


def load_alarms(path):
    """
    Load Production + Warning alarm sheets and apply two cleanups:
      1. Deduplicate echo entries (BSC and BSC 01.02 trigger names that are
         the same event seen via two channels).
      2. Drop near-zero-duration warnings (flickers cleared in < 1 min) into a
         separate 'transient' bucket — they're noise.

    Returns dict with keys:
        'production'   — Production sheet (raw)
        'warning'      — Warning sheet (raw)
        'production_dedup' — deduplicated (event_id based when available)
        'warning_persistent'  — Warning events with duration >= 1 min
        'warning_transient'   — Warning events with duration < 1 min
    """
    out = {}
    for sheet in ['Production', 'Warning']:
        try:
            df = pd.read_excel(path, sheet_name=sheet)
            df['Activated'] = pd.to_datetime(df['Activated'], errors='coerce')
            df['Deactivation'] = pd.to_datetime(df['Deactivation'], errors='coerce')
            df['duration_min'] = (
                df['Deactivation'] - df['Activated']
            ).dt.total_seconds() / 60.0
            df['element_class'] = df['Element'].astype(str).str.extract(
                r'^(BSC|PCS|Battery|System|LC)', flags=re.IGNORECASE
            )[0].str.upper().fillna('OTHER')
            out[sheet.lower()] = df
        except Exception as e:
            print(f"Warning: could not load alarm sheet {sheet}: {e}")
            out[sheet.lower()] = pd.DataFrame()

    # Dedup heuristic for Production: same Activated timestamp + same Trigger
    # name normalised (strip 01.02 etc.) + same Element → one event
    def _normalise_trigger(s):
        """Strip BB.CC and BB.CC.UU prefixes (with or without surrounding
        whitespace) and collapse multiple spaces."""
        s = re.sub(r'\s*\d+\.\d+\.\d+\s*', ' ', str(s))
        s = re.sub(r'\s*\d+\.\d+\s*', ' ', s)
        s = re.sub(r'\s+', ' ', s).strip()
        return s

    if not out['production'].empty:
        p = out['production'].copy()
        p['trigger_norm'] = p['Trigger name'].apply(_normalise_trigger)
        out['production_dedup'] = p.drop_duplicates(
            subset=['Activated', 'trigger_norm', 'Element']
        )
    else:
        out['production_dedup'] = pd.DataFrame()

    if not out['warning'].empty:
        w = out['warning'].copy()
        w['trigger_norm'] = w['Trigger name'].apply(_normalise_trigger)
        # Persistent: duration >= 1 min, deduplicated across echo channels
        wp = w[w['duration_min'].fillna(0) >= 1.0].drop_duplicates(
            subset=['Activated', 'trigger_norm', 'Element']
        )
        out['warning_persistent'] = wp
        out['warning_transient']  = w[w['duration_min'].fillna(0) < 1.0]
    else:
        out['warning_persistent'] = pd.DataFrame()
        out['warning_transient']  = pd.DataFrame()

    return out




# ── ALARM CLASSIFICATION ──────────────────────────────────────────────────────

DEFAULT_ALARM_CLASSIFICATIONS_CSV = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'data', 'alarm_classifications.csv'
)


def load_alarm_classifications(path=None):
    """
    Load the lookup table mapping trigger-name patterns to (subsystem, reason,
    severity, resolution_hint). Default location: data/alarm_classifications.csv
    relative to the project root.
    """
    if path is None:
        path = DEFAULT_ALARM_CLASSIFICATIONS_CSV
    if not os.path.exists(path):
        print(f"Warning: alarm classification file not found at {path}")
        return pd.DataFrame(columns=['trigger_pattern', 'subsystem', 'reason',
                                      'severity', 'resolution_hint'])
    df = pd.read_csv(path)
    df['trigger_pattern_upper'] = df['trigger_pattern'].astype(str).str.upper()
    return df


def apply_alarm_classifications(alarms, classifications):
    """
    Mutate `alarms['production_dedup']` and `alarms['warning_persistent']` to
    add columns: cls_subsystem, cls_reason, cls_severity, cls_resolution.

    Matching: each trigger name's UPPER form is checked against the
    trigger_pattern_upper substrings; longest pattern that matches wins.
    Anything unmatched falls into ('OTHER', 'Unclassified', 'Warning', '').

    Vectorised — runs a single str.contains pass per pattern (longest first),
    fills cells the first time a pattern hits.
    """
    import re as _re
    if classifications.empty:
        return alarms

    # Sort patterns longest-first so we set the most specific match first
    patterns = classifications.assign(
        _len=classifications['trigger_pattern_upper'].astype(str).str.len()
    ).sort_values('_len', ascending=False)

    for key in ('production_dedup', 'warning_persistent'):
        df = alarms.get(key)
        if df is None or df.empty:
            continue
        df = df.copy()
        col = 'trigger_norm' if 'trigger_norm' in df.columns else 'Trigger name'
        t_upper = df[col].astype(str).str.upper()

        # Initialise four classifier columns with the default
        df['cls_subsystem']  = 'OTHER'
        df['cls_reason']     = 'Unclassified'
        df['cls_severity']   = 'Warning'
        df['cls_resolution'] = ''
        matched_mask = pd.Series(False, index=df.index)

        for _, r in patterns.iterrows():
            pat = _re.escape(str(r['trigger_pattern_upper']))
            hit = t_upper.str.contains(pat, regex=True, na=False) & (~matched_mask)
            if hit.any():
                df.loc[hit, 'cls_subsystem']  = r['subsystem']
                df.loc[hit, 'cls_reason']     = r['reason']
                df.loc[hit, 'cls_severity']   = r['severity']
                df.loc[hit, 'cls_resolution'] = r['resolution_hint'] if pd.notna(r.get('resolution_hint','')) else ''
                matched_mask = matched_mask | hit

        alarms[key] = df
    return alarms


def tag_alarms_with_exclusions(alarms, exclusions):
    """
    Add 'is_excluded' (bool) and 'excluded_by' (exclusion_type str) columns
    to production_dedup / warning_persistent / warning_transient.

    Block ID is extracted from the 'Element' field via the same r'(\\d+)\\.\\d+'
    pattern used elsewhere in this service ("BSC 01.02" → block 1). Rows whose
    Element doesn't carry a block prefix get block_id=None and are only
    matched by plant-wide exclusions (affected_blocks empty / "all").

    Safe to call with empty `exclusions` — every row gets is_excluded=False
    and existing behaviour is preserved exactly.
    """
    from services.availability_service import match_alarm_to_exclusions

    keys = ('production_dedup', 'warning_persistent', 'warning_transient')
    for key in keys:
        df = alarms.get(key)
        if df is None or df.empty:
            continue
        df = df.copy()
        # Default columns first so downstream code can rely on them
        df['is_excluded'] = False
        df['excluded_by'] = ''
        if not exclusions:
            alarms[key] = df
            continue

        # Block ID from "BSC 01.02" → 1, or None if no match
        block_ids = df['Element'].astype(str).str.extract(
            r'(\d+)\.\d+'
        )[0]
        block_ids = pd.to_numeric(block_ids, errors='coerce')

        # Per-row match — vectorised would require building per-exclusion
        # masks; a Python loop here is acceptable (alarms ≪ many thousands
        # for a typical monthly report).
        is_excluded = []
        excluded_by = []
        for activated, blk in zip(df['Activated'], block_ids):
            blk_val = None if pd.isna(blk) else int(blk)
            hit = match_alarm_to_exclusions(activated, blk_val, exclusions)
            if hit is not None:
                is_excluded.append(True)
                excluded_by.append(hit.get('exclusion_type', ''))
            else:
                is_excluded.append(False)
                excluded_by.append('')
        df['is_excluded'] = is_excluded
        df['excluded_by'] = excluded_by
        alarms[key] = df
    return alarms


# ── SOC / SOH KPIs ────────────────────────────────────────────────────────────

def calc_soc_soh(site_kpi_5min):
    """
    Compute average SOC and SOH from site-total 5-min telemetry.

    Samples with value 0 are excluded — they correspond to periods when the
    BMS is not reporting (e.g. during an auxiliary-power outage). This
    matches the convention used in Sungrow-format reports.
    """
    soc_col = next((c for c in site_kpi_5min.columns
                     if 'AVERAGE SOC' in str(c).upper()), None)
    soh_col = next((c for c in site_kpi_5min.columns
                     if 'AVERAGE SOH' in str(c).upper()), None)
    out = {'avg_soc_pct': None, 'avg_soh_pct': None,
           'soc_series': None, 'soh_series': None}
    if soc_col is not None:
        s = pd.to_numeric(site_kpi_5min[soc_col], errors='coerce')
        s_valid = s[s > 0]
        out['avg_soc_pct'] = float(s_valid.mean()) if len(s_valid) else None
        df = site_kpi_5min[['Date_only', soc_col]].copy()
        df[soc_col] = pd.to_numeric(df[soc_col], errors='coerce').where(
            lambda x: x > 0)
        out['soc_series'] = df.groupby('Date_only')[soc_col].mean()
    if soh_col is not None:
        s = pd.to_numeric(site_kpi_5min[soh_col], errors='coerce')
        s_valid = s[s > 0]
        out['avg_soh_pct'] = float(s_valid.mean()) if len(s_valid) else None
        df = site_kpi_5min[['Date_only', soh_col]].copy()
        df[soh_col] = pd.to_numeric(df[soh_col], errors='coerce').where(
            lambda x: x > 0)
        out['soh_series'] = df.groupby('Date_only')[soh_col].mean()
    return out


# ── PLANT-LEVEL HOURS AVAILABILITY (Sungrow methodology) ──────────────────────

def calc_plant_hours_availability(block_5min, plant_capacity_mw=None,
                                   per_block_capacity_mw=None,
                                   redundancy_threshold_pct=100.0,
                                   contractual_plant_capacity_mw=None,
                                   excluded_hours: float = 0.0):
    """
    Sungrow-style availability:
      Scheduled hours        = period_hours
      Scheduled unavailability = (e.g. PM windows) — passed from outside
      Unscheduled per-container outage hours = sum across blocks where
                                                 working_status NOT IN AVAILABLE
      Plant-level outage threshold:
        - If contractual_plant_capacity_mw is provided, plant is "available"
          at a given timestamp when block-aggregate capacity ≥ contractual.
          This matches the contract-based SLA definition used in Sungrow
          reports ("did not affect availability since the total contracted
          capacity was ensured with the available blocks").
        - Otherwise falls back to redundancy_threshold_pct × plant_capacity_mw.

    If neither plant_capacity_mw nor contractual is provided, the redundancy
    rule is skipped and only container-aggregate outage hours are returned.
    """
    if not block_5min:
        return None

    blocks = sorted(block_5min.keys())
    per_block_outage_hours = {}
    for b in blocks:
        df = block_5min[b]
        s = df['working_status'].astype(str)
        # Contractual (Tashkent) rule: ONLY FAULT / FAULT STOP count as
        # unavailable. STANDBY, ALARM RUNNING, STARTING, STOPPED, KEY STOP
        # (planned) are all treated as available / not-counted.
        n_unavail = s.isin(FAULT_SHUTDOWN_STATES).sum()
        per_block_outage_hours[b] = n_unavail * 5 / 60.0   # 5-min samples → hours

    container_outage_hours = sum(per_block_outage_hours.values())

    # Per-timestamp plant capacity check (requires plant_capacity_mw + per-block)
    plant_outage_hours = None
    threshold_mw = None
    if per_block_capacity_mw and (contractual_plant_capacity_mw or plant_capacity_mw):
        if contractual_plant_capacity_mw:
            threshold_mw = float(contractual_plant_capacity_mw)
        else:
            threshold_mw = redundancy_threshold_pct / 100.0 * float(plant_capacity_mw)
        # For each 5-min slot, count how many blocks are AVAILABLE.
        first = block_5min[blocks[0]]
        avail_wide = pd.DataFrame(index=first['Datetime'])
        for b in blocks:
            df = block_5min[b]
            df_idx = df.set_index('Datetime')['working_status']
            avail_wide[b] = (~df_idx.isin(FAULT_SHUTDOWN_STATES)).reindex(avail_wide.index)
        avail_wide = avail_wide.fillna(False)
        n_available = avail_wide.sum(axis=1)
        plant_capacity_avail_mw = n_available * per_block_capacity_mw
        plant_unavail_mask = plant_capacity_avail_mw < threshold_mw
        plant_outage_hours = plant_unavail_mask.sum() * 5 / 60.0

    # Period scheduled hours = number of 5-min samples × 5 / 60
    n_samples = len(block_5min[blocks[0]]['Datetime'])
    scheduled_hours = n_samples * 5 / 60.0

    # Apply availability exclusions (planned PM, grid outage, force majeure, …).
    # Mirrors SCADA's pattern: cap excluded at the actual outage so we never
    # credit back more than was lost, then remove the excluded window from the
    # denominator. When excluded_hours == 0 the formula reduces to the
    # pre-exclusion behaviour exactly.
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
        'per_block_outage_hours':    per_block_outage_hours,
        'threshold_mw':              threshold_mw,
        'plant_capacity_mw':         plant_capacity_mw,
        'contractual_plant_capacity_mw': contractual_plant_capacity_mw,
        'excluded_hours':            float(excluded_hours or 0.0),
        'excluded_effective':        excluded_effective,
    }


# ── MONTHLY COMPARISON (single-month placeholder + history hook) ──────────────

def build_monthly_comparison(current_kpis, history_records=None):
    """
    Returns a DataFrame: rows are months, columns are SOC, SOH, RTE, Cycles,
    Discharge MWh, Charge MWh. `history_records` is a list of dicts the caller
    can persist (e.g. in a DB or json file). For the first run, only the
    current month is returned.
    """
    rows = []
    if history_records:
        for r in history_records:
            rows.append(r)
    rows.append({
        'month':           current_kpis['month'],
        'avg_soc_pct':     current_kpis.get('avg_soc_pct'),
        'avg_soh_pct':     current_kpis.get('avg_soh_pct'),
        'rte_pct':         current_kpis.get('rte_pct'),
        'cycles':          current_kpis.get('cycles_total'),
        'discharge_mwh':   current_kpis.get('discharge_mwh'),
        'charge_mwh':      current_kpis.get('charge_mwh'),
    })
    return pd.DataFrame(rows)



# ── KPI ENGINE ────────────────────────────────────────────────────────────────

# Working-status enum (Bukhara LC level)
RUNNING_STATES   = {'RUNNING'}
STANDBY_STATES   = {'STANDBY', 'INITIAL STATUS', 'SELF-CHECKING'}
# 'FAULT' is the state name the SCADA actually emits (Tashkent); 'FAULT STOP'
# etc. are kept for other firmware variants. ALARM RUNNING = still running.
FAULT_STATES     = {'FAULT', 'FAULT STOP', 'ALARM RUNNING', 'STOPPING'}
# Strict subset that means the unit was shut down by a fault (excludes ALARM
# RUNNING, which is still operating). Used for contractual availability.
FAULT_SHUTDOWN_STATES = {'FAULT', 'FAULT STOP'}
STOPPED_STATES   = {'STOPPED'}
AVAILABLE_STATES = RUNNING_STATES | STANDBY_STATES


DEFAULT_HISTORY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'data', 'monthly_history.json'
)
# Bukhara keeps its OWN month-over-month history so the 4.3 comparison never
# mixes in Tashkent months (both report services share this module's helpers).
DEFAULT_HISTORY_PATH_BUKHARA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'data', 'monthly_history_bukhara.json'
)


def load_history_records(path=None):
    """Load past-month KPI records from JSON for use in the monthly comparison.
    Returns an empty list if the file doesn't exist or can't be read."""
    import json
    if path is None:
        path = DEFAULT_HISTORY_PATH
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Warning: could not read history file {path}: {e}")
        return []


def save_history_record(record, path=None):
    """Append (or upsert by month) a record into the history JSON file.
    Last 12 months are kept."""
    import json
    if path is None:
        path = DEFAULT_HISTORY_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    existing = load_history_records(path)
    existing = [r for r in existing if r.get('month') != record.get('month')]
    existing.append(record)
    existing = existing[-12:]
    with open(path, 'w') as f:
        json.dump(existing, f, indent=2, default=str)
    return path


def calc_block_availability(df_block_5min):
    """
    Block availability from LC working-status feedback (5-min cadence).
    Available % = (RUNNING + STANDBY + INITIAL STATUS + SELF-CHECKING) / total.
    Fault and Stopped count as unavailable.
    """
    s = df_block_5min['working_status'].astype(str)
    total = len(s)
    if total == 0:
        return None
    running   = s.isin(RUNNING_STATES).sum()
    standby   = s.isin(STANDBY_STATES).sum()
    fault     = s.isin(FAULT_SHUTDOWN_STATES).sum()   # contractual: FAULT / FAULT STOP only
    stopped   = s.isin(STOPPED_STATES).sum()
    available = total - fault                          # everything except genuine faults
    return {
        'availability_pct': available / total * 100,
        'running_pct':      running / total * 100,
        'standby_pct':      standby / total * 100,
        'fault_pct':        fault / total * 100,
        'stopped_pct':      stopped / total * 100,
        'samples':          total,
        'operating_hours':  running * 5 / 60.0,   # 5-min samples → hours
    }


def calc_daily_block_kpis(df_lc_daily, block_5min, cycles_df, meter_daily,
                          alarms, lc_to_block_fn=None,
                          nameplate_kwh_per_block=None):
    """
    Returns DataFrame: one row per (date, block) with full KPI set.

    Args:
        df_lc_daily:  output of load_lc_daily_energy()
        block_5min:   output of load_block_5min_data()
        cycles_df:    output of load_battery_unit_monthly_cycles()
        meter_daily:  output of load_meter_daily()
        alarms:       output of load_alarms()
        lc_to_block_fn: callable LC_id → block_id. Default: identity (LC NN -> block N)
        nameplate_kwh_per_block: optional; if set, EFC = throughput / (2*nameplate)
    """
    if lc_to_block_fn is None:
        lc_to_block_fn = lambda lc_int: lc_int

    # Build per-block daily charge & discharge by aggregating LC columns
    # (exclude the pre-aggregated fleet_* totals)
    chg_cols = [c for c in df_lc_daily.columns
                if c.endswith('_charge_kwh') and c.startswith('LC ')]
    dis_cols = [c for c in df_lc_daily.columns
                if c.endswith('_discharge_kwh') and c.startswith('LC ')]
    block_chg = {}; block_dis = {}
    for c in chg_cols:
        lc_n = int(re.search(r'LC (\d+)', c).group(1))
        b = lc_to_block_fn(lc_n)
        block_chg.setdefault(b, []).append(c)
    for c in dis_cols:
        lc_n = int(re.search(r'LC (\d+)', c).group(1))
        b = lc_to_block_fn(lc_n)
        block_dis.setdefault(b, []).append(c)

    rows = []
    all_dates = sorted(df_lc_daily.index.unique())
    cycle_delta_by_block = dict(zip(cycles_df['block_id'], cycles_df['cycle_delta']))

    # Alarms by day & block
    prod_per_block_day = {}
    if not alarms['production_dedup'].empty:
        a = alarms['production_dedup'].copy()
        a['block_id'] = a['Element'].astype(str).str.extract(r'(\d+)\.\d+')[0]
        a['day'] = a['Activated'].dt.date
        for (b, d), g in a.dropna(subset=['block_id']).groupby(['block_id', 'day']):
            try:
                prod_per_block_day[(int(b), d)] = len(g)
            except Exception:
                pass

    n_days_in_period = len(all_dates) if all_dates else 1
    # Even split of monthly cycle delta across days (proxy for daily EFC)
    daily_efc_proxy = {b: v/n_days_in_period for b, v in cycle_delta_by_block.items()}

    for d in all_dates:
        # Per-block charge / discharge for this date
        for b in sorted(set(list(block_chg.keys()) + list(block_dis.keys()))):
            chg = df_lc_daily.loc[d, block_chg.get(b, [])].sum() if block_chg.get(b) else 0
            dis = df_lc_daily.loc[d, block_dis.get(b, [])].sum() if block_dis.get(b) else 0
            throughput = chg + dis
            rte = (dis / chg * 100) if chg > 0 else np.nan
            # Availability via block_5min for that day
            avail = np.nan; op_hours = np.nan; fault_pct = np.nan
            if b in block_5min:
                day_slice = block_5min[b][block_5min[b]['Date_only'] == d]
                a = calc_block_availability(day_slice) if not day_slice.empty else None
                if a:
                    avail = a['availability_pct']
                    op_hours = a['operating_hours']
                    fault_pct = a['fault_pct']
            efc_day = daily_efc_proxy.get(b, np.nan) if chg > 0 else 0.0
            alarms_n = prod_per_block_day.get((b, d), 0)

            # Daily SOC stats from the block's 5-min frame. Zero samples
            # correspond to idle/disconnected periods so they're excluded
            # before taking min/avg — matches the site-wide SOC aggregator.
            min_soc = np.nan; avg_soc = np.nan
            if b in block_5min and 'soc_pct' in block_5min[b].columns:
                day_slice_soc = block_5min[b].loc[
                    block_5min[b]['Date_only'] == d, 'soc_pct'
                ]
                day_slice_soc = pd.to_numeric(day_slice_soc, errors='coerce')
                day_slice_soc = day_slice_soc[day_slice_soc > 0]
                if not day_slice_soc.empty:
                    min_soc = float(day_slice_soc.min())
                    avg_soc = float(day_slice_soc.mean())

            # Quality flag
            if (chg == 0 and dis == 0):
                qflag = 'EXCLUDED'   # data outage day or site offline
            elif (chg < 100 or pd.isna(rte)):
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
                'efc':              round(efc_day, 3) if pd.notna(efc_day) else None,
                'availability_pct': round(avail, 2) if pd.notna(avail) else None,
                'operating_hours':  round(op_hours, 1) if pd.notna(op_hours) else None,
                'fault_pct':        round(fault_pct, 2) if pd.notna(fault_pct) else None,
                'min_soc_pct':      round(min_soc, 2) if pd.notna(min_soc) else None,
                'avg_soc_pct':      round(avg_soc, 2) if pd.notna(avg_soc) else None,
                'alarms_count':     alarms_n,
                'quality_flag':     qflag,
            })
    return pd.DataFrame(rows)


def detect_anomalies(daily_kpi, rte_std_threshold=1.0,
                     throughput_std_threshold=1.0,
                     shallow_cycle_threshold_pp=5.0,
                     rte_target_pct=85.0):
    """
    For each (block), compare its mean RTE / throughput against fleet mean and
    flag if more than N standard deviations below.

    Absolute floor: a block whose throughput-weighted RTE meets `rte_target_pct`
    is healthy in absolute terms and is NOT flagged, even if it sits slightly
    below the (very tight) fleet mean. This keeps the table to genuinely
    under-performing blocks rather than statistical noise. Pass
    rte_target_pct=None to restore the pure relative-only behaviour.

    Also produces a per-block diagnostic 'likely_cause' field for flagged
    blocks, based on whether the block ran with consistently higher minimum
    SOC than the fleet (shallow cycling) and/or reduced energy throughput.
    Empty string when the cause is unclear from the data.
    """
    valid = daily_kpi[daily_kpi['quality_flag'] == 'VALID'].copy()
    if valid.empty:
        return pd.DataFrame()

    # Throughput / availability / alarms aggregate over ALL valid days — these
    # are energy and uptime, every operating day counts.
    agg = {
        'avg_throughput':   ('throughput_kwh',  'mean'),
        'days_valid':       ('rte_pct',         'count'),
        'avg_availability': ('availability_pct','mean'),
        'total_alarms':     ('alarms_count',    'sum'),
    }
    if 'min_soc_pct' in valid.columns:
        agg['avg_min_soc'] = ('min_soc_pct', 'mean')
    if 'avg_soc_pct' in valid.columns:
        agg['avg_avg_soc'] = ('avg_soc_pct', 'mean')

    per_block = valid.groupby('block').agg(**agg).reset_index()

    # avg_rte = throughput-weighted RTE (Σ discharge / Σ charge) rather than
    # the simple mean of daily RTE values. The latter is biased by low-
    # throughput days where small absolute differences produce wonky RTEs
    # (Jensen's inequality on ratios). The throughput-weighted form matches
    # the canonical monthly figure the SCADA totalizer reports.
    #
    # RTE is summed over the WHOLE month (every day), not just full-cycle days.
    # Over a month the day-to-day SOC carry-over nets out, so Σ discharge /
    # Σ charge is the unbiased monthly efficiency. Restricting to full-cycle days
    # would bias it low, because the matching net-discharge days (RTE > 100%) are
    # filtered out while the net-charge days (low RTE) remain.
    rte_block = (daily_kpi.groupby('block')
                          .agg(sum_charge=('charge_kwh', 'sum'),
                               sum_discharge=('discharge_kwh', 'sum'))
                          .reset_index())
    per_block = per_block.merge(rte_block, on='block', how='left')
    for col in ('sum_charge', 'sum_discharge'):
        if col not in per_block.columns:
            per_block[col] = 0.0
        per_block[col] = per_block[col].fillna(0.0)

    per_block['avg_rte'] = pd.NA
    mask_pos = per_block['sum_charge'] > 0
    per_block.loc[mask_pos, 'avg_rte'] = (
        per_block.loc[mask_pos, 'sum_discharge']
        / per_block.loc[mask_pos, 'sum_charge'] * 100
    )
    per_block['avg_rte'] = pd.to_numeric(per_block['avg_rte'], errors='coerce')

    rte_mean = per_block['avg_rte'].mean()
    rte_std  = per_block['avg_rte'].std()
    th_mean  = per_block['avg_throughput'].mean()
    th_std   = per_block['avg_throughput'].std()
    per_block['rte_z']        = (per_block['avg_rte'] - rte_mean) / rte_std if rte_std > 0 else 0
    per_block['throughput_z'] = (per_block['avg_throughput'] - th_mean) / th_std if th_std > 0 else 0
    per_block['anomaly_rte']        = per_block['rte_z']        < -rte_std_threshold
    per_block['anomaly_throughput'] = per_block['throughput_z'] < -throughput_std_threshold
    per_block['anomaly_flag']       = per_block[['anomaly_rte', 'anomaly_throughput']].any(axis=1)

    # Absolute floor — suppress the flag for blocks that meet the RTE target.
    # A block at/above target is performing fine; being marginally below the
    # fleet mean is not a customer-reportable anomaly.
    if rte_target_pct is not None:
        meets_target = (per_block['avg_rte'] >= float(rte_target_pct)).fillna(False)
        per_block.loc[meets_target, 'anomaly_flag'] = False

    # Fleet-relative min-SOC delta (positive = block held higher minimum SOC
    # than the fleet, i.e. shallower cycling, which tends to depress RTE)
    if 'avg_min_soc' in per_block.columns:
        fleet_min_soc = per_block['avg_min_soc'].mean()
        per_block['min_soc_delta_pp'] = per_block['avg_min_soc'] - fleet_min_soc
    else:
        fleet_min_soc = float('nan')
        per_block['avg_min_soc']    = float('nan')
        per_block['min_soc_delta_pp'] = float('nan')

    # Likely-cause text per block (empty unless block is actually flagged)
    def _diagnose(r):
        if not r['anomaly_flag']:
            return ''
        parts = []
        if pd.notna(r['min_soc_delta_pp']) and r['min_soc_delta_pp'] > shallow_cycle_threshold_pp:
            parts.append(
                f"shallow cycling (min SOC ~{r['avg_min_soc']:.0f}%, "
                f"fleet ~{fleet_min_soc:.0f}%)"
            )
        if r['anomaly_throughput']:
            parts.append(
                f"reduced throughput ({r['avg_throughput']/1000:.1f} MWh/day)"
            )
        if not parts:
            return ''
        return 'Likely cause: ' + ' and '.join(parts) + '.'

    per_block['likely_cause'] = per_block.apply(_diagnose, axis=1)
    return per_block


def diagnose_low_rte_days(daily_kpi, rte_target=85.0, avail_floor=95.0,
                          net_charge_pp=10.0):
    """
    Diagnose why each VALID block-day came in below `rte_target`.

    A daily efficiency (discharge ÷ charge) below 100% means the block charged
    more than it discharged that day, i.e. it ended the day fuller than it
    started. That is normal: a full charge/discharge cycle usually spans across
    midnight, so a day spent mainly recharging reads low while the matching
    deep-discharge day reads over 100% (and is filtered out). Such days are a
    measurement artefact of the calendar boundary, not a real efficiency loss.

    Cause priority:
      1. 'Block stopped / maintenance' — availability < avail_floor that day.
      2. 'Partial cycle'               — not a full cycle (SOC didn't reach both
         rails; charged or discharged only part-way then stopped).
      3. 'Mostly charging that day'    — the block ended the day more than
         net_charge_pp points fuller than it started (or start/end SOC unknown):
         a day-boundary effect, the dominant reason daily RTE dips.
      4. 'Genuine low RTE'             — a full cycle that returned to its
         starting charge level (no net carry-over) yet still below target. The
         only category that points to a real efficiency loss.

    Returns (summary_df, genuine_df).
    """
    if (daily_kpi is None or daily_kpi.empty
            or 'rte_pct' not in daily_kpi.columns):
        return pd.DataFrame(), pd.DataFrame()

    df = daily_kpi[(daily_kpi['quality_flag'] == 'VALID')
                   & daily_kpi['rte_pct'].notna()
                   & (daily_kpi['rte_pct'] < rte_target)].copy()
    if df.empty:
        return pd.DataFrame(), pd.DataFrame()

    has_fc  = 'full_cycle' in df.columns
    has_soc = 'start_soc_pct' in df.columns and 'end_soc_pct' in df.columns

    def _cause(r):
        avail = r.get('availability_pct')
        fc    = bool(r.get('full_cycle')) if has_fc else None
        if avail is not None and pd.notna(avail) and avail < avail_floor:
            return 'Block stopped / maintenance'
        if fc is False:
            return 'Partial cycle'
        if has_soc:
            start = r.get('start_soc_pct')
            end   = r.get('end_soc_pct')
            if pd.notna(start) and pd.notna(end):
                if (end - start) > net_charge_pp:
                    return 'Mostly charging that day'
                return 'Genuine low RTE'
        # SOC trajectory unknown → cannot confirm a self-contained cycle, so
        # attribute to the day-boundary effect rather than over-flag a fault.
        return 'Mostly charging that day'

    df['cause'] = df.apply(_cause, axis=1)
    n = len(df)
    summary = (df.groupby('cause').size().reset_index(name='n_block_days')
                 .sort_values('n_block_days', ascending=False))
    summary['pct'] = (summary['n_block_days'] / n * 100).round(1)

    gcols = [c for c in ('block', 'date', 'rte_pct', 'start_soc_pct',
                         'end_soc_pct', 'min_soc_pct', 'max_soc_pct')
             if c in df.columns]
    genuine = (df[df['cause'] == 'Genuine low RTE'][gcols]
               .sort_values('rte_pct'))
    return summary.reset_index(drop=True), genuine.reset_index(drop=True)


def summarize_alarms(alarms):
    """
    Classify Production-dedup events by element class. Return:
      - by_class: events per class with mean duration and mean event count/day
      - top_triggers: most frequent normalised trigger names
      - longest: longest-duration individual events
    """
    out = {}
    p = alarms['production_dedup']
    if p.empty:
        return {'by_class': pd.DataFrame(), 'top_triggers': pd.DataFrame(),
                'longest': pd.DataFrame()}
    out['by_class'] = p.groupby('element_class').agg(
        n_events=('Trigger name', 'count'),
        mean_duration_min=('duration_min', 'mean'),
        total_duration_h=('duration_min', lambda x: x.sum() / 60.0),
    ).reset_index().sort_values('n_events', ascending=False)
    out['top_triggers'] = (p['trigger_norm'].value_counts()
                            .head(10).reset_index())
    out['top_triggers'].columns = ['trigger', 'count']
    longest_cols = ['Activated', 'Element', 'Trigger name', 'duration_min']
    # Carry forward exclusion tags when present so 5.2 can annotate rows
    for extra in ('is_excluded', 'excluded_by'):
        if extra in p.columns:
            longest_cols.append(extra)
    out['longest'] = p.nlargest(10, 'duration_min')[longest_cols]
    return out


# ── PLANNED-STOP CROSS-REFERENCE ──────────────────────────────────────────────

_MANUAL_STOP_PATTERNS = ('MANUAL STOP', 'KEY STOP')


def mark_planned_stop_events(events_df, ws_long=None):
    """
    Add a boolean `on_planned_stop` column to an alarm events frame.

    An event is considered to coincide with a planned/operator stop when ANY of:
      • it is already inside an availability exclusion window (is_excluded), OR
      • its trigger / reason is a Manual Stop / Key Stop (operator command), OR
      • the affected container (or any container of the block) was in the
        STOPPED working state at the event timestamp (floored to the 5-min grid).

    Adds helper columns `_blk` (block id) and `on_planned_stop`. Safe on empty
    input and when ws_long is unavailable (then only the first two rules apply).
    """
    if events_df is None or events_df.empty:
        return events_df
    df = events_df.copy()

    elem = df['Element'].astype(str).str.extract(r'(\d+)\.(\d+)')
    df['_blk'] = pd.to_numeric(elem[0], errors='coerce')
    df['_cnt'] = pd.to_numeric(elem[1], errors='coerce')
    # Element with no container suffix ("BSC 05") → block only
    blk_only = df['Element'].astype(str).str.extract(r'(\d+)')[0]
    df['_blk'] = df['_blk'].fillna(pd.to_numeric(blk_only, errors='coerce'))
    df['_ts'] = pd.to_datetime(df.get('Activated'), errors='coerce').dt.floor('5min')

    trig   = df.get('Trigger name', pd.Series('', index=df.index)).astype(str).str.upper()
    reason = df.get('cls_reason',  pd.Series('', index=df.index)).astype(str).str.upper()
    manual = pd.Series(False, index=df.index)
    for pat in _MANUAL_STOP_PATTERNS:
        manual |= trig.str.contains(pat, na=False) | reason.str.contains(pat, na=False)

    excluded = (df.get('is_excluded', pd.Series(False, index=df.index))
                  .fillna(False).astype(bool))

    stopped = pd.Series(False, index=df.index)
    if ws_long is not None and not ws_long.empty and 'working_status' in ws_long.columns:
        stop_up = {s.upper() for s in STOPPED_STATES}
        ws = ws_long.copy()
        ws['_st'] = ws['working_status'].astype(str).str.upper().isin(stop_up)
        ws = ws[ws['_st']]
        if not ws.empty:
            ws['_ts'] = pd.to_datetime(ws['Datetime'], errors='coerce').dt.floor('5min')
            ws = ws.dropna(subset=['_ts', 'block_id'])
            # Block-level set uses every stopped sample; container-level set only
            # rows with a valid container_id (avoids astype(int) on NaN).
            bset = set(zip(ws['block_id'].astype(int), ws['_ts']))
            ws_c = ws.dropna(subset=['container_id'])
            cset = set(zip(ws_c['block_id'].astype(int),
                           ws_c['container_id'].astype(int), ws_c['_ts']))

            def _is_stopped(r):
                if pd.isna(r['_ts']) or pd.isna(r['_blk']):
                    return False
                b = int(r['_blk'])
                if pd.notna(r['_cnt']) and (b, int(r['_cnt']), r['_ts']) in cset:
                    return True
                return (b, r['_ts']) in bset

            stopped = df.apply(_is_stopped, axis=1)

    df['on_planned_stop'] = (manual | excluded | stopped).astype(bool)
    return df


def _fmt_blocks_with_planned(blocks, planned_set, max_show=12):
    """'12, 45*, 58' — planned-stop blocks get a trailing asterisk."""
    parts = [f"{b}*" if b in planned_set else str(b) for b in blocks[:max_show]]
    s = ', '.join(parts)
    if len(blocks) > max_show:
        s += f" +{len(blocks) - max_show} more"
    return s


def build_event_type_table(events_df, top_n=40, period_end=None):
    """
    Collapse an event-level alarm frame into ONE ROW PER FAULT TYPE for the
    report's event tables.

    Fixes three defects of the previous `events.head(N)` rendering:

      1. The same fault firing simultaneously on several units of a block
         produced several visually identical rows, because the table shows the
         block but not the unit (e.g. BSC 32.01.01 and BSC 32.01.02 both read
         as "block 32"). Units are now counted, not repeated.
      2. Ranking individual events by duration let one dominant family crowd
         out everything else — 34 of 40 rows could be the same trigger, so fire
         and gas alarms never reached the report. One row per type guarantees
         every distinct fault appears.
      3. Still-active alarms (no Deactivation) had no duration, so they were
         silently dropped from duration-ranked tables. They are now measured to
         `period_end` and flagged — an unresolved alarm is the most important
         kind.

    Returns DataFrame[trigger, subsystem, reason, resolution, events, units,
    total_h, blocks, worst_block, worst_when, worst_h, active] sorted by
    total_h desc and capped at `top_n`. `resolution` is taken from the largest
    sub-group, so one reason carrying two different hint texts no longer splits
    into two rows.
    """
    if events_df is None or events_df.empty:
        return pd.DataFrame()

    df = events_df.copy()
    act = pd.to_datetime(df.get('Activated'), errors='coerce')
    deact = pd.to_datetime(df.get('Deactivation'), errors='coerce')
    if period_end is None:
        period_end = pd.concat([act, deact]).max()
    period_end = pd.to_datetime(period_end, errors='coerce')

    df['_active'] = deact.isna() & act.notna()
    end_eff = deact.fillna(period_end)
    dur_h = (end_eff - act).dt.total_seconds() / 3600.0
    if 'duration_min' in df.columns:
        known = pd.to_numeric(df['duration_min'], errors='coerce') / 60.0
        dur_h = known.where(~df['_active'] & known.notna(), dur_h)
    df['_dur_h'] = dur_h.clip(lower=0).fillna(0.0)
    df['_act_dt'] = act

    if '_blk' in df.columns:
        blk = pd.to_numeric(df['_blk'], errors='coerce')
    else:
        blk = pd.to_numeric(
            df.get('Element', pd.Series('', index=df.index))
              .astype(str).str.extract(r'(\d+)\.')[0], errors='coerce')
    df['_blk_n'] = blk
    df['_elem'] = df.get('Element', pd.Series('', index=df.index)).astype(str)

    keys = ['Trigger name']
    for c in ('cls_subsystem', 'cls_reason'):
        if c in df.columns:
            keys.append(c)

    rows = []
    for kv, g in df.groupby(keys, dropna=False):
        kv = kv if isinstance(kv, tuple) else (kv,)
        trig = str(kv[0])
        sub = str(kv[1]) if len(kv) > 1 else ''
        reason = str(kv[2]) if len(kv) > 2 else ''
        # resolution from the largest sub-group — avoids splitting one reason
        res = ''
        if 'cls_resolution' in g.columns:
            vc = g['cls_resolution'].astype(str).replace('', pd.NA).dropna().value_counts()
            res = str(vc.index[0]) if len(vc) else ''
        blocks = sorted({int(b) for b in g['_blk_n'].dropna().unique()})
        planned = set()
        if 'on_planned_stop' in g.columns:
            planned = {int(b) for b in
                       g.loc[g['on_planned_stop'].astype(bool), '_blk_n'].dropna().unique()}
        worst = g.loc[g['_dur_h'].idxmax()] if len(g) else None
        rows.append({
            'trigger':    trig,
            'subsystem':  sub,
            'reason':     reason,
            'resolution': res,
            'events':     int(len(g)),
            'units':      int(g['_elem'].nunique()),
            'total_h':    float(g['_dur_h'].sum()),
            'blocks':     _fmt_blocks_with_planned(blocks, planned),
            'n_blocks':   len(blocks),
            'worst_block': ('' if worst is None or pd.isna(worst['_blk_n'])
                            else str(int(worst['_blk_n']))),
            'worst_when': ('' if worst is None or pd.isna(worst['_act_dt'])
                           else worst['_act_dt'].strftime('%d.%m %H:%M')),
            'worst_h':    float(worst['_dur_h']) if worst is not None else 0.0,
            'active':     int(g['_active'].sum()),
        })

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    # Unresolved alarms first, then by total downtime
    out = out.sort_values(['active', 'total_h'], ascending=[False, False])
    return out.head(top_n).reset_index(drop=True)


def build_faults_summary(events_df, ws_long=None, top_n=15, drop_planned=False):
    """
    Group classified alarm events by (subsystem, reason, resolution) and return
    a render-ready summary used by both the PDF and DOCX faults/alarms tables.

    Columns: subsystem, reason, occurrences, total_hours, blocks_affected
    (comma-separated, planned-stop blocks marked '*'), any_planned, resolution.
    Pass `ws_long` to enable the STOPPED-state planned-stop check.

    When `drop_planned=True`, events that coincide with a planned/operator stop
    (manual/key stop, exclusion window, or STOPPED state) are removed entirely
    before grouping — so a fault on a block that was deliberately offline does
    not appear at all, while the same fault type elsewhere still counts. Default
    False keeps the original "mark with '*'" behaviour for other callers.
    """
    if (events_df is None or events_df.empty
            or 'cls_reason' not in events_df.columns):
        return pd.DataFrame()

    df = mark_planned_stop_events(events_df, ws_long)
    if drop_planned and 'on_planned_stop' in df.columns:
        df = df[~df['on_planned_stop'].astype(bool)]
        if df.empty:
            return pd.DataFrame()
    has_res = 'cls_resolution' in df.columns
    # Group by (subsystem, reason) ONLY — the resolution text must not be part
    # of the key. Two classification entries mapping different SCADA triggers
    # to the same reason but carrying different hint wording (e.g. the two
    # DCDC patterns) otherwise split one fault into two identical-looking rows.
    keys = ['cls_subsystem', 'cls_reason']

    rows = []
    for key_vals, g in df.groupby(keys):
        if not isinstance(key_vals, tuple):
            key_vals = (key_vals,)
        sub    = key_vals[0]
        reason = key_vals[1]
        # Resolution from the largest sub-group, so the dominant wording wins.
        res = ''
        if has_res:
            _vc = g['cls_resolution'].astype(str).replace('', pd.NA).dropna().value_counts()
            res = str(_vc.index[0]) if len(_vc) else ''
        blocks = sorted({int(b) for b in g['_blk'].dropna().unique()})
        planned = sorted({int(b) for b in
                          g.loc[g['on_planned_stop'], '_blk'].dropna().unique()})
        rows.append({
            'subsystem':       sub,
            'reason':          reason,
            'resolution':      res or '',
            'occurrences':     int(len(g)),
            'total_hours':     float(g['duration_min'].sum() / 60.0),
            'blocks_affected': _fmt_blocks_with_planned(blocks, set(planned)),
            'n_planned':       len(planned),
            'any_planned':     bool(planned),
        })
    out = pd.DataFrame(rows).sort_values('occurrences', ascending=False)
    if top_n:
        out = out.head(top_n)
    return out.reset_index(drop=True)


def build_breakdown_candidates(events_df, ws_long=None, top_n=10,
                               min_duration_h=1.0):
    """
    Auto-derive 'major breakdown' candidates for the semi-automatic 5.2 table.

    Picks the highest-downtime genuine faults (severity Fault/Critical),
    grouped per (block, reason), with planned/manual-stop events excluded. Each
    candidate carries block, first occurrence, subsystem and total downtime;
    the temporary/final-solution fields are left blank for the operator to
    complete. Returns a list of dicts matching the manual breakdown schema
    (plus 'blocks', 'downtime_h', 'occurrences', 'auto'). Empty list when there
    is nothing material to report.
    """
    if (events_df is None or events_df.empty
            or 'cls_reason' not in events_df.columns):
        return []

    df = mark_planned_stop_events(events_df, ws_long)
    df = df[~df['on_planned_stop'].astype(bool)].copy()
    sev = df.get('cls_severity', pd.Series('', index=df.index)).astype(str).str.upper()
    df = df[sev.isin(['FAULT', 'CRITICAL'])]
    if df.empty:
        return []

    df['_act'] = pd.to_datetime(df.get('Activated'), errors='coerce')
    df['_dur'] = pd.to_numeric(df.get('duration_min'), errors='coerce').fillna(0.0)

    rows = []
    for (blk, reason), g in df.groupby(['_blk', 'cls_reason']):
        if pd.isna(blk):
            continue
        total_h = float(g['_dur'].sum() / 60.0)
        if total_h < min_duration_h:
            continue
        first = g['_act'].min()
        sub = g['cls_subsystem'].iloc[0] if 'cls_subsystem' in g.columns else ''
        rows.append({
            'incident':           str(reason),
            'blocks':             str(int(blk)),
            'date_time':          first.strftime('%d.%m.%Y %H:%M') if pd.notna(first) else '',
            'breakdown_type':     str(sub or ''),
            'downtime_h':         round(total_h, 1),
            'occurrences':        int(len(g)),
            'temporary_solution': '',
            'final_solution':     '',
            'closure_date':       '',
            'auto':               True,
        })
    rows.sort(key=lambda r: r['downtime_h'], reverse=True)
    return rows[:top_n]


# ── CHART BUILDERS ────────────────────────────────────────────────────────────

def _chart_daily_rte_per_block(daily_kpi):
    fig, ax = plt.subplots(figsize=(11, 4))
    # Only plot days flagged VALID — INCOMPLETE (tiny charge) and ESTIMATED
    # (rte outside 50–100% band) days otherwise blow up the y-axis to
    # nonsensical values like 250 000%. The fleet mean was being pulled
    # along too.
    valid = daily_kpi[(daily_kpi['quality_flag'] == 'VALID')
                        & daily_kpi['rte_pct'].notna()]
    for block, grp in valid.groupby('block'):
        ax.plot(grp['date'], grp['rte_pct'], lw=0.8, alpha=0.45)
    fleet = valid.groupby('date')['rte_pct'].mean()
    ax.plot(fleet.index, fleet.values, color='#1A2B45', lw=2.5, label='Fleet mean')
    ax.axhline(85, color='#34C759', ls='--', lw=1, alpha=0.7, label='Target 85%')
    ax.set_ylim(0, 110)   # RTE > 100% is physically impossible; cap defensively
    ax.set_ylabel('RTE (%)', fontsize=8)
    ax.set_title('Daily Round-Trip Efficiency — per Block + Fleet Mean',
                 fontsize=10, fontweight='bold', color='#1A2B45')
    ax.legend(fontsize=7, loc='lower right')
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    ax.grid(axis='y', ls='--', alpha=0.3)
    fig.autofmt_xdate(rotation=30)
    fig.tight_layout()
    return fig


def _chart_throughput_per_day(daily_kpi):
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
    ax.set_title('Daily Fleet Throughput — Charge vs Discharge',
                 fontsize=10, fontweight='bold', color='#1A2B45')
    ax.legend(fontsize=7)
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    ax.grid(axis='y', ls='--', alpha=0.3)
    fig.tight_layout()
    return fig


def _chart_availability_heatmap(daily_kpi, exclusions=None):
    """One row per block, one column per day, cell colour = availability %.

    Stepped colormap with bands at operational thresholds so the bulk of
    "all OK" cells don't drown out the 95-99% vs 99-100% distinction and a
    50% outage looks visually distinct from a 0% one.

    When `exclusions` is provided (list of dicts from availability_service),
    cells inside a planned exclusion window are overlaid with diagonal
    hatching so the reader can immediately tell planned downtime from
    unplanned outage.
    """
    import matplotlib.colors as mcolors

    pivot = daily_kpi.pivot_table(
        index='block', columns='date', values='availability_pct', aggfunc='mean'
    )

    # Five bands at operationally meaningful thresholds
    bounds = [0, 50, 80, 95, 99, 100.001]
    band_colors = ['#8B0000',   # < 50%   critical
                    '#DC143C',   # 50–80%  major outage
                    '#FF8C00',   # 80–95%  degraded
                    '#9ACD32',   # 95–99%  minor degradation
                    '#22AA22']   # ≥ 99%   full operation
    cmap = mcolors.ListedColormap(band_colors)
    norm = mcolors.BoundaryNorm(bounds, cmap.N)

    fig, ax = plt.subplots(figsize=(11, max(3, 0.25*len(pivot)+1.5)))
    im = ax.imshow(pivot.values, aspect='auto', cmap=cmap, norm=norm)

    # Overlay diagonal hatching on cells that fall inside an exclusion window
    if exclusions:
        all_dates_list = list(pivot.columns)
        # Pre-compute exclusion intervals once: (start_date, end_date, block_set)
        intervals = []
        for exc in exclusions:
            try:
                d_from = pd.to_datetime(exc['date_from']).date()
                d_to   = pd.to_datetime(exc['date_to']).date()
            except Exception:
                continue
            aff = (exc.get('affected_blocks') or '').strip()
            if not aff or aff.lower() in ('all', 'all blocks'):
                blk_set = None   # all blocks
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

    # All block labels — at this figure height every row fits
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels([f'B{b}' for b in pivot.index], fontsize=6)
    cols = [str(c)[5:] for c in pivot.columns]
    step = max(1, len(cols)//15)
    ax.set_xticks(range(0, len(cols), step))
    ax.set_xticklabels([cols[i] for i in range(0, len(cols), step)],
                       fontsize=6, rotation=30)
    ax.set_title('Daily Availability Heatmap — Block × Day (%)',
                 fontsize=10, fontweight='bold', color='#1A2B45')

    # Banded colorbar with labels for each band
    cb = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02,
                       ticks=[25, 65, 87.5, 97, 99.5])
    cb.ax.set_yticklabels(['<50%', '50–80%', '80–95%', '95–99%', '≥99%'],
                           fontsize=6)

    if exclusions:
        ax.text(0.0, -0.18,
                'Hatched cells = inside a planned availability exclusion window.',
                transform=ax.transAxes, fontsize=6,
                color='#6B7A8D', style='italic')

    fig.tight_layout()
    return fig


# Patterns that the customer considers operational noise — even if the
# classifier tagged them as Fault, they are filtered out of the "Major Faults
# — Date/Time Detail" table. Uppercased substring match against Trigger name.
NOISE_TRIGGER_PATTERNS = [
    'INPUT DRY NODE',           # field-wiring signal, not a real fault
    'MANUAL STOP', 'KEY STOP',  # operator command, intentional
]

# Anything lasting at least this long, regardless of severity tag, is treated
# as important enough to appear in the detail table.
IMPORTANT_DURATION_THRESHOLD_MIN = 30.0


def select_important_alarms(alarms_df,
                             duration_threshold_min=IMPORTANT_DURATION_THRESHOLD_MIN,
                             noise_patterns=None):
    """
    Filter the production_dedup DataFrame down to the "important" alarms the
    customer wants surfaced with date+time+block:

      • cls_severity == 'Critical', OR
      • duration_min ≥ duration_threshold_min

    Always excludes:
      • duration < 1 min (transient)
      • cls_severity == 'Info' (manual stops, etc.)
      • Triggers matching `noise_patterns` (default NOISE_TRIGGER_PATTERNS)

    `is_excluded` events (inside an availability exclusion window) are also
    dropped from this view so the customer doesn't double-count them — they
    are still reported separately in the "Events During Exclusion Windows"
    informational section.

    Returns the filtered DataFrame, sorted by Activated ascending.
    """
    if alarms_df is None or alarms_df.empty:
        return pd.DataFrame()
    patterns = list(noise_patterns) if noise_patterns is not None \
                else NOISE_TRIGGER_PATTERNS

    df = alarms_df.copy()
    if 'is_excluded' in df.columns:
        df = df[~df['is_excluded'].astype(bool)]

    # Build the importance mask
    sev = df.get('cls_severity', pd.Series(['']*len(df), index=df.index))
    sev_up = sev.astype(str).str.upper()
    dur = pd.to_numeric(df.get('duration_min'), errors='coerce').fillna(0)

    important = (sev_up == 'CRITICAL') | (dur >= float(duration_threshold_min))

    # Hard exclusions
    important &= dur >= 1.0                       # not transient
    important &= sev_up != 'INFO'                 # not manual stops

    trig_up = df['Trigger name'].astype(str).str.upper()
    for pat in patterns:
        important &= ~trig_up.str.contains(pat.upper(), na=False)

    return df[important].sort_values('Activated')


def _chart_alarm_pie_by_trigger(alarms_df, top_n=12):
    """
    Pie chart of production_dedup grouped by normalised trigger name
    (top_n + 'Other'). Each slice shows count and percent; full names go in
    a legend on the right. Mirrors the customer-supplied pie example.
    """
    fig, ax = plt.subplots(figsize=(11, 4.5))
    if alarms_df is None or alarms_df.empty:
        ax.text(0.5, 0.5, 'No production alarms in period',
                ha='center', va='center', transform=ax.transAxes,
                color='#6B7A8D')
        ax.axis('off')
        return fig

    df = alarms_df.copy()
    if 'is_excluded' in df.columns:
        df = df[~df['is_excluded'].astype(bool)]
    if df.empty:
        ax.text(0.5, 0.5, 'No production alarms outside exclusion windows',
                ha='center', va='center', transform=ax.transAxes,
                color='#6B7A8D')
        ax.axis('off')
        return fig

    name_col = 'trigger_norm' if 'trigger_norm' in df.columns else 'Trigger name'
    counts = df[name_col].astype(str).value_counts()
    total = int(counts.sum())
    if len(counts) > top_n:
        head = counts.head(top_n)
        other = int(counts.iloc[top_n:].sum())
        counts = pd.concat([head, pd.Series({'Other': other})])

    # Cap trigger label length for the chart slices; legend gets full text
    short_labels = [(s[:40] + '…') if len(s) > 42 else s for s in counts.index]
    pct = counts / total * 100.0

    # Subtle colour palette: tab20 has enough distinct colours for top_n+1
    cmap = plt.get_cmap('tab20')
    colors_ = [cmap(i % 20) for i in range(len(counts))]

    # autopct=None — we annotate manually to combine count + percent
    wedges, _ = ax.pie(
        counts.values, labels=None, colors=colors_,
        startangle=90, counterclock=False,
        wedgeprops=dict(linewidth=0.5, edgecolor='white'),
    )
    # Annotate the big slices (≥ 1.5%) with "count (pct%)" outside the wedge
    for w, lab, c, p in zip(wedges, short_labels, counts.values, pct):
        if p < 1.5:
            continue
        ang = (w.theta2 + w.theta1) / 2.0
        x = 1.10 * np.cos(np.deg2rad(ang))
        y = 1.10 * np.sin(np.deg2rad(ang))
        ax.text(x, y, f'{int(c)} ({p:.2f}%)',
                ha='center' if abs(x) < 0.2 else ('left' if x > 0 else 'right'),
                va='center', fontsize=7, color='#1A2B45')

    ax.set_title('Production Alarm Distribution by Trigger Type',
                 fontsize=10, fontweight='bold', color='#1A2B45',
                 loc='left')
    # Legend on the right with full trigger names
    full_labels = [str(s)[:70] for s in counts.index]
    ax.legend(wedges, full_labels, title='Trigger name',
              loc='center left', bbox_to_anchor=(1.05, 0.5),
              fontsize=6.5, title_fontsize=7, frameon=False)
    fig.tight_layout()
    return fig


# Categorical availability heatmap (matches customer template)
HEATMAP_STATUS_COLORS = {
    'AVAILABLE':       '#5BBE57',   # green
    'AVAIL_WITH_ALRM': '#F2A742',   # orange
    'PARTIAL':         '#F7E04D',   # yellow
    'FAULT':           '#E84C3D',   # red
}
HEATMAP_STATUS_LABELS = {
    'AVAILABLE':       'System available',
    'AVAIL_WITH_ALRM': 'System available with alarms',
    'PARTIAL':         'System partially available',
    'FAULT':           'System Fault',
}
_HEATMAP_STATUS_CODE = {
    'AVAILABLE': 0, 'AVAIL_WITH_ALRM': 1, 'PARTIAL': 2, 'FAULT': 3,
}


def categorize_container_day_status(avail_pct, alarm_count, fault_pct=None,
                                      full_threshold=99.0, cycle_done=None):
    """
    Bucket a container-day record into one of the four customer-facing states.

      • AVAILABLE       fully available (avail_pct ≥ full_threshold), no alarms
      • AVAIL_WITH_ALRM fully available but with ≥1 production alarm that day
      • PARTIAL         worked at least a little but not fully (0 < avail_pct
                        < full_threshold)
      • FAULT           did NOT operate at all (avail_pct == 0) AND there is
                        error evidence (a production alarm or time spent in a
                        fault state). 0% with no error evidence is treated as
                        AVAILABLE — it is missing data or a planned stop, not a
                        confirmed fault.

    `cycle_done` (optional): when True, the block completed at least one full
    charge/discharge cycle that day, which is direct evidence it was operating.
    That overrides the availability-time buckets — a completed cycle is marked
    AVAILABLE (orange if alarms) regardless of brief working-status dips or
    data gaps. Callers that don't supply it (e.g. Bukhara) keep the original
    time-based behaviour.

    STANDBY counts as available (it is in AVAILABLE_STATES upstream). A NaN /
    None avail_pct is treated as AVAILABLE — "no data" defaults to nominal so
    empty days don't blush red.
    """
    if cycle_done:
        return 'AVAIL_WITH_ALRM' if (alarm_count and alarm_count > 0) else 'AVAILABLE'
    if avail_pct is None or pd.isna(avail_pct):
        return 'AVAILABLE'
    if avail_pct >= full_threshold:
        return 'AVAIL_WITH_ALRM' if (alarm_count and alarm_count > 0) else 'AVAILABLE'
    if avail_pct > 0:
        return 'PARTIAL'
    # avail_pct == 0 → did not operate at all today
    has_error_evidence = (alarm_count and alarm_count > 0) or \
                         (fault_pct is not None and pd.notna(fault_pct) and fault_pct > 0)
    return 'FAULT' if has_error_evidence else 'AVAILABLE'


# Severity ranking used to pick the dominant cause of an outage. Fault / Critical
# events are what actually take a container off-line, so they outrank Warnings.
_SEVERITY_RANK = {'CRITICAL': 3, 'FAULT': 2, 'WARNING': 1, 'INFO': 0}


def build_unavailability_reasons(container_day_status_df, alarms_df=None,
                                  include_partial=False):
    """
    For every container-day the heatmap paints red (FAULT) — i.e. the container
    was genuinely unavailable — find the dominant alarm cause and group runs of
    consecutive days into incidents.

    The dominant cause is the classified reason (`cls_reason`) with the greatest
    total fault duration in that container over the incident window, preferring
    Critical/Fault severity over Warnings. This is the "why was it red" answer
    the customer asks for next to section 4.4.

    Args:
      container_day_status_df: output of `build_container_day_status`
                               (date, block_id, container_id, status, ...).
      alarms_df: production_dedup DataFrame (classified). Block/container are
                 parsed from `Element` via the "BB.CC" regex. `is_excluded`
                 rows are dropped so exclusion-window events don't masquerade
                 as the cause.
      include_partial: also report PARTIAL (yellow) days when True. Default
                       False — only genuine FAULT days, matching the request.

    Returns:
      DataFrame[block_id, container_id, status, date_from, date_to, n_days,
                cause, subsystem, severity, fault_events, downtime_h]
      sorted by status (FAULT first) then date_from. `status` is 'FAULT' or
      'PARTIAL'. Empty frame when nothing was unavailable.
    """
    if container_day_status_df is None or container_day_status_df.empty:
        return pd.DataFrame()

    target = {'FAULT'} | ({'PARTIAL'} if include_partial else set())
    bad = container_day_status_df[
        container_day_status_df['status'].isin(target)
    ].copy()
    if bad.empty:
        return pd.DataFrame()
    bad['date'] = pd.to_datetime(bad['date']).dt.date

    # ── Index fault-class alarms by (block, container, date) ──────────────────
    a = pd.DataFrame()
    if alarms_df is not None and not alarms_df.empty:
        a = alarms_df.copy()
        if 'is_excluded' in a.columns:
            a = a[~a['is_excluded'].astype(bool)]
        a['date'] = pd.to_datetime(a.get('Activated'), errors='coerce').dt.date
        elem = a['Element'].astype(str).str.extract(r'(\d+)\.(\d+)')
        a['block_id']     = pd.to_numeric(elem[0], errors='coerce')
        a['container_id'] = pd.to_numeric(elem[1], errors='coerce')
        a = a.dropna(subset=['date', 'block_id', 'container_id'])
        if not a.empty:
            a['block_id']     = a['block_id'].astype(int)
            a['container_id'] = a['container_id'].astype(int)
            a['duration_min'] = pd.to_numeric(
                a.get('duration_min'), errors='coerce').fillna(0.0)
            sev = a.get('cls_severity', pd.Series('', index=a.index))
            a['sev_up']   = sev.astype(str).str.upper()
            a['sev_rank'] = a['sev_up'].map(_SEVERITY_RANK).fillna(0)
            if 'cls_reason' not in a.columns:
                a['cls_reason'] = 'Unclassified'
            if 'cls_subsystem' not in a.columns:
                a['cls_subsystem'] = 'OTHER'

    def _dominant_cause(block, container, d_from, d_to):
        """Return (cause, subsystem, severity, n_fault_events, downtime_h)."""
        if a.empty:
            return ('Unclassified', 'OTHER', '', 0, 0.0)
        sub = a[(a['block_id'] == block) & (a['container_id'] == container)
                & (a['date'] >= d_from) & (a['date'] <= d_to)]
        # Prefer the fault/critical events; fall back to any alarm in window
        faults = sub[sub['sev_rank'] >= _SEVERITY_RANK['FAULT']]
        pick = faults if not faults.empty else sub
        if pick.empty:
            return ('Unclassified', 'OTHER', '', 0, 0.0)
        # Rank reasons by total duration, then count; tie-break on severity
        ranked = (pick.groupby(['cls_reason', 'cls_subsystem'])
                      .agg(total_dur=('duration_min', 'sum'),
                           n=('duration_min', 'size'),
                           sev=('sev_rank', 'max'))
                      .reset_index()
                      .sort_values(['total_dur', 'n', 'sev'],
                                   ascending=False))
        top = ranked.iloc[0]
        sev_label = {3: 'Critical', 2: 'Fault',
                     1: 'Warning', 0: ''}.get(int(top['sev']), '')
        return (top['cls_reason'], top['cls_subsystem'], sev_label,
                int(len(faults)), float(pick['duration_min'].sum()) / 60.0)

    # ── Group consecutive same-status days per (block, container) into
    #    incidents. Grouping by status keeps a red outage and an adjacent
    #    yellow partial-availability spell as separate, correctly-labelled rows.
    incidents = []
    for (block, container, status), g in bad.groupby(
            ['block_id', 'container_id', 'status']):
        days = sorted(g['date'].unique())
        run_start = prev = days[0]
        for d in days[1:] + [None]:
            if d is not None and (d - prev).days == 1:
                prev = d
                continue
            cause, subsystem, severity, n_evt, downtime_h = _dominant_cause(
                int(block), int(container), run_start, prev)
            incidents.append({
                'block_id':     int(block),
                'container_id': int(container),
                'status':       status,
                'date_from':    run_start,
                'date_to':      prev,
                'n_days':       (prev - run_start).days + 1,
                'cause':        cause,
                'subsystem':    subsystem,
                'severity':     severity,
                'fault_events': n_evt,
                'downtime_h':   round(downtime_h, 1),
            })
            if d is not None:
                run_start = prev = d

    out = pd.DataFrame(incidents)
    if not out.empty:
        # FAULT incidents first (worst), then by start date
        out['_sev'] = (out['status'] == 'FAULT').map({True: 0, False: 1})
        out = (out.sort_values(['_sev', 'date_from', 'block_id', 'container_id'])
                  .drop(columns='_sev')
                  .reset_index(drop=True))
    return out


def build_container_day_status(container_day_avail_df, alarms_df=None,
                                 full_threshold=99.0):
    """
    Build the long-format frame consumed by
    `_chart_availability_heatmap_categorical`.

    Args:
      container_day_avail_df: DataFrame[date, block_id, container_id,
                                          availability_pct] and, optionally,
                                          fault_pct (% of the day in a fault
                                          state) — used to confirm a red FAULT
                                          day actually had error evidence.
      alarms_df: optional production_dedup DataFrame. Per-container alarm
                  counts (parsed from `Element` via "BB.CC" regex) drive the
                  green vs orange split and the FAULT error-evidence test.
                  `is_excluded` rows are dropped.

    Returns:
      DataFrame[date, block_id, container_id, status, status_code,
                alarm_count]
    """
    df = container_day_avail_df.copy()
    df['date'] = pd.to_datetime(df['date']).dt.date

    alarm_counts: dict = {}
    if alarms_df is not None and not alarms_df.empty:
        a = alarms_df.copy()
        if 'is_excluded' in a.columns:
            a = a[~a['is_excluded'].astype(bool)]
        a['date'] = pd.to_datetime(a.get('Activated'), errors='coerce').dt.date
        elem = a['Element'].astype(str).str.extract(r'(\d+)\.(\d+)')
        a['block_id']     = pd.to_numeric(elem[0], errors='coerce')
        a['container_id'] = pd.to_numeric(elem[1], errors='coerce')
        sub = a.dropna(subset=['date', 'block_id', 'container_id'])
        if not sub.empty:
            sub = sub.assign(
                block_id=sub['block_id'].astype(int),
                container_id=sub['container_id'].astype(int),
            )
            alarm_counts = sub.groupby(['date', 'block_id',
                                         'container_id']).size().to_dict()

    has_fault_col = 'fault_pct' in df.columns
    has_cycle_col = 'cycle_done' in df.columns
    rows = []
    for _, r in df.iterrows():
        d = r['date']
        b = int(r['block_id'])
        c = int(r['container_id'])
        ac = int(alarm_counts.get((d, b, c), 0))
        status = categorize_container_day_status(
            r.get('availability_pct'), ac,
            fault_pct=(r.get('fault_pct') if has_fault_col else None),
            full_threshold=full_threshold,
            cycle_done=(bool(r.get('cycle_done')) if has_cycle_col else None),
        )
        rows.append({
            'date':         d,
            'block_id':     b,
            'container_id': c,
            'status':       status,
            'status_code':  _HEATMAP_STATUS_CODE[status],
            'alarm_count':  ac,
        })
    return pd.DataFrame(rows)


def _chart_availability_heatmap_categorical(container_day_status_df,
                                              exclusions=None,
                                              container_label='LC',
                                              fig_width=11):
    """
    Container-level categorical availability heatmap matching the customer
    template (4 states, dates on Y, containers on X, legend below).

    `container_label` is the prefix used in column headers. Default 'LC'
    because the data feeding this chart is per-Local-Controller — each LC
    manages 2 BESS containers, so a block has 4 BESS containers but only
    2 LCs. Pass 'BESS' explicitly if a downstream caller really has
    BESS-container granularity.
    """
    import matplotlib.colors as mcolors
    from matplotlib.patches import Patch

    if container_day_status_df is None or container_day_status_df.empty:
        fig, ax = plt.subplots(figsize=(fig_width, 4))
        ax.text(0.5, 0.5, 'No availability data in period',
                ha='center', va='center', transform=ax.transAxes,
                color='#6B7A8D')
        ax.axis('off')
        return fig

    df = container_day_status_df.copy()
    df['date'] = pd.to_datetime(df['date'])
    pivot = df.pivot_table(
        index='date',
        columns=['block_id', 'container_id'],
        values='status_code',
        aggfunc='first',
    )
    pivot = pivot.reindex(sorted(pivot.columns), axis=1).sort_index()
    n_cols = len(pivot.columns)
    n_rows = len(pivot.index)

    fig_h = max(5.5, 0.22 * n_rows + 2.5)
    fig, ax = plt.subplots(figsize=(fig_width, fig_h))

    cmap = mcolors.ListedColormap([
        HEATMAP_STATUS_COLORS['AVAILABLE'],
        HEATMAP_STATUS_COLORS['AVAIL_WITH_ALRM'],
        HEATMAP_STATUS_COLORS['PARTIAL'],
        HEATMAP_STATUS_COLORS['FAULT'],
    ])
    bounds = [-0.5, 0.5, 1.5, 2.5, 3.5]
    norm = mcolors.BoundaryNorm(bounds, cmap.N)
    ax.imshow(pivot.values, aspect='auto', cmap=cmap, norm=norm)

    # X labels — "Block N - BESS1" / "Block N - BESS2", at the TOP of the chart
    col_labels = [f'Block {int(b)} - {container_label}{int(c)}'
                  for b, c in pivot.columns]
    ax.set_xticks(range(n_cols))
    x_fontsize = 4 if n_cols > 80 else (5 if n_cols > 40 else 7)
    ax.set_xticklabels(col_labels, fontsize=x_fontsize, rotation=90)
    ax.xaxis.tick_top()
    ax.xaxis.set_label_position('top')

    # Y labels — "D-Mon" (e.g. "1-Apr")
    def _fmt_date(d):
        ts = pd.Timestamp(d)
        try:
            return ts.strftime('%#d-%b')
        except (ValueError, AttributeError):
            return ts.strftime('%d-%b')
    ylabels = [_fmt_date(d) for d in pivot.index]
    ax.set_yticks(range(n_rows))
    ax.set_yticklabels(ylabels, fontsize=7)

    # Cell-edge grid (crisp Excel-like look matching the picture)
    ax.set_xticks([i - 0.5 for i in range(n_cols + 1)], minor=True)
    ax.set_yticks([i - 0.5 for i in range(n_rows + 1)], minor=True)
    ax.grid(which='minor', color='#222', lw=0.3)
    ax.tick_params(which='minor', bottom=False, left=False)

    # Exclusion overlay (hatching) — same convention as the old % heatmap
    if exclusions:
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
        for yi, day_ts in enumerate(pivot.index):
            day_d = day_ts.date()
            for xi, (blk, _con) in enumerate(pivot.columns):
                for d_from, d_to, blk_set in intervals:
                    if d_from <= day_d <= d_to and (
                            blk_set is None or int(blk) in blk_set):
                        ax.add_patch(plt.Rectangle(
                            (xi - 0.5, yi - 0.5), 1, 1,
                            fill=False, hatch='////',
                            edgecolor='white', linewidth=0))
                        break

    # Legend below the chart
    legend_elements = [
        Patch(facecolor=HEATMAP_STATUS_COLORS['AVAILABLE'],
              label=HEATMAP_STATUS_LABELS['AVAILABLE']),
        Patch(facecolor=HEATMAP_STATUS_COLORS['AVAIL_WITH_ALRM'],
              label=HEATMAP_STATUS_LABELS['AVAIL_WITH_ALRM']),
        Patch(facecolor=HEATMAP_STATUS_COLORS['PARTIAL'],
              label=HEATMAP_STATUS_LABELS['PARTIAL']),
        Patch(facecolor=HEATMAP_STATUS_COLORS['FAULT'],
              label=HEATMAP_STATUS_LABELS['FAULT']),
    ]
    ax.legend(handles=legend_elements,
              loc='upper center', bbox_to_anchor=(0.5, -0.04),
              ncol=4, fontsize=7, frameon=False)
    fig.tight_layout()
    return fig


def _chart_alarm_by_class(alarm_summary):
    by_class = alarm_summary['by_class']
    if by_class.empty:
        fig, ax = plt.subplots(figsize=(11, 3.5))
        ax.text(0.5, 0.5, 'No alarms in period', ha='center', va='center',
                transform=ax.transAxes, color='#6B7A8D')
        ax.axis('off'); return fig
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 3.5))
    ax1.barh(by_class['element_class'], by_class['n_events'],
             color='#0071E3', alpha=0.85)
    for i, v in enumerate(by_class['n_events']):
        ax1.text(v+5, i, str(int(v)), va='center', fontsize=7)
    ax1.set_xlabel('Production events (dedup)', fontsize=8)
    ax1.set_title('Production alarm count by device class', fontsize=9,
                  fontweight='bold', color='#1A2B45')
    ax1.invert_yaxis()
    ax1.spines['top'].set_visible(False); ax1.spines['right'].set_visible(False)
    ax1.grid(axis='x', ls='--', alpha=0.3)

    ax2.barh(by_class['element_class'], by_class['total_duration_h'],
             color='#FF9500', alpha=0.85)
    for i, v in enumerate(by_class['total_duration_h']):
        ax2.text(v+1, i, f'{v:.0f}h', va='center', fontsize=7)
    ax2.set_xlabel('Total downtime (hours)', fontsize=8)
    ax2.set_title('Production alarm duration by device class', fontsize=9,
                  fontweight='bold', color='#1A2B45')
    ax2.invert_yaxis()
    ax2.spines['top'].set_visible(False); ax2.spines['right'].set_visible(False)
    ax2.grid(axis='x', ls='--', alpha=0.3)
    fig.tight_layout()
    return fig


def format_exclusion_narrative(exclusions, default_reason='ongoing construction activities'):
    """
    Render the Availability Exclusions list as the customer-facing narrative
    used in the APRS BESS template:

      "Several zones were temporarily taken out of operation during <month> due
       to <reason>. The shutdown periods were as follows:
         • March 1-3: Zone 8
         • March 5-12: Zone 6"

    Each exclusion contributes one bullet "<MonthName> <D>-<D>: <description>".
    Returns a tuple (lead_paragraph_str, [bullet_str, …]). Empty when no
    exclusions are provided.
    """
    if not exclusions:
        return '', []
    bullets = []
    months_seen = set()
    for exc in exclusions:
        try:
            df_d = pd.to_datetime(exc.get('date_from')).date()
            dt_d = pd.to_datetime(exc.get('date_to')).date()
        except Exception:
            continue
        m_from = df_d.strftime('%B')
        m_to   = dt_d.strftime('%B')
        if m_from == m_to:
            rng = f"{m_from} {df_d.day}-{dt_d.day}"
        else:
            rng = f"{m_from} {df_d.day} - {m_to} {dt_d.day}"
        months_seen.add(m_from)
        desc = (exc.get('description') or '').strip()
        if not desc:
            # Fallback: use affected blocks (e.g. "Zone 8") or the exclusion type
            blk = (exc.get('affected_blocks') or '').strip()
            if blk and blk.lower() not in ('all', 'all blocks'):
                desc = f"Block(s) {blk}"
            else:
                desc = exc.get('exclusion_type', 'unspecified')
        bullets.append(f"{rng}: {desc}")
    month_str = (next(iter(months_seen)) if len(months_seen) == 1
                  else 'the reporting period')
    lead = (f"Several zones were temporarily taken out of operation during "
            f"{month_str} due to {default_reason}. The shutdown periods were "
            f"as follows:")
    return lead, bullets


def _chart_single_daily_bar(daily_kpi, kind, title, color):
    """Single-series daily bar chart for Imported (charge) or Exported
    (discharge) energy. `kind` is 'charge_kwh' or 'discharge_kwh'."""
    fig, ax = plt.subplots(figsize=(11, 3.5))
    if daily_kpi is None or daily_kpi.empty:
        ax.text(0.5, 0.5, 'No data in period', ha='center', va='center',
                transform=ax.transAxes, color='#6B7A8D'); ax.axis('off')
        return fig
    daily = daily_kpi.groupby('date')[kind].sum().reset_index()
    x = list(range(len(daily)))
    ax.bar(x, daily[kind]/1000, 0.7, color=color, alpha=0.85)
    labels = [str(d)[5:] for d in daily['date']]
    step = max(1, len(labels)//12)
    ax.set_xticks(list(range(0, len(labels), step)))
    ax.set_xticklabels([labels[i] for i in range(0, len(labels), step)],
                       fontsize=7, rotation=30)
    ax.set_ylabel('Energy (MWh)', fontsize=8)
    ax.set_title(title, fontsize=10, fontweight='bold', color='#1A2B45')
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    ax.grid(axis='y', ls='--', alpha=0.3)
    fig.tight_layout()
    return fig


def _chart_efc_per_block(cycles_df):
    fig, ax = plt.subplots(figsize=(11, 3.5))
    cycles_df = cycles_df.sort_values('block_id')
    ax.bar(cycles_df['block_id'].astype(str),
           cycles_df['cycle_delta'], color='#34C759', alpha=0.85)
    fleet_avg = cycles_df['cycle_delta'].mean()
    ax.axhline(fleet_avg, color='#1A2B45', ls='--', lw=1.5,
                label=f'Fleet mean {fleet_avg:.1f}')
    for i, v in enumerate(cycles_df['cycle_delta']):
        ax.text(i, v+0.3, f'{v:.1f}', ha='center', fontsize=7)
    ax.set_xlabel('Block', fontsize=8); ax.set_ylabel('Cycles', fontsize=8)
    ax.set_title('Monthly Equivalent Full Cycles (cycle counter delta)',
                 fontsize=10, fontweight='bold', color='#1A2B45')
    ax.legend(fontsize=7)
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    ax.grid(axis='y', ls='--', alpha=0.3)
    fig.tight_layout()
    return fig


# ── REPORT BUILDER ────────────────────────────────────────────────────────────

def _build_cover(story, report_month, site_name, n_blocks, period_str,
                   report_number=None, prepared_by=None, reviewed_by=None):
    story.append(Spacer(1, 30*mm))
    t = Table([
        [Paragraph('BESS OPERATIONS REPORT', ParagraphStyle(
            'CT', fontName='Helvetica-Bold', fontSize=26,
            textColor=WHITE, alignment=TA_CENTER))],
        [Paragraph(report_month, ParagraphStyle(
            'CS', fontName='Helvetica', fontSize=16,
            textColor=colors.HexColor('#8FA3BE'), alignment=TA_CENTER))],
    ], colWidths=[170*mm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), NAVY),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('TOPPADDING', (0,0), (-1,-1), 20),
        ('BOTTOMPADDING', (0,0), (-1,-1), 20),
    ]))
    story.append(t)
    story.append(Spacer(1, 12*mm))
    info = [
        ['Site:',             site_name],
        ['Reporting period:', period_str],
        ['Blocks monitored:', f'{n_blocks} blocks'],
        ['Report generated:', datetime.now().strftime('%d.%m.%Y %H:%M')],
        ['Report type:',      'Monthly Operations & Performance Summary'],
    ]
    if report_number: info.append(['Report No.:',  str(report_number)])
    if prepared_by:   info.append(['Prepared by:', str(prepared_by)])
    if reviewed_by:   info.append(['Reviewed by:', str(reviewed_by)])
    t2 = Table(info, colWidths=[55*mm, 115*mm])
    t2.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), GREY_BG),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 10),
        ('GRID', (0,0), (-1,-1), 0.3, GREY_LINE),
        ('FONT', (0,0), (0,-1), 'Helvetica-Bold'),
    ]))
    story.append(t2)
    story.append(Spacer(1, 8*mm))
    story.append(Paragraph(
        'This report summarises the operation and performance of the site for '
        'the reporting month. All figures are based on the site\'s own '
        'monitoring data — energy meters, state-of-charge and availability '
        'records.',
        STYLE_SMALL))
    story.append(PageBreak())


def generate_bukhara_report(
    site_kpi_path,
    overall_lc_path,
    battery_unit_path,
    meter_daily_path,
    alarm_path,
    output_path,
    site_name='Bukhara BESS',
    availability_path=None,        # optional — 77MB file, only needed for
                                    # detailed PCS-level fault correlation
    progress_callback=None,
    # ── Sungrow-style report extensions ──
    alarm_classifications_path=None,
    project_details=None,          # dict[label -> value] e.g. {'Capacity': '63 MW / 126 MWh'}
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
    cycles_accum_avg=None,         # fleet avg accumulative cycles (from history)
    pm_activities=None,            # list of strings
    cm_activities=None,            # list of strings (auto-generated if None)
    safety_incidents=None,         # list of dicts
    site_visits=None,              # list of strings
    recommendations=None,          # list of strings
    planned_next_period=None,      # list of strings
    history_records=None,          # list of past-month dicts for 4.3 comparison
    history_path=None,             # per-site history JSON (defaults to Bukhara-only file)
    annexes=None,                  # list of strings appended to Section 8
    output_format='pdf',           # 'pdf', 'docx', or 'both'
    exclusions=None,               # list of dicts from availability_service.get_exclusions()
):
    def log(msg):
        if progress_callback: progress_callback(msg)
        print(f"[Bukhara Report] {msg}")

    log("Loading site KPI (5-min)...")
    site_kpi = load_site_kpi_5min(site_kpi_path)

    log("Loading LC daily energy table...")
    lc_daily = load_lc_daily_energy(overall_lc_path)

    log("Loading per-block 5-min data...")
    block_5min = load_block_5min_data(overall_lc_path)

    log("Loading Battery Unit monthly cycle deltas...")
    cycles = load_battery_unit_monthly_cycles(battery_unit_path)

    block_avail = None
    if availability_path:
        log("Loading per-block availability statuses (optional)...")
        try:
            block_avail = load_block_availability_statuses(availability_path)
        except Exception as e:
            log(f"  (skipping — could not load availability file: {e})")

    log("Loading POI meter daily totals...")
    meter_daily = load_meter_daily(meter_daily_path)

    log("Loading alarms (with dedup)...")
    alarms = load_alarms(alarm_path)

    log("Computing daily block KPIs...")
    daily_kpi = calc_daily_block_kpis(
        lc_daily, block_5min, cycles, meter_daily, alarms
    )

    log("Detecting anomalies...")
    anomalies = detect_anomalies(daily_kpi)

    log("Loading alarm classifications...")
    classifications = load_alarm_classifications(alarm_classifications_path)
    alarms = apply_alarm_classifications(alarms, classifications)

    # Tag alarms whose Activated timestamp + block falls inside an exclusion
    # window. Adds is_excluded / excluded_by columns. No-op if exclusions=None.
    alarms = tag_alarms_with_exclusions(alarms, exclusions or [])

    log("Summarising alarms...")
    alarm_sum = summarize_alarms(alarms)

    log("Computing SOC / SOH from site KPI...")
    socsoh = calc_soc_soh(site_kpi)

    # ── Pre-compute exclusion hours (if any) so they can be applied below ─
    # Block list and dates come from the 5-min frames so the period exactly
    # matches what plant-availability is calculated on; this keeps the cap
    # (excluded ≤ plant_outage) honest even when exclusions straddle the
    # month boundary.
    excl_result = None
    if exclusions:
        log(f"Applying {len(exclusions)} availability exclusion(s)...")
        from services.availability_service import calculate_plant_excluded_hours
        block_ids = sorted(block_5min.keys()) if block_5min else []
        if block_5min and block_ids:
            first_ts = pd.to_datetime(block_5min[block_ids[0]]['Datetime'])
            all_dates = sorted(set(first_ts.dt.date.dropna()))
        else:
            all_dates = []
        excl_result = calculate_plant_excluded_hours(
            exclusions, all_dates, block_ids
        )

    excluded_hours_value = (excl_result['excluded_hours']
                             if excl_result else 0.0)

    # Container-day status frame for the categorical heatmap. Bukhara's raw
    # working_status is at block level, so both BESS1 and BESS2 of a block
    # share the same availability % — but per-container alarms (parsed from
    # the Element field) still split orange vs green meaningfully.
    container_day_status = pd.DataFrame()
    cda_rows = []
    for b, df_b in (block_5min or {}).items():
        if df_b is None or df_b.empty or 'working_status' not in df_b.columns:
            continue
        s = df_b.copy()
        _st = s['working_status'].astype(str)
        s['avail'] = ~_st.isin(FAULT_SHUTDOWN_STATES)   # contractual: not-faulted = available
        s['fault'] = _st.isin(FAULT_SHUTDOWN_STATES)
        daily = s.groupby('Date_only').agg(
            avail=('avail', lambda x: x.mean() * 100),
            fault=('fault', lambda x: x.mean() * 100))
        for d, row in daily.iterrows():
            for c in (1, 2):
                cda_rows.append({
                    'date': d, 'block_id': int(b), 'container_id': c,
                    'availability_pct': float(row['avail']),
                    'fault_pct': float(row['fault']),
                })
    if cda_rows:
        container_day_status = build_container_day_status(
            pd.DataFrame(cda_rows),
            alarms_df=alarms.get('production_dedup'),
        )

    log("Computing plant-level hours availability...")
    plant_avail = calc_plant_hours_availability(
        block_5min,
        plant_capacity_mw=plant_capacity_mw,
        per_block_capacity_mw=per_block_capacity_mw,
        redundancy_threshold_pct=redundancy_threshold_pct,
        contractual_plant_capacity_mw=contractual_plant_capacity_mw,
        excluded_hours=excluded_hours_value,
    )

    # Build a per-reason summary used by auto-generated Corrective Maintenance
    p = alarms.get('production_dedup', pd.DataFrame())
    if not p.empty and 'cls_reason' in p.columns:
        cls_summary = (p.groupby(['cls_reason', 'cls_resolution'])
                       .size().reset_index(name='n_events')
                       .sort_values('n_events', ascending=False))
    else:
        cls_summary = pd.DataFrame()

    # Period stats
    dates = sorted(daily_kpi['date'].unique())
    if not dates:
        raise RuntimeError("No valid days found in the data")
    period_str = f"{dates[0].strftime('%d %B %Y')} — {dates[-1].strftime('%d %B %Y')}"
    report_month = dates[0].strftime('%B %Y')
    n_blocks = daily_kpi['block'].nunique()

    # Fleet headline KPIs (across VALID days only)
    valid = daily_kpi[daily_kpi['quality_flag'] == 'VALID']
    total_charge_mwh    = daily_kpi['charge_kwh'].sum() / 1000
    total_discharge_mwh = daily_kpi['discharge_kwh'].sum() / 1000
    fleet_rte = (total_discharge_mwh / total_charge_mwh * 100) if total_charge_mwh > 0 else 0
    fleet_availability_container = valid['availability_pct'].mean()
    total_efc_fleet    = cycles['cycle_delta'].sum()
    avg_efc_per_block  = cycles['cycle_delta'].mean()
    days_excluded = (daily_kpi['quality_flag'] == 'EXCLUDED').sum() / max(n_blocks, 1)
    n_prod_alarms = len(alarms['production_dedup'])
    n_warn_persistent = len(alarms['warning_persistent'])
    n_warn_transient  = len(alarms['warning_transient'])

    # How many of those fell inside an availability exclusion window —
    # they are still listed (with a note) but excluded from the headline
    # tally. Zero when no exclusions were supplied.
    def _excluded_count(df):
        if df is None or df.empty or 'is_excluded' not in df.columns:
            return 0
        return int(df['is_excluded'].sum())
    n_prod_alarms_excluded     = _excluded_count(alarms.get('production_dedup'))
    n_warn_persistent_excluded = _excluded_count(alarms.get('warning_persistent'))
    n_prod_alarms_effective     = max(0, n_prod_alarms - n_prod_alarms_excluded)
    n_warn_persistent_effective = max(0, n_warn_persistent - n_warn_persistent_excluded)
    avg_soc_pct = socsoh.get('avg_soc_pct') or 0
    avg_soh_pct = socsoh.get('avg_soh_pct') or 0
    n_anomalies = int(anomalies['anomaly_flag'].sum()) if not anomalies.empty else 0

    # Monthly comparison table (auto-load history if not provided)
    if history_records is None:
        history_records = load_history_records(history_path or DEFAULT_HISTORY_PATH_BUKHARA)
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

    output_paths = []
    if output_format in ('pdf', 'both'):
        log("Building PDF...")
        _build_bukhara_pdf_internal(output_path, dict(locals()))
        output_paths.append(output_path)

    if output_format in ('docx', 'both'):
        docx_path = output_path.rsplit('.', 1)[0] + '.docx'
        log("Building DOCX...")
        _build_bukhara_docx(docx_path, dict(locals()))
        output_paths.append(docx_path)
        output_path = docx_path  # so the return path is informative

    # Persist this month's KPIs so next run's monthly-comparison chart works
    try:
        path = save_history_record(current_month_record,
                                   history_path or DEFAULT_HISTORY_PATH_BUKHARA)
        log(f"Saved month record to {path}")
    except Exception as e:
        log(f"Note: could not save history record: {e}")

    return output_paths if len(output_paths) > 1 else output_path


def _build_bukhara_pdf_internal(output_path, _ctx):
    """Wraps the original ReportLab PDF builder, fed by a context dict from
    the enclosing generate_bukhara_report() local scope."""
    g = _ctx
    # Recover variables we need (most carry through directly)
    site_name = g['site_name']; report_month = g['report_month']
    n_blocks = g['n_blocks']; period_str = g['period_str']
    project_details = g.get('project_details')
    default_cycle_target = g.get('default_cycle_target', 1)
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
    plant_avail = g.get('plant_avail'); cycles_accum_avg = g.get('cycles_accum_avg')
    scheduled_unavail_hours = g.get('scheduled_unavail_hours', 0.0)
    cls_summary = g['cls_summary']; daily_kpi = g['daily_kpi']
    monthly_compare = g['monthly_compare']; cycles = g['cycles']
    anomalies = g['anomalies']; alarms = g['alarms']; alarm_sum = g['alarm_sum']
    pm_activities = g.get('pm_activities'); cm_activities = g.get('cm_activities')
    safety_incidents = g.get('safety_incidents'); site_visits = g.get('site_visits')
    recommendations = g.get('recommendations'); planned_next_period = g.get('planned_next_period')
    annexes = g.get('annexes'); rte_status = 'above' if fleet_rte >= 85 else 'below'
    avail_status = 'above' if fleet_availability_container >= 95 else 'below'

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
        ['Project name',    site_name],
        ['Reporting period', period_str],
        ['OEM',             'Sungrow Power Supply Co. Ltd.'],
        ['Number of blocks', f'{n_blocks} blocks'],
    ]
    if project_details:
        for k, v in project_details.items():
            # ReportLab Paragraph interprets & as entity start — escape it
            safe_k = str(k).replace('&', '&amp;')
            safe_v = str(v).replace('&', '&amp;')
            project_rows.append([safe_k, safe_v])
    story.append(_styled_table(
        ['Parameter', 'Value'], project_rows,
        col_widths=[60*mm, 110*mm]
    ))
    story.append(_hr())

    # ── 2. SUMMARY ───────────────────────────────────────────────────────
    story.append(_section('2.  Summary', ''))
    story.append(Spacer(1, 3*mm))
    avail_status = 'above' if fleet_availability_container >= 95 else 'below'
    rte_status   = 'above' if fleet_rte >= 85 else 'below'
    summary_text = (
        f"During the reporting period <b>{period_str}</b>, the {site_name} fleet "
        f"({n_blocks} blocks) delivered <b>{total_discharge_mwh:,.1f} MWh</b> of "
        f"discharge energy on <b>{total_charge_mwh:,.1f} MWh</b> of charge "
        f"energy. Site round-trip efficiency was <b>{fleet_rte:.2f}%</b> "
        f"({rte_status} the 85% target). "
    )
    if plant_avail and plant_avail.get('plant_availability_pct') is not None:
        plant_avail_pct = plant_avail['plant_availability_pct']
        summary_text += (
            f"Plant-level availability (redundancy-based) was "
            f"<b>{plant_avail_pct:.2f}%</b>; "
            f"container-level availability was <b>{fleet_availability_container:.2f}%</b>. "
        )
    else:
        summary_text += (
            f"Container-level availability was <b>{fleet_availability_container:.2f}%</b> "
            f"({avail_status} the 95% target). "
        )
    summary_text += (
        f"Average state of charge: <b>{avg_soc_pct:.2f}%</b>. "
        f"Average state of health: <b>{avg_soh_pct:.2f}%</b>. "
        f"<b>{total_efc_fleet:.0f}</b> equivalent full cycles were completed "
        f"across the fleet (avg {avg_efc_per_block:.1f} per block, "
        f"{(avg_efc_per_block/600*100):.1f}% of the 600/year contractual target). "
        f"<b>{days_excluded:.1f}</b> day(s) per block (fleet average) were "
        f"flagged as EXCLUDED and removed from RTE / availability calculations."
    )
    story.append(Paragraph(summary_text, STYLE_BODY))
    story.append(_hr())

    # ── 3. SERVICES PROVISION ────────────────────────────────────────────
    story.append(_section('3.  Services Provision', ''))
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph('<b>3.1  Preventative Maintenance (PM)</b>', STYLE_H3))
    if pm_activities:
        for act in pm_activities:
            story.append(Paragraph(f'•  {act}', STYLE_BODY))
    else:
        story.append(Paragraph('No PM activities in the reporting period.',
                                STYLE_BODY))
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph('<b>3.2  Corrective Maintenance</b>', STYLE_H3))
    if cm_activities:
        for act in cm_activities:
            story.append(Paragraph(f'•  {act}', STYLE_BODY))
    else:
        # Auto-generated from alarm classification
        if cls_summary is not None and not cls_summary.empty:
            top_cm = cls_summary.head(5)
            story.append(Paragraph(
                'Automated summary based on alarm classification — recurring '
                'corrective items observed this period:', STYLE_BODY))
            cm_rows = [[r['cls_reason'], str(int(r['n_events'])),
                         r['cls_resolution'] or '—']
                        for _, r in top_cm.iterrows()]
            story.append(_styled_table(
                ['Item', 'Occurrences', 'Resolution applied'],
                cm_rows,
                col_widths=[70*mm, 30*mm, 70*mm]
            ))
        else:
            story.append(Paragraph('No corrective maintenance activities recorded.',
                                    STYLE_BODY))
    story.append(_hr())

    # ── 4. PLANT PERFORMANCE ─────────────────────────────────────────────
    story.append(_section("4.  Plant's Performance", ''))
    story.append(Spacer(1, 3*mm))

    # 4.1 Plant Performance Data
    story.append(Paragraph('<b>4.1  Plant Performance Data</b>', STYLE_H3))
    story.append(Spacer(1, 2*mm))
    story.append(_styled_table(
        ['No', 'Item', 'Total (MWh)'],
        [
            ['1', 'Imported Energy (Charge)', f'{total_charge_mwh:,.2f}'],
            ['2', 'Exported Energy (Discharge)', f'{total_discharge_mwh:,.2f}'],
        ],
        col_widths=[20*mm, 110*mm, 40*mm]
    ))
    story.append(Spacer(1, 4*mm))

    # Daily charge/discharge chart
    story.append(_fig_to_image(_chart_single_daily_bar(
        daily_kpi, 'charge_kwh', 'Daily Imported Power', '#0071E3'), 170, 60))
    story.append(Paragraph('Graph 1: Daily Imported Power (MWh)', STYLE_CAP))
    story.append(Spacer(1, 2*mm))
    story.append(_fig_to_image(_chart_single_daily_bar(
        daily_kpi, 'discharge_kwh', 'Daily Exported Power', '#AF52DE'), 170, 60))
    story.append(Paragraph('Graph 2: Daily Exported Power (MWh)', STYLE_CAP))
    story.append(Spacer(1, 3*mm))

    # Number of cycles
    story.append(Paragraph('<b>Number of Cycles</b>', STYLE_H3))
    fleet_avg_cycles = avg_efc_per_block
    yt = float(yearly_cycle_target) or 365.0
    accum_lifetime = cycles_accum_avg if cycles_accum_avg else fleet_avg_cycles
    annual_accum = fleet_avg_cycles
    try:
        if history_records:
            recent = sorted(
                [r for r in history_records if r.get('cycles_total') is not None],
                key=lambda r: str(r.get('month','')))[-11:]
            annual_accum = sum(float(r.get('cycles_total') or 0)
                                for r in recent) + fleet_avg_cycles
    except Exception:
        pass
    cycles_table_rows = [
        ['1', 'Number of Cycles in Reported Month',
         f'{fleet_avg_cycles:.1f}',
         f'{(fleet_avg_cycles/yt*100):.2f}%'],
        ['2', 'Accumulative Number of Cycles in one Year',
         f'{annual_accum:.1f}',
         f'{(annual_accum/yt*100):.2f}%'],
        ['3', 'Accumulative Number of Cycles',
         f'{accum_lifetime:.1f}',
         f'{(accum_lifetime/yt*100):.2f}%'],
    ]
    story.append(_styled_table(
        ['No', 'Item', 'Total', f'% of yearly cycles ({yt:.0f})'],
        cycles_table_rows,
        col_widths=[15*mm, 95*mm, 30*mm, 30*mm]
    ))
    story.append(Spacer(1, 3*mm))

    # Cycles per block
    story.append(Paragraph('<b>Cycles Block-wise</b>', STYLE_H3))
    block_cycle_rows = []
    for _, r in cycles.sort_values('block_id').iterrows():
        b = int(r['block_id'])
        # find daily totals for this block
        chg = daily_kpi[daily_kpi['block'] == b]['charge_kwh'].sum()
        dis = daily_kpi[daily_kpi['block'] == b]['discharge_kwh'].sum()
        block_cycle_rows.append([
            f'Block {b}',
            f"{r['cycle_end']:.2f}",
            f"{r['cycle_delta']:.2f}",
            f"{chg/1000:,.1f}",
            f"{dis/1000:,.1f}",
        ])
    story.append(_styled_table(
        ['Block', 'Accumulative cycles', 'Cycles in period',
         'Charged Energy (MWh)', 'Discharged Energy (MWh)'],
        block_cycle_rows,
        col_widths=[22*mm, 38*mm, 30*mm, 40*mm, 40*mm]
    ))
    story.append(Spacer(1, 3*mm))
    story.append(_fig_to_image(_chart_efc_per_block(cycles), 170, 60))
    story.append(Paragraph('Graph 3: Monthly EFC per block (cycle counter delta)',
                            STYLE_CAP))
    story.append(Spacer(1, 3*mm))

    # Scheduled / unscheduled availability hours (Sungrow style)
    story.append(Paragraph('<b>Availability Hours (plant-level redundancy method)</b>',
                            STYLE_H3))
    if plant_avail:
        sched = plant_avail['scheduled_hours']
        plant_out = plant_avail.get('plant_outage_hours')
        cont_out  = plant_avail['container_outage_hours']
        rows = [
            ['Scheduled hours',         f'{sched:,.1f} h'],
            ['Scheduled unavailability (PM)', f'{scheduled_unavail_hours:,.1f} h'],
            ['Unscheduled outage (container-aggregate)',
                f'{cont_out:,.1f} h ({cont_out/sched*100:.2f}%)'],
            ['Plant-level outage (redundancy adjusted)',
                f'{plant_out:,.1f} h ({plant_out/sched*100:.2f}%)'
                if plant_out is not None else
                'n/a (plant capacity not configured)'],
        ]
        if plant_avail.get('plant_availability_pct') is not None:
            rows.append([
                '<b>Plant-level availability</b>',
                f"<b>{plant_avail['plant_availability_pct']:.2f}%</b>"
            ])
        rows.append([
            'Container-level availability (current engine)',
            f"{fleet_availability_container:.2f}%"
        ])
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
    excl_result_local = g.get('excl_result')
    exclusions_local  = g.get('exclusions') or []
    if exclusions_local and excl_result_local and excl_result_local.get('events'):
        story.append(Paragraph('<b>Scheduled Unavailability</b>', STYLE_H3))
        story.append(Spacer(1, 2*mm))
        lead, bullets = format_exclusion_narrative(exclusions_local)
        if lead:
            story.append(Paragraph(lead, STYLE_BODY))
            for b in bullets:
                story.append(Paragraph(f"•  {b}", STYLE_BODY))
            story.append(Spacer(1, 3*mm))
        story.append(Paragraph('<b>Applied Exclusions (Detail)</b>', STYLE_BODY))
        story.append(Spacer(1, 2*mm))
        total_excl = excl_result_local['excluded_hours']
        applied    = (plant_avail.get('excluded_effective', 0.0)
                       if plant_avail else 0.0)
        summary = (
            f"<b>{len(excl_result_local['events'])}</b> exclusion event(s) "
            f"recorded during the period, totaling "
            f"<b>{total_excl:,.1f} h</b> of plant-wall-clock time "
            f"(weighted by affected blocks). "
            f"<b>{applied:,.1f} h</b> were applied to the availability "
            f"calculation (capped at actual outage hours so the metric "
            f"never credits more than was lost)."
        )
        story.append(Paragraph(summary, STYLE_BODY))
        story.append(Spacer(1, 2*mm))

        # Breakdown by type
        brk_rows = [
            [t, f"{h:.1f} h",
             f"{(h/total_excl*100 if total_excl else 0):.1f}%"]
            for t, h in excl_result_local['breakdown'].items() if h > 0
        ]
        if brk_rows:
            story.append(_styled_table(
                ['Exclusion Type', 'Weighted Hours', '% of Excluded'],
                brk_rows,
                col_widths=[80*mm, 45*mm, 45*mm]
            ))
            story.append(Spacer(1, 2*mm))

        # Per-event detail table
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
        story.append(_styled_table(
            ['Type', 'From', 'To', 'Hours', 'Blocks', 'Description'],
            detail_rows,
            col_widths=[32*mm, 28*mm, 28*mm, 18*mm, 22*mm, 42*mm]
        ))
        story.append(Spacer(1, 4*mm))

    # 4.2 Key Performance Indicators
    story.append(Paragraph('<b>4.2  Key Performance Indicators (KPIs)</b>', STYLE_H3))
    story.append(Spacer(1, 2*mm))
    story.append(_kpi_row([
        (f"{avg_soc_pct:.2f}%",  'Average SOC',  '#0071E3'),
        (f"{avg_soh_pct:.2f}%",  'Average SOH',  '#34C759'),
        (f"{fleet_rte:.2f}%",    'RTE',          '#FF9500'),
    ]))
    story.append(Spacer(1, 4*mm))

    # 4.3 Monthly comparison
    story.append(Paragraph('<b>4.3  Monthly Performance Comparison</b>', STYLE_H3))
    story.append(Spacer(1, 2*mm))
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
            'Only current month available — start storing each monthly run to '
            'build a trend (the report engine has a history hook for this).',
            STYLE_SMALL))
    story.append(Spacer(1, 4*mm))

    # 4.4 System availability (heatmap + RTE per block + anomalies)
    story.append(Paragraph('<b>4.4  System Availability</b>', STYLE_H3))
    story.append(Spacer(1, 2*mm))
    story.append(_fig_to_image(
        _chart_availability_heatmap_categorical(
            container_day_status,
            exclusions=g.get('exclusions'),
            fig_width=11),
        170, 130))
    story.append(Paragraph(
        'Graph 4: Daily availability status per container '
        '(Block N - LC1 / LC2). Green = available, orange = available '
        'with alarms, yellow = partially available, red = system fault. '
        'Hatched cells indicate days inside an availability exclusion window.',
        STYLE_CAP))
    story.append(Spacer(1, 3*mm))
    story.append(_fig_to_image(_chart_daily_rte_per_block(daily_kpi), 170, 70))
    story.append(Paragraph(
        'Graph 5: Daily RTE per block (light lines) and fleet mean (dark line). '
        'Fleet mean target: 85%.', STYLE_CAP))
    story.append(Spacer(1, 3*mm))

    # Anomaly callout
    if not anomalies.empty:
        flagged = anomalies[anomalies['anomaly_flag']].sort_values('rte_z')
        if not flagged.empty:
            story.append(Paragraph(
                f'<b>Block performance anomalies</b> ({len(flagged)} block(s) '
                f'flagged as more than 1σ below fleet mean on RTE or throughput):',
                STYLE_BODY))
            rows = []
            for _, r in flagged.iterrows():
                rows.append([
                    f"Block {int(r['block'])}",
                    f"{r['avg_rte']:.2f}%",
                    f"{r['rte_z']:+.2f}",
                    f"{r['avg_throughput']/1000:,.1f} MWh",
                    f"{r['avg_availability']:.2f}%" if pd.notna(r['avg_availability']) else '—',
                    str(int(r['total_alarms'])),
                    str(r.get('likely_cause', '') or ''),
                ])
            story.append(_styled_table(
                ['Block', 'Avg RTE', 'RTE z-score', 'Avg Throughput',
                 'Avg Availability', 'Total Alarms', 'Likely Cause'],
                rows,
                col_widths=[18*mm, 20*mm, 20*mm, 24*mm, 22*mm, 18*mm, 48*mm]
            ))
    story.append(_hr())

    # ── 5. SYSTEM OPERATION (Faults + Alarms semantic tables) ────────────
    story.append(_section('5.  System Operation', ''))
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph('<b>5.1  Summary of Faults and Alarms</b>', STYLE_H3))
    story.append(Spacer(1, 2*mm))

    # Faults — production events grouped by (cls_reason, cls_subsystem).
    # Rows tagged is_excluded=True are removed here (they happened during a
    # planned exclusion window) and listed separately at the end of 5.1.
    p_dedup_all = alarms.get('production_dedup', pd.DataFrame())
    p_dedup = (p_dedup_all[~p_dedup_all.get('is_excluded', False)]
                if not p_dedup_all.empty and 'is_excluded' in p_dedup_all.columns
                else p_dedup_all)
    if not p_dedup.empty and 'cls_reason' in p_dedup.columns:
        story.append(Paragraph('<b>Faults (Production)</b>', STYLE_BODY))
        if n_prod_alarms_excluded > 0:
            story.append(Paragraph(
                f"<i>{n_prod_alarms_excluded} of {n_prod_alarms} production "
                f"event(s) fell inside availability exclusion windows and are "
                f"listed separately at the end of this section. The grouped "
                f"table below reflects the remaining "
                f"{n_prod_alarms - n_prod_alarms_excluded} event(s).</i>",
                STYLE_SMALL))
            story.append(Spacer(1, 1*mm))
        grouped = (p_dedup
                   .groupby(['cls_subsystem', 'cls_reason', 'cls_resolution'])
                   .agg(occurrences=('Trigger name', 'count'),
                        blocks_affected=('Element',
                            lambda x: ', '.join(sorted(set(
                                str(e).split('.')[0].split()[-1] if pd.notna(e) else ''
                                for e in x
                            )))[:60]),
                        total_hours=('duration_min',
                            lambda x: x.sum()/60.0))
                   .reset_index()
                   .sort_values('occurrences', ascending=False))
        rows = []
        for _, r in grouped.head(15).iterrows():
            rows.append([
                r['cls_subsystem'],
                r['cls_reason'],
                str(int(r['occurrences'])),
                f"{r['total_hours']:.1f} h",
                r['blocks_affected'][:30],
                r['cls_resolution'][:40] if r['cls_resolution'] else '—',
            ])
        story.append(_styled_table(
            ['Sub-system', 'Reason / Cause', 'Occurrences',
             'Total Hours', 'Blocks Affected', 'Resolution'],
            rows,
            col_widths=[24*mm, 38*mm, 22*mm, 22*mm, 30*mm, 34*mm]
        ))
        story.append(Spacer(1, 4*mm))

    # Alarms — persistent warnings, excluded rows filtered out for the same
    # reason (and listed separately below).
    w_pers_all = alarms.get('warning_persistent', pd.DataFrame())
    w_persistent = (w_pers_all[~w_pers_all.get('is_excluded', False)]
                     if not w_pers_all.empty and 'is_excluded' in w_pers_all.columns
                     else w_pers_all)
    if not w_persistent.empty and 'cls_reason' in w_persistent.columns:
        story.append(Paragraph('<b>Alarms (Persistent Warnings)</b>', STYLE_BODY))
        if n_warn_persistent_excluded > 0:
            story.append(Paragraph(
                f"<i>{n_warn_persistent_excluded} of {n_warn_persistent} "
                f"persistent warning(s) fell inside exclusion windows and are "
                f"shown in the informational table at the end of this section.</i>",
                STYLE_SMALL))
            story.append(Spacer(1, 1*mm))
        grouped = (w_persistent
                   .groupby(['cls_subsystem', 'cls_reason'])
                   .agg(occurrences=('Trigger name', 'count'),
                        total_hours=('duration_min',
                            lambda x: x.sum()/60.0))
                   .reset_index()
                   .sort_values('occurrences', ascending=False))
        rows = []
        for _, r in grouped.head(15).iterrows():
            rows.append([
                r['cls_subsystem'],
                r['cls_reason'],
                str(int(r['occurrences'])),
                f"{r['total_hours']:.1f} h",
            ])
        story.append(_styled_table(
            ['Sub-system', 'Reason / Cause', 'Occurrences', 'Total Hours'],
            rows,
            col_widths=[30*mm, 70*mm, 35*mm, 35*mm]
        ))
        story.append(Spacer(1, 4*mm))

    # Events During Exclusion Windows — informational only, drawn from the
    # rows tagged is_excluded=True above. Renders only when ≥1 event exists.
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
        'Graph 6: Production alarm count and total downtime by raw device class. '
        'Used as a cross-check against the semantic classification above.',
        STYLE_CAP))
    story.append(Spacer(1, 3*mm))

    # Trigger-level pie chart (customer-facing distribution view)
    story.append(_fig_to_image(_chart_alarm_pie_by_trigger(p_dedup_all),
                                170, 75))
    story.append(Paragraph(
        'Graph 7: Production alarm distribution by trigger type — top 12 '
        'triggers plus the remaining tail grouped as "Other".',
        STYLE_CAP))
    story.append(Spacer(1, 4*mm))

    # Faults table — customer-template column layout
    important = select_important_alarms(p_dedup_all)
    story.append(Paragraph('<b>Faults</b>', STYLE_BODY))
    story.append(Paragraph(
        f'Critical-severity events and production faults lasting '
        f'≥ {int(IMPORTANT_DURATION_THRESHOLD_MIN)} minutes. Transient '
        f'(&lt; 1 min), Info-tagged (manual stops), Input-dry-node, and '
        f'events inside availability exclusion windows are filtered out.',
        STYLE_SMALL))
    story.append(Spacer(1, 1*mm))
    if important.empty:
        story.append(Paragraph(
            'No critical events during the reporting period.', STYLE_BODY))
    else:
        rows = []
        for i, (_, r) in enumerate(important.head(40).iterrows(), start=1):
            elem = str(r.get('Element', ''))
            m = re.search(r'(\d+)\.\d+', elem)
            blk = m.group(1) if m else ''
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
            ['No.', 'Fault Name', 'Block #', 'Sub-system',
             'Reason / Cause', 'Date / Period', 'Resolution'],
            rows,
            col_widths=[10*mm, 38*mm, 14*mm, 22*mm, 32*mm, 22*mm, 32*mm]
        ))
        if len(important) > 40:
            story.append(Paragraph(
                f'<i>Showing first 40 of {len(important)} important events.</i>',
                STYLE_SMALL))
    story.append(Spacer(1, 3*mm))

    # Alarms table — persistent warnings
    story.append(Paragraph('<b>Alarms</b>', STYLE_BODY))
    w_persistent_all = alarms.get('warning_persistent', pd.DataFrame())
    if w_persistent_all is None or w_persistent_all.empty:
        story.append(Paragraph('No persistent alarms in the reporting period.',
                                STYLE_BODY))
    else:
        w = w_persistent_all.copy()
        if 'is_excluded' in w.columns:
            w = w[~w['is_excluded'].astype(bool)]
        trig_up = w['Trigger name'].astype(str).str.upper()
        for pat in NOISE_TRIGGER_PATTERNS:
            w = w[~trig_up.str.contains(pat.upper(), na=False)]
            trig_up = w['Trigger name'].astype(str).str.upper()
        if w.empty:
            story.append(Paragraph('No persistent alarms (after noise-filter).',
                                    STYLE_BODY))
        else:
            rows = []
            for i, (_, r) in enumerate(
                    w.sort_values('duration_min', ascending=False)
                     .head(25).iterrows(), start=1):
                elem = str(r.get('Element', ''))
                m = re.search(r'(\d+)\.\d+', elem)
                blk = m.group(1) if m else ''
                rows.append([
                    str(i),
                    str(r.get('Trigger name', ''))[:42],
                    blk,
                    str(r.get('cls_subsystem', '') or ''),
                    str(r.get('cls_reason', '') or ''),
                    str(r.get('cls_resolution', '') or '—')[:40],
                ])
            story.append(_styled_table(
                ['No.', 'Fault Name', 'Block #', 'Sub-system',
                 'Reason / Cause', 'Resolution'],
                rows,
                col_widths=[10*mm, 42*mm, 14*mm, 24*mm, 36*mm, 44*mm]
            ))
    story.append(Spacer(1, 4*mm))

    # 5.2 Breakdowns / incidents — derived from longest events
    story.append(Paragraph('<b>5.2  Major Incidents and Breakdowns</b>', STYLE_H3))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(
        'Summary of breakdowns, incidents and their weight affecting '
        'availability.', STYLE_BODY))
    story.append(Spacer(1, 1*mm))
    if breakdown_incidents:
        rows = []
        for i, inc in enumerate(breakdown_incidents, start=1):
            rows.append([
                str(i),
                str(inc.get('incident', ''))[:50],
                str(inc.get('date_time', ''))[:18],
                str(inc.get('breakdown_type', ''))[:24],
                str(inc.get('temporary_solution', ''))[:36],
                str(inc.get('final_solution', ''))[:36],
                str(inc.get('closure_date', ''))[:14],
            ])
        story.append(_styled_table(
            ['No.', 'Breakdown incident', 'Date and time',
             'Breakdown type', 'Temporary solution', 'Final solution',
             'Date of closure'],
            rows,
            col_widths=[10*mm, 32*mm, 22*mm, 24*mm, 30*mm, 30*mm, 22*mm]
        ))
    else:
        story.append(Paragraph(
            'No breakdowns in the reported period.', STYLE_BODY))
        if not alarm_sum['longest'].empty:
            story.append(Paragraph(
                '<i>Longest events (informational):</i>', STYLE_SMALL))
            rows = []
            for _, r in alarm_sum['longest'].iterrows():
                trigger = str(r['Trigger name'])[:45]
                if bool(r.get('is_excluded', False)):
                    excl_lbl = str(r.get('excluded_by', '') or 'exclusion')
                    trigger = f"{trigger}  (during {excl_lbl})"
                rows.append([
                    str(r['Activated'])[:16],
                    str(r['Element'])[:20],
                    trigger,
                    f"{r['duration_min']/60:.1f} h" if pd.notna(r['duration_min']) else '—',
                ])
            story.append(_styled_table(
                ['Activated', 'Element', 'Trigger', 'Duration'], rows,
                col_widths=[34*mm, 30*mm, 80*mm, 26*mm]
            ))
    story.append(_hr())

    # ── 6. SITE OPERATION & FACILITY MANAGEMENT ──────────────────────────
    story.append(_section('6.  Site Operation and Facility Management', ''))
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph('<b>Incidents affecting safety and countermeasures</b>',
                            STYLE_H3))
    if safety_incidents:
        rows = [[s.get('incident',''), s.get('equipment_loss','-'),
                  s.get('weight',''), s.get('countermeasure','')]
                 for s in safety_incidents]
        story.append(_styled_table(
            ['Incident', 'Equipment Loss', 'Weight', 'Countermeasure'],
            rows, col_widths=[60*mm, 35*mm, 35*mm, 40*mm]
        ))
    else:
        story.append(Paragraph(
            'No safety incidents reported in the reporting period.',
            STYLE_BODY))
    story.append(Spacer(1, 4*mm))
    story.append(Paragraph('<b>Site visits and inspections</b>', STYLE_H3))
    if site_visits:
        for v in site_visits:
            story.append(Paragraph(f'•  {v}', STYLE_BODY))
    else:
        story.append(Paragraph(
            'No site visits recorded for the reporting period.', STYLE_BODY))
    story.append(_hr())

    # ── 7. CONCLUSIONS & RECOMMENDATIONS ─────────────────────────────────
    story.append(_section('7.  Conclusions and Recommendations', ''))
    story.append(Spacer(1, 3*mm))

    # Auto-generated conclusions
    auto_conclusions = [
        (f"Fleet round-trip efficiency was <b>{fleet_rte:.2f}%</b> "
         f"({rte_status} the 85% target). "
         f"Container-level availability was <b>{fleet_availability_container:.2f}%</b>."),
        (f"Across {n_blocks} blocks, the fleet delivered "
         f"<b>{total_discharge_mwh:,.1f} MWh</b> of discharge energy on "
         f"<b>{total_charge_mwh:,.1f} MWh</b> of charge, equivalent to "
         f"<b>{total_efc_fleet:.0f} EFC</b> "
         f"(avg {avg_efc_per_block:.1f} per block, "
         f"{(avg_efc_per_block/600*100):.1f}% of the 600/year target)."),
        (f"<b>{n_prod_alarms:,}</b> production-impacting events were recorded "
         f"after deduplication "
         f"(plus {n_warn_persistent:,} persistent warnings, "
         f"{n_warn_transient:,} transient events filtered as noise)."),
    ]
    if n_anomalies:
        auto_conclusions.append(
            f"<b>{n_anomalies}</b> block(s) flagged as underperforming versus "
            f"fleet mean (RTE or throughput z-score &lt; -1)."
        )
    if days_excluded > 0:
        auto_conclusions.append(
            f"<b>{days_excluded:.1f}</b> day(s) per block flagged as EXCLUDED "
            f"(zero throughput on both sides). These have been removed from "
            f"RTE and availability calculations."
        )
    for i, c in enumerate(auto_conclusions, 1):
        story.append(Paragraph(f'{i}.  {c}', STYLE_BODY))
        story.append(Spacer(1, 2*mm))

    # Operator-supplied recommendations
    if recommendations:
        story.append(Spacer(1, 3*mm))
        story.append(Paragraph('<b>Recommendations & Mitigation Strategies</b>',
                                STYLE_H3))
        for rec in recommendations:
            story.append(Paragraph(f'•  {rec}', STYLE_BODY))

    # Planned activities for next period
    if planned_next_period:
        story.append(Spacer(1, 3*mm))
        story.append(Paragraph('<b>Planned Activities for Next Reporting Period</b>',
                                STYLE_H3))
        for p in planned_next_period:
            story.append(Paragraph(f'•  {p}', STYLE_BODY))
    story.append(_hr())

    # ── 8. ANNEXES ───────────────────────────────────────────────────────
    story.append(_section('8.  List of Annexes', ''))
    story.append(Spacer(1, 3*mm))
    annex_lines = [
        'Annex 1: Daily Block KPI table — full month per block (CSV export)',
        'Annex 2: Alarm classification lookup applied to this report (CSV)',
        'Annex 3: Quality-flagged days (EXCLUDED / INCOMPLETE / ESTIMATED)',
        'Annex 4: Top-block performance anomaly z-scores',
    ]
    for line in annex_lines:
        story.append(Paragraph(f'•  {line}', STYLE_BODY))
    if annexes:
        for a in annexes:
            story.append(Paragraph(f'•  {a}', STYLE_BODY))

    story.append(Spacer(1, 6*mm))
    story.append(Paragraph(
        f'Generated by BESS Tracker — Bukhara module  |  '
        f'{datetime.now().strftime("%Y-%m-%d %H:%M")}',
        STYLE_SMALL))

    doc.build(story)
    print(f"[Bukhara Report] PDF saved: {output_path}")

    # Persist this month's KPIs so next run's monthly-comparison chart works
    try:
        path = save_history_record(current_month_record,
                                   g.get('history_path') or DEFAULT_HISTORY_PATH_BUKHARA)
        log(f"Saved month record to {path}")
    except Exception as e:
        log(f"Note: could not save history record: {e}")

    return output_path



def _build_bukhara_docx(output_path, _ctx):
    """Render the same 8-section report as a Word .docx file."""
    from services.docx_renderer import (
        make_doc, add_cover, add_section_banner, add_heading, add_paragraph,
        add_kpi_row, add_styled_table, add_image_from_fig, add_caption,
        add_hr, save_doc,
    )
    import pandas as pd
    g = _ctx
    site_name = g['site_name']; report_month = g['report_month']
    n_blocks = g['n_blocks']; period_str = g['period_str']
    project_details = g.get('project_details'); default_cycle_target = g.get('default_cycle_target', 1)
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
    plant_avail = g.get('plant_avail'); cycles_accum_avg = g.get('cycles_accum_avg')
    scheduled_unavail_hours = g.get('scheduled_unavail_hours', 0.0)
    cls_summary = g['cls_summary']; daily_kpi = g['daily_kpi']
    monthly_compare = g['monthly_compare']; cycles = g['cycles']
    anomalies = g['anomalies']; alarms = g['alarms']; alarm_sum = g['alarm_sum']
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
    project_rows = [['Project name', site_name],
                    ['Reporting period', period_str],
                    ['OEM', 'Sungrow Power Supply Co. Ltd.'],
                    ['Number of blocks', f'{n_blocks} blocks']]
    if project_details:
        for k, v in project_details.items():
            project_rows.append([str(k), str(v)])
    add_styled_table(doc, ['Parameter', 'Value'], project_rows)
    add_hr(doc)

    # 2. Summary
    add_section_banner(doc, '2.  Summary')
    sm = (f"During {period_str}, the {site_name} fleet ({n_blocks} blocks) "
           f"delivered <b>{total_discharge_mwh:,.1f} MWh</b> of discharge on "
           f"<b>{total_charge_mwh:,.1f} MWh</b> of charge. RTE was "
           f"<b>{fleet_rte:.2f}%</b> ({rte_status} the 85% target). ")
    if plant_avail and plant_avail.get('plant_availability_pct') is not None:
        sm += (f"Plant-level availability: <b>{plant_avail['plant_availability_pct']:.2f}%</b>; "
                f"container-level: <b>{fleet_availability_container:.2f}%</b>. ")
        if plant_avail.get('contractual_plant_capacity_mw'):
            sm += (f"Contractual capacity threshold: "
                    f"<b>{plant_avail['contractual_plant_capacity_mw']:.1f} MW</b>. ")
    else:
        sm += f"Container-level availability: <b>{fleet_availability_container:.2f}%</b>. "
    sm += (f"SOC: <b>{avg_soc_pct:.2f}%</b>, SOH: <b>{avg_soh_pct:.2f}%</b>. "
            f"EFC: <b>{total_efc_fleet:.0f}</b> total (avg {avg_efc_per_block:.1f} per block). "
            f"<b>{days_excluded:.1f}</b> EXCLUDED day(s) per block (fleet avg).")
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
            'corrective items observed this period:')
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
    hr_local = g.get('history_records')
    try:
        if hr_local:
            recent = sorted(
                [r for r in hr_local if r.get('cycles_total') is not None],
                key=lambda r: str(r.get('month','')))[-11:]
            annual_accum = sum(float(r.get('cycles_total') or 0)
                                for r in recent) + avg_efc_per_block
    except Exception:
        pass
    add_styled_table(doc,
        ['No', 'Item', 'Total', f'% of yearly cycles ({yt:.0f})'], [
        ['1', 'Number of Cycles in Reported Month',
          f'{avg_efc_per_block:.1f}', f'{avg_efc_per_block/yt*100:.2f}%'],
        ['2', 'Accumulative Number of Cycles in one Year',
          f'{annual_accum:.1f}', f'{annual_accum/yt*100:.2f}%'],
        ['3', 'Accumulative Number of Cycles',
          f'{accum_lifetime:.1f}', f'{accum_lifetime/yt*100:.2f}%']])

    if plant_avail:
        add_heading(doc, 'Availability Hours (plant-level redundancy method)', level=3)
        sched = plant_avail['scheduled_hours']
        plant_out = plant_avail.get('plant_outage_hours')
        cont_out = plant_avail['container_outage_hours']
        a_rows = [['Scheduled hours', f'{sched:,.1f} h'],
                  ['Scheduled unavailability (PM)', f'{scheduled_unavail_hours:,.1f} h'],
                  ['Unscheduled outage (container-aggregate)',
                   f'{cont_out:,.1f} h ({cont_out/sched*100:.2f}%)']]
        if plant_out is not None:
            a_rows.append(['Plant-level outage (redundancy adjusted)',
                f'{plant_out:,.1f} h ({plant_out/sched*100:.2f}%)'])
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
            f"{len(excl_result_local['events'])} exclusion event(s) recorded "
            f"during the period, totaling {total_excl:,.1f} h of plant-wall-"
            f"clock time (weighted by affected blocks). {applied:,.1f} h were "
            f"applied to the availability calculation (capped at actual outage "
            f"hours so the metric never credits more than was lost)."
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

    add_heading(doc, '4.2  Key Performance Indicators (KPIs)', level=3)
    add_kpi_row(doc, [
        (f"{avg_soc_pct:.2f}%", 'Average SOC', '#0071E3'),
        (f"{avg_soh_pct:.2f}%", 'Average SOH', '#34C759'),
        (f"{fleet_rte:.2f}%",    'RTE',         '#FF9500'),
    ])

    add_heading(doc, '4.3  Monthly Performance Comparison', level=3)
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
        add_styled_table(doc,
            ['Month', 'SOC', 'SOH', 'RTE', 'Cycles',
             'Discharge (MWh)', 'Charge (MWh)'], cmp_rows)

    add_heading(doc, '4.4  System Availability', level=3)
    add_image_from_fig(doc, _chart_availability_heatmap_categorical(
        g.get('container_day_status'),
        exclusions=g.get('exclusions'),
        fig_width=11), width_inches=6.5)
    add_caption(doc,
        'Graph 4: Daily availability status per container '
        '(green / orange / yellow / red).')
    add_image_from_fig(doc, _chart_daily_rte_per_block(daily_kpi))
    add_caption(doc, 'Graph 5: Daily RTE per block (light) and fleet mean (dark)')

    add_hr(doc)

    # 5. System Operation
    add_section_banner(doc, '5.  System Operation')
    add_heading(doc, '5.1  Summary of Faults and Alarms', level=3)
    p_dedup_all = alarms.get('production_dedup', pd.DataFrame())
    p_dedup = (p_dedup_all[~p_dedup_all.get('is_excluded', False)]
                if not p_dedup_all.empty and 'is_excluded' in p_dedup_all.columns
                else p_dedup_all)
    if not p_dedup.empty and 'cls_reason' in p_dedup.columns:
        add_paragraph(doc, 'Faults (Production)', bold=True)
        if n_prod_alarms_excluded > 0:
            add_paragraph(doc,
                f"{n_prod_alarms_excluded} of {n_prod_alarms} production "
                f"event(s) fell inside availability exclusion windows and "
                f"are listed separately below. The grouped table reflects "
                f"the remaining {n_prod_alarms - n_prod_alarms_excluded} event(s).",
                italic=True)
        gp = (p_dedup.groupby(['cls_subsystem', 'cls_reason', 'cls_resolution'])
                     .agg(occurrences=('Trigger name', 'count'),
                          total_hours=('duration_min', lambda x: x.sum()/60.0))
                     .reset_index().sort_values('occurrences', ascending=False))
        rows = [[r['cls_subsystem'], r['cls_reason'],
                  str(int(r['occurrences'])),
                  f"{r['total_hours']:.1f} h",
                  (r['cls_resolution'] or '—')[:50]]
                 for _, r in gp.head(15).iterrows()]
        add_styled_table(doc,
            ['Sub-system', 'Reason / Cause', 'Occurrences', 'Hours', 'Resolution'],
            rows)

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
            add_styled_table(doc,
                ['Class', 'Activated', 'Element', 'Trigger', 'Duration', 'During'],
                excl_rows)

    add_image_from_fig(doc, _chart_alarm_by_class(alarm_sum))
    add_caption(doc, 'Graph 6: Alarm count and duration by device class')

    # Trigger-level pie chart
    add_image_from_fig(doc, _chart_alarm_pie_by_trigger(p_dedup_all))
    add_caption(doc, 'Graph 7: Production alarm distribution by trigger type')

    # Faults table — customer-template layout
    add_paragraph(doc, 'Faults', bold=True)
    add_paragraph(doc,
        f'Critical-severity events and production faults lasting '
        f'≥ {int(IMPORTANT_DURATION_THRESHOLD_MIN)} minutes. Transient '
        f'(< 1 min), Info-tagged events, Input-dry-node, and events inside '
        f'availability exclusion windows are filtered out.', italic=True)
    important = select_important_alarms(p_dedup_all)
    if important.empty:
        add_paragraph(doc, 'No critical events during the reporting period.')
    else:
        rows = []
        for i, (_, r) in enumerate(important.head(40).iterrows(), start=1):
            elem = str(r.get('Element', ''))
            m = re.search(r'(\d+)\.\d+', elem)
            blk = m.group(1) if m else ''
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
        add_styled_table(doc,
            ['No.', 'Fault Name', 'Block #', 'Sub-system',
             'Reason / Cause', 'Date / Period', 'Resolution'], rows)
        if len(important) > 40:
            add_paragraph(doc,
                f'Showing first 40 of {len(important)} important events.',
                italic=True)

    # Alarms table
    add_paragraph(doc, 'Alarms', bold=True)
    w_persistent_all = alarms.get('warning_persistent', pd.DataFrame())
    if w_persistent_all is None or w_persistent_all.empty:
        add_paragraph(doc, 'No persistent alarms in the reporting period.')
    else:
        w = w_persistent_all.copy()
        if 'is_excluded' in w.columns:
            w = w[~w['is_excluded'].astype(bool)]
        trig_up = w['Trigger name'].astype(str).str.upper()
        for pat in NOISE_TRIGGER_PATTERNS:
            w = w[~trig_up.str.contains(pat.upper(), na=False)]
            trig_up = w['Trigger name'].astype(str).str.upper()
        if w.empty:
            add_paragraph(doc, 'No persistent alarms (after noise-filter).')
        else:
            rows = []
            for i, (_, r) in enumerate(
                    w.sort_values('duration_min', ascending=False)
                     .head(25).iterrows(), start=1):
                elem = str(r.get('Element', ''))
                m = re.search(r'(\d+)\.\d+', elem)
                blk = m.group(1) if m else ''
                rows.append([
                    str(i),
                    str(r.get('Trigger name', ''))[:42],
                    blk,
                    str(r.get('cls_subsystem', '') or ''),
                    str(r.get('cls_reason', '') or ''),
                    str(r.get('cls_resolution', '') or '—')[:40],
                ])
            add_styled_table(doc,
                ['No.', 'Fault Name', 'Block #', 'Sub-system',
                 'Reason / Cause', 'Resolution'], rows)

    add_heading(doc, '5.2  Major Incidents and Breakdowns', level=3)
    add_paragraph(doc, 'Summary of breakdowns, incidents and their weight '
                       'affecting availability.')
    breakdown_incidents_local = g.get('breakdown_incidents')
    if breakdown_incidents_local:
        rows = []
        for i, inc in enumerate(breakdown_incidents_local, start=1):
            rows.append([
                str(i),
                str(inc.get('incident', ''))[:50],
                str(inc.get('date_time', ''))[:18],
                str(inc.get('breakdown_type', ''))[:24],
                str(inc.get('temporary_solution', ''))[:36],
                str(inc.get('final_solution', ''))[:36],
                str(inc.get('closure_date', ''))[:14],
            ])
        add_styled_table(doc,
            ['No.', 'Breakdown incident', 'Date and time',
             'Breakdown type', 'Temporary solution', 'Final solution',
             'Date of closure'], rows)
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
                rows.append([
                    str(r['Activated'])[:16], str(r['Element'])[:20], trig,
                    (f"{r['duration_min']/60:.1f} h" if pd.notna(r['duration_min']) else '—'),
                ])
            add_styled_table(doc, ['Activated', 'Element', 'Trigger', 'Duration'], rows)
    add_hr(doc)

    # 6. Site Operation
    add_section_banner(doc, '6.  Site Operation and Facility Management')
    add_heading(doc, 'Incidents affecting safety and countermeasures', level=3)
    if safety_incidents:
        rows = [[s.get('incident',''), s.get('equipment_loss','-'),
                  s.get('weight',''), s.get('countermeasure','')]
                 for s in safety_incidents]
        add_styled_table(doc,
            ['Incident', 'Equipment Loss', 'Weight', 'Countermeasure'], rows)
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
        (f"Fleet RTE was <b>{fleet_rte:.2f}%</b> ({rte_status} 85% target). "
         f"Container availability: <b>{fleet_availability_container:.2f}%</b>."),
        (f"Across {n_blocks} blocks: <b>{total_discharge_mwh:,.1f} MWh</b> discharge / "
         f"<b>{total_charge_mwh:,.1f} MWh</b> charge, "
         f"<b>{total_efc_fleet:.0f} EFC</b>."),
        (f"<b>{n_prod_alarms:,}</b> production events (dedup) plus "
         f"<b>{n_warn_persistent:,}</b> persistent warnings."),
    ]
    if n_anomalies:
        auto.append(f"<b>{n_anomalies}</b> block(s) flagged as underperforming.")
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
        'Annex 1: Daily Block KPI table (CSV)',
        'Annex 2: Alarm classification lookup',
        'Annex 3: Quality-flagged days',
        'Annex 4: Block performance anomaly z-scores',
    ]:
        add_paragraph(doc, f'•  {line}')
    if annexes:
        for a in annexes: add_paragraph(doc, f'•  {a}')

    save_doc(doc, output_path)
    print(f"[Bukhara Report] DOCX saved: {output_path}")
    return output_path
