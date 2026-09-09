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
from services.availability_service import EXCLUSION_TYPES
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


def _wrap_row(layout):
    """A layout as a widget, so a whole row can be shown or hidden at once."""
    w = QWidget()
    w.setLayout(layout)
    return w


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
            'Load SCADA "Alarms report" exports so their months become part of '
            'the equipment history. Re-importing a month you already have '
            'changes nothing, so this is always safe to run.')
        intro.setWordWrap(True)
        intro.setStyleSheet('color:#6B7A8D;')
        lay.addWidget(intro)

        self.mode = QComboBox()
        self.mode.addItem('One export file', 'file')
        self.mode.addItem('Every month in a folder (backfill)', 'folder')
        self.mode.currentIndexChanged.connect(self._on_mode)
        lay.addWidget(self.mode)

        row = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setPlaceholderText('Alarms report.XLSX')
        row.addWidget(self.path, 1)
        browse = SecondaryButton('Browse…')
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        lay.addLayout(row)

        self.found_lbl = QLabel('')
        self.found_lbl.setWordWrap(True)
        self.found_lbl.setStyleSheet('color:#33465E;font-size:11px;')
        self.found_lbl.setVisible(False)
        lay.addWidget(self.found_lbl)

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
        self.month_row = _wrap_row(my)
        lay.addWidget(self.month_row)

        note = QLabel(
            'Availability exclusions recorded for a month are applied to it, so '
            'grid outages and PM are not charged to the equipment.')
        note.setWordWrap(True)
        note.setStyleSheet('color:#6B7A8D;font-size:11px;')
        lay.addWidget(note)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    def _on_mode(self):
        folder = self.mode.currentData() == 'folder'
        # In folder mode the month is read from each file's own timestamps —
        # the folder names ("June 2026", "August", "LC data march") are not
        # something to trust.
        self.month_row.setVisible(not folder)
        self.found_lbl.setVisible(folder)
        self.path.setPlaceholderText(
            'Folder holding the monthly export folders' if folder
            else 'Alarms report.XLSX')
        self.path.clear()
        self.found_lbl.setText('')

    def _browse(self):
        if self.mode.currentData() == 'folder':
            d = QFileDialog.getExistingDirectory(
                self, 'Folder with the monthly exports')
            if d:
                self.path.setText(d)
                self._scan(d)
            return
        f, _ = QFileDialog.getOpenFileName(
            self, 'Alarms report', '', 'Excel (*.xlsx *.XLSX *.xls)')
        if f:
            self.path.setText(f)

    def _scan(self, folder):
        found = ats.find_alarm_exports(folder)
        if not found:
            self.found_lbl.setText(
                '⚠ No alarm exports found under that folder.')
            return
        names = '\n'.join('   • ' + os.path.relpath(f, folder) for f in found[:12])
        more = f'\n   … and {len(found) - 12} more' if len(found) > 12 else ''
        self.found_lbl.setText(
            f'{len(found)} export(s) found — the month of each is read from its '
            f'own timestamps:\n{names}{more}')

    def values(self):
        return (self.path.text().strip(), int(self.year.value()),
                int(self.month.currentData()), self.mode.currentData())


