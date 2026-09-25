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
  * Materials: what the field reported using, the structured rows the office
    writes off, and the warehouse they leave. Stock moves only when somebody
    presses the button and confirms it — never on save.
"""
import datetime

from PyQt5.QtCore import Qt, pyqtSignal, QDate
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QLineEdit, QTextEdit, QCheckBox, QTableWidget, QTableWidgetItem, QSplitter,
    QAbstractItemView, QScrollArea, QFrame, QMessageBox, QHeaderView,
    QDoubleSpinBox, QSizePolicy, QGridLayout, QDateEdit, QDialog,
)

import services.team_service as team
import services.work_journal_service as wj
import services.worklog_parts_service as wparts
from ui.components import PageHeader, PrimaryButton, SecondaryButton

TABS = [('all', 'All'), ('open', 'Open'), ('scada', 'Needs a record (SCADA)'),
        ('noblock', 'No block'), ('conflict', 'Conflicts')]

# "Assigned to" is a column, not a detail: the office's first question in the
# morning is what is with whom. "Materials" is the second one: a record with
# parts still waiting to leave stock is office work nobody has finished.
COLUMNS = ["Date", "Node", "Work", "Status", "PTW No.", "Assigned to",
           "Materials", "Source"]
ALARM_COLUMNS = ["Start", "Node", "Fault", "Hours", "Class", "", "", "Source"]

STATUSES = ['Needs visit', 'Open', 'In progress', 'Done']
IMPACTS = [('none', 'None'), ('counts', 'Counts'), ('excluded', 'Excluded')]
# the phone's node picker offers the same devices
DEVICES = ['', 'PCS 1', 'PCS 2', 'PCS 3', 'PCS 4', 'BESS 1', 'BESS 2', 'BESS 3',
           'BESS 4', 'LC cabinet', 'MV station']

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


def parts_badge(count: dict) -> str:
    """The Materials cell for one record: what the office still has to do."""
    if not count or not count.get('total'):
        return ''
    if count.get('pending'):
        return f"{count['pending']} pending"
    return f"{count['deducted']} written off"


def _materials_combo(current: str = '') -> QComboBox:
    """The material catalogue, searchable — type a number or part of a name."""
    cb = QComboBox()
    cb.setEditable(True)
    cb.setInsertPolicy(QComboBox.NoInsert)
    cb.setMaxVisibleItems(20)
    # A combo asks to be as wide as its widest entry, and a material
    # description runs long — left alone, one catalogue row would widen the
    # whole card. The popup still shows the full text.
    cb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    cb.setMinimumContentsLength(18)
    cb.addItem('', '')
    try:
        from services.material_service import get_all_materials
        for m in get_all_materials():
            num = m['material_number']
            desc = m.get('description') or ''
            cb.addItem(f"{num} — {desc}" if desc else num, num)
    except Exception:                                    # noqa: BLE001
        pass
    if cb.completer() is not None:
        cb.completer().setFilterMode(Qt.MatchContains)
    i = cb.findData(current) if current else 0
    cb.setCurrentIndex(max(0, i))
    if current and i < 0:
        cb.setEditText(current)                  # a number not in the catalogue
    cb.lineEdit().setPlaceholderText("material number or name")
    return cb


def _combo_number(cb: QComboBox) -> str:
    """The material number a combo holds — picked from the list or typed."""
    data = cb.currentData()
    if data:
        return str(data)
    txt = cb.currentText().strip()
    if ' — ' in txt:
        txt = txt.split(' — ', 1)[0].strip()
    return txt


class _PartDialog(QDialog):
    """Edit one pending material row: material, quantity, unit."""

    def __init__(self, part: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Material row")
        self.setMinimumWidth(380)
        lay = QVBoxLayout(self)
        grid = QGridLayout()
        self.mat = _materials_combo(part.get('material_number') or '')
        self.qty = QDoubleSpinBox()
        self.qty.setRange(0.0, 1_000_000.0)
        self.qty.setDecimals(2)
        self.qty.setValue(float(part.get('quantity') or 1))
        self.unit = QLineEdit(part.get('unit') or '')
        self.unit.setPlaceholderText("pcs, m, l…")
        for r, (label, w) in enumerate((("Material", self.mat),
                                        ("Quantity", self.qty),
                                        ("Unit", self.unit))):
            grid.addWidget(QLabel(label), r, 0)
            grid.addWidget(w, r, 1)
        lay.addLayout(grid)
        btns = QHBoxLayout()
        btns.addStretch()
        cancel = SecondaryButton("Cancel")
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        save = PrimaryButton("Save")
        save.clicked.connect(self.accept)
        btns.addWidget(save)
        lay.addLayout(btns)

    def value(self) -> dict:
        return {'material_number': _combo_number(self.mat),
                'quantity': self.qty.value(),
                'unit': self.unit.text().strip()}


class MaterialsSection(QWidget):
    """The materials of one record, and the write-off to stock.

    Three things the office could not see anywhere before: what the field said
    it used (the phone's own free text), which materials are booked against the
    record, and which of them have actually left a warehouse.

    Nothing here touches stock until "Write off from stock" is pressed and the
    confirmation — warehouse, rows, any shortfall — is accepted. That is the
    service layer's design decision (worklog_parts_service's docstring) and the
    reason a record can be saved a hundred times without moving a single fuse.
    """
    changed = pyqtSignal()

    def __init__(self, entry_id: str, phone_note: str = '', parent=None):
        super().__init__(parent)
        self._entry_id = str(entry_id)
        self._parts = []
        self._build(phone_note)
        self.refresh()

    # ── build ────────────────────────────────────────────────────────────
    def _build(self, phone_note):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(6)
        self.head_lbl = QLabel("<b>Materials</b>")
        head.addWidget(self.head_lbl)
        head.addWidget(internal())
        head.addStretch()
        lay.addLayout(head)

        if phone_note:
            note = QLabel(f'Reported used: “{phone_note}”')
            note.setTextFormat(Qt.PlainText)
            note.setWordWrap(True)
            note.setStyleSheet("color:#1A2B45;background:#F7F8FA;"
                               "border:1px solid #E0E4EA;border-radius:4px;"
                               "padding:6px;")
            note.setToolTip("What the field wrote on the phone. The office "
                            "books the materials below against it.")
            lay.addWidget(note)

        self.wh_lbl = QLabel("")
        self.wh_lbl.setStyleSheet("color:#6B7A8D;")
        self.wh_lbl.setWordWrap(True)
        lay.addWidget(self.wh_lbl)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Material", "Description", "Qty", "Unit", "Status"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setFixedHeight(126)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        for col, w in ((0, 110), (2, 54), (3, 48), (4, 150)):
            self.table.setColumnWidth(col, w)
        self.table.itemSelectionChanged.connect(self._sync_buttons)
        lay.addWidget(self.table)

        self.empty_lbl = QLabel("No materials on this record yet — pick one below.")
        self.empty_lbl.setStyleSheet("color:#6B7A8D;")
        self.empty_lbl.setWordWrap(True)
        lay.addWidget(self.empty_lbl)

        add = QHBoxLayout()
        add.setSpacing(6)
        self.mat_cb = _materials_combo()
        self.mat_cb.setMinimumWidth(200)
        add.addWidget(self.mat_cb, 3)
        self.qty_spin = QDoubleSpinBox()
        self.qty_spin.setRange(0.0, 1_000_000.0)
        self.qty_spin.setDecimals(2)
        self.qty_spin.setValue(1.0)
        self.qty_spin.setFixedWidth(80)
        add.addWidget(self.qty_spin)
        self.unit_edit = QLineEdit()
        self.unit_edit.setPlaceholderText("unit")
        self.unit_edit.setFixedWidth(60)
        self.unit_edit.setToolTip("Leave empty to take the unit from the "
                                  "material catalogue.")
        add.addWidget(self.unit_edit)
        self.add_btn = SecondaryButton("Add")
        self.add_btn.clicked.connect(self._add)
        add.addWidget(self.add_btn)
        lay.addLayout(add)

        # what the warehouse holds of the material being picked, before it is
        # added — the moment to notice you are booking 5 against a stock of 1
        self.avail_lbl = QLabel("")
        self.avail_lbl.setStyleSheet("color:#6B7A8D;")
        self.avail_lbl.setWordWrap(True)
        lay.addWidget(self.avail_lbl)
        self.mat_cb.currentIndexChanged.connect(self._show_available)
        self.mat_cb.lineEdit().editingFinished.connect(self._show_available)

        self.catalogue_lbl = QLabel(
            "The material catalogue is empty — add the materials on the "
            "Spare parts page first, or type a material number here.")
        self.catalogue_lbl.setStyleSheet("color:#B26B00;")
        self.catalogue_lbl.setWordWrap(True)
        self.catalogue_lbl.setVisible(self.mat_cb.count() <= 1)
        lay.addWidget(self.catalogue_lbl)

        acts = QHBoxLayout()
        acts.setSpacing(6)
        self.off_btn = PrimaryButton("Write off from stock")
        self.off_btn.setToolTip("Records an OUT stock transaction for every "
                                "pending row. Asks first.")
        self.off_btn.clicked.connect(self._write_off)
        acts.addWidget(self.off_btn)
        self.edit_btn = SecondaryButton("Edit")
        self.edit_btn.clicked.connect(self._edit)
        acts.addWidget(self.edit_btn)
        self.del_btn = SecondaryButton("Remove")
        self.del_btn.clicked.connect(self._remove)
        acts.addWidget(self.del_btn)
        self.undo_btn = SecondaryButton("Undo write-off")
        self.undo_btn.setToolTip("Returns the quantity to the warehouse it "
                                 "left, as an IN transaction.")
        self.undo_btn.clicked.connect(self._undo)
        acts.addWidget(self.undo_btn)
        acts.addStretch()
        lay.addLayout(acts)

        hint = QLabel("Nothing leaves stock until you press “Write off from "
                      "stock”. Each write-off is an OUT transaction referenced "
                      f"LOG-{self._entry_id[:8]}; undoing one books it back in.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#6B7A8D;font-size:11px;")
        lay.addWidget(hint)

    # ── read ─────────────────────────────────────────────────────────────
    def refresh(self):
        try:
            self._parts = wparts.get_parts_for_entry(self._entry_id)
        except Exception as e:                           # noqa: BLE001
            self._parts = []
            self.wh_lbl.setText(f"Stock unavailable: {e}")
        self.table.setRowCount(0)
        pending = 0
        for p in self._parts:
            r = self.table.rowCount()
            self.table.insertRow(r)
            first = QTableWidgetItem(p['material_number'])
            first.setData(Qt.UserRole, p['id'])
            self.table.setItem(r, 0, first)
            self.table.setItem(r, 1, QTableWidgetItem(p.get('description') or ''))
            self.table.setItem(r, 2, QTableWidgetItem(f"{p['quantity']:g}"))
            self.table.setItem(r, 3, QTableWidgetItem(p.get('unit') or ''))
            if p['deducted']:
                st = QTableWidgetItem("Written off · "
                                      + (p.get('warehouse_name') or 'stock'))
                st.setForeground(QColor('#2E7D32'))
            else:
                avail = float(p.get('available') or 0)
                short = avail < float(p['quantity'])
                st = QTableWidgetItem(
                    f"Pending · in stock {avail:g}"
                    + (f" — short {float(p['quantity']) - avail:g}" if short else ""))
                st.setForeground(QColor('#C62828') if short else QColor('#B26B00'))
                pending += 1
            self.table.setItem(r, 4, st)

        self.head_lbl.setText(
            "<b>Materials</b>" if not self._parts else
            f"<b>Materials · {len(self._parts)}</b>"
            + (f" · {pending} pending" if pending else " · all written off"))
        self.empty_lbl.setVisible(not self._parts)
        self.table.setVisible(bool(self._parts))
        wh_name = (self._parts[0]['target_warehouse_name'] if self._parts
                   else self._warehouse_name())
        self.wh_lbl.setText(f"Write off from: {wh_name}")
        self.off_btn.setText(f"Write off from stock ({pending})" if pending
                             else "Write off from stock")
        self.off_btn.setEnabled(pending > 0)
        self._sync_buttons()

    def _show_available(self, *_):
        """The stock on hand for the material in the picker, if it is one the
        warehouse knows. Queried when a material is chosen, not per keystroke."""
        mat = _combo_number(self.mat_cb)
        if not mat:
            self.avail_lbl.setText("")
            return
        try:
            from services.stock_service import get_stock_quantity
            wh = wparts.resolve_warehouse_for_entry(self._entry_id)
            stock = get_stock_quantity(wh['id'], mat)
        except Exception:                                # noqa: BLE001
            self.avail_lbl.setText("")
            return
        if stock is None:
            self.avail_lbl.setText(f"{mat} is not stocked in {wh['name']} — "
                                   "it can be booked, but not written off yet.")
        else:
            self.avail_lbl.setText(
                f"{mat}: {float(stock['quantity']):g} "
                f"{stock.get('unit') or ''} in {wh['name']}".rstrip())

    def _warehouse_name(self) -> str:
        try:
            return wparts.resolve_warehouse_for_entry(self._entry_id)['name']
        except Exception:                                # noqa: BLE001
            return '—'

    def _current(self):
        r = self.table.currentRow()
        if r < 0:
            return None
        it = self.table.item(r, 0)
        if it is None:
            return None
        pid = it.data(Qt.UserRole)
        return next((p for p in self._parts if p['id'] == pid), None)

    def _sync_buttons(self):
        p = self._current()
        done = bool(p and p['deducted'])
        self.edit_btn.setEnabled(bool(p) and not done)
        self.del_btn.setEnabled(bool(p) and not done)
        self.undo_btn.setEnabled(done)
        why = ("This row is already written off — undo the write-off first."
               if done else "")
        for b in (self.edit_btn, self.del_btn):
            b.setToolTip(why or "Select a pending row.")

    # ── writes ───────────────────────────────────────────────────────────
    def _add(self):
        mat = _combo_number(self.mat_cb)
        if not mat:
            QMessageBox.information(self, "Material",
                                    "Pick a material, or type its number.")
            return
        qty = self.qty_spin.value()
        if qty <= 0:
            QMessageBox.information(self, "Quantity",
                                    "The quantity has to be more than 0.")
            return
        try:
            wparts.add_part(self._entry_id, mat, qty,
                            unit=self.unit_edit.text().strip())
        except Exception as e:                           # noqa: BLE001
            QMessageBox.warning(self, "Not added", str(e))
            return
        self.mat_cb.setCurrentIndex(0)
        self.qty_spin.setValue(1.0)
        self.unit_edit.clear()
        self.refresh()
        self.changed.emit()

    def _edit(self):
        p = self._current()
        if not p:
            return
        dlg = _PartDialog(p, self)
        if dlg.exec_() != QDialog.Accepted:
            return
        v = dlg.value()
        if not v['material_number'] or v['quantity'] <= 0:
            QMessageBox.information(self, "Material row",
                                    "A material and a quantity above 0 are needed.")
            return
        try:
            wparts.update_part(p['id'], **v)
        except ValueError as e:
            QMessageBox.warning(self, "Not changed", str(e))
        self.refresh()
        self.changed.emit()

    def _remove(self):
        p = self._current()
        if not p:
            return
        if QMessageBox.question(
                self, "Remove material",
                f"Remove {p['quantity']:g} x {p['material_number']} from this "
                "record?\n\nNothing has left stock, so nothing is booked back.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            wparts.delete_part(p['id'])
        except ValueError as e:
            QMessageBox.warning(self, "Not removed", str(e))
        self.refresh()
        self.changed.emit()

    def _write_off(self):
        plan = wparts.plan_deduction(self._entry_id)
        if not plan['pending']:
            QMessageBox.information(self, "Write off from stock",
                                    "Every material on this record is already "
                                    "written off.")
            return
        wh = plan['warehouse']['name']

        def lines(rows):
            return "\n".join(
                f"  {p['quantity']:g} {p.get('unit') or ''} x {p['material_number']}"
                f"   (in stock: {float(p.get('available') or 0):g})"
                for p in rows)

        if plan['short']:
            short = "\n".join(
                f"  {s['material_number']}: {s['wanted']:g} asked, "
                f"{s['available']:g} in stock — short {s['short']:g}"
                for s in plan['short'])
            tail = ("\n\nBook the delivery in on Spare parts, or lower the "
                    "quantity. Nothing has been written off.")
            if not plan['ok']:
                QMessageBox.warning(self, "Not enough in stock",
                                    f"“{wh}” does not hold enough:\n\n{short}{tail}")
                return
            if QMessageBox.question(
                    self, "Not enough in stock",
                    f"“{wh}” does not hold enough:\n\n{short}\n\n"
                    f"Write off the other {len(plan['ok'])} row(s) now?\n\n"
                    f"{lines(plan['ok'])}",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No) != QMessageBox.Yes:
                return
        elif QMessageBox.question(
                self, "Write off from stock",
                f"Write off from “{wh}”:\n\n{lines(plan['pending'])}\n\n"
                f"This records an OUT stock transaction referenced "
                f"LOG-{self._entry_id[:8]}. Continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No) != QMessageBox.Yes:
            return

        res = wparts.deduct_all_pending(self._entry_id)
        if res['failed']:
            QMessageBox.warning(
                self, "Partly written off",
                f"Written off: {res['deducted']} of {res['total_pending']}.\n\n"
                "Not written off:\n" + "\n".join("  " + f for f in res['failed']))
        else:
            QMessageBox.information(
                self, "Written off",
                f"{res['deducted']} material row(s) left “{wh}”. "
                "The movement is in the stock transactions as "
                f"LOG-{self._entry_id[:8]}.")
        self.refresh()
        self.changed.emit()

    def _undo(self):
        p = self._current()
        if not p:
            return
        wh = p.get('warehouse_name') or p.get('target_warehouse_name') or 'stock'
        if QMessageBox.question(
                self, "Undo write-off",
                f"Book {p['quantity']:g} x {p['material_number']} back into "
                f"“{wh}”?\n\nThe write-off stays in the transaction trail; this "
                "adds the movement that cancels it.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        res = wparts.reverse_part(p['id'])
        if not res['ok']:
            QMessageBox.warning(self, "Not undone", res['message'])
        self.refresh()
        self.changed.emit()


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
        self._parts = None          # the open card's MaterialsSection, if any
        self._build_ui()

    # ── UI ───────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.header = PageHeader("Work", "Every record of work — phone and desktop in one list")
        self.export_btn = SecondaryButton("Export to Excel")
        self.export_btn.setToolTip("The records in the list as shown — tab, filters and "
                                   "period apply. No photos.")
        self.export_btn.clicked.connect(self._export_excel)
        self.header.add_action(self.export_btn)
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
        self.assignee_cb = QComboBox()
        self.assignee_cb.setMinimumWidth(130)
        self.assignee_cb.setToolTip("What is with whom — the jobs the office "
                                    "gave to a technician")
        self._fill_assignee_chip()
        self.ptw_chk = QCheckBox("Has PTW")
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Block, fault, PTW…")
        self.search_edit.setMinimumWidth(180)
        for w in (self.kind_cb, self.status_cb, self.block_edit, self.period_cb,
                  self.source_cb, self.assignee_cb, self.ptw_chk):
            chips.addWidget(w)
        chips.addStretch()
        chips.addWidget(self.search_edit)
        root.addLayout(chips)
        for cb in (self.kind_cb, self.status_cb, self.period_cb, self.source_cb,
                   self.assignee_cb):
            cb.currentIndexChanged.connect(self._apply)
        self.ptw_chk.stateChanged.connect(self._apply)
        self.block_edit.textChanged.connect(self._apply)
        self.search_edit.textChanged.connect(self._apply)

        # list + card
        split = QSplitter(Qt.Horizontal)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(2, QHeaderView.Stretch)      # the work, not the note
        for col, w in ((0, 96), (1, 190), (3, 96), (4, 110), (5, 110), (6, 104),
                       (7, 92)):
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
                   self.source_cb, self.assignee_cb, self.ptw_chk,
                   self.block_edit, self.search_edit)
        for w in widgets:
            w.blockSignals(True)
        try:
            self.kind_cb.setCurrentIndex(max(0, self.kind_cb.findData(flt.get('kind'))))
            self.status_cb.setCurrentIndex(
                max(0, self.status_cb.findData(flt.get('status'))))
            self.source_cb.setCurrentIndex(0)
            self.assignee_cb.setCurrentIndex(
                max(0, self.assignee_cb.findData(flt.get('assignee'))))
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
        self._fill_assignee_chip()     # a sync may have brought new accounts
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
            assignee=self.assignee_cb.currentData(),
            text=self.search_edit.text().strip() or None)
        self._fill(rows)

    def _fill_assignee_chip(self):
        """The technicians this desktop knows, cached from the last sync."""
        cur = self.assignee_cb.currentData()
        self.assignee_cb.blockSignals(True)
        self.assignee_cb.clear()
        self.assignee_cb.addItem("Anyone", None)
        self.assignee_cb.addItem("Nobody yet", wj.ASSIGNED_NOBODY)
        for u in team.users():
            self.assignee_cb.addItem(u['username'], u['id'])
        i = self.assignee_cb.findData(cur)
        self.assignee_cb.setCurrentIndex(max(0, i))
        self.assignee_cb.blockSignals(False)

    def _fill(self, rows):
        self._shown = rows
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setRowCount(0)
        counts = self._parts_counts(rows)
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
            who = r.get('assignee_name') or (
                team.name_for(r.get('assignee')) if r.get('assignee') else '')
            cells = [r['date'], r['node'], work, r['status'], r['ptw'], who,
                     parts_badge(counts.get(str(r['ref']))), src]
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

    def _parts_counts(self, rows):
        """One query for the whole list's Materials column — never one per row.
        Old-format rows are `work_logs` ids and cannot carry parts."""
        refs = [str(r['ref']) for r in rows
                if r.get('ref') and not r.get('old_format')]
        if not refs:
            return {}
        try:
            return wparts.counts_for_entries(refs)
        except Exception:                                # noqa: BLE001
            return {}

    def _fill_alarms(self):
        """Needs a record (SCADA): alarms nobody has written up yet."""
        self._shown = []
        self.table.setRowCount(0)
        self.table.setHorizontalHeaderLabels(ALARM_COLUMNS)
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
                                   a.get('cls_reason') or '', '', '', 'SCADA']):
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
        self._parts = None
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

        # Who is to do it. The record itself is the job: the technician sees
        # this one in Tasks on the phone and fills it in — no second record.
        self._fields['assignee'] = QComboBox()
        self._fields['assignee'].addItem("— nobody —", '')
        for u in team.users():
            self._fields['assignee'].addItem(u['username'], u['id'])
        cur_id = row.get('assignee') or ''
        if cur_id and self._fields['assignee'].findData(cur_id) < 0:
            # assigned to somebody this desktop has not synced yet
            self._fields['assignee'].addItem(
                row.get('assignee_name') or 'someone else', cur_id)
        self._fields['assignee'].setCurrentIndex(
            max(0, self._fields['assignee'].findData(cur_id)))
        who_row = QHBoxLayout()
        who_row.addWidget(self._fields['assignee'])
        note = []
        if row.get('assigned_by'):
            note.append(f"given by {row['assigned_by']}")
        if row.get('due'):
            note.append(f"due {row['due']}")
        if not team.users():
            note.append("sync once to list the technicians")
        lbl = QLabel(' · '.join(note))
        lbl.setStyleSheet("color:#6B7A8D;")
        who_row.addWidget(lbl)
        who_row.addStretch()
        holder = QWidget()
        holder.setLayout(who_row)
        add("Assigned to", holder)

        self._fields['date'] = QDateEdit()
        self._fields['date'].setCalendarPopup(True)
        self._fields['date'].setDisplayFormat("dd.MM.yyyy")
        d = QDate.fromString((row['date'] or '')[:10], "yyyy-MM-dd")
        self._fields['date'].setDate(d if d.isValid() else QDate.currentDate())
        add("Date", self._fields['date'])

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
        self._fields['device'] = QComboBox()
        self._fields['device'].setEditable(True)
        self._fields['device'].addItems(DEVICES)
        self._fields['device'].setCurrentText(row['device'] or '')
        self._fields['device'].lineEdit().setPlaceholderText("device")
        blk_row.addWidget(self._fields['device'])
        holder = QWidget()
        holder.setLayout(blk_row)
        add("Node", holder)

        # the serial follows the node from the project's container list;
        # a typed one (a swapped unit) is kept
        self._fields['serial'] = QLineEdit(row.get('serial') or '')
        self._fields['serial'].setPlaceholderText("from the project when the node names one container")
        self._auto_serial_val = None
        add("Serial No.", self._fields['serial'])
        self._fields['block'].editingFinished.connect(self._auto_serial)
        self._fields['device'].currentTextChanged.connect(self._auto_serial)
        self._auto_serial(prime=True)

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
        dele = SecondaryButton("Delete")
        dele.setStyleSheet("color:#C62828;")
        dele.clicked.connect(self._delete_card)
        dele.setEnabled(not ro and row.get('key') is not None)
        btns.addWidget(dele)
        self.card_l.addLayout(btns)

        if row.get('key') and not row['old_format']:
            self._photos_section(row)
        if not row['old_format']:
            self._parts_section(row)

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

    def _export_excel(self):
        import os
        from PyQt5.QtWidgets import QFileDialog
        if self._tab == 'scada':
            QMessageBox.information(self, "Export to Excel",
                                    "Open a records tab (All, Open, No block…) to export.")
            return
        rows = list(self._shown)
        if not rows:
            QMessageBox.information(self, "Export to Excel", "No records in the list.")
            return
        ans = QMessageBox.question(
            self, "Export to Excel",
            f"{len(rows)} record(s), as the list shows them.\n\n"
            "Include the internal diagnosis notes?\n"
            "Choose No if the file may go to the customer.",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.No)
        if ans == QMessageBox.Cancel:
            return
        period = (f"{self._year}-{self._month:02d}"
                  if self.period_cb.currentData() == 'month' and self._year else "all time")
        name = f"Work records {self._name} {period}.xlsx".replace('/', '-')
        path, _ = QFileDialog.getSaveFileName(
            self, "Export to Excel",
            os.path.join(os.path.expanduser('~'), 'Documents', name), "Excel (*.xlsx)")
        if not path:
            return
        try:
            n = wj.export_excel(rows, path, include_internal=(ans == QMessageBox.Yes))
        except PermissionError:
            QMessageBox.warning(self, "Not saved",
                                "The file is open in Excel. Close it and export again.")
            return
        except Exception as e:                           # noqa: BLE001
            QMessageBox.warning(self, "Not saved", str(e))
            return
        if QMessageBox.question(self, "Exported",
                                f"{n} record(s) saved to\n{path}\n\nOpen the file?",
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            os.startfile(path)

    def _photos_section(self, row):
        """Thumbnails (click to view), the record's photo folder, save a copy."""
        from services.image_service import get_images_for_log
        imgs = get_images_for_log(row['ref'])
        if not imgs:
            return
        self._label(f"Photos · {len(imgs)}")
        from ui.worklog_entry_form import PhotoThumb
        strip = QHBoxLayout()
        strip.setSpacing(6)
        for img in imgs[:8]:
            th = PhotoThumb(img.get('thumbnail_path') or img.get('file_path') or '',
                            image_id=img.get('id', ''),
                            upload_status=img.get('upload_status', 'local'))
            th.clicked.connect(lambda _=None, i=img: self._open_photo(row, i))
            strip.addWidget(th)
        if len(imgs) > 8:
            strip.addWidget(QLabel(f"+{len(imgs) - 8}"))
        strip.addStretch()
        holder = QWidget()
        holder.setLayout(strip)
        scroll = QScrollArea()
        scroll.setWidget(holder)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setFixedHeight(holder.sizeHint().height() + 18)
        self.card_l.addWidget(scroll)

        pb = QHBoxLayout()
        opn = SecondaryButton("Open photo folder")
        opn.setToolTip(f"{wj.photos_root()}\\{self._name}\\{wj.photo_folder_name(row)}")
        opn.clicked.connect(lambda: self._open_photo_folder(row))
        pb.addWidget(opn)
        sv = SecondaryButton("Save photos to…")
        sv.clicked.connect(lambda: self._save_photos(row))
        pb.addWidget(sv)
        pb.addStretch()
        self.card_l.addLayout(pb)

    def _parts_section(self, row):
        """Materials: what the field reported, what the office booked, and the
        write-off to stock. Parts hang on the record's id, so an unsaved record
        says so instead of offering controls that would have nowhere to write."""
        if not row.get('key') or not row.get('ref'):
            self._label("Materials", internal())
            hint = QLabel("Save the record first — materials and their write-off "
                          "hang on the saved record.")
            hint.setWordWrap(True)
            hint.setStyleSheet("color:#6B7A8D;")
            self.card_l.addWidget(hint)
            if row.get('parts'):
                note = QLabel(f'Reported used: “{row["parts"]}”')
                note.setTextFormat(Qt.PlainText)
                note.setWordWrap(True)
                note.setStyleSheet("color:#1A2B45;background:#F7F8FA;"
                                   "border:1px solid #E0E4EA;border-radius:4px;"
                                   "padding:6px;")
                self.card_l.addWidget(note)
            return
        self._parts = MaterialsSection(row['ref'], row.get('parts') or '', self)
        self._parts.changed.connect(self._parts_changed)
        self.card_l.addWidget(self._parts)

    def _parts_changed(self):
        """Keep the list's Materials cell in step with the card. Only that one
        cell: reloading the list would throw the open card away, and with it
        whatever the user has typed into it but not saved."""
        if self._key is None:
            return
        i, row = next(((i, r) for i, r in enumerate(self._shown)
                       if r['key'] == self._key), (None, None))
        if row is None:
            return
        col = COLUMNS.index("Materials")
        count = wparts.get_parts_count(str(row['ref']))
        item = self.table.item(i, col)
        if item is None:
            item = QTableWidgetItem('')
            self.table.setItem(i, col, item)
        item.setText(parts_badge(count))

    def _open_photo(self, row, img):
        import os
        path = img.get('file_path') or ''
        if not os.path.isfile(path):
            from services.image_service import download_remote_image
            path = download_remote_image(img['id'], row['ref']) or ''
        if not os.path.isfile(path):
            QMessageBox.information(self, "Photo on the server",
                                    "This photo is not on this computer yet. "
                                    "It arrives with the next sync.")
            return
        from ui.worklog_entry_form import ImageViewerDialog
        ImageViewerDialog(path, self).exec_()

    def _open_photo_folder(self, row):
        import os
        try:
            res = wj.mirror_record_photos(self._name, row)
        except Exception as e:                           # noqa: BLE001
            QMessageBox.warning(self, "Photo folder", str(e))
            return
        if res:
            os.startfile(res['folder'])

    def _save_photos(self, row):
        import os
        from PyQt5.QtWidgets import QFileDialog
        from services.image_service import export_entry_images
        root = QFileDialog.getExistingDirectory(self, "Save photos to")
        if not root:
            return
        res = export_entry_images(row['ref'], os.path.join(root, wj.photo_folder_name(row)))
        msg = f"Saved {res['saved']} of {res['total']} photo(s) to\n{res['dest']}"
        if res['failed']:
            msg += f"\n\nNot saved: {len(res['failed'])} (still on the server — sync first)."
        if QMessageBox.question(self, "Photos saved", msg + "\n\nOpen the folder?",
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            os.startfile(res['dest'])

    def _show_alarm_card(self, alarm):
        self._clear_card()
        self._fields = {}
        self._parts = None
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
        from services.sync_config import sync_config
        f = self._fields
        blk = f['block'].text().strip()
        who = f['assignee'].currentData() or ''
        date = f['date'].date().toString("yyyy-MM-dd")
        # No 'parts' key on purpose. The free-text spare_parts note is the
        # phone's; the card shows it and never writes it, and save() only
        # touches the columns whose keys are here — so a desktop save cannot
        # blank what the field reported. test_work_parts.py pins this.
        return {
            'status': f['status'].currentText(),
            'date': date,
            'assignee': who,
            'assignee_name': f['assignee'].currentText() if who else '',
            # who handed it out, so the phone can say where the job came from
            'assigned_by': (sync_config.username or '') if who else '',
            # an assigned record IS the work order: its date is when it is
            # wanted, and that is what the phone shows as the due date
            'due': date if who else '',
            'block': int(blk) if blk.isdigit() else None,
            'lc': f['lc'].currentText(),
            'device': f['device'].currentText().strip(),
            'serial': f['serial'].text().strip(),
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
        self._auto_serial()

    def _auto_serial(self, *_, prime=False):
        """Put the project's serial for the node into the card. Only a blank
        field or one this card filled is replaced — never a typed number."""
        f = self._fields
        if self._pid is None or 'serial' not in f:
            return
        blk = f['block'].text().strip()
        _cid, serial = wj.container_for_node(
            self._pid, int(blk) if blk.isdigit() else None,
            f['device'].currentText())
        cur = f['serial'].text().strip()
        if prime and cur and cur == serial:
            self._auto_serial_val = serial
            return
        if cur and cur != self._auto_serial_val:
            return
        f['serial'].setText(serial)
        self._auto_serial_val = serial

    def _delete_card(self):
        if not self._key:
            return
        row = next((r for r in self._shown if r['key'] == self._key), None)
        what = (f"{row['date']} · {row['node']} · "
                f"{row['title'] or row['work_done'][:50]}") if row else ''
        if QMessageBox.question(
                self, "Delete record",
                f"Delete this record?\n\n{what}\n\nIt leaves the journal and the "
                "monthly report, and the phones after the next sync.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            wj.delete(self._key)
        except wj.ReadOnlyRecord as e:
            QMessageBox.information(self, "Old format", str(e))
            return
        except Exception as e:                           # noqa: BLE001
            QMessageBox.warning(self, "Not deleted", str(e))
            return
        deleted, self._key = self._key, None
        self.record_saved.emit(deleted)
        self.refresh()
        self._show_card(None)

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
                 'assignee': '', 'assignee_name': '', 'assigned_by': '', 'due': '',
                 'sync': 'local', 'category': 'fault', 'container_id': None,
                 'serial': '', 'parts': '',
                 'node': 'New record', 'age_days': 0}
        blank.update(prefill or {})
        self._key = None
        # drop the highlight, or a click on the same row would not reopen it
        self.table.blockSignals(True)
        self.table.clearSelection()
        self.table.blockSignals(False)
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
                if self._key != key:          # the row was already selected
                    self._on_select()
                return
