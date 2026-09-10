"""
ui/planner_page.py
-------------------
Work planner — the forward half of the app.

Three views over one list of jobs:
  Schedule   what is planned for a month or a day, filtered by type
  PM due     which blocks are overdue, so a campaign starts where it hurts
  Campaigns  the rolling PM rounds and imported plans

Marking a PM job done writes the `pm_activities` row the monthly report reads,
so the plan is the source of the report's PM hours rather than a separate list
somebody retypes at month end.
"""

import calendar
import datetime
import os

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QTabWidget, QMessageBox,
    QAbstractItemView, QDialog, QDialogButtonBox, QSpinBox, QDoubleSpinBox,
    QDateEdit, QCheckBox, QFileDialog, QTextEdit, QFormLayout, QGroupBox,
    QApplication, QHeaderView,
)
from PyQt5.QtCore import Qt, QDate
from PyQt5.QtGui import QColor

from ui.components import (PageHeader, PrimaryButton, SecondaryButton,
                           make_table)
from services.project_service import get_all_projects, get_project_by_id
import services.planner_service as pl

MONTHS = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
          'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

STATUS_COLOUR = {
    'planned':     '#33465E',
    'in_progress': '#8A6D1F',
    'done':        '#1E8E3E',
    'skipped':     '#9AA6B4',
    'moved':       '#8A6D1F',
}


def _month_bounds(year, month):
    last = calendar.monthrange(int(year), int(month))[1]
    return (f'{int(year):04d}-{int(month):02d}-01',
            f'{int(year):04d}-{int(month):02d}-{last:02d}')


# ── Dialogs ──────────────────────────────────────────────────────────────────

class _CampaignDialog(QDialog):
    """A PM round is generated, not typed: pick the blocks and the pace."""

    def __init__(self, project_id, n_blocks, due, types, parent=None):
        super().__init__(parent)
        self._due = due
        self.setWindowTitle('New PM campaign')
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        intro = QLabel(
            'The plant is maintained as a rolling round — a couple of blocks a '
            'day until every block is covered. Set the pace and the schedule '
            'is generated; you are not typing 70 rows.')
        intro.setWordWrap(True)
        intro.setStyleSheet('color:#6B7A8D;')
        lay.addWidget(intro)

        form = QFormLayout()
        self.title = QLineEdit(f'PM round — {datetime.date.today().strftime("%B %Y")}')
        form.addRow('Title:', self.title)

        self.kind = QComboBox()
        n_never = sum(1 for r in due if r['never'])
        self.kind.addItem(f'Overdue first ({n_never} block(s) never done)', 'due')
        self.kind.addItem(f'All {n_blocks} blocks in order', 'all')
        self.kind.addItem('Only the blocks I list', 'custom')
        self.kind.currentIndexChanged.connect(self._on_kind)
        form.addRow('Blocks:', self.kind)

        self.blocks = QLineEdit()
        self.blocks.setPlaceholderText('e.g. 4, 17-20, 33')
        self.blocks.setEnabled(False)
        form.addRow('', self.blocks)

        self.start = QDateEdit(QDate.currentDate())
        self.start.setCalendarPopup(True)
        self.start.setDisplayFormat('yyyy-MM-dd')
        form.addRow('Start:', self.start)

        self.per_day = QSpinBox(); self.per_day.setRange(1, 20); self.per_day.setValue(2)
        form.addRow('Blocks per day:', self.per_day)

        self.hours = QDoubleSpinBox(); self.hours.setRange(0, 48)
        self.hours.setDecimals(2); self.hours.setValue(4.0); self.hours.setSuffix(' h')
        form.addRow('Hours per block:', self.hours)

        self.skip_we = QCheckBox('Skip weekends')
        self.skip_we.setChecked(True)
        form.addRow('', self.skip_we)

        self.type_combo = QComboBox()
        for t in types:
            self.type_combo.addItem(t['label'], t['code'])
        i = self.type_combo.findData('pm')
        if i >= 0:
            self.type_combo.setCurrentIndex(i)
        form.addRow('Work type:', self.type_combo)

        self.assignee = QLineEdit()
        self.assignee.setPlaceholderText('optional')
        form.addRow('Assignee:', self.assignee)
        lay.addLayout(form)

        self.preview = QLabel('')
        self.preview.setWordWrap(True)
        self.preview.setStyleSheet(
            'background:#F5F8FC;border:1px solid #DDE6F0;border-radius:6px;'
            'padding:8px 10px;color:#33465E;font-size:11px;')
        lay.addWidget(self.preview)
        for w in (self.per_day, self.hours):
            w.valueChanged.connect(self._refresh)
        self.kind.currentIndexChanged.connect(self._refresh)
        self.start.dateChanged.connect(self._refresh)
        self.skip_we.toggled.connect(self._refresh)
        self.blocks.textChanged.connect(self._refresh)
        self._n_blocks = n_blocks
        self._refresh()

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Ok).setText('Create')
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    def _on_kind(self):
        self.blocks.setEnabled(self.kind.currentData() == 'custom')

    def blocks_list(self):
        mode = self.kind.currentData()
        if mode == 'all':
            return list(range(1, self._n_blocks + 1))
        if mode == 'due':
            return [r['block'] for r in self._due]
        return pl._parse_blocks(self.blocks.text())

    def _refresh(self):
        bl = self.blocks_list()
        if not bl:
            self.preview.setText('No blocks selected.')
            return
        per = max(1, self.per_day.value())
        days = (len(bl) + per - 1) // per
        span = days if not self.skip_we.isChecked() else int(days * 7 / 5) + 1
        end = self.start.date().addDays(span - 1)
        self.preview.setText(
            f'{len(bl)} block(s) · {per}/day · {days} working day(s) · '
            f'finishes about {end.toString("yyyy-MM-dd")} · '
            f'{len(bl) * self.hours.value():,.1f} h of work in total.')

    def values(self):
        return {
            'title': self.title.text().strip() or 'PM round',
            'blocks': self.blocks_list(),
            'start_date': self.start.date().toString('yyyy-MM-dd'),
            'blocks_per_day': self.per_day.value(),
            'hours_per_block': self.hours.value(),
            'skip_weekends': self.skip_we.isChecked(),
            'type_code': self.type_combo.currentData(),
            'assignee': self.assignee.text().strip(),
        }


