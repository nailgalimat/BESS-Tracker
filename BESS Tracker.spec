# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for BESS Tracker.
# Build with:  pyinstaller --clean "BESS Tracker.spec"

import os
from PyInstaller.utils.hooks import collect_data_files

# Project root (the directory containing this .spec file).
PROJECT_ROOT = os.path.dirname(os.path.abspath(SPEC))

# Bundled non-code resources (read-only at runtime).
# These get extracted at startup into the PyInstaller temp folder; the app's
# code references them via os.path.dirname(__file__)-relative paths.
datas = [
    (os.path.join(PROJECT_ROOT, 'data', 'alarm_classifications.csv'),
     'data'),
]
# monthly_history.json is optional — bundle only if it exists, otherwise the
# app writes it fresh on first run.
_hist = os.path.join(PROJECT_ROOT, 'data', 'monthly_history.json')
if os.path.exists(_hist):
    datas.append((_hist, 'data'))

# certifi CA bundle — required for `requests` HTTPS (cloud sync over https://).
# Without this the frozen exe raises SSL CERTIFICATE_VERIFY_FAILED even though
# a browser on the same machine connects fine.
datas += collect_data_files('certifi')

hidden = [
    # PyQt5 — sometimes missed by the auto-analyser when widgets are imported lazily
    'PyQt5.sip',
    'PyQt5.QtCore',
    'PyQt5.QtGui',
    'PyQt5.QtWidgets',

    # Matplotlib — Agg backend is what generates charts in the report
    'matplotlib.backends.backend_agg',
    'matplotlib.backends.backend_pdf',

    # Pandas / openpyxl glue that PyInstaller occasionally drops
    'pandas',
    'pandas._libs.tslibs.base',
    'openpyxl',
    'openpyxl.cell._writer',

    # ReportLab — explicit subpackages avoid runtime ImportError on Windows
    'reportlab',
    'reportlab.pdfbase',

    # python-docx for Word export
    'docx',
    'docx.oxml.ns',
    'reportlab.pdfbase._fontdata',
    'reportlab.platypus',
    'reportlab.lib',
    'reportlab.graphics',

    # Project modules — list every services/* and ui/* file so they're frozen
    # in even if some code path imports them dynamically.
    'database.db_manager',
    'services.analytics_service',
    'services.availability_service',
    'services.scada_report_service',
    'services.bukhara_report_service',
    'services.docx_renderer',
    'services.tashkent_report_service',
    'services.asset_service',
    'services.asset_tree_service',
    'services.checklist_service',
    'services.edit_serials_service',
    'services.kpi_service',
    'services.lifecycle_service',
    'services.log_service',
    'services.material_service',
    'services.project_service',
    'services.report_service',
    'services.stock_service',
    'services.work_log_service',
    # New: Field Log + Sync
    'services.worklog_entry_service',
    'services.worklog_parts_service',
    'services.worklog_report_service',
    'services.report_workflow_service',
    'ui.projects_page',
    'ui.monthly_reports_page',
    'ui.project_launcher',
    'ui.equipment_page',
    'services.image_service',
    'services.sync_config',
    'services.sync_client',
    'services.sync_worker',
    'ui.analytics_view',
    'ui.asset_page',
    'ui.checklist_page',
    'ui.components',
    'ui.daily_log_form',
    'ui.dashboard_page',
    'ui.edit_project_dialog',
    'ui.edit_serials_dialog',
    'ui.kpi_page',
    'ui.lifecycle_view',
    'ui.main_window',
    'ui.materials_dialog',
    'ui.project_dialog',
    'ui.reports_view',
    'ui.scada_report_page',
    'ui.block_report_page',
    'ui.stock_page',
    'ui.style',
    'ui.work_log_form',
    'ui.work_log_report',
    # New: Field Log UI + Sync dialogs
    'ui.worklog_entry_form',
    'ui.sync_settings_dialog',
    'ui.user_management_dialog',
    # requests + Pillow used by sync/images
    'requests',
    'requests.adapters',
    'certifi',
    'PIL',
    'PIL.Image',
    'PIL.ExifTags',
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='BESS Tracker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
