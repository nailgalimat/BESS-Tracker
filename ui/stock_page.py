"""
ui/stock_page.py
-----------------
Spare Parts Stock Management.

Four inner tabs:
  1. Stock View   — see current levels per warehouse, low-stock alerts
  2. Transaction  — record IN / OUT / TRANSFER
  3. History      — audit trail of all movements
  4. Consumption  — the old Daily Log: what was issued, to which block, and why.
                    **Read-only history** — the same rows the Work page shows,
                    through the one query in services/log_service.py, seen here
                    from the material's side. Nothing on this tab moves stock:
                    whatever these rows deducted was booked when they were
                    written.

Stock counts arrive as SAP exports; Import from Excel reads one through
services/stock_excel_service.py and always shows what it would change first —
nothing is written until that preview is confirmed.
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QComboBox, QDateEdit, QLineEdit, QDoubleSpinBox,
    QPushButton, QTableWidget, QTableWidgetItem, QGroupBox,
    QHeaderView, QAbstractItemView, QTabWidget, QMessageBox,
    QFileDialog, QTextEdit, QDialog, QDialogButtonBox, QInputDialog
)
from PyQt5.QtCore import Qt, QDate
from PyQt5.QtGui import QColor

from ui.components import PageHeader, PrimaryButton, SecondaryButton, make_table
from services import stock_excel_service as stock_excel
from services.stock_service import (
    get_all_warehouses, get_main_warehouse,
    ensure_project_warehouse, get_stock,
    get_stock_all_warehouses, set_stock_level,
    record_transaction, get_transactions,
    get_low_stock_items, export_stock_excel,
    create_warehouse, rename_warehouse,
    TRANSACTION_TYPES,
)
from services.project_service import get_all_projects
from services.material_service import get_material_numbers, get_material


class StockPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = PageHeader("Spare Parts Stock",
                            "Warehouse inventory and transactions")
        layout.addWidget(header)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        self.stock_tab = StockViewTab()
        self.tabs.addTab(self.stock_tab, "📦  Stock Levels")

        self.tx_tab = TransactionTab()
        self.tabs.addTab(self.tx_tab, "↕  Record Transaction")

        self.hist_tab = StockHistoryTab()
        self.tabs.addTab(self.hist_tab, "📋  History")

        self.cons_tab = ConsumptionHistoryTab()
        self.tabs.addTab(self.cons_tab, "🗂  Consumption history")

        self.tabs.currentChanged.connect(self._on_tab)

    def _on_tab(self, idx):
        if idx == 0: self.stock_tab.refresh()
        elif idx == 1: self.tx_tab.refresh()
        elif idx == 2: self.hist_tab.refresh()
        elif idx == 3: self.cons_tab.refresh()

    def set_current_project(self, pid, name=None):
        """The shell's open project — the consumption history is per project."""
        self.cons_tab.set_current_project(pid)

    def refresh_projects(self):
        self.tx_tab.refresh()
        self.hist_tab.refresh()
        self.cons_tab.refresh()


# ── STOCK VIEW ────────────────────────────────────────────────────────────────

class StockViewTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)

        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Warehouse:"))
        self.wh_combo = QComboBox()
        self.wh_combo.setMinimumWidth(220)
        self.wh_combo.currentIndexChanged.connect(self._load_stock)
        top_row.addWidget(self.wh_combo)

        new_wh_btn = SecondaryButton("➕  New")
        new_wh_btn.setToolTip("Create a warehouse (for a project or general)")
        new_wh_btn.clicked.connect(self._new_warehouse)
        top_row.addWidget(new_wh_btn)

        rename_wh_btn = SecondaryButton("✏️  Rename")
        rename_wh_btn.clicked.connect(self._rename_warehouse)
        top_row.addWidget(rename_wh_btn)
        top_row.addStretch()

        self.alert_label = QLabel("")
        self.alert_label.setStyleSheet("color:#C62828;font-weight:bold;")
        top_row.addWidget(self.alert_label)

        refresh_btn = SecondaryButton("↻  Refresh")
        refresh_btn.clicked.connect(self.refresh)
        top_row.addWidget(refresh_btn)

        tpl_btn = SecondaryButton("📋  Download Template")
        tpl_btn.setToolTip("Blank Excel sheet for a stock count")
        tpl_btn.clicked.connect(self._download_template)
        top_row.addWidget(tpl_btn)

        import_btn = SecondaryButton("📂  Import from Excel")
        import_btn.setToolTip("Read a SAP stock export or a filled-in template "
                              "into the selected warehouse (shows a preview first)")
        import_btn.clicked.connect(self._import_excel)
        top_row.addWidget(import_btn)

        export_btn = SecondaryButton("📥  Export All")
        export_btn.clicked.connect(self._export)
        top_row.addWidget(export_btn)
        layout.addLayout(top_row)

        # Edit stock group
        edit_group = QGroupBox("Set / Update Stock Level")
        ef = QHBoxLayout()
        ef.addWidget(QLabel("Material:"))
        self.edit_mat = QComboBox()
        self.edit_mat.setMinimumWidth(180)
        self.edit_mat.setEditable(True)
        ef.addWidget(self.edit_mat)
        ef.addWidget(QLabel("Qty:"))
        self.edit_qty = QDoubleSpinBox()
        self.edit_qty.setRange(0, 999999)
        self.edit_qty.setDecimals(1)
        ef.addWidget(self.edit_qty)
        ef.addWidget(QLabel("Min Qty:"))
        self.edit_min = QDoubleSpinBox()
        self.edit_min.setRange(0, 999999)
        self.edit_min.setDecimals(1)
        ef.addWidget(self.edit_min)
        ef.addWidget(QLabel("Unit:"))
        self.edit_unit = QLineEdit()
        self.edit_unit.setFixedWidth(60)
        ef.addWidget(self.edit_unit)
        set_btn = PrimaryButton("💾  Set")
        set_btn.clicked.connect(self._set_stock)
        ef.addWidget(set_btn)
        ef.addStretch()
        edit_group.setLayout(ef)
        layout.addWidget(edit_group)

        self.stock_table = make_table([
            "Material #","Description","Qty","Min Qty","Unit","Status"
        ])
        layout.addWidget(self.stock_table)

    def refresh(self):
        warehouses = get_all_warehouses()
        self.wh_combo.blockSignals(True)
        self.wh_combo.clear()
        for w in warehouses:
            label = ("🏭  " if w["is_main"] else "📦  ") + w["name"]
            self.wh_combo.addItem(label, userData=w["id"])
        self.wh_combo.blockSignals(False)

        # Update material dropdown
        self.edit_mat.clear()
        for mat in get_material_numbers():
            self.edit_mat.addItem(mat)

        # Low stock alert
        low = get_low_stock_items()
        if low:
            self.alert_label.setText(f"⚠️  {len(low)} item(s) below minimum stock!")
        else:
            self.alert_label.setText("")

        self._load_stock()

    def _load_stock(self):
        wh_id = self.wh_combo.currentData()
        if not wh_id:
            return
        items = get_stock(wh_id)
        self.stock_table.setRowCount(0)
        for item in items:
            row = self.stock_table.rowCount()
            self.stock_table.insertRow(row)
            is_low = item.get("low_stock", 0)
            bg = QColor("#FFEBEE") if is_low else QColor("#FFFFFF")
            status = "⚠️ Low" if is_low else "OK"
            for col, val in enumerate([
                item["material_number"],
                item.get("description",""),
                str(item["quantity"]),
                str(item["min_quantity"]),
                item.get("unit",""),
                status,
            ]):
                cell = QTableWidgetItem(val)
                cell.setTextAlignment(Qt.AlignCenter)
                cell.setBackground(bg)
                self.stock_table.setItem(row, col, cell)

    def _set_stock(self):
        wh_id = self.wh_combo.currentData()
        mat   = self.edit_mat.currentText().strip().upper()
        if not wh_id or not mat:
            QMessageBox.warning(self,"Required","Select warehouse and material.")
            return
        set_stock_level(wh_id, mat, self.edit_qty.value(),
                        self.edit_min.value(), self.edit_unit.text().strip())
        self._load_stock()

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(
            self,"Export Stock","stock_report.xlsx","Excel Files (*.xlsx)")
        if path:
            try:
                export_stock_excel(path)
                QMessageBox.information(self,"Exported",f"Saved to:\n{path}")
            except Exception as e:
                QMessageBox.critical(self,"Error",str(e))

    # ── Excel import ──────────────────────────────────────────────────────

    def _warehouse_label(self) -> str:
        return (self.wh_combo.currentText()
                .replace("🏭", "").replace("📦", "").strip())

    def _download_template(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Stock Count Template",
            "stock_count_template.xlsx", "Excel Files (*.xlsx)")
        if not path:
            return
        try:
            stock_excel.write_template(path)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        QMessageBox.information(
            self, "Template Saved",
            f"Template saved to:\n{path}\n\n"
            "Fill in one row per material — Qty is how many are on the shelf "
            "now — then use 'Import from Excel'.")

    def _import_excel(self):
        wh_id = self.wh_combo.currentData()
        if not wh_id:
            QMessageBox.warning(self, "No warehouse",
                                "Select a warehouse to import into first.")
            return

        path, _ = QFileDialog.getOpenFileName(
            self, "Import Stock Count from Excel", "",
            "Excel Files (*.xlsx *.xlsm)")
        if not path:
            return

        try:
            parsed = stock_excel.parse(path)
        except ValueError as e:
            QMessageBox.critical(self, "Import Failed", str(e))
            return
        except Exception as e:
            QMessageBox.critical(self, "Import Failed",
                                 f"Could not read this file:\n{e}")
            return

        if not parsed["rows"]:
            msg = ("No stock rows were found in this file.\n\n"
                   f"Read sheet '{parsed['sheet']}', header on row "
                   f"{parsed['header_row']}, but every row below it was empty "
                   "or unusable.")
            if parsed["problems"]:
                msg += "\n\n" + _problems_text(parsed["problems"])
            QMessageBox.information(self, "Nothing to Import", msg)
            return

        plan_rows = stock_excel.plan(wh_id, parsed["rows"])
        dlg = StockImportDialog(self._warehouse_label(), parsed, plan_rows,
                                parent=self)
        if dlg.exec_() != QDialog.Accepted:
            return

        chosen = dlg.selected_rows()
        if not chosen:
            QMessageBox.information(self, "Nothing Selected",
                                    "No rows were ticked — nothing was imported.")
            return

        try:
            counts = stock_excel.apply(
                wh_id, chosen, reference=f"IMPORT {parsed['file']}"[:90])
        except Exception as e:
            QMessageBox.critical(self, "Import Failed", str(e))
            return

        self.refresh()
        thresholds = (f"  ⚠️  {counts['thresholds_set']} low-stock threshold(s) set\n"
                      if counts["thresholds_set"]
                      else "  ⚠️  no low-stock threshold was changed\n")
        QMessageBox.information(
            self, "Import Result",
            f"Imported into {self._warehouse_label()}:\n"
            f"  ✅  {counts['created']} new material(s) added\n"
            f"  🔄  {counts['updated']} row(s) updated\n"
            f"  ⏭  {counts['unchanged']} already matched\n"
            f"  📋  {counts['transactions']} stock transaction(s) recorded\n"
            f"        (+{counts['quantity_in']:g} IN / -{counts['quantity_out']:g} OUT)\n"
            + thresholds +
            f"  📖  {counts['materials_created']} catalogue entry(ies) created, "
            f"{counts['materials_updated']} updated")

    def _new_warehouse(self):
        dlg = WarehouseDialog(parent=self)
        if dlg.exec_() == QDialog.Accepted:
            name, project_id, notes = dlg.get_data()
            try:
                wh_id = create_warehouse(name, project_id, notes)
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))
                return
            self.refresh()
            idx = self.wh_combo.findData(wh_id)
            if idx >= 0:
                self.wh_combo.setCurrentIndex(idx)

    def _rename_warehouse(self):
        wh_id = self.wh_combo.currentData()
        if not wh_id:
            return
        current = self.wh_combo.currentText().replace("🏭", "").replace("📦", "").strip()
        new_name, ok = QInputDialog.getText(
            self, "Rename Warehouse", "New name:", text=current)
        if ok and new_name.strip():
            rename_warehouse(wh_id, new_name)
            self.refresh()
            idx = self.wh_combo.findData(wh_id)
            if idx >= 0:
                self.wh_combo.setCurrentIndex(idx)