class _AddJobDialog(QDialog):
    """One job — a fault to chase today, a report to write, an inspection.

    The campaign generator covers PM rounds; everything else in an O&M day is
    a single entry, and until this existed there was no way to record one.
    """

    def __init__(self, types, default_type=None, default_date=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Add a job')
        self.setMinimumWidth(480)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        form = QFormLayout()
        self.type_combo = QComboBox()
        for t in types:
            self.type_combo.addItem(t['label'], t['code'])
        i = self.type_combo.findData(default_type or 'corrective')
        if i < 0:
            i = self.type_combo.findData('corrective')
        self.type_combo.setCurrentIndex(max(0, i))
        form.addRow('Type:', self.type_combo)

        self.title = QLineEdit()
        self.title.setPlaceholderText('e.g. Monthly report to the customer')
        form.addRow('Job *:', self.title)

        d = QDate.fromString(default_date, 'yyyy-MM-dd') if default_date else QDate.currentDate()
        self.date = QDateEdit(d if d.isValid() else QDate.currentDate())
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat('yyyy-MM-dd')
        form.addRow('Date:', self.date)

        self.block = QLineEdit()
        self.block.setPlaceholderText('optional — plant block number')
        form.addRow('Block:', self.block)

        self.hours = QDoubleSpinBox(); self.hours.setRange(0, 240)
        self.hours.setDecimals(2); self.hours.setSuffix(' h')
        form.addRow('Planned:', self.hours)

        self.assignee = QLineEdit()
        self.assignee.setPlaceholderText('optional')
        form.addRow('Assignee:', self.assignee)

        self.priority = QComboBox()
        for lbl, val in (('High', 1), ('Normal', 2), ('Low', 3)):
            self.priority.addItem(lbl, val)
        self.priority.setCurrentIndex(1)
        form.addRow('Priority:', self.priority)

        self.notes = QTextEdit(); self.notes.setMaximumHeight(60)
        self.notes.setPlaceholderText('optional detail')
        form.addRow('Detail:', self.notes)
        lay.addLayout(form)

        self.hint = QLabel('')
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet('color:#6B7A8D;font-size:11px;')
        lay.addWidget(self.hint)
        self.type_combo.currentIndexChanged.connect(self._on_type)
        self._types = {t['code']: t for t in types}
        self._on_type()

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Ok).setText('Add')
        box.accepted.connect(self.accept); box.rejected.connect(self.reject)
        lay.addWidget(box)

    def _on_type(self):
        t = self._types.get(self.type_combo.currentData(), {})
        if t.get('counts_as_downtime'):
            self.hint.setText('This type counts as unavailability — the hours '
                              'you record on completion go into the monthly '
                              'report and reduce availability.')
        else:
            self.hint.setText('This type does not affect availability — it is '
                              'here to plan and track the work, nothing more.')

    def values(self):
        blk = self.block.text().strip()
        return {
            'type_code': self.type_combo.currentData(),
            'title': self.title.text().strip(),
            'description': self.notes.toPlainText().strip(),
            'planned_date': self.date.date().toString('yyyy-MM-dd'),
            'block': int(blk) if blk.isdigit() else None,
            'planned_hours': self.hours.value(),
            'assignee': self.assignee.text().strip(),
            'priority': self.priority.currentData(),
        }


