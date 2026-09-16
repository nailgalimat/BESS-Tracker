"""
ui/monthly_reports_page.py
---------------------------
Monthly report workspace. Pick a Project + Month; the month's report is made
in four steps, each on its own tab and free to visit in any order:

  1 · Data           the month's SCADA files — added once, recognised from their
                     headers, copied next to the database, coverage by day
  2 · Check          read-only questions before generating (window edges, PM,
                     phone events, 3.2 records, data gaps, month-end files);
                     each opens the place where it is fixed
  3 · Customer text  narrative and corrective works (section 3.2)
  4 · Release        versions vN (never overwritten) with their numbers,
                     "mark the final DOCX as sent", locked / unlocked month

The Availability inputs tab (phone events, PM records, manual downtime,
exclusions, balancing) stays first. Static per-project inputs (customer,
capacities, availability basis, ...) come from the Projects page.
"""

import os
import traceback

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPushButton, QGroupBox,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QTabWidget, QDialog,
    QDialogButtonBox, QMessageBox, QProgressBar, QScrollArea, QCheckBox,
    QTimeEdit, QInputDialog, QFileDialog, QApplication, QHeaderView,
)
from PyQt5.QtCore import Qt, QDate, QTime, QDateTime, QThread, pyqtSignal, QUrl
from PyQt5.QtGui import QColor, QDesktopServices

from ui.components import PageHeader, PrimaryButton, SecondaryButton
import services.report_workflow_service as rw
import services.availability_service as av
import services.availability_inputs_service as avi
import services.month_dataset_service as mds
import services.report_precheck_service as rpc
import services.report_versions_service as rvs
from services.report_workflow_service import MONTHS_EN
# 3.2 rules live in the service (the pre-generation check uses them too); the
# names stay importable from here.
from services.report_workflow_service import (                      # noqa: F401
    CORRECTIVE_CATS, OPEN_STATUSES, PM_TEXT, _is_open, cm_skip_reason,
    CM_SKIP_LABELS as _SKIP_LABELS,
)

_WARN_STYLE = ("background:#FFF8E6;border:1px solid #F0D58C;border-radius:6px;"
               "padding:6px 8px;color:#6B4E00;font-size:11px;")
_LOCK_STYLE = ("background:#EEF3FA;border:1px solid #B9CBE3;border-radius:6px;"
               "padding:6px 8px;color:#1F3B63;font-size:11px;")
_ERR_STYLE = "color:#B4232A;font-size:11px;"
_HINT_STYLE = "color:#6B7A8D;font-size:11px;"
_COV_COLORS = {'full': '#3FA56B', 'partial': '#E8B64C', 'none': '#DCE2EA', 'future': '#F4F6F9'}
_SEV_LABEL = {'crit': '⛔', 'warn': '⚠', 'info': 'ℹ'}


def safety_incident_rows(text):
    """The Narrative tab's Safety incidents text as the report's rows: one
    incident per line, optionally 'incident | equipment loss | weight |
    countermeasure'. Empty — or just 'none' — gives [] and the report says
    there were none."""
    t = (text or '').strip()
    if t.lower().rstrip('.') in ('', 'none', 'nil', 'n/a', 'na', '-', 'no',
                                 'no incidents', 'no safety incidents'):
        return []
    rows = []
    for ln in t.splitlines():
        if not ln.strip():
            continue
        parts = [p.strip() for p in ln.split('|')]
        rows.append({'incident': parts[0],
                     'equipment_loss': parts[1] if len(parts) > 1 and parts[1] else '-',
                     'weight': parts[2] if len(parts) > 2 else '',
                     'countermeasure': parts[3] if len(parts) > 3 else ''})
    return rows


from ui.scada_report_page import ExclusionDialog, BalancingDialog


class _Worker(QThread):
    """Runs fn(progress_callback) off the UI thread."""
    progress = pyqtSignal(str)
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def run(self):
        try:
            self.done.emit(self._fn(self.progress.emit))
        except Exception as e:                              # noqa: BLE001
            self.failed.emit(f"{e}" + chr(10) + traceback.format_exc())


class ChooseTypesDialog(QDialog):
    """Files the data set could not recognise: pick a type or skip each."""

    def __init__(self, parent, results, site):
        super().__init__(parent)
        self.setWindowTitle("Choose the type of these files")
        self.setMinimumWidth(720)
        lay = QVBoxLayout(self)
        lay.addWidget(_info_label(
            "These files were not recognised from their headers. Choose what each one is, "
            "or skip it. Nothing is guessed.", _HINT_STYLE))
        self._rows = []
        t = QTableWidget(len(results), 3)
        t.setHorizontalHeaderLabels(["File", "Why", "Type"])
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        t.setColumnWidth(1, 220); t.setColumnWidth(2, 230)
        for i, r in enumerate(results):
            t.setItem(i, 0, QTableWidgetItem(r['name']))
            t.setItem(i, 1, QTableWidgetItem(r.get('note') or
                                             ('several types match' if r['status'] == 'ambiguous'
                                              else 'not recognised')))
            combo = QComboBox()
            combo.addItem("Skip", 'skip')
            keys = r.get('candidates') or [k['key'] for k in mds.types_for(site)]
            for k in keys:
                combo.addItem(mds.TYPE[k]['label'], k)
            t.setCellWidget(i, 2, combo)
            self._rows.append((r['path'], combo))
        lay.addWidget(t)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def answers(self):
        return {p: c.currentData() for p, c in self._rows}


class ConfirmGenerateDialog(QDialog):
    """Open questions before generating: generate over them (recorded) or go fix them."""

    def __init__(self, parent, questions):
        super().__init__(parent)
        self.setWindowTitle("Open questions")
        self.setMinimumWidth(760)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"<b>{len(questions)} open question(s).</b> Generating now "
                             "records them in the version's manifest as confirmed by you."))
        t = QTableWidget(len(questions), 2)
        t.setHorizontalHeaderLabels(["Question", "Details"])
        t.setColumnWidth(0, 230)
        t.horizontalHeader().setStretchLastSection(True)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        for i, q in enumerate(questions):
            t.setItem(i, 0, QTableWidgetItem(f"{_SEV_LABEL.get(q['severity'], '')} {q['title']}"))
            it = QTableWidgetItem(q['detail'])
            it.setToolTip(chr(10).join(x for x in (q['detail'], q.get('explain')) if x))
            t.setItem(i, 1, it)
        lay.addWidget(t)
        lay.addWidget(QLabel("Note for the manifest (optional):"))
        self.note = QLineEdit()
        self.note.setPlaceholderText("e.g. PCS fault status gap confirmed with the SCADA team")
        lay.addWidget(self.note)
        bb = QDialogButtonBox()
        go = bb.addButton("Generate anyway", QDialogButtonBox.AcceptRole)
        bb.addButton("Cancel — go to the check", QDialogButtonBox.RejectRole)
        go.clicked.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)


def _info_label(text, style=_WARN_STYLE):
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet(style)
    return lbl


