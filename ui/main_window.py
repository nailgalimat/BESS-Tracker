"""
ui/main_window.py
------------------
Main window — sidebar + QStackedWidget.

The menu holds **nine** items (NAV, below), grouped the way the day runs:
Today · Work · Plan · Equipment · Availability · Monthly report · Analysis ·
Spare parts · Project. Every other page still exists, still works and still
holds its data — it simply left the menu and is reached from
Project → "Archive of old pages" (ARCHIVE, below).

Page *indices* are unchanged: the stack keeps its original insertion order so
`PAGE_* == stack index` still holds for everything that existed before, and
new pages are appended after it. Changing the menu therefore means editing
NAV / ARCHIVE — not renumbering pages.

The header above the page carries the application context: the open project,
one shared month (Availability, Monthly report and Analysis follow it) and the
sync state.
"""

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QStackedWidget, QLabel, QPushButton, QSizePolicy, QComboBox, QSpinBox,
    QFrame, QStatusBar, QScrollArea
)
from PyQt5.QtCore import Qt, QDate
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
from ui.planner_page         import PlannerPage
from ui.worklog_entry_form   import FieldLogEntryPage, FieldLogRecordsPage
from ui.project_launcher      import ProjectLauncher
from ui.project_hub_page      import ProjectHubPage
from ui.today_page            import TodayPage
from ui.work_page             import WorkPage
from ui.availability_page     import AvailabilityPage
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
PAGE_PLANNER    = 20
# ── pages of the redesign, appended so the indices above keep their meaning ──
PAGE_PROJECT    = 21     # Project hub: settings, people, sync, archive
PAGE_TODAY      = 22     # Today: the seven blocks the day starts with
PAGE_WORK       = 23     # Work: one journal of every work record
PAGE_AVAIL      = 24     # Availability: the month's inputs

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
    PAGE_PLANNER:   "Planner",
    PAGE_PROJECT:   "Project",
    PAGE_TODAY:     "Today",
    PAGE_WORK:      "Work",
    PAGE_AVAIL:     "Availability",
}

# ── The menu ────────────────────────────────────────────────────────────────
# (group or None, label, page index). Nine items; the group label is drawn
# once above the first item that carries it.
NAV = [
    (None,     "Today",          PAGE_TODAY),
    ("WORK",   "Work",           PAGE_WORK),
    ("WORK",   "Plan",           PAGE_PLANNER),
    ("PLANT",  "Equipment",      PAGE_EQUIPMENT),
    ("PLANT",  "Availability",   PAGE_AVAIL),
    ("REPORT", "Monthly report", PAGE_MONTHLY),
    ("REPORT", "Analysis",       PAGE_ANALYTICS),
    ("STOCK",  "Spare parts",    PAGE_STOCK),
    ("SETUP",  "Project",        PAGE_PROJECT),
]

# Pages that follow the header's month.
MONTH_PAGES = ("Availability", "Monthly report", "Analysis")

# ── The archive ─────────────────────────────────────────────────────────────
# Left the menu in the redesign; nothing deleted, all still reachable from
# Project → "Archive of old pages". (label, page index, what it was)
ARCHIVE = [
    ("Overview",         PAGE_DASHBOARD,  "The old dashboard — replaced by Today"),
    ("Daily Log",        PAGE_DAILY_LOG,  "Daily site log — now written as a work record"),
    ("Work Reports",     PAGE_WORK_LOG,   "Desktop fault report — now the Work journal"),
    ("Field Log",        PAGE_FIELD_LOG,  "Desktop copy of the phone entry form"),
    ("Field Records",    PAGE_FIELD_RECS, "Phone records timeline — now inside Work"),
    ("Checklists / PM",  PAGE_CHECKLIST,  "Commissioning checklists — moving into Plan"),
    ("Asset Register",   PAGE_ASSETS,     "Serial numbers — now Equipment → Identity"),
    ("Materials",        PAGE_MATERIALS,  "Material catalogue — belongs to Spare parts"),
    ("Block Performance", PAGE_BLOCK_RPT, "Bukhara / Tashkent generator — now Monthly report"),
    ("KPI Dashboard",    PAGE_KPI,        "KPI tiles — moving into Analysis"),
    ("Lifecycle",        PAGE_LIFECYCLE,  "Container lifecycle — moving into Analysis"),
    ("SCADA Report",     PAGE_SCADA,      "Older SCADA report flow"),
    ("Reports",          PAGE_REPORTS,    "Daily-log reports"),
    ("Work Log Report",  PAGE_WORK_RPT,   "Work-report export"),
    ("Project Setup",    PAGE_PROJECTS,   "Report settings of the project"),
]