class _CompleteDialog(QDialog):
    """Actual date and actual hours — the two numbers the customer asks for."""

    def __init__(self, item, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Mark as done')
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        head = QLabel(f"<b>{item.get('title') or 'Job'}</b>")
        head.setWordWrap(True)
        lay.addWidget(head)
        sub = QLabel('Planned {} for {} h{}'.format(
            item.get('planned_date') or '—', item.get('planned_hours') or 0,
            f" · block {item['block']}" if item.get('block') else ''))
        sub.setStyleSheet('color:#6B7A8D;font-size:11px;')
        lay.addWidget(sub)

        form = QFormLayout()
        d = item.get('planned_date') or datetime.date.today().isoformat()
        self.date = QDateEdit(QDate.fromString(d, 'yyyy-MM-dd'))
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat('yyyy-MM-dd')
        form.addRow('Actual date:', self.date)
        self.hours = QDoubleSpinBox(); self.hours.setRange(0, 240)
        self.hours.setDecimals(2); self.hours.setSuffix(' h')
        self.hours.setValue(float(item.get('planned_hours') or 0))
        form.addRow('Actual hours:', self.hours)
        self.notes = QTextEdit(); self.notes.setMaximumHeight(70)
        self.notes.setPlaceholderText('anything worth knowing next time…')
        form.addRow('Notes:', self.notes)
        lay.addLayout(form)

        if item.get('counts_as_downtime'):
            note = QLabel('This type counts as unavailability — saving records '
                          'the downtime for the monthly report.')
            note.setWordWrap(True)
            note.setStyleSheet('color:#8A6D1F;font-size:11px;')
            lay.addWidget(note)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept); box.rejected.connect(self.reject)
        lay.addWidget(box)

    def values(self):
        return (self.date.date().toString('yyyy-MM-dd'), self.hours.value(),
                self.notes.toPlainText().strip())


class _ImportDialog(QDialog):
    """Bring a plan in from a spreadsheet, showing what maps to what first."""

    def __init__(self, types, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Import a plan from Excel')
        self.setMinimumSize(700, 460)
        self._preview = None
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        row = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setPlaceholderText('plan.xlsx')
        row.addWidget(self.path, 1)
        b = SecondaryButton('Browse…'); b.clicked.connect(self._browse)
        row.addWidget(b)
        lay.addLayout(row)

        self.info = QLabel('Choose a file — the columns are matched automatically, '
                           'and you can correct the match below.')
        self.info.setWordWrap(True)
        self.info.setStyleSheet('color:#6B7A8D;font-size:11px;')
        lay.addWidget(self.info)

        self.map_tbl = QTableWidget(0, 2)
        self.map_tbl.setHorizontalHeaderLabels(['Column in the file', 'Means'])
        self.map_tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.map_tbl.verticalHeader().setVisible(False)
        lay.addWidget(self.map_tbl, 1)

        form = QFormLayout()
        self.title = QLineEdit()
        self.title.setPlaceholderText('e.g. October PM (imported)')
        form.addRow('Plan name:', self.title)
        self.type_combo = QComboBox()
        for t in types:
            self.type_combo.addItem(t['label'], t['code'])
        i = self.type_combo.findData('pm')
        if i >= 0:
            self.type_combo.setCurrentIndex(i)
        form.addRow('Type for rows that do not say:', self.type_combo)
        lay.addLayout(form)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Ok).setText('Import')
        box.accepted.connect(self.accept); box.rejected.connect(self.reject)
        lay.addWidget(box)

    FIELDS = [('', '— ignore —'), ('block', 'Block'), ('planned_date', 'Planned date'),
              ('planned_hours', 'Planned hours'), ('title', 'Task / description'),
              ('type_code', 'Work type'), ('assignee', 'Assignee'),
              ('actual_date', 'Actual date'), ('actual_hours', 'Actual hours')]

    def _browse(self):
        f, _ = QFileDialog.getOpenFileName(self, 'Plan', '', 'Excel (*.xlsx *.xls)')
        if not f:
            return
        self.path.setText(f)
        try:
            self._preview = pl.preview_excel(f)
        except Exception as e:                        # noqa: BLE001
            QMessageBox.warning(self, 'Cannot read', str(e))
            return
        pv = self._preview
        self.info.setText(f"{pv['rows']} row(s). Matched "
                          f"{len(pv['mapping'])} of {len(pv['columns'])} columns — "
                          f"correct anything that is wrong.")
        self.map_tbl.setRowCount(0)
        for col in pv['columns']:
            i = self.map_tbl.rowCount()
            self.map_tbl.insertRow(i)
            self.map_tbl.setItem(i, 0, QTableWidgetItem(str(col)))
            cb = QComboBox()
            for code, label in self.FIELDS:
                cb.addItem(label, code)
            want = pv['mapping'].get(col, '')
            j = cb.findData(want)
            cb.setCurrentIndex(j if j >= 0 else 0)
            self.map_tbl.setCellWidget(i, 1, cb)
        if not self.title.text().strip():
            self.title.setText(os.path.splitext(os.path.basename(f))[0])

    def values(self):
        mapping = {}
        for i in range(self.map_tbl.rowCount()):
            col = self.map_tbl.item(i, 0).text()
            cb = self.map_tbl.cellWidget(i, 1)
            code = cb.currentData() if cb else ''
            if code:
                mapping[col] = code
        return (self.path.text().strip(), mapping,
                self.title.text().strip(), self.type_combo.currentData())