class _SuggestDialog(QDialog):
    """Grid outages found in the alarm history, for the operator to confirm."""

    def __init__(self, project_id, suggestions, parent=None):
        super().__init__(parent)
        self._pid = project_id
        self._rows = suggestions
        self.setWindowTitle('Grid outages found in the history')
        self.setMinimumSize(860, 460)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        intro = QLabel(
            'When most of the plant trips within the same minute, that is the '
            'grid going away — not seventy simultaneous equipment failures. '
            'These windows are visible in the alarm history but have no '
            'exclusion recorded, so right now the whole outage is charged to '
            'the equipment. Times are what the equipment actually saw: first '
            'trip to last recovery, which is usually a little wider than what '
            'gets written down by hand.')
        intro.setWordWrap(True)
        intro.setStyleSheet('color:#6B7A8D;')
        lay.addWidget(intro)

        self.tbl = QTableWidget(0, 7)
        self.tbl.setHorizontalHeaderLabels(
            ['Use', 'From', 'To', 'Blocks', 'Units', 'Alarms', 'What tripped'])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl.horizontalHeader().setStretchLastSection(True)
        for r in suggestions:
            i = self.tbl.rowCount()
            self.tbl.insertRow(i)
            chk = QTableWidgetItem()
            chk.setFlags(chk.flags() | Qt.ItemIsUserCheckable)
            chk.setCheckState(Qt.Checked)
            self.tbl.setItem(i, 0, chk)
            vals = [f"{r['date_from']} {r['time_from']}",
                    f"{r['date_to']} {r['time_to']}",
                    _fmt_blocks(r['blocks']),
                    str(r['units']), str(r['events']),
                    str(r['trigger'] or '')]
            for c, v in enumerate(vals, start=1):
                self.tbl.setItem(i, c, QTableWidgetItem(v))
        self.tbl.resizeColumnsToContents()
        lay.addWidget(self.tbl, 1)

        row = QHBoxLayout()
        row.addWidget(QLabel('Record as:'))
        self.kind = QComboBox()
        for t in EXCLUSION_TYPES:
            self.kind.addItem(t, t)
        i = self.kind.findData('Grid Outage')
        if i >= 0:
            self.kind.setCurrentIndex(i)
        row.addWidget(self.kind)
        row.addStretch()
        lay.addLayout(row)

        note = QLabel(
            'Confirming writes an availability exclusion for each ticked row '
            'and re-marks the alarms it covers, so those hours stop counting '
            'against the equipment. Untick anything that really was an '
            'equipment failure.')
        note.setWordWrap(True)
        note.setStyleSheet('color:#6B7A8D;font-size:11px;')
        lay.addWidget(note)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Ok).setText('Record these')
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    def chosen(self):
        out = []
        for i, r in enumerate(self._rows):
            if self.tbl.item(i, 0).checkState() == Qt.Checked:
                out.append(r)
        return out, self.kind.currentData()