# ── NEW WAREHOUSE DIALOG ──────────────────────────────────────────────────────

class WarehouseDialog(QDialog):
    """Create a warehouse, optionally bound to a project."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Warehouse")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)

        self.proj_combo = QComboBox()
        self.proj_combo.addItem("— General (no project) —", userData=None)
        for p in get_all_projects():
            self.proj_combo.addItem(p.name, userData=p.id)
        self.proj_combo.currentIndexChanged.connect(self._suggest_name)
        form.addRow("Project:", self.proj_combo)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g. Bukhara — Site Container")
        form.addRow("Name *:", self.name_input)

        self.notes_input = QLineEdit()
        self.notes_input.setPlaceholderText("Optional notes...")
        form.addRow("Notes:", self.notes_input)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _suggest_name(self):
        """Prefills the name from the selected project if not typed yet."""
        if self.name_input.text().strip():
            return
        pid = self.proj_combo.currentData()
        if pid is not None:
            self.name_input.setText(f"{self.proj_combo.currentText()} — Stock")

    def _validate_and_accept(self):
        if not self.name_input.text().strip():
            QMessageBox.warning(self, "Required", "Warehouse name is required.")
            return
        self.accept()

    def get_data(self):
        return (self.name_input.text().strip(),
                self.proj_combo.currentData(),
                self.notes_input.text().strip())


# ── IMPORT PREVIEW DIALOG ─────────────────────────────────────────────────────

def _problems_text(problems: list, limit: int = 12) -> str:
    """The parser's complaints as readable lines."""
    lines = []
    for p in problems[:limit]:
        who = f" {p['material_number']}" if p.get("material_number") else ""
        lines.append(f"Row {p['row']}{who}: {p['message']}")
    if len(problems) > limit:
        lines.append(f"...and {len(problems) - limit} more")
    return "\n".join(lines)


