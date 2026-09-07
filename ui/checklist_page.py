"""
ui/checklist_page.py
---------------------
Commissioning Checklist module.

Three inner tabs:
  1. Templates  — create/edit checklist templates and their items
  2. Run         — execute a checklist for a specific container/block
  3. History     — view past runs with pass/fail summary
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QSplitter,
    QLabel, QComboBox, QDateEdit, QLineEdit, QTextEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QGroupBox,
    QHeaderView, QAbstractItemView, QTabWidget, QMessageBox,
    QFileDialog, QDialog, QDialogButtonBox, QScrollArea, QFrame
)
from PyQt5.QtCore import Qt, QDate
from PyQt5.QtGui import QColor, QFont

from ui.components import PageHeader, PrimaryButton, SecondaryButton, make_table
from services.checklist_service import (
    get_all_templates, get_template, save_template, delete_template,
    get_items, save_items, start_run, get_runs, get_run_detail,
    save_result, auto_update_run_status, delete_run, export_run_to_excel,
)
from services.project_service import (
    get_all_projects, get_zones_for_project, get_blocks_for_zone,
    get_containers_for_block,
)
from models.models import CONTAINER_TYPES

RESULT_OPTIONS = ["Pending", "Pass", "Fail", "N/A"]
RESULT_COLORS  = {
    "Pass":    "#D4EDDA", "Fail": "#F8D7DA",
    "N/A":     "#E2E3E5", "Pending": "#FFF3CD"
}
RUN_STATUS_COLORS = {
    "Passed": "#D4EDDA", "Failed": "#F8D7DA", "In Progress": "#FFF3CD"
}


class ChecklistPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = PageHeader("Commissioning Checklists",
                            "Templates, runs and history")
        layout.addWidget(header)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # Tab 1: Templates
        self.tmpl_tab = TemplatesTab()
        self.tabs.addTab(self.tmpl_tab, "📋  Templates")

        # Tab 2: Run checklist
        self.run_tab = RunTab()
        self.tabs.addTab(self.run_tab, "▶  Run Checklist")

        # Tab 3: History
        self.hist_tab = HistoryTab()
        self.tabs.addTab(self.hist_tab, "📁  History")

        # Refresh history when switching to it
        self.tabs.currentChanged.connect(self._on_tab_changed)

    def _on_tab_changed(self, idx):
        if idx == 1:
            self.run_tab.refresh()
        elif idx == 2:
            self.hist_tab.refresh()

    def refresh_projects(self):
        self.run_tab.refresh()


# ── TEMPLATES TAB ─────────────────────────────────────────────────────────────

class TemplatesTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._selected_template_id = None
        self._build_ui()
        self._load_templates()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)

        splitter = QSplitter(Qt.Horizontal)

        # Left: template list
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setSpacing(6)
        ll.setContentsMargins(0, 0, 0, 0)

        btn_row = QHBoxLayout()
        new_btn = PrimaryButton("➕  New Template")
        new_btn.clicked.connect(self._new_template)
        btn_row.addWidget(new_btn)
        del_btn = SecondaryButton("🗑  Delete")
        del_btn.clicked.connect(self._delete_template)
        btn_row.addWidget(del_btn)
        ll.addLayout(btn_row)

        self.tmpl_table = make_table(["ID","Name","Type","Scope","Items"])
        self.tmpl_table.setMaximumWidth(380)
        self.tmpl_table.itemSelectionChanged.connect(self._on_template_selected)
        ll.addWidget(self.tmpl_table)
        splitter.addWidget(left)

        # Right: item editor
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setSpacing(6)
        rl.setContentsMargins(0, 0, 0, 0)

        self.items_label = QLabel("Select a template to edit its items")
        self.items_label.setStyleSheet("color:#555;font-style:italic;")
        rl.addWidget(self.items_label)

        items_btn_row = QHBoxLayout()
        add_item_btn = PrimaryButton("➕  Add Item")
        add_item_btn.clicked.connect(self._add_item_row)
        items_btn_row.addWidget(add_item_btn)
        save_items_btn = SecondaryButton("💾  Save Items")
        save_items_btn.clicked.connect(self._save_items)
        items_btn_row.addWidget(save_items_btn)
        items_btn_row.addStretch()
        rl.addLayout(items_btn_row)

        self.items_table = QTableWidget(0, 4)
        self.items_table.setHorizontalHeaderLabels(
            ["#","Category","Check Item","Expected Value"])
        self.items_table.setColumnWidth(0, 40)
        self.items_table.setColumnWidth(1, 110)
        self.items_table.setColumnWidth(2, 280)
        self.items_table.setColumnWidth(3, 150)
        self.items_table.horizontalHeader().setStretchLastSection(True)
        rl.addWidget(self.items_table)
        splitter.addWidget(right)
        splitter.setSizes([380, 600])
        layout.addWidget(splitter)

    def _load_templates(self):
        self.tmpl_table.setRowCount(0)
        for t in get_all_templates():
            row = self.tmpl_table.rowCount()
            self.tmpl_table.insertRow(row)
            for col, val in enumerate([
                str(t["id"]), t["name"],
                t.get("container_type","—") or "Multi",
                t.get("scope","container").capitalize(),
                str(t.get("item_count",0))
            ]):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignCenter)
                self.tmpl_table.setItem(row, col, item)

    def _on_template_selected(self):
        row = self.tmpl_table.currentRow()
        if row < 0:
            return
        id_item = self.tmpl_table.item(row, 0)
        if not id_item:
            return
        self._selected_template_id = int(id_item.text())
        tmpl = get_template(self._selected_template_id)
        if tmpl:
            self.items_label.setText(
                f"Items for: {tmpl['name']}  ({tmpl.get('container_type','') or 'Multi-type'})"
            )
        self._load_items()

    def _load_items(self):
        self.items_table.setRowCount(0)
        if not self._selected_template_id:
            return
        for item in get_items(self._selected_template_id):
            self._add_row_to_items_table(
                item.get("order_num", 0),
                item.get("category", ""),
                item.get("description", ""),
                item.get("expected", ""),
            )

    def _add_row_to_items_table(self, order=None, category="",
                                 description="", expected=""):
        row = self.items_table.rowCount()
        self.items_table.insertRow(row)
        order_val = str(order if order is not None else row + 1)
        self.items_table.setItem(row, 0, QTableWidgetItem(order_val))
        self.items_table.setItem(row, 1, QTableWidgetItem(category))
        self.items_table.setItem(row, 2, QTableWidgetItem(description))
        self.items_table.setItem(row, 3, QTableWidgetItem(expected))

    def _add_item_row(self):
        self._add_row_to_items_table()

    def _save_items(self):
        if not self._selected_template_id:
            QMessageBox.warning(self, "No Template",
                "Select a template first.")
            return
        items = []
        for row in range(self.items_table.rowCount()):
            desc = (self.items_table.item(row, 2) or
                    QTableWidgetItem("")).text().strip()
            if not desc:
                continue
            items.append({
                "order_num":   int((self.items_table.item(row, 0) or
                               QTableWidgetItem(str(row))).text() or row),
                "category":   (self.items_table.item(row, 1) or
                               QTableWidgetItem("")).text().strip(),
                "description": desc,
                "expected":   (self.items_table.item(row, 3) or
                               QTableWidgetItem("")).text().strip(),
            })
        save_items(self._selected_template_id, items)
        QMessageBox.information(self, "Saved",
            f"{len(items)} items saved.")
        self._load_templates()

    def _new_template(self):
        dlg = TemplateDialog(parent=self)
        if dlg.exec_() == QDialog.Accepted:
            data = dlg.get_data()
            save_template(**data)
            self._load_templates()

    def _delete_template(self):
        if not self._selected_template_id:
            return
        ans = QMessageBox.question(self, "Delete?",
            "Delete this template and all its items?\n"
            "(Existing runs using this template are NOT deleted)",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ans == QMessageBox.Yes:
            delete_template(self._selected_template_id)
            self._selected_template_id = None
            self._load_templates()
            self.items_table.setRowCount(0)


class TemplateDialog(QDialog):
    def __init__(self, existing=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Template" if not existing else "Edit Template")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_input = QLineEdit(existing["name"] if existing else "")
        self.name_input.setPlaceholderText("e.g. Battery Container Commissioning")
        form.addRow("Name *:", self.name_input)
        self.desc_input = QTextEdit()
        self.desc_input.setMaximumHeight(60)
        if existing:
            self.desc_input.setPlainText(existing.get("description",""))
        form.addRow("Description:", self.desc_input)
        self.type_combo = QComboBox()
        self.type_combo.addItem("— Multi-type (for block) —", "")
        for t in CONTAINER_TYPES:
            self.type_combo.addItem(t, t)
        if existing and existing.get("container_type"):
            idx = self.type_combo.findData(existing["container_type"])
            if idx >= 0:
                self.type_combo.setCurrentIndex(idx)
        form.addRow("Container Type:", self.type_combo)
        self.scope_combo = QComboBox()
        self.scope_combo.addItems(["container","block"])
        if existing:
            self.scope_combo.setCurrentText(existing.get("scope","container"))
        form.addRow("Scope:", self.scope_combo)
        layout.addLayout(form)
        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._validate)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _validate(self):
        if not self.name_input.text().strip():
            QMessageBox.warning(self,"Required","Template name is required.")
            return
        self.accept()

    def get_data(self):
        return {
            "name":           self.name_input.text().strip(),
            "description":    self.desc_input.toPlainText().strip(),
            "container_type": self.type_combo.currentData() or "",
            "scope":          self.scope_combo.currentText(),
        }


# ── RUN TAB ───────────────────────────────────────────────────────────────────

class RunTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._run_id = None
        self._result_map = {}  # result_id → row index in run table
        self._containers = []
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)

        # Setup group
        setup = QGroupBox("Setup")
        sf = QFormLayout()
        self.tmpl_combo = QComboBox()
        sf.addRow("Template *:", self.tmpl_combo)
        self.proj_combo = QComboBox()
        self.proj_combo.currentIndexChanged.connect(self._on_proj_changed)
        sf.addRow("Project *:", self.proj_combo)

        loc_row = QHBoxLayout()
        self.zone_combo = QComboBox()
        self.zone_combo.currentIndexChanged.connect(self._on_zone_changed)
        loc_row.addWidget(QLabel("Zone:")); loc_row.addWidget(self.zone_combo)
        self.block_combo = QComboBox()
        self.block_combo.currentIndexChanged.connect(self._on_block_changed)
        loc_row.addWidget(QLabel("Block:")); loc_row.addWidget(self.block_combo)
        self.cont_combo = QComboBox()
        loc_row.addWidget(QLabel("Container (optional):")); loc_row.addWidget(self.cont_combo)
        sf.addRow("Location:", loc_row)

        eng_date_row = QHBoxLayout()
        self.engineer_input = QLineEdit()
        self.engineer_input.setPlaceholderText("Engineer name")
        eng_date_row.addWidget(self.engineer_input)
        self.run_date = QDateEdit()
        self.run_date.setDate(QDate.currentDate())
        self.run_date.setCalendarPopup(True)
        self.run_date.setDisplayFormat("yyyy-MM-dd")
        eng_date_row.addWidget(self.run_date)
        sf.addRow("Engineer / Date:", eng_date_row)

        start_btn = PrimaryButton("▶  Start Checklist Run")
        start_btn.clicked.connect(self._start_run)
        sf.addRow("", start_btn)
        setup.setLayout(sf)
        layout.addWidget(setup)

        # Run execution table
        self.run_label = QLabel("Start a run to begin filling in results.")
        self.run_label.setStyleSheet("color:#555;font-style:italic;")
        layout.addWidget(self.run_label)

        self.run_table = QTableWidget(0, 6)
        self.run_table.setHorizontalHeaderLabels([
            "#","Category","Check Item","Expected","Result","Measured / Comment"
        ])
        self.run_table.setColumnWidth(0, 35)
        self.run_table.setColumnWidth(1, 100)
        self.run_table.setColumnWidth(2, 260)
        self.run_table.setColumnWidth(3, 120)
        self.run_table.setColumnWidth(4, 90)
        self.run_table.setColumnWidth(5, 180)
        self.run_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.run_table)

        finish_row = QHBoxLayout()
        finish_row.addStretch()
        export_btn = SecondaryButton("📥  Export to Excel")
        export_btn.clicked.connect(self._export_run)
        finish_row.addWidget(export_btn)
        finish_btn = PrimaryButton("✅  Finish & Save Run")
        finish_btn.clicked.connect(self._finish_run)
        finish_row.addWidget(finish_btn)
        layout.addLayout(finish_row)

    def refresh(self):
        self.tmpl_combo.clear()
        self.tmpl_combo.addItem("— Select Template —", userData=None)
        for t in get_all_templates():
            self.tmpl_combo.addItem(
                f"{t['name']} ({t.get('container_type','') or 'Multi'})",
                userData=t["id"]
            )
        self.proj_combo.clear()
        self.proj_combo.addItem("— Select Project —", userData=None)
        for p in get_all_projects():
            self.proj_combo.addItem(p.name, userData=p.id)

    def _on_proj_changed(self):
        self.zone_combo.clear()
        self.zone_combo.addItem("— Zone —", userData=None)
        pid = self.proj_combo.currentData()
        if pid:
            for z in get_zones_for_project(pid):
                self.zone_combo.addItem(f"Zone {z}", userData=z)

    def _on_zone_changed(self):
        self.block_combo.clear()
        self.block_combo.addItem("— Block —", userData=None)
        pid  = self.proj_combo.currentData()
        zone = self.zone_combo.currentData()
        if pid and zone:
            for b in get_blocks_for_zone(pid, zone):
                self.block_combo.addItem(f"Block {b}", userData=b)

    def _on_block_changed(self):
        self.cont_combo.clear()
        self.cont_combo.addItem("— Block scope —", userData=None)
        pid   = self.proj_combo.currentData()
        zone  = self.zone_combo.currentData()
        block = self.block_combo.currentData()
        self._containers = []
        if pid and zone and block:
            self._containers = get_containers_for_block(pid, zone, block)
            for c in self._containers:
                self.cont_combo.addItem(
                    f"C{c.container_index} — {c.container_type}",
                    userData=c.id
                )

    def _start_run(self):
        if not self.tmpl_combo.currentData():
            QMessageBox.warning(self,"Required","Select a template.")
            return
        if not self.proj_combo.currentData():
            QMessageBox.warning(self,"Required","Select a project.")
            return
        if not self.block_combo.currentData():
            QMessageBox.warning(self,"Required","Select Zone and Block.")
            return

        self._run_id = start_run(
            template_id  = self.tmpl_combo.currentData(),
            project_id   = self.proj_combo.currentData(),
            zone_number  = self.zone_combo.currentData(),
            block_number = self.block_combo.currentData(),
            run_date     = self.run_date.date().toString("yyyy-MM-dd"),
            engineer     = self.engineer_input.text().strip(),
            container_id = self.cont_combo.currentData(),
        )
        self._load_run_table()
        self.run_label.setText(
            f"Run #{self._run_id} started — fill in results below."
        )

    def _load_run_table(self):
        self.run_table.setRowCount(0)
        self._result_map = {}
        if not self._run_id:
            return
        for item in get_run_detail(self._run_id):
            row = self.run_table.rowCount()
            self.run_table.insertRow(row)
            for col, val in enumerate([
                str(item["order_num"]),
                item.get("category",""),
                item.get("description",""),
                item.get("expected",""),
            ]):
                cell = QTableWidgetItem(val)
                cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                self.run_table.setItem(row, col, cell)

            # Result dropdown
            result_combo = QComboBox()
            result_combo.addItems(RESULT_OPTIONS)
            result_combo.setCurrentText(item.get("result","Pending"))
            result_id = item["result_id"]
            result_combo.currentTextChanged.connect(
                lambda val, rid=result_id, r=row: self._on_result_changed(rid, val, r)
            )
            self.run_table.setCellWidget(row, 4, result_combo)

            # Measured + Comment combined
            measured = item.get("measured","") or ""
            comment  = item.get("comment","")  or ""
            combined = f"{measured} | {comment}".strip(" |") if (measured or comment) else ""
            mc_item  = QTableWidgetItem(combined)
            self.run_table.setItem(row, 5, mc_item)

            self._result_map[result_id] = row
            self._color_row(row, item.get("result","Pending"))

    def _on_result_changed(self, result_id: int, new_result: str, row: int):
        mc_item = self.run_table.item(row, 5)
        comment = mc_item.text() if mc_item else ""
        save_result(result_id, new_result, measured="", comment=comment)
        self._color_row(row, new_result)

    def _color_row(self, row: int, result: str):
        bg = QColor(RESULT_COLORS.get(result, "#FFFFFF"))
        for col in range(self.run_table.columnCount()):
            item = self.run_table.item(row, col)
            if item:
                item.setBackground(bg)

    def _finish_run(self):
        if not self._run_id:
            QMessageBox.warning(self,"No Run","Start a run first.")
            return
        # Save measured/comment from col 5
        for result_id, row in self._result_map.items():
            combo   = self.run_table.cellWidget(row, 4)
            mc_item = self.run_table.item(row, 5)
            result  = combo.currentText() if combo else "Pending"
            mc_text = mc_item.text() if mc_item else ""
            save_result(result_id, result, measured=mc_text, comment="")
        auto_update_run_status(self._run_id)
        QMessageBox.information(self,"Done",
            f"Run #{self._run_id} saved and status updated.")
        self._run_id = None
        self.run_table.setRowCount(0)
        self.run_label.setText("Run completed. Start a new run above.")

    def _export_run(self):
        if not self._run_id:
            QMessageBox.warning(self,"No Run","Start a run first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,"Export Checklist","checklist_run.xlsx",
            "Excel Files (*.xlsx)")
        if path:
            try:
                export_run_to_excel(self._run_id, path)
                QMessageBox.information(self,"Exported",f"Saved to:\n{path}")
            except Exception as e:
                QMessageBox.critical(self,"Error",str(e))


# ── HISTORY TAB ───────────────────────────────────────────────────────────────

class HistoryTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Project:"))
        self.proj_filter = QComboBox()
        self.proj_filter.setMinimumWidth(200)
        filter_row.addWidget(self.proj_filter)
        filter_row.addStretch()
        load_btn = PrimaryButton("🔍  Load")
        load_btn.clicked.connect(self.refresh)
        filter_row.addWidget(load_btn)
        exp_btn = SecondaryButton("📥  Export Selected")
        exp_btn.clicked.connect(self._export_selected)
        filter_row.addWidget(exp_btn)
        del_btn = SecondaryButton("🗑  Delete Selected")
        del_btn.clicked.connect(self._delete_selected)
        filter_row.addWidget(del_btn)
        layout.addLayout(filter_row)

        self.hist_table = make_table([
            "ID","Date","Project","Template","Zone","Block",
            "Engineer","Status","Total","Passed","Failed","Pending"
        ])
        layout.addWidget(self.hist_table)

    def refresh(self):
        self.proj_filter.blockSignals(True)
        current = self.proj_filter.currentData()
        self.proj_filter.clear()
        self.proj_filter.addItem("All Projects", userData=None)
        for p in get_all_projects():
            self.proj_filter.addItem(p.name, userData=p.id)
        if current:
            idx = self.proj_filter.findData(current)
            if idx >= 0:
                self.proj_filter.setCurrentIndex(idx)
        self.proj_filter.blockSignals(False)

        pid  = self.proj_filter.currentData()
        runs = get_runs(project_id=pid)
        self.hist_table.setRowCount(0)
        for run in runs:
            row = self.hist_table.rowCount()
            self.hist_table.insertRow(row)
            status = run.get("status","")
            bg = QColor(RUN_STATUS_COLORS.get(status,"#FFFFFF"))
            for col, val in enumerate([
                str(run["id"]), run["run_date"],
                run["project_name"], run["template_name"],
                str(run["zone_number"]), str(run["block_number"]),
                run.get("engineer",""),  status,
                str(run.get("total_items",0)), str(run.get("passed",0)),
                str(run.get("failed",0)),      str(run.get("pending",0)),
            ]):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignCenter)
                item.setBackground(bg)
                self.hist_table.setItem(row, col, item)

    def _get_selected_run_id(self):
        row = self.hist_table.currentRow()
        if row < 0:
            return None
        id_item = self.hist_table.item(row, 0)
        return int(id_item.text()) if id_item else None

    def _export_selected(self):
        run_id = self._get_selected_run_id()
        if not run_id:
            QMessageBox.information(self,"Select","Click a run row first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,"Export Checklist",f"checklist_run_{run_id}.xlsx",
            "Excel Files (*.xlsx)")
        if path:
            try:
                export_run_to_excel(run_id, path)
                QMessageBox.information(self,"Exported",f"Saved to:\n{path}")
            except Exception as e:
                QMessageBox.critical(self,"Error",str(e))

    def _delete_selected(self):
        run_id = self._get_selected_run_id()
        if not run_id:
            return
        ans = QMessageBox.question(self,"Delete?",
            f"Delete run #{run_id}?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ans == QMessageBox.Yes:
            delete_run(run_id)
            self.refresh()
