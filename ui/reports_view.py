"""
ui/reports_view.py
-------------------
Reports screen: filter, preview, edit, delete, export daily logs.

NEW: Right-click any row to Edit or Delete.
     Double-click a row to edit it.
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QComboBox, QDateEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QGroupBox,
    QFileDialog, QMessageBox, QHeaderView,
    QMenu, QAction, QDialog, QDialogButtonBox,
    QLineEdit, QDoubleSpinBox, QTextEdit, QAbstractItemView
)
from PyQt5.QtCore import Qt, QDate
from PyQt5.QtGui import QFont, QColor

from services.project_service import (
    get_all_projects, get_zones_for_project, get_blocks_for_zone
)
from services.report_service import get_report_dataframe, export_to_excel
from services.log_service import (
    delete_log_entry, get_log_entries, update_log_entry
)
import pandas as pd


class ReportsView(QWidget):
    """Reports tab: filter, preview, edit, delete and export daily logs."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dataframe = pd.DataFrame()
        self._raw_rows  = []   # raw dicts from DB — used for edit/delete
        self._build_ui()
        self._load_projects()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(10)

        title = QLabel("📊  Reports & Export")
        title.setFont(QFont("Segoe UI", 14, QFont.Bold))
        main_layout.addWidget(title)

        # ── Filters ───────────────────────────────────────────────────────
        filter_group = QGroupBox("Filters")
        filter_layout = QHBoxLayout()

        left_form = QFormLayout()
        self.proj_filter = QComboBox()
        self.proj_filter.setMinimumWidth(200)
        self.proj_filter.currentIndexChanged.connect(self._on_project_filter_changed)
        left_form.addRow("Project:", self.proj_filter)
        self.zone_filter = QComboBox()
        self.zone_filter.currentIndexChanged.connect(self._on_zone_filter_changed)
        left_form.addRow("Zone:", self.zone_filter)
        self.block_filter = QComboBox()
        left_form.addRow("Block:", self.block_filter)
        filter_layout.addLayout(left_form)
        filter_layout.addSpacing(30)

        right_form = QFormLayout()
        self.date_from = QDateEdit()
        self.date_from.setDate(QDate.currentDate().addMonths(-1))
        self.date_from.setCalendarPopup(True)
        self.date_from.setDisplayFormat("yyyy-MM-dd")
        right_form.addRow("Date From:", self.date_from)
        self.date_to = QDateEdit()
        self.date_to.setDate(QDate.currentDate())
        self.date_to.setCalendarPopup(True)
        self.date_to.setDisplayFormat("yyyy-MM-dd")
        right_form.addRow("Date To:", self.date_to)
        filter_layout.addLayout(right_form)
        filter_layout.addStretch()

        btn_col = QVBoxLayout()
        btn_col.addStretch()
        self.load_btn = QPushButton("🔍  Load Report")
        self.load_btn.setFixedHeight(38)
        self.load_btn.setStyleSheet(
            "QPushButton{background:#1976D2;color:white;font-weight:bold;"
            "border-radius:4px;padding:0 16px;}"
            "QPushButton:hover{background:#0D47A1;}"
        )
        self.load_btn.clicked.connect(self._load_report)
        btn_col.addWidget(self.load_btn)

        self.export_btn = QPushButton("📥  Export to Excel")
        self.export_btn.setFixedHeight(38)
        self.export_btn.setEnabled(False)
        self.export_btn.setStyleSheet(
            "QPushButton{background:#388E3C;color:white;font-weight:bold;"
            "border-radius:4px;padding:0 16px;}"
            "QPushButton:hover{background:#1B5E20;}"
            "QPushButton:disabled{background:#aaa;}"
        )
        self.export_btn.clicked.connect(self._export_excel)
        btn_col.addWidget(self.export_btn)
        filter_layout.addLayout(btn_col)
        filter_group.setLayout(filter_layout)
        main_layout.addWidget(filter_group)

        # ── Info + action buttons ─────────────────────────────────────────
        info_row = QHBoxLayout()
        self.result_label = QLabel(
            "No data loaded. Apply filters and click 'Load Report'."
        )
        self.result_label.setStyleSheet("color:#555;font-style:italic;")
        info_row.addWidget(self.result_label)
        info_row.addStretch()

        hint = QLabel("Right-click a row to Edit or Delete")
        hint.setStyleSheet("color:#888;font-size:11px;font-style:italic;")
        info_row.addWidget(hint)
        main_layout.addLayout(info_row)

        # ── Table ─────────────────────────────────────────────────────────
        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.doubleClicked.connect(self._on_double_click)
        main_layout.addWidget(self.table)

    # ── Projects / filters ────────────────────────────────────────────────

    def refresh_projects(self):
        self._load_projects()

    def _load_projects(self):
        self.proj_filter.blockSignals(True)
        self.proj_filter.clear()
        self.proj_filter.addItem("All Projects", userData=None)
        for p in get_all_projects():
            self.proj_filter.addItem(p.name, userData=p.id)
        self.proj_filter.blockSignals(False)
        self._on_project_filter_changed()

    def _on_project_filter_changed(self):
        self.zone_filter.blockSignals(True)
        self.zone_filter.clear()
        self.zone_filter.addItem("All Zones", userData=None)
        pid = self.proj_filter.currentData()
        if pid:
            for z in get_zones_for_project(pid):
                self.zone_filter.addItem(f"Zone {z}", userData=z)
        self.zone_filter.blockSignals(False)
        self._on_zone_filter_changed()

    def _on_zone_filter_changed(self):
        self.block_filter.clear()
        self.block_filter.addItem("All Blocks", userData=None)
        pid  = self.proj_filter.currentData()
        zone = self.zone_filter.currentData()
        if pid and zone:
            for b in get_blocks_for_zone(pid, zone):
                self.block_filter.addItem(f"Block {b}", userData=b)

    # ── Load / display ────────────────────────────────────────────────────

    def _load_report(self):
        pid       = self.proj_filter.currentData()
        zone      = self.zone_filter.currentData()
        block     = self.block_filter.currentData()
        date_from = self.date_from.date().toString("yyyy-MM-dd")
        date_to   = self.date_to.date().toString("yyyy-MM-dd")

        # Also fetch raw rows (with IDs) for edit/delete
        self._raw_rows = get_log_entries(
            project_id=pid, date_from=date_from, date_to=date_to,
            zone_number=zone, block_number=block,
        )

        self._dataframe = get_report_dataframe(
            project_id=pid, date_from=date_from, date_to=date_to,
            zone_number=zone, block_number=block,
        )

        self._populate_table(self._dataframe)
        count = len(self._dataframe)
        self.result_label.setText(
            f"Found {count} record{'s' if count != 1 else ''}."
        )
        self.export_btn.setEnabled(count > 0)

    def _populate_table(self, df: pd.DataFrame):
        self.table.setRowCount(0)
        if df.empty:
            self.table.setColumnCount(0)
            return
        self.table.setColumnCount(len(df.columns))
        self.table.setHorizontalHeaderLabels(list(df.columns))
        for row_idx, row in df.iterrows():
            self.table.insertRow(self.table.rowCount())
            for col_idx, value in enumerate(row):
                item = QTableWidgetItem(str(value) if pd.notna(value) else "")
                item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(self.table.rowCount() - 1, col_idx, item)

    # ── Context menu (right-click) ────────────────────────────────────────

    def _show_context_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0 or row >= len(self._raw_rows):
            return
        menu = QMenu(self)
        edit_act   = menu.addAction("✏️  Edit this entry")
        delete_act = menu.addAction("🗑  Delete this entry")
        action = menu.exec_(self.table.viewport().mapToGlobal(pos))
        if action == edit_act:
            self._edit_row(row)
        elif action == delete_act:
            self._delete_row(row)

    def _on_double_click(self, index):
        self._edit_row(index.row())

    def _get_log_id(self, row: int) -> int:
        """Gets the DB id from _raw_rows for the given table row."""
        if 0 <= row < len(self._raw_rows):
            return self._raw_rows[row].get("id", -1)
        return -1

    # ── Edit ──────────────────────────────────────────────────────────────

    def _edit_row(self, row: int):
        log_id = self._get_log_id(row)
        if log_id < 0:
            return
        raw = self._raw_rows[row]
        dlg = EditLogDialog(raw, parent=self)
        if dlg.exec_() == QDialog.Accepted:
            data = dlg.get_data()
            try:
                # Also re-balances warehouse stock if material/qty changed
                update_log_entry(log_id, data)
                self._load_report()   # refresh table
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to update:\n{e}")

    # ── Delete ────────────────────────────────────────────────────────────

    def _delete_row(self, row: int):
        log_id = self._get_log_id(row)
        if log_id < 0:
            return
        raw = self._raw_rows[row]
        stock_note = ""
        if raw.get("warehouse_id"):
            stock_note = (f"The quantity will be returned to stock at "
                          f"'{raw.get('warehouse_name','')}'.\n\n")
        ans = QMessageBox.question(
            self, "Delete Entry?",
            f"Delete log entry #{log_id}?\n\n"
            f"Date: {raw.get('date','')}\n"
            f"Material: {raw.get('material_number','')}\n"
            f"Qty: {raw.get('quantity','')}\n\n"
            f"{stock_note}"
            "This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if ans == QMessageBox.Yes:
            try:
                delete_log_entry(log_id)
                self._load_report()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to delete:\n{e}")

    # ── Export ────────────────────────────────────────────────────────────

    def _export_excel(self):
        if self._dataframe.empty:
            QMessageBox.warning(self, "No Data", "Load the report first.")
            return
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Report to Excel",
            "maintenance_report.xlsx", "Excel Files (*.xlsx)"
        )
        if not file_path:
            return
        try:
            export_to_excel(self._dataframe, file_path)
            QMessageBox.information(self, "Export Successful",
                f"Report exported to:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", str(e))


