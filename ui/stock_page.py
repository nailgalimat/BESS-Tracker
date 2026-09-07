"""
ui/stock_page.py
-----------------
Spare Parts Stock Management.

Three inner tabs:
  1. Stock View   — see current levels per warehouse, low-stock alerts
  2. Transaction  — record IN / OUT / TRANSFER
  3. History      — audit trail of all movements
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

        self.tabs.currentChanged.connect(self._on_tab)

    def _on_tab(self, idx):
        if idx == 0: self.stock_tab.refresh()
        elif idx == 1: self.tx_tab.refresh()
        elif idx == 2: self.hist_tab.refresh()

    def refresh_projects(self):
        self.tx_tab.refresh()
        self.hist_tab.refresh()


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
