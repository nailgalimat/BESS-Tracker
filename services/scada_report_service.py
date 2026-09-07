"""
services/scada_report_service.py
---------------------------------
BESS Monthly Operations Report generator.
Reads SCADA export files and produces a professional PDF report.

Called from ui/scada_report_page.py — no UI logic here.
"""

import warnings
warnings.filterwarnings('ignore')

import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import io
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image, PageBreak, HRFlowable,
)

# ── COLOUR PALETTE ─────────────────────────────────────────────────────────────
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

# ── STYLES ─────────────────────────────────────────────────────────────────────
STYLE_H2    = ParagraphStyle('H2', fontName='Helvetica-Bold', fontSize=13,
                textColor=NAVY, spaceAfter=4, spaceBefore=12, leading=16)
STYLE_H3    = ParagraphStyle('H3', fontName='Helvetica-Bold', fontSize=11,
                textColor=NAVY, spaceAfter=4, spaceBefore=8)
STYLE_BODY  = ParagraphStyle('Body', fontName='Helvetica', fontSize=10,
                textColor=NAVY, spaceAfter=4, leading=14)
STYLE_SMALL = ParagraphStyle('Small', fontName='Helvetica', fontSize=8,
                textColor=TEXT_MUTE, spaceAfter=2, leading=11)
STYLE_CAP   = ParagraphStyle('Cap', fontName='Helvetica-Oblique', fontSize=8,
                textColor=TEXT_MUTE, alignment=TA_CENTER, spaceAfter=6)
STYLE_KVAL  = ParagraphStyle('KVal', fontName='Helvetica-Bold', fontSize=22,
                textColor=NAVY, alignment=TA_CENTER, leading=26)
STYLE_KLBL  = ParagraphStyle('KLbl', fontName='Helvetica', fontSize=8,
                textColor=TEXT_MUTE, alignment=TA_CENTER, leading=10)


def hr():
    return HRFlowable(width='100%', thickness=0.5, color=GREY_LINE,
                      spaceAfter=6, spaceBefore=4)


def section_header(title, icon=''):
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


def kpi_row(kpis):
    n = len(kpis)
    col_w = 170 / n * mm
    cells = []
    for val, lbl, accent in kpis:
        inner = Table([
            [Paragraph(str(val), STYLE_KVAL)],
            [Paragraph(lbl,      STYLE_KLBL)],
        ], colWidths=[col_w - 8*mm])
        inner.setStyle(TableStyle([
            ('ALIGN',         (0,0), (-1,-1), 'CENTER'),
            ('TOPPADDING',    (0,0), (-1,-1), 4),
            ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ]))
        cells.append(inner)
    t = Table([cells], colWidths=[col_w] * n)
    cmds = [
        ('ALIGN',         (0,0), (-1,-1), 'CENTER'),
        ('VALIGN',        (0,0), (-1,-1), 'MIDDLE'),
        ('BACKGROUND',    (0,0), (-1,-1), WHITE),
        ('BOX',           (0,0), (-1,-1), 0.5, GREY_LINE),
        ('INNERGRID',     (0,0), (-1,-1), 0.5, GREY_LINE),
        ('TOPPADDING',    (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('LEFTPADDING',   (0,0), (-1,-1), 4),
        ('RIGHTPADDING',  (0,0), (-1,-1), 4),
    ]
    for i, (_, _, accent) in enumerate(kpis):
        cmds.append(('LINEBELOW', (i,0), (i,0), 3, colors.HexColor(accent)))
    t.setStyle(TableStyle(cmds))
    return t


def fig_to_image(fig, w_mm=170, h_mm=65):
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    buf.seek(0)
    plt.close(fig)
    return Image(buf, width=w_mm*mm, height=h_mm*mm)


def styled_table(headers, rows, col_widths=None):
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
        ('BACKGROUND',     (0,0), (-1,0),   NAVY),
        ('TEXTCOLOR',      (0,0), (-1,0),   WHITE),
        ('ALIGN',          (0,0), (-1,-1),  'CENTER'),
        ('VALIGN',         (0,0), (-1,-1),  'MIDDLE'),
        ('TOPPADDING',     (0,0), (-1,-1),  5),
        ('BOTTOMPADDING',  (0,0), (-1,-1),  5),
        ('LEFTPADDING',    (0,0), (-1,-1),  4),
        ('RIGHTPADDING',   (0,0), (-1,-1),  4),
        ('GRID',           (0,0), (-1,-1),  0.3, GREY_LINE),
        ('ROWBACKGROUNDS', (0,1), (-1,-1),  [WHITE, GREY_BG]),
    ]))
    return t


# ── DATA LOADERS ───────────────────────────────────────────────────────────────

def load_working_status(path):
    df = pd.read_excel(path)
    df['Date_parsed'] = pd.to_datetime(df['Date'], format='%m/%d/%Y', errors='coerce')
    cols = [c for c in df.columns if 'LC200' in str(c)]
    return df, cols


def load_charge_discharge(path):
    df = pd.read_excel(path)
    df['Date_parsed'] = pd.to_datetime(df['Date'], format='%m/%d/%Y', errors='coerce')
    cols = [c for c in df.columns if 'LC200' in str(c)]
    return df, cols


def load_alarms(path):
    result = {}
    for sheet in ['Communications', 'Production', 'Warning', 'Preventitive', 'Manual']:
        try:
            df = pd.read_excel(path, sheet_name=sheet).dropna(how='all')
            result[sheet] = df
        except Exception:
            result[sheet] = pd.DataFrame()
    return result