class StockImportDialog(QDialog):
    """Shows what an Excel stock count would change — before anything is written.

    This preview is the whole safety of the import: it names the warehouse being
    written into and the plant/location the file itself claims, so a file for the
    wrong site is caught by eye, and every row can be excluded on its own.
    Nothing here writes; the caller applies `selected_rows()` on Accepted.
    """

    STATUS_COLORS = {"new": "#E8F5E9", "changed": "#FFF8E1", "unchanged": "#FFFFFF"}
    STATUS_LABELS = {"new": "➕ New", "changed": "🔄 Changed", "unchanged": "= Unchanged"}

    def __init__(self, warehouse_label: str, parsed: dict, plan_rows: list,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import Stock Count — Preview")
        self.setMinimumSize(900, 600)
        self._plan = plan_rows
        self._parsed = parsed
        self._build_ui(warehouse_label, parsed, plan_rows)

    def _build_ui(self, warehouse_label: str, parsed: dict, plan_rows: list):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # ── what is being written where ───────────────────────────────────
        info = QGroupBox("Check this before importing")
        form = QFormLayout()
        form.setSpacing(6)

        target = QLabel(f"<b>{warehouse_label}</b>")
        form.addRow("Write into:", target)

        layout_name = "SAP stock export" if parsed["layout"] == "sap" \
            else "app export / template"
        form.addRow("File:", QLabel(
            f"{parsed['file']}  —  sheet '{parsed['sheet']}', {layout_name}, "
            f"header on row {parsed['header_row']}"))

        said = []
        if parsed["plants"]:
            said.append("Plant " + ", ".join(parsed["plants"]))
        if parsed["locations"]:
            said.append("Location " + ", ".join(parsed["locations"]))
        if parsed["warehouses"]:
            said.append("Warehouse " + ", ".join(parsed["warehouses"]))
        self.source_label = QLabel(" · ".join(said) if said
                                   else "— the file does not name a site —")
        self.source_label.setStyleSheet("color:#1565C0;font-weight:bold;")
        form.addRow("File says:", self.source_label)

        info.setLayout(form)
        layout.addWidget(info)

        # ── notes and problems ────────────────────────────────────────────
        notes = []
        if parsed["has_min_column"]:
            notes.append(
                "ℹ️  This file has a Min Qty column, so the low-stock thresholds "
                "in it will be applied. A row with an empty Min Qty cell keeps "
                "the threshold already set in the app.")
        else:
            notes.append(
                "ℹ️  This file has no Min Qty column, so every low-stock "
                "threshold in this warehouse stays exactly as it is.")
        if parsed["ignored_columns"]:
            notes.append("Ignored column(s): "
                         + ", ".join(parsed["ignored_columns"]) + ".")
        note = QLabel("  ".join(notes))
        note.setWordWrap(True)
        note.setStyleSheet("color:#455A64;")
        layout.addWidget(note)

        if parsed["problems"]:
            warn = QLabel(f"⚠️  {len(parsed['problems'])} row(s) could not be "
                          "read and are not listed below:")
            warn.setStyleSheet("color:#C62828;font-weight:bold;")
            layout.addWidget(warn)
            box = QTextEdit()
            box.setReadOnly(True)
            box.setMaximumHeight(80)
            box.setPlainText(_problems_text(parsed["problems"]))
            layout.addWidget(box)

        # ── summary ───────────────────────────────────────────────────────
        s = stock_excel.summarise(plan_rows)
        self.summary_label = QLabel()
        self.summary_label.setStyleSheet("font-weight:bold;")
        layout.addWidget(self.summary_label)

        if s["new"] == 0 and s["changed"] == 0:
            calm = QLabel(
                f"All {s['unchanged']} material(s) in this file already match "
                f"{warehouse_label} — no quantity and no threshold would move. "
                "Importing anyway only fills in descriptions and units.")
            calm.setWordWrap(True)
            calm.setStyleSheet("color:#2E7D32;")
            layout.addWidget(calm)

        # ── the rows ──────────────────────────────────────────────────────
        self.table = make_table([
            "", "Material #", "Description", "Unit",
            "Now", "In file", "Change", "Min Qty", "Status"
        ])
        self.table.setColumnWidth(0, 28)
        self.table.setColumnWidth(1, 130)
        self.table.setColumnWidth(2, 240)
        for row_index, p in enumerate(plan_rows):
            self.table.insertRow(row_index)
            tick = QTableWidgetItem()
            tick.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled |
                          Qt.ItemIsSelectable)
            tick.setCheckState(Qt.Checked)
            self.table.setItem(row_index, 0, tick)

            delta = p["delta"]
            change = f"{delta:+g}" if abs(delta) > 0 else "—"
            # A threshold change reads like a quantity change: before → after.
            if p["min_changed"]:
                min_text = f"{p['min_before']:g} → {p['min_after']:g}"
            elif p["min_after"]:
                min_text = f"{p['min_after']:g}"
            else:
                min_text = "—"
            bg = QColor(self.STATUS_COLORS.get(p["status"], "#FFFFFF"))
            for col, value in enumerate([
                p["material_number"],
                p["description"] or p.get("current_description", ""),
                p["unit"],
                "—" if p["status"] == "new" else f"{p['before']:g}",
                f"{p['after']:g}",
                change,
                min_text,
                self.STATUS_LABELS.get(p["status"], p["status"]),
            ], start=1):
                cell = QTableWidgetItem(str(value))
                cell.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter if col <= 3
                                      else Qt.AlignCenter)
                cell.setBackground(bg)
                self.table.setItem(row_index, col, cell)
        self.table.itemChanged.connect(lambda *_: self._refresh_counts())
        layout.addWidget(self.table)

        # ── selection + confirm ───────────────────────────────────────────
        btn_row = QHBoxLayout()
        for label, state in (("Select all", Qt.Checked),
                             ("Select none", Qt.Unchecked)):
            btn = SecondaryButton(label)
            btn.clicked.connect(lambda _=False, st=state: self._set_all(st))
            btn_row.addWidget(btn)
        only_changes = SecondaryButton("Only new / changed")
        only_changes.clicked.connect(self._select_changes)
        btn_row.addWidget(only_changes)
        btn_row.addStretch()

        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.apply_btn = self.buttons.addButton("✅  Import",
                                                QDialogButtonBox.AcceptRole)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        btn_row.addWidget(self.buttons)
        layout.addLayout(btn_row)

        self._refresh_counts()

    # ── selection ─────────────────────────────────────────────────────────

    def _set_all(self, state):
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(state)
        self.table.blockSignals(False)
        self._refresh_counts()

    def _select_changes(self):
        self.table.blockSignals(True)
        for row, p in enumerate(self._plan):
            self.table.item(row, 0).setCheckState(
                Qt.Unchecked if p["status"] == "unchanged" else Qt.Checked)
        self.table.blockSignals(False)
        self._refresh_counts()

    def _refresh_counts(self):
        chosen = self.selected_rows()
        s = stock_excel.summarise(chosen)
        text = (f"{s['total']} of {len(self._plan)} row(s) selected:  "
                f"{s['new']} new · {s['changed']} changed · "
                f"{s['unchanged']} unchanged")
        if s["thresholds"]:
            text += f" · {s['thresholds']} threshold(s) to set"
        self.summary_label.setText(text)
        self.apply_btn.setText(f"✅  Import {s['total']} row(s)")
        self.apply_btn.setEnabled(bool(chosen))

    def selected_rows(self) -> list:
        """The plan rows that are still ticked."""
        return [p for row, p in enumerate(self._plan)
                if self.table.item(row, 0).checkState() == Qt.Checked]