class _ImportWorker(QThread):
    """Loading and classifying ~20 000 alarms takes seconds — keep it off the
    UI thread so the window never goes grey."""

    progress = pyqtSignal(str)
    done = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, project_id, path, year=None, month=None,
                 mode='file', parent=None):
        super().__init__(parent)
        self.project_id, self.path = project_id, path
        self.year, self.month, self.mode = year, month, mode

    def run(self):
        try:
            if self.mode == 'folder':
                files = ats.find_alarm_exports(self.path)
                if not files:
                    self.failed.emit('No alarm exports found under that folder.')
                    return
            else:
                files = [self.path]

            total = {'inserted': 0, 'unparsed': 0, 'skipped': 0,
                     'exclusions': 0, 'files': len(files),
                     'months': [], 'problems': []}
            for i, f in enumerate(files, 1):
                head = f'[{i}/{len(files)}] {os.path.basename(f)}: '
                try:
                    st = self._one(f, head)
                except Exception as e:               # noqa: BLE001
                    # One unreadable export must not abandon the rest.
                    total['problems'].append(f'{os.path.basename(f)} — {e}')
                    continue
                if st is None:
                    total['problems'].append(
                        f'{os.path.basename(f)} — no dated alarms in it')
                    continue
                for k in ('inserted', 'unparsed', 'skipped'):
                    total[k] += st[k]
                total['exclusions'] += st['exclusions']
                total['months'].append(st['period'] + (st['inserted'],))
            self.done.emit(total)
        except Exception as e:                       # noqa: BLE001
            self.failed.emit(str(e))

    def _one(self, path, head=''):
        from services.bukhara_report_service import (
            load_alarms, load_alarm_classifications,
            apply_alarm_classifications, tag_alarms_with_exclusions)
        from services import availability_service as av

        self.progress.emit(head + 'reading…')
        alarms = load_alarms(path)
        self.progress.emit(head + 'classifying…')
        # None → the packaged data/alarm_classifications.csv, resolved
        # relative to the install root rather than the working directory
        alarms = apply_alarm_classifications(
            alarms, load_alarm_classifications(None))

        if self.mode == 'folder':
            period = ats.detect_alarm_period(alarms)
            if not period:
                return None
            year, month = period[0], period[1]
        else:
            year, month = self.year, self.month

        self.progress.emit(
            head + f'{MONTH_NAMES[month]} {year} — applying exclusions…')
        exc = av.get_exclusions(project_id=self.project_id,
                                year=year, month=month) or []
        alarms = tag_alarms_with_exclusions(alarms, exc)
        self.progress.emit(head + f'{MONTH_NAMES[month]} {year} — storing…')
        st = ats.import_alarm_events(self.project_id, year, month, alarms,
                                     log=lambda m: None)
        st['exclusions'] = len(exc)
        st['period'] = (year, month)
        return st


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
        grid_btn = SecondaryButton('⚡  Find grid outages in the history…')
        grid_btn.setToolTip(
            'Most of the plant tripping at once is the grid, not the '
            'equipment. Finds those windows so they stop counting as faults.')
        grid_btn.clicked.connect(self._suggest_exclusions)
        row.addWidget(grid_btn)
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

    def _suggest_exclusions(self):
        """Find grid outages the history can prove, and record the confirmed ones."""
        if not self._project_id:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            sug = ats.suggest_exclusion_windows(self._project_id)
        finally:
            QApplication.restoreOverrideCursor()
        if not sug:
            QMessageBox.information(
                self, 'Nothing found',
                'No plant-wide events without an exclusion.\n\n'
                'Every alarm left in the list looks like a genuine equipment '
                'fault rather than the grid going down.')
            return

        dlg = _SuggestDialog(self._project_id, sug, self)
        if dlg.exec_() != QDialog.Accepted:
            return
        chosen, kind = dlg.chosen()
        if not chosen:
            return

        from services import availability_service as av
        made = 0
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for w in chosen:
                y, m = int(w['date_from'][:4]), int(w['date_from'][5:7])
                av.add_exclusion(
                    exclusion_type=kind,
                    date_from=w['date_from'], date_to=w['date_to'],
                    time_from=w['time_from'], time_to=w['time_to'],
                    affected_blocks=','.join(str(b) for b in w['blocks']),
                    description=f"Plant-wide event, {w['units']} units — "
                                f"found in the alarm history",
                    project_id=self._project_id, year=y, month=m)
                made += 1
            # Stored alarms carry their exclusion flag from import time, so
            # the new windows mean nothing until the flags are recomputed.
            st = ats.reapply_exclusions(self._project_id, log=lambda m: None)
        finally:
            QApplication.restoreOverrideCursor()

        self._load_tree()
        self._load_gaps()
        if self._current_code:
            self.show_asset(self._current_code)
        QMessageBox.information(
            self, 'Recorded',
            f"{made} exclusion window(s) recorded.\n"
            f"{st['changed']:,} alarm(s) no longer count against the "
            f"equipment.")

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
        path, year, month, mode = dlg.values()
        if not path or not os.path.exists(path):
            QMessageBox.warning(
                self, 'Nothing chosen',
                'Choose a folder first.' if mode == 'folder'
                else 'Choose an Alarms report export first.')
            return
        self.import_btn.setEnabled(False)
        self.rebuild_btn.setEnabled(False)
        self.import_status.setVisible(True)
        self.import_status.setText('Starting…')
        self._worker = _ImportWorker(self._project_id, path, year, month,
                                     mode, self)
        self._worker.progress.connect(self.import_status.setText)
        self._worker.done.connect(self._on_import_done)
        self._worker.failed.connect(self._on_import_failed)
        self._worker.start()

    def _on_import_done(self, stats):
        self.import_btn.setEnabled(True)
        self.rebuild_btn.setEnabled(True)
        self.import_status.setVisible(False)
        self._rebuild(quiet=True)

        lines = ["{inserted:,} new event(s) stored from {files} file(s)."
                 .format(**stats)]
        months = stats.get('months') or []
        if months:
            lines.append('')
            for y, m, n in sorted(months):
                lines.append(f'   {MONTH_NAMES[m]} {y}: {n:,} new'
                             + ('  (already had it)' if not n else ''))
        lines.append('')
        lines.append("{unparsed:,} plant-level tag(s) had no asset code."
                     .format(**stats))
        lines.append("{exclusions} availability exclusion(s) applied."
                     .format(**stats))
        problems = stats.get('problems') or []
        if problems:
            lines.append('')
            lines.append(f'{len(problems)} file(s) could not be read:')
            lines += ['   • ' + p for p in problems[:6]]
        QMessageBox.information(self, 'Alarm history imported',
                                '\n'.join(lines))

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
