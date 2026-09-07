"""
ui/materials_dialog.py
-----------------------
Materials catalog management dialog.

Features:
  - View all materials in a searchable table
  - Add / Edit / Delete individual materials
  - Import from Excel (bulk)
  - Export to Excel
  - Download a template Excel file for easy data entry
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QGroupBox, QHeaderView,
    QFileDialog, QMessageBox, QDialogButtonBox,
    QAbstractItemView
)
from PyQt5.QtCore import Qt, QSortFilterProxyModel
from PyQt5.QtGui import QFont, QColor

import pandas as pd
from services.material_service import (
    get_all_materials, save_material, delete_material,
    import_materials_from_excel, export_materials_to_excel,
)


class MaterialsDialog(QDialog):
    """Full materials catalog manager."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("📦  Materials Catalog")
        self.setMinimumSize(820, 580)
        self._materials = []   # current list of dicts from DB
        self._build_ui()
        self._load_materials()

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # ── Top bar: search + buttons ─────────────────────────────────────
        top_row = QHBoxLayout()

        top_row.addWidget(QLabel("🔍 Search:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter by material number or description...")
        self.search_input.setMinimumWidth(280)
        self.search_input.textChanged.connect(self._filter_table)
        top_row.addWidget(self.search_input)
        top_row.addStretch()

        # Action buttons
        for label, slot, color in [
            ("➕  Add",    self._add_material,    "#1976D2"),
            ("✏️  Edit",   self._edit_material,   "#F57F17"),
            ("🗑  Delete", self._delete_material, "#C62828"),
        ]:
            btn = QPushButton(label)
            btn.setFixedHeight(34)
            btn.setStyleSheet(
                f"QPushButton{{background:{color};color:white;border-radius:4px;"
                f"padding:0 12px;font-weight:bold;}}"
                f"QPushButton:hover{{opacity:0.85;}}"
            )
            btn.clicked.connect(slot)
            top_row.addWidget(btn)

        layout.addLayout(top_row)

        # ── Material table ────────────────────────────────────────────────
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([
            "Material Number", "Description", "Unit", "Notes"
        ])
        self.table.setColumnWidth(0, 160)
        self.table.setColumnWidth(1, 300)
        self.table.setColumnWidth(2, 70)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.doubleClicked.connect(self._edit_material)
        layout.addWidget(self.table)

        # ── Bottom bar: import / export / count ───────────────────────────
        bottom_row = QHBoxLayout()

        self.count_label = QLabel("")
        self.count_label.setStyleSheet("color:#555; font-style:italic;")
        bottom_row.addWidget(self.count_label)
        bottom_row.addStretch()

        for label, slot, color in [
            ("📋  Download Template", self._download_template, "#546E7A"),
            ("📂  Import from Excel", self._import_excel,      "#2E7D32"),
            ("📥  Export to Excel",   self._export_excel,       "#1565C0"),
        ]:
            btn = QPushButton(label)
            btn.setFixedHeight(34)
            btn.setStyleSheet(
                f"QPushButton{{background:{color};color:white;border-radius:4px;"
                f"padding:0 12px;}}"
            )
            btn.clicked.connect(slot)
            bottom_row.addWidget(btn)

        close_btn = QPushButton("Close")
        close_btn.setFixedHeight(34)
        close_btn.clicked.connect(self.accept)
        bottom_row.addWidget(close_btn)

        layout.addLayout(bottom_row)

    # ── Data loading ──────────────────────────────────────────────────────

    def _load_materials(self):
        """Fetches all materials from DB and populates the table."""
        self._materials = get_all_materials()
        self._populate_table(self._materials)

    def _populate_table(self, materials: list):
        self.table.setRowCount(0)
        for m in materials:
            row = self.table.rowCount()
            self.table.insertRow(row)
            for col, key in enumerate(
                ["material_number", "description", "unit", "notes"]
            ):
                item = QTableWidgetItem(str(m.get(key, "") or ""))
                item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                self.table.setItem(row, col, item)
        self.count_label.setText(
            f"{len(materials)} material{'s' if len(materials) != 1 else ''}"
        )

    def _filter_table(self, text: str):
        """Live-filters the table by search text."""
        text = text.strip().lower()
        if not text:
            self._populate_table(self._materials)
            return
        filtered = [
            m for m in self._materials
            if text in m["material_number"].lower()
            or text in (m["description"] or "").lower()
        ]
        self._populate_table(filtered)

    def _selected_material_number(self) -> str:
        """Returns the material_number of the selected row, or empty string."""
        row = self.table.currentRow()
        if row < 0:
            return ""
        item = self.table.item(row, 0)
        return item.text() if item else ""

    # ── CRUD actions ──────────────────────────────────────────────────────

    def _add_material(self):
        dlg = MaterialEditDialog(parent=self)
        if dlg.exec_() == QDialog.Accepted:
            data = dlg.get_data()
            try:
                result = save_material(**data)
                self._load_materials()
                QMessageBox.information(
                    self, "Saved",
                    f"Material '{data['material_number']}' {result}."
                )
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    def _edit_material(self):
        mat_num = self._selected_material_number()
        if not mat_num:
            QMessageBox.information(self, "Select a row",
                                    "Click a material row first.")
            return

        # Find full data for this material
        mat = next((m for m in self._materials
                    if m["material_number"] == mat_num), None)
        if not mat:
            return

        dlg = MaterialEditDialog(existing=mat, parent=self)
        if dlg.exec_() == QDialog.Accepted:
            data = dlg.get_data()
            try:
                save_material(**data)
                self._load_materials()
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    def _delete_material(self):
        mat_num = self._selected_material_number()
        if not mat_num:
            QMessageBox.information(self, "Select a row",
                                    "Click a material row first.")
            return

        answer = QMessageBox.question(
            self, "Delete Material?",
            f"Delete '{mat_num}' from the catalog?\n\n"
            "Existing log entries that used this material will NOT be deleted\n"
            "(they keep their material number, just without a description lookup).",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if answer == QMessageBox.Yes:
            delete_material(mat_num)
            self._load_materials()

    # ── Import / Export ───────────────────────────────────────────────────

    def _import_excel(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Import Materials from Excel",
            "", "Excel Files (*.xlsx *.xls)"
        )
        if not file_path:
            return

        try:
            result = import_materials_from_excel(file_path)
        except ValueError as e:
            QMessageBox.critical(self, "Import Failed", str(e))
            return

        self._load_materials()

        msg = (
            f"Import complete:\n"
            f"  ✅  {result['created']} new materials added\n"
            f"  🔄  {result['updated']} existing materials updated\n"
            f"  ⏭  {result['skipped']} rows skipped (empty)\n"
        )
        if result["errors"]:
            msg += f"\n⚠️  {len(result['errors'])} errors:\n"
            msg += "\n".join(result["errors"][:10])
            if len(result["errors"]) > 10:
                msg += f"\n  ...and {len(result['errors']) - 10} more"

        QMessageBox.information(self, "Import Result", msg)

    def _export_excel(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Materials to Excel",
            "materials_catalog.xlsx", "Excel Files (*.xlsx)"
        )
        if not file_path:
            return
        try:
            export_materials_to_excel(file_path)
            QMessageBox.information(self, "Exported",
                f"Materials catalog saved to:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", str(e))

    def _download_template(self):
        """Creates and saves a blank Excel template for the user to fill in."""
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Import Template",
            "materials_import_template.xlsx", "Excel Files (*.xlsx)"
        )
        if not file_path:
            return

        # Template with example rows
        df = pd.DataFrame([
            {"material_number": "FUSE-250A",
             "description":     "Fuse 250A 690V gG",
             "unit":            "pcs",
             "notes":           "Example row — replace with your data"},
            {"material_number": "CABLE-6MM",
             "description":     "Cable 6mm² H07V-K black",
             "unit":            "m",
             "notes":           ""},
            {"material_number": "RELAY-24VDC",
             "description":     "Relay 24VDC coil 16A",
             "unit":            "pcs",
             "notes":           ""},
        ])

        with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Materials")
            ws = writer.sheets["Materials"]
            for col_idx, col in enumerate(df.columns, start=1):
                ws.column_dimensions[
                    ws.cell(row=1, column=col_idx).column_letter
                ].width = 30

        QMessageBox.information(
            self, "Template Saved",
            f"Template saved to:\n{file_path}\n\n"
            "Fill in your materials and use 'Import from Excel' to load them."
        )


# ── Single material add/edit dialog ──────────────────────────────────────────

class MaterialEditDialog(QDialog):
    """Small dialog for adding or editing a single material."""

    def __init__(self, existing: dict = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Material" if existing else "Add Material")
        self.setMinimumWidth(420)
        self._existing = existing
        self._build_ui()
        if existing:
            self._populate(existing)

    def _build_ui(self):
        layout = QVBoxLayout(self)

        form = QFormLayout()
        form.setSpacing(10)

        self.mat_num_input = QLineEdit()
        self.mat_num_input.setPlaceholderText("e.g. FUSE-250A")
        if self._existing:
            self.mat_num_input.setReadOnly(True)
            self.mat_num_input.setStyleSheet("background:#f0f0f0;")
        form.addRow("Material Number *:", self.mat_num_input)

        self.desc_input = QLineEdit()
        self.desc_input.setPlaceholderText("e.g. Fuse 250A 690V gG")
        form.addRow("Description *:", self.desc_input)

        self.unit_input = QLineEdit()
        self.unit_input.setPlaceholderText("e.g. pcs, m, kg (optional)")
        form.addRow("Unit:", self.unit_input)

        self.notes_input = QLineEdit()
        self.notes_input.setPlaceholderText("Optional notes...")
        form.addRow("Notes:", self.notes_input)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _populate(self, m: dict):
        self.mat_num_input.setText(m.get("material_number", ""))
        self.desc_input.setText(m.get("description", ""))
        self.unit_input.setText(m.get("unit", ""))
        self.notes_input.setText(m.get("notes", ""))

    def _validate_and_accept(self):
        if not self.mat_num_input.text().strip():
            QMessageBox.warning(self, "Required", "Material number is required.")
            return
        if not self.desc_input.text().strip():
            QMessageBox.warning(self, "Required", "Description is required.")
            return
        self.accept()

    def get_data(self) -> dict:
        return {
            "material_number": self.mat_num_input.text().strip().upper(),
            "description":     self.desc_input.text().strip(),
            "unit":            self.unit_input.text().strip(),
            "notes":           self.notes_input.text().strip(),
        }