# ── TRANSACTION TAB ───────────────────────────────────────────────────────────

class TransactionTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)

        group = QGroupBox("Record Stock Movement")
        form  = QFormLayout()
        form.setSpacing(10)

        self.wh_combo = QComboBox()
        self.wh_combo.setMinimumWidth(220)
        form.addRow("Warehouse *:", self.wh_combo)

        self.tx_type = QComboBox()
        self.tx_type.addItems(TRANSACTION_TYPES)
        self.tx_type.currentTextChanged.connect(self._on_type_changed)
        form.addRow("Type *:", self.tx_type)

        # Destination warehouse (shown only for TRANSFER)
        self.dest_wh_label = QLabel("Destination *:")
        self.dest_wh_combo = QComboBox()
        self.dest_wh_combo.setMinimumWidth(220)
        form.addRow(self.dest_wh_label, self.dest_wh_combo)
        self.dest_wh_label.setVisible(False)
        self.dest_wh_combo.setVisible(False)

        self.mat_combo = QComboBox()
        self.mat_combo.setEditable(True)
        self.mat_combo.setMinimumWidth(220)
        self.mat_combo.currentTextChanged.connect(self._on_mat_changed)
        form.addRow("Material *:", self.mat_combo)

        self.mat_desc = QLabel("—")
        self.mat_desc.setStyleSheet("color:#2E7D32;font-style:italic;")
        form.addRow("Description:", self.mat_desc)

        self.qty_spin = QDoubleSpinBox()
        self.qty_spin.setRange(0.01, 999999)
        self.qty_spin.setDecimals(2)
        self.qty_spin.setValue(1.0)
        form.addRow("Quantity *:", self.qty_spin)

        self.proj_combo = QComboBox()
        self.proj_combo.addItem("— No project —", userData=None)
        form.addRow("Project (optional):", self.proj_combo)

        self.date_edit = QDateEdit()
        self.date_edit.setDate(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        form.addRow("Date *:", self.date_edit)

        self.ref_input = QLineEdit()
        self.ref_input.setPlaceholderText("e.g. WO-001 or log reference")
        form.addRow("Reference:", self.ref_input)

        self.notes_input = QTextEdit()
        self.notes_input.setMaximumHeight(55)
        form.addRow("Notes:", self.notes_input)

        group.setLayout(form)
        layout.addWidget(group)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        clear_btn = SecondaryButton("🔄  Clear")
        clear_btn.clicked.connect(self._clear)
        btn_row.addWidget(clear_btn)
        save_btn = PrimaryButton("✅  Record Transaction")
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)
        layout.addStretch()

    def refresh(self):
        warehouses = get_all_warehouses()
        for combo in [self.wh_combo, self.dest_wh_combo]:
            combo.clear()
            for w in warehouses:
                label = ("🏭 " if w["is_main"] else "📦 ") + w["name"]
                combo.addItem(label, userData=w["id"])
        self.mat_combo.clear()
        for mat in get_material_numbers():
            self.mat_combo.addItem(mat)
        self.proj_combo.clear()
        self.proj_combo.addItem("— No project —", userData=None)
        for p in get_all_projects():
            self.proj_combo.addItem(p.name, userData=p.id)

    def _on_type_changed(self, tx_type):
        is_transfer = tx_type == "TRANSFER"
        self.dest_wh_label.setVisible(is_transfer)
        self.dest_wh_combo.setVisible(is_transfer)

    def _on_mat_changed(self, mat_num):
        mat = get_material(mat_num.strip().upper())
        if mat and mat.get("description"):
            self.mat_desc.setText(mat["description"])
        else:
            self.mat_desc.setText("—")

    def _save(self):
        wh_id = self.wh_combo.currentData()
        mat   = self.mat_combo.currentText().strip().upper()
        if not wh_id or not mat:
            QMessageBox.warning(self,"Required","Warehouse and material required.")
            return
        tx_type = self.tx_type.currentText()
        dest_id = self.dest_wh_combo.currentData() if tx_type=="TRANSFER" else None
        if tx_type == "TRANSFER" and (not dest_id or dest_id == wh_id):
            QMessageBox.warning(self,"Transfer","Select a different destination warehouse.")
            return
        try:
            record_transaction(
                warehouse_id      = wh_id,
                material_number   = mat,
                transaction_type  = tx_type,
                quantity          = self.qty_spin.value(),
                transaction_date  = self.date_edit.date().toString("yyyy-MM-dd"),
                project_id        = self.proj_combo.currentData(),
                reference         = self.ref_input.text().strip(),
                notes             = self.notes_input.toPlainText().strip(),
                dest_warehouse_id = dest_id,
            )
            QMessageBox.information(self,"Saved",
                f"{tx_type} of {self.qty_spin.value()} x {mat} recorded.")
            self._clear()
        except Exception as e:
            QMessageBox.critical(self,"Error",str(e))

    def _clear(self):
        self.qty_spin.setValue(1.0)
        self.ref_input.clear()
        self.notes_input.clear()
        self.date_edit.setDate(QDate.currentDate())


