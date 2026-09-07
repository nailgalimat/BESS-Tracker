"""
ui/lifecycle_view.py
---------------------
Container Lifecycle Report screen.

Two ways to find a container:
  1. Type a serial number directly (with autocomplete suggestions)
  2. Use cascading dropdowns: Project → Zone → Block → Container

The table shows every maintenance event for that container, in
chronological order (oldest first).

Filters: date range, material number, SAP ticket
Export: full lifecycle to Excel
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QComboBox, QDateEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QGroupBox, QHeaderView,
    QFileDialog, QMessageBox, QCompleter, QSplitter, QFrame
)
from PyQt5.QtCore import Qt, QDate, QSortFilterProxyModel, QStringListModel
from PyQt5.QtGui import QFont, QColor

import pandas as pd
from services.lifecycle_service import (
    get_lifecycle_by_serial,
    get_lifecycle_by_container_id,
    get_all_serial_numbers,
)
from services.project_service import (
    get_all_projects,
    get_zones_for_project,
    get_blocks_for_zone,
    get_containers_for_block,
)
from services.report_service import export_to_excel  # reuse existing export helper


# Column display names — maps raw dict keys → friendly headers
LIFECYCLE_COLUMNS = {
    "date":                 "Date",
    "project":              "Project",
    "zone":                 "Zone",
    "block":                "Block",
    "container_num":        "Container #",
    "container_type":       "Type",
    "serial_number":        "Serial Number",
    "material_number":      "Material #",
    "material_description": "Description",
    "quantity":             "Quantity",
    "comment":              "Comment",
    "sap_ticket":           "SAP Ticket",
    "recorded_at":          "Recorded At",
}


class LifecycleView(QWidget):
    """Container Lifecycle Report — full history for a single container."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []         # raw list of dicts from DB
        self._containers = []   # container objects for block dropdown
        self._build_ui()
        self._load_projects()
        self._refresh_serial_autocomplete()

    # ──────────────────────────────────────────────────────────────────────
    # UI BUILD
    # ──────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Title
        title = QLabel("🔍  Container Lifecycle Report")
        title.setFont(QFont("Segoe UI", 14, QFont.Bold))
        layout.addWidget(title)

        subtitle = QLabel("View the complete maintenance history of any container.")
        subtitle.setStyleSheet("color: #666; margin-bottom: 4px;")
        layout.addWidget(subtitle)

        # ── Search panel ────────────────────────────────────────────────
        search_group = QGroupBox("Find Container")
        search_layout = QVBoxLayout()

        # --- Option A: Serial number direct search ---
        serial_row = QHBoxLayout()
        serial_row.addWidget(QLabel("Serial Number:"))

        self.serial_input = QLineEdit()
        self.serial_input.setPlaceholderText("Type serial number (partial search supported)...")
        self.serial_input.setMinimumWidth(280)
        # Autocomplete will be set in _refresh_serial_autocomplete()
        serial_row.addWidget(self.serial_input)

        search_btn = QPushButton("🔍  Search by Serial")
        search_btn.setStyleSheet(
            "QPushButton { background:#1976D2; color:white; border-radius:4px; padding:4px 12px; }"
            "QPushButton:hover { background:#0D47A1; }"
        )
        search_btn.clicked.connect(self._search_by_serial)
        serial_row.addWidget(search_btn)
        serial_row.addStretch()
        search_layout.addLayout(serial_row)

        # Divider label
        or_label = QLabel("— or select by location —")
        or_label.setAlignment(Qt.AlignCenter)
        or_label.setStyleSheet("color: #999; margin: 4px 0;")
        search_layout.addWidget(or_label)

        # --- Option B: Cascading dropdowns ---
        cascade_row = QHBoxLayout()

        self.proj_combo = QComboBox()
        self.proj_combo.setMinimumWidth(180)
        self.proj_combo.currentIndexChanged.connect(self._on_project_changed)
        cascade_row.addWidget(QLabel("Project:"))
        cascade_row.addWidget(self.proj_combo)

        self.zone_combo = QComboBox()
        self.zone_combo.setMinimumWidth(100)
        self.zone_combo.currentIndexChanged.connect(self._on_zone_changed)
        cascade_row.addWidget(QLabel("Zone:"))
        cascade_row.addWidget(self.zone_combo)

        self.block_combo = QComboBox()
        self.block_combo.setMinimumWidth(100)
        self.block_combo.currentIndexChanged.connect(self._on_block_changed)
        cascade_row.addWidget(QLabel("Block:"))
        cascade_row.addWidget(self.block_combo)

        self.container_combo = QComboBox()
        self.container_combo.setMinimumWidth(160)
        cascade_row.addWidget(QLabel("Container:"))
        cascade_row.addWidget(self.container_combo)

        select_btn = QPushButton("▶  Load")
        select_btn.setStyleSheet(
            "QPushButton { background:#388E3C; color:white; border-radius:4px; padding:4px 12px; }"
            "QPushButton:hover { background:#1B5E20; }"
        )
        select_btn.clicked.connect(self._search_by_container)
        cascade_row.addWidget(select_btn)
        cascade_row.addStretch()
        search_layout.addLayout(cascade_row)

        search_group.setLayout(search_layout)
        layout.addWidget(search_group)

        # ── Filter panel ─────────────────────────────────────────────────
        filter_group = QGroupBox("Filters (optional)")
        filter_row = QHBoxLayout()

        filter_row.addWidget(QLabel("From:"))
        self.date_from = QDateEdit()
        self.date_from.setDate(QDate.currentDate().addYears(-5))
        self.date_from.setCalendarPopup(True)
        self.date_from.setDisplayFormat("yyyy-MM-dd")
        filter_row.addWidget(self.date_from)

        filter_row.addWidget(QLabel("To:"))
        self.date_to = QDateEdit()
        self.date_to.setDate(QDate.currentDate())
        self.date_to.setCalendarPopup(True)
        self.date_to.setDisplayFormat("yyyy-MM-dd")
        filter_row.addWidget(self.date_to)

        filter_row.addWidget(QLabel("Material:"))
        self.material_filter = QLineEdit()
        self.material_filter.setPlaceholderText("Any")
        self.material_filter.setMaximumWidth(140)
        filter_row.addWidget(self.material_filter)

        filter_row.addWidget(QLabel("SAP Ticket:"))
        self.sap_filter = QLineEdit()
        self.sap_filter.setPlaceholderText("Any")
        self.sap_filter.setMaximumWidth(120)
        filter_row.addWidget(self.sap_filter)

        filter_row.addStretch()
        filter_group.setLayout(filter_row)
        layout.addWidget(filter_group)

        # ── Results bar ───────────────────────────────────────────────────
        results_bar = QHBoxLayout()
        self.result_label = QLabel("Enter a serial number or select a container to begin.")
        self.result_label.setStyleSheet("color: #555; font-style: italic;")
        results_bar.addWidget(self.result_label)
        results_bar.addStretch()

        self.export_btn = QPushButton("📥  Export to Excel")
        self.export_btn.setEnabled(False)
        self.export_btn.setStyleSheet(
            "QPushButton { background:#F57F17; color:white; border-radius:4px; padding:4px 14px; }"
            "QPushButton:hover { background:#E65100; }"
            "QPushButton:disabled { background:#aaa; }"
        )
        self.export_btn.clicked.connect(self._export_excel)
        results_bar.addWidget(self.export_btn)
        layout.addLayout(results_bar)

        # ── Results table ─────────────────────────────────────────────────
        self.table = QTableWidget()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

    # ──────────────────────────────────────────────────────────────────────
    # DATA LOADING HELPERS
    # ──────────────────────────────────────────────────────────────────────

    def refresh_projects(self):
        """Called by main window when a new project is created."""
        self._load_projects()
        self._refresh_serial_autocomplete()

    def _refresh_serial_autocomplete(self):
        """Sets up autocomplete on the serial number input."""
        serials = get_all_serial_numbers()
        completer = QCompleter(serials, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.serial_input.setCompleter(completer)

    def _load_projects(self):
        self.proj_combo.blockSignals(True)
        self.proj_combo.clear()
        self.proj_combo.addItem("— Project —", userData=None)
        for p in get_all_projects():
            self.proj_combo.addItem(p.name, userData=p.id)
        self.proj_combo.blockSignals(False)
        self._on_project_changed()

    def _on_project_changed(self):
        self.zone_combo.blockSignals(True)
        self.zone_combo.clear()
        self.zone_combo.addItem("— Zone —", userData=None)
        project_id = self.proj_combo.currentData()
        if project_id:
            for z in get_zones_for_project(project_id):
                self.zone_combo.addItem(f"Zone {z}", userData=z)
        self.zone_combo.blockSignals(False)
        self._on_zone_changed()

    def _on_zone_changed(self):
        self.block_combo.blockSignals(True)
        self.block_combo.clear()
        self.block_combo.addItem("— Block —", userData=None)
        project_id = self.proj_combo.currentData()
        zone = self.zone_combo.currentData()
        if project_id and zone:
            for b in get_blocks_for_zone(project_id, zone):
                self.block_combo.addItem(f"Block {b}", userData=b)
        self.block_combo.blockSignals(False)
        self._on_block_changed()

    def _on_block_changed(self):
        self.container_combo.clear()
        self.container_combo.addItem("— Container —", userData=None)
        project_id = self.proj_combo.currentData()
        zone = self.zone_combo.currentData()
        block = self.block_combo.currentData()
        self._containers = []
        if project_id and zone and block:
            self._containers = get_containers_for_block(project_id, zone, block)
            for c in self._containers:
                label = f"C{c.container_index} ({c.container_type})"
                if c.serial_number:
                    label += f" — {c.serial_number}"
                self.container_combo.addItem(label, userData=c.id)

    # ──────────────────────────────────────────────────────────────────────
    # SEARCH ACTIONS
    # ──────────────────────────────────────────────────────────────────────

    def _get_filters(self):
        """Reads filter fields and returns them as a dict."""
        return {
            "date_from":      self.date_from.date().toString("yyyy-MM-dd"),
            "date_to":        self.date_to.date().toString("yyyy-MM-dd"),
            "material_number": self.material_filter.text().strip() or None,
            "sap_ticket":      self.sap_filter.text().strip() or None,
        }

    def _search_by_serial(self):
        serial = self.serial_input.text().strip()
        if not serial:
            QMessageBox.warning(self, "Input needed", "Please enter a serial number.")
            return
        f = self._get_filters()
        rows = get_lifecycle_by_serial(serial, **f)
        self._display_results(rows, f"serial number containing '{serial}'")

    def _search_by_container(self):
        container_id = self.container_combo.currentData()
        if container_id is None:
            QMessageBox.warning(self, "Input needed",
                                "Please select Project → Zone → Block → Container.")
            return
        f = self._get_filters()
        rows = get_lifecycle_by_container_id(container_id, **f)
        self._display_results(rows, "selected container")

    def _display_results(self, rows: list, source_label: str):
        """Populates the table from a list of dicts."""
        self._rows = rows
        self.table.setRowCount(0)

        if not rows:
            self.result_label.setText(
                f"No records found for {source_label}.")
            self.export_btn.setEnabled(False)
            self.table.setColumnCount(0)
            return

        # Set up columns using our friendly names
        col_keys = list(LIFECYCLE_COLUMNS.keys())
        col_headers = list(LIFECYCLE_COLUMNS.values())
        self.table.setColumnCount(len(col_headers))
        self.table.setHorizontalHeaderLabels(col_headers)

        for row_data in rows:
            row_idx = self.table.rowCount()
            self.table.insertRow(row_idx)
            for col_idx, key in enumerate(col_keys):
                value = row_data.get(key, "")
                item = QTableWidgetItem(str(value) if value else "")
                item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row_idx, col_idx, item)

        count = len(rows)
        self.result_label.setText(
            f"Found {count} event{'s' if count != 1 else ''} for {source_label}.")
        self.export_btn.setEnabled(True)

    # ──────────────────────────────────────────────────────────────────────
    # EXPORT
    # ──────────────────────────────────────────────────────────────────────

    def _export_excel(self):
        if not self._rows:
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Lifecycle to Excel",
            "container_lifecycle.xlsx", "Excel Files (*.xlsx)"
        )
        if not file_path:
            return

        # Build DataFrame with friendly column names
        df = pd.DataFrame(self._rows)
        df = df[[k for k in LIFECYCLE_COLUMNS if k in df.columns]]
        df = df.rename(columns=LIFECYCLE_COLUMNS)

        try:
            export_to_excel(df, file_path)
            QMessageBox.information(self, "Exported",
                f"Lifecycle report saved to:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", str(e))
