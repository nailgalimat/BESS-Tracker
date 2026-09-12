"""
run_tashkent_report.py
----------------------
Standalone driver for the Tashkent monthly operations report (70 blocks).
"""
import os, sys, time, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from services.tashkent_report_service import generate_tashkent_report

DEFAULT_INPUT_DIR = os.environ.get(
    'INPUT_DIR',
    '/sessions/bold-magical-planck/mnt/uploads',
)
DEFAULT_OUTPUT_DIR = os.environ.get(
    'OUT_DIR',
    os.path.dirname(os.path.abspath(__file__)),
)

PROJECT_DETAILS = {
    'Site':             'Tashkent BESS',
    'Project Capacity': '70 blocks × ~11 MWh (operational 5-95% SOC)',
    'Configuration':    '70 blocks × 2 containers × multiple racks per container',
    'OEM':              'Sungrow Power Supply Co. Ltd.',
}

RECOMMENDATIONS = [
    'Continue daily monitoring of block 45 family — chronic underperformer in prior reports.',
    'Review BSC Input Dry Node Fault occurrences — investigate field wiring on affected blocks.',
    'Address Firefighting system fire alarms (104 events) — likely false positives, calibrate detectors.',
]

PLANNED = [
    'PM activities to be scheduled with O&M team.',
    'Continue daily performance monitoring; escalate availability anomalies.',
]


def main():
    parser = argparse.ArgumentParser(description='Generate the Tashkent monthly report.')
    parser.add_argument('--input-dir',  default=DEFAULT_INPUT_DIR)
    parser.add_argument('--output-dir', default=DEFAULT_OUTPUT_DIR)
    parser.add_argument('--format', choices=['docx', 'pdf', 'both'], default='docx',
                        help='Output format: docx (default), pdf, or both')
    parser.add_argument('--month-label', default='March_2026')
    args = parser.parse_args()
    os.makedirs(args.output_dir, exist_ok=True)
    ext = 'docx' if args.format in ('docx', 'both') else 'pdf'
    output_path = os.path.join(args.output_dir,
        f'Tashkent_Monthly_Report_{args.month_label}.{ext}')

    print(f"Input dir : {args.input_dir}")
    print(f"Output dir: {args.output_dir}")
    print(f"Target    : {output_path}\n")

    t = time.time()
    generate_tashkent_report(
        working_status_path   = os.path.join(args.input_dir, 'LC working status.xlsx'),
        pcs_cd_path           = os.path.join(args.input_dir, 'PCS Charge_Discharge status.xlsx'),
        soc_path              = os.path.join(args.input_dir, 'SOC March.xlsx'),
        soh_snapshot_path     = os.path.join(args.input_dir, 'SOH last day of month.xlsx'),
        lc_charge_path        = os.path.join(args.input_dir, 'LC daily charge.xlsx'),
        lc_discharge_path     = os.path.join(args.input_dir, 'LC Daily discharge.xlsx'),
        hv_meter_daily_path   = os.path.join(args.input_dir, 'HV meter daily import and export.xlsx'),
        alarm_path            = os.path.join(args.input_dir, 'Alarm report.XLSX'),
        output_path           = output_path,
        site_name             = 'Tashkent BESS',
        project_details       = PROJECT_DETAILS,
        # plant_capacity_mw and per_block_capacity_mw left None until confirmed;
        # plant-level-hours availability will be reported as "container-aggregate" only.
        recommendations       = RECOMMENDATIONS,
        planned_next_period   = PLANNED,
        output_format         = args.format,
    )
    print(f"\nDone in {time.time()-t:.1f}s")
    if os.path.exists(output_path):
        print(f"Size: {os.path.getsize(output_path):,} bytes")


if __name__ == '__main__':
    main()
