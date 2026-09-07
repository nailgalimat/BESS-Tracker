"""
ui/daily_log_form.py
---------------------
The main daily input form for recording maintenance activities.

Key feature: Smart cascading dropdowns.
  Zone → Block → Container (auto-fills Type and Serial Number)

The user only needs to select location + enter material/quantity/comment.
Everything else is auto-filled from the project configuration.
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QComboBox, QDateEdit, QLineEdit, QDoubleSpinBox,
    QTextEdit, QPushButton, QMessageBox, QGroupBox, QFrame
)
from PyQt5.QtCore import Qt, QDate
from PyQt5.QtGui import QFont

from models.models import LogEntry
from services.project_service import (
    get_all_projects, get_zones_for_project,
    get_blocks_for_zone, get_containers_for_block
)
from services.log_service import save_log_entry
from services.material_service import get_material_numbers, get_material
from services.stock_service import (
    get_all_warehouses, get_stock_quantity, ensure_project_warehouse
)
from PyQt5.QtWidgets import QCompleter


class DailyLogForm(QWidget):
    """Form widget for recording daily maintenance activities."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_containers = []  # Container objects for selected block
        self._warehouses = []          # warehouse dicts for default selection
        self._build_ui()
        self._load_warehouses()
        self._load_projects()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(12)

        # ── Title ────────────────────────────────────────────────────────
        title = QLabel("📋  Daily Maintenance Log")
        title.setFont(QFont("Segoe UI", 14, QFont.Bold))
        main_layout.addWidget(title)

        # ── Row 1: Project + Date ────────────────────────────────────────
        top_group = QGroupBox("Project & Date")
        top_form = QFormLayout()

        self.project_combo = QComboBox()
        self.project_combo.setMinimumWidth(250)
        self.project_combo.currentIndexChanged.connect(self._on_project_changed)
        top_form.addRow("Project *:", self.project_combo)

        self.date_edit = QDateEdit()
        self.date_edit.setDate(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        top_form.addRow("Date *:", self.date_edit)

        top_group.setLayout(top_form)
        main_layout.addWidget(top_group)

        # ── Row 2: Location (cascading dropdowns) ───────────────────────
        loc_group = QGroupBox("Location  (select Zone → Block → Container)")
        loc_layout = QHBoxLayout()

        zone_col = QVBoxLayout()
        zone_col.addWidget(QLabel("Zone *:"))
        self.zone_combo = QComboBox()
        self.zone_combo.currentIndexChanged.connect(self._on_zone_changed)
        zone_col.addWidget(self.zone_combo)
        loc_layout.addLayout(zone_col)

        block_col = QVBoxLayout()
        block_col.addWidget(QLabel("Block *:"))
        self.block_combo = QComboBox()
        self.block_combo.currentIndexChanged.connect(self._on_block_changed)
        block_col.addWidget(self.block_combo)
        loc_layout.addLayout(block_col)

        cont_col = QVBoxLayout()
        cont_col.addWidget(QLabel("Container *:"))
        self.container_combo = QComboBox()
        self.container_combo.currentIndexChanged.connect(self._on_container_changed)
        cont_col.addWidget(self.container_combo)
        loc_layout.addLayout(cont_col)

        loc_group.setLayout(loc_layout)
        main_layout.addWidget(loc_group)

        # ── Row 3: Auto-filled fields (read-only) ───────────────────────
        auto_group = QGroupBox("Auto-filled Information")
        auto_form = QFormLayout()

        self.type_label = QLabel("—")
        self.type_label.setStyleSheet("color: #1565C0; font-weight: bold;")
        auto_form.addRow("Container Type:", self.type_label)

        self.serial_label = QLabel("—")
        self.serial_label.setStyleSheet("color: #1565C0; font-weight: bold;")
        auto_form.addRow("Serial Number:", self.serial_label)

        auto_group.setLayout(auto_form)
        main_layout.addWidget(auto_group)

        # ── Row 4: Activity details ──────────────────────────────────────
        activity_group = QGroupBox("Activity Details")
        activity_form = QFormLayout()

        self.material_input = QLineEdit()
        self.material_input.setPlaceholderText("e.g. FUSE-250A  (type to search catalog)")
        self.material_input.textChanged.connect(self._on_material_changed)
        activity_form.addRow("Material Number *:", self.material_input)

        # Auto-filled description from materials catalog
        self.material_desc_label = QLabel("—")
        self.material_desc_label.setStyleSheet(
            "color: #2E7D32; font-weight: bold; font-style: italic;"
        )
        self.material_desc_label.setWordWrap(True)
        activity_form.addRow("Description:", self.material_desc_label)

        self.quantity_spin = QDoubleSpinBox()
        self.quantity_spin.setRange(0.01, 99999)
        self.quantity_spin.setDecimals(2)
        self.quantity_spin.setValue(1.0)
        self.quantity_spin.valueChanged.connect(
            lambda _: self._update_stock_label())
        activity_form.addRow("Quantity *:", self.quantity_spin)

        # Warehouse to consume stock from (None = no deduction)
        self.warehouse_combo = QComboBox()
        self.warehouse_combo.setMinimumWidth(250)
        self.warehouse_combo.currentIndexChanged.connect(self._update_stock_label)
        activity_form.addRow("Consume from:", self.warehouse_combo)

        # Live stock availability for selected material + warehouse
        self.stock_label = QLabel("—")
        self.stock_label.setStyleSheet("color: #999; font-style: italic;")
        activity_form.addRow("Stock available:", self.stock_label)

        self.comment_input = QTextEdit()
        self.comment_input.setMaximumHeight(80)
        self.comment_input.setPlaceholderText("Optional: describe the work done, observations, etc.")
        activity_form.addRow("Comment:", self.comment_input)

        activity_group.setLayout(activity_form)
        main_layout.addWidget(activity_group)

        # Set up material autocomplete (loaded from catalog)
        self._refresh_material_autocomplete()

        # ── Save button ──────────────────────────────────────────────────
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.clear_btn = QPushButton("🔄  Clear Form")
        self.clear_btn.clicked.connect(self._clear_form)
        btn_layout.addWidget(self.clear_btn)

        self.save_btn = QPushButton("✅  Save Log Entry")
        self.save_btn.setFixedHeight(42)
        self.save_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50; color: white;
                font-weight: bold; border-radius: 4px; padding: 0 20px;
            }
            QPushButton:hover { background-color: #388E3C; }
        """)
        self.save_btn.clicked.connect(self._save_entry)
        btn_layout.addWidget(self.save_btn)

        main_layout.addLayout(btn_layout)
        main_layout.addStretch()

    # ── Data loading helpers ─────────────────────────────────────────────

    def _load_projects(self):
        """Populates the project dropdown on startup."""
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        self.project_combo.addItem("— Select Project —", userData=None)

        projects = get_all_projects()
        for p in projects:
            self.project_combo.addItem(p.name, userData=p.id)

        self.project_combo.blockSignals(False)
        self._on_project_changed()

    def refresh_projects(self):
        """Called from main window when a new project is created."""
        self._load_warehouses()
        self._load_projects()

    def refresh_materials(self):
        """Called from main window when materials catalog is updated."""
        self._refresh_material_autocomplete()

    # ── Warehouse helpers ────────────────────────────────────────────────

    def _load_warehouses(self):
        """Populates the warehouse dropdown (no deduction + all warehouses)."""
        self._warehouses = get_all_warehouses()
        self.warehouse_combo.blockSignals(True)
        self.warehouse_combo.clear()
        self.warehouse_combo.addItem("— No stock deduction —", userData=None)
        for w in self._warehouses:
            label = ("🏭  " if w["is_main"] else "📦  ") + w["name"]
            self.warehouse_combo.addItem(label, userData=w["id"])
        self.warehouse_combo.blockSignals(False)
        self._update_stock_label()

    def _select_default_warehouse(self, project_id):
        """Auto-selects the project's own warehouse for the chosen project."""
        if project_id is None:
            self.warehouse_combo.setCurrentIndex(0)
            return
        wh = next((w for w in self._warehouses
                   if w.get("project_id") == project_id), None)
        if wh is None:
            # Project has no warehouse yet — create it and reload
            name = self.project_combo.currentText()
            ensure_project_warehouse(project_id, name)
            self._load_warehouses()
            wh = next((w for w in self._warehouses
                       if w.get("project_id") == project_id), None)
        if wh:
            idx = self.warehouse_combo.findData(wh["id"])
            if idx >= 0:
                self.warehouse_combo.setCurrentIndex(idx)

    def _update_stock_label(self):
        """Shows available stock for the typed material in the chosen warehouse."""
        wh_id = self.warehouse_combo.currentData()
        mat   = self.material_input.text().strip().upper()
        if not wh_id:
            self.stock_label.setText("—  (no deduction)")
            self.stock_label.setStyleSheet("color: #999; font-style: italic;")
            return
        if not mat:
            self.stock_label.setText("—")
            self.stock_label.setStyleSheet("color: #999; font-style: italic;")
            return
        stock = get_stock_quantity(wh_id, mat)
        if stock is None:
            self.stock_label.setText("Not stocked in this warehouse")
            self.stock_label.setStyleSheet("color: #E65100; font-weight: bold;")
        else:
            qty  = stock["quantity"]
            unit = stock.get("unit") or ""
            text = f"{qty:g} {unit}".strip() + " in stock"
            if qty < self.quantity_spin.value():
                self.stock_label.setText(text + "  ⚠️ insufficient")
                self.stock_label.setStyleSheet("color: #C62828; font-weight: bold;")
            else:
                self.stock_label.setText(text)
                self.stock_label.setStyleSheet("color: #2E7D32; font-weight: bold;")

    def _refresh_material_autocomplete(self):
        """Loads material numbers from catalog and sets up autocomplete."""
        numbers = get_material_numbers()
        completer = QCompleter(numbers, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.material_input.setCompleter(completer)

    def _on_material_changed(self, text: str):
        """Auto-fills description when a known material number is typed."""
        mat = get_material(text.strip().upper())
        if mat and mat.get("description"):
            desc = mat["description"]
            unit = mat.get("unit", "")
            self.material_desc_label.setText(
                f"{desc}" + (f"  [{unit}]" if unit else "")
            )
            self.material_desc_label.setStyleSheet(
                "color: #2E7D32; font-weight: bold; font-style: italic;"
            )
        else:
            self.material_desc_label.setText("—  (not in catalog)")
            self.material_desc_label.setStyleSheet(
                "color: #999; font-style: italic;"
            )
        self._update_stock_label()

    def _on_project_changed(self):
        """When project changes, reload zones."""
        self.zone_combo.blockSignals(True)
        self.zone_combo.clear()
        self.zone_combo.addItem("— Zone —", userData=None)

        project_id = self.project_combo.currentData()
        if project_id is not None:
            zones = get_zones_for_project(project_id)
            for z in zones:
                self.zone_combo.addItem(f"Zone {z}", userData=z)

        self.zone_combo.blockSignals(False)
        self._load_warehouses()   # pick up warehouses created since last load
        self._select_default_warehouse(project_id)
        self._on_zone_changed()

    def _on_zone_changed(self):
        """When zone changes, reload blocks."""
        self.block_combo.blockSignals(True)
        self.block_combo.clear()
        self.block_combo.addItem("— Block —", userData=None)

        project_id = self.project_combo.currentData()
        zone = self.zone_combo.currentData()

        if project_id and zone:
            blocks = get_blocks_for_zone(project_id, zone)
            for b in blocks:
                self.block_combo.addItem(f"Block {b}", userData=b)

        self.block_combo.blockSignals(False)
        self._on_block_changed()

    def _on_block_changed(self):
        """When block changes, reload containers."""
        self.container_combo.blockSignals(True)
        self.container_combo.clear()
        self.container_combo.addItem("— Container —", userData=None)

        project_id = self.project_combo.currentData()
        zone = self.zone_combo.currentData()
        block = self.block_combo.currentData()

        self._current_containers = []
        if project_id and zone and block:
            self._current_containers = get_containers_for_block(project_id, zone, block)
            for c in self._current_containers:
                label = f"C{c.container_index}  ({c.container_type})"
                self.container_combo.addItem(label, userData=c.id)

        self.container_combo.blockSignals(False)
        self._on_container_changed()

    def _on_container_changed(self):
        """Auto-fills Type and Serial Number when container is selected."""
        container_id = self.container_combo.currentData()

        if container_id is None:
            self.type_label.setText("—")
            self.serial_label.setText("—")
            return

        # Find the matching container object
        selected = next((c for c in self._current_containers if c.id == container_id), None)
        if selected:
            self.type_label.setText(selected.container_type)
            self.serial_label.setText(selected.serial_number or "N/A")

    # ── Save logic ───────────────────────────────────────────────────────

    def _save_entry(self):
        """Validates and saves the log entry."""

        # Validation
        if self.project_combo.currentData() is None:
            QMessageBox.warning(self, "Validation", "Please select a project.")
            return
        if self.container_combo.currentData() is None:
            QMessageBox.warning(self, "Validation",
                                "Please select Zone → Block → Container.")
            return
        if not self.material_input.text().strip():
            QMessageBox.warning(self, "Validation", "Material number is required.")
            return

        material  = self.material_input.text().strip().upper()
        quantity  = self.quantity_spin.value()
        wh_id     = self.warehouse_combo.currentData()

        # Warn if the chosen warehouse doesn't have enough stock
        if wh_id:
            stock = get_stock_quantity(wh_id, material)
            available = stock["quantity"] if stock else 0
            if available < quantity:
                wh_name = self.warehouse_combo.currentText().strip("🏭📦 ")
                ans = QMessageBox.question(
                    self, "Insufficient Stock",
                    f"'{wh_name}' has only {available:g} x {material} in stock,\n"
                    f"but you are consuming {quantity:g}.\n\n"
                    "Save anyway? (stock will drop to 0)",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No
                )
                if ans != QMessageBox.Yes:
                    return

        entry = LogEntry(
            project_id=self.project_combo.currentData(),
            container_id=self.container_combo.currentData(),
            date=self.date_edit.date().toString("yyyy-MM-dd"),
            material_number=material,
            quantity=quantity,
            comment=self.comment_input.toPlainText().strip(),
            warehouse_id=wh_id,
        )

        try:
            entry_id = save_log_entry(entry)
            QMessageBox.information(self, "Saved",
                f"Log entry #{entry_id} saved successfully.")
            self._clear_form_after_save()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save:\n{str(e)}")

    def _clear_form_after_save(self):
        """Clears only the activity fields (keeps project/location selected)."""
        self.material_input.clear()
        self.material_desc_label.setText("—")
        self.quantity_spin.setValue(1.0)
        self.comment_input.clear()
        self.date_edit.setDate(QDate.currentDate())
        self._update_stock_label()

    def _clear_form(self):
        """Resets the entire form."""
        self.project_combo.setCurrentIndex(0)
        self.material_input.clear()
        self.material_desc_label.setText("—")
        self.quantity_spin.setValue(1.0)
        self.comment_input.clear()
        self.date_edit.setDate(QDate.currentDate())