# ── PM dialog ─────────────────────────────────────────────────────────────────
class PMDialog(QDialog):
    """One PM entry, checked against the rules every PM writer shares
    (report_workflow_service.validate_pm) before it closes: a block (the whole
    plant only as 'all'), 0 < hours <= 24, sane dates."""

    def __init__(self, parent=None, existing=None, project_id=None, info='', title=None):
        super().__init__(parent)
        self._pid = project_id
        self.setWindowTitle(title or ("Edit PM record" if existing else "Add PM record"))
        self.setMinimumWidth(500)
        lay = QVBoxLayout(self)
        if info:
            lay.addWidget(_info_label(info))
        form = QFormLayout()
        self.date_from = _date(); form.addRow("Date *:", self.date_from)
        self.date_to = _date(); form.addRow("To:", self.date_to)
        self.date_from.dateChanged.connect(self.date_to.setDate)
        self.blocks = QLineEdit()
        self.blocks.setPlaceholderText("41   or   41,42   or   39-42   ·   all = whole plant")
        form.addRow("Block(s) *:", self.blocks)
        self.hours = QDoubleSpinBox(); self.hours.setRange(0, rw.PM_MAX_HOURS)
        self.hours.setDecimals(2); self.hours.setSuffix(" h")
        form.addRow("Hours *:", self.hours)
        self.desc = QTextEdit(); self.desc.setMaximumHeight(70)
        self.desc.setPlaceholderText("What was done (e.g. PM as per the checklist)…")
        form.addRow("Description:", self.desc)
        lay.addLayout(form)
        lay.addWidget(_info_label(
            "One record per block per day — several blocks become one record each. "
            "The hours count against availability for the whole block, and the record "
            "covers the block's own stop in the SCADA data (no exclusion window needed).",
            _HINT_STYLE))
        self.err = _info_label('', _ERR_STYLE); self.err.setVisible(False)
        lay.addWidget(self.err)
        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept); btns.rejected.connect(self.reject)
        lay.addWidget(btns)
        if existing:
            self._prefill(existing)

    def _prefill(self, e):
        d1 = QDate.fromString((e.get('date_from') or '')[:10], "yyyy-MM-dd")
        if d1.isValid(): self.date_from.setDate(d1)
        d2 = QDate.fromString((e.get('date_to') or '')[:10], "yyyy-MM-dd")
        if d2.isValid(): self.date_to.setDate(d2)
        self.blocks.setText(str(e.get('affected_blocks') or ''))
        try:
            self.hours.setValue(min(float(e.get('hours') or 0), rw.PM_MAX_HOURS))
        except (TypeError, ValueError):
            pass
        self.desc.setPlainText(e.get('description', '') or '')

    def get_data(self):
        return {
            'affected_blocks': self.blocks.text().strip(),
            'date_from': self.date_from.date().toString("yyyy-MM-dd"),
            'date_to':   self.date_to.date().toString("yyyy-MM-dd"),
            'hours':     float(self.hours.value()),
            'description': self.desc.toPlainText().strip(),
        }

    def accept(self):
        d = self.get_data()
        try:
            v = rw.validate_pm(self._pid, d['affected_blocks'], d['date_from'],
                               d['date_to'], d['hours'])
        except (rw.PMValidationError, RuntimeError) as e:
            self.err.setText('• ' + '\n• '.join(getattr(e, 'problems', [str(e)])))
            self.err.setVisible(True)
            return
        h = d['hours']
        if h > rw.PM_CONFIRM_HOURS and QMessageBox.question(
                self, "Long PM", f"{h:g} h of PM on one block in one day — is that right?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        if d['affected_blocks'].lower() in ('all', 'all blocks') and QMessageBox.question(
                self, "Whole plant",
                f"PM on the whole plant: {len(v['blocks'])} blocks × {h:g} h = "
                f"{len(v['blocks']) * h:g} block-hours against availability. Save?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        super().accept()


# ── Manual downtime dialog ────────────────────────────────────────────────────
class ManualDowntimeDialog(QDialog):
    """Downtime the SCADA data does not show, entered by hand — a block held in
    STANDBY after a gas alarm until it is inspected, say. Clock times are
    optional; when given, the hours are worked out from them. Checked with
    availability_inputs_service.validate_manual before it closes."""

    def __init__(self, parent=None, project_id=None, existing=None,
                 single_block=False, info='', title=None):
        super().__init__(parent)
        self._pid = project_id
        self._single = single_block
        self.setWindowTitle(title or ("Edit manual unavailability" if existing
                                      else "Add manual unavailability"))
        self.setMinimumWidth(500)
        lay = QVBoxLayout(self)
        if info:
            lay.addWidget(_info_label(info))
        form = QFormLayout()
        self.blocks = QLineEdit()
        self.blocks.setPlaceholderText("41" if single_block else
                                       "41   or   41,42   ·   all = whole plant")
        form.addRow("Block *:" if single_block else "Block(s) *:", self.blocks)
        self.lc = QComboBox()
        self.lc.addItem("Whole block (both LCs)", None)
        self.lc.addItem("LC 1", 1); self.lc.addItem("LC 2", 2)
        form.addRow("LC:", self.lc)
        self.use_times = QCheckBox("Enter clock times (the hours are worked out)")
        form.addRow("", self.use_times)
        r1 = QHBoxLayout(); self.date_from = _date(); self.time_from = _time()
        r1.addWidget(self.date_from); r1.addWidget(QLabel(" at ")); r1.addWidget(self.time_from); r1.addStretch()
        form.addRow("From *:", r1)
        r2 = QHBoxLayout(); self.date_to = _date(); self.time_to = _time()
        r2.addWidget(self.date_to); r2.addWidget(QLabel(" at ")); r2.addWidget(self.time_to); r2.addStretch()
        form.addRow("To *:", r2)
        self.hours = QDoubleSpinBox(); self.hours.setRange(0, 744)
        self.hours.setDecimals(2); self.hours.setSuffix(" h")
        form.addRow("Hours *:", self.hours)
        self.cause = QLineEdit()
        self.cause.setPlaceholderText("e.g. held in STANDBY after gas alarm, waiting for inspection")
        form.addRow("Cause:", self.cause)
        lay.addLayout(form)
        self.err = _info_label('', _ERR_STYLE); self.err.setVisible(False)
        lay.addWidget(self.err)
        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept); btns.rejected.connect(self.reject)
        lay.addWidget(btns)

        self.date_from.dateChanged.connect(self.date_to.setDate)
        for w in (self.date_from, self.date_to):
            w.dateChanged.connect(self._recalc)
        for w in (self.time_from, self.time_to):
            w.timeChanged.connect(self._recalc)
        self.use_times.toggled.connect(self._on_times)
        self._on_times(False)
        if existing:
            self._prefill(existing)

    def _prefill(self, e):
        self.blocks.setText(str(e.get('block') if e.get('block') is not None else ''))
        i = self.lc.findData(e.get('lc'))
        self.lc.setCurrentIndex(i if i >= 0 else 0)
        d1 = QDate.fromString((e.get('date_from') or '')[:10], "yyyy-MM-dd")
        if d1.isValid(): self.date_from.setDate(d1)
        d2 = QDate.fromString((e.get('date_to') or '')[:10], "yyyy-MM-dd")
        if d2.isValid(): self.date_to.setDate(d2)
        tf, tt = (e.get('time_from') or ''), (e.get('time_to') or '')
        if tf and tt:
            self.time_from.setTime(QTime.fromString(tf[:5], "HH:mm"))
            self.time_to.setTime(QTime.fromString(tt[:5], "HH:mm"))
            self.use_times.setChecked(True)
        try:
            self.hours.setValue(float(e.get('downtime_h') or 0))
        except (TypeError, ValueError):
            pass
        self.cause.setText(e.get('cause') or '')

    def _on_times(self, on):
        self.time_from.setEnabled(on); self.time_to.setEnabled(on)
        self.hours.setReadOnly(on)
        self._recalc()

    def _recalc(self, *_):
        if not self.use_times.isChecked():
            return
        a = QDateTime(self.date_from.date(), self.time_from.time())
        z = QDateTime(self.date_to.date(), self.time_to.time())
        self.hours.setValue(max(0.0, a.secsTo(z) / 3600.0))

    def get_data(self):
        times = self.use_times.isChecked()
        return {
            'blocks': self.blocks.text().strip(),
            'lc': self.lc.currentData(),
            'date_from': self.date_from.date().toString("yyyy-MM-dd"),
            'date_to': self.date_to.date().toString("yyyy-MM-dd"),
            'time_from': self.time_from.time().toString("HH:mm") if times else '',
            'time_to': self.time_to.time().toString("HH:mm") if times else '',
            'hours': float(self.hours.value()),
            'cause': self.cause.text().strip(),
        }

    def accept(self):
        d = self.get_data()
        problems = []
        if d['time_from'] and d['time_to'] and \
                f"{d['date_to']} {d['time_to']}" <= f"{d['date_from']} {d['time_from']}":
            problems.append("the end is not after the start")
        try:
            v = avi.validate_manual(self._pid, d['blocks'], d['date_from'], d['date_to'],
                                    d['hours'], d['lc'], '', '')
            if self._single and len(v['blocks']) != 1:
                problems.append("one row is one block - add the other blocks as new rows")
        except (avi.InputValidationError, RuntimeError) as e:
            problems += getattr(e, 'problems', [str(e)])
        if problems:
            self.err.setText('• ' + '\n• '.join(problems))
            self.err.setVisible(True)
            return
        super().accept()


def _date():
    from PyQt5.QtWidgets import QDateEdit
    d = QDateEdit(); d.setCalendarPopup(True); d.setDisplayFormat("yyyy-MM-dd")
    d.setDate(QDate.currentDate()); return d


def _time():
    t = QTimeEdit(); t.setDisplayFormat("HH:mm"); t.setFixedWidth(80)
    return t


def _table(headers, widths, max_height=190):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.hideColumn(0)
    for i, w in enumerate(widths):
        t.setColumnWidth(i, w)
    t.horizontalHeader().setStretchLastSection(True)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setMaximumHeight(max_height); t.setAlternatingRowColors(True)
    return t


def _fill_row(table, values, flags=None):
    row = table.rowCount(); table.insertRow(row)
    for c, v in enumerate(values):
        it = QTableWidgetItem(str(v))
        if flags:
            it.setForeground(QColor('#B4232A'))
            it.setToolTip('\n'.join(flags))
        table.setItem(row, c, it)


