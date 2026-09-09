"""
ui/main_window.py
------------------
Main window — sidebar + QStackedWidget.
All pages listed clearly. New pages: Checklists, Stock, KPIs, Assets.
"""

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QStackedWidget, QLabel, QPushButton, QSizePolicy,
    QFrame, QStatusBar, QScrollArea
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
from ui.equipment_page       import EquipmentPage
from ui.worklog_entry_form   import FieldLogEntryPage, FieldLogRecordsPage
from ui.project_launcher      import ProjectLauncher
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
PAGE_LAUNCHER   = 18
PAGE_EQUIPMENT  = 19

PAGE_NAMES = {
    PAGE_DASHBOARD: "Dashboard",
    PAGE_DAILY_LOG: "Daily Log",
    PAGE_REPORTS:   "Reports",
    PAGE_ANALYTICS: "Analytics",
    PAGE_WORK_LOG:  "Work Report",
    PAGE_WORK_RPT:  "Work Log Report",
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
    PAGE_PROJECTS:  "Project Setup",
    PAGE_MONTHLY:   "Monthly Reports",
    PAGE_LAUNCHER:  "Select Project",
    PAGE_EQUIPMENT: "Equipment",
}

# Pages that can be scoped to the shell's "current project". Each such page
# exposes set_current_project(project_id); the shell calls it when a project
# is opened. Pages without the method are simply skipped (still self-scoped).
PROJECT_SCOPED_PAGES = ("projects_page", "monthly_page", "equipment_page")


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
        self.current_project_id   = None
        self.current_project_name = None
        self._ensure_project_warehouses()   # backfill warehouses for old projects
        self._build_ui()
        self._show_launcher()                # front door: pick a project first
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

        # ── Current-project header (hidden until a project is opened) ──────
        self.proj_header = QWidget()
        self.proj_header.setObjectName("ProjHeader")
        self.proj_header.setStyleSheet(
            "#ProjHeader{background:#142038;border-bottom:1px solid #0F1E30;}")
        phl = QVBoxLayout(self.proj_header)
        phl.setContentsMargins(16, 12, 12, 12)
        phl.setSpacing(4)
        cap = QLabel("CURRENT PROJECT")
        cap.setStyleSheet("color:#4A6080;font-size:9px;font-weight:bold;"
                          "letter-spacing:1px;background:transparent;")
        phl.addWidget(cap)
        self.current_proj_lbl = QLabel("—")
        self.current_proj_lbl.setWordWrap(True)
        self.current_proj_lbl.setStyleSheet(
            "color:#FFFFFF;font-size:13px;font-weight:bold;background:transparent;")
        phl.addWidget(self.current_proj_lbl)
        switch_btn = QPushButton("⇄  Switch project")
        switch_btn.setObjectName("SwitchBtn")
        switch_btn.setCursor(Qt.PointingHandCursor)
        switch_btn.setStyleSheet(
            "#SwitchBtn{background:#1E3A5F;color:#8FA3BE;border:none;border-radius:4px;"
            "padding:5px 8px;font-size:11px;text-align:left;}"
            "#SwitchBtn:hover{background:#243D5C;color:#FFFFFF;}")
        switch_btn.clicked.connect(self._switch_project)
        phl.addWidget(switch_btn)
        sl.addWidget(self.proj_header)

        # ── Module navigation, grouped by intent (hidden until project) ────
        self.module_nav = QWidget()
        self.module_nav.setStyleSheet("background:#1A2B45;")
        mn = QVBoxLayout(self.module_nav)
        mn.setContentsMargins(0, 0, 0, 0)
        mn.setSpacing(0)

        self._add_section(mn, "OPERATE")
        self._add_nav(mn, "📊", "Overview",       PAGE_DASHBOARD)
        self._add_nav(mn, "📋", "Daily Log",      PAGE_DAILY_LOG)
        self._add_nav(mn, "🔧", "Work Reports",   PAGE_WORK_LOG)
        self._add_nav(mn, "📸", "Field Log",      PAGE_FIELD_LOG)
        self._add_nav(mn, "🗂", "Field Records",  PAGE_FIELD_RECS)

        self._add_section(mn, "MAINTAIN")
        self._add_nav(mn, "🧩", "Equipment",       PAGE_EQUIPMENT)
        self._add_nav(mn, "✅", "Checklists / PM", PAGE_CHECKLIST)
        self._add_nav(mn, "🏷", "Asset Register",  PAGE_ASSETS)
        self._add_nav(mn, "📦", "Spare Parts",     PAGE_STOCK)
        self._add_nav(mn, "🧾", "Materials",       PAGE_MATERIALS)

        self._add_section(mn, "REPORT")
        self._add_nav(mn, "📅", "Monthly Reports",   PAGE_MONTHLY)
        self._add_nav(mn, "🔋", "Block Performance", PAGE_BLOCK_RPT)

        self._add_section(mn, "SETUP")
        self._add_nav(mn, "⚙", "Project Setup",     PAGE_PROJECTS)

        # Legacy analytics/report pages — still reachable, tucked away and
        # collapsed by default so the primary nav stays lean.
        self._add_collapsible(mn, "MORE TOOLS", [
            ("🔍", "Lifecycle",     PAGE_LIFECYCLE),
            ("📄", "SCADA Report",  PAGE_SCADA),
            ("🎯", "KPI Dashboard", PAGE_KPI),
            ("📉", "Analytics",     PAGE_ANALYTICS),
            ("📈", "Reports",       PAGE_REPORTS),
            ("📋", "Work Log Report", PAGE_WORK_RPT),
        ])

        # Scrollable so the grouped nav never gets clipped on short windows.
        self.nav_scroll = QScrollArea()
        self.nav_scroll.setWidgetResizable(True)
        self.nav_scroll.setFrameShape(QFrame.NoFrame)
        self.nav_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.nav_scroll.setStyleSheet(
            "QScrollArea{border:none;background:#1A2B45;}"
            "QScrollBar:vertical{background:#1A2B45;width:8px;margin:0;}"
            "QScrollBar::handle:vertical{background:#3A4E6E;border-radius:4px;min-height:28px;}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
        )
        self.nav_scroll.setWidget(self.module_nav)
        sl.addWidget(self.nav_scroll, 1)

        self.proj_header.setVisible(False)
        self.module_nav.setVisible(False)

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
        self.launcher             = ProjectLauncher()        # 18
        self.equipment_page       = EquipmentPage()          # 19
        # One tap from an alarm to a pre-filled Work Report, and straight back
        # to the list afterwards so the next one is one tap away too.
        self.equipment_page.work_report_requested.connect(self._report_from_alarm)
        self.work_log_form.alarm_report_saved.connect(self._back_to_equipment)
        self.launcher.project_selected.connect(self._open_project)
        self.launcher.new_project_requested.connect(self._new_project)
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
            self.launcher,         # 18  (Project launcher — front door)
            self.equipment_page,   # 19  (Equipment — asset tree + history)
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
            "color:#8296B3;font-size:10px;font-weight:bold;"
            "padding:14px 16px 6px 20px;background:transparent;letter-spacing:1.5px;"
        )
        layout.addWidget(lbl)

    def _add_nav(self, layout, icon, label, page_idx):
        btn = NavButton(icon, label)
        btn.clicked.connect(lambda _, idx=page_idx: self._navigate(idx))
        layout.addWidget(btn)
        self._nav_buttons.append((btn, page_idx))

    def _add_collapsible(self, layout, title, items):
        """A section header that toggles a group of nav buttons (collapsed by
        default). Keeps legacy pages reachable without cluttering the nav."""
        header = QPushButton(f"{title}    ▸")
        header.setObjectName("NavToggle")
        header.setCursor(Qt.PointingHandCursor)
        header.setStyleSheet(
            "#NavToggle{color:#8296B3;font-size:10px;font-weight:bold;letter-spacing:1.5px;"
            "background:transparent;border:none;text-align:left;padding:14px 16px 6px 20px;}"
            "#NavToggle:hover{color:#FFFFFF;}"
        )
        body = QWidget()
        body.setStyleSheet("background:#1A2B45;")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)
        for icon, label, idx in items:
            self._add_nav(bl, icon, label, idx)
        body.setVisible(False)

        def _toggle():
            vis = not body.isVisible()
            body.setVisible(vis)
            header.setText(f"{title}    {'▾' if vis else '▸'}")

        header.clicked.connect(_toggle)
        layout.addWidget(header)
        layout.addWidget(body)

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

    # ── Project shell (launcher ↔ workspace) ────────────────────────────────
    def _show_launcher(self):
        """Return to the front door: no project selected, module nav hidden."""
        self.current_project_id = None
        self.current_project_name = None
        self.proj_header.setVisible(False)
        self.module_nav.setVisible(False)
        for btn, _idx in self._nav_buttons:
            btn.set_active(False)
        self.launcher.reload()
        self.stack.setCurrentIndex(PAGE_LAUNCHER)
        self.status_bar.showMessage(
            f"  Select a project  |  {len(get_all_projects())} project(s)")

    def _open_project(self, pid: int, name: str):
        """Enter a project's workspace and scope the shell to it."""
        self.current_project_id = pid
        self.current_project_name = name
        self.current_proj_lbl.setText(name or "—")
        self.proj_header.setVisible(True)
        self.module_nav.setVisible(True)
        self._push_current_project()
        self._navigate(PAGE_DASHBOARD)   # open on Overview

    def _switch_project(self):
        self._show_launcher()

    def _report_from_alarm(self, event: dict):
        """Equipment page → Work Report, pre-filled from the SCADA alarm."""
        self._navigate(PAGE_WORK_LOG)
        try:
            self.work_log_form.prefill_from_alarm(event)
        except Exception as e:                       # noqa: BLE001
            self.status_bar.showMessage(f"  Could not prefill: {e}", 6000)

    def _back_to_equipment(self, work_log_id: int):
        """Report written — return to the list it was raised from."""
        self._navigate(PAGE_EQUIPMENT)
        try:
            self.equipment_page.refresh_after_report()
        except Exception:                            # noqa: BLE001
            pass
        self.status_bar.showMessage(
            f"  Work report #{work_log_id} saved — next one?", 6000)

    def _push_current_project(self):
        """Tell project-scoped pages which project is now active."""
        pid = self.current_project_id
        for attr in PROJECT_SCOPED_PAGES:
            page = getattr(self, attr, None)
            if page is not None and hasattr(page, "set_current_project"):
                try:
                    page.set_current_project(pid)
                except Exception:
                    pass

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
        self.launcher.reload()
        self._push_current_project()
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
