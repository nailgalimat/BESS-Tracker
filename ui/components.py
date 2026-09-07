"""
ui/components.py
-----------------
Reusable UI components used across multiple pages.

  - KPICard        : dashboard metric card
  - PageHeader     : consistent title bar for each page
  - FilterBar      : standard horizontal filter row
  - make_table     : creates a pre-styled QTableWidget
  - StatusBadge    : coloured status label
  - PrimaryButton / SecondaryButton / SuccessButton : styled buttons
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QHeaderView,
    QAbstractItemView, QFrame, QSizePolicy
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QColor


# ── KPI Card ──────────────────────────────────────────────────────────────────

class KPICard(QWidget):
    """
    A metric card showing:
      icon + label (top)
      large value  (middle)
      unit + trend (bottom)

    Usage:
        card = KPICard("Active Containers", "148", unit="units",
                       icon="🔋", trend="+3 today", trend_up=True)
    """

    def __init__(self, label: str, value: str, unit: str = "",
                 icon: str = "", trend: str = "",
                 trend_up: bool = True, accent_color: str = "#0071E3",
                 parent=None):
        super().__init__(parent)
        self.setObjectName("KPICard")
        self.setMinimumSize(160, 110)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._build(label, value, unit, icon, trend, trend_up, accent_color)

    def _build(self, label, value, unit, icon, trend, trend_up, accent):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)

        # Top row: icon + label
        top = QHBoxLayout()
        if icon:
            icon_lbl = QLabel(icon)
            icon_lbl.setStyleSheet(f"font-size:18px; color:{accent};")
            icon_lbl.setObjectName("KPICardLabel")
            top.addWidget(icon_lbl)
        lbl = QLabel(label.upper())
        lbl.setObjectName("KPICardLabel")
        lbl.setStyleSheet(
            f"font-size:10px; font-weight:bold; color:#6B7A8D; letter-spacing:0.8px;"
        )
        top.addWidget(lbl)
        top.addStretch()
        layout.addLayout(top)

        # Value row
        val_row = QHBoxLayout()
        val_lbl = QLabel(value)
        val_lbl.setObjectName("KPICardValue")
        val_lbl.setStyleSheet(
            f"font-size:28px; font-weight:bold; color:#1A2B45;"
        )
        val_row.addWidget(val_lbl)
        self._value_label = val_lbl

        if unit:
            unit_lbl = QLabel(unit)
            unit_lbl.setObjectName("KPICardUnit")
            unit_lbl.setStyleSheet("font-size:13px; color:#6B7A8D; padding-top:12px;")
            val_row.addWidget(unit_lbl)
        val_row.addStretch()
        layout.addLayout(val_row)

        # Trend row
        if trend:
            trend_color = "#34C759" if trend_up else "#FF3B30"
            trend_arrow = "▲" if trend_up else "▼"
            trend_lbl = QLabel(f"{trend_arrow} {trend}")
            trend_lbl.setStyleSheet(
                f"font-size:11px; color:{trend_color}; font-weight:bold;"
            )
            layout.addWidget(trend_lbl)
        else:
            layout.addStretch()

        # Left accent border
        self.setStyleSheet(
            f"#KPICard {{ "
            f"background:#FFFFFF; border:1px solid #E0E4EA; border-radius:8px;"
            f"border-left: 4px solid {accent};"
            f"}}"
        )

    def update_value(self, new_value: str):
        """Update the displayed value at runtime."""
        self._value_label.setText(new_value)


# ── Page Header ───────────────────────────────────────────────────────────────

class PageHeader(QWidget):
    """
    Consistent top bar for every page.
    Shows title on the left and optional action buttons on the right.
    """

    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("PageHeader")
        self.setFixedHeight(56)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 0, 16, 0)

        left = QVBoxLayout()
        left.setSpacing(1)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("PageTitle")
        title_lbl.setStyleSheet(
            "font-size:16px; font-weight:bold; color:#1A2B45;"
        )
        left.addWidget(title_lbl)

        if subtitle:
            sub_lbl = QLabel(subtitle)
            sub_lbl.setObjectName("PageSubtitle")
            sub_lbl.setStyleSheet("font-size:11px; color:#6B7A8D;")
            left.addWidget(sub_lbl)

        layout.addLayout(left)
        layout.addStretch()

        # Slot for action buttons — call add_action() after construction
        self._btn_layout = QHBoxLayout()
        self._btn_layout.setSpacing(8)
        layout.addLayout(self._btn_layout)

        self.setStyleSheet("""
            PageHeader, #PageHeader {
                background-color: #FFFFFF;
                border-bottom: 1px solid #E0E4EA;
            }
        """)

    def add_action(self, button: QPushButton):
        """Add an action button to the right side of the header."""
        self._btn_layout.addWidget(button)


# ── Filter Bar ────────────────────────────────────────────────────────────────

class FilterBar(QWidget):
    """
    Horizontal filter panel — wraps controls in a white card.
    Usage:
        bar = FilterBar()
        bar.add_widget(QLabel("Project:"))
        bar.add_widget(my_combo)
        bar.add_stretch()
        bar.add_widget(load_btn)
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("FilterPanel")
        self.setFixedHeight(52)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(14, 8, 14, 8)
        self._layout.setSpacing(10)

    def add_widget(self, widget: QWidget):
        self._layout.addWidget(widget)

    def add_stretch(self):
        self._layout.addStretch()

    def add_spacing(self, px: int):
        self._layout.addSpacing(px)


