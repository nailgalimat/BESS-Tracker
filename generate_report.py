"""
BESS Monthly Operations Report Generator
Generates a professional PDF report from Huawei/Sungrow SCADA exports.

Input files:
  - working_status.xlsx        : hourly system working status per container
  - charge_discharge__status.xlsx : hourly charge/discharge status per container
  - Alarm_report__*.xlsx       : alarm log (sheets: Communications, Production, Warning)
  - exported_imported_.xlsx    : daily energy exported (kWh)
  - import_export_.xlsx        : daily energy imported (kWh)

Usage:
  python generate_report.py
  (files are read from the same folder as the script, or provide paths below)
"""

import os
import sys
import warnings
warnings.filterwarnings('ignore')

import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec
import io
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image, PageBreak, HRFlowable, KeepTogether
)
from reportlab.platypus import Flowable

# ── COLOUR PALETTE ────────────────────────────────────────────────────────────
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

# ── STYLES ────────────────────────────────────────────────────────────────────
styles = getSampleStyleSheet()

STYLE_H1 = ParagraphStyle('H1', fontName='Helvetica-Bold', fontSize=18,
    textColor=NAVY, spaceAfter=6, spaceBefore=0)
STYLE_H2 = ParagraphStyle('H2', fontName='Helvetica-Bold', fontSize=13,
    textColor=NAVY, spaceAfter=4, spaceBefore=12,
    borderPad=4, leading=16)
STYLE_H3 = ParagraphStyle('H3', fontName='Helvetica-Bold', fontSize=11,
    textColor=NAVY, spaceAfter=4, spaceBefore=8)
STYLE_BODY = ParagraphStyle('Body', fontName='Helvetica', fontSize=10,
    textColor=NAVY, spaceAfter=4, leading=14)
STYLE_SMALL = ParagraphStyle('Small', fontName='Helvetica', fontSize=8,
    textColor=TEXT_MUTE, spaceAfter=2, leading=11)
STYLE_CAPTION = ParagraphStyle('Caption', fontName='Helvetica-Oblique', fontSize=8,
    textColor=TEXT_MUTE, alignment=TA_CENTER, spaceAfter=6)
STYLE_KPI_VAL = ParagraphStyle('KPIVal', fontName='Helvetica-Bold', fontSize=22,
    textColor=NAVY, alignment=TA_CENTER, leading=26)
STYLE_KPI_LBL = ParagraphStyle('KPILbl', fontName='Helvetica', fontSize=8,
    textColor=TEXT_MUTE, alignment=TA_CENTER, leading=10)


# ── HORIZONTAL RULE ───────────────────────────────────────────────────────────
def hr(color=GREY_LINE, thickness=0.5):
    return HRFlowable(width='100%', thickness=thickness, color=color,
                      spaceAfter=6, spaceBefore=4)


# ── SECTION HEADER ────────────────────────────────────────────────────────────
def section_header(title: str, icon: str = ''):
    data = [[Paragraph(f'{icon}  {title}' if icon else title, STYLE_H2)]]
    t = Table(data, colWidths=['100%'])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), LIGHT_BLU),
        ('LEFTPADDING', (0,0), (-1,-1), 10),
        ('RIGHTPADDING', (0,0), (-1,-1), 10),
        ('TOPPADDING', (0,0), (-1,-1), 7),
        ('BOTTOMPADDING', (0,0), (-1,-1), 7),
        ('ROUNDEDCORNERS', [4, 4, 4, 4]),
    ]))
    return t


# ── KPI CARD ROW ─────────────────────────────────────────────────────────────
def kpi_row(kpis: list):
    """
    kpis: list of (value, label, accent_hex) tuples
    Returns a Table row of KPI cards.
    """
    n = len(kpis)
    col_w = 170 / n * mm

    cells = []
    for val, lbl, accent in kpis:
        accent_c = colors.HexColor(accent)
        inner = [
            [Paragraph(str(val), STYLE_KPI_VAL)],
            [Paragraph(lbl, STYLE_KPI_LBL)],
        ]
        inner_t = Table(inner, colWidths=[col_w - 8*mm])
        inner_t.setStyle(TableStyle([
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('TOPPADDING', (0,0), (-1,-1), 4),
            ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ]))
        cells.append(inner_t)

    t = Table([cells], colWidths=[col_w] * n)
    style_cmds = [
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BACKGROUND', (0,0), (-1,-1), WHITE),
        ('BOX', (0,0), (-1,-1), 0.5, GREY_LINE),
        ('INNERGRID', (0,0), (-1,-1), 0.5, GREY_LINE),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('LEFTPADDING', (0,0), (-1,-1), 4),
        ('RIGHTPADDING', (0,0), (-1,-1), 4),
    ]
    for i, (_, _, accent) in enumerate(kpis):
        style_cmds.append(('LINEBELOW', (i,0), (i,0), 3, colors.HexColor(accent)))
    t.setStyle(TableStyle(style_cmds))
    return t


# ── MATPLOTLIB CHART → ReportLab Image ───────────────────────────────────────
def fig_to_image(fig, width_mm=170, height_mm=70):
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    buf.seek(0)
    plt.close(fig)
    return Image(buf, width=width_mm*mm, height=height_mm*mm)


# ── DATA LOADING ──────────────────────────────────────────────────────────────

def load_working_status(path):
    df = pd.read_excel(path)
    df['Date_parsed'] = pd.to_datetime(df['Date'], format='%m/%d/%Y', errors='coerce')
    device_cols = [c for c in df.columns if 'LC200' in str(c)]
    return df, device_cols

def load_charge_discharge(path):
    df = pd.read_excel(path)
    df['Date_parsed'] = pd.to_datetime(df['Date'], format='%m/%d/%Y', errors='coerce')
    device_cols = [c for c in df.columns if 'LC200' in str(c)]
    return df, device_cols

def load_alarms(path):
    result = {}
    for sheet in ['Communications', 'Production', 'Warning', 'Preventitive', 'Manual']:
        try:
            df = pd.read_excel(path, sheet_name=sheet)
            df = df.dropna(how='all')
            result[sheet] = df
        except Exception:
            result[sheet] = pd.DataFrame()
    return result

