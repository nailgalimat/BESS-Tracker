"""
run_bukhara_report.py
---------------------
Standalone driver for the Bukhara monthly operations report.

Usage (defaults read from the /sessions/.../uploads folder when developing):

    python run_bukhara_report.py

Or override paths via env vars / CLI:

    INPUT_DIR=/path/to/scada OUT_DIR=./reports python run_bukhara_report.py

The first time it runs, monthly comparison shows only the current month plus
whatever is in data/monthly_history.json. Each subsequent run appends to that
file automatically (last 12 months are kept).

To plug into the desktop UI later, import generate_bukhara_report() directly:

    from services.bukhara_report_service import generate_bukhara_report
    generate_bukhara_report(..., project_details=..., recommendations=...)
"""
import os, sys, time, argparse

# Make sure the local package is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from services.bukhara_report_service import generate_bukhara_report


# ── Defaults — adjust to your environment ─────────────────────────────────────

DEFAULT_INPUT_DIR = os.environ.get(
    'INPUT_DIR',
    '/sessions/bold-magical-planck/mnt/uploads',
)
DEFAULT_OUTPUT_DIR = os.environ.get(
    'OUT_DIR',
    os.path.dirname(os.path.abspath(__file__)),
)


# Project description — sourced from the SCADA contract; adjust per project.
NUR_BUKHARA_PROJECT_DETAILS = {
    'Customer':         'MASDAR',
    'O&M Company':      'MASDAR MSTS',
    'OEM':              'Sungrow Power Supply Co. Ltd.',
    'Project Capacity': '63 MW / 126 MWh',
    'Equipment':        ('ST5015UX-S-2H_V152 (30); '
                          'SC5500UD-MV_V129 (15); SCC_V152 (15)'),
}

# Recommendations + planned activities are operator-supplied and edited each
# month. Centralising them here makes them visible to the next month's author.
RECOMMENDATIONS_APRIL_2026 = [
    ('Auxiliary power: consistent supply is a critical dependency — '
     'investigate alternatives to DG-only fallback for future outages.'),
    ('Shutdown procedure: reduce power setpoint to 0 before suspending '
     'charging/discharging. Power down only once PCS is in Standby.'),
    ('Start-up procedure: start all blocks first, confirm Standby, then '
     'issue power setpoints from SCADA PPC screen.'),
    ('Idle management: keep all blocks in Standby mode during non-operating '
     'intervals and transitions between charge/discharge cycles.'),
]

PLANNED_MAY_2026 = [
    'PM activities scheduled for 12.05.2026 — 26.05.2026 (actual dates '
    'subject to site conditions).',
    'Continue daily performance monitoring and flag availability / RTE '
    'anomalies to Sungrow for rapid response.',
]

SAFETY_INCIDENTS_APRIL_2026 = [
    {'incident': 'AC Breaker Fault',
     'equipment_loss': '-',
     'weight': 'Low — no harm to personnel or major equipment damage',
     'countermeasure': 'Reboot the PCS'},
    {'incident': 'Soft Start (DC switch) Fault',
     'equipment_loss': '-',
     'weight': 'Low — no harm to personnel or major equipment damage',
     'countermeasure': 'Reboot the PCS'},
]

SITE_VISITS_APRIL_2026 = [
    ('01.04.2026 — 13.04.2026: Sungrow service engineer and technicians '
     'visited the site to support the O&M team during the planned auxiliary '
     'power shutdown. After restoration of aux supply they remained on site '
     'to monitor equipment performance.'),
]


def main():
    parser = argparse.ArgumentParser(
        description='Generate the Nur Bukhara monthly operations report.')
    parser.add_argument('--input-dir',  default=DEFAULT_INPUT_DIR,
                        help='Folder containing the SCADA export xlsx files')
    parser.add_argument('--output-dir', default=DEFAULT_OUTPUT_DIR,
                        help='Destination folder for the report')
    parser.add_argument('--format', choices=['docx', 'pdf', 'both'], default='docx',
                        help='Output format: docx (default), pdf, or both')
    parser.add_argument('--month-label', default='April_2026',
                        help='Label used in the output filename')
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    ext = 'docx' if args.format in ('docx', 'both') else 'pdf'
    output_path = os.path.join(args.output_dir,
        f'Nur_Bukhara_Monthly_Report_{args.month_label}.{ext}')

    print(f"Input dir : {args.input_dir}")
    print(f"Output dir: {args.output_dir}")
    print(f"Target    : {output_path}")
    print()

    t = time.time()
    generate_bukhara_report(
        site_kpi_path     = os.path.join(args.input_dir,
            'Bukhara_Main KPI_5 minute data_April_2026.xlsx'),
        overall_lc_path   = os.path.join(args.input_dir,
            'Bukhara_Overall_LC data_April_2026.xlsx'),
        battery_unit_path = os.path.join(args.input_dir,
            'Overall_Battery Unit Data_April_2026.xlsx'),
        meter_daily_path  = os.path.join(args.input_dir,
            'Main_Meter_daily_April_2026.xlsx'),
        alarm_path        = os.path.join(args.input_dir,
            'Monthly Alarm Report_2026_04_01.XLSX'),
        output_path       = output_path,
        site_name         = 'Nur Bukhara 63MW/126MWh BESS Plant',
        project_details   = NUR_BUKHARA_PROJECT_DETAILS,
        plant_capacity_mw       = 63.0,
        per_block_capacity_mw   = 63.0 / 15,
        redundancy_threshold_pct = 100.0,
        cycles_accum_avg        = 301.0,
        safety_incidents        = SAFETY_INCIDENTS_APRIL_2026,
        site_visits             = SITE_VISITS_APRIL_2026,
        recommendations         = RECOMMENDATIONS_APRIL_2026,
        planned_next_period     = PLANNED_MAY_2026,
        # history_records auto-loaded from data/monthly_history.json
    )
    print(f"\nDone in {time.time()-t:.1f}s")
    if os.path.exists(output_path):
        print(f"Size: {os.path.getsize(output_path):,} bytes")


if __name__ == '__main__':
    main()
