"""ui/work_page.py — one journal of work, list on the left, record on the right.

Replaces four screens that all wrote "a job someone did": Work Reports, Field
Log, Field Records and the Daily Log. One record shape, one list, one card.

  * Tabs — All · Open · Needs a record (SCADA) · No block · Conflicts.
  * Chips — type, status, block, period, "has PTW", source — filtered by
    work_journal_service, the same function the Today counters use, so a count
    and the list it opens can never disagree.
  * The card says out loud which text the customer will read ("In report") and
    which stays ours ("Internal") — the boundary the monthly report depends on.
  * `work_logs` rows are shown, marked "Old format", and cannot be edited: they
    still count in the report, but new work is written as a record the phones
    understand.
"""
import datetime

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QLineEdit, QTextEdit, QCheckBox, QTableWidget, QTableWidgetItem, QSplitter,
    QAbstractItemView, QScrollArea, QFrame, QMessageBox, QHeaderView,
    QDoubleSpinBox, QSizePolicy, QGridLayout,
)

import services.work_journal_service as wj
from ui.components import PageHeader, PrimaryButton, SecondaryButton

TABS = [('all', 'All'), ('open', 'Open'), ('scada', 'Needs a record (SCADA)'),
        ('noblock', 'No block'), ('conflict', 'Conflicts')]

STATUSES = ['Needs visit', 'Open', 'In progress', 'Done']
IMPACTS = [('none', 'None'), ('counts', 'Counts'), ('excluded', 'Excluded')]

_CHIP = ("QLabel{border-radius:8px;padding:1px 8px;font-size:11px;}")


def _tag(text, fg, bg):
    lbl = QLabel(text)
    lbl.setStyleSheet(_CHIP + f"QLabel{{color:{fg};background:{bg};}}")
    lbl.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    return lbl


def in_report():
    return _tag("In report", "#1565C0", "#E3F2FD")


def internal():
    return _tag("Internal", "#6B4E00", "#FFF4D6")


