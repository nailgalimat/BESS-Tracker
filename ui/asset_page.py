"""
ui/asset_page.py
-----------------
Asset Register — Manufacturer, Model, Firmware per container.
Simple table view with inline editing.
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QTableWidget, QTableWidgetItem, QMessageBox,
    QHeaderView, QAbstractItemView
)
from PyQt5.QtCore import Qt
from ui.components import PageHeader, PrimaryButton, SecondaryButton
from services.asset_service import get_assets_for_project, bulk_save_assets
from services.project_service import get_all_projects


class AssetPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._project_id = None
        self._build_ui()
        self._load_projects()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = PageHeader("Asset Register",
                            "Manufacturer · Model · Firmware per container")
        layout.addWidget(header)

        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(24, 16, 24, 24)
        cl.setSpacing(12)

        top = QHBoxLayout()
        top.addWidget(QLabel("Project:"))
        self.proj_combo = QComboBox()
        self.proj_combo.setMinimumWidth(240)
        top.addWidget(self.proj_combo)

        load_btn = PrimaryButton("📂  Load")
        load_btn.clicked.connect(self._load_assets)
        top.addWidget(load_btn)
        top.addStretch()

        save_btn = PrimaryButton("💾  Save All Changes")
        save_btn.clicked.connect(self._save_assets)
        top.addWidget(save_btn)
        cl.addLayout(top)

        self.info_label = QLabel("Load a project to view and edit asset details.")
        self.info_label.setStyleSheet("color:#555;font-style:italic;")
        cl.addWidget(self.info_label)

        # Table: Zone, Block, Cont#, Type, Serial, Manufacturer, Model, Firmware
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels([
            "Zone","Block","Cont #","Type","Serial Number",
            "Manufacturer","Model","Firmware Version"
        ])
        # First 5 cols read-only, last 3 editable
        self.table.setColumnWidth(0, 50)
        self.table.setColumnWidth(1, 50)
        self.table.setColumnWidth(2, 60)
        self.table.setColumnWidth(3, 150)
        self.table.setColumnWidth(4, 160)
        self.table.setColumnWidth(5, 150)
        self.table.setColumnWidth(6, 150)
        self.table.setColumnWidth(7, 160)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        cl.addWidget(self.table)
        layout.addWidget(content)

    def _load_projects(self):
        self.proj_combo.clear()
        self.proj_combo.addItem("— Select Project —", userData=None)
        for p in get_all_projects():
            self.proj_combo.addItem(p.name, userData=p.id)

    def refresh_projects(self):
        self._load_projects()

    def _load_assets(self):
        self._project_id = self.proj_combo.currentData()
        if not self._project_id:
            QMessageBox.warning(self,"No Project","Select a project first.")
            return
        assets = get_assets_for_project(self._project_id)
        self.table.setRowCount(0)
        from PyQt5.QtGui import QColor
        for a in assets:
            row = self.table.rowCount()
            self.table.insertRow(row)
            # Read-only cols
            for col, val in enumerate([
                str(a["zone_number"]), str(a["block_number"]),
                str(a["container_index"]), a["container_type"],
                a.get("serial_number","")
            ]):
                item = QTableWidgetItem(val)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                item.setTextAlignment(Qt.AlignCenter)
                item.setBackground(QColor("#F5F7FA"))
                self.table.setItem(row, col, item)
            # Editable cols
            for col, val in enumerate([
                a.get("manufacturer",""),
                a.get("model",""),
                a.get("firmware_version",""),
            ], start=5):
                self.table.setItem(row, col, QTableWidgetItem(val or ""))
            # Store container_id in UserRole of col 0
            self.table.item(row, 0).setData(Qt.UserRole, a["container_id"])

        self.info_label.setText(
            f"Loaded {len(assets)} containers. "
            "Edit Manufacturer, Model, and Firmware Version, then click Save."
        )

    def _save_assets(self):
        if not self._project_id:
            QMessageBox.warning(self,"No project","Load a project first.")
            return
        updates = []
        for row in range(self.table.rowCount()):
            id_item = self.table.item(row, 0)
            if not id_item:
                continue
            container_id = id_item.data(Qt.UserRole)
            def cell(c):
                item = self.table.item(row, c)
                return item.text().strip() if item else ""
            updates.append({
                "container_id":    container_id,
                "manufacturer":    cell(5),
                "model":           cell(6),
                "firmware_version":cell(7),
            })
        try:
            bulk_save_assets(updates)
            QMessageBox.information(self,"Saved",
                f"Asset details saved for {len(updates)} containers.")
        except Exception as e:
            QMessageBox.critical(self,"Error",str(e))