def load_energy(exp_path, imp_path):
    dfs = {}
    for label, path, col in [
        ('exported', exp_path, 'Energy_Exported_kWh'),
        ('imported', imp_path, 'Energy_Imported_kWh'),
    ]:
        try:
            df_raw = pd.read_excel(path, header=None)
            header_row = None
            for i, row in df_raw.iterrows():
                vals = [v for v in row if v is not None and str(v) != 'nan']
                if 'DateTime' in str(vals) or 'Date' in str(vals):
                    header_row = i
                    break
            if header_row is None:
                for i, row in df_raw.iterrows():
                    vals = [v for v in row if v is not None and str(v) != 'nan']
                    if vals and isinstance(vals[0], (int, float)):
                        header_row = max(0, i - 1)
                        break
            df = pd.read_excel(path, skiprows=header_row or 0, header=0).dropna(how='all')
            energy_col = next((c for c in df.columns if any(
                k in str(c) for k in ['kWh','Energy','Export','Import'])), None)
            if energy_col is None:
                num = df.select_dtypes(include='number').columns
                energy_col = num[-1] if len(num) > 0 else None
            date_col = next((c for c in df.columns if 'Date' in str(c)), None)
            if date_col is None:
                num = df.select_dtypes(include='number').columns
                date_col = num[1] if len(num) > 1 else None
            if date_col and energy_col:
                df2 = df[[date_col, energy_col]].copy()
                df2.columns = ['Date', col]
                df2 = df2.dropna()
                df2['Date'] = pd.to_datetime(df2['Date'].apply(
                    lambda x: pd.Timestamp('1899-12-30') + pd.Timedelta(days=float(x))
                    if isinstance(x, (int, float)) else x), errors='coerce')
                dfs[label] = df2.dropna(subset=['Date'])
            else:
                dfs[label] = pd.DataFrame(columns=['Date', col])
        except Exception as e:
            dfs[label] = pd.DataFrame(columns=['Date', col])
    return dfs.get('exported', pd.DataFrame()), dfs.get('imported', pd.DataFrame())


# ── LC DAILY ENERGY (capacity availability) ───────────────────────────────────

def load_lc_daily_energy(charge_path, discharge_path):
    """
    Load LC daily charge & discharge energy files (30-min totalizer exports).
    The 'DAILY CHARGE/DISCHARGE ENERGY' parameter accumulates within each day
    and resets at midnight; the per-day max is the day's total per LC.

    Returns:
        df_charge:    DataFrame indexed by date, columns = normalised LC names (kWh)
        df_discharge: same structure
        lc_ids:       list of LC IDs common to both files
    """
    def _read(path):
        df = pd.read_excel(path)
        df['Date_parsed'] = pd.to_datetime(df['Date'], format='%m/%d/%Y', errors='coerce')
        device_cols = [c for c in df.columns if 'LC200' in str(c)]
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

    def _normalise(cols):
        out = {}
        for c in cols:
            parts = str(c).split(' - ')
            out[c] = parts[1].strip() if len(parts) >= 2 else str(c)
        return out

    df_chg = df_chg.rename(columns=_normalise(chg_cols)) if not df_chg.empty else df_chg
    df_dis = df_dis.rename(columns=_normalise(dis_cols)) if not df_dis.empty else df_dis

    common = sorted(set(df_chg.columns).intersection(df_dis.columns)) \
             if not df_chg.empty and not df_dis.empty else []
    if common:
        df_chg = df_chg[common]
        df_dis = df_dis[common]

    return df_chg, df_dis, common


def load_cycle_targets(path, default=1):
    """
    Load per-day cycle target file (Date, Cycle Target). Returns
    (dict {date->int target}, default_target). If path is None or
    missing, returns ({}, default).
    """
    import os
    if not path or not os.path.exists(path):
        return {}, default
    try:
        if str(path).lower().endswith('.csv'):
            df = pd.read_csv(path)
        else:
            df = pd.read_excel(path)
        date_col = next((c for c in df.columns if 'date' in str(c).lower()), df.columns[0])
        tgt_col  = next((c for c in df.columns
                          if 'cycle' in str(c).lower() or 'target' in str(c).lower()),
                          df.columns[-1])
        df['_date'] = pd.to_datetime(df[date_col], errors='coerce').dt.date
        df = df.dropna(subset=['_date'])
        return dict(zip(df['_date'], df[tgt_col].astype(int))), default
    except Exception as e:
        print(f"Warning: Could not load cycle targets file: {e}")
        return {}, default


# ── KPI CALCULATORS ────────────────────────────────────────────────────────────

def calc_availability(df, cols, excluded_device_hours: int = 0):
    """
    Calculates availability metrics.

    Args:
        df:                     working_status dataframe
        cols:                   device columns (LC200 list)
        excluded_device_hours:  device-hours to exclude from technical availability

    Returns dict with both:
        availability_raw       — pure (RUNNING+STANDBY)/total
        availability_technical — excludes scheduled maintenance, grid outages, etc.
    """
    total_device_hours = len(df) * len(cols)
    running = (df[cols] == 'RUNNING').sum().sum()
    standby = (df[cols] == 'STANDBY').sum().sum()
    fault   = (df[cols] == 'FAULT').sum().sum()
    stopped = (df[cols] == 'STOPPED').sum().sum()

    available   = running + standby
    unavailable = max(0, total_device_hours - available)

    # Raw availability — counts all hours
    raw = (available / total_device_hours * 100) if total_device_hours else 0

    # Technical availability — exclude planned outage / force majeure / etc.
    # Convention: excluded device-hours are treated as if they did not occur.
    # They are removed from the denominator (total). They are capped at the
    # actual unavailable hours so the metric never exceeds 100% and so
    # available time that happened to fall inside a maintenance window is
    # not double-counted as "given back".
    excluded_effective = min(int(excluded_device_hours), int(unavailable))
    eff_total = max(1, total_device_hours - excluded_effective)
    technical = (available / eff_total * 100) if eff_total else raw

    return {
        'availability':            raw,           # backwards-compat key
        'availability_raw':        raw,
        'availability_technical':  technical,
        'excluded_device_hours':   excluded_device_hours,
        'excluded_effective':      excluded_effective,
        'running_pct':   running  / total_device_hours * 100 if total_device_hours else 0,
        'standby_pct':   standby  / total_device_hours * 100 if total_device_hours else 0,
        'fault_pct':     fault    / total_device_hours * 100 if total_device_hours else 0,
        'stopped_pct':   stopped  / total_device_hours * 100 if total_device_hours else 0,
        'total_devices': len(cols),
        'total_hours':   len(df),
    }


