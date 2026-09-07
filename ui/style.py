"""
ui/style.py
------------
Central QSS stylesheet for the entire application.
Industrial light theme — inspired by Huawei FusionSolar / Sungrow portals.

To modify colours, change the variables at the top of APP_STYLE.
All other rules reference those colours by value — use Find & Replace if needed.

COLOUR PALETTE:
  Background:   #F0F2F5  (page background, light grey)
  Surface:      #FFFFFF  (cards, panels)
  Sidebar:      #1A2B45  (dark navy)
  Sidebar text: #8FA3BE  (muted)
  Accent:       #0071E3  (active/selected, blue)
  Border:       #E0E4EA
  Text primary: #1A2B45
  Text muted:   #6B7A8D
  Success:      #34C759  (Fixed/OK)
  Warning:      #FF9500  (Monitoring)
  Danger:       #FF3B30  (Not resolved/Fault)
  Escalated:    #AF52DE  (purple)
"""

APP_STYLE = """

/* ── Global ─────────────────────────────────────────────────────────────── */
QWidget {
    font-family: "Segoe UI", "Inter", "Arial", sans-serif;
    font-size: 13px;
    color: #1A2B45;
    background-color: #F0F2F5;
}

QMainWindow {
    background-color: #F0F2F5;
}

/* ── Sidebar ─────────────────────────────────────────────────────────────── */
#Sidebar {
    background-color: #1A2B45;
    border-right: 1px solid #142038;
    min-width: 210px;
    max-width: 210px;
}

#SidebarLogo {
    background-color: #142038;
    color: #FFFFFF;
    font-size: 14px;
    font-weight: bold;
    padding: 18px 16px;
    border-bottom: 1px solid #0F1E30;
}

#SidebarVersion {
    color: #4A6080;
    font-size: 10px;
    padding: 0 16px 12px 16px;
    background-color: #142038;
}

#NavButton {
    background-color: transparent;
    color: #8FA3BE;
    text-align: left;
    padding: 11px 16px 11px 20px;
    border: none;
    border-left: 3px solid transparent;
    font-size: 13px;
    border-radius: 0px;
}

#NavButton:hover {
    background-color: #243D5C;
    color: #FFFFFF;
    border-left: 3px solid #4A7FC1;
}

#NavButton[active="true"] {
    background-color: #1E3A5F;
    color: #FFFFFF;
    border-left: 3px solid #0071E3;
    font-weight: bold;
}

#SidebarSection {
    color: #4A6080;
    font-size: 10px;
    font-weight: bold;
    padding: 14px 16px 4px 20px;
    background-color: #1A2B45;
    letter-spacing: 1px;
}

/* ── Page area ───────────────────────────────────────────────────────────── */
#PageArea {
    background-color: #F0F2F5;
}

#PageHeader {
    background-color: #FFFFFF;
    border-bottom: 1px solid #E0E4EA;
    padding: 0px;
    min-height: 52px;
    max-height: 52px;
}

#PageTitle {
    font-size: 17px;
    font-weight: bold;
    color: #1A2B45;
    padding: 0 24px;
}

#PageSubtitle {
    font-size: 12px;
    color: #6B7A8D;
    padding: 0 24px;
}

/* ── Cards ───────────────────────────────────────────────────────────────── */
#KPICard {
    background-color: #FFFFFF;
    border: 1px solid #E0E4EA;
    border-radius: 8px;
    padding: 16px;
}

#KPICardValue {
    font-size: 26px;
    font-weight: bold;
    color: #1A2B45;
}

#KPICardLabel {
    font-size: 11px;
    color: #6B7A8D;
    font-weight: normal;
}

#KPICardUnit {
    font-size: 12px;
    color: #6B7A8D;
}

#KPICardTrend {
    font-size: 11px;
}

#Card {
    background-color: #FFFFFF;
    border: 1px solid #E0E4EA;
    border-radius: 8px;
}

#CardTitle {
    font-size: 13px;
    font-weight: bold;
    color: #1A2B45;
    padding: 14px 16px 4px 16px;
    border-bottom: 1px solid #F0F2F5;
}

/* ── Toolbar (top of each page) ──────────────────────────────────────────── */
#ToolBar {
    background-color: #FFFFFF;
    border-bottom: 1px solid #E0E4EA;
    padding: 8px 16px;
    spacing: 8px;
}

/* ── Buttons ─────────────────────────────────────────────────────────────── */
#PrimaryButton {
    background-color: #0071E3;
    color: #FFFFFF;
    border: none;
    border-radius: 5px;
    padding: 7px 18px;
    font-weight: bold;
    font-size: 12px;
}
#PrimaryButton:hover  { background-color: #005BBD; }
#PrimaryButton:pressed{ background-color: #004A99; }

#SecondaryButton {
    background-color: #FFFFFF;
    color: #1A2B45;
    border: 1px solid #C8CDD6;
    border-radius: 5px;
    padding: 7px 18px;
    font-size: 12px;
}
#SecondaryButton:hover  { background-color: #F5F7FA; border-color: #A0A8B5; }

#SuccessButton {
    background-color: #34C759;
    color: #FFFFFF;
    border: none;
    border-radius: 5px;
    padding: 7px 18px;
    font-weight: bold;
    font-size: 12px;
}
#SuccessButton:hover { background-color: #28A745; }

#DangerButton {
    background-color: #FF3B30;
    color: #FFFFFF;
    border: none;
    border-radius: 5px;
    padding: 7px 18px;
    font-size: 12px;
}
#DangerButton:hover { background-color: #CC2F26; }

#WarningButton {
    background-color: #FF9500;
    color: #FFFFFF;
    border: none;
    border-radius: 5px;
    padding: 7px 18px;
    font-size: 12px;
}
#WarningButton:hover { background-color: #CC7700; }

/* ── Filter Panel ────────────────────────────────────────────────────────── */
#FilterPanel {
    background-color: #FFFFFF;
    border: 1px solid #E0E4EA;
    border-radius: 6px;
    padding: 10px 14px;
}

#FilterLabel {
    font-size: 11px;
    color: #6B7A8D;
    font-weight: bold;
}

/* ── Input fields ────────────────────────────────────────────────────────── */
QLineEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QDateEdit {
    background-color: #FFFFFF;
    border: 1px solid #C8CDD6;
    border-radius: 4px;
    padding: 5px 8px;
    color: #1A2B45;
    selection-background-color: #0071E3;
}
QLineEdit:focus, QTextEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QDateEdit:focus {
    border: 1px solid #0071E3;
    outline: none;
}

QComboBox {
    background-color: #FFFFFF;
    border: 1px solid #C8CDD6;
    border-radius: 4px;
    padding: 5px 8px;
    color: #1A2B45;
}
QComboBox:focus { border: 1px solid #0071E3; }
QComboBox::drop-down {
    border: none;
    width: 20px;
}
QComboBox QAbstractItemView {
    background-color: #FFFFFF;
    border: 1px solid #C8CDD6;
    selection-background-color: #E8F0FD;
    selection-color: #1A2B45;
}

/* ── Tables ──────────────────────────────────────────────────────────────── */
QTableWidget {
    background-color: #FFFFFF;
    border: 1px solid #E0E4EA;
    border-radius: 6px;
    gridline-color: #F0F2F5;
    selection-background-color: #E8F0FD;
    selection-color: #1A2B45;
    alternate-background-color: #FAFBFC;
}
QTableWidget::item {
    padding: 6px 8px;
    border: none;
}
QTableWidget::item:selected {
    background-color: #E8F0FD;
    color: #1A2B45;
}
QHeaderView::section {
    background-color: #F5F7FA;
    color: #6B7A8D;
    font-weight: bold;
    font-size: 11px;
    padding: 7px 8px;
    border: none;
    border-bottom: 2px solid #E0E4EA;
    border-right: 1px solid #E8EAF0;
    letter-spacing: 0.5px;
}
QHeaderView::section:first { border-top-left-radius: 6px; }
QHeaderView { background-color: #F5F7FA; }

/* ── Tab Widget (inner tabs only, not main nav) ──────────────────────────── */
QTabWidget::pane {
    border: 1px solid #E0E4EA;
    border-radius: 0 6px 6px 6px;
    background: #FFFFFF;
}
QTabBar::tab {
    background: #F0F2F5;
    color: #6B7A8D;
    padding: 8px 18px;
    border: 1px solid #E0E4EA;
    border-bottom: none;
    border-radius: 5px 5px 0 0;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background: #FFFFFF;
    color: #0071E3;
    font-weight: bold;
    border-top: 2px solid #0071E3;
}
QTabBar::tab:hover:!selected { background: #E8EDF5; }

/* ── Group boxes ─────────────────────────────────────────────────────────── */
QGroupBox {
    background-color: #FFFFFF;
    border: 1px solid #E0E4EA;
    border-radius: 6px;
    margin-top: 8px;
    padding-top: 10px;
    font-weight: bold;
    font-size: 12px;
    color: #6B7A8D;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 6px;
    left: 12px;
    color: #6B7A8D;
}

/* ── Scroll bars ─────────────────────────────────────────────────────────── */
QScrollBar:vertical {
    background: #F0F2F5;
    width: 8px;
    border-radius: 4px;
}
QScrollBar::handle:vertical {
    background: #C8CDD6;
    border-radius: 4px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover { background: #A0A8B5; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }

QScrollBar:horizontal {
    background: #F0F2F5;
    height: 8px;
    border-radius: 4px;
}
QScrollBar::handle:horizontal {
    background: #C8CDD6;
    border-radius: 4px;
    min-width: 30px;
}
QScrollBar::handle:horizontal:hover { background: #A0A8B5; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

/* ── Status badges ───────────────────────────────────────────────────────── */
#BadgeFixed       { color: #34C759; font-weight: bold; font-size: 11px; }
#BadgeMonitoring  { color: #FF9500; font-weight: bold; font-size: 11px; }
#BadgeUnresolved  { color: #FF3B30; font-weight: bold; font-size: 11px; }
#BadgeEscalated   { color: #AF52DE; font-weight: bold; font-size: 11px; }

/* ── Splitter ────────────────────────────────────────────────────────────── */
QSplitter::handle {
    background-color: #E0E4EA;
    width: 1px;
}

/* ── Message boxes ───────────────────────────────────────────────────────── */
QMessageBox {
    background-color: #FFFFFF;
}
QMessageBox QPushButton {
    background-color: #0071E3;
    color: white;
    border-radius: 4px;
    padding: 6px 18px;
    font-size: 12px;
}
QMessageBox QPushButton:hover { background-color: #005BBD; }

/* ── Dialog ──────────────────────────────────────────────────────────────── */
QDialog {
    background-color: #F0F2F5;
}

"""

# Status colour map — used programmatically in table rows
STATUS_ROW_COLORS = {
    "Fixed":        "#F0FBF4",
    "Monitoring":   "#FFFBF0",
    "Not resolved": "#FFF5F5",
    "Escalated":    "#F8F0FF",
}

STATUS_TEXT_COLORS = {
    "Fixed":        "#1A7A3A",
    "Monitoring":   "#8A5A00",
    "Not resolved": "#CC1A1A",
    "Escalated":    "#7B2A9E",
}