# ── HISTORY TAB ───────────────────────────────────────────────────────────────

class StockHistoryTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)

        top = QHBoxLayout()
        top.addWidget(QLabel("Warehouse:"))
        self.wh_filter = QComboBox()
        self.wh_filter.setMinimumWidth(200)
        top.addWidget(self.wh_filter)
        top.addStretch()
        load_btn = PrimaryButton("🔍  Load")
        load_btn.clicked.connect(self.refresh)
        top.addWidget(load_btn)
        layout.addLayout(top)

        self.hist_table = make_table([
            "Date","Warehouse","Material #","Description",
            "Type","Qty","Project","Reference","Notes"
        ])
        layout.addWidget(self.hist_table)

    def refresh(self):
        self.wh_filter.clear()
        self.wh_filter.addItem("All Warehouses", userData=None)
        for w in get_all_warehouses():
            self.wh_filter.addItem(w["name"], userData=w["id"])

        wh_id = self.wh_filter.currentData()
        txs   = get_transactions(warehouse_id=wh_id, limit=300)
        self.hist_table.setRowCount(0)

        type_colors = {"IN":"#D4EDDA","OUT":"#F8D7DA","TRANSFER":"#E3F2FD"}
        for tx in txs:
            row = self.hist_table.rowCount()
            self.hist_table.insertRow(row)
            bg = QColor(type_colors.get(tx.get("transaction_type",""),"#FFFFFF"))
            for col, val in enumerate([
                tx.get("date",""), tx.get("warehouse",""),
                tx.get("material_number",""), tx.get("description",""),
                tx.get("transaction_type",""), str(tx.get("quantity","")),
                tx.get("project",""), tx.get("reference",""),
                tx.get("notes",""),
            ]):
                item = QTableWidgetItem(str(val) if val else "")
                item.setTextAlignment(Qt.AlignCenter)
                item.setBackground(bg)
                self.hist_table.setItem(row, col, item)