def calc_daily_availability(df, cols):
    df = df.copy()
    df['day'] = df['Date_parsed'].dt.date
    rows = []
    for day, grp in df.groupby('day'):
        t = len(grp) * len(cols)
        r = (grp[cols] == 'RUNNING').sum().sum()
        s = (grp[cols] == 'STANDBY').sum().sum()
        f = (grp[cols] == 'FAULT').sum().sum()
        rows.append({'date': day,
                     'availability': (r + s) / t * 100 if t else 0,
                     'fault_pct':    f / t * 100 if t else 0})
    return pd.DataFrame(rows)


def calc_charge_discharge(df, cols):
    total    = len(df) * len(cols)
    charging = (df[cols] == 'CHARGING').sum().sum()
    disch    = (df[cols] == 'DISCHARGING').sum().sum()
    non_op   = (df[cols] == 'NON-OPERATING MODE').sum().sum()
    return {
        'charging_pct':    charging / total * 100 if total else 0,
        'discharging_pct': disch    / total * 100 if total else 0,
        'non_op_pct':      non_op   / total * 100 if total else 0,
        'charging_hrs':    charging / len(cols) if cols else 0,
        'discharging_hrs': disch    / len(cols) if cols else 0,
    }


# ── CAPACITY AVAILABILITY ─────────────────────────────────────────────────────

LC_CAP_NAMEPLATE_KWH   = 5504.0
LC_CAP_OPERATIONAL_KWH = 5504.0 * 0.90    # 4953.6 kWh, current 5-95% SOC window


def calc_capacity_availability(df_chg, df_dis, lc_cols, cycle_targets, default_target=1):
    """
    Per-LC per-day cycles_completed = min(daily_charge, daily_discharge) / capacity.
    Per-LC per-day availability = min(cycles_completed / target, 1.0) * 100%.
    Both operational (5-95%) and nameplate (0-100%) divisors are computed.

    Returns None if either energy frame is empty.
    """
    if df_chg is None or df_dis is None or df_chg.empty or df_dis.empty or not lc_cols:
        return None

    common_dates = df_chg.index.intersection(df_dis.index)
    df_chg = df_chg.loc[common_dates].copy()
    df_dis = df_dis.loc[common_dates].copy()

    cycles_op = pd.DataFrame(index=common_dates, columns=lc_cols, dtype=float)
    cycles_np = cycles_op.copy()
    for lc in lc_cols:
        m = pd.concat([df_chg[lc], df_dis[lc]], axis=1).min(axis=1)
        cycles_op[lc] = m / LC_CAP_OPERATIONAL_KWH
        cycles_np[lc] = m / LC_CAP_NAMEPLATE_KWH

    target_series = pd.Series(
        [cycle_targets.get(d, default_target) for d in common_dates],
        index=common_dates, name='target', dtype=float
    )

    avail_op = (cycles_op.div(target_series, axis=0).clip(upper=1.0) * 100.0)
    avail_np = (cycles_np.div(target_series, axis=0).clip(upper=1.0) * 100.0)

    daily = pd.DataFrame({
        'date':            list(common_dates),
        'target':          target_series.values,
        'cycles_op_avg':   cycles_op.mean(axis=1).values,
        'cycles_np_avg':   cycles_np.mean(axis=1).values,
        'availability_op': avail_op.mean(axis=1).values,
        'availability_np': avail_np.mean(axis=1).values,
        'fleet_charge_mwh':    df_chg[lc_cols].sum(axis=1).values / 1000.0,
        'fleet_discharge_mwh': df_dis[lc_cols].sum(axis=1).values / 1000.0,
    })

    period = {
        'availability_op_pct': float(daily['availability_op'].mean()),
        'availability_np_pct': float(daily['availability_np'].mean()),
        'avg_cycles_op':       float(daily['cycles_op_avg'].mean()),
        'avg_cycles_np':       float(daily['cycles_np_avg'].mean()),
        'total_charge_mwh':    float(daily['fleet_charge_mwh'].sum()),
        'total_discharge_mwh': float(daily['fleet_discharge_mwh'].sum()),
        'n_lcs':               len(lc_cols),
        'n_days':              len(common_dates),
        'cap_op_kwh':          LC_CAP_OPERATIONAL_KWH,
        'cap_np_kwh':          LC_CAP_NAMEPLATE_KWH,
    }

    per_lc = pd.DataFrame({
        'lc':                lc_cols,
        'avg_avail_op_pct':  avail_op.mean(axis=0).values,
        'avg_avail_np_pct':  avail_np.mean(axis=0).values,
        'total_charge_mwh':    df_chg[lc_cols].sum(axis=0).values / 1000.0,
        'total_discharge_mwh': df_dis[lc_cols].sum(axis=0).values / 1000.0,
    }).sort_values('avg_avail_op_pct')

    return {'daily': daily, 'period': period, 'per_lc': per_lc}


# ── CHART BUILDERS ─────────────────────────────────────────────────────────────

