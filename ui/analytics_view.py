"""
ui/analytics_view.py
---------------------
Analytics Dashboard screen.

Three sections, each with its own table:
  A. Most frequently used materials
  B. Most serviced containers
  C. Most active blocks

Optional bar chart (matplotlib) for monthly activity trend.
Chart is gracefully hidden if matplotlib is not installed.

Export: all analytics tables to a single Excel file (4 sheets).
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QTabWidget,
    QTableWidget, QTableWidgetItem, QGroupBox,
    QHeaderView, QFileDialog, QMessageBox, QSpinBox,
    QSizePolicy
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont

import pandas as pd
from services.analytics_service import (
    get_materials_analytics,
    get_containers_analytics,
    get_blocks_analytics,
    get_activity_by_month,
    export_analytics_to_excel,
)
from services.project_service import get_all_projects

# Try to import matplotlib — it's optional
try:
    import matplotlib
    matplotlib.use("Qt5Agg")                          # use Qt5 backend
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
    from matplotlib.figure import Figure
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False


class AnalyticsView(QWidget):
    """Analytics dashboard with material, container, and block stats."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self._load_projects()

    # ──────────────────────────────────────────────────────────────────────
    # UI BUILD
    # ──────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Title
        title = QLabel("📈  Analytics Dashboard")
        title.setFont(QFont("Segoe UI", 14, QFont.Bold))
        layout.addWidget(title)

        # ── Control bar ───────────────────────────────────────────────────
        ctrl = QHBoxLayout()

        ctrl.addWidget(QLabel("Project:"))
        self.proj_filter = QComboBox()
        self.proj_filter.setMinimumWidth(200)
        ctrl.addWidget(self.proj_filter)

        ctrl.addWidget(QLabel("Top N:"))
        self.top_n_spin = QSpinBox()
        self.top_n_spin.setRange(5, 100)
        self.top_n_spin.setValue(20)
        self.top_n_spin.setToolTip("How many rows to show per table")
        ctrl.addWidget(self.top_n_spin)

        refresh_btn = QPushButton("🔄  Refresh")
        refresh_btn.setStyleSheet(
            "QPushButton { background:#1976D2; color:white; border-radius:4px; padding:4px 14px; }"
            "QPushButton:hover { background:#0D47A1; }"
        )
        refresh_btn.clicked.connect(self._refresh_all)
        ctrl.addWidget(refresh_btn)

        ctrl.addStretch()

        export_btn = QPushButton("📥  Export All to Excel")
        export_btn.setStyleSheet(
            "QPushButton { background:#388E3C; color:white; border-radius:4px; padding:4px 14px; }"
            "QPushButton:hover { background:#1B5E20; }"
        )
        export_btn.clicked.connect(self._export_excel)
        ctrl.addWidget(export_btn)

        layout.addLayout(ctrl)

        # ── Inner tab widget (Materials / Containers / Blocks / Chart) ───
        self.inner_tabs = QTabWidget()
        layout.addWidget(self.inner_tabs)

        # Tab A: Materials
        self.materials_tab = QWidget()
        self.inner_tabs.addTab(self.materials_tab, "📦  Materials")
        self._build_table_tab(
            self.materials_tab,
            "Most frequently used materials",
            attr_name="materials_table"
        )

        # Tab B: Containers
        self.containers_tab = QWidget()
        self.inner_tabs.addTab(self.containers_tab, "🔋  Containers")
        self._build_table_tab(
            self.containers_tab,
            "Most serviced containers",
            attr_name="containers_table"
        )

        # Tab C: Blocks
        self.blocks_tab = QWidget()
        self.inner_tabs.addTab(self.blocks_tab, "🏗️  Blocks")
        self._build_table_tab(
            self.blocks_tab,
            "Most active blocks",
            attr_name="blocks_table"
        )

        # Tab D: Chart (only if matplotlib available)
        if MATPLOTLIB_AVAILABLE:
            self.chart_tab = QWidget()
            self.inner_tabs.addTab(self.chart_tab, "📊  Activity Chart")
            self._build_chart_tab()
        else:
            # Show a friendly "install matplotlib" hint
            hint_tab = QWidget()
            hint_layout = QVBoxLayout(hint_tab)
            hint_layout.addStretch()
            hint = QLabel(
                "📊  Charts are available if you install matplotlib.\n\n"
                "Run:  pip install matplotlib"
            )
            hint.setAlignment(Qt.AlignCenter)
            hint.setStyleSheet("color: #777; font-size: 13px;")
            hint_layout.addWidget(hint)
            hint_layout.addStretch()
            self.inner_tabs.addTab(hint_tab, "📊  Activity Chart")

    def _build_table_tab(self, parent: QWidget, description: str, attr_name: str):
        """Builds a tab with a description label + QTableWidget."""
        layout = QVBoxLayout(parent)

        desc = QLabel(description)
        desc.setStyleSheet("color: #555; margin: 4px 0 8px 0;")
        layout.addWidget(desc)

        table = QTableWidget()
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table)

        # Store reference as instance attribute (e.g. self.materials_table)
        setattr(self, attr_name, table)

    def _build_chart_tab(self):
        """Builds the matplotlib chart tab."""
        layout = QVBoxLayout(self.chart_tab)

        label = QLabel("Monthly maintenance activity (number of log entries)")
        label.setStyleSheet("color: #555; margin-bottom: 6px;")
        layout.addWidget(label)

        # Matplotlib figure embedded in Qt
        self.figure = Figure(figsize=(8, 4), tight_layout=True)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.canvas)

    # ──────────────────────────────────────────────────────────────────────
    # DATA LOADING
    # ──────────────────────────────────────────────────────────────────────

    def refresh_projects(self):
        """Called by main window when a new project is added."""
        self._load_projects()

    def _load_projects(self):
        self.proj_filter.blockSignals(True)
        self.proj_filter.clear()
        self.proj_filter.addItem("All Projects", userData=None)
        for p in get_all_projects():
            self.proj_filter.addItem(p.name, userData=p.name)
        self.proj_filter.blockSignals(False)

    def _refresh_all(self):
        """Loads all analytics data — fetches logs ONCE, reuses for all tables."""
        from services.analytics_service import _fetch_all_logs
        project = self.proj_filter.currentData()
        top_n   = self.top_n_spin.value()

        # Single fetch — passed to all analytics functions
        import pandas as pd
        df = _fetch_all_logs()
        if project and not df.empty:
            df = df[df["project"] == project]

        self._populate_table(self.materials_table,
            get_materials_analytics(project, top_n, _df=df))
        self._populate_table(self.containers_table,
            get_containers_analytics(project, top_n, _df=df))
        self._populate_table(self.blocks_table,
            get_blocks_analytics(project, top_n, _df=df))

        if MATPLOTLIB_AVAILABLE:
            self._draw_chart(get_activity_by_month(project, _df=df))

    def _populate_table(self, table: QTableWidget, df: pd.DataFrame):
        """Fills a QTableWidget from a pandas DataFrame."""
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
            for col_idx, value in enumerate(row):
                item = QTableWidgetItem(str(value) if pd.notna(value) else "")
                item.setTextAlignment(Qt.AlignCenter)
                table.setItem(row_idx, col_idx, item)

    def _draw_chart(self, df: pd.DataFrame):
        """Draws a bar chart of monthly activity using matplotlib."""
        self.figure.clear()
        ax = self.figure.add_subplot(111)

        if df.empty:
            ax.text(0.5, 0.5, "No data available",
                    ha="center", va="center", transform=ax.transAxes,
                    fontsize=12, color="gray")
            self.canvas.draw()
            return

        x = range(len(df))
        bars = ax.bar(x, df["Actions"], color="#1976D2", alpha=0.85)

        # Labels on bars
        for bar, val in zip(bars, df["Actions"]):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.1,
                str(val), ha="center", va="bottom", fontsize=8
            )

        ax.set_xticks(list(x))
        ax.set_xticklabels(df["Month"].tolist(), rotation=45, ha="right", fontsize=8)
        ax.set_ylabel("Number of Actions")
        ax.set_title("Monthly Maintenance Activity")
        ax.yaxis.get_major_locator().set_params(integer=True)
        ax.grid(axis="y", linestyle="--", alpha=0.4)

        self.canvas.draw()

    # ──────────────────────────────────────────────────────────────────────
    # EXPORT
    # ──────────────────────────────────────────────────────────────────────

    def _export_excel(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Analytics to Excel",
            "analytics_report.xlsx", "Excel Files (*.xlsx)"
        )
        if not file_path:
            return

        project = self.proj_filter.currentData()
        try:
            export_analytics_to_excel(file_path, project)
            QMessageBox.information(self, "Exported",
                f"Analytics report saved to:\n{file_path}\n\n"
                "Sheets: Materials, Containers, Blocks, Monthly Activity")
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", str(e))