class _TypeDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Add a work type')
        self.setMinimumWidth(400)
        lay = QVBoxLayout(self)
        note = QLabel('For work this project does that the standard types do '
                      'not cover — commissioning, for instance.')
        note.setWordWrap(True); note.setStyleSheet('color:#6B7A8D;font-size:11px;')
        lay.addWidget(note)
        form = QFormLayout()
        self.label = QLineEdit(); self.label.setPlaceholderText('Commissioning')
        form.addRow('Name:', self.label)
        self.downtime = QCheckBox('Counts as unavailability in the monthly report')
        form.addRow('', self.downtime)
        lay.addLayout(form)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept); box.rejected.connect(self.reject)
        lay.addWidget(box)

    def values(self):
        lbl = self.label.text().strip()
        return lbl, lbl.lower().replace(' ', '_'), self.downtime.isChecked()


# ── Page ─────────────────────────────────────────────────────────────────────

class PlannerPage(QWidget):
    """Scoped to the shell's current project."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project_id = None
        self._items = []
        self._build_ui()
        self._load_projects()

    # ── build ────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = PageHeader('Planner', 'PM campaigns · daily work · plan vs actual')
        b = PrimaryButton('＋  Add job…')
        b.setToolTip('One job — a fault to chase, a report to write, an '
                     'inspection. For a PM round use the campaign button.')
        b.clicked.connect(self._add_job)
        header.add_action(b)
        b = SecondaryButton('＋  New PM campaign…')
        b.clicked.connect(self._new_campaign)
        header.add_action(b)
        b = SecondaryButton('⤓  Import Excel…')
        b.clicked.connect(self._import_excel)
        header.add_action(b)
        b = SecondaryButton('⤒  Export for customer…')
        b.setToolTip('Block, date and duration only — nothing else leaves here.')
        b.clicked.connect(self._export_customer)
        header.add_action(b)
        root.addWidget(header)

        bar = QWidget()
        bar.setStyleSheet('background:#FFFFFF;border-bottom:1px solid #E0E4EA;')
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(24, 10, 24, 10)
        bl.setSpacing(10)
        bl.addWidget(QLabel('Project:'))
        self.project_combo = QComboBox(); self.project_combo.setMinimumWidth(210)
        self.project_combo.currentIndexChanged.connect(self._on_project_changed)
        bl.addWidget(self.project_combo)

        bl.addSpacing(12)
        bl.addWidget(QLabel('Month:'))
        self.month = QComboBox()
        for i in range(1, 13):
            self.month.addItem(MONTHS[i], i)
        self.month.setCurrentIndex(datetime.date.today().month - 1)
        self.month.currentIndexChanged.connect(self._reload)
        bl.addWidget(self.month)
        self.year = QSpinBox(); self.year.setRange(2020, 2100)
        self.year.setValue(datetime.date.today().year)
        self.year.valueChanged.connect(self._reload)
        bl.addWidget(self.year)

        bl.addSpacing(12)
        bl.addWidget(QLabel('Type:'))
        self.type_filter = QComboBox(); self.type_filter.setMinimumWidth(150)
        self.type_filter.currentIndexChanged.connect(self._reload)
        bl.addWidget(self.type_filter)
        add_t = SecondaryButton('＋ type')
        add_t.setFixedWidth(74)
        add_t.clicked.connect(self._add_type)
        bl.addWidget(add_t)

        bl.addWidget(QLabel('Status:'))
        self.status_filter = QComboBox()
        self.status_filter.addItem('All', None)
        for s in ('planned', 'in_progress', 'done', 'skipped'):
            self.status_filter.addItem(s.replace('_', ' ').title(), s)
        self.status_filter.currentIndexChanged.connect(self._reload)
        bl.addWidget(self.status_filter)
        bl.addStretch()
        root.addWidget(bar)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_schedule(), 'Schedule')
        self.tabs.addTab(self._tab_due(), 'PM due')
        self.tabs.addTab(self._tab_plans(), 'Campaigns')
        self.tabs.currentChanged.connect(self._on_tab)
        root.addWidget(self.tabs, 1)

        self.summary = QLabel('')
        self.summary.setStyleSheet(
            'background:#FFFFFF;border-top:1px solid #E0E4EA;'
            'padding:8px 24px;color:#33465E;font-size:12px;')
        root.addWidget(self.summary)

    def _tab_schedule(self):
        w = QWidget(); lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 12, 20, 12); lay.setSpacing(8)
        self.tbl = make_table(['Date', 'Type', 'Block', 'Job', 'Assignee',
                               'Planned h', 'Actual date', 'Actual h', 'Status'])
        self.tbl.doubleClicked.connect(self._complete_selected)
        lay.addWidget(self.tbl, 1)
        # An empty grid tells you nothing about *why* it is empty — usually a
        # filter, sometimes simply that nothing has been planned yet.
        self.empty_hint = QLabel('')
        self.empty_hint.setWordWrap(True)
        self.empty_hint.setAlignment(Qt.AlignCenter)
        self.empty_hint.setStyleSheet(
            'color:#6B7A8D;font-size:12px;padding:18px;background:#FFFFFF;'
            'border:1px dashed #D5DEE8;border-radius:8px;')
        self.empty_hint.setVisible(False)
        lay.addWidget(self.empty_hint)
        row = QHBoxLayout()
        for label, slot, primary in (
            ('✓  Mark done…', self._complete_selected, True),
            ('↷  Move to…', self._move_selected, False),
            ('↺  Reopen', self._reopen_selected, False),
            ('🗑  Delete', self._delete_selected, False),
        ):
            btn = (PrimaryButton if primary else SecondaryButton)(label)
            btn.clicked.connect(slot)
            row.addWidget(btn)
        row.addStretch()
        hint = QLabel('Double-click a row to record what actually happened.')
        hint.setStyleSheet('color:#6B7A8D;font-size:11px;')
        row.addWidget(hint)
        lay.addLayout(row)
        return w

    def _tab_due(self):
        w = QWidget(); lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 12, 20, 12); lay.setSpacing(8)
        cap = QLabel('When each block last had PM, oldest first — including PM '
                     'recorded before the planner existed, which for this plant '
                     'sits in the scheduled-maintenance exclusions. A campaign '
                     'should start here, not at block 1.')
        cap.setWordWrap(True); cap.setStyleSheet('color:#6B7A8D;font-size:11px;')
        lay.addWidget(cap)
        row = QHBoxLayout()
        row.addWidget(QLabel('PM interval:'))
        self.interval = QSpinBox(); self.interval.setRange(7, 730)
        self.interval.setValue(90); self.interval.setSuffix(' days')
        self.interval.valueChanged.connect(self._load_due)
        row.addWidget(self.interval)
        row.addStretch()
        lay.addLayout(row)
        self.due_tbl = make_table(['Block', 'Last PM', 'Days ago', 'Overdue by'])
        lay.addWidget(self.due_tbl, 1)
        return w

    def _tab_plans(self):
        w = QWidget(); lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 12, 20, 12); lay.setSpacing(8)
        self.plan_tbl = make_table(['Plan', 'Kind', 'From', 'To', 'Jobs',
                                    'Done', 'Status'])
        lay.addWidget(self.plan_tbl, 1)
        row = QHBoxLayout(); row.addStretch()
        d = SecondaryButton('🗑  Delete campaign')
        d.clicked.connect(self._delete_plan)
        row.addWidget(d)
        lay.addLayout(row)
        return w

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
        i = self.project_combo.findData(project_id)
        if i < 0:
            self._load_projects()
            i = self.project_combo.findData(project_id)
        if i >= 0 and i != self.project_combo.currentIndex():
            self.project_combo.setCurrentIndex(i)
        elif i >= 0:
            self._on_project_changed()

    def _on_project_changed(self):
        self._project_id = self.project_combo.currentData()
        self._reload_types()
        self._reload()

    def _reload_types(self):
        cur = self.type_filter.currentData()
        self.type_filter.blockSignals(True)
        self.type_filter.clear()
        self.type_filter.addItem('All types', None)
        for t in pl.get_types(self._project_id):
            self.type_filter.addItem(t['label'], t['code'])
        i = self.type_filter.findData(cur)
        self.type_filter.setCurrentIndex(max(0, i))
        self.type_filter.blockSignals(False)

    def _n_blocks(self):
        p = get_project_by_id(self._project_id) if self._project_id else None
        return int(getattr(p, 'num_blocks', 0) or 0) or 70

    # ── loading ──────────────────────────────────────────────────────────
    def _reload(self):
        if not self._project_id:
            return
        d0, d1 = _month_bounds(self.year.value(), self.month.currentData())
        self._items = pl.get_items(
            self._project_id, date_from=d0, date_to=d1,
            type_code=self.type_filter.currentData(),
            status=self.status_filter.currentData())
        self._fill_schedule()
        self._fill_summary()
        if self.tabs.currentIndex() == 1:
            self._load_due()
        elif self.tabs.currentIndex() == 2:
            self._load_plans()

    def _on_tab(self, i):
        if i == 1:
            self._load_due()
        elif i == 2:
            self._load_plans()

    def _fill_schedule(self):
        self.tbl.setRowCount(0)
        for it in self._items:
            r = self.tbl.rowCount()
            self.tbl.insertRow(r)
            vals = [it.get('planned_date') or '',
                    it.get('type_label') or it.get('type_code') or '',
                    str(it['block']) if it.get('block') else '',
                    it.get('title') or '',
                    it.get('assignee') or '',
                    f"{float(it.get('planned_hours') or 0):g}",
                    it.get('actual_date') or '',
                    f"{float(it['actual_hours']):g}" if it.get('actual_hours') is not None else '',
                    (it.get('status') or '').replace('_', ' ')]
            for c, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                if c in (2, 5, 7):
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.tbl.setItem(r, c, cell)
            col = STATUS_COLOUR.get(it.get('status'), '#33465E')
            self.tbl.item(r, 8).setForeground(QColor(col))
            if it.get('moved_from'):
                self.tbl.item(r, 0).setToolTip(
                    f"first planned for {it['moved_from']}")
                self.tbl.item(r, 0).setForeground(QColor('#8A6D1F'))
            self.tbl.item(r, 0).setData(Qt.UserRole, it)
        self._show_empty_hint()

    def _show_empty_hint(self):
        if self.tbl.rowCount():
            self.empty_hint.setVisible(False)
            return
        filters = []
        if self.type_filter.currentData():
            filters.append(f'type “{self.type_filter.currentText()}”')
        if self.status_filter.currentData():
            filters.append(f'status “{self.status_filter.currentText()}”')
        month = f'{MONTHS[self.month.currentData()]} {self.year.value()}'
        if filters:
            self.empty_hint.setText(
                f'Nothing in {month} matches {" and ".join(filters)}.\n'
                'Widen the filters above, or add a job.')
        else:
            self.empty_hint.setText(
                f'Nothing planned for {month} yet.\n\n'
                '“＋ Add job…” records one piece of work — a fault to chase, a '
                'report to write.  “＋ New PM campaign…” lays a whole PM round '
                'across the calendar.  “⤓ Import Excel…” brings in a plan you '
                'already have.')
        self.empty_hint.setVisible(True)

    def _fill_summary(self):
        if not self._project_id:
            self.summary.setText('')
            return
        y, m = self.year.value(), self.month.currentData()
        cost = pl.plan_availability_cost(self._project_id, y, m)
        n = len(self._items)
        done = sum(1 for i in self._items if i['status'] == 'done')
        moved = sum(1 for i in self._items if i.get('moved_from'))
        bits = [f'{n} job(s) this month', f'{done} done']
        if moved:
            bits.append(f'{moved} rescheduled')
        if cost['planned_hours']:
            bits.append(
                f"downtime planned {cost['planned_hours']:g} h "
                f"({cost['planned_cost_pct']:.3f}% of availability), "
                f"recorded {cost['actual_hours']:g} h")
        self.summary.setText('   ·   '.join(bits))

    def _load_due(self):
        if not self._project_id:
            return
        due = pl.pm_due(self._project_id, list(range(1, self._n_blocks() + 1)),
                        interval_days=self.interval.value())
        self.due_tbl.setRowCount(0)
        for r in due:
            i = self.due_tbl.rowCount()
            self.due_tbl.insertRow(i)
            over = r['overdue_by']
            vals = [str(r['block']), r['last_pm'] or 'never',
                    str(r['days_since']) if r['days_since'] is not None else '—',
                    (f"{over} d" if (over is not None and over > 0) else
                     ('—' if over is not None else 'unknown'))]
            for c, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                if c != 1:
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.due_tbl.setItem(i, c, cell)
            if r['never'] or (over is not None and over > 0):
                for c in range(4):
                    self.due_tbl.item(i, c).setForeground(QColor('#B4232A'))

    def _load_plans(self):
        if not self._project_id:
            return
        self.plan_tbl.setRowCount(0)
        for p in pl.get_plans(self._project_id):
            i = self.plan_tbl.rowCount()
            self.plan_tbl.insertRow(i)
            vals = [p['title'], p.get('kind') or '', p.get('date_from') or '',
                    p.get('date_to') or '', str(p.get('n_items') or 0),
                    str(p.get('n_done') or 0), p.get('status') or '']
            for c, v in enumerate(vals):
                self.plan_tbl.setItem(i, c, QTableWidgetItem(str(v)))
            self.plan_tbl.item(i, 0).setData(Qt.UserRole, p)

    # ── actions ──────────────────────────────────────────────────────────
    def _selected_item(self):
        rows = self.tbl.selectionModel().selectedRows() if self.tbl.selectionModel() else []
        if not rows:
            QMessageBox.information(self, 'Nothing selected', 'Pick a job first.')
            return None
        return self.tbl.item(rows[0].row(), 0).data(Qt.UserRole)

    def _complete_selected(self):
        it = self._selected_item()
        if not it:
            return
        dlg = _CompleteDialog(it, self)
        if dlg.exec_() != QDialog.Accepted:
            return
        date, hours, notes = dlg.values()
        out = pl.complete_item(it['id'], actual_date=date, actual_hours=hours,
                               notes=notes)
        self._reload()
        if out['wrote_downtime']:
            self.summary.setText(
                self.summary.text() + '   ·   downtime recorded for the monthly report')

    def _reopen_selected(self):
        it = self._selected_item()
        if not it:
            return
        pl.reopen_item(it['id'])
        self._reload()

    def _move_selected(self):
        it = self._selected_item()
        if not it:
            return
        dlg = QDialog(self); dlg.setWindowTitle('Move to')
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel('The job keeps the date it was first planned for, '
                             'so slippage stays visible.'))
        de = QDateEdit(QDate.fromString(it.get('planned_date') or
                                        datetime.date.today().isoformat(),
                                        'yyyy-MM-dd'))
        de.setCalendarPopup(True); de.setDisplayFormat('yyyy-MM-dd')
        lay.addWidget(de)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(dlg.accept); box.rejected.connect(dlg.reject)
        lay.addWidget(box)
        if dlg.exec_() == QDialog.Accepted:
            pl.move_item(it['id'], de.date().toString('yyyy-MM-dd'))
            self._reload()

    def _delete_selected(self):
        it = self._selected_item()
        if not it:
            return
        if QMessageBox.question(
                self, 'Delete', f"Delete “{it.get('title')}”?\n"
                "Any downtime it recorded goes with it.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        pl.delete_item(it['id'])
        self._reload()

    def _add_job(self):
        if not self._project_id:
            return
        # default to whatever the type filter is showing, and to a date inside
        # the month being looked at — otherwise the new job lands out of view
        y, m = self.year.value(), self.month.currentData()
        today = datetime.date.today()
        default_date = (today.isoformat() if (today.year, today.month) == (y, m)
                        else f'{y:04d}-{m:02d}-01')
        dlg = _AddJobDialog(pl.get_types(self._project_id),
                            default_type=self.type_filter.currentData(),
                            default_date=default_date, parent=self)
        if dlg.exec_() != QDialog.Accepted:
            return
        v = dlg.values()
        if not v['title']:
            QMessageBox.warning(self, 'Needs a name',
                                'Give the job a short name.')
            return
        pl.add_item(self._project_id, **v)
        # make sure the new job is actually visible: jump to its month and
        # drop a status filter that would hide it
        d = v['planned_date']
        self.year.blockSignals(True); self.month.blockSignals(True)
        self.year.setValue(int(d[:4]))
        self.month.setCurrentIndex(int(d[5:7]) - 1)
        self.year.blockSignals(False); self.month.blockSignals(False)
        if self.status_filter.currentData() not in (None, 'planned'):
            self.status_filter.setCurrentIndex(0)
        self._reload()

    def _new_campaign(self):
        if not self._project_id:
            return
        n = self._n_blocks()
        due = pl.pm_due(self._project_id, list(range(1, n + 1)))
        dlg = _CampaignDialog(self._project_id, n, due,
                              pl.get_types(self._project_id), self)
        if dlg.exec_() != QDialog.Accepted:
            return
        v = dlg.values()
        if not v['blocks']:
            QMessageBox.warning(self, 'No blocks', 'Nothing to schedule.')
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            res = pl.generate_pm_campaign(self._project_id, **v)
        finally:
            QApplication.restoreOverrideCursor()
        self._reload()
        QMessageBox.information(
            self, 'Campaign created',
            f"{res['items']} job(s) scheduled from {res['date_from']} "
            f"to {res['date_to']}.")

    def _import_excel(self):
        if not self._project_id:
            return
        dlg = _ImportDialog(pl.get_types(self._project_id), self)
        if dlg.exec_() != QDialog.Accepted:
            return
        path, mapping, title, default_type = dlg.values()
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, 'No file', 'Choose a spreadsheet first.')
            return
        if 'planned_date' not in mapping.values():
            QMessageBox.warning(self, 'No date column',
                                'Point one column at "Planned date" — a job '
                                'without a date cannot be scheduled.')
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            res = pl.import_from_excel(self._project_id, path, mapping,
                                       plan_title=title or None,
                                       default_type=default_type,
                                       log=lambda m: None)
        except Exception as e:                        # noqa: BLE001
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, 'Import failed', str(e))
            return
        QApplication.restoreOverrideCursor()
        self._reload()
        msg = [f"{res['imported']} job(s) imported.",
               f"{res['completed']} already had an actual date — their downtime "
               f"was recorded." if res.get('completed') else '',
               f"{res['skipped']} blank row(s) skipped." if res['skipped'] else '']
        if res['problems']:
            msg.append('')
            msg.append(f"{len(res['problems'])} row(s) could not be used:")
            msg += ['   • ' + p for p in res['problems'][:8]]
        QMessageBox.information(self, 'Imported',
                                '\n'.join(m for m in msg if m))

    def _export_customer(self):
        if not self._project_id:
            return
        y, m = self.year.value(), self.month.currentData()
        default = f'PM {MONTHS[m]} {y}.xlsx'
        path, _ = QFileDialog.getSaveFileName(
            self, 'Save the customer sheet', default, 'Excel (*.xlsx)')
        if not path:
            return
        try:
            res = pl.export_customer_excel(self._project_id, y, m, path)
        except Exception as e:                        # noqa: BLE001
            QMessageBox.critical(self, 'Export failed', str(e))
            return
        QMessageBox.information(
            self, 'Saved',
            f"{res['rows']} completed PM job(s), {res['total_hours']:g} h in total.\n\n"
            f"Block, date and duration only — assignees, priorities and what "
            f"slipped stay in the app.\n\n{res['path']}")

    def _add_type(self):
        if not self._project_id:
            return
        dlg = _TypeDialog(self)
        if dlg.exec_() != QDialog.Accepted:
            return
        label, code, downtime = dlg.values()
        if not label:
            return
        pl.add_type(self._project_id, code, label, counts_as_downtime=downtime)
        self._reload_types()

    def _delete_plan(self):
        rows = (self.plan_tbl.selectionModel().selectedRows()
                if self.plan_tbl.selectionModel() else [])
        if not rows:
            return
        p = self.plan_tbl.item(rows[0].row(), 0).data(Qt.UserRole)
        ans = QMessageBox.question(
            self, 'Delete campaign',
            f"Delete “{p['title']}” and its {p.get('n_items', 0)} job(s)?\n\n"
            "Yes deletes the jobs too. No keeps them, unattached.",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Cancel)
        if ans == QMessageBox.Cancel:
            return
        pl.delete_plan(p['id'], delete_items=(ans == QMessageBox.Yes))
        self._load_plans()
        self._reload()
