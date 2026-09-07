"""
ui/main_window.py
------------------
Main window — sidebar + QStackedWidget.
All pages listed clearly. New pages: Checklists, Stock, KPIs, Assets.
"""

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QStackedWidget, QLabel, QPushButton, QSizePolicy,
    QFrame, QStatusBar
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont

from ui.daily_log_form    import DailyLogForm
from ui.reports_view      import ReportsView
from ui.lifecycle_view    import LifecycleView
from ui.analytics_view    import AnalyticsView
from ui.work_log_form     import WorkLogForm
from ui.work_log_report   import WorkLogReport
from ui.dashboard_page    import DashboardPage
from ui.materials_dialog  import MaterialsDialog
from ui.scada_report_page import ScadaReportPage
from ui.block_report_page import BlockReportPage
from ui.projects_page       import ProjectsPage
from ui.monthly_reports_page import MonthlyReportsPage
from ui.checklist_page    import ChecklistPage
from ui.stock_page        import StockPage
from ui.kpi_page          import KpiPage
from ui.asset_page           import AssetPage
from ui.worklog_entry_form   import FieldLogEntryPage, FieldLogRecordsPage
from ui.sync_settings_dialog import SyncSettingsDialog

from ui.project_dialog        import ProjectDialog
from ui.edit_project_dialog   import EditProjectDialog
from ui.edit_serials_dialog   import EditSerialsDialog
from ui.user_management_dialog import UserManagementDialog

from services.project_service import get_all_projects
from services.sync_config import sync_config


# Page index constants — easier to read than magic numbers
PAGE_DASHBOARD  = 0
PAGE_DAILY_LOG  = 1
PAGE_REPORTS    = 2
PAGE_ANALYTICS  = 3
PAGE_WORK_LOG   = 4
PAGE_WORK_RPT   = 5
PAGE_LIFECYCLE  = 6
PAGE_MATERIALS  = 7
PAGE_SCADA      = 8
PAGE_CHECKLIST  = 9
PAGE_STOCK      = 10
PAGE_KPI        = 11
PAGE_ASSETS     = 12
PAGE_BLOCK_RPT  = 13
PAGE_FIELD_LOG  = 14
PAGE_FIELD_RECS = 15
PAGE_PROJECTS   = 16
PAGE_MONTHLY    = 17

PAGE_NAMES = {
    PAGE_DASHBOARD: "Dashboard",
    PAGE_DAILY_LOG: "Daily Log",
    PAGE_REPORTS:   "Reports",
    PAGE_ANALYTICS: "Analytics",
    PAGE_WORK_LOG:  "Work Log",
    PAGE_WORK_RPT:  "Work Report",
    PAGE_LIFECYCLE: "Container Lifecycle",
    PAGE_MATERIALS: "Materials",
    PAGE_SCADA:     "SCADA Report",
    PAGE_CHECKLIST: "Checklists",
    PAGE_STOCK:     "Spare Parts Stock",
    PAGE_KPI:       "KPI Dashboard",
    PAGE_ASSETS:    "Asset Register",
    PAGE_BLOCK_RPT: "Block Performance",
    PAGE_FIELD_LOG: "Field Log",
    PAGE_FIELD_RECS: "Field Log — Records",
    PAGE_PROJECTS:  "Projects",
    PAGE_MONTHLY:   "Monthly Reports",
}


