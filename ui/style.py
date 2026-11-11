"""
ui/style.py
------------
Central QSS stylesheet for the entire application.

One set of colours, defined once in TOKENS below and substituted into the
stylesheet. Neutrals carry the layout; the primary action is ink-filled (no
blue lead); state colours (ok / warn / crit) are dark enough to read as text
and are always paired with a word.

Pages should take colours from TOKENS (`from ui.style import TOKENS as T`)
rather than writing new hex values.
"""

TOKENS = {
    # neutrals
    'ink':      '#17212E',   # text, primary button, selected edge
    'ink2':     '#3A4757',   # secondary text
    'muted':    '#5B6776',   # hints, captions
    'line':     '#DDE2E8',   # dividers, card borders
    'line2':    '#C3CBD5',   # input borders
    'canvas':   '#F4F6F8',   # page background
    'surface':  '#FFFFFF',   # cards, tables, inputs
    'sunken':   '#EEF1F4',   # table header, selected row
    'chrome':   '#1C2633',   # sidebar
    'chrome2':  '#8E9AAB',   # text on the sidebar
    'chrome3':  '#141C27',   # sidebar, darker band
    'chromeHi': '#2A3646',   # sidebar hover / active
    # state — colour is always paired with a word
    'ok':   '#1E6B3A', 'okBg':   '#E8F4EC', 'okBd':   '#B9DCC5',
    'warn': '#7A5200', 'warnBg': '#FFF5DC', 'warnBd': '#EDD49A',
    'crit': '#A61B1B', 'critBg': '#FDECEC', 'critBd': '#F1C2C2',
    'wait': '#5B3A8C', 'waitBg': '#F3EEFA', 'waitBd': '#D6C8EC',
}

