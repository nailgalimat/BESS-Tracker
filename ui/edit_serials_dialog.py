"""
ui/edit_serials_dialog.py
--------------------------
Dialog for editing container types and serial numbers on an existing project.

User flow:
  1. Select a project from the dropdown
  2. Table loads showing all containers for that project
  3. User edits Type and/or Serial Number cells directly
  4. Click "Save Changes"

Nothing else about the project (name, structure) is changed here.
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton,
    QTableWidget, QTableWidgetItem, QMessageBox,
    QGroupBox, QHeaderView, QFileDialog
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont

from models.models import CONTAINER_TYPES, default_container_type
from services.project_service import get_all_projects, get_containers_for_project
from services.edit_serials_service import (
    update_container_serial_and_type,
    export_serials_template,
    import_serials_from_excel,
)


class EditSerialsDialog(QDialog):
    """Dialog to edit serial numbers and container types for an existing project."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Container Serials & Types")
        self.setMinimumSize(750, 500)
        self._containers = []   # Container objects currently loaded
        self._build_ui()
        self._load_projects()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # ── Project selector ─────────────────────────────────────────────
        sel_group = QGroupBox("Select Project")
        sel_row = QHBoxLayout()

        sel_row.addWidget(QLabel("Project:"))
        self.proj_combo = QComboBox()
        self.proj_combo.setMinimumWidth(260)
        sel_row.addWidget(self.proj_combo)

        load_btn = QPushButton("📂  Load Containers")
        load_btn.setStyleSheet(
            "QPushButton { background:#1976D2; color:white; border-radius:4px; padding:4px 14px; }"
            "QPushButton:hover { background:#0D47A1; }"
        )
        load_btn.clicked.connect(self._load_containers)
        sel_row.addWidget(load_btn)
        sel_row.addStretch()
        sel_group.setLayout(sel_row)
        layout.addWidget(sel_group)

        # ── Info label ───────────────────────────────────────────────────
        self.info_label = QLabel("Select a project and click 'Load Containers'.")
        self.info_label.setStyleSheet("color: #555; font-style: italic;")
        layout.addWidget(self.info_label)

        # ── Container table ──────────────────────────────────────────────
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([
            "Zone", "Block", "Container #", "Type", "Serial Number"
        ])
        # Zone / Block / Container # are read-only — only Type and Serial editable
        self.table.setColumnWidth(0, 60)
        self.table.setColumnWidth(1, 60)
        self.table.setColumnWidth(2, 90)
        self.table.setColumnWidth(3, 180)
        self.table.setColumnWidth(4, 200)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.table)

        # ── Buttons ──────────────────────────────────────────────────────
        btn_row = QHBoxLayout()

        # Template + Import buttons on the left
        template_btn = QPushButton("📋  Download Template")
        template_btn.setToolTip("Download an Excel template pre-filled with this project's containers")
        template_btn.setStyleSheet(
            "QPushButton { background:#546E7A; color:white; border-radius:4px; padding:4px 12px; }"
            "QPushButton:hover { background:#37474F; }"
        )
        template_btn.clicked.connect(self._download_template)
        btn_row.addWidget(template_btn)

        import_btn = QPushButton("📂  Import from Excel")
        import_btn.setToolTip("Import serial numbers from a filled-in template")
        import_btn.setStyleSheet(
            "QPushButton { background:#2E7D32; color:white; border-radius:4px; padding:4px 12px; }"
            "QPushButton:hover { background:#1B5E20; }"
        )
        import_btn.clicked.connect(self._import_from_excel)
        btn_row.addWidget(import_btn)

        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        save_btn = QPushButton("💾  Save Changes")
        save_btn.setFixedHeight(40)
        save_btn.setStyleSheet(
            "QPushButton { background:#4CAF50; color:white; font-weight:bold;"
            "border-radius:4px; padding:0 20px; }"
            "QPushButton:hover { background:#388E3C; }"
        )
        save_btn.clicked.connect(self._save_changes)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    # ── Template & Import ─────────────────────────────────────────────────

    def _download_template(self):
        """Exports a pre-filled template for the loaded project."""
        if not self._containers:
            QMessageBox.warning(self, "Load first",
                "Please load a project's containers before downloading a template.")
            return

        project_name = self.proj_combo.currentText().replace(" ", "_")
        default_name = f"serials_template_{project_name}.xlsx"

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Serial Numbers Template",
            default_name, "Excel Files (*.xlsx)"
        )
        if not file_path:
            return

        try:
            export_serials_template(self._containers, file_path)
            QMessageBox.information(
                self, "Template Saved",
                f"Template saved to:\n{file_path}\n\n"
                "Instructions:\n"
                "1. Fill in the 'Serial Number' column\n"
                "2. Optionally change the 'Type' column\n"
                "3. Do NOT change any other columns\n"
                "4. Click 'Import from Excel' to load it back"
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save template:\n{str(e)}")

    def _import_from_excel(self):
        """Imports serial numbers from a filled-in template."""
        if not self._containers:
            QMessageBox.warning(self, "Load first",
                "Please load a project's containers first, then import.")
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self, "Import Serial Numbers",
            "", "Excel Files (*.xlsx *.xls)"
        )
        if not file_path:
            return

        try:
            result = import_serials_from_excel(file_path)
        except ValueError as e:
            QMessageBox.critical(self, "Import Failed", str(e))
            return
        except Exception as e:
            QMessageBox.critical(self, "Import Failed", f"Unexpected error:\n{str(e)}")
            return

        # Reload the table to show updated values
        self._load_containers()

        msg = (
            f"Import complete:\n"
            f"  ✅  {result['updated']} container(s) updated\n"
            f"  ⏭  {result['skipped']} row(s) skipped\n"
        )
        if result['errors']:
            msg += f"\n⚠️  {len(result['errors'])} error(s):\n"
            msg += "\n".join(result['errors'][:5])
        QMessageBox.information(self, "Import Result", msg)

    # ── Data loading ─────────────────────────────────────────────────────

    def _load_projects(self):
        self.proj_combo.clear()
        self.proj_combo.addItem("— Select Project —", userData=None)
        for p in get_all_projects():
            self.proj_combo.addItem(p.name, userData=p.id)

    def _load_containers(self):
        """Loads all containers for the selected project into the table."""
        project_id = self.proj_combo.currentData()
        if project_id is None:
            QMessageBox.warning(self, "No Project", "Please select a project first.")
            return

        self._containers = get_containers_for_project(project_id)
        if not self._containers:
            QMessageBox.information(self, "Empty", "No containers found for this project.")
            return

        self.table.setRowCount(0)

        for c in self._containers:
            row = self.table.rowCount()
            self.table.insertRow(row)

            # Zone, Block, Container # — read-only
            for col, val in enumerate([
                str(c.zone_number),
                str(c.block_number),
                str(c.container_index)
            ]):
                item = QTableWidgetItem(val)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, col, item)

            # Type — editable dropdown
            type_combo = QComboBox()
            type_combo.addItems(CONTAINER_TYPES)
            type_combo.setCurrentText(c.container_type)
            self.table.setCellWidget(row, 3, type_combo)

            # Serial Number — editable text cell
            serial_item = QTableWidgetItem(c.serial_number or "")
            serial_item.setToolTip("Click to edit serial number")
            self.table.setItem(row, 4, serial_item)

        count = len(self._containers)
        self.info_label.setText(
            f"Loaded {count} container{'s' if count != 1 else ''}. "
            "Edit Type and/or Serial Number, then click Save."
        )

    # ── Save ─────────────────────────────────────────────────────────────

    def _save_changes(self):
        """Reads the table and saves updated type + serial for each container."""
        if not self._containers:
            QMessageBox.warning(self, "Nothing to save",
                                "Load a project's containers first.")
            return

        updates = []
        for row, container in enumerate(self._containers):
            new_type   = self.table.cellWidget(row, 3).currentText()
            serial_item = self.table.item(row, 4)
            new_serial  = serial_item.text().strip() if serial_item else ""
            updates.append((container.id, new_type, new_serial))

        try:
            update_container_serial_and_type(updates)
            QMessageBox.information(
                self, "Saved",
                f"Updated {len(updates)} container{'s' if len(updates) != 1 else ''} successfully."
            )
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save:\n{str(e)}")
