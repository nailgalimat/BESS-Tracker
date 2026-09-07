"""
ui/work_log_report.py
----------------------
Work Summary Report screen.

Shows three sub-tables:
  1. Full work log list (filtered)
  2. Status breakdown (Fixed / Monitoring / etc)
  3. Most frequently serviced containers (top issues)

Export to Excel (3 sheets).
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QComboBox, QDateEdit, QPushButton, QTabWidget,
    QTableWidget, QTableWidgetItem, QGroupBox,
    QHeaderView, QFileDialog, QMessageBox,
    QAbstractItemView
)
from PyQt5.QtCore import Qt, QDate
from PyQt5.QtGui import QFont, QColor
import pandas as pd

from services.project_service import get_all_projects
from services.work_log_service import (
    get_work_logs_dataframe, get_status_summary,
    get_most_frequent_faults, export_work_logs_excel,
    STATUS_COLORS,
)


class WorkLogReport(QWidget):
    """Work Summary Report — filter, view, export."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self._load_projects()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        title = QLabel("📋  Work Summary Report")
        title.setFont(QFont("Segoe UI", 14, QFont.Bold))
        layout.addWidget(title)

        # ── Filter bar ────────────────────────────────────────────────────
        filter_group = QGroupBox("Filters")
        filter_row = QHBoxLayout()

        filter_row.addWidget(QLabel("Project:"))
        self.proj_filter = QComboBox()
        self.proj_filter.setMinimumWidth(200)
        filter_row.addWidget(self.proj_filter)

        filter_row.addWidget(QLabel("From:"))
        self.date_from = QDateEdit()
        self.date_from.setDate(QDate.currentDate().addMonths(-1))
        self.date_from.setCalendarPopup(True)
        self.date_from.setDisplayFormat("yyyy-MM-dd")
        filter_row.addWidget(self.date_from)

        filter_row.addWidget(QLabel("To:"))
        self.date_to = QDateEdit()
        self.date_to.setDate(QDate.currentDate())
        self.date_to.setCalendarPopup(True)
        self.date_to.setDisplayFormat("yyyy-MM-dd")
        filter_row.addWidget(self.date_to)

        filter_row.addStretch()

        load_btn = QPushButton("🔍  Load Report")
        load_btn.setStyleSheet(
            "QPushButton{background:#1976D2;color:white;font-weight:bold;"
            "border-radius:4px;padding:4px 14px;}"
        )
        load_btn.clicked.connect(self._load_report)
        filter_row.addWidget(load_btn)

        export_btn = QPushButton("📥  Export to Excel")
        export_btn.setStyleSheet(
            "QPushButton{background:#388E3C;color:white;font-weight:bold;"
            "border-radius:4px;padding:4px 14px;}"
        )
        export_btn.clicked.connect(self._export_excel)
        filter_row.addWidget(export_btn)

        filter_group.setLayout(filter_row)
        layout.addWidget(filter_group)

        self.result_label = QLabel("Apply filters and click Load Report.")
        self.result_label.setStyleSheet("color:#555;font-style:italic;")
        layout.addWidget(self.result_label)

        # ── Inner tabs ────────────────────────────────────────────────────
        self.inner_tabs = QTabWidget()

        # Tab 1: Full log
        self.log_tab = QWidget()
        self.inner_tabs.addTab(self.log_tab, "📄  All Work Logs")
        self._build_table_tab(self.log_tab, "log_table")

        # Tab 2: Status summary
        self.status_tab = QWidget()
        self.inner_tabs.addTab(self.status_tab, "📊  Status Summary")
        self._build_table_tab(self.status_tab, "status_table")

        # Tab 3: Top issues
        self.issues_tab = QWidget()
        self.inner_tabs.addTab(self.issues_tab, "⚠️  Top Issues")
        self._build_table_tab(self.issues_tab, "issues_table")

        layout.addWidget(self.inner_tabs)

    def _build_table_tab(self, parent: QWidget, attr_name: str):
        layout = QVBoxLayout(parent)
        table = QTableWidget()
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table)
        setattr(self, attr_name, table)

    # ── Data ──────────────────────────────────────────────────────────────

    def refresh_projects(self):
        self._load_projects()

    def _load_projects(self):
        self.proj_filter.blockSignals(True)
        self.proj_filter.clear()
        self.proj_filter.addItem("All Projects", userData=None)
        for p in get_all_projects():
            self.proj_filter.addItem(p.name, userData=p.id)
        self.proj_filter.blockSignals(False)

    def _load_report(self):
        pid       = self.proj_filter.currentData()
        date_from = self.date_from.date().toString("yyyy-MM-dd")
        date_to   = self.date_to.date().toString("yyyy-MM-dd")

        logs_df   = get_work_logs_dataframe(pid, date_from, date_to)
        status_df = get_status_summary(pid, date_from, date_to)
        issues_df = get_most_frequent_faults(pid, date_from, date_to)

        self._fill_table(self.log_table, logs_df, color_col="Status")
        self._fill_table(self.status_table, status_df)
        self._fill_table(self.issues_table, issues_df, color_col="Last Status")

        count = len(logs_df)
        self.result_label.setText(
            f"{count} work log entr{'ies' if count!=1 else 'y'} found."
        )

    def _fill_table(self, table: QTableWidget, df: pd.DataFrame,
                    color_col: str = None):
        table.setRowCount(0)
        if df.empty:
            table.setColumnCount(1)
            table.setHorizontalHeaderLabels(["No data"])
            return

        table.setColumnCount(len(df.columns))
        table.setHorizontalHeaderLabels(list(df.columns))

        for _, row in df.iterrows():
            row_idx = table.rowCount()
            table.insertRow(row_idx)
            status_val = str(row.get(color_col, "")) if color_col else None
            bg = QColor(STATUS_COLORS.get(status_val, "#FFFFFF")) if status_val else None

            for col_idx, value in enumerate(row):
                item = QTableWidgetItem(str(value) if pd.notna(value) else "")
                item.setTextAlignment(Qt.AlignCenter)
                if bg:
                    item.setBackground(bg)
                table.setItem(row_idx, col_idx, item)

    def _export_excel(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Work Report", "work_summary_report.xlsx",
            "Excel Files (*.xlsx)"
        )
        if not file_path:
            return
        pid = self.proj_filter.currentData()
        try:
            export_work_logs_excel(
                file_path, project_id=pid,
                date_from=self.date_from.date().toString("yyyy-MM-dd"),
                date_to=self.date_to.date().toString("yyyy-MM-dd"),
            )
            QMessageBox.information(self, "Exported",
                f"Work report saved to:\n{file_path}\n\n"
                "Sheets: Work Logs / Status Summary / Top Issues")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
