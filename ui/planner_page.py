"""
ui/planner_page.py
-------------------
Work planner — the forward half of the app.

Views over one list of jobs, plus two lists that are not jobs:
  Schedule    what is planned for a month or a day, filtered by type
  PM due      which blocks are overdue, so a campaign starts where it hurts
  Campaigns   the rolling PM rounds and imported plans
  Checklists  the customer's PM checklists, per block
  Action list the office's organisational actions — no block, no hours, and
              deliberately outside the work journal so that nothing on it can
              reach the customer's monthly report

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
    QApplication, QHeaderView, QInputDialog,
)
from PyQt5.QtCore import Qt, QDate
from PyQt5.QtGui import QColor

from ui.components import (PageHeader, PrimaryButton, SecondaryButton,
                           make_table)
from services.project_service import get_all_projects, get_project_by_id
import services.planner_service as pl
import services.team_service as team

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

        # A named technician, not free text: the jobs can then be published to
        # that person's phone. Typing a name still works for someone who has
        # no account yet.
        self.assignee = QComboBox()
        self.assignee.setEditable(True)
        self.assignee.addItem('', '')
        for u in team.users():
            self.assignee.addItem(u['username'], u['id'])
        self.assignee.lineEdit().setPlaceholderText(
            'optional — pick a technician to send the jobs to their phone')
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
            'assignee': self.assignee.currentText().strip(),
            'assignee_id': self._assignee_id(),
        }

    def _assignee_id(self):
        """The server user id behind the chosen name, '' for typed text."""
        name = self.assignee.currentText().strip()
        i = self.assignee.findText(name)
        return (self.assignee.itemData(i) or '') if i >= 0 else ''


class _AssignDialog(QDialog):
    """Who gets these jobs on their phone.

    A plan item never left this desktop; published, it becomes a work record
    in the one journal — the technician opens it in Tasks, does the work and
    fills that same record in.
    """

    def __init__(self, users, n_jobs, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Send to a technician')
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        intro = QLabel(
            f'{n_jobs} job(s) become work records assigned to one person. They '
            'appear in Tasks on that phone after the next sync, and what the '
            'technician writes comes back into the same record — not a second '
            'one. Sending twice does not duplicate them.')
        intro.setWordWrap(True)
        intro.setStyleSheet('color:#6B7A8D;')
        lay.addWidget(intro)

        form = QFormLayout()
        self.who = QComboBox()
        for u in users:
            self.who.addItem(f"{u['username']}  ({u['role'] or 'user'})", u['id'])
        form.addRow('Technician:', self.who)
        lay.addLayout(form)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Ok).setText('Send')
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    def values(self):
        return (self.who.currentData() or '',
                self.who.currentText().split('  (')[0])


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


class _ChecklistPlanDialog(QDialog):
    """A PM campaign's checklists: one per block, out of the customer's own
    workbook. What is left out of this campaign is ticked off here — the row
    still goes to the customer, empty, with the note written beside it."""

    def __init__(self, templates, n_blocks, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Plan checklists')
        self.setMinimumSize(760, 560)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        form = QFormLayout()
        self.template = QComboBox()
        for t in templates:
            self.template.addItem(
                '{}{} · {} item(s)'.format(t['name'],
                                           f" ({t['kind']})" if t['kind'] else '',
                                           t['item_count']), t['id'])
        self.template.currentIndexChanged.connect(self._load_items)
        form.addRow('Checklist:', self.template)

        self.campaign = QLineEdit(f'PM {datetime.date.today().strftime("%B %Y")}')
        form.addRow('Campaign:', self.campaign)

        self.kind = QComboBox()
        self.kind.addItem(f'All {n_blocks} blocks', 'all')
        self.kind.addItem('Only the blocks I list', 'custom')
        self.kind.currentIndexChanged.connect(
            lambda: self.blocks.setEnabled(self.kind.currentData() == 'custom'))
        form.addRow('Blocks:', self.kind)
        self.blocks = QLineEdit()
        self.blocks.setPlaceholderText('e.g. 4, 17-20, 33')
        self.blocks.setEnabled(False)
        form.addRow('', self.blocks)

        self.date = QDateEdit(QDate.currentDate())
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat('yyyy-MM-dd')
        form.addRow('Date:', self.date)
        lay.addLayout(form)

        cap = QLabel('Untick anything this campaign does not cover — "we are not '
                     'doing the RMU this time". The row stays in the customer\'s '
                     'file, empty, with your note.')
        cap.setWordWrap(True); cap.setStyleSheet('color:#6B7A8D;font-size:11px;')
        lay.addWidget(cap)

        self.tbl = QTableWidget(0, 5)
        self.tbl.setHorizontalHeaderLabels(
            ['In scope', 'No.', 'Equipment', 'Item', 'Note if left out'])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.tbl, 1)

        row = QHBoxLayout()
        b = SecondaryButton('＋  Add item…')
        b.setToolTip('An item this plant needs that the customer\'s file does '
                     'not list. It is written at the end of its group.')
        b.clicked.connect(self._add_item)
        row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)

        self._n_blocks = n_blocks
        self._load_items()

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Ok).setText('Plan')
        box.accepted.connect(self.accept); box.rejected.connect(self.reject)
        lay.addWidget(box)

    def template_id(self):
        return self.template.currentData()

    def _load_items(self):
        import services.checklist_pm_service as cs
        self.tbl.setRowCount(0)
        tid = self.template_id()
        if not tid:
            return
        for it in cs.items(tid):
            r = self.tbl.rowCount()
            self.tbl.insertRow(r)
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Checked)
            chk.setData(Qt.UserRole, it['id'])
            self.tbl.setItem(r, 0, chk)
            for c, v in ((1, it['s_no'] or ''), (2, it['equipment'] or ''),
                         (3, it['description'] or '')):
                cell = QTableWidgetItem(str(v))
                cell.setFlags(Qt.ItemIsEnabled)
                self.tbl.setItem(r, c, cell)
            self.tbl.setItem(r, 4, QTableWidgetItem(''))
        self.tbl.resizeColumnsToContents()

    def _add_item(self):
        import services.checklist_pm_service as cs
        tid = self.template_id()
        if not tid:
            return
        groups = []
        for it in cs.items(tid):
            if it['equipment'] and it['equipment'] not in groups:
                groups.append(it['equipment'])
        dlg = QDialog(self)
        dlg.setWindowTitle('Add an item')
        dlg.setMinimumWidth(460)
        lay = QVBoxLayout(dlg)
        form = QFormLayout()
        group = QComboBox()
        for g in groups:
            group.addItem(g, g)
        form.addRow('Group:', group)
        text = QLineEdit()
        text.setPlaceholderText('e.g. Check the anti-condensation heater')
        form.addRow('Item:', text)
        lay.addLayout(form)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(dlg.accept); box.rejected.connect(dlg.reject)
        lay.addWidget(box)
        if dlg.exec_() != QDialog.Accepted or not text.text().strip():
            return
        cs.add_item(tid, text.text().strip(), equipment=group.currentData() or '')
        self._load_items()

    def values(self):
        blocks = (list(range(1, self._n_blocks + 1))
                  if self.kind.currentData() == 'all'
                  else pl._parse_blocks(self.blocks.text()))
        excluded, notes = [], {}
        for r in range(self.tbl.rowCount()):
            cell = self.tbl.item(r, 0)
            if cell.checkState() == Qt.Checked:
                continue
            iid = cell.data(Qt.UserRole)
            excluded.append(iid)
            note = (self.tbl.item(r, 4).text() or '').strip()
            if note:
                notes[iid] = note
        return {'template_id': self.template_id(),
                'blocks': blocks,
                'run_date': self.date.date().toString('yyyy-MM-dd'),
                'campaign': self.campaign.text().strip(),
                'excluded_item_ids': excluded,
                'excluded_notes': notes}


class _ChecklistFillDialog(QDialog):
    """One block's checklist, filled or corrected here — technicians forget,
    and a checklist that cannot be finished in the office is a checklist the
    customer never gets."""

    RESULTS = [('—', ''), ('OK', 'OK'), ('NOK', 'NOK'), ('N/A', 'N/A')]

    def __init__(self, run, parent=None):
        super().__init__(parent)
        import services.checklist_pm_service as cs
        self._cs = cs
        self._run = run
        self.setWindowTitle('Checklist — block {}'.format(run['plant_block']))
        self.setMinimumSize(860, 620)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        head = QLabel('<b>{}</b> · block {} · {} · {}'.format(
            run['template_name'], run['plant_block'], run['campaign'] or '—',
            run['run_date']))
        head.setWordWrap(True)
        lay.addWidget(head)

        form = QFormLayout()
        self.ptw = QLineEdit(run['ptw_no'] or '')
        self.ptw.setPlaceholderText('optional')
        form.addRow('PTW No.:', self.ptw)
        self.serial = QLineEdit(run['serial'] or '')
        self.serial.setPlaceholderText('blank when the project does not know it')
        form.addRow('Equipment serial:', self.serial)
        self.signed = QLineEdit(run['signed_by'] or '')
        self.signed.setPlaceholderText('optional')
        form.addRow('Signed by:', self.signed)
        lay.addLayout(form)

        self.tbl = QTableWidget(0, 6)
        self.tbl.setHorizontalHeaderLabels(
            ['No.', 'Equipment', 'Activity', 'Item', 'Result', 'Comment'])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.tbl, 1)
        self._fill()

        self.note = QLabel('')
        self.note.setStyleSheet('color:#6B7A8D;font-size:11px;')
        lay.addWidget(self.note)
        self._refresh_note()

        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept); box.rejected.connect(self.reject)
        lay.addWidget(box)

    def _fill(self):
        for it in self._run['items']:
            r = self.tbl.rowCount()
            self.tbl.insertRow(r)
            for c, v in ((0, it['s_no'] or ''), (1, it['equipment'] or ''),
                         (2, it['activity'] or ''), (3, it['text'] or '')):
                cell = QTableWidgetItem(str(v))
                cell.setFlags(Qt.ItemIsEnabled)
                self.tbl.setItem(r, c, cell)
            self.tbl.item(r, 0).setData(Qt.UserRole, it['item_id'])
            excluded = (it['result'] == self._cs.EXCLUDED)
            if excluded:
                lbl = QTableWidgetItem('Excluded')
                lbl.setFlags(Qt.ItemIsEnabled)
                lbl.setForeground(QColor('#9AA6B4'))
                self.tbl.setItem(r, 4, lbl)
                for c in range(4):
                    self.tbl.item(r, c).setForeground(QColor('#9AA6B4'))
            else:
                cb = QComboBox()
                for label, code in self.RESULTS:
                    cb.addItem(label, code)
                i = cb.findData(it['result'] or '')
                cb.setCurrentIndex(max(0, i))
                cb.currentIndexChanged.connect(self._refresh_note)
                self.tbl.setCellWidget(r, 4, cb)
            self.tbl.setItem(r, 5, QTableWidgetItem(it['comment'] or ''))
        self.tbl.resizeColumnsToContents()
        self.tbl.setColumnWidth(3, 340)

    def _refresh_note(self):
        vals = self.values()['results']
        done = sum(1 for v in vals.values()
                   if v['result'] in (self._cs.OK, self._cs.NOK, self._cs.NA))
        nok = sum(1 for v in vals.values() if v['result'] == self._cs.NOK)
        ex = sum(1 for v in vals.values() if v['result'] == self._cs.EXCLUDED)
        self.note.setText(
            f'{done} of {len(vals) - ex} in scope answered · {nok} NOK · '
            f'{ex} left out of this campaign. Measurements and one-unit '
            f'deviations go in the comment ("BESS 3: door seal torn").')

    def values(self):
        results = {}
        for r in range(self.tbl.rowCount()):
            iid = self.tbl.item(r, 0).data(Qt.UserRole)
            w = self.tbl.cellWidget(r, 4)
            res = w.currentData() if w else self._cs.EXCLUDED
            results[iid] = {'result': res,
                            'comment': (self.tbl.item(r, 5).text() or '').strip()}
        in_scope = [v for v in results.values() if v['result'] != self._cs.EXCLUDED]
        done = all(v['result'] for v in in_scope)
        return {'results': results,
                'status': 'Done' if (in_scope and done) else 'In Progress',
                'ptw_no': self.ptw.text().strip(),
                'serial': self.serial.text().strip(),
                'signed_by': self.signed.text().strip()}


class _ActionImportDialog(QDialog):
    """The action list from a spreadsheet, showing what maps to what first.

    Importing the same sheet again is the normal case — it is a living
    document — so the dialog says out loud what a re-import does.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Import the action list')
        self.setMinimumSize(700, 460)
        self._preview = None
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        row = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setPlaceholderText('action list.xlsx')
        row.addWidget(self.path, 1)
        b = SecondaryButton('Browse…'); b.clicked.connect(self._browse)
        row.addWidget(b)
        lay.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel('Sheet:'))
        self.sheet = QComboBox(); self.sheet.setMinimumWidth(170)
        self.sheet.currentIndexChanged.connect(self._reread)
        row.addWidget(self.sheet)
        row.addStretch()
        lay.addLayout(row)

        self.info = QLabel('Choose a file — the columns are matched '
                           'automatically, and you can correct the match below.')
        self.info.setWordWrap(True)
        self.info.setStyleSheet('color:#6B7A8D;font-size:11px;')
        lay.addWidget(self.info)

        self.map_tbl = QTableWidget(0, 2)
        self.map_tbl.setHorizontalHeaderLabels(['Column in the file', 'Means'])
        self.map_tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.map_tbl.verticalHeader().setVisible(False)
        lay.addWidget(self.map_tbl, 1)

        note = QLabel('Importing the same sheet again updates the rows that '
                      'really changed and adds the new ones. An item already '
                      'here that is no longer in the file is kept, not deleted '
                      '— somebody may have been working on it.')
        note.setWordWrap(True); note.setStyleSheet('color:#6B7A8D;font-size:11px;')
        lay.addWidget(note)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Ok).setText('Import')
        box.accepted.connect(self.accept); box.rejected.connect(self.reject)
        lay.addWidget(box)

    FIELDS = [('', '— ignore —'), ('seq', 'No.'), ('topic', 'Topic'),
              ('description', 'Description'), ('todo', 'Remarks / To do'),
              ('due_date', 'Target date'), ('assigned_name', 'Owner'),
              ('status', 'Status')]

    def _browse(self):
        f, _ = QFileDialog.getOpenFileName(self, 'Action list', '',
                                           'Excel (*.xlsx *.xls)')
        if not f:
            return
        self.path.setText(f)
        try:
            import openpyxl
            names = openpyxl.load_workbook(f, read_only=True).sheetnames
        except Exception:                             # noqa: BLE001
            names = []
        self.sheet.blockSignals(True)
        self.sheet.clear()
        for n in (names or ['']):
            self.sheet.addItem(n or 'first sheet', n or 0)
        self.sheet.blockSignals(False)
        self._reread()

    def _reread(self):
        f = self.path.text().strip()
        if not f:
            return
        import services.action_list_service as als
        try:
            self._preview = als.preview_excel(f, sheet=self.sheet.currentData())
        except Exception as e:                        # noqa: BLE001
            QMessageBox.warning(self, 'Cannot read', str(e))
            return
        pv = self._preview
        self.info.setText(
            f"{pv['rows']} row(s). Matched {len(pv['mapping'])} of "
            f"{len(pv['columns'])} columns — correct anything that is wrong.")
        self.map_tbl.setRowCount(0)
        for col in pv['columns']:
            i = self.map_tbl.rowCount()
            self.map_tbl.insertRow(i)
            self.map_tbl.setItem(i, 0, QTableWidgetItem(str(col)))
            cb = QComboBox()
            for code, label in self.FIELDS:
                cb.addItem(label, code)
            j = cb.findData(pv['mapping'].get(col, ''))
            cb.setCurrentIndex(j if j >= 0 else 0)
            self.map_tbl.setCellWidget(i, 1, cb)

    def values(self):
        mapping = {}
        for i in range(self.map_tbl.rowCount()):
            col = self.map_tbl.item(i, 0).text()
            cb = self.map_tbl.cellWidget(i, 1)
            code = cb.currentData() if cb else ''
            if code:
                mapping[col] = code
        return (self.path.text().strip(), mapping,
                self.sheet.currentData() if self.sheet.count() else 0)


