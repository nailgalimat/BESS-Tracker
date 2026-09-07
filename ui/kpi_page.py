"""
ui/kpi_page.py
---------------
KPI Dashboard: MTTR, MTBF, Status Overview per container type.
Optional bar charts if matplotlib is available.
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QTabWidget,
    QTableWidget, QTableWidgetItem, QGroupBox,
    QHeaderView, QAbstractItemView, QFileDialog,
    QMessageBox, QSizePolicy, QFrame
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
import pandas as pd

from ui.components import PageHeader, PrimaryButton, SecondaryButton, make_table
from services.kpi_service import get_full_kpi_summary, export_kpi_excel
from services.project_service import get_all_projects

try:
    import matplotlib
    matplotlib.use("Qt5Agg")
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
    from matplotlib.figure import Figure
    MATPLOTLIB = True
except ImportError:
    MATPLOTLIB = False


class KpiPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self._load_projects()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = PageHeader("KPI Dashboard",
                            "MTTR · MTBF · Resolution rate per container type")
        layout.addWidget(header)

        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(24, 16, 24, 24)
        cl.setSpacing(12)

        # Filter bar
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Project:"))
        self.proj_filter = QComboBox()
        self.proj_filter.setMinimumWidth(220)
        filter_row.addWidget(self.proj_filter)
        filter_row.addStretch()

        calc_btn = PrimaryButton("📊  Calculate KPIs")
        calc_btn.clicked.connect(self._calculate)
        filter_row.addWidget(calc_btn)

        export_btn = SecondaryButton("📥  Export Excel")
        export_btn.clicked.connect(self._export)
        filter_row.addWidget(export_btn)
        cl.addLayout(filter_row)

        self.info_label = QLabel(
            "Select a project (or All) and click Calculate KPIs."
        )
        self.info_label.setStyleSheet("color:#555;font-style:italic;")
        cl.addWidget(self.info_label)

        # Inner tabs
        self.inner_tabs = QTabWidget()

        # Overview tab
        self.ov_tab = QWidget()
        self.inner_tabs.addTab(self.ov_tab, "📋  Status Overview")
        self._build_table_tab(self.ov_tab, "ov_table")

        # MTTR tab
        self.mttr_tab = QWidget()
        self.inner_tabs.addTab(self.mttr_tab, "⏱  MTTR")
        self._build_table_tab(self.mttr_tab, "mttr_table",
            info="Mean Time To Repair: average days from fault logged to Fixed status.")

        # MTBF tab
        self.mtbf_tab = QWidget()
        self.inner_tabs.addTab(self.mtbf_tab, "🔁  MTBF")
        self._build_table_tab(self.mtbf_tab, "mtbf_table",
            info="Mean Time Between Failures: average days between fault events per container type.")

        # Chart tab
        if MATPLOTLIB:
            self.chart_tab = QWidget()
            self.inner_tabs.addTab(self.chart_tab, "📊  Charts")
            self._build_chart_tab()
        else:
            hint = QWidget()
            hl = QVBoxLayout(hint)
            hl.addStretch()
            h = QLabel("Install matplotlib for charts:\n\npip install matplotlib")
            h.setAlignment(Qt.AlignCenter)
            h.setStyleSheet("color:#777;font-size:13px;")
            hl.addWidget(h)
            hl.addStretch()
            self.inner_tabs.addTab(hint, "📊  Charts")

        cl.addWidget(self.inner_tabs)
        layout.addWidget(content)

    def _build_table_tab(self, parent, attr_name, info=""):
        tl = QVBoxLayout(parent)
        tl.setContentsMargins(8, 8, 8, 8)
        if info:
            lbl = QLabel(info)
            lbl.setStyleSheet("color:#555;font-size:11px;margin-bottom:4px;")
            lbl.setWordWrap(True)
            tl.addWidget(lbl)
        table = make_table([])
        tl.addWidget(table)
        setattr(self, attr_name, table)

    def _build_chart_tab(self):
        layout = QVBoxLayout(self.chart_tab)
        layout.setContentsMargins(8, 8, 8, 8)
        self.figure = Figure(figsize=(10, 7), tight_layout=True)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.canvas)

    def _load_projects(self):
        self.proj_filter.clear()
        self.proj_filter.addItem("All Projects", userData=None)
        for p in get_all_projects():
            self.proj_filter.addItem(p.name, userData=p.id)

    def refresh_projects(self):
        self._load_projects()

    # ── Calculate ─────────────────────────────────────────────────────────

    def _calculate(self):
        pid  = self.proj_filter.currentData()
        kpis = get_full_kpi_summary(pid)

        self._fill_table(self.ov_table,   kpis["overview"])
        self._fill_table(self.mttr_table, kpis["mttr"])
        self._fill_table(self.mtbf_table, kpis["mtbf"])

        if MATPLOTLIB:
            self._draw_charts(kpis)

        total_events = kpis["overview"]["Total Events"].sum() if not kpis["overview"].empty else 0
        self.info_label.setText(
            f"Calculated from {total_events} work log events."
        )

    def _fill_table(self, table: QTableWidget, df: pd.DataFrame):
        table.setRowCount(0)
        if df.empty:
            table.setColumnCount(1)
            table.setHorizontalHeaderLabels(["No data — add work logs first"])
            return
        table.setColumnCount(len(df.columns))
        table.setHorizontalHeaderLabels(list(df.columns))
        for _, row in df.iterrows():
            row_idx = table.rowCount()
            table.insertRow(row_idx)
            for col_idx, val in enumerate(row):
                item = QTableWidgetItem(str(val) if pd.notna(val) else "")
                item.setTextAlignment(Qt.AlignCenter)
                table.setItem(row_idx, col_idx, item)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)

    def _draw_charts(self, kpis: dict):
        self.figure.clear()

        mttr_df = kpis["mttr"]
        mtbf_df = kpis["mtbf"]
        ov_df   = kpis["overview"]

        axes = self.figure.subplots(1, 3)

        def bar(ax, df, x_col, y_col, title, color, ylabel):
            if df.empty:
                ax.text(0.5, 0.5, "No data", ha="center", va="center",
                        transform=ax.transAxes, color="#999")
            else:
                ax.bar(range(len(df)), df[y_col], color=color, alpha=0.85)
                ax.set_xticks(range(len(df)))
                ax.set_xticklabels(df[x_col].tolist(),
                                    rotation=30, ha="right", fontsize=8)
                ax.set_ylabel(ylabel, fontsize=8)
                for i, v in enumerate(df[y_col]):
                    ax.text(i, v + 0.1, f"{v}", ha="center", fontsize=7)
            ax.set_title(title, fontsize=9, fontweight="bold", color="#1A2B45")
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            ax.grid(axis="y", linestyle="--", alpha=0.3)

        bar(axes[0], mttr_df, "Container Type", "Avg MTTR (days)",
            "Avg MTTR (days)", "#0071E3", "Days")
        bar(axes[1], mtbf_df, "Container Type", "Avg MTBF (days)",
            "Avg MTBF (days)", "#34C759", "Days")

        # Resolution rate pie
        if not ov_df.empty and "Resolution Rate (%)" in ov_df.columns:
            axes[2].bar(range(len(ov_df)), ov_df["Resolution Rate (%)"],
                        color="#FF9500", alpha=0.85)
            axes[2].set_xticks(range(len(ov_df)))
            axes[2].set_xticklabels(ov_df["Container Type"].tolist(),
                                     rotation=30, ha="right", fontsize=8)
            axes[2].set_ylabel("Resolution Rate (%)", fontsize=8)
            axes[2].set_ylim(0, 110)
            axes[2].set_title("Resolution Rate (%)", fontsize=9,
                               fontweight="bold", color="#1A2B45")
            axes[2].spines["top"].set_visible(False)
            axes[2].spines["right"].set_visible(False)
            axes[2].grid(axis="y", linestyle="--", alpha=0.3)
        else:
            axes[2].text(0.5, 0.5, "No data", ha="center", va="center",
                          transform=axes[2].transAxes, color="#999")

        self.canvas.draw()

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export KPI Report", "kpi_report.xlsx",
            "Excel Files (*.xlsx)")
        if path:
            try:
                export_kpi_excel(path, self.proj_filter.currentData())
                QMessageBox.information(self, "Exported", f"Saved to:\n{path}")
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))