_QSS = """
/* ── Global ─────────────────────────────────────────────────────────────── */
QWidget {
    font-family: "Segoe UI", "Inter", "Arial", sans-serif;
    font-size: 13px;
    color: {ink};
}
QMainWindow, QDialog, QStackedWidget, #PageArea { background-color: {canvas}; }
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollArea > #qt_scrollarea_viewport { background: transparent; }
QLabel { background: transparent; }
QMenu { background-color: #FFFFFF; border: 1px solid {line2}; padding: 4px 0; }
QMenu::item { padding: 6px 24px; }
QMenu::item:selected { background-color: {sunken}; color: {ink}; }
QToolTip { background-color: {ink}; color: #FFFFFF; border: none; padding: 4px 6px; }
QListWidget, QTreeWidget, QTreeView, QListView {
    background-color: #FFFFFF;
    border: 1px solid {line};
    border-radius: 4px;
    selection-background-color: {sunken};
    selection-color: {ink};
}
QCheckBox, QRadioButton { background: transparent; }

QMainWindow {
    background-color: {canvas};
}

/* ── Sidebar ─────────────────────────────────────────────────────────────── */
#Sidebar {
    background-color: {ink};
    border-right: 1px solid {chrome3};
    min-width: 210px;
    max-width: 210px;
}

#SidebarLogo {
    background-color: {chrome3};
    color: #FFFFFF;
    font-size: 14px;
    font-weight: bold;
    padding: 18px 16px;
    border-bottom: 1px solid {chrome3};
}

#SidebarVersion {
    color: {chrome2};
    font-size: 10px;
    padding: 0 16px 12px 16px;
    background-color: {chrome3};
}

#NavButton {
    background-color: transparent;
    color: {chrome2};
    text-align: left;
    padding: 11px 16px 11px 20px;
    border: none;
    border-left: 3px solid transparent;
    font-size: 13px;
    border-radius: 0px;
}

#NavButton:hover {
    background-color: {chromeHi};
    color: #FFFFFF;
    border-left: 3px solid {chrome2};
}

#NavButton[active="true"] {
    background-color: {chromeHi};
    color: #FFFFFF;
    border-left: 3px solid #FFFFFF;
    font-weight: bold;
}

#SidebarSection {
    color: {chrome2};
    font-size: 10px;
    font-weight: bold;
    padding: 14px 16px 4px 20px;
    background-color: {ink};
    letter-spacing: 1px;
}

/* ── Page area ───────────────────────────────────────────────────────────── */
#PageArea {
    background-color: {canvas};
}

#PageHeader {
    background-color: #FFFFFF;
    border-bottom: 1px solid {line};
    padding: 0px;
    min-height: 52px;
    max-height: 52px;
}

#PageTitle {
    font-size: 17px;
    font-weight: bold;
    color: {ink};
    padding: 0 24px;
}

#PageSubtitle {
    font-size: 12px;
    color: {muted};
    padding: 0 24px;
}

/* ── Cards ───────────────────────────────────────────────────────────────── */
#KPICard {
    background-color: #FFFFFF;
    border: 1px solid {line};
    border-radius: 8px;
    padding: 16px;
}

#KPICardValue {
    font-size: 26px;
    font-weight: bold;
    color: {ink};
}

#KPICardLabel {
    font-size: 11px;
    color: {muted};
    font-weight: normal;
}

#KPICardUnit {
    font-size: 12px;
    color: {muted};
}

#KPICardTrend {
    font-size: 11px;
}

#Card {
    background-color: #FFFFFF;
    border: 1px solid {line};
    border-radius: 8px;
}

#CardTitle {
    font-size: 13px;
    font-weight: bold;
    color: {ink};
    padding: 14px 16px 4px 16px;
    border-bottom: 1px solid {canvas};
}

/* ── Toolbar (top of each page) ──────────────────────────────────────────── */
#ToolBar {
    background-color: #FFFFFF;
    border-bottom: 1px solid {line};
    padding: 8px 16px;
    spacing: 8px;
}

/* ── Buttons ─────────────────────────────────────────────────────────────── */
#PrimaryButton {
    background-color: {ink};
    color: #FFFFFF;
    border: none;
    border-radius: 4px;
    padding: 7px 18px;
    font-weight: bold;
    font-size: 12px;
}
#PrimaryButton:hover  { background-color: {ink2}; }
#PrimaryButton:pressed{ background-color: #000000; }

#SecondaryButton {
    background-color: #FFFFFF;
    color: {ink};
    border: 1px solid {line2};
    border-radius: 4px;
    padding: 7px 18px;
    font-size: 12px;
}
#SecondaryButton:hover  { background-color: {sunken}; border-color: {muted}; }

#SuccessButton {
    background-color: {ok};
    color: #FFFFFF;
    border: none;
    border-radius: 4px;
    padding: 7px 18px;
    font-weight: bold;
    font-size: 12px;
}
#SuccessButton:hover { background-color: #17552E; }

#DangerButton {
    background-color: {crit};
    color: #FFFFFF;
    border: none;
    border-radius: 4px;
    padding: 7px 18px;
    font-size: 12px;
}
#DangerButton:hover { background-color: #861515; }

#WarningButton {
    background-color: {warn};
    color: #FFFFFF;
    border: none;
    border-radius: 4px;
    padding: 7px 18px;
    font-size: 12px;
}
#WarningButton:hover { background-color: #5F4000; }

/* ── Filter Panel ────────────────────────────────────────────────────────── */
#FilterPanel {
    background-color: #FFFFFF;
    border: 1px solid {line};
    border-radius: 6px;
    padding: 10px 14px;
}

#FilterLabel {
    font-size: 11px;
    color: {muted};
    font-weight: bold;
}

/* ── Input fields ────────────────────────────────────────────────────────── */
QLineEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QDateEdit {
    background-color: #FFFFFF;
    border: 1px solid {line2};
    border-radius: 4px;
    padding: 5px 8px;
    color: {ink};
    selection-background-color: {ink};
}
QLineEdit:focus, QTextEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QDateEdit:focus {
    border: 1px solid {ink};
    outline: none;
}

QComboBox {
    background-color: #FFFFFF;
    border: 1px solid {line2};
    border-radius: 4px;
    padding: 5px 8px;
    color: {ink};
}
QComboBox:focus { border: 1px solid {ink}; }
QComboBox::drop-down {
    border: none;
    width: 20px;
}
QComboBox QAbstractItemView {
    background-color: #FFFFFF;
    border: 1px solid {line2};
    selection-background-color: {sunken};
    selection-color: {ink};
}

/* ── Tables ──────────────────────────────────────────────────────────────── */
QTableWidget {
    background-color: #FFFFFF;
    border: 1px solid {line};
    border-radius: 6px;
    gridline-color: {canvas};
    selection-background-color: {sunken};
    selection-color: {ink};
    alternate-background-color: #FAFBFC;
}
QTableWidget::item {
    padding: 6px 8px;
    border: none;
}
QTableWidget::item:selected {
    background-color: {sunken};
    color: {ink};
}
QHeaderView::section {
    background-color: {sunken};
    color: {muted};
    font-weight: bold;
    font-size: 11px;
    padding: 7px 8px;
    border: none;
    border-bottom: 2px solid {line};
    border-right: 1px solid {line};
    letter-spacing: 0.5px;
}
QHeaderView::section:first { border-top-left-radius: 6px; }
QHeaderView { background-color: {sunken}; }

/* ── Tab Widget (inner tabs only, not main nav) ──────────────────────────── */
QTabWidget::pane {
    border: 1px solid {line};
    border-radius: 0 6px 6px 6px;
    background: #FFFFFF;
}
QTabBar::tab {
    background: {canvas};
    color: {muted};
    padding: 8px 18px;
    border: 1px solid {line};
    border-bottom: none;
    border-radius: 5px 5px 0 0;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background: #FFFFFF;
    color: {ink};
    font-weight: bold;
    border-top: 2px solid {ink};
}
QTabBar::tab:hover:!selected { background: {sunken}; }

/* ── Group boxes ─────────────────────────────────────────────────────────── */
QGroupBox {
    background-color: #FFFFFF;
    border: 1px solid {line};
    border-radius: 6px;
    margin-top: 8px;
    padding-top: 10px;
    font-weight: bold;
    font-size: 12px;
    color: {muted};
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 6px;
    left: 12px;
    color: {muted};
}

/* ── Scroll bars ─────────────────────────────────────────────────────────── */
QScrollBar:vertical {
    background: {canvas};
    width: 8px;
    border-radius: 4px;
}
QScrollBar::handle:vertical {
    background: {line2};
    border-radius: 4px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover { background: {muted}; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }

QScrollBar:horizontal {
    background: {canvas};
    height: 8px;
    border-radius: 4px;
}
QScrollBar::handle:horizontal {
    background: {line2};
    border-radius: 4px;
    min-width: 30px;
}
QScrollBar::handle:horizontal:hover { background: {muted}; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

/* ── Status badges ───────────────────────────────────────────────────────── */
#BadgeFixed       { color: {ok}; font-weight: bold; font-size: 11px; }
#BadgeMonitoring  { color: {warn}; font-weight: bold; font-size: 11px; }
#BadgeUnresolved  { color: {crit}; font-weight: bold; font-size: 11px; }
#BadgeEscalated   { color: {wait}; font-weight: bold; font-size: 11px; }

/* ── Splitter ────────────────────────────────────────────────────────────── */
QSplitter::handle {
    background-color: {line};
    width: 1px;
}

/* ── Message boxes ───────────────────────────────────────────────────────── */
QMessageBox {
    background-color: #FFFFFF;
}
QMessageBox QPushButton {
    background-color: {ink};
    color: white;
    border-radius: 4px;
    padding: 6px 18px;
    font-size: 12px;
}
QMessageBox QPushButton:hover { background-color: {ink2}; }

/* ── Dialog ──────────────────────────────────────────────────────────────── */
QDialog {
    background-color: {canvas};
}

"""


def _build(qss: str) -> str:
    for name, value in TOKENS.items():
        qss = qss.replace('{' + name + '}', value)
    return qss


APP_STYLE = _build(_QSS)

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
