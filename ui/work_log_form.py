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
from PyQt5.QtCore import Qt, QDate
from PyQt5.QtGui import QFont, QColor

from services.project_service import (
    get_all_projects, get_zones_for_project,
    get_blocks_for_zone, get_containers_for_block,
)
from services.work_log_service import (
    save_work_log, get_work_logs, delete_work_log,
    update_work_log, STATUS_OPTIONS, STATUS_COLORS,
    add_work_log_material, set_work_log_unavailability, set_work_log_exclusion,
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

    def __init__(self, parent=None):
        super().__init__(parent)
        self._containers = []   # containers in selected block
        self._all_rows   = []   # raw dicts from last DB query
        self._materials  = []   # parts staged for this work report
        self._wh_id      = None # project warehouse id (materials source)
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

        msg = f"Work report #{entry_id} saved."
        if extras:
            msg += "\n\n• " + "\n• ".join(extras)
        QMessageBox.information(self, "Saved", msg)
        self._clear_work_fields()
        self._reload_materials_combo()   # refresh stock quantities in the picker
        self._load_table()

    def _clear_work_fields(self):
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