class NavButton(QPushButton):
    def __init__(self, icon, label, parent=None):
        super().__init__(f"  {icon}   {label}", parent)
        self.setObjectName("NavButton")
        self.setCheckable(False)
        self.setFixedHeight(42)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)

    def set_active(self, active: bool):
        self.setProperty("active", "true" if active else "false")
        self.style().unpolish(self)
        self.style().polish(self)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BESS Tracker  —  Field Service Portal")
        self.setMinimumSize(1150, 720)
        self._nav_buttons = []
        self.sync_worker  = None
        self._ensure_project_warehouses()   # backfill warehouses for old projects
        self._build_ui()
        self._navigate(PAGE_DASHBOARD)
        self._update_status()
        self._start_sync_worker()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Sidebar ───────────────────────────────────────────────────────
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(215)
        sl = QVBoxLayout(sidebar)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(0)

        logo = QWidget()
        logo.setObjectName("SidebarLogo")
        ll = QVBoxLayout(logo)
        ll.setContentsMargins(16, 16, 16, 6)
        ll.setSpacing(2)
        lbl = QLabel("⚡ BESS Tracker")
        lbl.setStyleSheet("font-size:15px;font-weight:bold;color:#FFFFFF;background:transparent;")
        ll.addWidget(lbl)
        sub = QLabel("Field Service Portal")
        sub.setStyleSheet("font-size:10px;color:#4A6080;background:transparent;")
        ll.addWidget(sub)
        sl.addWidget(logo)

        self._add_section(sl, "OVERVIEW")
        self._add_nav(sl, "📊", "Dashboard",     PAGE_DASHBOARD)

        self._add_section(sl, "OPERATIONS")
        self._add_nav(sl, "📋", "Daily Log",     PAGE_DAILY_LOG)
        self._add_nav(sl, "🔧", "Work Log",      PAGE_WORK_LOG)
        self._add_nav(sl, "📸", "Field Log",     PAGE_FIELD_LOG)
        self._add_nav(sl, "🗂", "Field Log — Records", PAGE_FIELD_RECS)
        self._add_nav(sl, "✅", "Checklists",    PAGE_CHECKLIST)

        self._add_section(sl, "ASSETS & STOCK")
        self._add_nav(sl, "🏷", "Asset Register", PAGE_ASSETS)
        self._add_nav(sl, "📦", "Spare Parts",    PAGE_STOCK)
        self._add_nav(sl, "🧾", "Materials",      PAGE_MATERIALS)

        self._add_section(sl, "REPORTS & KPI")
        self._add_nav(sl, "📈", "Reports",        PAGE_REPORTS)
        self._add_nav(sl, "📋", "Work Report",    PAGE_WORK_RPT)
        self._add_nav(sl, "🔍", "Lifecycle",      PAGE_LIFECYCLE)
        self._add_nav(sl, "📉", "Analytics",      PAGE_ANALYTICS)
        self._add_nav(sl, "🎯", "KPI Dashboard",  PAGE_KPI)
        self._add_nav(sl, "📄", "SCADA Report",   PAGE_SCADA)
        self._add_nav(sl, "🔋", "Block Performance", PAGE_BLOCK_RPT)
        self._add_nav(sl, "🗂", "Projects",         PAGE_PROJECTS)
        self._add_nav(sl, "📅", "Monthly Reports",  PAGE_MONTHLY)

        sl.addStretch()

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color:#243D5C;margin:0 12px;")
        sl.addWidget(sep)

        for icon, label, slot in [
            ("➕", "New Project",  self._new_project),
            ("🔧", "Edit Project", self._edit_project),
            ("✏️", "Edit Serials", self._edit_serials),
            ("🔄", "Sync Settings", self._open_sync_settings),
            ("👥", "Users",         self._open_user_mgmt),
        ]:
            btn = QPushButton(f"  {icon}   {label}")
            btn.setObjectName("NavButton")
            btn.setFixedHeight(38)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(slot)
            sl.addWidget(btn)

        self.proj_count_lbl = QLabel("")
        self.proj_count_lbl.setAlignment(Qt.AlignCenter)
        self.proj_count_lbl.setStyleSheet(
            "color:#4A6080;font-size:10px;padding:8px;background:transparent;"
        )
        sl.addWidget(self.proj_count_lbl)

        # Sync status label (bottom of sidebar)
        self.sync_lbl = QLabel("  🔄 Sync: off")
        self.sync_lbl.setStyleSheet(
            "color:#4A6080;font-size:10px;padding:4px 12px 8px;background:transparent;"
        )
        sl.addWidget(self.sync_lbl)

        root.addWidget(sidebar)

        # ── Page stack ────────────────────────────────────────────────────
        self.stack = QStackedWidget()
        self.stack.setObjectName("PageArea")

        self.dashboard_page   = DashboardPage()
        self.log_form         = DailyLogForm()
        self.reports_view     = ReportsView()
        self.analytics_view   = AnalyticsView()
        self.work_log_form    = WorkLogForm()
        self.work_log_report  = WorkLogReport()
        self.lifecycle_view   = LifecycleView()
        self.materials_page   = MaterialsDialog()
        self.materials_page.setWindowFlags(Qt.Widget)
        self.scada_page       = ScadaReportPage()
        self.block_report_page = BlockReportPage()
        self.checklist_page   = ChecklistPage()
        self.stock_page       = StockPage()
        self.kpi_page         = KpiPage()
        self.asset_page       = AssetPage()
        self.field_log_page       = FieldLogEntryPage()      # entry form
        self.field_log_records    = FieldLogRecordsPage()    # timeline + report
        self.projects_page        = ProjectsPage()           # 16
        self.monthly_page         = MonthlyReportsPage()     # 17
        # New entries → refresh the records timeline so it's current when opened
        self.field_log_page.entry_saved.connect(
            lambda: self.field_log_records._load_timeline())

        for page in [
            self.dashboard_page,   # 0
            self.log_form,         # 1
            self.reports_view,     # 2
            self.analytics_view,   # 3
            self.work_log_form,    # 4
            self.work_log_report,  # 5
            self.lifecycle_view,   # 6
            self.materials_page,   # 7
            self.scada_page,       # 8
            self.checklist_page,   # 9
            self.stock_page,       # 10
            self.kpi_page,         # 11
            self.asset_page,       # 12
            self.block_report_page,# 13
            self.field_log_page,   # 14  (Field Log — entry)
            self.field_log_records,# 15  (Field Log — records)
            self.projects_page,    # 16  (Projects)
            self.monthly_page,     # 17  (Monthly Reports)
        ]:
            self.stack.addWidget(page)

        root.addWidget(self.stack)

        self.status_bar = QStatusBar()
        self.status_bar.setStyleSheet(
            "QStatusBar{background:#FFFFFF;color:#6B7A8D;"
            "border-top:1px solid #E0E4EA;font-size:11px;}"
        )
        self.setStatusBar(self.status_bar)

    def _add_section(self, layout, text):
        lbl = QLabel(text)
        lbl.setStyleSheet(
            "color:#4A6080;font-size:10px;font-weight:bold;"
            "padding:12px 16px 4px 20px;background:#1A2B45;letter-spacing:1px;"
        )
        layout.addWidget(lbl)

    def _add_nav(self, layout, icon, label, page_idx):
        btn = NavButton(icon, label)
        btn.clicked.connect(lambda _, idx=page_idx: self._navigate(idx))
        layout.addWidget(btn)
        self._nav_buttons.append((btn, page_idx))

    def _navigate(self, page_idx: int):
        self.stack.setCurrentIndex(page_idx)
        for btn, idx in self._nav_buttons:
            btn.set_active(idx == page_idx)
        if page_idx == PAGE_DASHBOARD:
            self.dashboard_page.refresh()
        elif page_idx == PAGE_FIELD_RECS:
            self.field_log_records._load_timeline()
        self.status_bar.showMessage(
            f"  {PAGE_NAMES.get(page_idx,'')}  |  "
            f"{len(get_all_projects())} project(s)"
        )

    def _new_project(self):
        dlg = ProjectDialog(self)
        if dlg.exec_() == ProjectDialog.Accepted:
            self._ensure_project_warehouses()
            self._refresh_all()

    def _edit_project(self):
        dlg = EditProjectDialog(self)
        if dlg.exec_() == EditProjectDialog.Accepted:
            self._refresh_all()

    def _edit_serials(self):
        dlg = EditSerialsDialog(self)
        dlg.exec_()
        self.lifecycle_view._refresh_serial_autocomplete()

    def _ensure_project_warehouses(self):
        """Auto-creates a warehouse for every project that doesn't have one."""
        from services.stock_service import ensure_project_warehouse
        for p in get_all_projects():
            ensure_project_warehouse(p.id, p.name)

    def _refresh_all(self):
        self.log_form.refresh_projects()
        self.reports_view.refresh_projects()
        self.lifecycle_view.refresh_projects()
        self.analytics_view.refresh_projects()
        self.work_log_form.refresh_projects()
        self.work_log_report.refresh_projects()
        self.field_log_page.refresh_projects()
        self.field_log_records.refresh_projects()
        self.checklist_page.refresh_projects()
        self.stock_page.refresh_projects()
        self.kpi_page.refresh_projects()
        self.asset_page.refresh_projects()
        self.dashboard_page.refresh()
        self._update_status()

    def _update_status(self):
        count = len(get_all_projects())
        self.proj_count_lbl.setText(
            f"{count} project{'s' if count != 1 else ''}"
        )
        self.status_bar.showMessage(
            f"  Ready  |  {count} project(s) in database"
        )

    # ── Sync ──────────────────────────────────────────────────────────────────

    def _start_sync_worker(self):
        """Start the background sync thread if sync is configured and enabled."""
        if not sync_config.is_configured() or not sync_config.enabled:
            return
        from services.sync_worker import SyncWorker
        self.sync_worker = SyncWorker(self)
        self.sync_worker.status_changed.connect(self._on_sync_status_changed)
        self.sync_worker.sync_done.connect(self._on_sync_done)
        self.sync_worker.sync_error.connect(
            lambda msg: self._on_sync_status_changed(f"❌ {msg[:60]}")
        )
        self.sync_worker.start()
        self.sync_lbl.setText(f"  ✅ Sync: {sync_config.username}@server")

    def _on_sync_status_changed(self, msg: str):
        self.sync_lbl.setText(f"  {msg}")
        self.status_bar.showMessage(f"  {msg}")

    def _on_sync_done(self, result: dict):
        # Refresh the Field Log records timeline if it's currently visible
        if self.stack.currentIndex() == PAGE_FIELD_RECS:
            self.field_log_records._load_timeline()

    def _open_sync_settings(self):
        dlg = SyncSettingsDialog(self)
        dlg.exec_()
        # (Re)start / stop worker based on new config state
        if sync_config.is_configured() and sync_config.enabled:
            if self.sync_worker is None or not self.sync_worker.isRunning():
                self._start_sync_worker()
            else:
                # Already running — trigger immediate sync to reflect new login
                self.sync_worker.trigger_now()
            self.sync_lbl.setText(f"  ✅ Sync: {sync_config.username}@server")
        else:
            if self.sync_worker and self.sync_worker.isRunning():
                self.sync_worker.stop()
                self.sync_worker = None
            self.sync_lbl.setText("  🔄 Sync: off")

    def _open_user_mgmt(self):
        dlg = UserManagementDialog(self)
        dlg.exec_()

    def closeEvent(self, e):
        if self.sync_worker and self.sync_worker.isRunning():
            self.sync_worker.stop()
        super().closeEvent(e)
