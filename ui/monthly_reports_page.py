"""
ui/monthly_reports_page.py
---------------------------
Monthly report workspace. Pick a Project + Month, then fill in that month's
data across tabs — availability inputs (phone events waiting for the desktop,
PM records, manual downtime, exclusions, balancing), corrective works,
narrative — attach the month's SCADA files and generate the report.

Static per-project inputs (customer, capacities, availability basis, …) come
from the Projects page and are pulled automatically at generation time.
"""

import re

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPushButton, QGroupBox,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QTabWidget, QDialog,
    QDialogButtonBox, QMessageBox, QProgressBar, QScrollArea, QCheckBox,
    QTimeEdit, QInputDialog,
)
from PyQt5.QtCore import Qt, QDate, QTime, QDateTime
from PyQt5.QtGui import QColor

from ui.components import PageHeader, PrimaryButton, SecondaryButton
import services.report_workflow_service as rw
import services.availability_service as av
import services.availability_inputs_service as avi
import services.work_log_service as wls
from services.worklog_entry_service import get_worklog_entries
from services.report_workflow_service import MONTHS_EN

# Field Log categories that count as corrective maintenance for the report
CORRECTIVE_CATS = {'fault', 'repair', 'maintenance'}
# Work still outstanding. Section 3.2 reports maintenance *performed*, so these
# are held back — printed as plain bullets they read to the customer exactly
# like completed work ("Antifreeze LOW LEVEL." among August's). Anything else,
# including a blank status on a legacy entry, is treated as done as before.
OPEN_STATUSES = {'open', 'pending', 'in progress', 'in_progress', 'in-progress'}
# A field-log entry about PM is not corrective work: the PM record carries it
# (Availability inputs). "PM activity for 3 hours per checklist" (10.09) used
# to print in 3.2 as a repair.
PM_TEXT = re.compile(r'\bPM\b|preventive', re.IGNORECASE)
_WARN_STYLE = ("background:#FFF8E6;border:1px solid #F0D58C;border-radius:6px;"
               "padding:6px 8px;color:#6B4E00;font-size:11px;")
_ERR_STYLE = "color:#B4232A;font-size:11px;"
_HINT_STYLE = "color:#6B7A8D;font-size:11px;"


def _is_open(row) -> bool:
    return (row.get('status') or '').strip().lower() in OPEN_STATUSES


def cm_skip_reason(row):
    """Why a corrective item stays out of section 3.2, or None to print it.
    'open' — not finished; 'conflict' — the desktop and server copies differ,
    settle it first; 'pm' — it is PM, reported through the PM records;
    'no_block' — no plant block to put on the line (set it with Edit record)."""
    if _is_open(row):
        return 'open'
    if (row.get('sync_status') or '') == 'conflict':
        return 'conflict'
    if PM_TEXT.search(f"{row.get('fault') or ''} {row.get('action') or ''}"):
        return 'pm'
    if row.get('block') in (None, '', 0):
        return 'no_block'
    return None