# ── CONSUMPTION HISTORY (the old Daily Log) ───────────────────────────────────

class ConsumptionHistoryTab(QWidget):
    """What was issued from stock in the old Daily Log, newest first.

    Read-only. These rows are also in the Work list (same query, in
    services/log_service.consumption_history) — there as the work, here as the
    material. Nothing on this tab writes anything: no row can be edited or
    deleted, and no stock transaction is created or reversed. Whatever these
    rows took out of a warehouse was booked at the time they were written.
    """

    COLUMNS = ["Date", "Material #", "Description", "Qty", "Block", "Node",
               "Warehouse", "SAP ticket", "Comment"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pid = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)

        note = QLabel(
            "Materials issued in the old Daily Log — read-only history. Each row "
            "is one material against one block, with what was done written next to "
            "it. The same rows are on the Work page. Internal: materials "
            "consumption is not part of the customer's monthly report.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#6B4E00;background:#FFF4D6;border:1px solid #E0C97F;"
                           "border-radius:4px;padding:6px 8px;")
        layout.addWidget(note)

        top = QHBoxLayout()
        top.addWidget(QLabel("Project:"))
        self.proj_filter = QComboBox()
        self.proj_filter.setMinimumWidth(220)
        top.addWidget(self.proj_filter)
        top.addWidget(QLabel("Warehouse:"))
        self.wh_filter = QComboBox()
        self.wh_filter.setMinimumWidth(180)
        top.addWidget(self.wh_filter)
        top.addWidget(QLabel("Find:"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("material, description, comment or SAP")
        self.search.setMinimumWidth(200)
        top.addWidget(self.search)
        top.addStretch()
        load_btn = PrimaryButton("🔍  Load")
        load_btn.clicked.connect(self._load)
        top.addWidget(load_btn)
        layout.addLayout(top)

        self.cons_table = make_table(self.COLUMNS)
        layout.addWidget(self.cons_table)

        self.foot = QLabel("")
        self.foot.setStyleSheet("color:#6B7A8D;")
        layout.addWidget(self.foot)

        self.search.returnPressed.connect(self._load)
        self.proj_filter.currentIndexChanged.connect(self._load)
        self.wh_filter.currentIndexChanged.connect(self._load)

    # ── shell hook ───────────────────────────────────────────────────────
    def set_current_project(self, pid):
        self._pid = pid
        self.refresh()

    def refresh(self):
        """Rebuild the choosers (a project or warehouse may be new), keeping
        what is selected, then load."""
        for combo, items, blank in (
                (self.proj_filter, [(p.name, p.id) for p in get_all_projects()], None),
                (self.wh_filter, [(w["name"], w["id"]) for w in get_all_warehouses()],
                 ("All warehouses", None))):
            want = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            if blank:
                combo.addItem(blank[0], userData=blank[1])
            for label, data in items:
                combo.addItem(label, userData=data)
            if combo is self.proj_filter and self._pid is not None:
                want = self._pid
            i = combo.findData(want)
            combo.setCurrentIndex(i if i >= 0 else 0)
            combo.blockSignals(False)
        self._load()

    def _load(self):
        from services.log_service import consumption_history
        self.cons_table.setRowCount(0)
        pid = self.proj_filter.currentData()
        if pid is None:
            self.foot.setText("Open a project to see its consumption history.")
            return
        rows = consumption_history(
            pid, warehouse_id=self.wh_filter.currentData(),
            text=self.search.text().strip() or None)
        for r in rows:
            i = self.cons_table.rowCount()
            self.cons_table.insertRow(i)
            qty = r.get("quantity")
            try:
                qty_txt = f"{float(qty):g}" if qty is not None else ""
            except (TypeError, ValueError):
                qty_txt = str(qty or "")
            blk = r.get("plant_block")
            cells = [r.get("date", ""), r.get("material_number", ""),
                     r.get("description", ""), qty_txt,
                     str(blk) if blk else "—", r.get("device", ""),
                     r.get("warehouse", ""), r.get("sap_ticket", ""),
                     r.get("comment", "")]
            for col, val in enumerate(cells):
                item = QTableWidgetItem(str(val) if val else "")
                if col != len(cells) - 1:
                    item.setTextAlignment(Qt.AlignCenter)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.cons_table.setItem(i, col, item)
        self.foot.setText(
            f"{len(rows)} row(s) · newest first · read-only" if rows else
            "No Daily Log rows for this filter.")