# ── Edit dialog ───────────────────────────────────────────────────────────────

class EditLogDialog(QDialog):
    """Dialog to edit a single daily log entry."""

    def __init__(self, entry: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Edit Log Entry #{entry.get('id','')}")
        self.setMinimumWidth(440)
        self._build_ui(entry)

    def _build_ui(self, entry):
        layout = QVBoxLayout(self)
        form   = QFormLayout()
        form.setSpacing(10)

        # Read-only info
        info = QLabel(
            f"Project: {entry.get('project_name','')}   |   "
            f"Z{entry.get('zone_number','')} / "
            f"B{entry.get('block_number','')} / "
            f"C{entry.get('container_index','')}   |   "
            f"Serial: {entry.get('serial_number','')}"
        )
        info.setStyleSheet(
            "color:#1565C0;font-size:11px;background:#E3F2FD;"
            "padding:6px;border-radius:4px;"
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        # Editable fields
        self.date_edit = QDateEdit()
        self.date_edit.setDate(
            QDate.fromString(entry.get("date",""), "yyyy-MM-dd")
        )
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        form.addRow("Date *:", self.date_edit)

        self.mat_input = QLineEdit(str(entry.get("material_number","")))
        form.addRow("Material Number *:", self.mat_input)

        self.qty_spin = QDoubleSpinBox()
        self.qty_spin.setRange(0.01, 99999)
        self.qty_spin.setDecimals(2)
        self.qty_spin.setValue(float(entry.get("quantity", 1)))
        form.addRow("Quantity *:", self.qty_spin)

        self.sap_input = QLineEdit(str(entry.get("sap_ticket","") or ""))
        self.sap_input.setPlaceholderText("Optional")
        form.addRow("SAP Ticket:", self.sap_input)

        self.comment_input = QTextEdit()
        self.comment_input.setMaximumHeight(80)
        self.comment_input.setPlainText(str(entry.get("comment","") or ""))
        form.addRow("Comment:", self.comment_input)

        layout.addLayout(form)

        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._validate)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _validate(self):
        if not self.mat_input.text().strip():
            QMessageBox.warning(self,"Required","Material number is required.")
            return
        self.accept()

    def get_data(self) -> dict:
        return {
            "date":            self.date_edit.date().toString("yyyy-MM-dd"),
            "material_number": self.mat_input.text().strip().upper(),
            "quantity":        self.qty_spin.value(),
            "sap_ticket":      self.sap_input.text().strip(),
            "comment":         self.comment_input.toPlainText().strip(),
        }
