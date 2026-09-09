"""
ui/equipment_page.py
---------------------
Equipment — the plant's real hierarchy and the history of each piece of it.

Left:  block → LC → PCS unit tree, generated from SCADA alarm Element codes.
Right: everything known about the selected asset — alarm history by month,
       worst faults, recent events, work reports, PM, exclusions, and the
       "has this happened elsewhere?" lookup used while troubleshooting.

Data here is as of the last imported month, never live. Every screen shows
that date at the top so nobody mistakes it for the current state of the plant.
"""

import datetime
import os

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QLineEdit,
    QPushButton, QTreeWidget, QTreeWidgetItem, QSplitter, QTabWidget,
    QMessageBox, QHeaderView, QAbstractItemView, QScrollArea, QFrame,
    QTableWidget, QTableWidgetItem, QApplication, QDialog, QSpinBox,
    QFileDialog, QDialogButtonBox, QProgressBar, QCheckBox
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QFont

from ui.components import (PageHeader, PrimaryButton, SecondaryButton,
                           make_table, HSeparator)
from services.project_service import get_all_projects
import services.asset_tree_service as ats

MONTH_NAMES = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
               'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

LEVEL_ICON = {'block': '🏗', 'lc': '🗄', 'unit': '⚡', 'module': '🔌'}


def _fmt_dt(v, width=16):
    """SCADA timestamps are stored as 'YYYY-MM-DD HH:MM:SS'."""
    return str(v)[:width] if v else '—'


def _fmt_h(v):
    try:
        return f'{float(v):.2f}'
    except (TypeError, ValueError):
        return '—'


def _fmt_blocks(blocks):
    """[3, 4, 5, 9] → '3-5, 9' — a plant-wide incident lists 70 blocks."""
    nums = sorted({int(b) for b in (blocks or [])})
    if not nums:
        return '—'
    runs, start, prev = [], nums[0], nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        runs.append((start, prev)); start = prev = n
    runs.append((start, prev))
    out = ', '.join(str(a) if a == b else f'{a}-{b}' for a, b in runs)
    return out if len(out) <= 34 else out[:31] + '…'


class _Stat(QFrame):
    """A small labelled figure in the asset header strip."""

    def __init__(self, caption, value, accent='#1A2B45', parent=None):
        super().__init__(parent)
        self.setObjectName('EqStat')
        self.setStyleSheet(
            '#EqStat{background:#FFFFFF;border:1px solid #E0E4EA;'
            'border-radius:8px;}')
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)
        cap = QLabel(caption.upper())
        cap.setStyleSheet('font-size:9px;font-weight:bold;color:#6B7A8D;'
                          'letter-spacing:1px;background:transparent;')
        lay.addWidget(cap)
        self.val = QLabel(str(value))
        self.val.setStyleSheet(f'font-size:19px;font-weight:bold;color:{accent};'
                               'background:transparent;')
        lay.addWidget(self.val)

    def set_value(self, v):
        self.val.setText(str(v))


