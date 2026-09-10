"""
ui/work_log_form.py
--------------------
Work Log module — two panels in one screen:

LEFT:  Add new work log entry form
RIGHT: Filterable table of existing work logs

Features:
- Smart cascading dropdowns (Project → Zone → Block → Container)
- Auto-fills Serial Number and Container Type
- Status colour coding in the table
- Edit and Delete selected row
- Right-click context menu on table
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QSplitter,
    QLabel, QComboBox, QDateEdit, QLineEdit, QTextEdit,
    QPushButton, QMessageBox, QGroupBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView,
    QMenu, QAction, QDialog, QDialogButtonBox,
    QCheckBox, QDoubleSpinBox, QTimeEdit, QScrollArea, QFrame,
)
from PyQt5.QtCore import Qt, QDate, QTime, pyqtSignal
from PyQt5.QtGui import QFont, QColor

from services.project_service import (
    get_all_projects, get_zones_for_project,
    get_blocks_for_zone, get_containers_for_block,
    plant_block_to_zone,
)
from services.work_log_service import (
    save_work_log, get_work_logs, delete_work_log,
    update_work_log, STATUS_OPTIONS, STATUS_COLORS,
    add_work_log_material, set_work_log_unavailability, set_work_log_exclusion,
    link_work_log_alarms,
)
from services.stock_service import (
    get_project_warehouse_id, get_stock, record_transaction,
)
from services.availability_service import (
    add_manual_unavailability, add_exclusion, EXCLUSION_TYPES,
)


def _row_widget(layout):
    """Wrap a layout in a QWidget so it can be dropped into a QFormLayout row."""
    w = QWidget(); w.setLayout(layout); return w


# Friendly column headers for the table
TABLE_COLUMNS = [
    ("id",               "ID"),
    ("date",             "Date"),
    ("project",          "Project"),
    ("zone",             "Zone"),
    ("block",            "Block"),
    ("container_num",    "Cont #"),
    ("serial_number",    "Serial"),
    ("status",           "Status"),
    ("fault_description","Fault"),
    ("work_performed",   "Work Performed"),
    ("sap_ticket",       "SAP Ticket"),
    ("comments",         "Comments"),
]
COL_KEYS    = [c[0] for c in TABLE_COLUMNS]
COL_HEADERS = [c[1] for c in TABLE_COLUMNS]


class WorkLogForm(QWidget):
    """Work Log module: add entries + view/filter/edit existing ones."""

    # Emitted after saving a report that was raised from an alarm, so the
    # shell can take the engineer back to the list they came from.
    alarm_report_saved = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._containers = []   # containers in selected block
        self._all_rows   = []   # raw dicts from last DB query
        self._materials  = []   # parts staged for this work report
        self._wh_id      = None # project warehouse id (materials source)
        self._alarm_event_id = None  # set when raised from the Equipment page
        self._alarm_ids  = []   # every alarm this report answers (one incident)
        self._precedents = []   # earlier reports about the same fault
        self._build_ui()
        self._load_projects()

    # ── UI BUILD ──────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        title = QLabel("🔧  Work Reports")
        title.setFont(QFont("Segoe UI", 14, QFont.Bold))
        layout.addWidget(title)

        splitter = QSplitter(Qt.Horizontal)

        # ── LEFT: Entry form (scrollable — the Work Report has grown) ──────
        form_scroll = QScrollArea()
        form_scroll.setWidgetResizable(True)
        form_scroll.setFrameShape(QFrame.NoFrame)
        form_scroll.setMinimumWidth(390)
        form_scroll.setMaximumWidth(470)
        form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        form_widget = QWidget()
        form_layout = QVBoxLayout(form_widget)
        form_layout.setSpacing(6)

        # Shown only when this report was raised from an alarm on the
        # Equipment page, so it is obvious what the report is answering.
        self.alarm_banner = QLabel("")
        self.alarm_banner.setWordWrap(True)
        self.alarm_banner.setStyleSheet(
            "background:#EAF3FF;border:1px solid #B9D6F7;border-radius:6px;"
            "padding:8px 10px;color:#14508C;font-size:11px;")
        self.alarm_banner.setVisible(False)
        form_layout.addWidget(self.alarm_banner)

        # Asset group — pick the asset, serial + type auto-fill
        loc_group = QGroupBox("Asset")
        loc_form = QFormLayout()

        self.proj_combo = QComboBox()
        self.proj_combo.currentIndexChanged.connect(self._on_project_changed)
        loc_form.addRow("Project *:", self.proj_combo)

        self.date_edit = QDateEdit()
        self.date_edit.setDate(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        loc_form.addRow("Date *:", self.date_edit)

        self.zone_combo = QComboBox()
        self.zone_combo.currentIndexChanged.connect(self._on_zone_changed)
        loc_form.addRow("Zone *:", self.zone_combo)

        self.block_combo = QComboBox()
        self.block_combo.currentIndexChanged.connect(self._on_block_changed)
        loc_form.addRow("Block *:", self.block_combo)

        self.container_combo = QComboBox()
        self.container_combo.currentIndexChanged.connect(self._on_container_changed)
        loc_form.addRow("Container *:", self.container_combo)

        self.serial_label = QLabel("—")
        self.serial_label.setStyleSheet("color:#1565C0;font-weight:bold;")
        loc_form.addRow("Serial Number:", self.serial_label)

        self.type_label = QLabel("—")
        self.type_label.setStyleSheet("color:#1565C0;font-weight:bold;")
        loc_form.addRow("Type:", self.type_label)

        loc_group.setLayout(loc_form)
        form_layout.addWidget(loc_group)

        # Work details group
        work_group = QGroupBox("Work Details")
        work_form = QFormLayout()

        self.fault_input = QTextEdit()
        self.fault_input.setMaximumHeight(56)
        self.fault_input.setPlaceholderText("Describe the fault or error observed...")
        # Look for earlier reports as soon as the engineer stops typing the
        # fault — the precedent is most useful before writing the fix, not after
        self.fault_input.installEventFilter(self)
        work_form.addRow("Fault / Error *:", self.fault_input)

        self.root_cause_input = QTextEdit()
        self.root_cause_input.setMaximumHeight(48)
        self.root_cause_input.setPlaceholderText("Root cause (optional)…")
        work_form.addRow("Root Cause:", self.root_cause_input)

        self.work_input = QTextEdit()
        self.work_input.setMaximumHeight(56)
        self.work_input.setPlaceholderText("Describe the corrective action performed...")
        work_form.addRow("Corrective Action *:", self.work_input)

        self.status_combo = QComboBox()
        self.status_combo.addItems(STATUS_OPTIONS)
        work_form.addRow("Result Status *:", self.status_combo)

        self.engineer_input = QLineEdit()
        self.engineer_input.setPlaceholderText("Engineer name (optional)")
        work_form.addRow("Engineer:", self.engineer_input)

        time_row = QHBoxLayout(); time_row.setContentsMargins(0, 0, 0, 0)
        self.start_time = QTimeEdit(); self.start_time.setDisplayFormat("HH:mm")
        self.end_time = QTimeEdit(); self.end_time.setDisplayFormat("HH:mm")
        time_row.addWidget(QLabel("Start")); time_row.addWidget(self.start_time)
        time_row.addWidget(QLabel("End")); time_row.addWidget(self.end_time)
        work_form.addRow("Time:", _row_widget(time_row))

        self.sap_input = QLineEdit()
        self.sap_input.setPlaceholderText("e.g. SAP-12345 (optional)")
        work_form.addRow("SAP Ticket:", self.sap_input)

        self.comments_input = QTextEdit()
        self.comments_input.setMaximumHeight(44)
        self.comments_input.setPlaceholderText("Optional comments...")
        work_form.addRow("Comments:", self.comments_input)

        work_group.setLayout(work_form)
        form_layout.addWidget(work_group)

        # Materials used → decrements the project warehouse on save
        mat_group = QGroupBox("Materials Used")
        mat_l = QVBoxLayout(mat_group)
        self.no_materials = QCheckBox("No materials used")
        self.no_materials.toggled.connect(self._toggle_no_materials)
        mat_l.addWidget(self.no_materials)
        pick_row = QHBoxLayout(); pick_row.setContentsMargins(0, 0, 0, 0)
        self.mat_combo = QComboBox(); self.mat_combo.setMinimumWidth(150)
        pick_row.addWidget(self.mat_combo, 1)
        self.mat_qty = QDoubleSpinBox(); self.mat_qty.setRange(0.0, 100000)
        self.mat_qty.setValue(1); self.mat_qty.setDecimals(2)
        pick_row.addWidget(self.mat_qty)
        add_mat_btn = QPushButton("➕"); add_mat_btn.setFixedWidth(34)
        add_mat_btn.clicked.connect(self._add_material)
        pick_row.addWidget(add_mat_btn)
        mat_l.addLayout(pick_row)
        self.mat_hint = QLabel("")
        self.mat_hint.setStyleSheet("color:#8A5A00;font-size:11px;")
        self.mat_hint.setWordWrap(True)
        mat_l.addWidget(self.mat_hint)
        self.mat_table = QTableWidget(0, 3)
        self.mat_table.setHorizontalHeaderLabels(["Material", "Qty", "Unit"])
        self.mat_table.setMaximumHeight(110)
        self.mat_table.horizontalHeader().setStretchLastSection(True)
        self.mat_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.mat_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.mat_table.verticalHeader().setVisible(False)
        mat_l.addWidget(self.mat_table)
        rm_mat_btn = QPushButton("🗑  Remove selected material")
        rm_mat_btn.clicked.connect(self._remove_material)
        mat_l.addWidget(rm_mat_btn)
        form_layout.addWidget(mat_group)

        # "Seen before?" — what was done about this same fault last time.
        self.prec_group = QGroupBox("Seen before")
        pl = QVBoxLayout(self.prec_group)
        pl.setSpacing(4)
        # What the manufacturer's guide says to do about this fault.
        self.prec_ref = QLabel("")
        self.prec_ref.setWordWrap(True)
        self.prec_ref.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.prec_ref.setStyleSheet(
            "background:#F2F7FF;border:1px solid #CFE0F5;border-radius:5px;"
            "padding:7px 9px;color:#24405E;font-size:11px;")
        self.prec_ref.setVisible(False)
        pl.addWidget(self.prec_ref)

        # Our own note on this fault, if anyone has written one. Internal —
        # it is not part of what the monthly report sends the customer.
        self.prec_note = QLabel("")
        self.prec_note.setWordWrap(True)
        self.prec_note.setStyleSheet(
            "background:#FFF9E6;border:1px solid #F0DFA8;border-radius:5px;"
            "padding:7px 9px;color:#6B5518;font-size:11px;")
        self.prec_note.setVisible(False)
        pl.addWidget(self.prec_note)
        self.prec_hint = QLabel("No earlier report for this fault.")
        self.prec_hint.setStyleSheet("color:#6B7A8D;font-size:11px;")
        self.prec_hint.setWordWrap(True)
        pl.addWidget(self.prec_hint)
        self.prec_list = QTableWidget(0, 4)
        self.prec_list.setHorizontalHeaderLabels(
            ["Date", "Block", "What was done", "Result"])
        self.prec_list.setMaximumHeight(120)
        self.prec_list.horizontalHeader().setStretchLastSection(True)
        self.prec_list.setColumnWidth(0, 76)
        self.prec_list.setColumnWidth(1, 44)
        self.prec_list.setColumnWidth(2, 210)
        self.prec_list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.prec_list.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.prec_list.verticalHeader().setVisible(False)
        self.prec_list.itemSelectionChanged.connect(self._show_precedent)
        pl.addWidget(self.prec_list)
        self.prec_detail = QLabel("")
        self.prec_detail.setWordWrap(True)
        self.prec_detail.setStyleSheet(
            "background:#F5F8FC;border:1px solid #DDE6F0;border-radius:5px;"
            "padding:6px 8px;color:#33465E;font-size:11px;")
        self.prec_detail.setVisible(False)
        pl.addWidget(self.prec_detail)
        self.prec_copy = QPushButton("↧  Copy this fix into the form")
        self.prec_copy.clicked.connect(self._copy_precedent)
        self.prec_copy.setEnabled(False)
        pl.addWidget(self.prec_copy)
        self.prec_group.setVisible(False)
        form_layout.addWidget(self.prec_group)

        # Availability impact — classify how this event affects availability
        av_group = QGroupBox("Availability Impact")
        av_l = QFormLayout(av_group)
        self.impact_combo = QComboBox()
        self.impact_combo.addItem("No impact on availability", "none")
        self.impact_combo.addItem("Counts as unavailability (our fault / PM)", "counts")
        self.impact_combo.addItem("Excluded — not our fault (grid, force majeure)", "excluded")
        self.impact_combo.currentIndexChanged.connect(self._on_impact_changed)
        av_l.addRow("Impact:", self.impact_combo)
        self.downtime_h = QDoubleSpinBox(); self.downtime_h.setRange(0.0, 100000)
        self.downtime_h.setDecimals(2); self.downtime_h.setSuffix(" h")
        self.downtime_h.setEnabled(False)
        av_l.addRow("Downtime:", self.downtime_h)
        self.lc_combo = QComboBox()
        self.lc_combo.addItem("Whole block", None)
        self.lc_combo.addItem("LC 1", 1); self.lc_combo.addItem("LC 2", 2)
        self.lc_combo.setEnabled(False)
        av_l.addRow("Scope:", self.lc_combo)
        self.excl_type = QComboBox()
        for t in EXCLUSION_TYPES:
            self.excl_type.addItem(t, t)
        self.excl_type.setEnabled(False)
        av_l.addRow("Exclusion type:", self.excl_type)
        self.av_hint = QLabel("Choose how this event affects the monthly availability figure.")
        self.av_hint.setStyleSheet("color:#6B7A8D;font-size:11px;")
        self.av_hint.setWordWrap(True)
        av_l.addRow(self.av_hint)
        form_layout.addWidget(av_group)

        # Buttons
        btn_row = QHBoxLayout()
        clear_btn = QPushButton("🔄 Clear")
        clear_btn.clicked.connect(self._clear_form)
        btn_row.addWidget(clear_btn)

        save_btn = QPushButton("✅  Save Work Report")
        save_btn.setFixedHeight(38)
        save_btn.setStyleSheet(
            "QPushButton{background:#4CAF50;color:white;font-weight:bold;"
            "border-radius:4px;padding:0 16px;}"
            "QPushButton:hover{background:#388E3C;}"
        )
        save_btn.clicked.connect(self._save_entry)
        btn_row.addWidget(save_btn)
        form_layout.addLayout(btn_row)
        form_layout.addStretch()

        form_scroll.setWidget(form_widget)
        splitter.addWidget(form_scroll)

        # ── RIGHT: Filter + Table ─────────────────────────────────────────
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setSpacing(6)

        # Filter bar
        filter_group = QGroupBox("Filter")
        filter_row = QHBoxLayout()

        filter_row.addWidget(QLabel("Project:"))
        self.filter_proj = QComboBox()
        self.filter_proj.setMinimumWidth(150)
        filter_row.addWidget(self.filter_proj)

        filter_row.addWidget(QLabel("From:"))
        self.filter_from = QDateEdit()
        self.filter_from.setDate(QDate.currentDate().addMonths(-1))
        self.filter_from.setCalendarPopup(True)
        self.filter_from.setDisplayFormat("yyyy-MM-dd")
        filter_row.addWidget(self.filter_from)

        filter_row.addWidget(QLabel("To:"))
        self.filter_to = QDateEdit()
        self.filter_to.setDate(QDate.currentDate())
        self.filter_to.setCalendarPopup(True)
        self.filter_to.setDisplayFormat("yyyy-MM-dd")
        filter_row.addWidget(self.filter_to)

        filter_row.addWidget(QLabel("Status:"))
        self.filter_status = QComboBox()
        self.filter_status.addItem("All", userData=None)
        for s in STATUS_OPTIONS:
            self.filter_status.addItem(s, userData=s)
        filter_row.addWidget(self.filter_status)

        load_btn = QPushButton("🔍 Load")
        load_btn.setStyleSheet(
            "QPushButton{background:#1976D2;color:white;border-radius:4px;padding:4px 12px;}"
        )
        load_btn.clicked.connect(self._load_table)
        filter_row.addWidget(load_btn)
        filter_row.addStretch()
        filter_group.setLayout(filter_row)
        right_layout.addWidget(filter_group)

        # Result info + export button
        info_row = QHBoxLayout()
        self.result_label = QLabel("Click 'Load' to view work logs.")
        self.result_label.setStyleSheet("color:#555;font-style:italic;")
        info_row.addWidget(self.result_label)
        info_row.addStretch()

        export_btn = QPushButton("📥 Export Excel")
        export_btn.setStyleSheet(
            "QPushButton{background:#388E3C;color:white;border-radius:4px;padding:4px 12px;}"
        )
        export_btn.clicked.connect(self._export_excel)
        info_row.addWidget(export_btn)
        right_layout.addLayout(info_row)

        # Table
        self.table = QTableWidget(0, len(COL_HEADERS))
        self.table.setHorizontalHeaderLabels(COL_HEADERS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.hideColumn(0)   # hide ID column
        right_layout.addWidget(self.table)

        splitter.addWidget(right_widget)
        splitter.setCollapsible(0, False)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([440, 760])
        layout.addWidget(splitter, 1)

    # ── Project/location cascade ──────────────────────────────────────────

    def refresh_projects(self):
        self._load_projects()

    def _load_projects(self):
        for combo in [self.proj_combo, self.filter_proj]:
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("— Select —", userData=None)
            for p in get_all_projects():
                combo.addItem(p.name, userData=p.id)
            combo.blockSignals(False)
        self._on_project_changed()

    def _on_project_changed(self):
        self.zone_combo.blockSignals(True)
        self.zone_combo.clear()
        self.zone_combo.addItem("— Zone —", userData=None)
        pid = self.proj_combo.currentData()
        if pid:
            for z in get_zones_for_project(pid):
                self.zone_combo.addItem(f"Zone {z}", userData=z)
        self.zone_combo.blockSignals(False)
        self._on_zone_changed()
        self._reload_materials_combo()

    def _on_zone_changed(self):
        self.block_combo.blockSignals(True)
        self.block_combo.clear()
        self.block_combo.addItem("— Block —", userData=None)
        pid = self.proj_combo.currentData()
        zone = self.zone_combo.currentData()
        if pid and zone:
            for b in get_blocks_for_zone(pid, zone):
                self.block_combo.addItem(f"Block {b}", userData=b)
        self.block_combo.blockSignals(False)
        self._on_block_changed()

    def _on_block_changed(self):
        self.container_combo.clear()
        self.container_combo.addItem("— Container —", userData=None)
        pid   = self.proj_combo.currentData()
        zone  = self.zone_combo.currentData()
        block = self.block_combo.currentData()
        self._containers = []
        if pid and zone and block:
            self._containers = get_containers_for_block(pid, zone, block)
            for c in self._containers:
                label = f"C{c.container_index} ({c.container_type})"
                self.container_combo.addItem(label, userData=c.id)
        self._on_container_changed()

    def _on_container_changed(self):
        cid = self.container_combo.currentData()
        if cid is None:
            self.serial_label.setText("—")
            self.type_label.setText("—")
            return
        c = next((x for x in self._containers if x.id == cid), None)
        if c:
            self.serial_label.setText(c.serial_number or "N/A")
            self.type_label.setText(c.container_type)

    # ── Raised from an alarm ────────────────────────────────────────────────

    def prefill_from_alarm(self, ev: dict):
        """Open this form already filled in from a SCADA alarm.

        `ev` is a row from asset_tree_service (alarm_events). Everything the
        alarm already knows — where, what, when, how long, and whether the
        downtime counts — is filled in, so the engineer only writes what was
        actually done.
        """
        self._alarm_event_id = ev.get('id')
        # A grouped incident carries every alarm it covers; a single alarm is
        # just a group of one.
        self._alarm_ids = [int(i) for i in (ev.get('ids') or [ev.get('id')]) if i]

        pid = ev.get('project_id')
        if pid is not None:
            idx = self.proj_combo.findData(pid)
            if idx >= 0:
                self.proj_combo.setCurrentIndex(idx)

        block = ev.get('block')
        if block is not None:
            self._select_block(int(block))
        lc = ev.get('lc')
        self._select_container_for(ev)

        act = str(ev.get('activated') or '')
        de = str(ev.get('deactivated') or '')
        if len(act) >= 10:
            d = QDate.fromString(act[:10], 'yyyy-MM-dd')
            if d.isValid():
                self.date_edit.setDate(d)
        if len(act) >= 16:
            t = QTime.fromString(act[11:16], 'HH:mm')
            if t.isValid():
                self.start_time.setTime(t)
        # The form records one day's work. An alarm that ran past midnight
        # would otherwise read as "15:26 → 17:31" on the start date, i.e. two
        # hours instead of five weeks — so only fill the end time when the
        # alarm actually cleared the same day, and spell the real window out
        # in the comments either way.
        same_day = len(de) >= 10 and de[:10] == act[:10]
        if same_day and len(de) >= 16:
            t = QTime.fromString(de[11:16], 'HH:mm')
            if t.isValid():
                self.end_time.setTime(t)
        elif de:
            self.comments_input.setPlainText(
                f"Alarm window {act[:16]} → {de[:16]} "
                f"({float(ev.get('hours') or 0):.2f} h, spans several days).")

        parts = [str(ev.get('trigger_name') or '').strip()]
        assets = ev.get('assets') or []
        if len(self._alarm_ids) > 1 and assets:
            shown = ', '.join(assets[:8])
            more = f" +{len(assets) - 8} more" if len(assets) > 8 else ''
            parts.append(f"[{len(assets)} units: {shown}{more}]")
        elif ev.get('element'):
            parts.append(f"[{ev['element']}]")
        self.fault_input.setPlainText(' '.join(p for p in parts if p))
        reason = str(ev.get('cls_reason') or '').strip()
        if reason and reason.lower() != 'unclassified':
            self.root_cause_input.setPlainText(reason)

        # Availability impact stays "none" on purpose. This downtime is
        # already in the availability figure — it came from SCADA. Adding a
        # manual_unavailability row for the same alarm would charge the plant
        # twice; the same goes for an exclusion when the alarm already fell
        # inside an exclusion window. Only override this when SCADA missed
        # downtime that the alarm does not cover.
        hours = float(ev.get('hours') or 0)
        self._set_impact('none')
        i = self.lc_combo.findData(int(lc) if lc is not None else None)
        if i >= 0:
            self.lc_combo.setCurrentIndex(i)
        self.av_hint.setText(
            'Left at "no impact": this alarm is already counted in the '
            'availability figure from SCADA. Change it only for downtime the '
            'alarm does not already cover.')

        state = ('inside an exclusion window (' + str(ev.get('excluded_by') or '')
                 + ')') if ev.get('is_excluded') else 'counted against availability'
        n = len(self._alarm_ids)
        if n > 1:
            assets = ev.get('assets') or []
            where = ', '.join(assets[:6]) + ('…' if len(assets) > 6 else '')
            self.alarm_banner.setText(
                f"⚡ One incident, {n} units — {ev.get('trigger_name','')} · "
                f"from {act[:16]} · worst {hours:.2f} h "
                f"({float(ev.get('total_hours') or 0):.2f} h in total). "
                f"Units: {where}. Saving covers all {n} alarms.")
        else:
            self.alarm_banner.setText(
                f"⚡ Raised from alarm #{ev.get('id')} — {ev.get('element','')} · "
                f"{act[:16]} · {hours:.2f} h · {state}. "
                f"Saving links the report to it.")
        self.alarm_banner.setVisible(True)
        self._load_precedents(str(ev.get('trigger_name') or ''))
        self.work_input.setFocus()

    def _select_block(self, plant_block: int):
        """Select the zone/block pair for a plant-wide block number.

        The alarm says "plant block 57"; the form's dropdowns are per zone,
        where that is zone 8, block 2. See project_service.get_block_map.
        """
        pid = self.proj_combo.currentData()
        if not pid:
            return None, None
        zone, local = plant_block_to_zone(pid, plant_block)
        if zone is None:
            return None, None
        zi = self.zone_combo.findData(zone)
        if zi < 0:
            return None, None
        self.zone_combo.setCurrentIndex(zi)
        bi = self.block_combo.findData(local)
        if bi >= 0:
            self.block_combo.setCurrentIndex(bi)
        return zone, local

    # SCADA element type → the kind of container that houses it. The
    # container inventory is coarser than the SCADA hierarchy (one "LC
    # Cabinet" row per block, where SCADA sees two LCs), so this picks the
    # right *kind* of asset; the exact unit stays in the fault text.
    _EQ_TO_CONTAINER = (
        ('DC/DC', 'battery'), ('CMU', 'battery'), ('BMS', 'battery'),
        ('BSC', 'pcs'), ('PCS', 'pcs'),
        ('LC', 'lc'),
    )

    def _select_container_for(self, ev: dict):
        eq = str(ev.get('equipment_type') or '').upper()
        want = next((kind for prefix, kind in self._EQ_TO_CONTAINER
                     if eq.startswith(prefix)), None)
        if want is None:
            return
        for i in range(self.container_combo.count()):
            cid = self.container_combo.itemData(i)
            c = next((x for x in self._containers if x.id == cid), None)
            if c and want in str(c.container_type or '').lower():
                self.container_combo.setCurrentIndex(i)
                return

    # ── "Seen before?" ─────────────────────────────────────────────────────

    def eventFilter(self, obj, event):
        from PyQt5.QtCore import QEvent
        if (obj is self.fault_input and event.type() == QEvent.FocusOut
                and not self._alarm_event_id):
            if len(self.fault_input.toPlainText().strip()) >= 6:
                self._load_precedents()
        return super().eventFilter(obj, event)

    def _load_precedents(self, trigger_name: str = ''):
        """Show what was done about this same fault before."""
        import services.asset_tree_service as ats
        pid = self.proj_combo.currentData()
        self._precedents = []
        self.prec_list.setRowCount(0)
        self.prec_detail.setVisible(False)
        self.prec_copy.setEnabled(False)
        if not pid:
            self.prec_group.setVisible(False)
            return
        try:
            self._precedents = ats.find_precedents(
                pid, trigger_name=trigger_name,
                fault_text=self.fault_input.toPlainText().strip())
        except Exception:                            # noqa: BLE001
            self._precedents = []
        # The manufacturer's entry for this fault, when there is one.
        ref = None
        if trigger_name:
            try:
                ref = ats.get_fault_reference(trigger_name)
            except Exception:                        # noqa: BLE001
                ref = None
        if ref and ref.get('remedy'):
            self.prec_ref.setText(
                f"🛠  <b>{ref.get('source', '')}</b><br>{ref['remedy']}")
            self.prec_ref.setVisible(True)
        else:
            self.prec_ref.setVisible(False)

        note = None
        if trigger_name:
            try:
                note = ats.get_fault_note(pid, trigger_name)
            except Exception:                        # noqa: BLE001
                note = None
        if note and (note.get('note') or '').strip():
            self.prec_note.setText('📓  ' + note['note'].strip())
            self.prec_note.setVisible(True)
        else:
            self.prec_note.setVisible(False)

        self.prec_group.setVisible(True)
        if not self._precedents:
            self.prec_hint.setText(
                "No earlier report for this fault — this is the first one.")
            return
        exact = sum(1 for p in self._precedents if p['match'] == 'same alarm')
        self.prec_hint.setText(
            f"{len(self._precedents)} earlier report(s)"
            + (f", {exact} on the same alarm" if exact else ", matched on text")
            + ". Pick one to see what was done.")
        for p in self._precedents:
            r = self.prec_list.rowCount()
            self.prec_list.insertRow(r)
            blk = ''
            if p.get('block_number') is not None:
                from services.project_service import zone_block_to_plant
                blk = str(zone_block_to_plant(pid, p.get('zone_number'),
                                              p.get('block_number'))
                          or p.get('block_number'))
            for c, v in enumerate([str(p.get('date') or ''), blk,
                                   str(p.get('work_performed') or '')[:90],
                                   str(p.get('status') or '')]):
                self.prec_list.setItem(r, c, QTableWidgetItem(v))

    def _show_precedent(self):
        r = self.prec_list.currentRow()
        if not (0 <= r < len(self._precedents)):
            return
        p = self._precedents[r]
        bits = [f"<b>{p.get('date','')}</b> — {p.get('fault_description','')}"]
        if p.get('root_cause'):
            bits.append(f"Root cause: {p['root_cause']}")
        if p.get('work_performed'):
            bits.append(f"Fix: {p['work_performed']}")
        if p.get('materials'):
            bits.append("Parts: " + ", ".join(p['materials']))
        tail = " · ".join(x for x in [p.get('engineer'), p.get('status'),
                                      p.get('sap_ticket')] if x)
        if tail:
            bits.append(tail)
        self.prec_detail.setText("<br>".join(bits))
        self.prec_detail.setVisible(True)
        self.prec_copy.setEnabled(bool(p.get('work_performed')))

    def _copy_precedent(self):
        r = self.prec_list.currentRow()
        if not (0 <= r < len(self._precedents)):
            return
        p = self._precedents[r]
        if p.get('work_performed'):
            self.work_input.setPlainText(str(p['work_performed']))
        if p.get('root_cause') and not self.root_cause_input.toPlainText().strip():
            self.root_cause_input.setPlainText(str(p['root_cause']))
        self.work_input.setFocus()

    def _set_impact(self, mode: str):
        i = self.impact_combo.findData(mode)
        if i >= 0:
            self.impact_combo.setCurrentIndex(i)

    def _clear_alarm_link(self):
        self._alarm_event_id = None
        self._alarm_ids = []
        self.alarm_banner.setVisible(False)
        self._precedents = []
        self.prec_list.setRowCount(0)
        self.prec_detail.setVisible(False)
        self.prec_note.setVisible(False)
        self.prec_ref.setVisible(False)
        self.prec_copy.setEnabled(False)
        self.prec_group.setVisible(False)

    # ── Materials & availability ────────────────────────────────────────────

    def _reload_materials_combo(self):
        """Populate the material picker from the current project's warehouse."""
        self.mat_combo.clear()
        self._materials = []
        self._refresh_mat_table()
        self.mat_hint.setText("")
        pid = self.proj_combo.currentData()
        self._wh_id = None
        if not pid:
            return
        self._wh_id = get_project_warehouse_id(pid)
        if not self._wh_id:
            self.mat_hint.setText("No project warehouse yet.")
            return
        stock = get_stock(self._wh_id)
        if not stock:
            self.mat_hint.setText("No stock in the project warehouse — add it on the Spare Parts page.")
        for s in stock:
            label = (f"{s['material_number']} — {s.get('description','')}  "
                     f"({s['quantity']:g} {s.get('unit','')} in stock)")
            self.mat_combo.addItem(label, s)

    def _toggle_no_materials(self, checked):
        self.mat_combo.setEnabled(not checked)
        self.mat_qty.setEnabled(not checked)
        if checked:
            self._materials = []
            self._refresh_mat_table()

    def _add_material(self):
        if self.no_materials.isChecked():
            return
        data = self.mat_combo.currentData()
        if not data:
            QMessageBox.information(self, "No material",
                "No stock available in the project warehouse.\n"
                "Add stock on the Spare Parts page first.")
            return
        qty = self.mat_qty.value()
        if qty <= 0:
            return
        avail = data.get("quantity", 0) or 0
        if qty > avail:
            self.mat_hint.setText(
                f"⚠ {data['material_number']}: only {avail:g} in stock (will go to 0).")
        else:
            self.mat_hint.setText("")
        self._materials.append({
            "material_number": data["material_number"],
            "description":     data.get("description", ""),
            "quantity":        qty,
            "unit":            data.get("unit", ""),
        })
        self._refresh_mat_table()

    def _remove_material(self):
        r = self.mat_table.currentRow()
        if 0 <= r < len(self._materials):
            self._materials.pop(r)
            self._refresh_mat_table()

    def _refresh_mat_table(self):
        self.mat_table.setRowCount(0)
        for m in self._materials:
            r = self.mat_table.rowCount()
            self.mat_table.insertRow(r)
            for c, v in enumerate([m["material_number"],
                                   f"{m['quantity']:g}", m["unit"]]):
                self.mat_table.setItem(r, c, QTableWidgetItem(str(v)))

    def _on_impact_changed(self, *_):
        mode = self.impact_combo.currentData()
        counts = (mode == "counts")
        excluded = (mode == "excluded")
        self.downtime_h.setEnabled(counts or excluded)
        self.lc_combo.setEnabled(counts)
        self.excl_type.setEnabled(excluded)
        if counts:
            self.av_hint.setText("Counted as downtime — reduces availability in the monthly report.")
        elif excluded:
            self.av_hint.setText("Credited back — this downtime will NOT reduce availability.")
        else:
            self.av_hint.setText("Choose how this event affects the monthly availability figure.")

    # ── Save ──────────────────────────────────────────────────────────────

    def _save_entry(self):
        if self.proj_combo.currentData() is None:
            QMessageBox.warning(self, "Required", "Please select a project.")
            return
        if self.container_combo.currentData() is None:
            QMessageBox.warning(self, "Required", "Please select Zone → Block → Container.")
            return
        if not self.fault_input.toPlainText().strip():
            QMessageBox.warning(self, "Required", "Fault description is required.")
            return
        if not self.work_input.toPlainText().strip():
            QMessageBox.warning(self, "Required", "Work performed is required.")
            return

        pid = self.proj_combo.currentData()
        cid = self.container_combo.currentData()
        c   = next((x for x in self._containers if x.id == cid), None)
        date_str = self.date_edit.date().toString("yyyy-MM-dd")
        block    = self.block_combo.currentData()
        impact   = self.impact_combo.currentData()

        entry_id = save_work_log(
            project_id       = pid,
            container_id     = cid,
            date             = date_str,
            zone_number      = self.zone_combo.currentData(),
            block_number     = block,
            container_index  = c.container_index if c else 0,
            serial_number    = c.serial_number if c else "",
            fault_description= self.fault_input.toPlainText().strip(),
            work_performed   = self.work_input.toPlainText().strip(),
            status           = self.status_combo.currentText(),
            sap_ticket       = self.sap_input.text().strip(),
            comments         = self.comments_input.toPlainText().strip(),
            root_cause       = self.root_cause_input.toPlainText().strip(),
            engineer         = self.engineer_input.text().strip(),
            start_time       = self.start_time.time().toString("HH:mm"),
            end_time         = self.end_time.time().toString("HH:mm"),
            affects_availability = 1 if impact in ("counts", "excluded") else 0,
            availability_impact  = impact,
            alarm_event_id       = self._alarm_event_id,
        )

        # ── Fan-out 1: material consumption → OUT stock transactions ─────────
        extras = []
        consumed = (not self.no_materials.isChecked()
                    and self._materials and self._wh_id)
        if consumed:
            for m in self._materials:
                tx_id = None
                try:
                    tx_id = record_transaction(
                        warehouse_id     = self._wh_id,
                        material_number  = m["material_number"],
                        transaction_type = "OUT",
                        quantity         = m["quantity"],
                        transaction_date = date_str,
                        project_id       = pid,
                        reference        = f"WL-{entry_id}",
                        notes            = "Work report material",
                    )
                except Exception as e:
                    QMessageBox.warning(self, "Stock",
                        f"Could not deduct {m['material_number']}:\n{e}")
                add_work_log_material(
                    entry_id, m["material_number"], m["description"],
                    m["quantity"], m["unit"], self._wh_id, tx_id)
            extras.append(f"{len(self._materials)} material(s) deducted from stock")

        # ── Fan-out 2: availability impact → counts (downtime) or excluded ──
        h = self.downtime_h.value()
        desc = self.fault_input.toPlainText().strip()[:200] or "Work report"
        y, mo = self.date_edit.date().year(), self.date_edit.date().month()
        if impact == "counts" and h > 0 and block:
            try:
                unavail_id = add_manual_unavailability(
                    block=int(block), date_from=date_str, date_to=date_str,
                    downtime_h=h, lc=self.lc_combo.currentData(), cause=desc,
                    project_id=pid, year=y, month=mo)
                set_work_log_unavailability(entry_id, unavail_id)
                extras.append(f"{h:g} h counted as unavailability")
            except Exception as e:
                QMessageBox.warning(self, "Availability",
                    f"Work report saved, but the downtime event failed:\n{e}")
        elif impact == "excluded" and h > 0 and block:
            try:
                total_min = min(int(round(h * 60)), 1439)
                time_to = f"{total_min // 60:02d}:{total_min % 60:02d}"
                excl_id = add_exclusion(
                    exclusion_type=self.excl_type.currentData(),
                    date_from=date_str, date_to=date_str,
                    time_from="00:00", time_to=time_to,
                    affected_blocks=str(block), description=desc,
                    project_id=pid, year=y, month=mo)
                set_work_log_exclusion(entry_id, excl_id)
                extras.append(f"{h:g} h excluded ({self.excl_type.currentData()}) — not counted")
            except Exception as e:
                QMessageBox.warning(self, "Availability",
                    f"Work report saved, but the exclusion failed:\n{e}")

        if self._alarm_ids:
            link_work_log_alarms(entry_id, self._alarm_ids)
            n = len(self._alarm_ids)
            extras.append(f"linked to {n} alarm(s)" if n > 1
                          else f"linked to alarm #{self._alarm_event_id}")

        from_alarm = bool(self._alarm_ids)
        msg = f"Work report #{entry_id} saved."
        if extras:
            msg += "\n\n• " + "\n• ".join(extras)
        QMessageBox.information(self, "Saved", msg)
        self._clear_work_fields()
        self._reload_materials_combo()   # refresh stock quantities in the picker
        self._load_table()
        if from_alarm:
            self.alarm_report_saved.emit(entry_id)

    def _clear_work_fields(self):
        self._clear_alarm_link()
        self.fault_input.clear()
        self.root_cause_input.clear()
        self.work_input.clear()
        self.status_combo.setCurrentIndex(0)
        self.engineer_input.clear()
        self.sap_input.clear()
        self.comments_input.clear()
        self.date_edit.setDate(QDate.currentDate())
        self.no_materials.setChecked(False)
        self._materials = []
        self._refresh_mat_table()
        self.impact_combo.setCurrentIndex(0)
        self.downtime_h.setValue(0)
        self.lc_combo.setCurrentIndex(0)
        self.excl_type.setCurrentIndex(0)

    def _clear_form(self):
        self.proj_combo.setCurrentIndex(0)
        self._clear_work_fields()

    # ── Table loading ─────────────────────────────────────────────────────

    def _load_table(self):
        pid    = self.filter_proj.currentData()
        status = self.filter_status.currentData()
        rows   = get_work_logs(
            project_id = pid,
            date_from  = self.filter_from.date().toString("yyyy-MM-dd"),
            date_to    = self.filter_to.date().toString("yyyy-MM-dd"),
            status     = status,
        )
        self._all_rows = rows
        self.table.setRowCount(0)

        for row_data in rows:
            row_idx = self.table.rowCount()
            self.table.insertRow(row_idx)
            row_status = row_data.get("status", "Fixed")
            bg = QColor(STATUS_COLORS.get(row_status, "#FFFFFF"))

            for col_idx, key in enumerate(COL_KEYS):
                val = row_data.get(key, "")
                item = QTableWidgetItem(str(val) if val else "")
                item.setTextAlignment(Qt.AlignCenter)
                item.setBackground(bg)
                self.table.setItem(row_idx, col_idx, item)

        count = len(rows)
        self.result_label.setText(
            f"{count} entr{'ies' if count != 1 else 'y'} found."
        )

    # ── Context menu (right-click) ────────────────────────────────────────

    def _show_context_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        menu = QMenu(self)
        edit_action   = menu.addAction("✏️  Edit this entry")
        delete_action = menu.addAction("🗑  Delete this entry")
        action = menu.exec_(self.table.viewport().mapToGlobal(pos))

        if action == edit_action:
            self._edit_row(row)
        elif action == delete_action:
            self._delete_row(row)

    def _get_row_id(self, row: int) -> int:
        item = self.table.item(row, 0)   # ID column (hidden)
        return int(item.text()) if item else -1

    def _delete_row(self, row: int):
        entry_id = self._get_row_id(row)
        if entry_id < 0:
            return
        answer = QMessageBox.question(
            self, "Delete?",
            "Delete this work report?\n\n"
            "Any consumed stock will be restored and the linked downtime "
            "event removed from the monthly report.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if answer == QMessageBox.Yes:
            delete_work_log(entry_id)
            self._load_table()

    def _edit_row(self, row: int):
        entry_id = self._get_row_id(row)
        if entry_id < 0:
            return
        # Find the raw dict for this entry
        entry = next((r for r in self._all_rows if r["id"] == entry_id), None)
        if not entry:
            return
        dlg = WorkLogEditDialog(entry, parent=self)
        if dlg.exec_() == QDialog.Accepted:
            update_work_log(entry_id, **dlg.get_data())
            self._load_table()

    # ── Export ────────────────────────────────────────────────────────────

    def _export_excel(self):
        from PyQt5.QtWidgets import QFileDialog
        from services.work_log_service import export_work_logs_excel
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Work Logs", "work_logs.xlsx", "Excel Files (*.xlsx)"
        )
        if not file_path:
            return
        pid = self.filter_proj.currentData()
        try:
            export_work_logs_excel(
                file_path, project_id=pid,
                date_from=self.filter_from.date().toString("yyyy-MM-dd"),
                date_to=self.filter_to.date().toString("yyyy-MM-dd"),
            )
            QMessageBox.information(self, "Exported", f"Saved to:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))


# ── Edit dialog ───────────────────────────────────────────────────────────────

class WorkLogEditDialog(QDialog):
    """Simple dialog to edit an existing work log entry."""

    def __init__(self, entry: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Work Log Entry")
        self.setMinimumWidth(460)
        self._build_ui(entry)

    def _build_ui(self, entry):
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(8)

        self.date_edit = QDateEdit()
        self.date_edit.setDate(
            QDate.fromString(entry.get("date",""), "yyyy-MM-dd")
            or QDate.currentDate()
        )
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        form.addRow("Date:", self.date_edit)

        self.fault_input = QTextEdit()
        self.fault_input.setMaximumHeight(70)
        self.fault_input.setPlainText(entry.get("fault_description",""))
        form.addRow("Fault:", self.fault_input)

        self.work_input = QTextEdit()
        self.work_input.setMaximumHeight(70)
        self.work_input.setPlainText(entry.get("work_performed",""))
        form.addRow("Work Performed:", self.work_input)

        self.status_combo = QComboBox()
        self.status_combo.addItems(STATUS_OPTIONS)
        self.status_combo.setCurrentText(entry.get("status","Fixed"))
        form.addRow("Status:", self.status_combo)

        self.sap_input = QLineEdit(entry.get("sap_ticket",""))
        form.addRow("SAP Ticket:", self.sap_input)

        self.comments_input = QTextEdit()
        self.comments_input.setMaximumHeight(55)
        self.comments_input.setPlainText(entry.get("comments",""))
        form.addRow("Comments:", self.comments_input)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_data(self) -> dict:
        return {
            "date":             self.date_edit.date().toString("yyyy-MM-dd"),
            "fault_description":self.fault_input.toPlainText().strip(),
            "work_performed":   self.work_input.toPlainText().strip(),
            "status":           self.status_combo.currentText(),
            "sap_ticket":       self.sap_input.text().strip(),
            "comments":         self.comments_input.toPlainText().strip(),
        }