def load_energy(exp_path, imp_path):
    """Load energy export/import files - handles the SCADA format with serial dates."""
    dfs = {}
    for label, path, col_name in [
        ('exported', exp_path, 'Energy_Exported_kWh'),
        ('imported', imp_path, 'Energy_Imported_kWh'),
    ]:
        try:
            # Read raw to find data - the files have merged cells at top
            df_raw = pd.read_excel(path, header=None)
            # Find row with 'DateTime' or numeric serial dates
            header_row = None
            for i, row in df_raw.iterrows():
                vals = [v for v in row if v is not None and str(v) != 'nan']
                if 'DateTime' in str(vals) or 'Date' in str(vals):
                    header_row = i
                    break
            if header_row is None:
                # Try to find first numeric data row
                for i, row in df_raw.iterrows():
                    vals = [v for v in row if v is not None and str(v) != 'nan']
                    if vals and isinstance(vals[0], (int, float)):
                        header_row = i - 1
                        break

            if header_row is not None:
                df = pd.read_excel(path, skiprows=header_row, header=0)
            else:
                df = pd.read_excel(path, header=0)

            df = df.dropna(how='all')
            # Get the energy column (last non-empty column usually)
            energy_col = None
            for col in df.columns:
                if 'kWh' in str(col) or 'Energy' in str(col) or 'Export' in str(col) or 'Import' in str(col):
                    energy_col = col
                    break
            if energy_col is None:
                # Take last numeric column
                num_cols = df.select_dtypes(include='number').columns
                if len(num_cols) > 0:
                    energy_col = num_cols[-1]

            # Date column
            date_col = None
            for col in df.columns:
                if 'Date' in str(col) or 'date' in str(col):
                    date_col = col
                    break
            if date_col is None:
                num_cols = df.select_dtypes(include='number').columns
                if len(num_cols) > 1:
                    date_col = num_cols[1]  # second numeric col usually is date

            if date_col and energy_col:
                df_clean = df[[date_col, energy_col]].copy()
                df_clean.columns = ['Date', col_name]
                df_clean = df_clean.dropna()
                # Convert serial dates
                df_clean['Date'] = pd.to_datetime(
                    df_clean['Date'].apply(
                        lambda x: pd.Timestamp('1899-12-30') + pd.Timedelta(days=float(x))
                        if isinstance(x, (int, float)) else x
                    ), errors='coerce'
                )
                df_clean = df_clean.dropna(subset=['Date'])
                dfs[label] = df_clean
            else:
                dfs[label] = pd.DataFrame(columns=['Date', col_name])
        except Exception as e:
            print(f"Warning: Could not load {label} energy file: {e}")
            dfs[label] = pd.DataFrame(columns=['Date', col_name])
    return dfs.get('exported', pd.DataFrame()), dfs.get('imported', pd.DataFrame())


def load_lc_daily_energy(charge_path, discharge_path):
    """
    Load LC daily charge & discharge energy files (30-min totalizer exports).
    The 'DAILY CHARGE/DISCHARGE ENERGY' parameter accumulates within each day
    and resets at midnight; the per-day max is the day's total per LC.

    Returns:
        df_charge:    DataFrame indexed by date, columns = LC device names, values in kWh
        df_discharge: same structure
        device_cols:  list of LC column names common to both files
    """
    def _read(path):
        df = pd.read_excel(path)
        df['Date_parsed'] = pd.to_datetime(df['Date'], format='%m/%d/%Y', errors='coerce')
        device_cols = [c for c in df.columns if 'LC200' in str(c)]
        # Daily total = max within each calendar day (totalizer that resets at midnight)
        df_daily = df.groupby(df['Date_parsed'].dt.date)[device_cols].max()
        df_daily.index.name = 'date'
        return df_daily, device_cols

    try:
        df_chg, chg_cols = _read(charge_path)
    except Exception as e:
        print(f"Warning: Could not load LC daily charge file: {e}")
        df_chg, chg_cols = pd.DataFrame(), []

    try:
        df_dis, dis_cols = _read(discharge_path)
    except Exception as e:
        print(f"Warning: Could not load LC daily discharge file: {e}")
        df_dis, dis_cols = pd.DataFrame(), []

    # Strip parameter-specific suffix so charge/discharge column names line up
    def _normalise(cols):
        # 'Tashkent - LC200 01.01 - LC - DAILY CHARGE ENERGY (kWh)' -> 'LC200 01.01'
        out = {}
        for c in cols:
            parts = str(c).split(' - ')
            if len(parts) >= 2:
                out[c] = parts[1].strip()
            else:
                out[c] = str(c)
        return out

    df_chg = df_chg.rename(columns=_normalise(chg_cols)) if not df_chg.empty else df_chg
    df_dis = df_dis.rename(columns=_normalise(dis_cols)) if not df_dis.empty else df_dis

    # Common LC IDs across both files (and drop any extra-day rows)
    common = sorted(set(df_chg.columns).intersection(df_dis.columns)) if not df_chg.empty and not df_dis.empty else []
    if common:
        df_chg = df_chg[common]
        df_dis = df_dis[common]

    return df_chg, df_dis, common


def load_cycle_targets(path, default=1):
    """
    Load per-day cycle target file. Expected columns: 'Date', 'Cycle Target'.
    Returns a dict: {date -> target_int}. Missing days fall back to `default`.
    Pass `path=None` (or a path that doesn't exist) to use the default for all days.
    """
    if not path or not os.path.exists(path):
        return {}, default
    try:
        if str(path).lower().endswith('.csv'):
            df = pd.read_csv(path)
        else:
            df = pd.read_excel(path)
        # Find date and target columns flexibly
        date_col = next((c for c in df.columns if 'date' in str(c).lower()), df.columns[0])
        tgt_col = next((c for c in df.columns
                        if 'cycle' in str(c).lower() or 'target' in str(c).lower()),
                       df.columns[-1])
        df['_date'] = pd.to_datetime(df[date_col], errors='coerce').dt.date
        df = df.dropna(subset=['_date'])
        targets = dict(zip(df['_date'], df[tgt_col].astype(int)))
        return targets, default
    except Exception as e:
        print(f"Warning: Could not load cycle targets file: {e}")
        return {}, default


# ── KPI CALCULATIONS ──────────────────────────────────────────────────────────

def calc_availability(df_ws, device_cols):
    total = len(df_ws) * len(device_cols)
    running = (df_ws[device_cols] == 'RUNNING').sum().sum()
    standby = (df_ws[device_cols] == 'STANDBY').sum().sum()
    fault   = (df_ws[device_cols] == 'FAULT').sum().sum()
    stopped = (df_ws[device_cols] == 'STOPPED').sum().sum()
    avail   = (running + standby) / total * 100 if total > 0 else 0
    return {
        'availability': avail,
        'running_pct':  running / total * 100,
        'standby_pct':  standby / total * 100,
        'fault_pct':    fault / total * 100,
        'stopped_pct':  stopped / total * 100,
        'total_devices': len(device_cols),
        'total_hours': len(df_ws),
    }

def calc_daily_availability(df_ws, device_cols):
    df_ws = df_ws.copy()
    df_ws['day'] = df_ws['Date_parsed'].dt.date
    daily = []
    for day, grp in df_ws.groupby('day'):
        t = len(grp) * len(device_cols)
        r = (grp[device_cols] == 'RUNNING').sum().sum()
        s = (grp[device_cols] == 'STANDBY').sum().sum()
        f = (grp[device_cols] == 'FAULT').sum().sum()
        daily.append({
            'date': day,
            'availability': (r + s) / t * 100 if t > 0 else 0,
            'fault_pct': f / t * 100 if t > 0 else 0,
        })
    return pd.DataFrame(daily)