class _ImportDialog(QDialog):
    """Pick an Alarms report export and the month it belongs to."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Import alarm history')
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        intro = QLabel(
            'Load a SCADA "Alarms report" export so its month becomes part of '
            'the equipment history. Re-importing a month you already have '
            'changes nothing, so this is always safe to run.')
        intro.setWordWrap(True)
        intro.setStyleSheet('color:#6B7A8D;')
        lay.addWidget(intro)

        row = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setPlaceholderText('Alarms report.XLSX')
        row.addWidget(self.path, 1)
        browse = SecondaryButton('Browse…')
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        lay.addLayout(row)

        today = datetime.date.today()
        prev = (today.replace(day=1) - datetime.timedelta(days=1))
        my = QHBoxLayout()
        my.addWidget(QLabel('Month:'))
        self.month = QComboBox()
        for i in range(1, 13):
            self.month.addItem(MONTH_NAMES[i], i)
        self.month.setCurrentIndex(prev.month - 1)
        my.addWidget(self.month)
        my.addWidget(QLabel('Year:'))
        self.year = QSpinBox()
        self.year.setRange(2020, 2100)
        self.year.setValue(prev.year)
        my.addWidget(self.year)
        my.addStretch()
        lay.addLayout(my)

        note = QLabel(
            'Availability exclusions recorded for that month are applied, so '
            'grid outages and PM are not charged to the equipment.')
        note.setWordWrap(True)
        note.setStyleSheet('color:#6B7A8D;font-size:11px;')
        lay.addWidget(note)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    def _browse(self):
        f, _ = QFileDialog.getOpenFileName(
            self, 'Alarms report', '', 'Excel (*.xlsx *.XLSX *.xls)')
        if f:
            self.path.setText(f)

    def values(self):
        return (self.path.text().strip(), int(self.year.value()),
                int(self.month.currentData()))


class _ImportWorker(QThread):
    """Loading and classifying ~20 000 alarms takes seconds — keep it off the
    UI thread so the window never goes grey."""

    progress = pyqtSignal(str)
    done = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, project_id, path, year, month, parent=None):
        super().__init__(parent)
        self.project_id, self.path = project_id, path
        self.year, self.month = year, month

    def run(self):
        try:
            from services.bukhara_report_service import (
                load_alarms, load_alarm_classifications,
                apply_alarm_classifications, tag_alarms_with_exclusions)
            from services import availability_service as av

            self.progress.emit('Reading alarms…')
            alarms = load_alarms(self.path)
            self.progress.emit('Classifying…')
            # None → the packaged data/alarm_classifications.csv, resolved
            # relative to the install root rather than the working directory
            alarms = apply_alarm_classifications(
                alarms, load_alarm_classifications(None))
            self.progress.emit('Applying exclusions…')
            exc = av.get_exclusions(project_id=self.project_id,
                                    year=self.year, month=self.month) or []
            alarms = tag_alarms_with_exclusions(alarms, exc)
            self.progress.emit('Storing history…')
            stats = ats.import_alarm_events(
                self.project_id, self.year, self.month, alarms,
                log=lambda m: self.progress.emit(m))
            stats['exclusions'] = len(exc)
            self.done.emit(stats)
        except Exception as e:                       # noqa: BLE001
            self.failed.emit(str(e))


class EquipmentPage(QWidget):
    """Asset tree + per-asset history. Scoped to the shell's current project."""

    # Emitted when the user raises a work report from an alarm row. Carries
    # the whole alarm_events row plus project_id, so the Work Report form can
    # fill itself in without another lookup.
    work_report_requested = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project_id = None
        self._current_code = None
        self._stats = {}
        self._build_ui()
        self._load_projects()

    # ── construction ─────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = PageHeader('Equipment',
                            'Block → LC → PCS unit · full history per asset')
        self.import_btn = PrimaryButton('⤓  Import alarms…')
        self.import_btn.setToolTip(
            'Load a month of SCADA alarms into the equipment history.\n'
            'Monthly reports do this automatically — use this to backfill '
            'earlier months.')
        self.import_btn.clicked.connect(self._import)
        header.add_action(self.import_btn)
        self.rebuild_btn = SecondaryButton('⟲  Rebuild tree')
        self.rebuild_btn.setToolTip(
            'Regenerate blocks / LCs / units from imported alarm history.\n'
            'Existing serial numbers and notes are kept.')
        self.rebuild_btn.clicked.connect(lambda: self._rebuild())
        header.add_action(self.rebuild_btn)
        root.addWidget(header)

        # Project picker + freshness banner
        bar = QWidget()
        bar.setStyleSheet('background:#FFFFFF;border-bottom:1px solid #E0E4EA;')
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(24, 10, 24, 10)
        bl.setSpacing(10)
        bl.addWidget(QLabel('Project:'))
        self.project_combo = QComboBox()
        self.project_combo.setMinimumWidth(240)
        self.project_combo.currentIndexChanged.connect(self._on_project_changed)
        bl.addWidget(self.project_combo)
        bl.addStretch()
        self.fresh_lbl = QLabel('')
        self.fresh_lbl.setStyleSheet(
            'color:#8A6D1F;background:#FFF6DC;border:1px solid #F0DFA8;'
            'border-radius:6px;padding:5px 12px;font-size:11px;')
        bl.addWidget(self.fresh_lbl)
        self.import_status = QLabel('')
        self.import_status.setStyleSheet('color:#0071E3;font-size:11px;')
        self.import_status.setVisible(False)
        bl.addWidget(self.import_status)
        root.addWidget(bar)

        split = QSplitter(Qt.Horizontal)
        split.setStyleSheet('QSplitter::handle{background:#E0E4EA;width:1px;}')

        # ── left: tree ────────────────────────────────────────────────────
        left = QWidget()
        left.setStyleSheet('background:#FFFFFF;')
        ll = QVBoxLayout(left)
        ll.setContentsMargins(12, 12, 12, 12)
        ll.setSpacing(8)

        self.search = QLineEdit()
        self.search.setPlaceholderText('Find block or unit — e.g. 32 or 32.01.02')
        self.search.setClearButtonEnabled(True)
        self.search.returnPressed.connect(self._jump_to_search)
        self.search.textChanged.connect(self._filter_tree)
        ll.addWidget(self.search)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['Asset', 'Faults', 'Fault h'])
        self.tree.headerItem().setToolTip(
            2, 'Summed alarm duration in equipment-hours (two units in fault '
               'for an hour = 2 h), excluding grid-outage and PM windows.')
        self.tree.setColumnWidth(0, 210)
        self.tree.setColumnWidth(1, 55)
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.itemSelectionChanged.connect(self._on_tree_selection)
        self.tree.itemExpanded.connect(self._on_expand)
        ll.addWidget(self.tree, 1)

        self.tree_hint = QLabel('')
        self.tree_hint.setStyleSheet('color:#6B7A8D;font-size:11px;')
        ll.addWidget(self.tree_hint)
        split.addWidget(left)

        # ── right: detail ────────────────────────────────────────────────
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(20, 16, 20, 16)
        rl.setSpacing(12)

        self.asset_title = QLabel('Select an asset')
        self.asset_title.setStyleSheet(
            'font-size:19px;font-weight:bold;color:#1A2B45;')
        rl.addWidget(self.asset_title)

        self.asset_sub = QLabel('')
        self.asset_sub.setStyleSheet('color:#6B7A8D;font-size:12px;')
        rl.addWidget(self.asset_sub)

        stats = QHBoxLayout()
        stats.setSpacing(10)
        self.st_events = _Stat('Own alarms', '—')
        self.st_events.setToolTip(
            'Alarms raised by this equipment, excluding those that fell inside '
            'a grid-outage or PM window. Matches the tree on the left.')
        self.st_hours = _Stat('Own fault hours', '—', '#FF3B30')
        self.st_hours.setToolTip(
            'Summed alarm duration, excluding alarms that fell inside a grid-'
            'outage or PM window. Equipment-hours, not wall-clock hours: '
            'faults on several units at once add up.')
        self.st_excl = _Stat('In excluded windows', '—', '#6B7A8D')
        self.st_excl.val.setStyleSheet(
            'font-size:15px;font-weight:bold;color:#6B7A8D;background:transparent;')
        self.st_excl.setToolTip(
            'Hours the equipment spent in alarm during grid outages or planned '
            'maintenance — not counted against availability.')
        self.st_reports = _Stat('Work reports', '—', '#0071E3')
        for s in (self.st_events, self.st_hours, self.st_excl, self.st_reports):
            stats.addWidget(s)
        stats.addStretch()
        rl.addLayout(stats)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_overview(), 'Overview')
        self.tabs.addTab(self._tab_alarms(), 'Alarm log')
        self.tabs.addTab(self._tab_gaps(), 'Needs a report')
        self.tabs.addTab(self._tab_work(), 'Work & PM')
        self.tabs.addTab(self._tab_identity(), 'Identity')
        self.tabs.currentChanged.connect(self._on_tab_changed)
        rl.addWidget(self.tabs, 1)

        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([340, 900])
        root.addWidget(split, 1)

    def _tab_overview(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(8)

        lay.addWidget(self._caption('Worst faults on this asset'))
        self.faults_tbl = make_table(
            ['Fault', 'Subsystem', 'Reason', 'Events', 'Hours', 'Last seen'])
        self.faults_tbl.itemSelectionChanged.connect(self._on_fault_selected)
        lay.addWidget(self.faults_tbl, 3)

        row = QHBoxLayout()
        row.addWidget(self._caption('Same fault elsewhere in the plant'))
        row.addStretch()
        self.similar_hint = QLabel('select a fault above')
        self.similar_hint.setStyleSheet('color:#6B7A8D;font-size:11px;')
        row.addWidget(self.similar_hint)
        lay.addLayout(row)
        self.similar_tbl = make_table(
            ['Asset', 'Equipment', 'Events', 'Hours', 'Last seen'])
        lay.addWidget(self.similar_tbl, 2)

        lay.addWidget(self._caption('By month'))
        self.month_tbl = make_table(
            ['Month', 'Alarms', 'Excluded', 'Own fault h', 'Total h'])
        self.month_tbl.setMaximumHeight(150)
        lay.addWidget(self.month_tbl, 1)
        return w

    def _tab_alarms(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(8)
        cap = QLabel('Most recent 40 alarm events. Greyed rows fell inside an '
                     'availability exclusion window (grid outage / PM).')
        cap.setStyleSheet('color:#6B7A8D;font-size:11px;')
        cap.setWordWrap(True)
        lay.addWidget(cap)
        self.alarm_tbl = make_table(
            ['Report', 'Element', 'Fault', 'Severity', 'Activated', 'Cleared',
             'Hours', 'Excluded by'])
        self.alarm_tbl.doubleClicked.connect(lambda: self._raise_report(self.alarm_tbl))
        lay.addWidget(self.alarm_tbl, 1)

        row = QHBoxLayout()
        row.addStretch()
        btn = PrimaryButton('🔧  Raise work report from this alarm')
        btn.setToolTip('Opens the Work Report form already filled in from the '
                       'selected alarm (double-click a row does the same).')
        btn.clicked.connect(lambda: self._raise_report(self.alarm_tbl))
        row.addWidget(btn)
        lay.addLayout(row)
        return w

    def _tab_gaps(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(8)

        cap = QLabel(
            'Faults that cost availability and have no work report against '
            'them. This is the list the customer asks about at month end — '
            'alarms inside grid-outage or PM windows are left out, because '
            'those are not ours to explain.')
        cap.setStyleSheet('color:#6B7A8D;font-size:11px;')
        cap.setWordWrap(True)
        lay.addWidget(cap)

        ctl = QHBoxLayout()
        ctl.addWidget(QLabel('Longer than'))
        self.gap_hours = QComboBox()
        for h in (0.5, 1.0, 2.0, 4.0, 8.0):
            self.gap_hours.addItem(f'{h:g} h', h)
        self.gap_hours.setCurrentIndex(1)
        self.gap_hours.currentIndexChanged.connect(self._load_gaps)
        ctl.addWidget(self.gap_hours)
        self.gap_scope = QComboBox()
        self.gap_scope.addItem('Whole plant', False)
        self.gap_scope.addItem('Selected asset only', True)
        self.gap_scope.currentIndexChanged.connect(self._load_gaps)
        ctl.addWidget(self.gap_scope)
        self.gap_group = QCheckBox('Group into incidents')
        self.gap_group.setChecked(True)
        self.gap_group.setToolTip(
            'One fault often trips many units at once — 25 units in the same '
            'minute is one incident and one report, not 25.')
        self.gap_group.toggled.connect(self._load_gaps)
        ctl.addWidget(self.gap_group)
        ctl.addStretch()
        self.gap_summary = QLabel('')
        self.gap_summary.setStyleSheet('color:#B4232A;font-weight:bold;')
        ctl.addWidget(self.gap_summary)
        lay.addLayout(ctl)

        self.gap_tbl = make_table(
            ['Where', 'Units', 'Fault', 'Reason', 'Activated', 'Worst h',
             'Total h', 'Note'])
        self.gap_tbl.doubleClicked.connect(lambda: self._raise_report(self.gap_tbl))
        lay.addWidget(self.gap_tbl, 1)

        row = QHBoxLayout()
        row.addStretch()
        btn = PrimaryButton('🔧  Raise work report from this alarm')
        btn.clicked.connect(lambda: self._raise_report(self.gap_tbl))
        row.addWidget(btn)
        lay.addLayout(row)
        return w

    def _tab_work(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(self._caption('Work reports on this block'))
        self.work_tbl = make_table(
            ['Date', 'Zone', 'Block', 'Fault', 'Work performed', 'Root cause',
             'Status', 'Engineer', 'Alarm'])
        lay.addWidget(self.work_tbl, 2)
        lay.addWidget(self._caption('Planned maintenance'))
        self.pm_tbl = make_table(['From', 'To', 'Hours', 'Blocks', 'Description'])
        lay.addWidget(self.pm_tbl, 1)
        lay.addWidget(self._caption('Availability exclusions covering this block'))
        self.excl_tbl = make_table(
            ['Type', 'From', 'To', 'Blocks', 'Description'])
        lay.addWidget(self.excl_tbl, 1)
        return w

    def _tab_identity(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 16, 0, 0)
        lay.setSpacing(10)

        grid = QVBoxLayout()
        grid.setSpacing(8)
        for attr, label, ph in (
            ('id_name', 'Name', 'Block 32 · LC 1 · Unit 2'),
            ('id_serial', 'Serial number', 'as printed on the nameplate'),
            ('id_type', 'Equipment type', 'PCS / LC / BLOCK'),
            ('id_notes', 'Notes', 'anything the next engineer should know'),
        ):
            row = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setFixedWidth(130)
            lbl.setStyleSheet('color:#6B7A8D;')
            row.addWidget(lbl)
            edit = QLineEdit()
            edit.setPlaceholderText(ph)
            setattr(self, attr, edit)
            row.addWidget(edit, 1)
            grid.addLayout(row)
        lay.addLayout(grid)

        btns = QHBoxLayout()
        btns.addStretch()
        save = PrimaryButton('Save')
        save.clicked.connect(self._save_identity)
        btns.addWidget(save)
        lay.addLayout(btns)

        note = QLabel(
            'Capacity and hierarchy are generated from SCADA element codes and '
            'are not edited here. DC/DC and CMU modules get their own record '
            'only once one is actually replaced.')
        note.setWordWrap(True)
        note.setStyleSheet('color:#6B7A8D;font-size:11px;')
        lay.addWidget(note)
        lay.addStretch()
        return w

    @staticmethod
    def _caption(text):
        lbl = QLabel(text.upper())
        lbl.setStyleSheet('font-size:10px;font-weight:bold;color:#6B7A8D;'
                          'letter-spacing:1px;')
        return lbl

    # ── project scoping ──────────────────────────────────────────────────
    def _load_projects(self):
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        for p in get_all_projects():
            self.project_combo.addItem(p.name, p.id)
        self.project_combo.blockSignals(False)
        if self.project_combo.count():
            self._on_project_changed()

    def set_current_project(self, project_id):
        """Called by the shell when a project is opened."""
        idx = self.project_combo.findData(project_id)
        if idx < 0:
            self._load_projects()
            idx = self.project_combo.findData(project_id)
        if idx >= 0 and idx != self.project_combo.currentIndex():
            self.project_combo.setCurrentIndex(idx)
        elif idx >= 0:
            self._on_project_changed()

    def _on_project_changed(self):
        self._project_id = self.project_combo.currentData()
        self._current_code = None
        self._refresh_freshness()
        self._load_tree()
        self._clear_detail()
        self.gap_tbl.setRowCount(0)
        self.gap_summary.setText('')
        self._set_gap_badge(0)

    def _refresh_freshness(self):
        if not self._project_id:
            self.fresh_lbl.setText('')
            return
        f = ats.get_data_freshness(self._project_id)
        if not f:
            self.fresh_lbl.setText(
                '⚠  No alarm history imported yet — generate a monthly report first')
            return
        self.fresh_lbl.setText(
            f"Data as of {MONTH_NAMES[int(f['month'])]} {f['year']} · "
            f"{f['n']:,} events · imported {_fmt_dt(f['imported_at'], 16)}")

    # ── tree ─────────────────────────────────────────────────────────────
    def _load_tree(self):
        self.tree.clear()
        if not self._project_id:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self._stats = ats.get_tree_stats(self._project_id)
            for r in ats.get_children(self._project_id):
                self.tree.addTopLevelItem(self._make_item(r))
        finally:
            QApplication.restoreOverrideCursor()
        self.tree_hint.setText(
            f'{self.tree.topLevelItemCount()} blocks · faults and hours exclude '
            'grid-outage / PM windows'
            if self.tree.topLevelItemCount() else
            'Nothing here yet — press "Rebuild tree" after importing a month.')

    def _make_item(self, row):
        code = row['code']
        s = self._stats.get(code, {})
        item = QTreeWidgetItem([
            f"{LEVEL_ICON.get(row['level'], '•')}  {row.get('name') or code}",
            str(s.get('events', '') or ''),
            _fmt_h(s.get('hours')) if s.get('hours') else '',
        ])
        item.setData(0, Qt.UserRole, code)
        item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
        item.setTextAlignment(2, Qt.AlignRight | Qt.AlignVCenter)
        hours = float(s.get('hours') or 0)
        if hours >= 24:
            item.setForeground(2, QColor('#FF3B30'))
        elif hours >= 4:
            item.setForeground(2, QColor('#FF9500'))
        if row['level'] != 'module':
            # lazy child marker — real children are loaded on expand
            item.addChild(QTreeWidgetItem(['…']))
        return item

    def _on_expand(self, item):
        if item.childCount() == 1 and item.child(0).text(0) == '…':
            item.takeChildren()
            code = item.data(0, Qt.UserRole)
            for r in ats.get_children(self._project_id, code):
                item.addChild(self._make_item(r))
            if item.childCount() == 0:
                item.setChildIndicatorPolicy(QTreeWidgetItem.DontShowIndicator)

    def _filter_tree(self, text):
        text = (text or '').strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            it.setHidden(bool(text) and text not in it.text(0).lower()
                         and not str(it.data(0, Qt.UserRole)).startswith(text))

    def _jump_to_search(self):
        """Typing a code (32, 32.01, 32.01.02) opens and reveals that asset."""
        code = (self.search.text() or '').strip()
        if not code or not self._project_id:
            return
        if ats.get_asset(self._project_id, code):
            self._reveal_in_tree(code)
            self.show_asset(code)
        else:
            self.asset_sub.setText(f'No asset with code "{code}" in this project.')

    def _reveal_in_tree(self, code: str):
        """Expand block → LC → unit down to `code` and select it."""
        parts = code.split('.')
        chain = ['.'.join(parts[:i + 1]) for i in range(len(parts))]
        item = None
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            it.setHidden(False)
            if it.data(0, Qt.UserRole) == chain[0]:
                item = it
        if item is None:
            return
        for want in chain[1:]:
            self.tree.expandItem(item)          # lazily loads the children
            nxt = next((item.child(k) for k in range(item.childCount())
                        if item.child(k).data(0, Qt.UserRole) == want), None)
            if nxt is None:
                break
            item = nxt
        self.tree.setCurrentItem(item)
        self.tree.scrollToItem(item)

    def _on_tree_selection(self):
        items = self.tree.selectedItems()
        if not items:
            return
        code = items[0].data(0, Qt.UserRole)
        if code and code != '…':
            self.show_asset(code)

    # ── detail ───────────────────────────────────────────────────────────
    def _clear_detail(self):
        self._current_code = None
        self.asset_title.setText('Select an asset')
        self.asset_sub.setText('')
        for s in (self.st_events, self.st_hours, self.st_excl, self.st_reports):
            s.set_value('—')
        for t in (self.faults_tbl, self.similar_tbl, self.month_tbl,
                  self.alarm_tbl, self.work_tbl, self.pm_tbl, self.excl_tbl):
            t.setRowCount(0)

    def show_asset(self, code):
        """Load and display one asset. Public — the shell may deep-link here."""
        if not self._project_id:
            return
        self._current_code = code
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            h = ats.get_asset_history(self._project_id, code)
        finally:
            QApplication.restoreOverrideCursor()

        a = h['asset']
        self.asset_title.setText(a.get('name') or code)
        bits = [f"Code {code}", (a.get('level') or '').upper()]
        if a.get('equipment_type'):
            bits.append(a['equipment_type'])
        if a.get('capacity_kwh'):
            bits.append(f"{float(a['capacity_kwh']):,.0f} kWh")
        if a.get('serial_number'):
            bits.append(f"S/N {a['serial_number']}")
        t = h['totals'] or {}
        if t.get('first_seen'):
            bits.append(f"history {_fmt_dt(t['first_seen'], 10)} → "
                        f"{_fmt_dt(t.get('last_seen'), 10)}")
        self.asset_sub.setText('  ·  '.join(str(b) for b in bits if b))

        n_all = int(t.get('events') or 0)
        n_excl = int(t.get('excluded_events') or 0)
        self.st_events.set_value(f"{n_all - n_excl:,}")
        self.st_hours.set_value(_fmt_h(t.get('own_hours') or 0))
        self.st_excl.set_value(
            f"{n_excl:,} · {_fmt_h(t.get('excluded_hours') or 0)} h")
        self.st_reports.set_value(str(len(h['work_reports'])))

        self._fill_faults(h['top_faults'])
        self._fill_months(h['by_month'])
        self._fill_alarms(h['recent'])
        self._fill_work(h)
        self._fill_identity(a)
        self.similar_tbl.setRowCount(0)
        self.similar_hint.setText('select a fault above')
        if self.tabs.tabText(self.tabs.currentIndex()).startswith('Needs'):
            self._load_gaps()

    def _fill_faults(self, rows):
        self.faults_tbl.setRowCount(0)
        for r in rows:
            i = self.faults_tbl.rowCount()
            self.faults_tbl.insertRow(i)
            vals = [str(r.get('trigger_name') or ''),
                    str(r.get('cls_subsystem') or ''),
                    str(r.get('cls_reason') or ''),
                    str(r.get('events') or 0),
                    _fmt_h(r.get('hours')),
                    _fmt_dt(r.get('last_seen'))]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if c >= 3:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.faults_tbl.setItem(i, c, item)

    def _fill_months(self, rows):
        self.month_tbl.setRowCount(0)
        for r in rows:
            i = self.month_tbl.rowCount()
            self.month_tbl.insertRow(i)
            vals = [f"{MONTH_NAMES[int(r['month'])]} {r['year']}",
                    str(r.get('events') or 0),
                    str(r.get('excluded') or 0),
                    _fmt_h(r.get('own_hours')),
                    _fmt_h(r.get('hours'))]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if c:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.month_tbl.setItem(i, c, item)

    def _fill_alarms(self, rows):
        self.alarm_tbl.setRowCount(0)
        grey = QColor('#9AA6B4')
        for r in rows:
            i = self.alarm_tbl.rowCount()
            self.alarm_tbl.insertRow(i)
            reported = r.get('work_log_id')
            vals = ['✓' if reported else '',
                    str(r.get('element') or ''),
                    str(r.get('trigger_name') or ''),
                    str(r.get('cls_severity') or ''),
                    _fmt_dt(r.get('activated')),
                    _fmt_dt(r.get('deactivated')),
                    _fmt_h(r.get('hours')),
                    str(r.get('excluded_by') or '')]
            excluded = bool(r.get('is_excluded'))
            for c, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if c in (0, 6):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if excluded:
                    item.setForeground(grey)
                self.alarm_tbl.setItem(i, c, item)
            if reported:
                self.alarm_tbl.item(i, 0).setForeground(QColor('#1E8E3E'))
                self.alarm_tbl.item(i, 0).setToolTip(
                    f'Work report #{reported}')
            # keep the whole alarm row on the first cell for one-tap reporting
            self.alarm_tbl.item(i, 0).setData(Qt.UserRole, r)

    # ── Reconciliation: faults with no work report ───────────────────────
    def _on_tab_changed(self, idx):
        if self.tabs.tabText(idx).startswith('Needs'):
            self._load_gaps()

    def _load_gaps(self):
        self.gap_tbl.setRowCount(0)
        self.gap_summary.setText('')
        if not self._project_id:
            return
        f = ats.get_data_freshness(self._project_id)
        if not f:
            return
        scoped = bool(self.gap_scope.currentData())
        alarms = ats.get_unreported_faults(
            self._project_id, year=int(f['year']), month=int(f['month']),
            min_hours=float(self.gap_hours.currentData()),
            code=self._current_code if scoped else None)

        if self.gap_group.isChecked():
            rows = ats.group_faults_into_incidents(alarms)
        else:
            rows = [dict(a, ids=[a['id']], units=1,
                         assets=[a.get('asset_code') or ''],
                         blocks=[a['block']] if a.get('block') is not None else [],
                         total_hours=a.get('hours')) for a in alarms]
        rows = ats.flag_incidents_near_exclusions(self._project_id, rows)

        for r in rows:
            i = self.gap_tbl.rowCount()
            self.gap_tbl.insertRow(i)
            assets = r.get('assets') or []
            where_txt = (assets[0] if len(assets) == 1
                         else f"{len(assets)} units · blocks "
                              + _fmt_blocks(r.get('blocks')))
            vals = [where_txt,
                    str(r.get('units') or 1),
                    str(r.get('trigger_name') or ''),
                    str(r.get('cls_reason') or ''),
                    _fmt_dt(r.get('activated')),
                    _fmt_h(r.get('hours')),
                    _fmt_h(r.get('total_hours')),
                    str(r.get('near_exclusion') or '')]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if c in (1, 5, 6):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.gap_tbl.setItem(i, c, item)
            if (r.get('units') or 1) > 1:
                self.gap_tbl.item(i, 1).setForeground(QColor('#B4232A'))
            if r.get('near_exclusion'):
                note = self.gap_tbl.item(i, 7)
                note.setForeground(QColor('#8A6D1F'))
                note.setToolTip(
                    'This started just outside a recorded exclusion window. '
                    'Units usually trip before the operator writes down the '
                    'outage — check the window times before writing a report.')
                for c in range(self.gap_tbl.columnCount()):
                    self.gap_tbl.item(i, c).setForeground(QColor('#8A6D1F'))
            self.gap_tbl.item(i, 0).setData(Qt.UserRole, r)

        total_h = sum(float(r.get('total_hours') or 0) for r in rows)
        where = 'on this asset' if scoped else 'plant-wide'
        if rows:
            unit = 'incident(s)' if self.gap_group.isChecked() else 'fault(s)'
            near = sum(1 for r in rows if r.get('near_exclusion'))
            txt = (f'{len(rows)} unreported {unit} {where} · '
                   f'{len(alarms)} alarm(s) · {total_h:,.1f} equipment-hours')
            if near:
                txt += f'  ·  {near} sit just outside an exclusion window'
            self.gap_summary.setText(txt)
        else:
            self.gap_summary.setText('')
        self._set_gap_badge(len(rows))

    def refresh_after_report(self):
        """A work report was just saved — reopen the gap list on it."""
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i).startswith('Needs'):
                self.tabs.setCurrentIndex(i)
                break
        self._load_gaps()
        if self._current_code:
            self.show_asset(self._current_code)

    def _set_gap_badge(self, n):
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i).startswith('Needs'):
                self.tabs.setTabText(
                    i, f'Needs a report ({n})' if n else 'Needs a report')
                return

    def _raise_report(self, table):
        """One tap from an alarm to a pre-filled Work Report."""
        rows = (table.selectionModel().selectedRows()
                if table.selectionModel() else [])
        if not rows:
            QMessageBox.information(self, 'No alarm selected',
                                    'Pick the alarm this report answers.')
            return
        ev = table.item(rows[0].row(), 0).data(Qt.UserRole)
        if not ev:
            return
        if ev.get('work_log_id'):
            ok = QMessageBox.question(
                self, 'Already reported',
                f"Alarm #{ev['id']} already has work report "
                f"#{ev['work_log_id']}.\nRaise another one anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ok != QMessageBox.Yes:
                return
        # A grouped incident carries a `primary` alarm with the per-unit
        # detail (element, block, LC) the form needs; merge the two so the
        # form sees one flat row either way.
        payload = dict(ev.get('primary') or {})
        payload.update({k: v for k, v in ev.items() if k != 'primary'})
        payload['project_id'] = self._project_id
        self.work_report_requested.emit(payload)

    def _fill_work(self, h):
        self.work_tbl.setRowCount(0)
        for r in h['work_reports']:
            i = self.work_tbl.rowCount()
            self.work_tbl.insertRow(i)
            vals = [str(r.get('date') or ''), str(r.get('zone_number') or ''),
                    str(r.get('block_number') or ''),
                    str(r.get('fault_description') or ''),
                    str(r.get('work_performed') or ''),
                    str(r.get('root_cause') or ''),
                    str(r.get('status') or ''), str(r.get('engineer') or ''),
                    f"#{r['alarm_event_id']}" if r.get('alarm_event_id') else '']
            for c, v in enumerate(vals):
                self.work_tbl.setItem(i, c, QTableWidgetItem(v))

        self.pm_tbl.setRowCount(0)
        for r in h['pm']:
            i = self.pm_tbl.rowCount()
            self.pm_tbl.insertRow(i)
            vals = [str(r.get('date_from') or ''), str(r.get('date_to') or ''),
                    _fmt_h(r.get('hours')),
                    str(r.get('affected_blocks') or 'all'),
                    str(r.get('description') or '')]
            for c, v in enumerate(vals):
                self.pm_tbl.setItem(i, c, QTableWidgetItem(v))

        self.excl_tbl.setRowCount(0)
        for r in h['exclusions']:
            i = self.excl_tbl.rowCount()
            self.excl_tbl.insertRow(i)
            frm = f"{r.get('date_from') or ''} {(r.get('time_from') or '')[:5]}"
            to = f"{r.get('date_to') or ''} {(r.get('time_to') or '')[:5]}"
            vals = [str(r.get('exclusion_type') or ''), frm.strip(), to.strip(),
                    str(r.get('affected_blocks') or 'all'),
                    str(r.get('description') or '')]
            for c, v in enumerate(vals):
                self.excl_tbl.setItem(i, c, QTableWidgetItem(v))

    def _fill_identity(self, a):
        self.id_name.setText(str(a.get('name') or ''))
        self.id_serial.setText(str(a.get('serial_number') or ''))
        self.id_type.setText(str(a.get('equipment_type') or ''))
        self.id_notes.setText(str(a.get('notes') or ''))

    # ── actions ──────────────────────────────────────────────────────────
    def _on_fault_selected(self):
        rows = self.faults_tbl.selectionModel().selectedRows() \
            if self.faults_tbl.selectionModel() else []
        if not rows or not self._current_code:
            return
        trigger = self.faults_tbl.item(rows[0].row(), 0).text()
        sim = ats.find_similar_failures(self._project_id, trigger,
                                        exclude_code=self._current_code, limit=25)
        self.similar_tbl.setRowCount(0)
        for r in sim:
            i = self.similar_tbl.rowCount()
            self.similar_tbl.insertRow(i)
            vals = [str(r.get('asset_code') or ''),
                    str(r.get('equipment_type') or ''),
                    str(r.get('events') or 0),
                    _fmt_h(r.get('hours')),
                    _fmt_dt(r.get('last_seen'))]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if c in (2, 3):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.similar_tbl.setItem(i, c, item)
        self.similar_hint.setText(
            f'{len(sim)} other asset(s) hit by this fault'
            if sim else 'only this asset has seen this fault')

    def _save_identity(self):
        if not (self._project_id and self._current_code):
            return
        ats.update_asset(self._project_id, self._current_code,
                         name=self.id_name.text().strip(),
                         serial_number=self.id_serial.text().strip(),
                         equipment_type=self.id_type.text().strip(),
                         notes=self.id_notes.text().strip())
        self.asset_title.setText(self.id_name.text().strip()
                                 or self._current_code)
        self._load_tree()
        QMessageBox.information(self, 'Saved',
                                f'Asset {self._current_code} updated.')

    def _import(self):
        if not self._project_id:
            return
        dlg = _ImportDialog(self)
        if dlg.exec_() != QDialog.Accepted:
            return
        path, year, month = dlg.values()
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, 'No file',
                                'Choose an Alarms report export first.')
            return
        self.import_btn.setEnabled(False)
        self.rebuild_btn.setEnabled(False)
        self.import_status.setVisible(True)
        self.import_status.setText('Starting…')
        self._worker = _ImportWorker(self._project_id, path, year, month, self)
        self._worker.progress.connect(self.import_status.setText)
        self._worker.done.connect(self._on_import_done)
        self._worker.failed.connect(self._on_import_failed)
        self._worker.start()

    def _on_import_done(self, stats):
        self.import_btn.setEnabled(True)
        self.rebuild_btn.setEnabled(True)
        self.import_status.setVisible(False)
        self._rebuild(quiet=True)
        QMessageBox.information(
            self, 'Alarm history imported',
            "{inserted:,} new event(s) stored.\n"
            "{unparsed:,} plant-level tag(s) had no asset code and were "
            "skipped.\n{exclusions} availability exclusion(s) applied.".format(
                **stats))

    def _on_import_failed(self, msg):
        self.import_btn.setEnabled(True)
        self.rebuild_btn.setEnabled(True)
        self.import_status.setVisible(False)
        QMessageBox.critical(self, 'Import failed', msg)

    def _rebuild(self, quiet=False):
        if not self._project_id:
            return
        # A block is 2 LCs of 2 PCS units each — 4 units per block. Capacity
        # comes from the project's own configuration, never a hard-coded plant.
        blk_kwh = 0.0
        try:
            import services.report_workflow_service as rw
            blk_kwh = float(
                (rw.get_project_config(self._project_id) or {})
                .get('per_block_capacity_mw') or 0) * 1000.0
        except Exception:                            # noqa: BLE001
            blk_kwh = 0.0
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            made = ats.rebuild_asset_tree(
                self._project_id,
                unit_capacity_kwh=(blk_kwh / 4.0) if blk_kwh else None,
                lc_capacity_kwh=(blk_kwh / 2.0) if blk_kwh else None)
        finally:
            QApplication.restoreOverrideCursor()
        self._load_tree()
        self._refresh_freshness()
        if not quiet:
            QMessageBox.information(
                self, 'Tree rebuilt',
                'Added {block} block(s), {lc} LC(s), {unit} unit(s).\n'
                'Existing records were left untouched.'.format(**made))