def _chart_availability(df_daily):
    fig, ax = plt.subplots(figsize=(11, 3.5))
    x    = range(len(df_daily))
    vals = df_daily['availability'].values
    flt  = df_daily['fault_pct'].values
    ax.fill_between(x, vals, alpha=0.12, color='#0071E3')
    ax.plot(x, vals, color='#0071E3', lw=2, marker='o', ms=3, label='Availability %')
    ax.bar(x, flt, color='#FF3B30', alpha=0.55, label='Fault %')
    ax.axhline(95, color='#34C759', ls='--', lw=1, alpha=0.7, label='Target 95%')
    dates = [str(d).replace('2026-','') for d in df_daily['date']]
    step  = max(1, len(dates) // 10)
    ax.set_xticks(list(range(0, len(dates), step)))
    ax.set_xticklabels([dates[i] for i in range(0, len(dates), step)], fontsize=7, rotation=30)
    ax.set_ylim(0, 105)
    ax.set_ylabel('Availability (%)', fontsize=8)
    ax.set_title('Daily System Availability', fontsize=10, fontweight='bold', color='#1A2B45')
    ax.legend(fontsize=7, loc='lower right')
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    ax.grid(axis='y', ls='--', alpha=0.3)
    fig.tight_layout()
    return fig


def _chart_energy(df_exp, df_imp):
    fig, ax = plt.subplots(figsize=(11, 3.5))
    if not df_exp.empty:
        ax.bar([i - 0.2 for i in range(len(df_exp))],
               df_exp['Energy_Exported_kWh'] / 1000,
               width=0.4, color='#0071E3', alpha=0.8, label='Exported (MWh)')
    if not df_imp.empty:
        ax.bar([i + 0.2 for i in range(len(df_imp))],
               df_imp['Energy_Imported_kWh'] / 1000,
               width=0.4, color='#34C759', alpha=0.8, label='Imported (MWh)')
    if not df_exp.empty:
        labels = [str(d)[:10].replace('2026-','') for d in df_exp['Date']]
        step   = max(1, len(labels) // 10)
        ax.set_xticks(list(range(0, len(labels), step)))
        ax.set_xticklabels([labels[i] for i in range(0, len(labels), step)],
                            fontsize=7, rotation=30)
    ax.set_ylabel('Energy (MWh)', fontsize=8)
    ax.set_title('Daily Energy Exported / Imported', fontsize=10, fontweight='bold',
                 color='#1A2B45')
    ax.legend(fontsize=7)
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    ax.grid(axis='y', ls='--', alpha=0.3)
    fig.tight_layout()
    return fig


def _chart_pies(kpis, cd):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9, 3.2))
    for ax, lbls, vals, clrs, title in [
        (ax1,
         ['Running', 'Standby', 'Fault', 'Stopped'],
         [kpis['running_pct'], kpis['standby_pct'], kpis['fault_pct'], kpis['stopped_pct']],
         ['#0071E3', '#34C759', '#FF3B30', '#FF9500'],
         'Working Status'),
        (ax2,
         ['Charging', 'Discharging', 'Non-Operating'],
         [cd['charging_pct'], cd['discharging_pct'], cd['non_op_pct']],
         ['#0071E3', '#AF52DE', '#6B7A8D'],
         'Charge / Discharge'),
    ]:
        fv = [(l, v, c) for l, v, c in zip(lbls, vals, clrs) if v > 0]
        if fv:
            fl, fvals, fc = zip(*fv)
            ax.pie(fvals, labels=fl, colors=fc, autopct='%1.1f%%',
                   startangle=90, pctdistance=0.75,
                   textprops={'fontsize': 7})
        ax.set_title(title, fontsize=9, fontweight='bold', color='#1A2B45')
    fig.tight_layout()
    return fig


def _chart_alarms(df_warn):
    top = df_warn['Trigger name'].value_counts().head(8)
    fig, ax = plt.subplots(figsize=(11, 3.5))
    bars = ax.barh(range(len(top)), top.values, color='#FF9500', alpha=0.85)
    ax.set_yticks(range(len(top)))
    ax.set_yticklabels([str(l)[:55] for l in top.index], fontsize=7)
    ax.invert_yaxis()
    ax.set_xlabel('Occurrences', fontsize=8)
    ax.set_title('Top 8 Warning Alarms', fontsize=10, fontweight='bold', color='#1A2B45')
    for bar, val in zip(bars, top.values):
        ax.text(bar.get_width() + 0.5, bar.get_y() + bar.get_height() / 2,
                str(val), va='center', fontsize=7, color='#1A2B45')
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    ax.grid(axis='x', ls='--', alpha=0.3)
    fig.tight_layout()
    return fig


def _chart_capacity(df_daily):
    """Daily fleet-wide capacity availability — operational + nameplate + cycle target."""
    fig, ax1 = plt.subplots(figsize=(11, 3.5))
    n = len(df_daily); x = range(n)
    op = df_daily['availability_op'].values
    npv = df_daily['availability_np'].values
    ax1.fill_between(x, op, alpha=0.12, color='#0071E3')
    ax1.plot(x, op, color='#0071E3', lw=2, marker='o', ms=3,
             label='Operational (5–95%)')
    ax1.plot(x, npv, color='#AF52DE', lw=1.5, ls='--', marker='s', ms=3,
             label='Nameplate (0–100%)')
    ax1.axhline(95, color='#34C759', ls=':', lw=1, alpha=0.7, label='Target 95%')
    ax1.set_ylim(0, 105)
    ax1.set_ylabel('Capacity Availability (%)', fontsize=8)
    ax1.set_title('Daily Capacity Availability — Operational vs Nameplate',
                  fontsize=10, fontweight='bold', color='#1A2B45')
    dates = [str(d).replace('2026-','') for d in df_daily['date']]
    step  = max(1, n // 12)
    ax1.set_xticks(list(range(0, n, step)))
    ax1.set_xticklabels([dates[i] for i in range(0, n, step)],
                        fontsize=7, rotation=30)

    ax2 = ax1.twinx()
    ax2.step(x, df_daily['target'].values, where='mid',
             color='#FF9500', lw=1.2, alpha=0.75, label='Cycle target')
    ax2.set_ylim(0, max(3, df_daily['target'].max() + 1))
    ax2.set_ylabel('Cycle target', fontsize=8, color='#FF9500')
    ax2.tick_params(axis='y', colors='#FF9500', labelsize=7)
    ax2.spines['top'].set_visible(False)

    ax1.spines['top'].set_visible(False)
    ax1.grid(axis='y', ls='--', alpha=0.3)
    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, fontsize=7, loc='lower right', ncol=2)
    fig.tight_layout()
    return fig