class MonthlyReportsPage(QWidget):
    # The availability inputs moved to their own page in the redesign; this
    # page keeps the four steps and links to them.
    open_availability = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pid = None; self._year = None; self._month = None
        self._site_type = 'tashkent'
        self._worker = None         # generation
        self._an_worker = None      # data set analysis
        self._wr_rows = []          # work reports auto-pulled for the month
        self._inputs = None         # availability_inputs_service.month_inputs
        self._questions = []        # last pre-generation check
        self._edit_buttons = []     # disabled while the month is locked
        self._lock_banners = []
        self._build_ui()
        self._reload_projects()

    # ── UI ────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(PageHeader("Monthly Reports",
                                  "Pick a project and month · 1 Data · 2 Check · 3 Customer text · 4 Release"))

        # ── Selector bar ────────────────────────────────────────────────
        bar = QHBoxLayout(); bar.setContentsMargins(16, 6, 16, 6)
        bar.addWidget(QLabel("Project:"))
        self.proj_combo = QComboBox(); self.proj_combo.setMinimumWidth(200)
        self.proj_combo.currentIndexChanged.connect(self._on_project)
        bar.addWidget(self.proj_combo)
        bar.addWidget(QLabel("Year:"))
        self.year_spin = QSpinBox(); self.year_spin.setRange(2020, 2100)
        self.year_spin.setValue(QDate.currentDate().year())
        bar.addWidget(self.year_spin)
        bar.addWidget(QLabel("Month:"))
        self.month_combo = QComboBox(); self.month_combo.addItems(MONTHS_EN[1:])
        self.month_combo.setCurrentIndex(QDate.currentDate().month() - 1)
        bar.addWidget(self.month_combo)
        load_btn = PrimaryButton("📂  Open month")
        load_btn.clicked.connect(self._open_month)
        bar.addWidget(load_btn)
        # The month's inputs (waiting phone events, PM, manual downtime,
        # windows) are a page of their own now — one click away, not a tab.
        avail_btn = SecondaryButton("⚖  Availability inputs")
        avail_btn.setToolTip("Phone events, PM records, manual downtime and "
                             "exclusion windows — on the Availability page")
        avail_btn.clicked.connect(lambda: self.open_availability.emit())
        bar.addWidget(avail_btn)
        self.month_lbl = QLabel(""); self.month_lbl.setStyleSheet("color:#6B7A8D;font-style:italic;")
        bar.addWidget(self.month_lbl)
        bar.addStretch()
        root.addLayout(bar)

        self.tabs = QTabWidget(); self.tabs.setEnabled(False)
        root.addWidget(self.tabs, 1)
        self._build_inputs_tab()
        self._build_data_tab()
        self._build_check_tab()
        self._build_text_tab()
        self._build_release_tab()

    def _lock_banner(self, layout):
        lbl = _info_label("", _LOCK_STYLE)
        lbl.setVisible(False)
        layout.addWidget(lbl)
        self._lock_banners.append(lbl)
        return lbl

    def _editing(self, *buttons):
        self._edit_buttons.extend(buttons)

    # Availability inputs tab — everything besides SCADA that moves availability
    def _build_inputs_tab(self):
        w = QWidget(); outer = QVBoxLayout(w)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        inner = QWidget(); l = QVBoxLayout(inner)
        self._lock_banner(l)

        # Phone events waiting for the desktop
        self.pend_group = QGroupBox("Waiting for the desktop — phone events not in the report yet")
        pl = QVBoxLayout(self.pend_group)
        pr = QHBoxLayout()
        b = PrimaryButton("✔  Review / confirm…"); b.clicked.connect(self._review_pending); pr.addWidget(b)
        self._editing(b)
        b = SecondaryButton("✖  Reject…"); b.clicked.connect(self._reject_pending); pr.addWidget(b)
        self._editing(b)
        pr.addStretch(); pl.addLayout(pr)
        self.pend_table = _table(["ID", "Type", "Received", "What the phone sent", "Why it waits"],
                                 [0, 150, 130, 330], max_height=160)
        self.pend_table.doubleClicked.connect(lambda *_: self._review_pending())
        pl.addWidget(self.pend_table)
        pl.addWidget(_info_label(
            "Downtime and exclusions from phones count only after you confirm them here "
            "with the real times and blocks. PM from a phone is applied on arrival, unless "
            "it needs a decision (no block, hours out of range, or a PM record already on "
            "that block and day).", _HINT_STYLE))
        l.addWidget(self.pend_group)

        # PM records
        g = QGroupBox("Preventive maintenance — one record per block per day")
        gl = QVBoxLayout(g)
        br = QHBoxLayout()
        b1 = PrimaryButton("➕  Add PM"); b1.clicked.connect(self._add_pm); br.addWidget(b1)
        b2 = SecondaryButton("✏️  Edit"); b2.clicked.connect(self._edit_pm); br.addWidget(b2)
        b3 = SecondaryButton("🗑  Delete"); b3.clicked.connect(self._del_pm); br.addWidget(b3)
        self._editing(b1, b2, b3)
        br.addStretch()
        self.pm_summary = QLabel(""); self.pm_summary.setStyleSheet("color:#6B7A8D;font-style:italic;")
        br.addWidget(self.pm_summary)
        gl.addLayout(br)
        self.pm_table = _table(["ID", "Date", "Block", "Hours", "Source", "Description", "Check"],
                               [0, 90, 60, 55, 100, 220], max_height=230)
        self.pm_table.doubleClicked.connect(lambda *_: self._edit_pm())
        gl.addWidget(self.pm_table)
        gl.addWidget(_info_label(
            "PM counts as unavailability: its hours reduce availability for the whole block. "
            "A PM record also covers the block's own stop in the SCADA data that day, so no "
            "exclusion window is needed for PM. A second record for the same block and day "
            "is not counted — the Check column says so.", _HINT_STYLE))
        l.addWidget(g)

        # Manual unavailability
        mg = QGroupBox("Manual unavailability — downtime SCADA does not show")
        ml = QVBoxLayout(mg)
        mr = QHBoxLayout()
        for txt, fn in [("➕  Add", self._add_man), ("✏️  Edit", self._edit_man), ("🗑  Delete", self._del_man)]:
            b = (PrimaryButton if txt.startswith("➕") else SecondaryButton)(txt); b.clicked.connect(fn)
            mr.addWidget(b); self._editing(b)
        mr.addStretch(); ml.addLayout(mr)
        self.man_table = _table(["ID", "Block", "LC", "From", "To", "Hours", "Source", "Cause", "Check"],
                                [0, 55, 60, 120, 120, 55, 100, 200])
        self.man_table.doubleClicked.connect(lambda *_: self._edit_man())
        ml.addWidget(self.man_table)
        ml.addWidget(_info_label(
            "For a block or LC that was not available although SCADA counts it as available — "
            "e.g. held in STANDBY after a gas alarm until inspected. Work Reports marked "
            "“counts” add rows here too.", _HINT_STYLE))
        l.addWidget(mg)

        # Exclusions
        eg = QGroupBox("Availability Exclusions"); el = QVBoxLayout(eg)
        er = QHBoxLayout()
        for txt, fn in [("➕  Add", self._add_excl), ("✏️  Edit", self._edit_excl), ("🗑  Delete", self._del_excl)]:
            b = (PrimaryButton if txt.startswith("➕") else SecondaryButton)(txt); b.clicked.connect(fn); er.addWidget(b)
            self._editing(b)
        er.addStretch(); el.addLayout(er)
        self.excl_table = _table(["ID", "Type", "From", "To", "Blocks", "Description"], [0, 150, 130, 130, 100])
        self.excl_table.doubleClicked.connect(lambda *_: self._edit_excl())
        el.addWidget(self.excl_table); l.addWidget(eg)

        # Balancing
        bg = QGroupBox("Cycle-Balancing / Rested Blocks"); bl = QVBoxLayout(bg)
        brow = QHBoxLayout()
        for txt, fn in [("➕  Add", self._add_bal), ("✏️  Edit", self._edit_bal), ("🗑  Delete", self._del_bal)]:
            b = (PrimaryButton if txt.startswith("➕") else SecondaryButton)(txt); b.clicked.connect(fn); brow.addWidget(b)
            self._editing(b)
        brow.addStretch(); bl.addLayout(brow)
        self.bal_table = _table(["ID", "From", "To", "Blocks", "Note"], [0, 110, 110, 150])
        self.bal_table.doubleClicked.connect(lambda *_: self._edit_bal())
        bl.addWidget(self.bal_table); l.addWidget(bg)

        l.addStretch(); scroll.setWidget(inner); outer.addWidget(scroll)
        self._inputs_tab = w
        self.tabs.addTab(w, "Availability inputs")

    def take_inputs_tab(self):
        """Hand the inputs editor to the Availability page.

        The widget, its tables, its dialogs and the month lock move as they
        are — the page that adopts it shows the same editor, not a copy, so
        there is one implementation of the validation and one writer of the
        month's inputs. Called once by the shell at start-up."""
        i = self.tabs.indexOf(self._inputs_tab)
        if i >= 0:
            self.tabs.removeTab(i)
        self._inputs_tab.setParent(None)
        return self._inputs_tab

    # ── 1 · Data ──────────────────────────────────────────────────────────
    def _build_data_tab(self):
        w = QWidget(); outer = QVBoxLayout(w)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        inner = QWidget(); l = QVBoxLayout(inner)
        self._lock_banner(l)

        top = QHBoxLayout()
        txt = QLabel("<b>Add the month's SCADA exports</b> — files or a whole folder. Types are "
                     "recognised from the column headers; files are copied into the month and "
                     "never picked again.")
        txt.setWordWrap(True); top.addWidget(txt, 1)
        self.add_files_btn = PrimaryButton("➕  Add files…"); self.add_files_btn.clicked.connect(self._add_files)
        self.add_folder_btn = SecondaryButton("📁  Add folder…"); self.add_folder_btn.clicked.connect(self._add_folder)
        top.addWidget(self.add_files_btn); top.addWidget(self.add_folder_btn)
        self._editing(self.add_files_btn, self.add_folder_btn)
        l.addLayout(top)

        self.last_add_group = QGroupBox("Last upload"); lg = QVBoxLayout(self.last_add_group)
        self.last_add_table = _table(["#", "File", "Result", "Recognised as", "Note"],
                                     [0, 260, 90, 190], max_height=200)
        lg.addWidget(self.last_add_table)
        self.last_add_group.setVisible(False)
        l.addWidget(self.last_add_group)

        fg = QGroupBox("Files in the month"); fl = QVBoxLayout(fg)
        fr = QHBoxLayout()
        self.data_status = QLabel(""); self.data_status.setStyleSheet(_HINT_STYLE)
        fr.addWidget(self.data_status, 1)
        self.remove_file_btn = SecondaryButton("🗑  Remove from month")
        self.remove_file_btn.clicked.connect(self._remove_file)
        self._editing(self.remove_file_btn)
        b = SecondaryButton("📂  Open folder"); b.clicked.connect(self._open_month_folder)
        fr.addWidget(self.remove_file_btn); fr.addWidget(b)
        fl.addLayout(fr)
        self.files_table = _table(["ID", "Type", "File", "Data", "Added", "SHA-256"],
                                  [0, 190, 250, 230, 120], max_height=320)
        fl.addWidget(self.files_table)
        l.addWidget(fg)

        cg = QGroupBox("Coverage by day"); cl = QVBoxLayout(cg)
        self.cov_table = QTableWidget(0, 0)
        self.cov_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.cov_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.cov_table.horizontalHeader().setDefaultSectionSize(24)
        self.cov_table.verticalHeader().setDefaultSectionSize(20)
        self.cov_table.setMaximumHeight(260)
        cl.addWidget(self.cov_table)
        legend = QLabel("green — data · amber — part of the day · grey — no data · light — not due yet")
        legend.setStyleSheet(_HINT_STYLE); cl.addWidget(legend)
        self.month_end_lbl = QLabel(""); self.month_end_lbl.setWordWrap(True)
        cl.addWidget(self.month_end_lbl)
        self.analyse_lbl = QLabel(""); self.analyse_lbl.setStyleSheet(_HINT_STYLE)
        cl.addWidget(self.analyse_lbl)
        l.addWidget(cg)

        l.addStretch(); scroll.setWidget(inner); outer.addWidget(scroll)
        self._data_tab = w
        self.tabs.addTab(w, "1 · Data")

    # ── 2 · Check ─────────────────────────────────────────────────────────
    def _build_check_tab(self):
        w = QWidget(); l = QVBoxLayout(w)
        top = QHBoxLayout()
        self.run_check_btn = PrimaryButton("🔎  Run the check"); self.run_check_btn.clicked.connect(self._run_check)
        top.addWidget(self.run_check_btn)
        hint = QLabel("Questions before generating. The check never changes an input — each "
                      "question opens the place where it is fixed.")
        hint.setStyleSheet(_HINT_STYLE); hint.setWordWrap(True)
        top.addWidget(hint, 1)
        l.addLayout(top)
        self.check_summary = QLabel("Not run yet for this month.")
        l.addWidget(self.check_summary)
        self.check_table = QTableWidget(0, 4)
        self.check_table.setHorizontalHeaderLabels(["", "Question", "Details", "Fix"])
        self.check_table.setColumnWidth(0, 28); self.check_table.setColumnWidth(1, 230)
        self.check_table.setColumnWidth(3, 170)
        self.check_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.check_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.check_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.check_table.setWordWrap(True)
        l.addWidget(self.check_table, 1)
        self._check_tab = w
        self.tabs.addTab(w, "2 · Check")

    # ── 3 · Customer text ─────────────────────────────────────────────────
    def _build_text_tab(self):
        w = QWidget(); outer = QVBoxLayout(w)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        inner = QWidget(); l = QVBoxLayout(inner)
        self._lock_banner(l)

        ng = QGroupBox("Narrative"); f = QFormLayout(ng)
        self.report_number = QLineEdit(); f.addRow("Report No.:", self.report_number)
        self.recommendations = QTextEdit(); self.recommendations.setMaximumHeight(80)
        f.addRow("Recommendations:", self.recommendations)
        self.planned_next = QTextEdit(); self.planned_next.setMaximumHeight(80)
        f.addRow("Planned next period:", self.planned_next)
        self.safety_incidents = QTextEdit(); self.safety_incidents.setMaximumHeight(80)
        self.safety_incidents.setPlaceholderText(
            "One incident per line — optionally: incident | equipment loss | weight | countermeasure")
        f.addRow("Safety incidents:", self.safety_incidents)
        hint = QLabel("Printed in section 6 of the report. Leave empty (or “None”) and the "
                      "report says there were no safety incidents.")
        hint.setStyleSheet(_HINT_STYLE); hint.setWordWrap(True)
        f.addRow("", hint)
        self.site_visits = QTextEdit(); self.site_visits.setMaximumHeight(60)
        f.addRow("Site visits:", self.site_visits)
        concl = QLabel("Conclusions (section 7) are written by the report from the month's numbers; "
                       "the recommendations and planned activities above follow them.")
        concl.setStyleSheet(_HINT_STYLE); concl.setWordWrap(True)
        f.addRow("Conclusions:", concl)
        save = PrimaryButton("💾  Save customer text"); save.clicked.connect(self._save_narrative)
        self._editing(save)
        r = QHBoxLayout(); r.addStretch(); r.addWidget(save); f.addRow("", _wrap(r))
        l.addWidget(ng)

        # Corrective Maintenance auto-pulled from the month's Work Reports
        wrg = QGroupBox("3.2 Corrective maintenance — from Work Reports and the phone Field Log")
        wrl = QVBoxLayout(wrg)
        hdr = QHBoxLayout()
        self.cm_include = QCheckBox("Include these work reports in the monthly report")
        self.cm_include.setChecked(True)
        self.cm_include.toggled.connect(lambda *_: self._refresh_cm_preview())
        hdr.addWidget(self.cm_include)
        hdr.addStretch()
        self.wr_count_lbl = QLabel("—")
        self.wr_count_lbl.setStyleSheet("color:#6B7A8D;font-style:italic;")
        self.wr_count_lbl.setWordWrap(True)
        hdr.addWidget(self.wr_count_lbl)
        wrl.addLayout(hdr)
        act = QHBoxLayout()
        b = SecondaryButton("✏️  Edit record…"); b.clicked.connect(self._edit_corrective); act.addWidget(b)
        b = SecondaryButton("⚖  Resolve conflict…"); b.clicked.connect(self._resolve_corrective); act.addWidget(b)
        act.addStretch()
        refresh_wr = SecondaryButton("⟲  Refresh")
        refresh_wr.clicked.connect(self._refresh_work_reports)
        act.addWidget(refresh_wr)
        wrl.addLayout(act)
        self.wr_table = _table(
            ["#", "Date", "Block", "Fault", "Action", "SAP", "Status", "In 3.2"],
            [0, 90, 50, 190, 190, 70, 60], max_height=260)
        self.wr_table.doubleClicked.connect(lambda *_: self._edit_corrective())
        wrl.addWidget(self.wr_table)
        wr_hint = QLabel("🖥 desktop Work Reports + 📱 mobile Field Log entries "
                         "(fault / repair / maintenance) for this month. A line reaches "
                         "section 3.2 only when it is finished, has a plant block, is not "
                         "PM and is not in a sync conflict — “Edit record…” sets the block "
                         "(it syncs back to the phones; a clash goes through Resolve).")
        wr_hint.setStyleSheet(_HINT_STYLE); wr_hint.setWordWrap(True)
        wrl.addWidget(wr_hint)

        # Month-end reconciliation: faults SCADA saw that nobody wrote up.
        self.gap_lbl = QLabel("")
        self.gap_lbl.setWordWrap(True)
        self.gap_lbl.setVisible(False)
        self.gap_lbl.setStyleSheet(
            "background:#FFF4F4;border:1px solid #F3C9C9;border-radius:6px;"
            "padding:8px 10px;color:#9B2C2C;font-size:11px;")
        wrl.addWidget(self.gap_lbl)
        l.addWidget(wrg)

        cmg = QGroupBox("Additional corrective notes (one per line)")
        cml = QVBoxLayout(cmg)
        self.cm_text = QTextEdit()
        self.cm_text.setMaximumHeight(90)
        self.cm_text.setPlaceholderText("Extra corrective actions not captured as Work Reports…")
        self.cm_text.textChanged.connect(self._refresh_cm_preview)
        cml.addWidget(self.cm_text)
        save = SecondaryButton("💾  Save notes"); save.clicked.connect(self._save_narrative)
        self._editing(save)
        r = QHBoxLayout(); r.addStretch(); r.addWidget(save); cml.addLayout(r)
        l.addWidget(cmg)

        pg = QGroupBox("3.2 as it will print"); pgl = QVBoxLayout(pg)
        self.cm_preview = QTextEdit(); self.cm_preview.setReadOnly(True)
        self.cm_preview.setMaximumHeight(150)
        pgl.addWidget(self.cm_preview)
        self.cm_preview_lbl = QLabel(""); self.cm_preview_lbl.setStyleSheet(_HINT_STYLE)
        self.cm_preview_lbl.setWordWrap(True)
        pgl.addWidget(self.cm_preview_lbl)
        l.addWidget(pg)

        l.addStretch(); scroll.setWidget(inner); outer.addWidget(scroll)
        self._text_tab = w
        self.tabs.addTab(w, "3 · Customer text")

    # ── 4 · Release ───────────────────────────────────────────────────────
    def _build_release_tab(self):
        w = QWidget(); outer = QVBoxLayout(w)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        inner = QWidget(); l = QVBoxLayout(inner)

        vg = QGroupBox("Versions"); vl = QVBoxLayout(vg)
        r = QHBoxLayout()
        self.gen_btn = PrimaryButton("⚡  Generate v1"); self.gen_btn.setMinimumWidth(180)
        self.gen_btn.clicked.connect(self._generate); r.addWidget(self.gen_btn)
        b = SecondaryButton("📄  Open DOCX"); b.clicked.connect(self._open_docx); r.addWidget(b)
        b = SecondaryButton("🧾  Open manifest"); b.clicked.connect(self._open_manifest); r.addWidget(b)
        b = SecondaryButton("📂  Open folder"); b.clicked.connect(self._open_month_folder); r.addWidget(b)
        r.addStretch()
        vl.addLayout(r)
        self.versions_table = _table(
            ["ID", "Version", "Generated", "Availability", "RTE", "Cycles m / y / total",
             "SOC", "SOH", "What changed", "Status"],
            [0, 60, 120, 85, 70, 130, 60, 65, 330], max_height=240)
        vl.addWidget(self.versions_table)
        vh = QLabel("Each generation is a new file — nothing is overwritten. Generated files are "
                    "read-only: Word opens them read-only, so make your edits and Save As, then mark "
                    "that file as sent. Numbers and row counts are compared with the version before.")
        vh.setStyleSheet(_HINT_STYLE); vh.setWordWrap(True)
        vl.addWidget(vh)
        self.progress = QProgressBar(); self.progress.setRange(0, 0); self.progress.setVisible(False)
        self.progress.setFixedHeight(6); vl.addWidget(self.progress)
        self.log = QTextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(150)
        self.log.setStyleSheet("QTextEdit{background:#1A2B45;color:#8FA3BE;font-family:Consolas,monospace;font-size:11px;border-radius:4px;padding:6px;}")
        vl.addWidget(self.log)
        l.addWidget(vg)

        sg = QGroupBox("Sent and locked"); sl = QVBoxLayout(sg)
        self.sent_lbl = QLabel("No version marked as sent."); self.sent_lbl.setWordWrap(True)
        sl.addWidget(self.sent_lbl)
        sr = QHBoxLayout()
        self.mark_sent_btn = PrimaryButton("✉  Mark final DOCX as sent…")
        self.mark_sent_btn.clicked.connect(self._mark_sent)
        self.unlock_btn = SecondaryButton("🔓  Unlock month…"); self.unlock_btn.clicked.connect(self._unlock_month)
        self.relock_btn = SecondaryButton("🔒  Lock again"); self.relock_btn.clicked.connect(self._relock_month)
        for b in (self.mark_sent_btn, self.unlock_btn, self.relock_btn):
            sr.addWidget(b)
        sr.addStretch(); sl.addLayout(sr)
        sh = QLabel("You edit the generated file in Word and send that one. Pick the edited file: the "
                    "app keeps a copy and its SHA-256 with the version it is based on, and the month's "
                    "inputs, data set and customer text become read-only. Unlocking needs a reason; "
                    "it is recorded.")
        sh.setStyleSheet(_HINT_STYLE); sh.setWordWrap(True)
        sl.addWidget(sh)
        self.month_log_lbl = QLabel(""); self.month_log_lbl.setStyleSheet(_HINT_STYLE)
        self.month_log_lbl.setWordWrap(True)
        sl.addWidget(self.month_log_lbl)
        l.addWidget(sg)

        l.addStretch(); scroll.setWidget(inner); outer.addWidget(scroll)
        self._release_tab = w
        self.tabs.addTab(w, "4 · Release")

    # ── Data plumbing ────────────────────────────────────────────────────
    def _reload_projects(self):
        self.proj_combo.blockSignals(True); self.proj_combo.clear()
        for p in rw.list_projects():
            self.proj_combo.addItem(p['name'], p['id'])
        self.proj_combo.blockSignals(False)

    def showEvent(self, e):
        # refresh the project list every time the page is shown (new projects)
        cur = self.proj_combo.currentData()
        self._reload_projects()
        if cur is not None:
            i = self.proj_combo.findData(cur)
            if i >= 0: self.proj_combo.setCurrentIndex(i)
        super().showEvent(e)

    def set_current_project(self, pid):
        """Called by the shell when a project is opened in the launcher —
        pre-selects it here (the month is still opened explicitly)."""
        if pid is None:
            return
        i = self.proj_combo.findData(pid)
        if i >= 0:
            self.proj_combo.setCurrentIndex(i)

    def set_month(self, year: int, month: int):
        """The shell's header owns the month; Availability, Monthly report and
        Analysis follow it. Selecting a month here does not open it — opening
        writes the month's row, and that stays an explicit act."""
        if not year or not month:
            return
        self.year_spin.setValue(int(year))
        self.month_combo.setCurrentIndex(int(month) - 1)
        if self._pid is not None and (self._year, self._month) != (year, month):
            self._open_month()          # a month is already open: follow along

    def _on_project(self, *_):
        pass  # month is opened explicitly via the button

    def _blocks_list(self):
        proj = rw.get_project(self._pid) or {}
        n = int(proj.get('num_blocks') or 15)
        return list(range(1, n + 1))

    def _default_date(self):
        """Today when it lies in the open month, else the month's first day."""
        today = QDate.currentDate()
        if today.year() == self._year and today.month() == self._month:
            return today
        return QDate(self._year, self._month, 1)

    def _open_month(self):
        pid = self.proj_combo.currentData()
        if pid is None:
            QMessageBox.information(self, "No project", "Create a project first (Projects page)."); return
        self._pid = pid
        self._year = self.year_spin.value()
        self._month = self.month_combo.currentIndex() + 1
        rw.ensure_report_month(self._pid, self._year, self._month)
        cfg = rw.get_project_config(self._pid)
        self._site_type = (cfg.get('site_type') or 'tashkent')
        self._questions = []
        self.tabs.setEnabled(True)
        self.month_lbl.setText(f"{self.proj_combo.currentText()} — {MONTHS_EN[self._month]} {self._year} · type: {self._site_type}")
        self._load_all()

    def _load_all(self):
        self._refresh_inputs(); self._refresh_excl(); self._refresh_bal()
        self._refresh_work_reports()
        m = rw.get_report_month(self._pid, self._year, self._month) or {}
        self.report_number.setText(m.get('report_number', '') or '')
        self.cm_text.setPlainText(m.get('cm_activities', '') or '')
        self.site_visits.setPlainText(m.get('site_visits', '') or '')
        self.recommendations.setPlainText(m.get('recommendations', '') or '')
        self.planned_next.setPlainText(m.get('planned_next', '') or '')
        self.safety_incidents.setPlainText(m.get('safety_incidents', '') or '')
        self._fill_check(None)
        self._refresh_data()
        self._refresh_release()
        self._analyse()

    def _save_narrative(self):
        """Save the month's customer text. False when the month is locked."""
        if self._pid is None: return False
        try:
            rw.save_report_month(self._pid, self._year, self._month,
                report_number=self.report_number.text().strip(),
                cm_activities=self.cm_text.toPlainText().strip(),
                site_visits=self.site_visits.toPlainText().strip(),
                recommendations=self.recommendations.toPlainText().strip(),
                planned_next=self.planned_next.toPlainText().strip(),
                safety_incidents=self.safety_incidents.toPlainText().strip())
        except rw.MonthLockedError as e:
            QMessageBox.information(self, "Month locked", str(e))
            return False
        self.log.append("Saved month narrative.") if hasattr(self, 'log') else None
        return True

    # ── Locked month ──────────────────────────────────────────────────────
    def _apply_lock(self):
        """Mirror the service's lock: edit buttons off, text read-only, banners."""
        locked = bool(self._pid and rw.month_locked_at(self._pid, self._year, self._month))
        sent = rvs.latest_sent(self._pid, self._year, self._month) if locked else None
        txt = (f"🔒  {MONTHS_EN[self._month]} {self._year} is locked — the report was marked as sent"
               + (f" (v{sent['version']}, {sent['sent_original_name']})" if sent else "")
               + ". Its inputs, data set and customer text are read-only. "
                 "Unlock it on 4 · Release, with a reason, to change them.") if locked else ""
        for lbl in self._lock_banners:
            lbl.setText(txt); lbl.setVisible(locked)
        for b in self._edit_buttons:
            b.setEnabled(not locked)
        for wdg in (self.report_number, self.recommendations, self.planned_next,
                    self.safety_incidents, self.site_visits, self.cm_text):
            wdg.setReadOnly(locked)
        return locked

    def _locked_warning(self, e):
        QMessageBox.information(self, "Month locked", str(e))

    # ── Availability inputs: PM, manual downtime, phone events ───────────
    def _refresh_inputs(self):
        if self._pid is None:
            return
        data = avi.month_inputs(self._pid, self._year, self._month)
        self._inputs = data

        self.pend_table.setRowCount(0)
        kinds = {'pm': '🧰 PM', 'counts': '⛔ Downtime — counts', 'excluded': '➖ Excluded'}
        reasons = {'confirm': 'needs times and blocks confirmed',
                   'duplicate': 'a PM record already exists', 'invalid': 'cannot be applied as sent',
                   'locked': 'the month was locked (report sent)'}
        for q in data['pending']:
            why = reasons.get(q['reason'], q['reason'])
            if q.get('note'):
                why += ' — ' + q['note']
            _fill_row(self.pend_table, [q['event_id'], kinds.get(q['kind'], q['kind']),
                                        (q.get('received_at') or '')[:16], q['summary'], why])
        self.pend_group.setVisible(bool(data['pending']))

        self.pm_table.setRowCount(0)
        for r in data['pm']:
            blk = r.get('affected_blocks') or '—'
            _fill_row(self.pm_table,
                      [r['id'], r.get('date_from', '') or '', blk, rw.fmt_hours(r.get('hours')),
                       r['source_label'], r.get('description', '') or '',
                       '; '.join(r['flags']) or '✓'], r['flags'])
        t = data['totals']
        self.pm_summary.setText(
            f"{t['pm_records']} record(s) · {t['pm_block_days']} block-day(s) · "
            f"{rw.fmt_hours(t['pm_hours'])} h charged")

        self.man_table.setRowCount(0)
        for r in data['manual']:
            frm = f"{r.get('date_from', '')} {r.get('time_from') or ''}".strip()
            to = f"{r.get('date_to', '')} {r.get('time_to') or ''}".strip()
            _fill_row(self.man_table,
                      [r['id'], r.get('block', ''), r.get('lc') or 'both', frm, to,
                       rw.fmt_hours(r.get('downtime_h')), r['source_label'],
                       r.get('cause', '') or '', '; '.join(r['flags']) or '✓'], r['flags'])

        i = self.tabs.indexOf(self._inputs_tab)
        n = t['pending']
        if i >= 0:          # -1 once the editor lives on the Availability page
            self.tabs.setTabText(i, "Availability inputs"
                                 + (f"  ({n} waiting)" if n else ""))

    # kept for callers that refresh one table
    def _refresh_pm(self):
        self._refresh_inputs()

    def _refresh_man(self):
        self._refresh_inputs()

    def _selected_id(self, table):
        r = table.currentRow()
        if r < 0 or table.item(r, 0) is None:
            return None
        return table.item(r, 0).text()

    def _write_pm(self, write, hours):
        """Run a PM write. When a block-day already has a record, ask which
        hours are right — one PM is charged once — and write accordingly."""
        try:
            return write('raise')
        except (rw.PMValidationError, RuntimeError) as e:
            QMessageBox.warning(self, "PM not saved", '\n'.join(getattr(e, 'problems', [str(e)])))
            return None
        except rw.PMDuplicateError as e:
            lines = []
            for d in e.duplicates:
                ex = d['existing']
                lines.append(f"•  block {d['block']} on {d['date']}: {rw.fmt_hours(ex.get('hours'))} h"
                             f" ({avi.SOURCE_LABELS.get(ex.get('source') or '', 'entered earlier')})"
                             f" — {ex.get('description') or ''}")
            box = QMessageBox(self)
            box.setWindowTitle("PM already recorded")
            box.setIcon(QMessageBox.Question)
            box.setText("These block-days already have a PM record:\n\n" + '\n'.join(lines)
                        + "\n\nA PM is charged once. Which hours are right?")
            repl = box.addButton(f"Use {rw.fmt_hours(hours)} h", QMessageBox.DestructiveRole)
            keep = box.addButton("Keep the existing hours", QMessageBox.AcceptRole)
            box.addButton(QMessageBox.Cancel)
            box.exec_()
            if box.clickedButton() is repl:
                mode = 'update'
            elif box.clickedButton() is keep:
                mode = 'skip'
            else:
                return None
            try:
                return write(mode)
            except (rw.PMValidationError, rw.PMDuplicateError, RuntimeError) as e2:
                QMessageBox.warning(self, "PM not saved", str(e2))
                return None

    def _add_pm(self):
        if self._pid is None: return
        dlg = PMDialog(self, project_id=self._pid)
        dlg.date_from.setDate(self._default_date())
        if dlg.exec_() != QDialog.Accepted: return
        d = dlg.get_data()
        self._write_pm(lambda mode: rw.record_pm(
            self._pid, d['affected_blocks'], d['date_from'], d['date_to'], d['hours'],
            d['description'], source='desktop', on_duplicate=mode), d['hours'])
        self._refresh_inputs()

    def _edit_pm(self):
        pm_id = self._selected_id(self.pm_table)
        if pm_id is None: return
        existing = rw.get_pm_record(int(pm_id))
        if not existing: return
        dlg = PMDialog(self, existing=existing, project_id=self._pid)
        if dlg.exec_() != QDialog.Accepted: return
        d = dlg.get_data()
        try:
            rw.update_pm_record(int(pm_id), d['affected_blocks'], d['date_from'],
                                d['date_to'], d['hours'], d['description'])
        except rw.PMDuplicateError as e:
            QMessageBox.warning(self, "PM already recorded",
                                f"{e}\n\nEdit or delete that record instead.")
        except (rw.PMValidationError, RuntimeError) as e:
            QMessageBox.warning(self, "PM not saved", '\n'.join(getattr(e, 'problems', [str(e)])))
        self._refresh_inputs()

    def _del_pm(self):
        pm_id = self._selected_id(self.pm_table)
        if pm_id is None: return
        r = rw.get_pm_record(int(pm_id)) or {}
        if QMessageBox.question(
                self, "Delete PM record",
                f"Delete PM record #{pm_id} — block {r.get('affected_blocks')}, "
                f"{r.get('date_from')}, {rw.fmt_hours(r.get('hours'))} h?\n"
                "Its hours leave the report.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            rw.delete_pm_activity(int(pm_id))
        except rw.MonthLockedError as e:
            self._locked_warning(e)
        self._refresh_inputs()

    def _manual_row(self, entry_id):
        for r in (self._inputs or {}).get('manual', []):
            if str(r['id']) == str(entry_id):
                return r
        return None

    def _add_man(self):
        if self._pid is None: return
        dlg = ManualDowntimeDialog(self, project_id=self._pid)
        dlg.date_from.setDate(self._default_date())
        if dlg.exec_() != QDialog.Accepted: return
        d = dlg.get_data()
        try:
            avi.record_manual(self._pid, d['blocks'], d['date_from'], d['date_to'], d['hours'],
                              d['lc'], d['cause'], d['time_from'], d['time_to'], source='desktop')
        except (avi.InputValidationError, RuntimeError) as e:
            QMessageBox.warning(self, "Not saved", '\n'.join(getattr(e, 'problems', [str(e)])))
        self._refresh_inputs()

    def _edit_man(self):
        mid = self._selected_id(self.man_table)
        row = self._manual_row(mid) if mid is not None else None
        if not row: return
        dlg = ManualDowntimeDialog(self, project_id=self._pid, existing=row, single_block=True)
        if dlg.exec_() != QDialog.Accepted: return
        d = dlg.get_data()
        try:
            avi.update_manual(int(mid), self._pid, d['blocks'], d['date_from'], d['date_to'],
                              d['hours'], d['lc'], d['cause'], d['time_from'], d['time_to'])
        except (avi.InputValidationError, RuntimeError) as e:
            QMessageBox.warning(self, "Not saved", '\n'.join(getattr(e, 'problems', [str(e)])))
        self._refresh_inputs()

    def _del_man(self):
        mid = self._selected_id(self.man_table)
        if mid is None: return
        row = self._manual_row(mid) or {}
        if QMessageBox.question(
                self, "Delete downtime",
                f"Delete manual unavailability #{mid} — block {row.get('block')}, "
                f"{row.get('date_from')}, {rw.fmt_hours(row.get('downtime_h'))} h?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            av.delete_manual_unavailability(int(mid))
        except rw.MonthLockedError as e:
            self._locked_warning(e)
        self._refresh_inputs()

    def _selected_pending(self):
        eid = self._selected_id(self.pend_table)
        if eid is None:
            QMessageBox.information(self, "Nothing selected", "Pick a waiting phone event first.")
            return None
        return avi.get_queued_event(eid)

    def _review_pending(self):
        q = self._selected_pending()
        if not q: return
        ev = q['event'] or {}
        said = avi.event_summary(ev)
        if q['kind'] == 'pm':
            info = f"From a phone: {said}."
            if q.get('note'):
                info += f"\nWhy it waits: {q['note']}."
            info += "\nCorrect it if needed; saving applies it as the PM record."
            dlg = PMDialog(self, project_id=self._pid, title="Phone PM — apply", info=info,
                           existing={'date_from': ev.get('date_from'), 'date_to': ev.get('date_to'),
                                     'affected_blocks': ev.get('blocks'), 'hours': ev.get('hours'),
                                     'description': ev.get('description')})
            if dlg.exec_() != QDialog.Accepted: return
            d = dlg.get_data()
            self._write_pm(lambda mode: avi.apply_pm_event(
                q['event_id'], d['affected_blocks'], d['date_from'], d['date_to'], d['hours'],
                d['description'], on_duplicate=mode), d['hours'])
        elif q['kind'] == 'counts':
            info = (f"From a phone: {said}.\nConfirm the blocks, dates and hours as they "
                    "really were; saving adds the downtime to the report.")
            dlg = ManualDowntimeDialog(
                self, project_id=self._pid, title="Phone downtime — confirm", info=info,
                existing={'block': ev.get('blocks') or '', 'date_from': ev.get('date_from'),
                          'date_to': ev.get('date_to'), 'downtime_h': ev.get('hours'),
                          'cause': ev.get('description') or 'Reported from the phone'})
            if dlg.exec_() != QDialog.Accepted: return
            d = dlg.get_data()
            try:
                avi.confirm_counts_event(q['event_id'], d['blocks'], d['date_from'], d['date_to'],
                                         d['hours'], d['lc'], d['cause'], d['time_from'], d['time_to'])
            except (avi.InputValidationError, ValueError, RuntimeError) as e:
                QMessageBox.warning(self, "Not confirmed", '\n'.join(getattr(e, 'problems', [str(e)])))
        elif q['kind'] == 'excluded':
            spec = av.parse_block_spec(ev.get('blocks'))
            existing = {'exclusion_type': ev.get('exclusion_type') or 'Grid Outage',
                        'date_from': ev.get('date_from'), 'date_to': ev.get('date_to') or ev.get('date_from'),
                        'time_from': '00:00', 'time_to': '00:00',
                        'affected_blocks': ','.join(map(str, sorted(spec))) if spec else '',
                        'description': ev.get('description') or ''}
            dlg = ExclusionDialog(self, blocks_list=self._blocks_list(), existing=existing)
            dlg.setWindowTitle("Phone exclusion — confirm the real window")
            dlg.layout().insertWidget(0, _info_label(
                f"From a phone: {said}.\nSet the real start and end time and the blocks; "
                "saving adds the exclusion window to the report."))
            while True:
                if dlg.exec_() != QDialog.Accepted: return
                data = dlg.get_data()
                blocks = data['affected_blocks']
                if not blocks:
                    if QMessageBox.question(
                            self, "Whole plant?",
                            f"No blocks selected. Apply the window to all {len(self._blocks_list())} blocks?",
                            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                        continue
                    blocks = 'all'
                try:
                    avi.confirm_excluded_event(q['event_id'], data['exclusion_type'], data['date_from'],
                                               data['time_from'], data['date_to'], data['time_to'],
                                               blocks, data['description'])
                    break
                except (avi.InputValidationError, ValueError, RuntimeError) as e:
                    QMessageBox.warning(self, "Not confirmed", '\n'.join(getattr(e, 'problems', [str(e)])))
            self._refresh_excl()
        self._refresh_inputs()

    def _reject_pending(self):
        q = self._selected_pending()
        if not q: return
        note, ok = QInputDialog.getText(
            self, "Reject phone event",
            f"{avi.event_summary(q['event'] or {})}\n\nIt will not reach the report. Reason (optional):")
        if not ok: return
        try:
            avi.reject_event(q['event_id'], note.strip())
        except ValueError as e:
            QMessageBox.warning(self, "Not rejected", str(e))
        self._refresh_inputs()

    # ── Work reports (auto-pulled corrective maintenance) ─────────────────────
    def _corrective_rows(self):
        """The month's corrective items (report_workflow_service.corrective_rows)."""
        return rw.corrective_rows(self._pid, self._year, self._month)

    def _refresh_work_reports(self):
        if self._pid is None:
            return
        rows = self._corrective_rows()
        self._wr_rows = rows
        self.wr_table.setRowCount(0)
        for i, r in enumerate(rows):
            row = self.wr_table.rowCount(); self.wr_table.insertRow(row)
            fault = f"{r['src']} {r['fault']}".strip() if r.get('fault') else r['src']
            why = cm_skip_reason(r)
            blk = str(r.get('block') or '')
            if not blk and r.get('location'):
                blk = f"? ({r['location']})"
            vals = [str(i), r.get('date', '') or '', blk,
                    fault, r.get('action', '') or '', r.get('sap', '') or '',
                    r.get('status', '') or '', _SKIP_LABELS.get(why, '✓') if why else '✓']
            for c, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                if why in ('no_block', 'conflict'):
                    it.setForeground(QColor('#B4232A'))
                self.wr_table.setItem(row, c, it)
        n = len(rows); nmob = sum(1 for r in rows if r['src'] == '📱')
        held = {}
        for r in rows:
            k = cm_skip_reason(r)
            if k:
                held[k] = held.get(k, 0) + 1
        txt = f"{n} item(s) this month" + (f" · {nmob} from mobile 📱" if nmob else "")
        if held:
            txt += " · kept out of the report: " + ', '.join(
                f"{held[k]} {_SKIP_LABELS[k]}" for k in ('open', 'no_block', 'pm', 'conflict')
                if held.get(k))
        self.wr_count_lbl.setText(txt)
        self._refresh_gap_notice()
        self._refresh_cm_preview()

    def _selected_corrective(self):
        r = self.wr_table.currentRow()
        if r < 0 or self.wr_table.item(r, 0) is None:
            QMessageBox.information(self, "Nothing selected", "Pick a work record first.")
            return None
        i = int(self.wr_table.item(r, 0).text())
        return self._wr_rows[i] if 0 <= i < len(self._wr_rows) else None

    def _edit_corrective(self):
        row = self._selected_corrective()
        if not row: return
        if row['src'] != '📱' or not row.get('id'):
            QMessageBox.information(self, "Desktop work report",
                                    f"Desktop work report #{row.get('id')} is edited on the "
                                    "Work Report page.")
            return
        if row.get('sync_status') == 'conflict':
            QMessageBox.information(self, "Sync conflict",
                                    "This record is in a sync conflict — resolve it first, "
                                    "then edit the version you kept.")
            return
        from ui.worklog_entry_form import EditWorklogEntryDialog
        dlg = EditWorklogEntryDialog(row['id'], parent=self)
        if dlg.exec_() == QDialog.Accepted:
            self._refresh_work_reports()

    def _resolve_corrective(self):
        row = self._selected_corrective()
        if not row: return
        if row.get('sync_status') != 'conflict':
            QMessageBox.information(self, "No conflict", "This record is not in a sync conflict.")
            return
        from ui.worklog_entry_form import resolve_entry_conflict
        if resolve_entry_conflict(self, row['id']):
            self._refresh_work_reports()

    def _refresh_gap_notice(self):
        """Faults in this month's imported SCADA alarms with no work report."""
        self.gap_lbl.setVisible(False)
        if self._pid is None:
            return
        try:
            import services.asset_tree_service as ats
            gaps = ats.get_unreported_faults(self._pid, self._year, self._month,
                                             min_hours=1.0)
            inc = ats.flag_incidents_near_exclusions(
                self._pid, ats.group_faults_into_incidents(gaps))
        except Exception:
            return
        if not inc:
            return
        # One fault across many units is one incident — count those, not
        # alarms, or the number is meaningless.
        near = [g for g in inc if g.get('near_exclusion')]
        real = [g for g in inc if not g.get('near_exclusion')]
        hrs = sum(float(g.get('total_hours') or 0) for g in real)
        worst = ', '.join(
            f"{g['trigger_name'][:38]} ({g['units']}×, {g['total_hours']:g} h)"
            for g in real[:2]) or '—'
        txt = (f"⚠  {len(real)} incident(s) this month — "
               f"{sum(g['units'] for g in real)} alarm(s), {hrs:,.1f} "
               f"equipment-hours — have no work report. Worst: {worst}.")
        if near:
            txt += (f"  A further {len(near)} incident(s) "
                    f"({sum(g['units'] for g in near)} alarms) started just "
                    f"outside a grid-outage / PM window — those are probably "
                    f"the outage itself, not equipment faults; check the "
                    f"window times rather than writing reports.")
        txt += "  Open Equipment → “Needs a report”."
        self.gap_lbl.setText(txt)
        self.gap_lbl.setVisible(True)

    def _wr_as_cm_lines(self):
        """The month's corrective items as report lines — finished work with a
        plant block only (report_workflow_service.cm_lines)."""
        return rw.cm_lines(self._wr_rows)

    def _refresh_cm_preview(self):
        if not hasattr(self, 'cm_preview'):
            return
        lines = self._collect_cm_lines()
        self.cm_preview.setPlainText('\n'.join('•  ' + ln for ln in lines) or
                                     '(nothing — the report prints the automated summary)')
        held = {}
        for r in self._wr_rows:
            k = cm_skip_reason(r)
            if k:
                held[k] = held.get(k, 0) + 1
        self.cm_preview_lbl.setText(
            f"{len(lines)} line(s) print" + (" · not included: " + ', '.join(
                f"{held[k]} {_SKIP_LABELS[k]}" for k in ('open', 'no_block', 'pm', 'conflict')
                if held.get(k)) if held else ""))

    def _collect_cm_lines(self):
        """Corrective-maintenance lines for the report: auto-pulled work reports
        (when included) plus any additional free-text notes."""
        lines = []
        if getattr(self, 'cm_include', None) and self.cm_include.isChecked():
            lines += self._wr_as_cm_lines()
        lines += self._lines(self.cm_text.toPlainText())
        return lines

    # ── Exclusion handlers ───────────────────────────────────────────────
    def _refresh_excl(self):
        self.excl_table.setRowCount(0)
        for e in av.get_exclusions(project_id=self._pid, year=self._year, month=self._month):
            row = self.excl_table.rowCount(); self.excl_table.insertRow(row)
            blk = e.get('affected_blocks') or 'All'
            for c, v in enumerate([str(e['id']), e.get('exclusion_type', ''),
                                   f"{e.get('date_from','')} {e.get('time_from','')}",
                                   f"{e.get('date_to','')} {e.get('time_to','')}", blk,
                                   e.get('description', '')]):
                self.excl_table.setItem(row, c, QTableWidgetItem(str(v)))

    def _add_excl(self):
        dlg = ExclusionDialog(self, blocks_list=self._blocks_list())
        if dlg.exec_() != QDialog.Accepted: return
        try:
            av.add_exclusion(**dlg.get_data(), project_id=self._pid, year=self._year, month=self._month)
        except rw.MonthLockedError as e:
            self._locked_warning(e)
        self._refresh_excl()

    def _edit_excl(self):
        r = self.excl_table.currentRow()
        if r < 0: return
        eid = int(self.excl_table.item(r, 0).text())
        existing = next((x for x in av.get_exclusions(project_id=self._pid, year=self._year, month=self._month) if x['id'] == eid), None)
        if not existing: return
        dlg = ExclusionDialog(self, blocks_list=self._blocks_list(), existing=existing)
        if dlg.exec_() != QDialog.Accepted: return
        try:
            av.update_exclusion(eid, **dlg.get_data(), year=self._year, month=self._month)
        except rw.MonthLockedError as e:
            self._locked_warning(e)
        self._refresh_excl()

    def _del_excl(self):
        r = self.excl_table.currentRow()
        if r < 0: return
        eid = self.excl_table.item(r, 0).text()
        if QMessageBox.question(
                self, "Delete exclusion",
                f"Delete exclusion window #{eid} ({self.excl_table.item(r, 1).text()}, "
                f"{self.excl_table.item(r, 2).text()} → {self.excl_table.item(r, 3).text()})?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            av.delete_exclusion(int(eid))
        except rw.MonthLockedError as e:
            self._locked_warning(e)
        self._refresh_excl()

    # ── Balancing handlers ───────────────────────────────────────────────
    def _refresh_bal(self):
        self.bal_table.setRowCount(0)
        for b in av.get_balancing_periods(project_id=self._pid, year=self._year, month=self._month):
            row = self.bal_table.rowCount(); self.bal_table.insertRow(row)
            blk = b.get('affected_blocks') or 'All'
            for c, v in enumerate([str(b['id']), b.get('date_from', ''), b.get('date_to', ''),
                                   blk, b.get('note', '')]):
                self.bal_table.setItem(row, c, QTableWidgetItem(str(v)))

    def _add_bal(self):
        dlg = BalancingDialog(self, blocks_list=self._blocks_list())
        if dlg.exec_() != QDialog.Accepted: return
        try:
            av.add_balancing_period(**dlg.get_data(), project_id=self._pid, year=self._year, month=self._month)
        except rw.MonthLockedError as e:
            self._locked_warning(e)
        self._refresh_bal()

    def _edit_bal(self):
        r = self.bal_table.currentRow()
        if r < 0: return
        bid = int(self.bal_table.item(r, 0).text())
        existing = next((x for x in av.get_balancing_periods(project_id=self._pid, year=self._year, month=self._month) if x['id'] == bid), None)
        if not existing: return
        dlg = BalancingDialog(self, blocks_list=self._blocks_list(), existing=existing)
        if dlg.exec_() != QDialog.Accepted: return
        try:
            av.update_balancing_period(bid, **dlg.get_data(), year=self._year, month=self._month)
        except rw.MonthLockedError as e:
            self._locked_warning(e)
        self._refresh_bal()

    def _del_bal(self):
        r = self.bal_table.currentRow()
        if r < 0: return
        bid = self.bal_table.item(r, 0).text()
        if QMessageBox.question(self, "Delete balancing period",
                                f"Delete balancing period #{bid}?",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            av.delete_balancing_period(int(bid))
        except rw.MonthLockedError as e:
            self._locked_warning(e)
        self._refresh_bal()

    # ── Generate ─────────────────────────────────────────────────────────
    # PM-as-downtime, the PM bullet lines, capacities and the availability
    # inputs come from report_workflow_service.report_inputs — the same call
    # the real-report test makes, so the two cannot be fed differently.

    def _lines(self, text):
        return [ln.strip() for ln in (text or '').splitlines() if ln.strip()]

    def _report_params(self):
        """The generator's keyword arguments that come from the database and
        this page; the SCADA files come from the month's data set and the
        output file from the version (report_versions_service.generate_version)."""
        cfg = rw.get_project_config(self._pid)
        proj = rw.get_project(self._pid) or {}
        pd = {}
        for label, key in [('Customer', 'customer'), ('O&M Company', 'om_company'),
                           ('OEM', 'oem'), ('Equipment', 'equipment'),
                           ('Project Capacity', 'project_capacity_str')]:
            if cfg.get(key): pd[label] = cfg[key]
        return dict(
            rw.report_inputs(self._pid, self._year, self._month),
            site_name=proj.get('name', ''),
            project_details=pd,
            prepared_by=cfg.get('prepared_by') or None,
            reviewed_by=cfg.get('reviewed_by') or None,
            report_number=self.report_number.text().strip() or None,
            cm_activities=self._collect_cm_lines() or None,
            site_visits=self._lines(self.site_visits.toPlainText()) or None,
            recommendations=self._lines(self.recommendations.toPlainText()) or None,
            planned_next_period=self._lines(self.planned_next.toPlainText()) or None,
            # Saved with the month but never passed on, so the report always
            # said "No safety incidents in the reporting period".
            safety_incidents=safety_incident_rows(self.safety_incidents.toPlainText()) or None,
        )

    def _open_path(self, path):
        if not path:
            return
        if not os.path.exists(path) and not os.path.splitext(path)[1]:
            os.makedirs(path, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _open_month_folder(self):
        if self._pid is not None:
            self._open_path(mds.month_dir(self._pid, self._year, self._month))

    # ── 1 · Data ──────────────────────────────────────────────────────────
    def _add_files(self):
        if self._pid is None: return
        paths, _ = QFileDialog.getOpenFileNames(
            self, f"Add files to {MONTHS_EN[self._month]} {self._year}", "",
            "Excel files (*.xlsx *.xlsm *.XLSX);;All files (*)")
        if paths:
            self._add_paths(paths)

    def _add_folder(self):
        if self._pid is None: return
        folder = QFileDialog.getExistingDirectory(
            self, f"Add a folder to {MONTHS_EN[self._month]} {self._year}")
        if not folder:
            return
        paths = mds.folder_files(folder)
        if not paths:
            QMessageBox.information(self, "No Excel files", f"No .xlsx files directly in\n{folder}")
            return
        self._add_paths(paths)

    def _add_paths(self, paths, answers=None, analyse=True):
        """Add files to the month's data set; ask for the type of the ones
        that were not recognised. Returns the results."""
        QApplication.setOverrideCursor(Qt.WaitCursor)
        err = None
        try:
            res = mds.add_files(self._pid, self._year, self._month, paths)
        except rw.MonthLockedError as e:
            res, err = [], e
        finally:
            QApplication.restoreOverrideCursor()
        if err is not None:
            self._locked_warning(err)
            return []
        ask = [r for r in res if r['status'] in ('unrecognised', 'ambiguous')]
        if ask:
            if answers is None:
                dlg = ChooseTypesDialog(self, ask, self._site_type)
                answers = dlg.answers() if dlg.exec_() == QDialog.Accepted else {
                    r['path']: 'skip' for r in ask}
            again = {p: t for p, t in answers.items() if p in {r['path'] for r in ask}}
            if again:
                res2 = {r['path']: r for r in mds.add_files(
                    self._pid, self._year, self._month, list(again), again)}
                res = [res2.get(r['path'], r) for r in res]
        self._show_last_add(res)
        self._refresh_data()
        if analyse:
            self._analyse()
        return res

    def _show_last_add(self, res):
        labels = {'added': '✓ added', 'replaced': '✓ replaced', 'duplicate': 'already in',
                  'restored': '✓ copy restored',
                  'ignored': 'not needed', 'skipped': 'skipped', 'unrecognised': '✖ not recognised',
                  'ambiguous': '? unsure', 'error': '✖ error'}
        self.last_add_table.setRowCount(0)
        for i, r in enumerate(res):
            bad = r['status'] in ('unrecognised', 'ambiguous', 'error')
            _fill_row(self.last_add_table,
                      [i, r['name'], labels.get(r['status'], r['status']),
                       r.get('label') or '—', ' · '.join(x for x in (r.get('period'), r.get('note')) if x)],
                      [r.get('note') or r['status']] if bad else None)
        self.last_add_group.setVisible(bool(res))

    def _remove_file(self):
        fid = self._selected_id(self.files_table)
        if fid is None: return
        f = mds.get_file(int(fid)) or {}
        if QMessageBox.question(
                self, "Remove from month",
                f"Take {f.get('original_name')} ({f.get('label')}) out of the month's data set?\n"
                "Its stored copy is kept for earlier versions.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            mds.remove_file(int(fid))
        except rw.MonthLockedError as e:
            self._locked_warning(e)
        self._refresh_data()

    def _analyse(self):
        """Read the files that have no coverage yet, off the UI thread — this
        also fills the parse cache, so the report run after it is quick."""
        if self._pid is None or (self._an_worker and self._an_worker.isRunning()):
            return
        if all(f['coverage'].get('analysed_at')
               for f in mds.list_files(self._pid, self._year, self._month)):
            return
        pid, y, m = self._pid, self._year, self._month
        self.analyse_lbl.setText("Reading the files for coverage…")
        self._an_worker = _Worker(lambda cb: mds.analyse_pending(pid, y, m, cb))
        self._an_worker.progress.connect(self.analyse_lbl.setText)
        self._an_worker.done.connect(lambda *_: (self.analyse_lbl.setText(""), self._refresh_data()))
        self._an_worker.failed.connect(lambda msg: self.analyse_lbl.setText("Coverage: " + msg[:200]))
        self._an_worker.start()

    def _refresh_data(self):
        if self._pid is None:
            return
        files = mds.list_files(self._pid, self._year, self._month)
        self.files_table.setRowCount(0)
        for f in files:
            cov = f['coverage']
            data = (f"{cov['first'][:16]} → {cov['last'][:16]}" if cov.get('first')
                    else 'reading…' if not cov.get('analysed_at') else cov.get('error', '—'))
            _fill_row(self.files_table, [f['id'], f['label'], f['original_name'], data,
                                         (f.get('added_at') or '')[:16], f['sha256'][:12] + '…'])
        missing = mds.missing_required(self._pid, self._year, self._month)
        self.data_status.setText(
            f"{len(files)} file(s)" + (" · missing: " + ', '.join(missing) if missing else
                                       " · every file the report needs is here"))

        grid = mds.coverage_grid(self._pid, self._year, self._month)
        import calendar
        import datetime as _dt
        n_days = calendar.monthrange(self._year, self._month)[1]
        due = set(mds.expected_days(self._year, self._month))
        self.cov_table.clear()
        self.cov_table.setRowCount(len(grid)); self.cov_table.setColumnCount(n_days)
        self.cov_table.setHorizontalHeaderLabels([str(d) for d in range(1, n_days + 1)])
        self.cov_table.setVerticalHeaderLabels(
            [g['label'] + ('' if g['file'] else '  (not added)') for g in grid])
        for r, g in enumerate(grid):
            for c in range(n_days):
                iso = _dt.date(self._year, self._month, c + 1).isoformat()
                state = g['days'].get(iso) or ('none' if iso in due else 'future')
                it = QTableWidgetItem('')
                it.setBackground(QColor(_COV_COLORS[state]))
                it.setToolTip(f"{g['label']} · {c + 1:02d}.{self._month:02d} · "
                              + {'full': 'data', 'partial': 'part of the day', 'none': 'no data',
                                 'future': 'not due yet'}[state])
                self.cov_table.setItem(r, c, it)
        ends = mds.month_end(self._pid, self._year, self._month)
        self.month_end_lbl.setText("<b>Month end:</b> " + " · ".join(
            ("✓ " if e['present'] else "— ") + e['label']
            + (f" ({e['first'][8:10]}.{e['first'][5:7]})" if e['present'] and e.get('first') else '')
            for e in ends))
        i = self.tabs.indexOf(self._data_tab)
        self.tabs.setTabText(i, "1 · Data" + (f"  ({len(missing)} missing)" if missing else
                                              f"  ({len(files)} files)" if files else ""))
        self._apply_lock()

    # ── 2 · Check ─────────────────────────────────────────────────────────
    def _run_check(self):
        if self._pid is None: return []
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            qs = rpc.run_precheck(self._pid, self._year, self._month)
        finally:
            QApplication.restoreOverrideCursor()
        self._fill_check(qs)
        return qs

    def _fill_check(self, qs):
        self._questions = qs or []
        self.check_table.setRowCount(0)
        i = self.tabs.indexOf(self._check_tab)
        if qs is None:
            self.check_summary.setText("Not run yet for this month.")
            self.tabs.setTabText(i, "2 · Check")
            return
        for q in qs:
            row = self.check_table.rowCount(); self.check_table.insertRow(row)
            self.check_table.setItem(row, 0, QTableWidgetItem(_SEV_LABEL.get(q['severity'], '')))
            self.check_table.setItem(row, 1, QTableWidgetItem(q['title']))
            it = QTableWidgetItem(q['detail'] + (f"\n{q['explain']}" if q.get('explain') else ''))
            it.setToolTip(it.text())
            self.check_table.setItem(row, 2, it)
            btn = SecondaryButton(q['action'])
            btn.clicked.connect(lambda _=False, q=q: self._goto(q))
            self.check_table.setCellWidget(row, 3, btn)
        self.check_table.resizeRowsToContents()
        n = len(rpc.needs_confirmation(qs))
        self.check_summary.setText(
            "No open questions." if not qs else
            f"{n} question(s) to settle or confirm" + (f", {len(qs) - n} for information" if len(qs) > n else ""))
        self.tabs.setTabText(i, "2 · Check" + (f"  ({n} open)" if n else "  ✓"))

    def _select_by_id(self, table, ref):
        for r in range(table.rowCount()):
            it = table.item(r, 0)
            if it is not None and it.text() == str(ref):
                table.selectRow(r); table.scrollToItem(it)
                return True
        return False

    def _goto(self, q):
        """Open the place a question is fixed — never change anything."""
        target, ref = q['target'], q.get('ref')
        if target.startswith('inputs.'):
            self.tabs.setCurrentWidget(self._inputs_tab)
            table = {'inputs.exclusions': self.excl_table, 'inputs.pm': self.pm_table,
                     'inputs.manual': self.man_table, 'inputs.pending': self.pend_table}[target]
            if ref is not None:
                self._select_by_id(table, ref)
            table.setFocus()
        elif target == 'text.works':
            self.tabs.setCurrentWidget(self._text_tab)
            for i, r in enumerate(self._wr_rows):
                if r.get('id') == ref:
                    self.wr_table.selectRow(i)
                    break
        else:
            self.tabs.setCurrentWidget(self._data_tab)

    # ── 4 · Release ───────────────────────────────────────────────────────
    def _refresh_release(self):
        if self._pid is None:
            return
        vers = rvs.list_versions(self._pid, self._year, self._month)
        self.versions_table.setRowCount(0)

        def f(v, fmt):
            return fmt.format(v) if v is not None else '—'
        for v in vers:
            sm = v['summary']
            status = (f"Sent {v['sent_at'][:10]}" if v.get('sent_at') else 'Draft')
            _fill_row(self.versions_table, [
                v['id'], f"v{v['version']}", (v.get('generated_at') or '')[:16],
                f(sm.get('availability_pct'), '{:.2f} %'), f(sm.get('rte_pct'), '{:.2f} %'),
                f"{f(sm.get('cycles_month'), '{:.1f}')} / {f(sm.get('cycles_in_year'), '{:.1f}')} / "
                f"{f(sm.get('cycles_lifetime'), '{:.1f}')}",
                f(sm.get('avg_soc_pct'), '{:.1f} %'), f(sm.get('avg_soh_pct'), '{:.2f} %'),
                v['changes'] + (f" · {sm['confirmed']} question(s) confirmed" if sm.get('confirmed') else ''),
                status])
        if vers:
            self.versions_table.selectRow(0)
        nxt = rvs.next_version(self._pid, self._year, self._month)
        self.gen_btn.setText(f"⚡  Generate v{nxt}")

        st = rvs.month_status(self._pid, self._year, self._month)
        sent, locked = st['sent'], bool(st['locked_at'])
        if sent:
            self.sent_lbl.setText(
                f"<b>Sent {sent['sent_at'][:16]} · based on v{sent['version']}</b><br>"
                f"{sent['sent_original_name']} · SHA-256 {sent['sent_docx_sha256'][:12]}… · copy stored<br>"
                + ("Month inputs are <b>locked</b>." if locked else
                   "Month is <b>unlocked</b> — inputs can change; lock it again when done."))
        else:
            self.sent_lbl.setText("No version marked as sent.")
        self.mark_sent_btn.setEnabled(bool(vers))
        self.unlock_btn.setEnabled(locked)
        self.relock_btn.setEnabled(bool(sent) and not locked)
        self.month_log_lbl.setText('<br>'.join(
            f"{e['at'][:16]} · {e['action']}" + (f" v{e['version']}" if e.get('version') else '')
            + (f" — {e['reason']}" if e.get('reason') else '') for e in st['log'][-6:]))
        i = self.tabs.indexOf(self._release_tab)
        self.tabs.setTabText(i, "4 · Release" + (
            f"  (sent v{sent['version']}{' · locked' if locked else ''})" if sent
            else f"  (v{vers[0]['version']} draft)" if vers else ""))
        self._apply_lock()

    def _selected_version(self):
        vid = self._selected_id(self.versions_table)
        if vid is None:
            vers = rvs.list_versions(self._pid, self._year, self._month) if self._pid else []
            return vers[0] if vers else None
        return rvs.get_version(int(vid))

    def _open_docx(self):
        v = self._selected_version()
        if v: self._open_path(v['docx_abs'])

    def _open_manifest(self):
        v = self._selected_version()
        if v and v.get('manifest_abs'): self._open_path(v['manifest_abs'])

    def _generate(self):
        if self._pid is None:
            QMessageBox.information(self, "No month", "Open a project + month first."); return
        if self._worker and self._worker.isRunning():
            return
        missing = mds.missing_required(self._pid, self._year, self._month)
        if missing:
            QMessageBox.warning(self, "Files missing",
                                "Add these to the month's data set first:\n• " + '\n• '.join(missing))
            self.tabs.setCurrentWidget(self._data_tab)
            return
        if not rw.month_locked_at(self._pid, self._year, self._month):
            self._save_narrative()
        qs = self._run_check()
        open_qs = rpc.needs_confirmation(qs)
        confirmations, note = [], ''
        if open_qs:
            dlg = ConfirmGenerateDialog(self, open_qs)
            if dlg.exec_() != QDialog.Accepted:
                self.tabs.setCurrentWidget(self._check_tab)
                return
            confirmations = [{k: q.get(k) for k in ('kind', 'severity', 'title', 'detail', 'explain')}
                             for q in open_qs]
            note = dlg.note.text().strip()
        params = self._report_params()
        pid, y, m = self._pid, self._year, self._month
        self.log.clear(); self.progress.setVisible(True); self.gen_btn.setEnabled(False)
        self.tabs.setCurrentWidget(self._release_tab)
        self._worker = _Worker(lambda cb: rvs.generate_version(pid, y, m, params, confirmations, note, cb))
        self._worker.progress.connect(lambda msg: self.log.append(msg))
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_err)
        self._worker.start()

    def _on_done(self, row):
        self.progress.setVisible(False); self.gen_btn.setEnabled(True)
        self._refresh_release()
        if row:
            self.log.append(f"✅ v{row['version']}: {row['docx_abs']}")
            QMessageBox.information(self, "Report ready", f"v{row['version']} saved:\n{row['docx_abs']}")

    def _on_err(self, msg):
        self.progress.setVisible(False); self.gen_btn.setEnabled(True)
        self.log.append(f"❌ {msg}")
        QMessageBox.critical(self, "Error", msg[:2000] + "\n\nEarlier versions are not affected.")

    def _mark_sent(self):
        v = self._selected_version()
        if not v:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, f"The final DOCX that was sent (based on v{v['version']})",
            os.path.dirname(v['docx_abs']), "Word documents (*.docx)")
        if not path:
            return
        if QMessageBox.question(
                self, "Mark as sent",
                f"Mark {os.path.basename(path)} as the report sent for {MONTHS_EN[self._month]} "
                f"{self._year}, based on v{v['version']}?\n\nA copy and its SHA-256 are kept, and "
                "the month's inputs lock.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            rvs.mark_sent(v['id'], path)
        except (ValueError, OSError) as e:
            QMessageBox.warning(self, "Not marked", str(e)); return
        self._refresh_release()

    def _unlock_month(self):
        if self._pid is None: return
        reason, ok = QInputDialog.getMultiLineText(
            self, "Unlock month",
            f"Why does {MONTHS_EN[self._month]} {self._year} need to change after the report "
            "was sent? The reason is recorded.")
        if not ok:
            return
        try:
            rvs.unlock_month(self._pid, self._year, self._month, reason)
        except ValueError as e:
            QMessageBox.warning(self, "Not unlocked", str(e)); return
        self._refresh_release()

    def _relock_month(self):
        if self._pid is None: return
        try:
            rvs.lock_month(self._pid, self._year, self._month)
        except ValueError as e:
            QMessageBox.warning(self, "Not locked", str(e)); return
        self._refresh_release()


def _wrap(layout):
    w = QWidget(); w.setLayout(layout); return w