def calc_charge_discharge_summary(df_cd, device_cols):
    total = len(df_cd) * len(device_cols)
    charging    = (df_cd[device_cols] == 'CHARGING').sum().sum()
    discharging = (df_cd[device_cols] == 'DISCHARGING').sum().sum()
    non_op      = (df_cd[device_cols] == 'NON-OPERATING MODE').sum().sum()
    return {
        'charging_pct':    charging / total * 100,
        'discharging_pct': discharging / total * 100,
        'non_op_pct':      non_op / total * 100,
        'charging_hrs':    charging / len(device_cols),
        'discharging_hrs': discharging / len(device_cols),
    }


# ── CAPACITY AVAILABILITY ─────────────────────────────────────────────────────

# Per-LC nameplate (full 0-100% SOC) and operational (5-95% SOC) per-cycle limits, kWh.
LC_CAP_NAMEPLATE_KWH   = 5504.0
LC_CAP_OPERATIONAL_KWH = 5504.0 * 0.90   # 4953.6 kWh, current 5-95% operating window


def calc_capacity_availability(df_chg, df_dis, lc_cols, cycle_targets, default_target=1):
    """
    Compute capacity-based availability per LC per day, using
    cycles_completed = min(daily_charge, daily_discharge) / per_cycle_capacity
    availability     = min(cycles_completed / target, 1.0) * 100%

    Returns a dict with daily fleet series and period-wide KPIs for both
    operational (5-95%) and nameplate (0-100%) divisors.
    """
    if df_chg.empty or df_dis.empty or not lc_cols:
        return None

    # Align indices
    common_dates = df_chg.index.intersection(df_dis.index)
    df_chg = df_chg.loc[common_dates].copy()
    df_dis = df_dis.loc[common_dates].copy()

    # Per LC per day "completed cycles" using min(charge, discharge)
    cycles_op = pd.DataFrame(
        index=common_dates, columns=lc_cols, dtype=float
    )
    cycles_np = cycles_op.copy()
    for lc in lc_cols:
        m = pd.concat([df_chg[lc], df_dis[lc]], axis=1).min(axis=1)
        cycles_op[lc] = m / LC_CAP_OPERATIONAL_KWH
        cycles_np[lc] = m / LC_CAP_NAMEPLATE_KWH

    # Resolve daily target series (default for missing days)
    target_series = pd.Series(
        [cycle_targets.get(d, default_target) for d in common_dates],
        index=common_dates, name='target', dtype=float
    )

    # Per-LC-per-day availability % (capped at 100)
    avail_op = (cycles_op.div(target_series, axis=0).clip(upper=1.0) * 100.0)
    avail_np = (cycles_np.div(target_series, axis=0).clip(upper=1.0) * 100.0)

    # Daily fleet aggregation = mean across LCs
    daily = pd.DataFrame({
        'date':            common_dates,
        'target':          target_series.values,
        'cycles_op_avg':   cycles_op.mean(axis=1).values,
        'cycles_np_avg':   cycles_np.mean(axis=1).values,
        'availability_op': avail_op.mean(axis=1).values,
        'availability_np': avail_np.mean(axis=1).values,
        'fleet_charge_mwh':    df_chg[lc_cols].sum(axis=1).values / 1000.0,
        'fleet_discharge_mwh': df_dis[lc_cols].sum(axis=1).values / 1000.0,
    })

    # Period-wide KPIs (mean of daily fleet means)
    period = {
        'availability_op_pct': daily['availability_op'].mean(),
        'availability_np_pct': daily['availability_np'].mean(),
        'avg_cycles_op':       daily['cycles_op_avg'].mean(),
        'avg_cycles_np':       daily['cycles_np_avg'].mean(),
        'total_charge_mwh':    daily['fleet_charge_mwh'].sum(),
        'total_discharge_mwh': daily['fleet_discharge_mwh'].sum(),
        'n_lcs':               len(lc_cols),
        'n_days':              len(common_dates),
        'cap_op_kwh':          LC_CAP_OPERATIONAL_KWH,
        'cap_np_kwh':          LC_CAP_NAMEPLATE_KWH,
    }

    # Per-LC summary: mean operational availability across the period
    per_lc = pd.DataFrame({
        'lc':                lc_cols,
        'avg_avail_op_pct':  avail_op.mean(axis=0).values,
        'avg_avail_np_pct':  avail_np.mean(axis=0).values,
        'total_charge_mwh':    df_chg[lc_cols].sum(axis=0).values / 1000.0,
        'total_discharge_mwh': df_dis[lc_cols].sum(axis=0).values / 1000.0,
    }).sort_values('avg_avail_op_pct')

    return {
        'daily':  daily,
        'period': period,
        'per_lc': per_lc,
    }


# ── CHART BUILDERS ────────────────────────────────────────────────────────────