class WorkPage(QWidget):
    record_saved = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pid = None
        self._name = ''
        self._year = None
        self._month = None
        self._rows = []
        self._shown = []
        self._alarms = []
        self._tab = 'all'
        self._key = None
        self._build_ui()

    # ── UI ───────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.header = PageHeader("Work", "Every record of work — phone and desktop in one list")
        self.new_btn = PrimaryButton("New record")
        self.new_btn.clicked.connect(self._new_record)
        self.header.add_action(self.new_btn)
        root.addWidget(self.header)

        # tabs
        tabrow = QHBoxLayout()
        tabrow.setContentsMargins(16, 8, 16, 0)
        tabrow.setSpacing(6)
        self._tab_buttons = {}
        for key, label in TABS:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                "QPushButton{border:1px solid #D6DBE3;border-radius:14px;"
                "padding:4px 12px;background:#FFFFFF;color:#1A2B45;}"
                "QPushButton:checked{background:#0071E3;color:#FFFFFF;"
                "border-color:#0071E3;font-weight:bold;}")
            b.clicked.connect(lambda _=False, k=key: self._set_tab(k))
            tabrow.addWidget(b)
            self._tab_buttons[key] = b
        tabrow.addStretch()
        root.addLayout(tabrow)

        # chips
        chips = QHBoxLayout()
        chips.setContentsMargins(16, 8, 16, 8)
        chips.setSpacing(8)
        self.kind_cb = QComboBox()
        self.kind_cb.addItem("Any type", None)
        for k in (wj.KIND_FAULT, wj.KIND_PM, wj.KIND_OTHER):
            self.kind_cb.addItem(k, k)
        self.status_cb = QComboBox()
        self.status_cb.addItem("Any status", None)
        for s in STATUSES:
            self.status_cb.addItem(s, s)
        self.block_edit = QLineEdit()
        self.block_edit.setPlaceholderText("Block")
        self.block_edit.setFixedWidth(70)
        self.period_cb = QComboBox()
        self.period_cb.addItem("This month", 'month')
        self.period_cb.addItem("All time", 'all')
        self.source_cb = QComboBox()
        self.source_cb.addItem("Any source", None)
        self.source_cb.addItem("Phone", 'phone')
        self.source_cb.addItem("Desktop", 'desktop')
        self.source_cb.addItem("Old format", 'old')
        self.ptw_chk = QCheckBox("Has PTW")
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Block, fault, PTW…")
        self.search_edit.setMinimumWidth(180)
        for w in (self.kind_cb, self.status_cb, self.block_edit, self.period_cb,
                  self.source_cb, self.ptw_chk):
            chips.addWidget(w)
        chips.addStretch()
        chips.addWidget(self.search_edit)
        root.addLayout(chips)
        for cb in (self.kind_cb, self.status_cb, self.period_cb, self.source_cb):
            cb.currentIndexChanged.connect(self._apply)
        self.ptw_chk.stateChanged.connect(self._apply)
        self.block_edit.textChanged.connect(self._apply)
        self.search_edit.textChanged.connect(self._apply)

        # list + card
        split = QSplitter(Qt.Horizontal)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Date", "Node", "Work", "Status", "PTW No.", "Source"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(2, QHeaderView.Stretch)      # the work, not the note
        for col, w in ((0, 96), (1, 190), (3, 96), (4, 110), (5, 92)):
            self.table.setColumnWidth(col, w)
        self.table.itemSelectionChanged.connect(self._on_select)
        split.addWidget(self.table)

        self.card_area = QScrollArea()
        self.card_area.setWidgetResizable(True)
        self.card_area.setFrameShape(QFrame.NoFrame)
        self.card = QWidget()
        self.card_l = QVBoxLayout(self.card)
        self.card_l.setContentsMargins(16, 14, 16, 16)
        self.card_l.setSpacing(8)
        self.card_area.setWidget(self.card)
        split.addWidget(self.card_area)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([760, 520])
        root.addWidget(split, 1)

        self.foot = QLabel("")
        self.foot.setStyleSheet("color:#6B7A8D;padding:4px 16px 8px;")
        root.addWidget(self.foot)
        self._set_tab('all', reload=False)

    # ── shell hooks ──────────────────────────────────────────────────────
    def set_current_project(self, pid, name=None):
        self._pid = pid
        self._name = name or ''
        self.refresh()

    def set_month(self, year, month):
        self._year, self._month = year, month
        self.refresh()

    def apply_filter(self, flt: dict):
        """Opened from a Today counter: show exactly those rows — so every
        chip not named in the filter is cleared first, or a leftover chip
        would quietly shrink the list below the count that opened it."""
        widgets = (self.kind_cb, self.status_cb, self.period_cb,
                   self.source_cb, self.ptw_chk, self.block_edit, self.search_edit)
        for w in widgets:
            w.blockSignals(True)
        try:
            self.kind_cb.setCurrentIndex(max(0, self.kind_cb.findData(flt.get('kind'))))
            self.status_cb.setCurrentIndex(
                max(0, self.status_cb.findData(flt.get('status'))))
            self.source_cb.setCurrentIndex(0)
            self.ptw_chk.setChecked(False)
            self.block_edit.setText(str(flt.get('block') or ''))
            self.search_edit.clear()
            self.period_cb.setCurrentIndex(1 if flt.get('period', 'all') == 'all' else 0)
        finally:
            for w in widgets:
                w.blockSignals(False)
        self._set_tab(flt.get('tab') or 'all')

    # ── data ─────────────────────────────────────────────────────────────
    def _set_tab(self, key, reload=True):
        self._tab = key
        for k, b in self._tab_buttons.items():
            b.setChecked(k == key)
        if reload:
            self.refresh()

    def refresh(self):
        if self._pid is None:
            self._rows = []
            self._fill([])
            return
        d1 = d2 = None
        if self.period_cb.currentData() == 'month' and self._year:
            d1, d2 = wj.month_range(self._year, self._month)
        self._rows = wj.records(self._pid, date_from=d1, date_to=d2)
        self._update_tab_counts()
        self._apply()

    def _update_tab_counts(self):
        counts = {'all': len(self._rows),
                  'open': len(wj.filter_rows(self._rows, 'open')),
                  'noblock': len(wj.filter_rows(self._rows, 'noblock')),
                  'conflict': len(wj.filter_rows(self._rows, 'conflict'))}
        if self._pid is not None:
            self._alarms = wj.needs_record(self._pid, self._year, self._month)
        counts['scada'] = len(self._alarms)
        for key, label in TABS:
            n = counts.get(key, 0)
            self._tab_buttons[key].setText(f"{label} · {n}" if n else label)

    def _apply(self, *_):
        if self._tab == 'scada':
            self._fill_alarms()
            return
        block = self.block_edit.text().strip()
        rows = wj.filter_rows(
            self._rows, tab=self._tab,
            kind=self.kind_cb.currentData(),
            status=self.status_cb.currentData(),
            block=block if block.isdigit() else None,
            ptw_only=self.ptw_chk.isChecked(),
            source=self.source_cb.currentData(),
            text=self.search_edit.text().strip() or None)
        self._fill(rows)

    def _fill(self, rows):
        self._shown = rows
        self.table.setHorizontalHeaderLabels(
            ["Date", "Node", "Work", "Status", "PTW No.", "Source"])
        self.table.setRowCount(0)
        for r in rows:
            i = self.table.rowCount()
            self.table.insertRow(i)
            work = r['title'] or r['work_done'] or '(no text)'
            if r['kind'] == wj.KIND_PM:
                work = 'PM · ' + work
            src = {'phone': 'Phone', 'desktop': 'Desktop', 'old': 'Old format'}.get(
                r['source'], r['source'])
            if r['sync'] == 'conflict':
                src = 'Conflict'
            cells = [r['date'], r['node'], work, r['status'], r['ptw'], src]
            for c, v in enumerate(cells):
                it = QTableWidgetItem(str(v or ''))
                if r['sync'] == 'conflict':
                    it.setForeground(Qt.red)
                elif r['old_format']:
                    it.setForeground(Qt.gray)
                self.table.setItem(i, c, it)
        if rows:
            self.foot.setText(
                f"{len(rows)} record(s) · phone and desktop in one list"
                + (f" · {self._month:02d}.{self._year}" if self._year
                   and self.period_cb.currentData() == 'month' else ""))
        else:
            self.foot.setText("No records for this filter · try “All time” "
                              "or another tab")
        self._show_card(None)

    def _fill_alarms(self):
        """Needs a record (SCADA): alarms nobody has written up yet."""
        self._shown = []
        self.table.setRowCount(0)
        self.table.setHorizontalHeaderLabels(
            ["Start", "Node", "Fault", "Hours", "Class", "Source"])
        for a in self._alarms:
            i = self.table.rowCount()
            self.table.insertRow(i)
            blk = a.get('block')
            node = f"Block {blk}" if blk else (a.get('asset_code') or '')
            if a.get('lc'):
                node += f" · LC{a['lc']}"
            for c, v in enumerate([str(a.get('activated') or '')[:16], node,
                                   a.get('trigger_name') or '',
                                   f"{a.get('hours') or 0:g}",
                                   a.get('cls_reason') or '', 'SCADA']):
                self.table.setItem(i, c, QTableWidgetItem(str(v)))
        self.foot.setText(f"{len(self._alarms)} SCADA fault(s) with no work "
                          "record · select one to write it up")
        self._show_card(None)

    def _on_select(self):
        r = self.table.currentRow()
        if r < 0:
            return
        if self._tab == 'scada':
            if r < len(self._alarms):
                self._show_alarm_card(self._alarms[r])
            return
        if r < len(self._shown):
            self._key = self._shown[r]['key']
            self._show_card(self._shown[r])

    # ── the card ─────────────────────────────────────────────────────────
    def _clear_card(self):
        while self.card_l.count():
            it = self.card_l.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
            elif it.layout() is not None:
                lay = it.layout()
                while lay.count():
                    sub = lay.takeAt(0)
                    if sub.widget() is not None:
                        sub.widget().setParent(None)

    def _label(self, text, tag=None):
        row = QHBoxLayout()
        row.setSpacing(6)
        lbl = QLabel(f"<b>{text}</b>")
        row.addWidget(lbl)
        if tag is not None:
            row.addWidget(tag)
        row.addStretch()
        self.card_l.addLayout(row)

    def _banner(self, text, kind='warn'):
        col = {'warn': ('#FF9500', '#FFF6E6'), 'crit': ('#FF3B30', '#FFECEB'),
               'info': ('#0071E3', '#EAF3FF')}[kind]
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color:{col[0]};background:{col[1]};border:1px solid {col[0]};"
                          "border-radius:4px;padding:6px 8px;")
        self.card_l.addWidget(lbl)

    def _show_card(self, row):
        self._clear_card()
        self._fields = {}
        if row is None:
            hint = QLabel("Select a record on the left, or press "
                          "<b>New record</b>.")
            hint.setWordWrap(True)
            hint.setStyleSheet("color:#6B7A8D;")
            self.card_l.addWidget(hint)
            self.card_l.addStretch()
            return

        head = QLabel(f"<span style='font-size:16px;font-weight:bold'>{row['node']}</span>"
                      f"   <span style='color:#6B7A8D'>{row['kind']} · {row['date']}</span>")
        head.setWordWrap(True)
        self.card_l.addWidget(head)

        if row['old_format']:
            self._banner("Old format — this is a Work Report row. It still counts "
                         "in the monthly report; it is read-only here. Open it on "
                         "Work Reports in the archive to change it.", 'info')
        if row['sync'] == 'conflict':
            self._banner("Two versions of this record: it was edited on a phone and "
                         "on the desktop. Nothing is overwritten until you choose.",
                         'crit')
        suggestion = None
        if not row['block']:
            self._banner("No plant block — this record cannot go into the customer's "
                         "section 3.2. Set the block below.", 'warn')
            if row.get('location') and self._pid is not None:
                suggestion = wj.suggest_block(self._pid, row['location'])

        ro = not row['editable']
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        r = 0

        def add(label, widget, tag=None):
            nonlocal r
            cell = QHBoxLayout()
            cell.setSpacing(5)
            cell.addWidget(QLabel(label))
            if tag is not None:
                cell.addWidget(tag)
            cell.addStretch()
            holder = QWidget()
            holder.setLayout(cell)
            grid.addWidget(holder, r, 0)
            grid.addWidget(widget, r, 1)
            r += 1

        self._fields['status'] = QComboBox()
        self._fields['status'].addItems(STATUSES)
        self._fields['status'].setCurrentText(row['status'])
        add("Status", self._fields['status'])

        self._fields['block'] = QLineEdit(str(row['block'] or ''))
        self._fields['block'].setPlaceholderText("plant block, e.g. 57")
        blk_row = QHBoxLayout()
        blk_row.addWidget(self._fields['block'])
        zl = QLabel(row['zone_label'])
        zl.setStyleSheet("color:#6B7A8D;")
        blk_row.addWidget(zl)
        self._fields['lc'] = QComboBox()
        self._fields['lc'].addItems(['', 'LC1', 'LC2', 'Whole block'])
        self._fields['lc'].setCurrentText(row['lc'] or '')
        blk_row.addWidget(self._fields['lc'])
        self._fields['device'] = QLineEdit(row['device'] or '')
        self._fields['device'].setPlaceholderText("PCS 2 / BESS 3 …")
        blk_row.addWidget(self._fields['device'])
        holder = QWidget()
        holder.setLayout(blk_row)
        add("Node", holder)

        if row.get('location') and not row['block']:
            loc_row = QHBoxLayout()
            loc = QLabel(f"“{row['location']}”")
            loc.setStyleSheet("color:#6B7A8D;")
            loc_row.addWidget(loc)
            if suggestion:
                zl_s = wj.zone_label(self._pid, suggestion['block'])
                use = SecondaryButton(
                    f"Use Block {suggestion['block']}"
                    + (f" · {zl_s}" if zl_s else "")
                    + (f" · {suggestion['lc']}" if suggestion['lc'] else ""))
                use.setToolTip(f"Read from the phone's text: {suggestion['why']}")
                use.clicked.connect(lambda _=False, s=suggestion: self._use_suggestion(s))
                loc_row.addWidget(use)
                self._fields['_suggest'] = use
            loc_row.addStretch()
            holder = QWidget()
            holder.setLayout(loc_row)
            add("Phone location", holder)

        self._fields['title'] = QLineEdit(row['title'])
        add("Fault", self._fields['title'], in_report())

        self._fields['work_done'] = QTextEdit(row['work_done'])
        self._fields['work_done'].setFixedHeight(56)
        add("What was done", self._fields['work_done'], in_report())

        self._fields['internal_note'] = QTextEdit(row['internal_note'])
        self._fields['internal_note'].setFixedHeight(48)
        add("Diagnosis / note", self._fields['internal_note'], internal())

        ptw_row = QHBoxLayout()
        self._fields['ptw'] = QLineEdit(row['ptw'])
        self._fields['ptw'].setPlaceholderText("optional")
        ptw_row.addWidget(self._fields['ptw'])
        ptw_row.addWidget(QLabel("SAP"))
        self._fields['sap'] = QLineEdit(row['sap'])
        self._fields['sap'].setPlaceholderText("optional")
        ptw_row.addWidget(self._fields['sap'])
        holder = QWidget()
        holder.setLayout(ptw_row)
        add("PTW No.", holder)

        hrow = QHBoxLayout()
        self._fields['time_from'] = QLineEdit(row['time_from'])
        self._fields['time_from'].setPlaceholderText("08:05")
        self._fields['time_to'] = QLineEdit(row['time_to'])
        self._fields['time_to'].setPlaceholderText("11:20")
        self._fields['hours'] = QDoubleSpinBox()
        self._fields['hours'].setRange(0, 24)
        self._fields['hours'].setDecimals(2)
        self._fields['hours'].setValue(float(row['hours'] or 0))
        for w in (self._fields['time_from'], self._fields['time_to'],
                  self._fields['hours']):
            hrow.addWidget(w)
        holder = QWidget()
        holder.setLayout(hrow)
        add("Start · end · hours", holder)

        self._fields['impact'] = QComboBox()
        for code, label in IMPACTS:
            self._fields['impact'].addItem(label, code)
        i = self._fields['impact'].findData(row['impact'] or 'none')
        self._fields['impact'].setCurrentIndex(max(0, i))
        add("Availability", self._fields['impact'])

        wrap = QWidget()
        wrap.setLayout(grid)
        self.card_l.addWidget(wrap)

        if ro:
            for w in self._fields.values():
                w.setEnabled(False)

        # buttons
        btns = QHBoxLayout()
        save = PrimaryButton("Save")
        save.clicked.connect(self._save_card)
        save.setEnabled(not ro)
        btns.addWidget(save)
        rep = SecondaryButton("Repeat on another node")
        rep.clicked.connect(self._repeat)
        rep.setEnabled(not ro)
        btns.addWidget(rep)
        btns.addStretch()
        done = SecondaryButton("Mark done")
        done.clicked.connect(lambda: self._quick_status('Done'))
        done.setEnabled(not ro)
        btns.addWidget(done)
        self.card_l.addLayout(btns)

        # seen before + guidance (ours, never the customer's)
        if self._pid is not None and row['title']:
            prev = wj.seen_before(self._pid, row)
            if prev:
                self._label(f"Seen before on this node · {len(prev)}")
                for p in prev:
                    lbl = QLabel(f"{p['date']} · {p['status']} · "
                                 f"{p['work_done'][:60] or '—'}")
                    lbl.setStyleSheet("color:#6B7A8D;")
                    lbl.setWordWrap(True)
                    self.card_l.addWidget(lbl)
            g = wj.guidance(self._pid, row['title'])
            if g['note'] or g['vendor']:
                self._label("How we deal with it", internal())
                txt = QLabel((g['note'] + "\n\n" + g['vendor']).strip())
                txt.setWordWrap(True)
                txt.setStyleSheet("color:#1A2B45;background:#F7F8FA;"
                                  "border:1px solid #E0E4EA;border-radius:4px;padding:6px;")
                self.card_l.addWidget(txt)
        self.card_l.addStretch()

    def _show_alarm_card(self, alarm):
        self._clear_card()
        self._fields = {}
        blk = alarm.get('block')
        head = QLabel(f"<span style='font-size:16px;font-weight:bold'>"
                      f"{('Block %s' % blk) if blk else (alarm.get('asset_code') or '')}"
                      f"</span>   <span style='color:#6B7A8D'>SCADA fault</span>")
        self.card_l.addWidget(head)
        for k, v in (("Fault", alarm.get('trigger_name')),
                     ("From", alarm.get('activated')),
                     ("To", alarm.get('deactivated')),
                     ("Hours", alarm.get('hours')),
                     ("Class", alarm.get('cls_reason'))):
            lbl = QLabel(f"<b>{k}:</b> {v if v is not None else '—'}")
            lbl.setWordWrap(True)
            self.card_l.addWidget(lbl)
        self._banner("No work record answers this fault. The customer asks what "
                     "was done about it — write it now, while it is known.", 'info')
        b = PrimaryButton("Create record from this fault")
        b.clicked.connect(lambda: self._record_from_alarm(alarm))
        self.card_l.addWidget(b)
        self.card_l.addStretch()

    # ── writes ───────────────────────────────────────────────────────────
    def _collect(self):
        f = self._fields
        blk = f['block'].text().strip()
        return {
            'status': f['status'].currentText(),
            'block': int(blk) if blk.isdigit() else None,
            'lc': f['lc'].currentText(),
            'device': f['device'].text().strip(),
            'title': f['title'].text().strip(),
            'work_done': f['work_done'].toPlainText().strip(),
            'internal_note': f['internal_note'].toPlainText().strip(),
            'ptw': f['ptw'].text().strip(),
            'sap': f['sap'].text().strip(),
            'time_from': f['time_from'].text().strip(),
            'time_to': f['time_to'].text().strip(),
            'hours': f['hours'].value() or None,
            'impact': f['impact'].currentData(),
        }

    def _save_card(self):
        if self._pid is None or not self._fields:
            return
        row = next((r for r in self._shown if r['key'] == self._key), None)
        data = self._collect()
        data['kind'] = row['kind'] if row else wj.KIND_FAULT
        data['date'] = row['date'] if row else datetime.date.today().isoformat()
        try:
            key = wj.save(self._pid, self._key, **data)
        except wj.ReadOnlyRecord as e:
            QMessageBox.information(self, "Old format", str(e))
            return
        except Exception as e:                           # noqa: BLE001
            QMessageBox.warning(self, "Not saved", str(e))
            return
        self._key = key
        self.record_saved.emit(key)
        self.refresh()
        self._select_key(key)

    def _use_suggestion(self, s):
        """Fill the node from the phone's own text; saving stays explicit."""
        if not self._fields:
            return
        self._fields['block'].setText(str(s['block']))
        if s.get('lc'):
            self._fields['lc'].setCurrentText(s['lc'])

    def _quick_status(self, status):
        if not self._fields:
            return
        self._fields['status'].setCurrentText(status)
        self._save_card()

    def _new_record(self, prefill=None):
        if self._pid is None:
            QMessageBox.information(self, "No project", "Open a project first.")
            return
        blank = {'key': None, 'ref': None, 'source': 'desktop', 'editable': True,
                 'old_format': False,
                 'date': datetime.date.today().isoformat(), 'block': None,
                 'zone_label': '', 'lc': '', 'device': '', 'location': '',
                 'kind': wj.KIND_FAULT, 'title': '', 'work_done': '',
                 'internal_note': '', 'status': 'Open', 'ptw': '', 'sap': '',
                 'hours': None, 'time_from': '', 'time_to': '', 'impact': 'none',
                 'sync': 'local', 'category': 'fault', 'container_id': None,
                 'node': 'New record', 'age_days': 0}
        blank.update(prefill or {})
        self._key = None
        self._show_card(blank)

    def _repeat(self):
        """The same defect on the next node: a copy without the node, so 13
        identical repairs are 13 quick records instead of 13 full forms."""
        if not self._fields:
            return
        data = self._collect()
        data.update(block=None, device='', status='Open')
        self._new_record({'title': data['title'], 'work_done': data['work_done'],
                          'internal_note': data['internal_note'],
                          'ptw': data['ptw'], 'lc': data['lc']})

    def _record_from_alarm(self, alarm):
        blk = alarm.get('block')
        self._set_tab('all')
        self._new_record({
            'block': int(blk) if blk else None,
            'zone_label': wj.zone_label(self._pid, blk) if blk else '',
            'lc': f"LC{alarm['lc']}" if alarm.get('lc') else '',
            'title': alarm.get('trigger_name') or '',
            'date': str(alarm.get('activated') or '')[:10]
                    or datetime.date.today().isoformat(),
            # SCADA already counts this stop: counting it again here would
            # charge the same downtime twice
            'impact': 'none',
            'node': (f"Block {blk}" if blk else '') + (
                f" · LC{alarm['lc']}" if alarm.get('lc') else ''),
        })

    def _select_key(self, key):
        for i, r in enumerate(self._shown):
            if r['key'] == key:
                self.table.selectRow(i)
                return