# ── COVER ──────────────────────────────────────────────────────────────────────

def _build_cover(story, report_month, site_name, num_devices, period_str):
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
        ('BACKGROUND',    (0,0), (-1,-1), NAVY),
        ('ALIGN',         (0,0), (-1,-1), 'CENTER'),
        ('TOPPADDING',    (0,0), (-1,-1), 20),
        ('BOTTOMPADDING', (0,0), (-1,-1), 20),
    ]))
    story.append(t)
    story.append(Spacer(1, 12*mm))
    info = [
        ['Site / Project:',   site_name],
        ['Reporting Period:',  period_str],
        ['Total Devices:',    f'{num_devices} containers (LC200 units)'],
        ['Report Generated:', datetime.now().strftime('%Y-%m-%d %H:%M')],
        ['Report Type:',      'Monthly Operations & Performance Summary'],
    ]
    t2 = Table(info, colWidths=[55*mm, 115*mm])
    t2.setStyle(TableStyle([
        ('BACKGROUND',    (0,0), (-1,-1), GREY_BG),
        ('TOPPADDING',    (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING',   (0,0), (-1,-1), 10),
        ('RIGHTPADDING',  (0,0), (-1,-1), 10),
        ('GRID',          (0,0), (-1,-1), 0.3, GREY_LINE),
        ('FONT',          (0,0), (0,-1),  'Helvetica-Bold'),
    ]))
    story.append(t2)
    story.append(Spacer(1, 8*mm))
    story.append(Paragraph(
        'This report has been automatically generated from SCADA system exports.',
        STYLE_SMALL))
    story.append(PageBreak())


# ── MAIN FUNCTION (called from UI) ────────────────────────────────────────────

def generate_scada_report(
    working_status_path: str,
    charge_discharge_path: str,
    alarm_path: str,
    exported_path: str,
    imported_path: str,
    output_path: str,
    site_name: str = 'BESS Site',
    progress_callback=None,
    exclusions: list = None,    # list of dicts from availability_service
    lc_charge_path: str = None,
    lc_discharge_path: str = None,
    cycle_targets_path: str = None,
    default_cycle_target: int = 1,
    project_blocks: list = None,        # explicit list of block IDs from project
    plant_capacity_mw: float = None,
    per_block_capacity_mw: float = None,
    redundancy_threshold_pct: float = 100.0,
) -> str:
    """
    Generates a PDF report from SCADA export files.

    exclusions: optional list of {exclusion_type, date_from, date_to,
                affected_blocks, description} dicts.
                Used for technical availability calculation.
    """
    def progress(msg):
        if progress_callback:
            progress_callback(msg)
        print(f"[SCADA Report] {msg}")

    progress("Loading working status data...")
    df_ws, ws_cols = load_working_status(working_status_path)

    progress("Loading charge/discharge data...")
    df_cd, cd_cols = load_charge_discharge(charge_discharge_path)

    progress("Loading alarm data...")
    alarms = load_alarms(alarm_path)

    progress("Loading energy data...")
    df_exp, df_imp = load_energy(exported_path, imported_path)

    # ── Capacity availability (optional) ──────────────────────────────────
    cap_kpis = None
    if lc_charge_path and lc_discharge_path:
        progress("Loading LC daily charge/discharge data...")
        df_lc_chg, df_lc_dis, lc_ids = load_lc_daily_energy(
            lc_charge_path, lc_discharge_path)
        cycle_targets, default_t = load_cycle_targets(
            cycle_targets_path, default=default_cycle_target)
        progress("Calculating capacity availability...")
        cap_kpis = calc_capacity_availability(
            df_lc_chg, df_lc_dis, lc_ids, cycle_targets,
            default_target=default_t)

    # ── Calculate excluded device-hours (if any) ──────────────────────────
    excluded_device_hours = 0
    excluded_breakdown    = {}
    if exclusions:
        progress("Applying availability exclusions...")
        from services.availability_service import calculate_excluded_hours
        all_dates  = sorted(set(df_ws['Date_parsed'].dt.date.dropna()))
        # Use the project's actual block list when supplied; otherwise derive
        # from the SCADA column names (LC200 BB.CC → block BB) so the
        # affected_blocks selector lines up with reality.
        if project_blocks:
            all_blocks = list(project_blocks)
        else:
            import re as _re
            block_ids = set()
            for c in ws_cols:
                m = _re.search(r'LC200\s*(\d+)\.\d+', str(c))
                if m:
                    block_ids.add(int(m.group(1)))
            all_blocks = sorted(block_ids) if block_ids else [1]
        result = calculate_excluded_hours(
            exclusions, all_dates, all_blocks, len(ws_cols)
        )
        excluded_device_hours = result["excluded_device_hours"]
        excluded_breakdown    = result["breakdown"]

    progress("Calculating KPIs...")
    kpis     = calc_availability(df_ws, ws_cols, excluded_device_hours)
    cd       = calc_charge_discharge(df_cd, cd_cols)
    df_daily = calc_daily_availability(df_ws, ws_cols)

    # Date range info
    date_min    = df_ws['Date_parsed'].min()
    date_max    = df_ws['Date_parsed'].max()
    period_str  = f"{date_min.strftime('%d %B %Y')} — {date_max.strftime('%d %B %Y')}"
    report_month= date_min.strftime('%B %Y')

    # Energy totals
    total_exp   = df_exp['Energy_Exported_kWh'].sum() if not df_exp.empty else 0
    total_imp   = df_imp['Energy_Imported_kWh'].sum() if not df_imp.empty else 0
    net_energy  = total_exp - total_imp

    # Alarm counts
    prod_alarms = len(alarms.get('Production',     pd.DataFrame()))
    warn_alarms = len(alarms.get('Warning',        pd.DataFrame()))
    comm_alarms = len(alarms.get('Communications', pd.DataFrame()))
    total_alarms= prod_alarms + warn_alarms + comm_alarms

    progress("Building PDF...")

    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        leftMargin=20*mm, rightMargin=20*mm,
        topMargin=20*mm,  bottomMargin=20*mm,
        title=f'BESS Operations Report — {report_month}',
        author='BESS Tracker Field Service',
    )
    story = []

    # Cover
    _build_cover(story, report_month, site_name, kpis['total_devices'], period_str)

    # Executive Summary
    story.append(section_header('Executive Summary', 'Summary'))
    story.append(Spacer(1, 4*mm))

    # If exclusions exist — show both raw and technical availability
    if exclusions and excluded_device_hours > 0:
        story.append(kpi_row([
            (f"{kpis['availability_raw']:.1f}%",       'Raw Availability',       '#6B7A8D'),
            (f"{kpis['availability_technical']:.1f}%", 'Technical Availability', '#0071E3'),
            (f"{kpis['total_devices']}",                'Active Containers',      '#34C759'),
            (f"{total_exp/1000:,.0f} MWh",             'Energy Exported',        '#FF9500'),
        ]))
    else:
        story.append(kpi_row([
            (f"{kpis['availability']:.1f}%", 'System Availability', '#0071E3'),
            (f"{kpis['total_devices']}",     'Active Containers',   '#34C759'),
            (f"{total_exp/1000:,.0f} MWh",  'Energy Exported',     '#FF9500'),
            (f"{total_imp/1000:,.0f} MWh",  'Energy Imported',     '#AF52DE'),
        ]))
    story.append(Spacer(1, 4*mm))
    story.append(kpi_row([
        (f"{kpis['fault_pct']:.2f}%", 'Fault Rate',       '#FF3B30'),
        (f"{prod_alarms}",             'Production Alarms','#FF3B30'),
        (f"{warn_alarms}",             'Warnings',         '#FF9500'),
        (f"{comm_alarms}",             'Comm. Alarms',     '#6B7A8D'),
    ]))
    story.append(Spacer(1, 5*mm))

    headline_avail = kpis['availability_technical'] if (exclusions and excluded_device_hours > 0) else kpis['availability_raw']
    avail_status = 'above' if headline_avail >= 95 else 'below'
    label = 'technical availability' if (exclusions and excluded_device_hours > 0) else 'availability'
    story.append(Paragraph(
        f"During <b>{period_str}</b>, the {site_name} BESS system maintained an overall "
        f"{label} of <b>{headline_avail:.2f}%</b> ({avail_status} the 95% target). "
        f"<b>{kpis['total_devices']} LC200 containers</b> were monitored. "
        f"Energy exported: <b>{total_exp/1000:,.1f} MWh</b>, imported: "
        f"<b>{total_imp/1000:,.1f} MWh</b>. "
        f"Net {'export' if net_energy >= 0 else 'import'}: "
        f"<b>{abs(net_energy)/1000:,.1f} MWh</b>. "
        f"Total alarm events: <b>{total_alarms}</b>.",
        STYLE_BODY))
    story.append(hr())

    # Availability
    story.append(section_header('System Availability', 'Availability'))
    story.append(Spacer(1, 3*mm))
    story.append(fig_to_image(_chart_availability(df_daily), 170, 65))
    story.append(Paragraph('Figure 1: Daily system availability (%) and fault rate (%)', STYLE_CAP))
    story.append(Spacer(1, 3*mm))
    story.append(styled_table(
        ['Status', '% of Time', 'Avg Hours/Device', 'Description'],
        [
            ['RUNNING',      f"{kpis['running_pct']:.1f}%",
             f"{int(kpis['running_pct']*kpis['total_hours']/100)} hrs", 'Active operation'],
            ['STANDBY',      f"{kpis['standby_pct']:.1f}%",
             f"{int(kpis['standby_pct']*kpis['total_hours']/100)} hrs", 'Ready, not operating'],
            ['FAULT',        f"{kpis['fault_pct']:.2f}%",
             f"{int(kpis['fault_pct']*kpis['total_hours']/100)} hrs",  'System fault'],
            ['STOPPED',      f"{kpis['stopped_pct']:.2f}%",
             f"{int(kpis['stopped_pct']*kpis['total_hours']/100)} hrs",'Manually stopped'],
            ['AVAILABILITY', f"{kpis['availability']:.2f}%", '—', 'Running + Standby'],
        ],
        col_widths=[40*mm, 35*mm, 45*mm, 50*mm]
    ))
    story.append(Spacer(1, 3*mm))
    # Daily detail table
    avail_rows = []
    for _, row in df_daily.iterrows():
        status = 'OK' if row['availability'] >= 95 else 'Low'
        avail_rows.append([str(row['date']), f"{row['availability']:.1f}%",
                            f"{row['fault_pct']:.2f}%", status])
    story.append(styled_table(['Date','Availability','Fault Rate','Status'],
                               avail_rows[:31],
                               col_widths=[45*mm,40*mm,40*mm,45*mm]))
    story.append(hr())

    # ── Availability Exclusions Section (only if any exist) ──────────────
    if exclusions and excluded_device_hours > 0:
        story.append(section_header('Availability Exclusions', 'Exclusions'))
        story.append(Spacer(1, 3*mm))

        excl_summary = (
            f"<b>{len(exclusions)}</b> exclusion event(s) recorded during the period, "
            f"totaling <b>{excluded_device_hours:,}</b> excluded device-hours. "
            f"These hours are removed from the technical availability calculation "
            f"as they represent downtime caused by external factors or planned activities."
        )
        story.append(Paragraph(excl_summary, STYLE_BODY))
        story.append(Spacer(1, 3*mm))

        # Breakdown by type
        breakdown_rows = [
            [t, f"{int(h):,} device-hours",
             f"{(h/excluded_device_hours*100 if excluded_device_hours else 0):.1f}%"]
            for t, h in excluded_breakdown.items() if h > 0
        ]
        if breakdown_rows:
            story.append(Paragraph('<b>Exclusion Breakdown by Type</b>', STYLE_H3))
            story.append(styled_table(
                ['Exclusion Type', 'Device-Hours', '% of Excluded'],
                breakdown_rows,
                col_widths=[80*mm, 50*mm, 40*mm]
            ))
            story.append(Spacer(1, 3*mm))

        # Detail table — each exclusion event
        story.append(Paragraph('<b>Exclusion Event Detail</b>', STYLE_H3))
        detail_rows = []
        for exc in exclusions:
            t_from = exc.get("time_from","00:00") or "00:00"
            t_to   = exc.get("time_to","23:59")   or "23:59"
            detail_rows.append([
                exc.get("exclusion_type",""),
                f"{exc.get('date_from','')} {t_from}",
                f"{exc.get('date_to','')} {t_to}",
                exc.get("affected_blocks","") or "All blocks",
                exc.get("description",""),
            ])
        story.append(styled_table(
            ['Type', 'From', 'To', 'Blocks', 'Description'],
            detail_rows,
            col_widths=[35*mm, 28*mm, 28*mm, 22*mm, 57*mm]
        ))
        story.append(hr())

    # ── Capacity Availability Section (only if LC energy files provided) ─
    if cap_kpis is not None:
        per   = cap_kpis['period']
        daily_cap = cap_kpis['daily']
        per_lc    = cap_kpis['per_lc']

        story.append(section_header('Capacity Availability (per LC, per cycle)',
                                     'Capacity'))
        story.append(Spacer(1, 3*mm))

        story.append(Paragraph(
            f"This metric assesses whether each LC delivered its contracted "
            f"charge / discharge cycles. <b>Cycles completed</b> per LC per day "
            f"= min(daily charge, daily discharge) ÷ per-cycle capacity. "
            f"Two divisors are shown side-by-side: <b>operational</b> "
            f"({per['cap_op_kwh']:,.0f} kWh, 5–95% SOC, current operating window) "
            f"and <b>nameplate</b> ({per['cap_np_kwh']:,.0f} kWh, 0–100% SOC). "
            f"Daily cycle target is sourced from the cycle-targets file "
            f"(default: {default_cycle_target}). Per-day availability is capped "
            f"at 100% so multi-cycle days do not inflate the metric.",
            STYLE_BODY))
        story.append(Spacer(1, 3*mm))

        story.append(kpi_row([
            (f"{per['availability_op_pct']:.1f}%",
             'Capacity Avail. (Operational, 5–95%)', '#0071E3'),
            (f"{per['availability_np_pct']:.1f}%",
             'Capacity Avail. (Nameplate, 0–100%)', '#AF52DE'),
            (f"{per['avg_cycles_op']:.2f}",
             'Avg cycles / LC / day', '#34C759'),
            (f"{per['n_lcs']}",
             'LCs analysed', '#6B7A8D'),
        ]))
        story.append(Spacer(1, 4*mm))

        story.append(fig_to_image(_chart_capacity(daily_cap), 170, 65))
        story.append(Paragraph(
            'Figure 1b: Daily fleet-wide capacity availability — operational '
            '(5–95%) and nameplate (0–100%) divisors, with cycle target on '
            'the right axis.', STYLE_CAP))
        story.append(Spacer(1, 3*mm))

        # Daily detail table
        story.append(Paragraph('<b>Daily Capacity Detail (fleet average)</b>',
                                STYLE_H3))
        cap_rows = []
        for _, row in daily_cap.iterrows():
            tgt = int(row['target'])
            cap_rows.append([
                str(row['date']),
                str(tgt),
                f"{row['fleet_charge_mwh']:,.1f}",
                f"{row['fleet_discharge_mwh']:,.1f}",
                f"{row['cycles_op_avg']:.2f} / {tgt}",
                f"{row['availability_op']:.1f}%",
                f"{row['availability_np']:.1f}%",
                'OK' if row['availability_op'] >= 95 else 'Low',
            ])
        story.append(styled_table(
            ['Date', 'Target', 'Charge MWh', 'Discharge MWh',
             'Cycles done/target', 'Avail Op.', 'Avail Np.', 'Status'],
            cap_rows,
            col_widths=[22*mm, 15*mm, 22*mm, 24*mm, 28*mm, 18*mm, 18*mm, 23*mm]
        ))
        story.append(Spacer(1, 3*mm))

        # Bottom-10 LCs
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
        story.append(hr())

    # Charge/Discharge
    story.append(section_header('Charge / Discharge Operations', 'Charge/Discharge'))
    story.append(Spacer(1, 3*mm))
    story.append(fig_to_image(_chart_pies(kpis, cd), 170, 65))
    story.append(Paragraph('Figure 2: Working status and charge/discharge distribution', STYLE_CAP))
    story.append(Spacer(1, 3*mm))
    story.append(styled_table(
        ['Mode', '% of Time', 'Avg Hours/Device', 'Description'],
        [
            ['CHARGING',      f"{cd['charging_pct']:.1f}%",
             f"{cd['charging_hrs']:.0f} hrs",    'Grid to battery'],
            ['DISCHARGING',   f"{cd['discharging_pct']:.1f}%",
             f"{cd['discharging_hrs']:.0f} hrs", 'Battery to grid'],
            ['NON-OPERATING', f"{cd['non_op_pct']:.1f}%",
             '—', 'Idle / standby mode'],
        ],
        col_widths=[40*mm, 35*mm, 45*mm, 50*mm]
    ))
    story.append(hr())

    # Energy
    story.append(section_header('Energy Performance', 'Energy'))
    story.append(Spacer(1, 3*mm))
    story.append(kpi_row([
        (f"{total_exp/1000:,.1f}", 'Total Exported (MWh)', '#0071E3'),
        (f"{total_imp/1000:,.1f}", 'Total Imported (MWh)', '#34C759'),
        (f"{abs(net_energy)/1000:,.1f}",
         f"Net {'Export' if net_energy>=0 else 'Import'} (MWh)", '#FF9500'),
    ]))
    story.append(Spacer(1, 4*mm))
    if not df_exp.empty or not df_imp.empty:
        story.append(fig_to_image(_chart_energy(df_exp, df_imp), 170, 65))
        story.append(Paragraph('Figure 3: Daily energy exported and imported (MWh)', STYLE_CAP))
        story.append(Spacer(1, 3*mm))
        if not df_exp.empty and not df_imp.empty:
            merged = pd.merge(df_exp, df_imp, on='Date', how='outer').sort_values('Date')
            energy_rows = []
            for _, row in merged.iterrows():
                e = row.get('Energy_Exported_kWh', 0)
                i = row.get('Energy_Imported_kWh', 0)
                n = (e - i) if pd.notna(e) and pd.notna(i) else 0
                energy_rows.append([
                    str(row['Date'])[:10],
                    f"{e/1000:,.2f}" if pd.notna(e) else '—',
                    f"{i/1000:,.2f}" if pd.notna(i) else '—',
                    f"{n/1000:,.2f}" if pd.notna(e) and pd.notna(i) else '—',
                ])
            story.append(styled_table(['Date','Exported (MWh)','Imported (MWh)','Net (MWh)'],
                                       energy_rows, col_widths=[45*mm,42*mm,42*mm,41*mm]))
    story.append(hr())

    # Alarms
    story.append(section_header('Alarm & Fault Analysis', 'Alarms'))
    story.append(Spacer(1, 3*mm))
    story.append(kpi_row([
        (f"{prod_alarms}", 'Production Alarms', '#FF3B30'),
        (f"{warn_alarms}", 'Warning Alarms',    '#FF9500'),
        (f"{comm_alarms}", 'Comm. Alarms',      '#6B7A8D'),
        (f"{total_alarms}",'Total Events',       '#1A2B45'),
    ]))
    story.append(Spacer(1, 4*mm))
    df_warn = alarms.get('Warning', pd.DataFrame())
    if not df_warn.empty and 'Trigger name' in df_warn.columns:
        story.append(fig_to_image(_chart_alarms(df_warn), 170, 65))
        story.append(Paragraph('Figure 4: Top 8 warning alarm types', STYLE_CAP))
        story.append(Spacer(1, 3*mm))
        top = df_warn['Trigger name'].value_counts().head(15)
        story.append(styled_table(
            ['Alarm Type', 'Count', '% of Total'],
            [[n[:60], str(c), f"{c/len(df_warn)*100:.1f}%"] for n, c in top.items()],
            col_widths=[100*mm, 35*mm, 35*mm]
        ))
        story.append(Spacer(1, 3*mm))
    df_prod = alarms.get('Production', pd.DataFrame())
    if not df_prod.empty:
        story.append(Paragraph('<b>Production Alarms (first 20)</b>', STYLE_H3))
        prod_rows = []
        for _, row in df_prod.head(20).iterrows():
            dur = ''
            try:
                if pd.notna(row.get('Activated')) and pd.notna(row.get('Deactivation')):
                    d = (pd.to_datetime(row['Deactivation']) -
                         pd.to_datetime(row['Activated'])).total_seconds() / 60
                    dur = f"{d:.0f} min"
            except Exception:
                pass
            prod_rows.append([
                str(row.get('Element',''))[:20],
                str(row.get('Trigger name',''))[:40],
                str(row.get('Activated',''))[:16],
                str(row.get('Deactivation',''))[:16],
                dur,
            ])
        story.append(styled_table(
            ['Element','Alarm Type','Start','End','Duration'],
            prod_rows,
            col_widths=[30*mm, 72*mm, 28*mm, 28*mm, 12*mm]
        ))
    story.append(hr())

    # Conclusions
    story.append(section_header('Conclusions & Observations', 'Conclusions'))
    story.append(Spacer(1, 3*mm))
    for i, text in enumerate([
        f"System availability of <b>{kpis['availability']:.2f}%</b> was recorded — "
        f"{'meeting' if kpis['availability'] >= 95 else 'below'} the 95% target.",
        f"All <b>{kpis['total_devices']} LC200 containers</b> monitored. "
        f"Fault rate: <b>{kpis['fault_pct']:.2f}%</b>.",
        f"Energy: <b>{total_exp/1000:,.1f} MWh exported</b>, "
        f"<b>{total_imp/1000:,.1f} MWh imported</b>. "
        f"Net: {abs(net_energy)/1000:,.1f} MWh {'export' if net_energy>=0 else 'import'}.",
        f"Total alarm events: <b>{total_alarms}</b>. "
        f"Production: {prod_alarms}, Warnings: {warn_alarms}, Comms: {comm_alarms}.",
    ], 1):
        story.append(Paragraph(f'{i}.  {text}', STYLE_BODY))
        story.append(Spacer(1, 2*mm))

    story.append(Spacer(1, 6*mm))
    story.append(Paragraph(
        f'Generated by BESS Tracker — Field Service Portal  |  '
        f'{datetime.now().strftime("%Y-%m-%d %H:%M")}',
        STYLE_SMALL))

    doc.build(story)
    progress(f"PDF saved: {output_path}")
    return output_path
# end of scada_report_service.py