# Pages that can be scoped to the shell's "current project". A page either
# exposes set_current_project(project_id) or simply owns a project combo box
# (`proj_combo` / `project_combo`) — the shell selects the open project in it,
# so no page asks "— Select —" again while a project is open.
PROJECT_SCOPED_PAGES = ("projects_page", "monthly_page", "equipment_page",
                        "planner_page",
                        # both read and write exclusions / downtime, which must
                        # carry the project or they leak into another plant
                        "block_report_page", "scada_page",
                        # entry pages that used to start at "— Select —"
                        "log_form", "work_log_form", "work_log_report",
                        "field_log_page", "field_log_records", "checklist_page",
                        "stock_page", "kpi_page", "asset_page", "materials_page",
                        "analytics_view", "lifecycle_view", "reports_view",
                        "project_hub", "today_page", "work_page",
                        "availability_page")


class NavButton(QPushButton):
    def __init__(self, label, parent=None):
        super().__init__(f"   {label}", parent)
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
        # painted explicitly: the global QWidget rule would otherwise leave the
        # brand white on white, which is exactly how it looked before
        logo.setStyleSheet("#SidebarLogo{background-color:#142038;}")
        ll = QVBoxLayout(logo)
        ll.setContentsMargins(16, 16, 16, 10)
        ll.setSpacing(2)
        lbl = QLabel("BESS Tracker")
        lbl.setStyleSheet("font-size:15px;font-weight:bold;color:#FFFFFF;"
                          "background-color:transparent;")
        ll.addWidget(lbl)
        sub = QLabel("O&M workspace")
        sub.setStyleSheet("font-size:10px;color:#8FA3BE;background-color:transparent;")
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

        # Nine items, grouped. Everything else lives in the Project archive.
        self._nav_by_label = {}
        last_group = "—"
        for group, label, idx in NAV:
            if group != last_group:
                if group:
                    self._add_section(mn, group)
                last_group = group
            self._add_nav(mn, label, idx)
        # items keep their height; the spare space goes below them, not between
        mn.addStretch(1)

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

        # New / Edit project, Serials, Sync and Users used to sit here as five
        # buttons on every screen (~230 px). They are now in Project.
        self.proj_count_lbl = QLabel("")
        self.proj_count_lbl.setAlignment(Qt.AlignCenter)
        self.proj_count_lbl.setStyleSheet(
            "color:#4A6080;font-size:10px;padding:8px;background:transparent;"
        )
        sl.addWidget(self.proj_count_lbl)

        # Sync status (bottom of sidebar). It is the only place an error is
        # ever shown, so it opens the settings that explain it instead of
        # leaving the user to hunt for them under Project.
        self.sync_lbl = QPushButton("  🔄 Sync: off")
        self.sync_lbl.setCursor(Qt.PointingHandCursor)
        self.sync_lbl.setToolTip("Server, login and what is waiting from the phones")
        self.sync_lbl.setStyleSheet(
            "QPushButton{color:#4A6080;font-size:10px;padding:4px 12px 8px;"
            "background:transparent;border:none;text-align:left;}"
            "QPushButton:hover{color:#FFFFFF;}"
        )
        self.sync_lbl.clicked.connect(self._open_sync_settings)
        sl.addWidget(self.sync_lbl)

        root.addWidget(sidebar)

        # ── Main area: context header + page stack ────────────────────────
        main = QWidget()
        main.setObjectName("PageArea")
        ml = QVBoxLayout(main)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(0)
        ml.addWidget(self._build_topbar())

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
        self.planner_page         = PlannerPage()            # 20
        self.project_hub          = ProjectHubPage(          # 21
            archive_items=ARCHIVE, settings_page=PAGE_PROJECTS)
        self.project_hub.page_requested.connect(self._navigate)
        self.project_hub.action_requested.connect(self._hub_action)
        self.today_page           = TodayPage()              # 22
        self.today_page.open_page.connect(self._go)
        self.today_page.open_filtered.connect(self._go_filtered)
        self.work_page            = WorkPage()               # 23
        self.work_page.record_saved.connect(lambda _k: self.today_page.refresh())
        # The availability inputs leave the Monthly report and become a page:
        # the editor itself moves, so both screens cannot drift apart.
        self.availability_page    = AvailabilityPage(self.monthly_page)   # 24
        self.availability_page.adopt_editor(self.monthly_page.take_inputs_tab())
        self.availability_page.open_report.connect(lambda: self._go("Monthly report"))
        self.monthly_page.open_availability.connect(lambda: self._go("Availability"))
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
            self.planner_page,     # 20  (Planner — PM campaigns + daily work)
            self.project_hub,      # 21  (Project — settings, people, archive)
            self.today_page,       # 22  (Today — the seven blocks)
            self.work_page,        # 23  (Work — the journal and the record card)
            self.availability_page,# 24  (Availability — the month's inputs)
        ]:
            self.stack.addWidget(page)

        ml.addWidget(self.stack, 1)
        root.addWidget(main, 1)

        self.status_bar = QStatusBar()
        self.status_bar.setStyleSheet(
            "QStatusBar{background:#FFFFFF;color:#6B7A8D;"
            "border-top:1px solid #E0E4EA;font-size:11px;}"
        )
        self.setStatusBar(self.status_bar)

    # ── Context header ───────────────────────────────────────────────────
    def _build_topbar(self):
        """Project · page · shared month · sync — the application's context,
        not a form field of the page below it."""
        bar = QWidget()
        bar.setObjectName("TopBar")
        bar.setFixedHeight(48)
        bar.setStyleSheet("#TopBar{background-color:#FFFFFF;"
                          "border-bottom:1px solid #E0E4EA;}")
        l = QHBoxLayout(bar)
        l.setContentsMargins(20, 0, 16, 0)
        l.setSpacing(10)

        self.page_title_lbl = QLabel("")
        self.page_title_lbl.setStyleSheet(
            "font-size:16px;font-weight:bold;color:#1A2B45;background:transparent;")
        l.addWidget(self.page_title_lbl)

        self.top_project_lbl = QLabel("")
        self.top_project_lbl.setStyleSheet(
            "color:#6B7A8D;font-size:12px;background:transparent;")
        l.addWidget(self.top_project_lbl)

        l.addSpacing(8)
        self.month_box = QWidget()
        self.month_box.setStyleSheet("background:transparent;")
        mb = QHBoxLayout(self.month_box)
        mb.setContentsMargins(0, 0, 0, 0)
        mb.setSpacing(6)
        cap = QLabel("Month:")
        cap.setStyleSheet("color:#6B7A8D;background:transparent;")
        mb.addWidget(cap)
        self.month_combo = QComboBox()
        self.month_combo.addItems(["January", "February", "March", "April", "May",
                                   "June", "July", "August", "September",
                                   "October", "November", "December"])
        self.month_combo.setMinimumWidth(110)
        mb.addWidget(self.month_combo)
        self.year_spin = QSpinBox()
        self.year_spin.setRange(2020, 2100)
        mb.addWidget(self.year_spin)
        # default: the month people are actually reporting on
        today = QDate.currentDate()
        y, m = today.year(), today.month()
        if today.day() <= 10:
            y, m = (y - 1, 12) if m == 1 else (y, m - 1)
        self.current_year, self.current_month = y, m
        self.year_spin.setValue(y)
        self.month_combo.setCurrentIndex(m - 1)
        self.month_combo.currentIndexChanged.connect(self._on_month_changed)
        self.year_spin.valueChanged.connect(self._on_month_changed)
        l.addWidget(self.month_box)
        self.month_box.setVisible(False)

        l.addStretch()
        self.top_sync_lbl = QLabel("")
        self.top_sync_lbl.setStyleSheet(
            "color:#6B7A8D;font-size:11px;background:transparent;")
        l.addWidget(self.top_sync_lbl)
        return bar

    def _on_month_changed(self, *_):
        self.current_year = self.year_spin.value()
        self.current_month = self.month_combo.currentIndex() + 1
        self._push_month()

    def _push_month(self):
        """Availability, Monthly report and Analysis share one month."""
        for attr in ("today_page", "work_page", "monthly_page",
                     "availability_page", "analytics_view", "kpi_page",
                     "block_report_page"):
            page = getattr(self, attr, None)
            if page is not None and hasattr(page, "set_month"):
                try:
                    page.set_month(self.current_year, self.current_month)
                except Exception:                        # noqa: BLE001
                    pass

    def _add_section(self, layout, text):
        lbl = QLabel(text)
        lbl.setStyleSheet(
            "color:#8296B3;font-size:10px;font-weight:bold;"
            "padding:14px 16px 6px 20px;background:transparent;letter-spacing:1.5px;"
        )
        layout.addWidget(lbl)

    def _add_nav(self, layout, label, page_idx):
        btn = NavButton(label)
        btn.clicked.connect(lambda _, lb=label: self._go(lb))
        layout.addWidget(btn)
        self._nav_buttons.append((btn, page_idx, label))
        self._nav_by_label[label] = (btn, page_idx)

    def _add_collapsible(self, layout, title, items):   # kept: unused by NAV
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

    def _go(self, label: str):
        """Navigate by menu item. Two items may share a page while a layer of
        the redesign is still landing — the label decides what is highlighted."""
        entry = self._nav_by_label.get(label)
        if entry is None:
            return
        self._navigate(entry[1], nav_label=label)

    def _navigate(self, page_idx: int, nav_label: str = None):
        self.stack.setCurrentIndex(page_idx)
        if nav_label is None:                    # archive / programmatic jump
            for lb, (_b, idx) in self._nav_by_label.items():
                if idx == page_idx:
                    nav_label = lb
                    break
        for btn, _idx, lb in self._nav_buttons:
            btn.set_active(lb == nav_label)
        title = nav_label or PAGE_NAMES.get(page_idx, "")
        self.page_title_lbl.setText(title)
        in_archive = nav_label is None
        self.top_project_lbl.setText(
            ("· " + (self.current_project_name or "")
             + ("  ·  Archive of old pages" if in_archive else ""))
            if self.current_project_name else "")
        self.month_box.setVisible(title in MONTH_PAGES)
        if page_idx == PAGE_TODAY:
            self.today_page.refresh()
        elif page_idx == PAGE_WORK:
            self.work_page.refresh()
        elif page_idx == PAGE_AVAIL:
            self.availability_page.refresh()
        elif page_idx == PAGE_DASHBOARD:
            self.dashboard_page.refresh()
        elif page_idx == PAGE_FIELD_RECS:
            self.field_log_records._load_timeline()
        # pages rebuild their project list when shown — keep the open project
        # selected rather than letting them fall back to "— Select —"
        try:
            self._select_project_in_combo(self.stack.currentWidget(),
                                          self.current_project_id)
        except Exception:                                # noqa: BLE001
            pass
        self.status_bar.showMessage(
            f"  {title or PAGE_NAMES.get(page_idx,'')}  |  "
            f"{len(get_all_projects())} project(s)"
        )

    def _go_filtered(self, label: str, flt: dict):
        """A count on Today opens the list that holds exactly those rows."""
        self._go(label)
        page = self.stack.currentWidget()
        if hasattr(page, "apply_filter"):
            try:
                page.apply_filter(dict(flt or {}))
            except Exception:                            # noqa: BLE001
                pass

    def _hub_action(self, action: str):
        """Buttons of the Project page that open a dialog."""
        {"new_project": self._new_project,
         "edit_project": self._edit_project,
         "edit_serials": self._edit_serials,
         "sync": self._open_sync_settings,
         "users": self._open_user_mgmt}.get(action, lambda: None)()

    # ── Project shell (launcher ↔ workspace) ────────────────────────────────
    def _show_launcher(self):
        """Return to the front door: no project selected, module nav hidden."""
        self.current_project_id = None
        self.current_project_name = None
        self.proj_header.setVisible(False)
        self.module_nav.setVisible(False)
        for btn, _idx, _lb in self._nav_buttons:
            btn.set_active(False)
        self.page_title_lbl.setText("Select a project")
        self.top_project_lbl.setText("")
        self.month_box.setVisible(False)
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
        self._push_month()
        self._go("Today")                # the day starts here

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
        """Tell project-scoped pages which project is now active.

        A page either implements set_current_project(), or just owns a project
        combo box — in which case the shell selects the open project in it.
        That is what stops Work Reports, Field Log, Field Records, Run
        Checklist and the rest from asking "— Select —" with a project open,
        and what keeps Block Performance off Bukhara on a Tashkent site."""
        pid = self.current_project_id
        name = self.current_project_name
        for attr in PROJECT_SCOPED_PAGES:
            page = getattr(self, attr, None)
            if page is None:
                continue
            try:
                if hasattr(page, "set_current_project"):
                    try:
                        page.set_current_project(pid, name)
                    except TypeError:
                        page.set_current_project(pid)
                self._select_project_in_combo(page, pid)
            except Exception:                            # noqa: BLE001
                pass

    @staticmethod
    def _select_project_in_combo(page, pid):
        """Select `pid` in whichever project combo the page owns."""
        if pid is None:
            return
        for attr in ("proj_combo", "project_combo"):
            combo = getattr(page, attr, None)
            if isinstance(combo, QComboBox):
                i = combo.findData(pid)
                if i >= 0 and combo.currentIndex() != i:
                    combo.setCurrentIndex(i)

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
        self.top_sync_lbl.setText(msg)
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