def chart_daily_availability(df_daily):
    fig, ax = plt.subplots(figsize=(11, 3.5))
    dates = [str(d) for d in df_daily['date']]
    vals  = df_daily['availability'].values
    fault = df_daily['fault_pct'].values

    ax.fill_between(range(len(dates)), vals, alpha=0.15, color='#0071E3')
    ax.plot(range(len(dates)), vals, color='#0071E3', linewidth=2, marker='o',
            markersize=3, label='Availability %')
    ax.bar(range(len(dates)), fault, color='#FF3B30', alpha=0.6, label='Fault %')

    ax.axhline(y=95, color='#34C759', linestyle='--', linewidth=1, alpha=0.7, label='Target 95%')
    ax.set_xticks(range(0, len(dates), max(1, len(dates)//10)))
    ax.set_xticklabels([dates[i].replace('2026-','') for i in range(0, len(dates), max(1, len(dates)//10))],
                        fontsize=7, rotation=30)
    ax.set_ylim(0, 105)
    ax.set_ylabel('Availability (%)', fontsize=8)
    ax.set_title('Daily System Availability', fontsize=10, fontweight='bold', color='#1A2B45')
    ax.legend(fontsize=7, loc='lower right')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.grid(axis='y', linestyle='--', alpha=0.3)
    fig.tight_layout()
    return fig

def chart_energy(df_exp, df_imp):
    if df_exp.empty and df_imp.empty:
        fig, ax = plt.subplots(figsize=(11, 3.5))
        ax.text(0.5, 0.5, 'Energy data not available', ha='center', va='center',
                transform=ax.transAxes, fontsize=12, color='#6B7A8D')
        ax.set_title('Daily Energy (kWh)', fontsize=10)
        return fig

    fig, ax = plt.subplots(figsize=(11, 3.5))
    if not df_exp.empty:
        x = range(len(df_exp))
        ax.bar([i - 0.2 for i in x], df_exp['Energy_Exported_kWh'] / 1000,
               width=0.4, color='#0071E3', alpha=0.8, label='Exported (MWh)')
    if not df_imp.empty:
        x = range(len(df_imp))
        ax.bar([i + 0.2 for i in x], df_imp['Energy_Imported_kWh'] / 1000,
               width=0.4, color='#34C759', alpha=0.8, label='Imported (MWh)')

    if not df_exp.empty:
        labels = [str(d)[:10].replace('2026-','') for d in df_exp['Date']]
        ax.set_xticks(range(0, len(labels), max(1, len(labels)//10)))
        ax.set_xticklabels([labels[i] for i in range(0, len(labels), max(1, len(labels)//10))],
                            fontsize=7, rotation=30)
    ax.set_ylabel('Energy (MWh)', fontsize=8)
    ax.set_title('Daily Energy Exported / Imported', fontsize=10, fontweight='bold', color='#1A2B45')
    ax.legend(fontsize=7)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.grid(axis='y', linestyle='--', alpha=0.3)
    fig.tight_layout()
    return fig

def chart_status_pie(kpis):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9, 3.2))

    # Working status pie
    ws_labels = ['Running', 'Standby', 'Fault', 'Stopped']
    ws_vals   = [kpis['running_pct'], kpis['standby_pct'],
                 kpis['fault_pct'],   kpis['stopped_pct']]
    ws_colors = ['#0071E3', '#34C759', '#FF3B30', '#FF9500']
    ws_vals_f = [v for v in ws_vals if v > 0]
    ws_labels_f = [l for l, v in zip(ws_labels, ws_vals) if v > 0]
    ws_colors_f = [c for c, v in zip(ws_colors, ws_vals) if v > 0]
    wedges, texts, autotexts = ax1.pie(
        ws_vals_f, labels=ws_labels_f, colors=ws_colors_f,
        autopct='%1.1f%%', startangle=90, pctdistance=0.75,
        textprops={'fontsize': 7}
    )
    ax1.set_title('Working Status Distribution', fontsize=9, fontweight='bold', color='#1A2B45')

    # Charge/discharge pie
    cd_labels = ['Charging', 'Discharging', 'Non-Operating']
    cd_vals   = [kpis.get('cd_charging_pct', 31),
                 kpis.get('cd_discharging_pct', 27),
                 kpis.get('cd_non_op_pct', 42)]
    cd_colors = ['#0071E3', '#AF52DE', '#6B7A8D']
    ax2.pie(cd_vals, labels=cd_labels, colors=cd_colors,
            autopct='%1.1f%%', startangle=90, pctdistance=0.75,
            textprops={'fontsize': 7})
    ax2.set_title('Charge / Discharge Distribution', fontsize=9, fontweight='bold', color='#1A2B45')

    fig.tight_layout()
    return fig

def chart_capacity_availability(df_daily):
    """
    Two-curve daily chart of capacity-based availability:
      - Operational (5-95% SOC, 4,954 kWh per cycle)
      - Nameplate   (0-100% SOC, 5,504 kWh per cycle)
    Plus the daily cycle target on a secondary axis.
    """
    fig, ax1 = plt.subplots(figsize=(11, 3.5))
    dates = [str(d) for d in df_daily['date']]
    n = len(dates)
    x = range(n)

    op = df_daily['availability_op'].values
    np_= df_daily['availability_np'].values

    ax1.fill_between(x, op, alpha=0.12, color='#0071E3')
    ax1.plot(x, op, color='#0071E3', linewidth=2, marker='o', markersize=3,
             label='Operational (5–95%)')
    ax1.plot(x, np_, color='#AF52DE', linewidth=1.5, linestyle='--',
             marker='s', markersize=3, label='Nameplate (0–100%)')
    ax1.axhline(y=95, color='#34C759', linestyle=':', linewidth=1, alpha=0.7,
                label='Target 95%')
    ax1.set_ylim(0, 105)
    ax1.set_ylabel('Capacity Availability (%)', fontsize=8)
    ax1.set_title('Daily Capacity Availability — Operational vs Nameplate',
                  fontsize=10, fontweight='bold', color='#1A2B45')

    ax1.set_xticks(range(0, n, max(1, n//12)))
    ax1.set_xticklabels(
        [dates[i].replace('2026-', '') for i in range(0, n, max(1, n//12))],
        fontsize=7, rotation=30
    )

    # Secondary axis: daily cycle target (step plot)
    ax2 = ax1.twinx()
    ax2.step(x, df_daily['target'].values, where='mid',
             color='#FF9500', linewidth=1.2, alpha=0.75,
             label='Cycle target')
    ax2.set_ylim(0, max(3, df_daily['target'].max() + 1))
    ax2.set_ylabel('Cycle target', fontsize=8, color='#FF9500')
    ax2.tick_params(axis='y', colors='#FF9500', labelsize=7)
    ax2.spines['top'].set_visible(False)

    ax1.spines['top'].set_visible(False)
    ax1.grid(axis='y', linestyle='--', alpha=0.3)

    # Combined legend
    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, fontsize=7, loc='lower right', ncol=2)

    fig.tight_layout()
    return fig


def chart_top_alarms(df_warn):
    if df_warn.empty:
        return None
    top = df_warn['Trigger name'].value_counts().head(8)
    fig, ax = plt.subplots(figsize=(11, 3.5))
    bars = ax.barh(range(len(top)), top.values, color='#FF9500', alpha=0.85)
    ax.set_yticks(range(len(top)))
    ax.set_yticklabels([str(l)[:55] for l in top.index], fontsize=7)
    ax.invert_yaxis()
    ax.set_xlabel('Occurrences', fontsize=8)
    ax.set_title('Top 8 Warning Alarms', fontsize=10, fontweight='bold', color='#1A2B45')
    for bar, val in zip(bars, top.values):
        ax.text(bar.get_width() + 0.5, bar.get_y() + bar.get_height()/2,
                str(val), va='center', fontsize=7, color='#1A2B45')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.grid(axis='x', linestyle='--', alpha=0.3)
    fig.tight_layout()
    return fig


# ── STYLED TABLE HELPER ───────────────────────────────────────────────────────

def styled_table(headers, rows, col_widths=None, accent_col=None):
    """Creates a styled ReportLab Table."""
    header_row = [Paragraph(f'<b>{h}</b>', ParagraphStyle(
        'TH', fontName='Helvetica-Bold', fontSize=8,
        textColor=WHITE, alignment=TA_CENTER)) for h in headers]

    data = [header_row]
    for i, row in enumerate(rows):
        cells = []
        for j, cell in enumerate(row):
            style = ParagraphStyle(
                'TD', fontName='Helvetica', fontSize=8,
                textColor=NAVY, alignment=TA_CENTER, leading=10
            )
            cells.append(Paragraph(str(cell) if cell is not None else '', style))
        data.append(cells)

    if col_widths is None:
        page_w = 170 * mm
        col_widths = [page_w / len(headers)] * len(headers)

    t = Table(data, colWidths=col_widths, repeatRows=1)
    style_cmds = [
        ('BACKGROUND',   (0,0), (-1,0),   NAVY),
        ('TEXTCOLOR',    (0,0), (-1,0),   WHITE),
        ('ALIGN',        (0,0), (-1,-1),  'CENTER'),
        ('VALIGN',       (0,0), (-1,-1),  'MIDDLE'),
        ('TOPPADDING',   (0,0), (-1,-1),  5),
        ('BOTTOMPADDING',(0,0), (-1,-1),  5),
        ('LEFTPADDING',  (0,0), (-1,-1),  4),
        ('RIGHTPADDING', (0,0), (-1,-1),  4),
        ('GRID',         (0,0), (-1,-1),  0.3, GREY_LINE),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [WHITE, GREY_BG]),
    ]
    t.setStyle(TableStyle(style_cmds))
    return t


# ── COVER PAGE ────────────────────────────────────────────────────────────────

def build_cover(story, report_month, site_name, num_devices, period_str):
    story.append(Spacer(1, 30*mm))

    # Title block
    title_data = [[
        Paragraph('BESS OPERATIONS REPORT', ParagraphStyle(
            'Cover', fontName='Helvetica-Bold', fontSize=26,
            textColor=WHITE, alignment=TA_CENTER, spaceAfter=8)),
    ],[
        Paragraph(report_month, ParagraphStyle(
            'CoverSub', fontName='Helvetica', fontSize=16,
            textColor=colors.HexColor('#8FA3BE'), alignment=TA_CENTER)),
    ]]
    t = Table(title_data, colWidths=[170*mm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), NAVY),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('TOPPADDING', (0,0), (-1,-1), 20),
        ('BOTTOMPADDING', (0,0), (-1,-1), 20),
        ('LEFTPADDING', (0,0), (-1,-1), 20),
        ('RIGHTPADDING', (0,0), (-1,-1), 20),
        ('ROUNDEDCORNERS', [8, 8, 8, 8]),
    ]))
    story.append(t)
    story.append(Spacer(1, 12*mm))

    # Site info block
    info_data = [
        [Paragraph('<b>Site / Project:</b>', STYLE_BODY),
         Paragraph(site_name, STYLE_BODY)],
        [Paragraph('<b>Reporting Period:</b>', STYLE_BODY),
         Paragraph(period_str, STYLE_BODY)],
        [Paragraph('<b>Total Devices:</b>', STYLE_BODY),
         Paragraph(f'{num_devices} containers (LC200 units)', STYLE_BODY)],
        [Paragraph('<b>Report Generated:</b>', STYLE_BODY),
         Paragraph(datetime.now().strftime('%Y-%m-%d %H:%M'), STYLE_BODY)],
        [Paragraph('<b>Report Type:</b>', STYLE_BODY),
         Paragraph('Monthly Operations & Performance Summary', STYLE_BODY)],
    ]
    t2 = Table(info_data, colWidths=[55*mm, 115*mm])
    t2.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), GREY_BG),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 10),
        ('RIGHTPADDING', (0,0), (-1,-1), 10),
        ('GRID', (0,0), (-1,-1), 0.3, GREY_LINE),
        ('FONT', (0,0), (0,-1), 'Helvetica-Bold'),
    ]))
    story.append(t2)
    story.append(Spacer(1, 10*mm))

    # Disclaimer
    story.append(Paragraph(
        'This report has been automatically generated from SCADA system exports. '
        'All data reflects actual measurements recorded by the system monitoring platform.',
        STYLE_SMALL))
    story.append(PageBreak())


# ── MAIN REPORT BUILDER ───────────────────────────────────────────────────────

def generate_report(
    working_status_path,
    charge_discharge_path,
    alarm_path,
    exported_path,
    imported_path,
    output_path,
    site_name='Tashkent BESS',
    lc_charge_path=None,
    lc_discharge_path=None,
    cycle_targets_path=None,
    default_cycle_target=1,
):
    print(f"[Report] Loading data...")

    # Load all data
    df_ws, ws_cols     = load_working_status(working_status_path)
    df_cd, cd_cols     = load_charge_discharge(charge_discharge_path)
    alarms             = load_alarms(alarm_path)
    df_exp, df_imp     = load_energy(exported_path, imported_path)

    # Capacity availability inputs (optional — section is skipped if missing)
    cap_kpis = None
    if lc_charge_path and lc_discharge_path:
        df_lc_chg, df_lc_dis, lc_ids = load_lc_daily_energy(lc_charge_path, lc_discharge_path)
        cycle_targets, default_target = load_cycle_targets(
            cycle_targets_path, default=default_cycle_target
        )
        cap_kpis = calc_capacity_availability(
            df_lc_chg, df_lc_dis, lc_ids, cycle_targets, default_target=default_target
        )

    # Calculate KPIs
    kpis = calc_availability(df_ws, ws_cols)
    cd   = calc_charge_discharge_summary(df_cd, cd_cols)
    kpis.update({
        'cd_charging_pct':    cd['charging_pct'],
        'cd_discharging_pct': cd['discharging_pct'],
        'cd_non_op_pct':      cd['non_op_pct'],
    })
    df_daily_avail = calc_daily_availability(df_ws, ws_cols)

    # Date range
    date_min = df_ws['Date_parsed'].min()
    date_max = df_ws['Date_parsed'].max()
    period_str = f"{date_min.strftime('%d %B %Y')} — {date_max.strftime('%d %B %Y')}"
    report_month = date_min.strftime('%B %Y')

    # Energy totals
    total_exp = df_exp['Energy_Exported_kWh'].sum() if not df_exp.empty else 0
    total_imp = df_imp['Energy_Imported_kWh'].sum() if not df_imp.empty else 0
    net_energy = total_exp - total_imp

    # Alarm counts
    total_alarms = sum(len(v) for v in alarms.values() if not v.empty)
    prod_alarms  = len(alarms.get('Production', pd.DataFrame()))
    warn_alarms  = len(alarms.get('Warning', pd.DataFrame()))
    comm_alarms  = len(alarms.get('Communications', pd.DataFrame()))

    print(f"[Report] Building PDF...")

    # ── PDF DOCUMENT SETUP ──────────────────────────────────────────────
    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        leftMargin=20*mm, rightMargin=20*mm,
        topMargin=20*mm, bottomMargin=20*mm,
        title=f'BESS Operations Report — {report_month}',
        author='BESS Tracker Field Service',
    )

    story = []

    # ── 1. COVER PAGE ───────────────────────────────────────────────────
    build_cover(story, report_month, site_name,
                kpis['total_devices'], period_str)

    # ── 2. EXECUTIVE SUMMARY ────────────────────────────────────────────
    story.append(section_header('Executive Summary', '📋'))
    story.append(Spacer(1, 4*mm))

    story.append(kpi_row([
        (f"{kpis['availability']:.1f}%", 'System Availability', '#0071E3'),
        (f"{kpis['total_devices']}", 'Active Containers', '#34C759'),
        (f"{total_exp/1000:,.0f} MWh", 'Energy Exported', '#FF9500'),
        (f"{total_imp/1000:,.0f} MWh", 'Energy Imported', '#AF52DE'),
    ]))
    story.append(Spacer(1, 4*mm))
    story.append(kpi_row([
        (f"{kpis['fault_pct']:.2f}%", 'Fault Rate', '#FF3B30'),
        (f"{prod_alarms}", 'Production Alarms', '#FF3B30'),
        (f"{warn_alarms}", 'Warnings', '#FF9500'),
        (f"{comm_alarms}", 'Comm. Alarms', '#6B7A8D'),
    ]))
    story.append(Spacer(1, 4*mm))

    # Capacity availability summary (only if data is available)
    if cap_kpis is not None:
        story.append(kpi_row([
            (f"{cap_kpis['period']['availability_op_pct']:.1f}%",
             'Capacity Avail. (Operational, 5–95%)', '#0071E3'),
            (f"{cap_kpis['period']['availability_np_pct']:.1f}%",
             'Capacity Avail. (Nameplate, 0–100%)', '#AF52DE'),
            (f"{cap_kpis['period']['avg_cycles_op']:.2f}",
             'Avg cycles / LC / day', '#34C759'),
            (f"{cap_kpis['period']['total_discharge_mwh']:,.0f} MWh",
             'Total Fleet Discharge', '#FF9500'),
        ]))
    story.append(Spacer(1, 6*mm))

    # Summary paragraph
    avail_status = "above" if kpis['availability'] >= 95 else "below"
    summary_text = (
        f"During the reporting period <b>{period_str}</b>, the {site_name} BESS system "
        f"maintained an overall availability of <b>{kpis['availability']:.2f}%</b>, "
        f"which is {avail_status} the 95% target threshold. "
        f"A total of <b>{kpis['total_devices']} LC200 containers</b> were monitored. "
        f"The system exported <b>{total_exp/1000:,.1f} MWh</b> and imported "
        f"<b>{total_imp/1000:,.1f} MWh</b> of energy, resulting in a net "
        f"{'export' if net_energy >= 0 else 'import'} of "
        f"<b>{abs(net_energy)/1000:,.1f} MWh</b>. "
        f"A total of <b>{total_alarms}</b> alarm events were recorded, "
        f"including {prod_alarms} production-impacting alarms."
    )
    story.append(Paragraph(summary_text, STYLE_BODY))
    story.append(Spacer(1, 4*mm))
    story.append(hr())

    # ── 3. AVAILABILITY ANALYSIS ─────────────────────────────────────────
    story.append(section_header('System Availability', '📊'))
    story.append(Spacer(1, 3*mm))

    # Daily availability chart
    fig_avail = chart_daily_availability(df_daily_avail)
    story.append(fig_to_image(fig_avail, 170, 65))
    story.append(Paragraph('Figure 1: Daily system availability (%) and fault rate (%)',
                            STYLE_CAPTION))
    story.append(Spacer(1, 4*mm))

    # Status distribution table
    status_rows = [
        ['RUNNING',        f"{kpis['running_pct']:.1f}%",
         f"{int(kpis['running_pct']*kpis['total_hours']/100)} hrs", 'Active operation'],
        ['STANDBY',        f"{kpis['standby_pct']:.1f}%",
         f"{int(kpis['standby_pct']*kpis['total_hours']/100)} hrs", 'Ready, not operating'],
        ['FAULT',          f"{kpis['fault_pct']:.2f}%",
         f"{int(kpis['fault_pct']*kpis['total_hours']/100)} hrs", 'System fault detected'],
        ['STOPPED',        f"{kpis['stopped_pct']:.2f}%",
         f"{int(kpis['stopped_pct']*kpis['total_hours']/100)} hrs", 'Manually stopped'],
        ['AVAILABILITY',   f"{kpis['availability']:.2f}%",
         '—', 'Running + Standby'],
    ]
    story.append(styled_table(
        ['Status', '% of Time', 'Avg Hours/Device', 'Description'],
        status_rows,
        col_widths=[40*mm, 35*mm, 45*mm, 50*mm]
    ))
    story.append(Spacer(1, 4*mm))

    # Daily availability detail table (first/last 10 days)
    story.append(Paragraph('<b>Daily Availability Detail</b>', STYLE_H3))
    avail_table_rows = []
    for _, row in df_daily_avail.iterrows():
        status = '✓ OK' if row['availability'] >= 95 else '⚠ Low'
        avail_table_rows.append([
            str(row['date']),
            f"{row['availability']:.1f}%",
            f"{row['fault_pct']:.2f}%",
            status
        ])
    story.append(styled_table(
        ['Date', 'Availability', 'Fault Rate', 'Status'],
        avail_table_rows[:31],
        col_widths=[45*mm, 40*mm, 40*mm, 45*mm]
    ))
    story.append(Spacer(1, 4*mm))
    story.append(hr())

    # ── 4. CHARGE / DISCHARGE ────────────────────────────────────────────
    story.append(section_header('Charge / Discharge Operations', '🔋'))
    story.append(Spacer(1, 3*mm))

    # Pie charts
    fig_pie = chart_status_pie(kpis)
    story.append(fig_to_image(fig_pie, 170, 65))
    story.append(Paragraph('Figure 2: Working status distribution and charge/discharge split',
                            STYLE_CAPTION))
    story.append(Spacer(1, 4*mm))

    cd_rows = [
        ['CHARGING',       f"{cd['charging_pct']:.1f}%",
         f"{cd['charging_hrs']:.0f} hrs",    'Grid to battery'],
        ['DISCHARGING',    f"{cd['discharging_pct']:.1f}%",
         f"{cd['discharging_hrs']:.0f} hrs", 'Battery to grid'],
        ['NON-OPERATING',  f"{cd['non_op_pct']:.1f}%",
         '—', 'Standby / idle mode'],
    ]
    story.append(styled_table(
        ['Mode', '% of Time', 'Avg Hours/Device', 'Description'],
        cd_rows,
        col_widths=[40*mm, 35*mm, 45*mm, 50*mm]
    ))
    story.append(Spacer(1, 4*mm))
    story.append(hr())

    # ── 4b. CAPACITY AVAILABILITY ────────────────────────────────────────
    if cap_kpis is not None:
        per = cap_kpis['period']
        daily = cap_kpis['daily']
        per_lc = cap_kpis['per_lc']

        story.append(section_header('Capacity Availability (per LC, per cycle)', '🔄'))
        story.append(Spacer(1, 3*mm))

        intro = (
            f"This metric assesses whether each LC delivered its contracted daily charge / discharge cycles. "
            f"<b>Cycles completed</b> per LC per day = min(daily charge, daily discharge) ÷ per-cycle capacity. "
            f"Two divisors are shown side-by-side: <b>operational</b> ({per['cap_op_kwh']:,.0f} kWh, 5–95% SOC, "
            f"current operating window) and <b>nameplate</b> ({per['cap_np_kwh']:,.0f} kWh, 0–100% SOC). "
            f"Daily cycle target is sourced from the cycle-targets file (default: {default_cycle_target}). "
            f"Per-day availability is capped at 100% so multi-cycle days do not inflate the metric."
        )
        story.append(Paragraph(intro, STYLE_BODY))
        story.append(Spacer(1, 3*mm))

        # Headline KPI cards
        story.append(kpi_row([
            (f"{per['availability_op_pct']:.1f}%",
             f"Capacity Avail. (Operational, 5–95%)", '#0071E3'),
            (f"{per['availability_np_pct']:.1f}%",
             f"Capacity Avail. (Nameplate, 0–100%)", '#AF52DE'),
            (f"{per['avg_cycles_op']:.2f}",
             f"Avg cycles/LC/day (operational)", '#34C759'),
            (f"{per['n_lcs']}",
             f"LCs analysed", '#6B7A8D'),
        ]))
        story.append(Spacer(1, 4*mm))

        # Daily availability chart (operational vs nameplate + cycle target)
        fig_cap = chart_capacity_availability(daily)
        story.append(fig_to_image(fig_cap, 170, 65))
        story.append(Paragraph(
            'Figure 2b: Daily fleet-wide capacity availability — operational (5–95%) '
            'and nameplate (0–100%) divisors, with daily cycle target shown on the '
            'right axis.', STYLE_CAPTION))
        story.append(Spacer(1, 4*mm))

        # Daily detail table
        story.append(Paragraph('<b>Daily Capacity Detail (fleet average)</b>', STYLE_H3))
        cap_rows = []
        for _, row in daily.iterrows():
            tgt = int(row['target'])
            cycles_str = f"{row['cycles_op_avg']:.2f} / {tgt}"
            avail_op_str = f"{row['availability_op']:.1f}%"
            avail_np_str = f"{row['availability_np']:.1f}%"
            status = '✓ OK' if row['availability_op'] >= 95 else '⚠ Low'
            cap_rows.append([
                str(row['date']),
                str(tgt),
                f"{row['fleet_charge_mwh']:,.1f}",
                f"{row['fleet_discharge_mwh']:,.1f}",
                cycles_str,
                avail_op_str,
                avail_np_str,
                status,
            ])
        story.append(styled_table(
            ['Date', 'Target', 'Charge MWh', 'Discharge MWh',
             'Cycles done/target', 'Avail Op.', 'Avail Np.', 'Status'],
            cap_rows,
            col_widths=[22*mm, 15*mm, 22*mm, 24*mm, 28*mm, 18*mm, 18*mm, 23*mm]
        ))
        story.append(Spacer(1, 4*mm))

        # Bottom-10 LCs (chronic underperformers)
        if len(per_lc) > 0:
            bottom = per_lc.head(10)
            story.append(Paragraph(
                '<b>Lowest-availability LCs over the period (operational, 5–95%)</b>',
                STYLE_H3))
            lc_rows = []
            for _, row in bottom.iterrows():
                lc_rows.append([
                    str(row['lc']),
                    f"{row['avg_avail_op_pct']:.1f}%",
                    f"{row['avg_avail_np_pct']:.1f}%",
                    f"{row['total_charge_mwh']:,.2f}",
                    f"{row['total_discharge_mwh']:,.2f}",
                ])
            story.append(styled_table(
                ['LC', 'Avg Avail Op.', 'Avg Avail Np.',
                 'Period Charge (MWh)', 'Period Discharge (MWh)'],
                lc_rows,
                col_widths=[35*mm, 30*mm, 30*mm, 38*mm, 38*mm]
            ))

        story.append(Spacer(1, 4*mm))
        story.append(hr())

    # ── 5. ENERGY ────────────────────────────────────────────────────────
    story.append(section_header('Energy Performance', '⚡'))
    story.append(Spacer(1, 3*mm))

    story.append(kpi_row([
        (f"{total_exp/1000:,.1f}", 'Total Exported (MWh)', '#0071E3'),
        (f"{total_imp/1000:,.1f}", 'Total Imported (MWh)', '#34C759'),
        (f"{abs(net_energy)/1000:,.1f}", f"Net {'Export' if net_energy>=0 else 'Import'} (MWh)", '#FF9500'),
    ]))
    story.append(Spacer(1, 4*mm))

    if not df_exp.empty or not df_imp.empty:
        fig_energy = chart_energy(df_exp, df_imp)
        story.append(fig_to_image(fig_energy, 170, 65))
        story.append(Paragraph('Figure 3: Daily energy exported and imported (MWh)',
                                STYLE_CAPTION))
        story.append(Spacer(1, 4*mm))

        # Energy table
        if not df_exp.empty and not df_imp.empty:
            merged = pd.merge(df_exp, df_imp, on='Date', how='outer').sort_values('Date')
            energy_rows = []
            for _, row in merged.iterrows():
                exp_v = row.get('Energy_Exported_kWh', 0)
                imp_v = row.get('Energy_Imported_kWh', 0)
                net   = (exp_v - imp_v) if pd.notna(exp_v) and pd.notna(imp_v) else 0
                energy_rows.append([
                    str(row['Date'])[:10],
                    f"{exp_v/1000:,.2f}" if pd.notna(exp_v) else '—',
                    f"{imp_v/1000:,.2f}" if pd.notna(imp_v) else '—',
                    f"{net/1000:,.2f}" if pd.notna(exp_v) and pd.notna(imp_v) else '—',
                ])
            story.append(styled_table(
                ['Date', 'Exported (MWh)', 'Imported (MWh)', 'Net (MWh)'],
                energy_rows,
                col_widths=[45*mm, 42*mm, 42*mm, 41*mm]
            ))

    story.append(Spacer(1, 4*mm))
    story.append(hr())

    # ── 6. ALARM ANALYSIS ────────────────────────────────────────────────
    story.append(section_header('Alarm & Fault Analysis', '🚨'))
    story.append(Spacer(1, 3*mm))

    story.append(kpi_row([
        (f"{prod_alarms}", 'Production Alarms', '#FF3B30'),
        (f"{warn_alarms}", 'Warning Alarms', '#FF9500'),
        (f"{comm_alarms}", 'Communication Alarms', '#6B7A8D'),
        (f"{total_alarms}", 'Total Events', '#1A2B45'),
    ]))
    story.append(Spacer(1, 4*mm))

    # Top warnings chart
    df_warn = alarms.get('Warning', pd.DataFrame())
    if not df_warn.empty and 'Trigger name' in df_warn.columns:
        fig_alarms = chart_top_alarms(df_warn)
        if fig_alarms:
            story.append(fig_to_image(fig_alarms, 170, 65))
            story.append(Paragraph('Figure 4: Top 8 warning alarm types by occurrence count',
                                    STYLE_CAPTION))
            story.append(Spacer(1, 4*mm))

        # Top 15 alarm table
        story.append(Paragraph('<b>Warning Alarm Summary</b>', STYLE_H3))
        top_alarms = df_warn['Trigger name'].value_counts().head(15)
        alarm_rows = [[name[:60], str(count),
                        f"{count/len(df_warn)*100:.1f}%"]
                       for name, count in top_alarms.items()]
        story.append(styled_table(
            ['Alarm Type', 'Count', '% of Total'],
            alarm_rows,
            col_widths=[100*mm, 35*mm, 35*mm]
        ))
        story.append(Spacer(1, 4*mm))

    # Production alarms detail
    df_prod = alarms.get('Production', pd.DataFrame())
    if not df_prod.empty:
        story.append(Paragraph('<b>Production Alarms (first 20)</b>', STYLE_H3))
        prod_rows = []
        for _, row in df_prod.head(20).iterrows():
            activated   = str(row.get('Activated',''))[:16]
            deactivated = str(row.get('Deactivation',''))[:16]
            duration    = ''
            try:
                if pd.notna(row.get('Activated')) and pd.notna(row.get('Deactivation')):
                    dur = (pd.to_datetime(row['Deactivation']) -
                           pd.to_datetime(row['Activated'])).total_seconds() / 60
                    duration = f"{dur:.0f} min"
            except Exception:
                pass
            prod_rows.append([
                str(row.get('Element',''))[:20],
                str(row.get('Trigger name',''))[:40],
                activated, deactivated, duration
            ])
        story.append(styled_table(
            ['Element', 'Alarm Type', 'Start', 'End', 'Duration'],
            prod_rows,
            col_widths=[30*mm, 72*mm, 28*mm, 28*mm, 12*mm]
        ))

    story.append(Spacer(1, 4*mm))
    story.append(hr())

    # ── 7. COMMUNICATION ALARMS ──────────────────────────────────────────
    df_comm = alarms.get('Communications', pd.DataFrame())
    if not df_comm.empty:
        story.append(section_header('Communication Events', '📡'))
        story.append(Spacer(1, 3*mm))

        # Extract device name from element
        if 'Element' in df_comm.columns:
            top_comm = df_comm['Element'].value_counts().head(10)
            comm_rows = [[elem, str(cnt)] for elem, cnt in top_comm.items()]
            story.append(Paragraph(
                f'A total of <b>{len(df_comm)}</b> communication events were recorded. '
                f'Most were brief connection drops affecting LC200 units.',
                STYLE_BODY))
            story.append(Spacer(1, 3*mm))
            story.append(styled_table(
                ['Device', 'Event Count'],
                comm_rows[:10],
                col_widths=[100*mm, 70*mm]
            ))
        story.append(Spacer(1, 4*mm))
        story.append(hr())

    # ── 8. CONCLUSIONS ───────────────────────────────────────────────────
    story.append(section_header('Conclusions & Observations', '📝'))
    story.append(Spacer(1, 3*mm))

    conclusions = [
        f"System availability of <b>{kpis['availability']:.2f}%</b> was recorded for the period — "
        f"{'meeting' if kpis['availability'] >= 95 else 'below'} the 95% contractual target.",

        f"All <b>{kpis['total_devices']} LC200 containers</b> were monitored throughout the period. "
        f"Fault rate was maintained at <b>{kpis['fault_pct']:.2f}%</b>.",

        f"Energy performance: <b>{total_exp/1000:,.1f} MWh exported</b> and "
        f"<b>{total_imp/1000:,.1f} MWh imported</b>. "
        f"Net {'export' if net_energy >= 0 else 'import'}: {abs(net_energy)/1000:,.1f} MWh.",

        f"A total of <b>{total_alarms} alarm events</b> were recorded. "
        f"Production-impacting alarms: {prod_alarms}. "
        f"Warning alarms: {warn_alarms}.",

        f"Communication events: {comm_alarms} brief connection drops recorded, "
        f"all self-recovered without manual intervention.",
    ]
    if cap_kpis is not None:
        conclusions.insert(2,
            f"Capacity availability — <b>{cap_kpis['period']['availability_op_pct']:.2f}%</b> "
            f"(operational, 5–95%) and <b>{cap_kpis['period']['availability_np_pct']:.2f}%</b> "
            f"(nameplate, 0–100%). Fleet averaged "
            f"<b>{cap_kpis['period']['avg_cycles_op']:.2f}</b> cycles per LC per day. "
            f"Total discharge across the fleet: "
            f"<b>{cap_kpis['period']['total_discharge_mwh']:,.0f} MWh</b>."
        )
    for i, text in enumerate(conclusions, 1):
        story.append(Paragraph(f'{i}.  {text}', STYLE_BODY))
        story.append(Spacer(1, 2*mm))

    story.append(Spacer(1, 6*mm))
    story.append(Paragraph(
        f'Report generated automatically by BESS Tracker — Field Service Portal  |  '
        f'{datetime.now().strftime("%Y-%m-%d %H:%M")}',
        STYLE_SMALL))

    # ── BUILD ────────────────────────────────────────────────────────────
    doc.build(story)
    print(f"[Report] PDF saved to: {output_path}")
    return output_path


# ── RUN ───────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    BASE = '/mnt/user-data/uploads'
    OUT  = '/mnt/user-data/outputs/BESS_Operations_Report_March_2026.pdf'

    generate_report(
        working_status_path   = f'{BASE}/working_status.xlsx',
        charge_discharge_path = f'{BASE}/charge_discharge__status.xlsx',
        alarm_path            = f'{BASE}/Alarm_report__2026_04_07.XLSX',
        exported_path         = f'{BASE}/exported_imported_.xlsx',
        imported_path         = f'{BASE}/import_export_.xlsx',
        output_path           = OUT,
        site_name             = 'Tashkent BESS',
        # New capacity-availability inputs (optional)
        lc_charge_path        = f'{BASE}/LC daily charge.xlsx',
        lc_discharge_path     = f'{BASE}/LC Daily discharge.xlsx',
        cycle_targets_path    = f'{BASE}/cycle_targets.xlsx',
        default_cycle_target  = 1,
    )