class _ActionItemDialog(QDialog):
    """One action item by hand. No block, no hours, no equipment — this is the
    office's own list, and nothing on it belongs in the customer's report."""

    def __init__(self, users, item=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Action item')
        self.setMinimumWidth(560)
        it = item or {}
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        form = QFormLayout()

        self.seq = QSpinBox(); self.seq.setRange(0, 9999)
        self.seq.setSpecialValueText('—')
        self.seq.setValue(int(it.get('seq') or 0))
        form.addRow('No.:', self.seq)

        self.topic = QLineEdit(it.get('topic') or '')
        self.topic.setPlaceholderText('Spare parts of EPC')
        form.addRow('Topic:', self.topic)

        self.description = QTextEdit(it.get('description') or '')
        self.description.setPlaceholderText('26 battery packs\nany others spare parts?')
        self.description.setFixedHeight(80)
        form.addRow('Description:', self.description)

        self.todo = QTextEdit(it.get('todo') or '')
        self.todo.setPlaceholderText('Confirm EPC purchased, compare with Annex 13')
        self.todo.setFixedHeight(70)
        form.addRow('Remarks / To do:', self.todo)

        self.due = QDateEdit(); self.due.setCalendarPopup(True)
        self.due.setDisplayFormat('yyyy-MM-dd')
        self.has_due = QCheckBox('has a target date')
        self.has_due.setChecked(bool(it.get('due_date')))
        self.due.setDate(QDate.fromString(it.get('due_date') or '', 'yyyy-MM-dd')
                         if it.get('due_date') else QDate.currentDate())
        due_row = QHBoxLayout(); due_row.addWidget(self.due); due_row.addWidget(self.has_due)
        due_row.addStretch()
        form.addRow('Target date:', due_row)

        self.who = QComboBox()
        self.who.addItem('— nobody —', '')
        for u in (users or []):
            self.who.addItem(f"{u['username']}  ({u['role'] or 'user'})", u['id'])
        j = self.who.findData(str(it.get('assigned_to') or ''))
        self.who.setCurrentIndex(j if j >= 0 else 0)
        form.addRow('Assigned to:', self.who)

        self.status = QComboBox()
        import services.action_list_service as als
        for s in als.STATUSES:
            self.status.addItem(s.title(), s)
        k = self.status.findData(it.get('status') or 'open')
        self.status.setCurrentIndex(max(0, k))
        form.addRow('Status:', self.status)
        lay.addLayout(form)

        note = QLabel('An action item is organisational — it never reaches the '
                      'customer\'s monthly report, and it carries no block, no '
                      'hours and no availability.')
        note.setWordWrap(True); note.setStyleSheet('color:#6B7A8D;font-size:11px;')
        lay.addWidget(note)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept); box.rejected.connect(self.reject)
        lay.addWidget(box)

    def values(self):
        return {
            'seq': self.seq.value() or None,
            'topic': self.topic.text().strip(),
            'description': self.description.toPlainText().strip(),
            'todo': self.todo.toPlainText().strip(),
            'due_date': (self.due.date().toString('yyyy-MM-dd')
                         if self.has_due.isChecked() else ''),
            'assigned_to': self.who.currentData() or '',
            'assigned_name': (self.who.currentText().split('  (')[0]
                              if self.who.currentData() else ''),
            'status': self.status.currentData(),
        }


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
        self.tabs.addTab(self._tab_checklists(), 'Checklists')
        self.tabs.addTab(self._tab_actions(), 'Action list')
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
            ('📱  Send to a technician…', self._send_to_phone, False),
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

    def _tab_checklists(self):
        """The customer's own PM checklists: imported, planned per block, sent
        to the phones, exported back into the customer's file."""
        w = QWidget(); lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 12, 20, 12); lay.setSpacing(8)
        cap = QLabel('One checklist per block, out of the workbook the customer '
                     'issued. Planned checklists reach the phones on the next '
                     'sync; what the technicians tick comes back here, and the '
                     'export writes it into the customer\'s own file.')
        cap.setWordWrap(True); cap.setStyleSheet('color:#6B7A8D;font-size:11px;')
        lay.addWidget(cap)

        row = QHBoxLayout()
        b = SecondaryButton('⤓  Import checklist…')
        b.setToolTip('The customer\'s Excel checklist. Importing it again '
                     'updates the items and keeps what is already filled in.')
        b.clicked.connect(self._import_checklist)
        row.addWidget(b)
        b = PrimaryButton('＋  Plan checklists…')
        b.clicked.connect(self._plan_checklists)
        row.addWidget(b)
        row.addSpacing(12)
        row.addWidget(QLabel('Campaign:'))
        self.cl_campaign = QComboBox(); self.cl_campaign.setMinimumWidth(190)
        self.cl_campaign.currentIndexChanged.connect(self._fill_checklists)
        row.addWidget(self.cl_campaign)
        row.addStretch()
        lay.addLayout(row)

        # Which checklists this project has, without opening a dialog to find out
        self.cl_templates = QLabel('')
        self.cl_templates.setWordWrap(True)
        self.cl_templates.setStyleSheet('color:#33465E;font-size:11px;')
        lay.addWidget(self.cl_templates)

        self.cl_tbl = make_table(['Block', 'Checklist', 'Campaign', 'Date',
                                  'Progress', 'NOK', 'Status', 'Filled on'])
        self.cl_tbl.doubleClicked.connect(self._open_checklist)
        lay.addWidget(self.cl_tbl, 1)

        row = QHBoxLayout()
        for label, slot, primary in (
            ('✎  Open / fill…', self._open_checklist, True),
            ('⤒  Export this one…', self._export_checklist, False),
            ('⤒  Export the campaign…', self._export_campaign_checklists, False),
            ('🗑  Delete', self._delete_checklist, False),
        ):
            btn = (PrimaryButton if primary else SecondaryButton)(label)
            btn.clicked.connect(slot)
            row.addWidget(btn)
        row.addStretch()
        hint = QLabel('Double-click a row to fill it in.')
        hint.setStyleSheet('color:#6B7A8D;font-size:11px;')
        row.addWidget(hint)
        lay.addLayout(row)
        return w

    def _tab_actions(self):
        """The office's action list: what has to be confirmed, ordered,
        submitted or trained. Not plant work — it never reaches the
        customer's monthly report."""
        w = QWidget(); lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 12, 20, 12); lay.setSpacing(8)
        cap = QLabel('Organisational actions — confirmations, BOMs, '
                     'certificates, training. They carry no block and no '
                     'hours, so nothing here reaches the customer\'s monthly '
                     'report. An item given to a person shows up in Tasks on '
                     'that phone after the next sync.')
        cap.setWordWrap(True); cap.setStyleSheet('color:#6B7A8D;font-size:11px;')
        lay.addWidget(cap)

        row = QHBoxLayout()
        b = SecondaryButton('⤓  Import from Excel…')
        b.setToolTip('Importing the same sheet again updates what changed and '
                     'adds what is new — nothing already worked on is lost.')
        b.clicked.connect(self._import_actions)
        row.addWidget(b)
        b = PrimaryButton('＋  Add item…')
        b.clicked.connect(self._add_action)
        row.addWidget(b)
        row.addSpacing(12)
        row.addWidget(QLabel('Show:'))
        self.act_filter = QComboBox(); self.act_filter.setMinimumWidth(190)
        for label, data in (('Open items', 'open'),
                            ('Overdue or due in 7 days', 'soon'),
                            ('Everything', 'all'),
                            ('Done', 'done')):
            self.act_filter.addItem(label, data)
        self.act_filter.currentIndexChanged.connect(self._fill_actions)
        row.addWidget(self.act_filter)
        row.addStretch()
        lay.addLayout(row)

        self.act_tbl = make_table(['No.', 'Topic', 'To do', 'Due',
                                   'Assigned to', 'Status'])
        self.act_tbl.doubleClicked.connect(self._edit_action)
        lay.addWidget(self.act_tbl, 1)

        row = QHBoxLayout()
        for label, slot, primary in (
            ('✎  Edit…', self._edit_action, True),
            ('👤  Assign…', self._assign_action, False),
            ('✓  Mark done…', self._done_action, False),
            ('⤒  Export to Excel…', self._export_actions, False),
            ('🗑  Delete', self._delete_action, False),
        ):
            btn = (PrimaryButton if primary else SecondaryButton)(label)
            btn.clicked.connect(slot)
            row.addWidget(btn)
        row.addStretch()
        hint = QLabel('Double-click a row to edit it.  Overdue items are red.')
        hint.setStyleSheet('color:#6B7A8D;font-size:11px;')
        row.addWidget(hint)
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
        elif self.tabs.currentIndex() == 3:
            self._load_checklists()
        elif self.tabs.currentIndex() == 4:
            self._fill_actions()

    def _on_tab(self, i):
        if i == 1:
            self._load_due()
        elif i == 2:
            self._load_plans()
        elif i == 3:
            self._load_checklists()
        elif i == 4:
            self._fill_actions()

    def apply_filter(self, flt: dict):
        """A count on the Today screen opens the list that holds exactly those
        rows. Only the action list is addressed this way so far."""
        if (flt or {}).get('tab') != 'actions':
            return
        self.tabs.setCurrentIndex(4)
        want = (flt or {}).get('due') or 'open'
        i = self.act_filter.findData(want)
        if i >= 0:
            self.act_filter.setCurrentIndex(i)
        self._fill_actions()

    def _fill_schedule(self):
        self.tbl.setRowCount(0)
        for it in self._items:
            r = self.tbl.rowCount()
            self.tbl.insertRow(r)
            # 📱 = published as a work record, so it is on that phone
            who = it.get('assignee') or ''
            if it.get('record_uuid'):
                who = f"📱 {who}" if who else '📱 sent'
            vals = [it.get('planned_date') or '',
                    it.get('type_label') or it.get('type_code') or '',
                    str(it['block']) if it.get('block') else '',
                    it.get('title') or '',
                    who,
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

    # ── checklists ───────────────────────────────────────────────────────
    def _load_checklists(self):
        """The campaign list first, then the checklists of the one on show."""
        if not self._project_id:
            return
        import services.checklist_pm_service as cs
        tpls = cs.templates(self._project_id)
        self.cl_templates.setText(
            'Checklists imported:  ' + '   ·   '.join(
                '{} ({} items)'.format(t['name'], t['item_count']) for t in tpls)
            if tpls else 'No checklist imported for this project yet.')
        cur = self.cl_campaign.currentData()
        self.cl_campaign.blockSignals(True)
        self.cl_campaign.clear()
        self.cl_campaign.addItem('All campaigns', None)
        for c in cs.campaigns(self._project_id):
            name = c['campaign'] or '(no campaign)'
            self.cl_campaign.addItem(
                f"{name} · {c['n_done']}/{c['n_runs']} done", c['campaign'])
        i = self.cl_campaign.findData(cur)
        self.cl_campaign.setCurrentIndex(max(0, i))
        self.cl_campaign.blockSignals(False)
        self._fill_checklists()

    def _fill_checklists(self):
        if not self._project_id:
            return
        import services.checklist_pm_service as cs
        self.cl_tbl.setRowCount(0)
        rows = cs.runs(self._project_id, campaign=self.cl_campaign.currentData())
        for r in rows:
            i = self.cl_tbl.rowCount()
            self.cl_tbl.insertRow(i)
            vals = [str(r['plant_block'] or ''), r['template_name'],
                    r['campaign'] or '', r['run_date'] or '',
                    f"{r['n_done']}/{r['n_items']}", str(r['n_nok'] or ''),
                    r['status'] or '', (r['source'] or '').title()]
            for c, v in enumerate(vals):
                cell = QTableWidgetItem(str(v))
                if c in (0, 4, 5):
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.cl_tbl.setItem(i, c, cell)
            if r['n_nok']:
                self.cl_tbl.item(i, 5).setForeground(QColor('#B4232A'))
            self.cl_tbl.item(i, 6).setForeground(
                QColor('#1E8E3E' if r['status'] == 'Done' else '#8A6D1F'))
            self.cl_tbl.item(i, 0).setData(Qt.UserRole, r)

    def _selected_checklist(self):
        rows = (self.cl_tbl.selectionModel().selectedRows()
                if self.cl_tbl.selectionModel() else [])
        if not rows:
            QMessageBox.information(self, 'Nothing selected',
                                    'Pick a checklist first.')
            return None
        return self.cl_tbl.item(rows[0].row(), 0).data(Qt.UserRole)

    def _import_checklist(self):
        if not self._project_id:
            return
        import services.checklist_pm_service as cs
        path, _ = QFileDialog.getOpenFileName(
            self, 'The customer\'s checklist', '', 'Excel (*.xlsx)')
        if not path:
            return
        try:
            tid = cs.import_template(self._project_id, path)
        except Exception as e:                        # noqa: BLE001
            QMessageBox.critical(self, 'Cannot read this file', str(e))
            return
        n = len(cs.items(tid))
        self._load_checklists()
        QMessageBox.information(
            self, 'Imported',
            f'{n} item(s). The workbook itself is kept beside the database, so '
            f'the export goes back into the customer\'s own file.')

    def _plan_checklists(self):
        if not self._project_id:
            return
        import services.checklist_pm_service as cs
        try:
            tpls = cs.templates(self._project_id)
        except Exception as e:                        # noqa: BLE001
            QMessageBox.critical(self, 'Could not read the checklists', str(e))
            return
        if not tpls:
            QMessageBox.information(
                self, 'No checklist yet',
                'Import the customer\'s checklist first (⤓ Import checklist…).')
            return
        dlg = _ChecklistPlanDialog(tpls, self._n_blocks(), self)
        if dlg.exec_() != QDialog.Accepted:
            return
        v = dlg.values()
        if not v['blocks']:
            QMessageBox.warning(self, 'No blocks', 'Nothing to plan.')
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            made = cs.plan_runs(self._project_id, **v)
        except Exception as e:                        # noqa: BLE001
            # A database error here used to escape the slot, and Qt kills the
            # app when that happens. The global hook catches it now, but the
            # page that failed should still say so where it happened.
            QMessageBox.critical(self, 'Could not plan the checklists', str(e))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self._load_checklists()
        QMessageBox.information(
            self, 'Planned',
            f"{len(made)} checklist(s) ready — they reach the phones on the "
            f"next sync.")

    def _open_checklist(self):
        r = self._selected_checklist()
        if not r:
            return
        import services.checklist_pm_service as cs
        run = cs.run_detail(r['uuid'])
        if not run:
            return
        dlg = _ChecklistFillDialog(run, self)
        if dlg.exec_() != QDialog.Accepted:
            return
        v = dlg.values()
        cs.save_results(r['uuid'], v['results'], status=v['status'],
                        signed_by=v['signed_by'], ptw_no=v['ptw_no'],
                        serial=v['serial'], source='desktop')
        self._load_checklists()

    def _export_checklist(self):
        r = self._selected_checklist()
        if not r:
            return
        d = QFileDialog.getExistingDirectory(self, 'Where to save')
        if not d:
            return
        import services.checklist_pm_service as cs
        try:
            out = cs.export_run(r['uuid'], d, self.project_combo.currentText())
        except Exception as e:                        # noqa: BLE001
            QMessageBox.critical(self, 'Export failed', str(e))
            return
        QMessageBox.information(self, 'Saved', out)

    def _export_campaign_checklists(self):
        if not self._project_id:
            return
        campaign = self.cl_campaign.currentData()
        if campaign is None:
            QMessageBox.information(self, 'Which campaign?',
                                    'Choose a campaign above first.')
            return
        d = QFileDialog.getExistingDirectory(self, 'Where to save')
        if not d:
            return
        import services.checklist_pm_service as cs
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            out = cs.export_campaign(self._project_id, campaign, d,
                                     self.project_combo.currentText())
        finally:
            QApplication.restoreOverrideCursor()
        bad = [o for o in out if str(o).startswith('!')]
        QMessageBox.information(
            self, 'Exported',
            '{} file(s) written to\n{}{}'.format(
                len(out) - len(bad), d,
                '\n\nNot written:\n' + '\n'.join(bad) if bad else ''))

    def _delete_checklist(self):
        r = self._selected_checklist()
        if not r:
            return
        if QMessageBox.question(
                self, 'Delete',
                f"Delete the checklist for block {r['plant_block']}?\n"
                "It disappears from the phones on the next sync.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        import services.checklist_pm_service as cs
        cs.delete_run(r['uuid'])
        self._load_checklists()

    # ── action list ──────────────────────────────────────────────────────
    def _action_rows(self):
        """The rows the current filter asks for. "Overdue or due in 7 days"
        goes through the very function the Today screen counts, so the count
        there and the list here can never disagree."""
        import services.action_list_service as als
        what = self.act_filter.currentData()
        if what == 'soon':
            return als.due_soon(self._project_id)
        if what == 'done':
            return als.items(self._project_id, status=als.DONE)
        if what == 'all':
            return als.items(self._project_id)
        return als.items(self._project_id, include_done=False)

    def _fill_actions(self):
        if not self._project_id:
            return
        self.act_tbl.setRowCount(0)
        for it in self._action_rows():
            i = self.act_tbl.rowCount()
            self.act_tbl.insertRow(i)
            # the table is one line per item: a multi-line cell is shown as
            # its first line, and the whole text sits in the tooltip
            todo = (it.get('todo') or '').replace('\n', ' · ')
            who = it.get('assigned_name') or (
                '#' + it['assigned_to'] if it.get('assigned_to') else '')
            vals = [str(it.get('seq') or ''), it.get('topic') or '', todo,
                    it.get('due_date') or '', who,
                    (it.get('status') or '').replace('_', ' ').title()]
            for c, v in enumerate(vals):
                cell = QTableWidgetItem(str(v))
                if c == 0:
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.act_tbl.setItem(i, c, cell)
            full = '\n\n'.join(x for x in (it.get('description'),
                                           it.get('todo')) if x)
            if full:
                self.act_tbl.item(i, 1).setToolTip(full)
                self.act_tbl.item(i, 2).setToolTip(it.get('todo') or '')
            if it.get('overdue'):
                for c in range(self.act_tbl.columnCount()):
                    self.act_tbl.item(i, c).setForeground(QColor('#B4232A'))
            elif (it.get('status') or '') == 'done':
                self.act_tbl.item(i, 5).setForeground(QColor('#1E8E3E'))
            self.act_tbl.item(i, 0).setData(Qt.UserRole, it)

    def _selected_action(self):
        rows = (self.act_tbl.selectionModel().selectedRows()
                if self.act_tbl.selectionModel() else [])
        if not rows:
            QMessageBox.information(self, 'Nothing selected',
                                    'Pick an action item first.')
            return None
        return self.act_tbl.item(rows[0].row(), 0).data(Qt.UserRole)

    def _team(self):
        try:
            return team.users()
        except Exception:                             # noqa: BLE001
            return []

    def _import_actions(self):
        if not self._project_id:
            return
        import services.action_list_service as als
        dlg = _ActionImportDialog(self)
        if dlg.exec_() != QDialog.Accepted:
            return
        path, mapping, sheet = dlg.values()
        if not path or not mapping:
            QMessageBox.warning(self, 'Nothing to import',
                                'Choose a file and say which column is which.')
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            res = als.import_from_excel(self._project_id, path, mapping,
                                        sheet=sheet, log=lambda *_a: None)
        except Exception as e:                        # noqa: BLE001
            QMessageBox.critical(self, 'Could not import the action list', str(e))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.tabs.setCurrentIndex(4)
        self._fill_actions()
        extra = ''
        if res['untouched']:
            extra = ('\n\n{} item(s) here are not in this file and were kept:\n  '
                     .format(len(res['untouched']))
                     + '\n  '.join(res['untouched'][:8]))
        QMessageBox.information(
            self, 'Action list imported',
            f"{res['added']} new, {res['updated']} updated, "
            f"{res['unchanged']} unchanged." + extra)

    def _add_action(self):
        if not self._project_id:
            return
        import services.action_list_service as als
        dlg = _ActionItemDialog(self._team(), None, self)
        if dlg.exec_() != QDialog.Accepted:
            return
        v = dlg.values()
        if not v['topic']:
            QMessageBox.warning(self, 'No topic', 'An item needs a topic.')
            return
        als.save(self._project_id, None, **v)
        self._fill_actions()

    def _edit_action(self):
        it = self._selected_action()
        if not it:
            return
        import services.action_list_service as als
        dlg = _ActionItemDialog(self._team(), it, self)
        if dlg.exec_() != QDialog.Accepted:
            return
        als.save(self._project_id, it['uuid'], **dlg.values())
        self._fill_actions()

    def _assign_action(self):
        it = self._selected_action()
        if not it:
            return
        import services.action_list_service as als
        users = self._team()
        if not users:
            QMessageBox.information(
                self, 'No people yet',
                'The list of accounts comes from the sync server. Sync once '
                '(🔄 in the toolbar) and try again.')
            return
        dlg = _AssignDialog(users, 1, self)
        dlg.setWindowTitle('Give this action item to…')
        if dlg.exec_() != QDialog.Accepted:
            return
        uid, name = dlg.values()
        als.assign(it['uuid'], uid, name)
        self._fill_actions()
        QMessageBox.information(
            self, 'Assigned',
            f"“{it['topic']}” is {name}'s — it appears in Tasks on that phone "
            f"after the next sync.")

    def _done_action(self):
        it = self._selected_action()
        if not it:
            return
        import services.action_list_service as als
        note, ok = QInputDialog.getMultiLineText(
            self, 'Mark done', f"{it['topic']}\n\nWhat was the outcome? "
                               f"(optional — it goes in the export)", '')
        if not ok:
            return
        als.mark_done(it['uuid'], note=note.strip(),
                      by=(it.get('assigned_name') or ''), source='desktop')
        self._fill_actions()

    def _delete_action(self):
        it = self._selected_action()
        if not it:
            return
        if QMessageBox.question(
                self, 'Delete',
                f"Delete “{it['topic']}”?\nIt disappears from the phones on "
                f"the next sync.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        import services.action_list_service as als
        als.delete(it['uuid'])
        self._fill_actions()

    def _export_actions(self):
        if not self._project_id:
            return
        import services.action_list_service as als
        path, _ = QFileDialog.getSaveFileName(
            self, 'Export the action list', 'Action list.xlsx', 'Excel (*.xlsx)')
        if not path:
            return
        try:
            res = als.export_excel(self._project_id, path)
        except Exception as e:                        # noqa: BLE001
            QMessageBox.critical(self, 'Could not export', str(e))
            return
        QMessageBox.information(
            self, 'Exported',
            f"{res['rows']} item(s), with the status, who did it and when.")

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
        from services import report_workflow_service as rw
        if (it.get('counts_as_downtime') and hours > rw.PM_CONFIRM_HOURS
                and QMessageBox.question(
                    self, 'Long PM', f'{hours:g} h of PM on one block in one day — is that right?',
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes):
            return
        try:
            out = pl.complete_item(it['id'], actual_date=date, actual_hours=hours,
                                   notes=notes)
        except rw.PMValidationError as e:
            QMessageBox.warning(self, 'Cannot mark done', '\n'.join(e.problems))
            return
        except rw.PMDuplicateError as e:
            d = e.duplicates[0]
            ex = d['existing']
            box = QMessageBox(self)
            box.setWindowTitle('PM already recorded')
            box.setText(
                f"Block {d['block']} on {d['date']} already has a PM record: "
                f"{rw.fmt_hours(ex.get('hours'))} h ({ex.get('source') or 'entered earlier'})"
                f" — “{ex.get('description') or ''}”.\n\n"
                "One PM is charged once. Which hours are right?")
            keep = box.addButton(f"Keep {rw.fmt_hours(ex.get('hours'))} h", QMessageBox.AcceptRole)
            repl = box.addButton(f"Use {hours:g} h", QMessageBox.DestructiveRole)
            box.addButton(QMessageBox.Cancel)
            box.exec_()
            if box.clickedButton() not in (keep, repl):
                return
            out = pl.complete_item(it['id'], actual_date=date, actual_hours=hours,
                                   notes=notes,
                                   on_duplicate='link' if box.clickedButton() is keep else 'update')
        self._reload()
        if out['wrote_downtime']:
            self.summary.setText(
                self.summary.text() + '   ·   downtime recorded for the monthly report')

    def _selected_items(self):
        """Every selected job, for the actions that work on a batch."""
        rows = self.tbl.selectionModel().selectedRows() if self.tbl.selectionModel() else []
        return [self.tbl.item(r.row(), 0).data(Qt.UserRole) for r in rows]

    def _send_to_phone(self):
        """Publish planned jobs as records assigned to a technician, so a
        campaign planned here actually reaches the phones."""
        if not self._project_id:
            return
        items = self._selected_items() or list(self._items)
        if not items:
            QMessageBox.information(self, 'Nothing to send',
                                    'There are no jobs in this view.')
            return
        try:
            users = team.users()
        except Exception as e:                        # noqa: BLE001
            QMessageBox.critical(self, 'Could not read the technician list',
                                 str(e))
            return
        if not users:
            QMessageBox.information(
                self, 'No technicians yet',
                'The list of accounts comes from the sync server. Sync once '
                '(🔄 in the toolbar) and try again.')
            return
        if not self._selected_items() and QMessageBox.question(
                self, 'Send every job shown',
                f'Nothing is selected, so all {len(items)} job(s) in this view '
                'would be sent. Continue?',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        dlg = _AssignDialog(users, len(items), self)
        if dlg.exec_() != QDialog.Accepted:
            return
        uid, name = dlg.values()
        self._publish(items, uid, name)

    def _publish(self, items, user_id, user_name):
        from services.sync_config import sync_config
        import services.sync_client as sc
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            res = pl.publish_jobs(self._project_id, [it['id'] for it in items],
                                  assignee_id=user_id, assignee_name=user_name,
                                  assigned_by=sync_config.username or '')
            # An older server accepts the record and silently drops who it is
            # for, so "Sent to Ivan" would be a lie and nothing would reach
            # any phone. Ask the server once — one message for the batch, not
            # a dialog per job.
            warning = sc.verify_assignment([k[2:] for k in res.get('keys', [])],
                                           user_id)
        except Exception as e:                        # noqa: BLE001
            QApplication.restoreOverrideCursor()      # before the dialog blocks
            QMessageBox.critical(self, 'Could not send the jobs', str(e))
            self._reload()
            return None
        else:
            QApplication.restoreOverrideCursor()
        self._reload()
        if warning:
            QMessageBox.warning(
                self, 'Sent here, but not to the phone',
                f"{res['published']} job(s) were written for {user_name} in "
                f"this app.\n\n{warning}")
        else:
            QMessageBox.information(
                self, 'Sent',
                f"{res['published']} job(s) are now work records for {user_name}"
                + (f", {res['reassigned']} already published were reassigned"
                   if res['reassigned'] else '')
                + '.\n\nThey reach the phone on its next sync, and show in Work '
                  'here as assigned.')
        return res

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
        # A campaign nobody can see is the old problem: offer to send it now,
        # while the person who planned it is still here.
        if res['items'] and v.get('assignee_id') and QMessageBox.question(
                self, 'Send to the phone',
                f"Send these {res['items']} job(s) to {v['assignee']} now?\n\n"
                "They become work records in the journal and show in Tasks on "
                "that phone after the next sync.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes) == QMessageBox.Yes:
            self._publish([{'id': i} for i in res['item_ids']],
                          v['assignee_id'], v['assignee'])

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
        if res.get('notes'):
            msg.append('')
            msg.append(f"{len(res['notes'])} completion(s) matched a PM record that "
                       "already existed — no second record was added:")
            msg += ['   • ' + p for p in res['notes'][:8]]
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