# ── Standard table factory ────────────────────────────────────────────────────

def make_table(columns: list, hide_id: bool = True) -> QTableWidget:
    """
    Creates a pre-styled QTableWidget ready to use.

    Args:
        columns:  list of column header strings
        hide_id:  if True and first column is "ID", hide it
    """
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels(columns)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setAlternatingRowColors(True)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)
    table.verticalHeader().setVisible(False)
    table.setShowGrid(False)
    table.setFocusPolicy(Qt.NoFocus)

    if hide_id and columns and columns[0].upper() == "ID":
        table.hideColumn(0)

    return table


def fill_table(table: QTableWidget, rows: list, columns: list,
               status_col: str = None, status_colors: dict = None):
    """
    Fills a QTableWidget from a list of dicts.

    Args:
        table:         the QTableWidget to fill
        rows:          list of dicts
        columns:       list of (key, header) tuples OR just header strings
        status_col:    dict key to use for row colour coding
        status_colors: {status_value: hex_color}
    """
    from PyQt5.QtWidgets import QTableWidgetItem
    table.setRowCount(0)

    for row_data in rows:
        row_idx = table.rowCount()
        table.insertRow(row_idx)

        status_val = row_data.get(status_col, "") if status_col else None
        bg = None
        if status_val and status_colors:
            hex_color = status_colors.get(status_val)
            if hex_color:
                bg = QColor(hex_color)

        for col_idx, col in enumerate(columns):
            key = col[0] if isinstance(col, tuple) else col
            val = row_data.get(key, "")
            item = QTableWidgetItem(str(val) if val else "")
            item.setTextAlignment(Qt.AlignCenter)
            if bg:
                item.setBackground(bg)
            table.setItem(row_idx, col_idx, item)


# ── Styled buttons ────────────────────────────────────────────────────────────

def PrimaryButton(text: str, parent=None) -> QPushButton:
    btn = QPushButton(text, parent)
    btn.setObjectName("PrimaryButton")
    btn.setFixedHeight(34)
    return btn


def SecondaryButton(text: str, parent=None) -> QPushButton:
    btn = QPushButton(text, parent)
    btn.setObjectName("SecondaryButton")
    btn.setFixedHeight(34)
    return btn


def SuccessButton(text: str, parent=None) -> QPushButton:
    btn = QPushButton(text, parent)
    btn.setObjectName("SuccessButton")
    btn.setFixedHeight(34)
    return btn


def DangerButton(text: str, parent=None) -> QPushButton:
    btn = QPushButton(text, parent)
    btn.setObjectName("DangerButton")
    btn.setFixedHeight(34)
    return btn


# ── Divider ───────────────────────────────────────────────────────────────────

def HSeparator() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setStyleSheet("color: #E0E4EA;")
    return line