_SKIP_LABELS = {'open': 'open', 'conflict': 'in sync conflict',
                'pm': 'PM (see PM records)', 'no_block': 'no block'}


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
from ui.block_report_page import (
    _FilePicker, _SavePathPicker, BlockReportWorker,
)


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
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pid = None; self._year = None; self._month = None
        self._worker = None
        self._wr_rows = []          # work reports auto-pulled for the month
        self._inputs = None         # availability_inputs_service.month_inputs
        self._build_ui()
        self._reload_projects()

    # ── UI ────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(PageHeader("Monthly Reports",
                                  "Pick a project and month, fill in the month's data, attach SCADA and generate"))

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
        self.month_lbl = QLabel(""); self.month_lbl.setStyleSheet("color:#6B7A8D;font-style:italic;")
        bar.addWidget(self.month_lbl)
        bar.addStretch()
        root.addLayout(bar)

        self.tabs = QTabWidget(); self.tabs.setEnabled(False)
        root.addWidget(self.tabs, 1)
        self._build_inputs_tab()
        self._build_works_tab()
        self._build_narrative_tab()
        self._build_scada_tab()

    # Availability inputs tab — everything besides SCADA that moves availability
    def _build_inputs_tab(self):
        w = QWidget(); outer = QVBoxLayout(w)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        inner = QWidget(); l = QVBoxLayout(inner)

        # Phone events waiting for the desktop
        self.pend_group = QGroupBox("Waiting for the desktop — phone events not in the report yet")
        pl = QVBoxLayout(self.pend_group)
        pr = QHBoxLayout()
        b = PrimaryButton("✔  Review / confirm…"); b.clicked.connect(self._review_pending); pr.addWidget(b)
        b = SecondaryButton("✖  Reject…"); b.clicked.connect(self._reject_pending); pr.addWidget(b)
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
        b = PrimaryButton("➕  Add"); b.clicked.connect(self._add_man); mr.addWidget(b)
        b = SecondaryButton("✏️  Edit"); b.clicked.connect(self._edit_man); mr.addWidget(b)
        b = SecondaryButton("🗑  Delete"); b.clicked.connect(self._del_man); mr.addWidget(b)
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
        er.addStretch(); el.addLayout(er)
        self.excl_table = _table(["ID", "Type", "From", "To", "Blocks", "Description"], [0, 150, 130, 130, 100])
        self.excl_table.doubleClicked.connect(lambda *_: self._edit_excl())
        el.addWidget(self.excl_table); l.addWidget(eg)

        # Balancing
        bg = QGroupBox("Cycle-Balancing / Rested Blocks"); bl = QVBoxLayout(bg)
        brow = QHBoxLayout()
        for txt, fn in [("➕  Add", self._add_bal), ("✏️  Edit", self._edit_bal), ("🗑  Delete", self._del_bal)]:
            b = (PrimaryButton if txt.startswith("➕") else SecondaryButton)(txt); b.clicked.connect(fn); brow.addWidget(b)
        brow.addStretch(); bl.addLayout(brow)
        self.bal_table = _table(["ID", "From", "To", "Blocks", "Note"], [0, 110, 110, 150])
        self.bal_table.doubleClicked.connect(lambda *_: self._edit_bal())
        bl.addWidget(self.bal_table); l.addWidget(bg)

        l.addStretch(); scroll.setWidget(inner); outer.addWidget(scroll)
        self._inputs_tab = w
        self.tabs.addTab(w, "Availability inputs")

    # Corrective works tab
    def _build_works_tab(self):
        w = QWidget(); outer = QVBoxLayout(w)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        inner = QWidget(); l = QVBoxLayout(inner)

        # Corrective Maintenance auto-pulled from the month's Work Reports
        wrg = QGroupBox("Corrective Maintenance — auto-pulled from Work Reports")
        wrl = QVBoxLayout(wrg)
        hdr = QHBoxLayout()
        self.cm_include = QCheckBox("Include these work reports in the monthly report")
        self.cm_include.setChecked(True)
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
        cml.addWidget(self.cm_text)
        save = SecondaryButton("💾  Save notes"); save.clicked.connect(self._save_narrative)
        r = QHBoxLayout(); r.addStretch(); r.addWidget(save); cml.addLayout(r)
        l.addWidget(cmg)

        l.addStretch(); scroll.setWidget(inner); outer.addWidget(scroll)
        self.tabs.addTab(w, "Corrective works")

    # Narrative tab
    def _build_narrative_tab(self):
        w = QWidget(); f = QFormLayout(w)
        self.report_number = QLineEdit(); f.addRow("Report No.:", self.report_number)
        self.site_visits = QTextEdit(); self.site_visits.setMaximumHeight(80); f.addRow("Site visits:", self.site_visits)
        self.recommendations = QTextEdit(); self.recommendations.setMaximumHeight(80); f.addRow("Recommendations:", self.recommendations)
        self.planned_next = QTextEdit(); self.planned_next.setMaximumHeight(80); f.addRow("Planned next period:", self.planned_next)
        self.safety_incidents = QTextEdit(); self.safety_incidents.setMaximumHeight(80)
        self.safety_incidents.setPlaceholderText(
            "One incident per line — optionally: incident | equipment loss | weight | countermeasure")
        f.addRow("Safety incidents:", self.safety_incidents)
        hint = QLabel("Printed in section 6 of the report. Leave empty (or “None”) and the "
                      "report says there were no safety incidents.")
        hint.setStyleSheet(_HINT_STYLE); hint.setWordWrap(True)
        f.addRow("", hint)
        save = PrimaryButton("💾  Save narrative"); save.clicked.connect(self._save_narrative)
        r = QHBoxLayout(); r.addStretch(); r.addWidget(save); f.addRow("", _wrap(r))
        self.tabs.addTab(w, "Narrative")

    # SCADA + generate tab
    def _build_scada_tab(self):
        w = QWidget(); outer = QVBoxLayout(w)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        inner = QWidget(); l = QVBoxLayout(inner)

        self.tk_group = QGroupBox("Tashkent SCADA files"); tf = QFormLayout(self.tk_group)
        self.tk = {}
        for key, lbl in [('working_status', 'LC Working Status'), ('pcs_cd', 'PCS Charge/Discharge'),
                         ('soc', 'SOC'), ('soh_snapshot', 'SOH snapshot'),
                         ('lc_charge', 'LC Daily Charge'), ('lc_discharge', 'LC Daily Discharge'),
                         ('hv_meter', 'HV Meter daily'), ('alarms', 'Alarm Report'),
                         ('cycles_first_day', 'Cycles first day (opt)'), ('cycles_last_day', 'Cycles last day (opt)'),
                         ('lc_total_charge', 'LC total charge (opt)'), ('lc_total_discharge', 'LC total discharge (opt)'),
                         ('pcs_fault', 'PCS fault status (opt)')]:
            self.tk[key] = _FilePicker(); tf.addRow(lbl + ":", self.tk[key])
        l.addWidget(self.tk_group)

        self.bk_group = QGroupBox("Bukhara SCADA files"); bf = QFormLayout(self.bk_group)
        self.bk = {}
        for key, lbl in [('site_kpi', 'Main KPI 5-min'), ('overall_lc', 'LC Data Total'),
                         ('battery_unit', 'Battery Unit data'), ('meter_daily', 'Main Meter daily'),
                         ('alarms', 'Alarm Report'), ('availability', 'System availability (opt)')]:
            self.bk[key] = _FilePicker(); bf.addRow(lbl + ":", self.bk[key])
        l.addWidget(self.bk_group)

        # Word only — the report is edited before it goes out, and a PDF is
        # printed from Word at the end.
        og = QGroupBox("Output"); of = QFormLayout(og)
        self.out_path = _SavePathPicker("Where to save the report (.docx)")
        of.addRow("Save to *:", self.out_path)
        l.addWidget(og)

        r = QHBoxLayout(); r.addStretch()
        self.gen_btn = PrimaryButton("⚡  Generate Report"); self.gen_btn.setMinimumWidth(200)
        self.gen_btn.clicked.connect(self._generate); r.addWidget(self.gen_btn)
        l.addLayout(r)
        self.progress = QProgressBar(); self.progress.setRange(0, 0); self.progress.setVisible(False)
        self.progress.setFixedHeight(6); l.addWidget(self.progress)
        self.log = QTextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(150)
        self.log.setStyleSheet("QTextEdit{background:#1A2B45;color:#8FA3BE;font-family:Consolas,monospace;font-size:11px;border-radius:4px;padding:6px;}")
        l.addWidget(self.log)

        l.addStretch(); scroll.setWidget(inner); outer.addWidget(scroll)
        self.tabs.addTab(w, "SCADA & Generate")

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
        self.tk_group.setVisible(self._site_type == 'tashkent')
        self.bk_group.setVisible(self._site_type == 'bukhara')
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

    def _save_narrative(self):
        if self._pid is None: return
        rw.save_report_month(self._pid, self._year, self._month,
            report_number=self.report_number.text().strip(),
            cm_activities=self.cm_text.toPlainText().strip(),
            site_visits=self.site_visits.toPlainText().strip(),
            recommendations=self.recommendations.toPlainText().strip(),
            planned_next=self.planned_next.toPlainText().strip(),
            safety_incidents=self.safety_incidents.toPlainText().strip())
        self.log.append("Saved month narrative.") if hasattr(self, 'log') else None

    # ── Availability inputs: PM, manual downtime, phone events ───────────
    def _refresh_inputs(self):
        if self._pid is None:
            return
        data = avi.month_inputs(self._pid, self._year, self._month)
        self._inputs = data

        self.pend_table.setRowCount(0)
        kinds = {'pm': '🧰 PM', 'counts': '⛔ Downtime — counts', 'excluded': '➖ Excluded'}
        reasons = {'confirm': 'needs times and blocks confirmed',
                   'duplicate': 'a PM record already exists', 'invalid': 'cannot be applied as sent'}
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
        self.tabs.setTabText(i, "Availability inputs" + (f"  ({n} waiting)" if n else ""))

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
        rw.delete_pm_activity(int(pm_id)); self._refresh_inputs()

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
        av.delete_manual_unavailability(int(mid)); self._refresh_inputs()

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
        """Unified corrective-maintenance items for the month, from BOTH the
        desktop Work Reports and mobile Field Log entries (which sync down)."""
        import calendar
        last = calendar.monthrange(self._year, self._month)[1]
        start = f"{self._year:04d}-{self._month:02d}-01"
        end = f"{self._year:04d}-{self._month:02d}-{last:02d}"
        # Both sources number blocks per zone (Zone 8 / Block 2); the report
        # and the customer speak plant-wide (Block 57). Translate once here,
        # through the block map only — a pair the map does not know gets no
        # block rather than a guessed one.
        from services.project_service import zone_block_to_plant

        def _plant(zone, block):
            return zone_block_to_plant(self._pid, zone, block)

        rows = []
        for r in wls.get_work_logs_for_month(self._pid, self._year, self._month):
            rows.append({'src': '🖥', 'id': r.get('id'), 'date': r.get('date', '') or '',
                         'block': _plant(r.get('zone'), r.get('block')),
                         'zone': r.get('zone'), 'local_block': r.get('block'),
                         'cont': r.get('container_num'),
                         'fault': r.get('fault_description', '') or '',
                         'action': r.get('work_performed', '') or '',
                         'sap': r.get('sap_ticket', '') or '',
                         'status': r.get('status', '') or '', 'sync_status': ''})
        try:
            entries = get_worklog_entries(project_id=self._pid, date_from=start, date_to=end)
        except Exception:
            entries = []
        for e in entries:
            if (e.get('category') or '') not in CORRECTIVE_CATS:
                continue
            rows.append({'src': '📱', 'id': e.get('id'), 'date': e.get('log_date', '') or '',
                         'block': _plant(e.get('zone_number'),
                                         e.get('block_number')),
                         'zone': e.get('zone_number'),
                         'local_block': e.get('block_number'),
                         'cont': e.get('container_index'),
                         'fault': e.get('fault_name', '') or '',
                         'action': e.get('description', '') or '',
                         'sap': e.get('sap_ticket', '') or '',
                         'status': e.get('status', '') or '',
                         'sync_status': e.get('sync_status', '') or '',
                         'location': e.get('site_location', '') or ''})
        rows.sort(key=lambda r: (r['date'] or ''))
        return rows

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
        """Format the month's corrective items (desktop + mobile) as report lines.

        Only finished corrective work with a plant block (cm_skip_reason):
        open items, PM entries, entries in a sync conflict and entries without
        a block stay on the page, counted, but do not go to the customer. The
        line names the plant block only — the container index is not an LC
        number and used to print as "Block 33/C3".
        """
        out = []
        for r in self._wr_rows:
            if cm_skip_reason(r):
                continue
            blk = r.get('block')
            fault = (r.get('fault') or '').strip()
            action = (r.get('action') or '').strip()
            sap = (r.get('sap') or '').strip()
            loc = f"Block {blk}"
            line = f"{loc}: {fault}" if fault else loc
            if action:
                line += f" — {action}"
            if sap:
                line += f" [{sap}]"
            out.append(line)
        return out

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
        av.add_exclusion(**dlg.get_data(), project_id=self._pid, year=self._year, month=self._month)
        self._refresh_excl()

    def _edit_excl(self):
        r = self.excl_table.currentRow()
        if r < 0: return
        eid = int(self.excl_table.item(r, 0).text())
        existing = next((x for x in av.get_exclusions(project_id=self._pid, year=self._year, month=self._month) if x['id'] == eid), None)
        if not existing: return
        dlg = ExclusionDialog(self, blocks_list=self._blocks_list(), existing=existing)
        if dlg.exec_() != QDialog.Accepted: return
        av.update_exclusion(eid, **dlg.get_data(), year=self._year, month=self._month)
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
        av.delete_exclusion(int(eid)); self._refresh_excl()

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
        av.add_balancing_period(**dlg.get_data(), project_id=self._pid, year=self._year, month=self._month)
        self._refresh_bal()

    def _edit_bal(self):
        r = self.bal_table.currentRow()
        if r < 0: return
        bid = int(self.bal_table.item(r, 0).text())
        existing = next((x for x in av.get_balancing_periods(project_id=self._pid, year=self._year, month=self._month) if x['id'] == bid), None)
        if not existing: return
        dlg = BalancingDialog(self, blocks_list=self._blocks_list(), existing=existing)
        if dlg.exec_() != QDialog.Accepted: return
        av.update_balancing_period(bid, **dlg.get_data(), year=self._year, month=self._month)
        self._refresh_bal()

    def _del_bal(self):
        r = self.bal_table.currentRow()
        if r < 0: return
        bid = self.bal_table.item(r, 0).text()
        if QMessageBox.question(self, "Delete balancing period",
                                f"Delete balancing period #{bid}?",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        av.delete_balancing_period(int(bid)); self._refresh_bal()

    # ── Generate ─────────────────────────────────────────────────────────
    # PM-as-downtime, the PM bullet lines, capacities and the availability
    # inputs come from report_workflow_service.report_inputs — the same call
    # the real-report test makes, so the two cannot be fed differently.

    def _lines(self, text):
        return [ln.strip() for ln in (text or '').splitlines() if ln.strip()]

    def _report_params(self):
        """The generator's keyword arguments that come from the database and
        this page (SCADA file paths are added by _generate)."""
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
            output_path=self.out_path.path(),
            output_format='docx',
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

    def _generate(self):
        if self._pid is None:
            QMessageBox.information(self, "No month", "Open a project + month first."); return
        if not self.out_path.path():
            QMessageBox.warning(self, "Missing", "Choose where to save the report."); return
        self._save_narrative()
        common = self._report_params()
        if self._site_type == 'bukhara':
            miss = [k for k in ('site_kpi', 'overall_lc', 'battery_unit', 'meter_daily', 'alarms') if not self.bk[k].path()]
            if miss:
                QMessageBox.warning(self, "Missing SCADA", "Attach: " + ", ".join(miss)); return
            params = dict(common, **{k: v.path() for k, v in self.bk.items() if v.path()})
        else:
            miss = [k for k in ('working_status', 'pcs_cd', 'soc', 'soh_snapshot', 'lc_charge', 'lc_discharge', 'hv_meter', 'alarms') if not self.tk[k].path()]
            if miss:
                QMessageBox.warning(self, "Missing SCADA", "Attach: " + ", ".join(miss)); return
            params = dict(common, **{k: v.path() for k, v in self.tk.items() if v.path()})
            # Keep the month's alarms as equipment history (Equipment page)
            params['project_id'] = self._pid

        n_wait = len(avi.pending_events(self._pid, self._year, self._month))
        if n_wait and QMessageBox.question(
                self, "Phone events waiting",
                f"{n_wait} phone event(s) for this month are still waiting on the "
                "Availability inputs tab and are NOT in the report. Generate anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return

        self.log.clear(); self.progress.setVisible(True); self.gen_btn.setEnabled(False)
        self._worker = BlockReportWorker(self._site_type, params)
        self._worker.progress.connect(lambda m: self.log.append(m))
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_err)
        self._worker.start()

    def _on_done(self, out):
        self.progress.setVisible(False); self.gen_btn.setEnabled(True)
        self.log.append(f"✅ Done: {out}")
        QMessageBox.information(self, "Report ready", f"Saved:\n{out}")

    def _on_err(self, msg):
        self.progress.setVisible(False); self.gen_btn.setEnabled(True)
        self.log.append(f"❌ {msg}")
        QMessageBox.critical(self, "Error", msg)


def _wrap(layout):
    w = QWidget(); w.setLayout(layout); return w
