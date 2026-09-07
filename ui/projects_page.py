"""
ui/projects_page.py
--------------------
Project setup: create projects and enter the STATIC report inputs once
(customer, O&M, OEM, capacities, contractual/availability basis, cycle target,
site type). These feed every monthly report for that project.

Monthly data (PM, unavailability, narrative, SCADA files) lives on the separate
Monthly Reports page.
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QComboBox, QDoubleSpinBox, QSpinBox, QListWidget, QListWidgetItem,
    QPushButton, QGroupBox, QMessageBox, QInputDialog, QScrollArea,
)
from PyQt5.QtCore import Qt

from ui.components import PageHeader, PrimaryButton, SecondaryButton
import services.report_workflow_service as rw


class ProjectsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_pid = None
        self._build_ui()
        self._reload_projects()

    # ── UI ────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(PageHeader("Projects",
                                  "Set up each project once — these details feed every monthly report"))

        body = QHBoxLayout()
        body.setContentsMargins(16, 8, 16, 16)
        body.setSpacing(14)

        # ── Left: project list ──────────────────────────────────────────
        left = QVBoxLayout()
        left.addWidget(QLabel("<b>Projects</b>"))
        self.proj_list = QListWidget()
        self.proj_list.setMaximumWidth(240)
        self.proj_list.currentItemChanged.connect(self._on_select)
        left.addWidget(self.proj_list)
        newp = PrimaryButton("➕  New Project")
        newp.clicked.connect(self._new_project)
        left.addWidget(newp)
        body.addLayout(left)

        # ── Right: config form (scrollable) ──────────────────────────────
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        content = QWidget(); form_wrap = QVBoxLayout(content)

        idn = QGroupBox("Identity")
        idf = QFormLayout(idn)
        self.name = QLineEdit()
        idf.addRow("Project name *:", self.name)
        self.site_type = QComboBox(); self.site_type.addItems(["tashkent", "bukhara"])
        idf.addRow("Report type *:", self.site_type)
        self.num_blocks = QSpinBox(); self.num_blocks.setRange(1, 500)
        idf.addRow("Number of blocks:", self.num_blocks)
        self.customer = QLineEdit(); self.customer.setPlaceholderText("e.g. MASDAR")
        idf.addRow("Customer:", self.customer)
        self.om_company = QLineEdit(); self.om_company.setPlaceholderText("e.g. MASDAR MSTS")
        idf.addRow("O&M company:", self.om_company)
        self.oem = QLineEdit(); self.oem.setPlaceholderText("e.g. Sungrow")
        idf.addRow("OEM:", self.oem)
        self.equipment = QLineEdit()
        idf.addRow("Equipment:", self.equipment)
        self.project_capacity_str = QLineEdit()
        self.project_capacity_str.setPlaceholderText("e.g. 63 MW / 770 MWh")
        idf.addRow("Capacity (label):", self.project_capacity_str)
        form_wrap.addWidget(idn)

        cap = QGroupBox("Capacity & availability basis")
        cf = QFormLayout(cap)
        self.plant_capacity_mw = QDoubleSpinBox(); self.plant_capacity_mw.setRange(0, 100000); self.plant_capacity_mw.setSuffix(" MW"); self.plant_capacity_mw.setDecimals(2)
        cf.addRow("Plant power:", self.plant_capacity_mw)
        self.per_block_capacity_mw = QDoubleSpinBox(); self.per_block_capacity_mw.setRange(0, 10000); self.per_block_capacity_mw.setSuffix(" MW"); self.per_block_capacity_mw.setDecimals(3)
        cf.addRow("Per-block power:", self.per_block_capacity_mw)
        self.contractual_plant_capacity_mw = QDoubleSpinBox(); self.contractual_plant_capacity_mw.setRange(0, 100000); self.contractual_plant_capacity_mw.setSuffix(" MW"); self.contractual_plant_capacity_mw.setDecimals(2)
        cf.addRow("Contractual power:", self.contractual_plant_capacity_mw)
        self.availability_basis_mwh = QDoubleSpinBox(); self.availability_basis_mwh.setRange(0, 100000); self.availability_basis_mwh.setSuffix(" MWh"); self.availability_basis_mwh.setDecimals(1)
        cf.addRow("Availability basis:", self.availability_basis_mwh)
        self.redundancy_threshold_pct = QDoubleSpinBox(); self.redundancy_threshold_pct.setRange(0, 100); self.redundancy_threshold_pct.setValue(100); self.redundancy_threshold_pct.setSuffix(" %")
        cf.addRow("Redundancy threshold:", self.redundancy_threshold_pct)
        self.yearly_cycle_target = QDoubleSpinBox(); self.yearly_cycle_target.setRange(0, 5000); self.yearly_cycle_target.setValue(365)
        cf.addRow("Yearly cycle target:", self.yearly_cycle_target)
        form_wrap.addWidget(cap)

        sig = QGroupBox("Report signatures")
        sf = QFormLayout(sig)
        self.prepared_by = QLineEdit(); sf.addRow("Prepared by:", self.prepared_by)
        self.reviewed_by = QLineEdit(); sf.addRow("Reviewed by:", self.reviewed_by)
        form_wrap.addWidget(sig)

        btn_row = QHBoxLayout(); btn_row.addStretch()
        self.save_btn = PrimaryButton("💾  Save Project")
        self.save_btn.clicked.connect(self._save)
        btn_row.addWidget(self.save_btn)
        form_wrap.addLayout(btn_row)
        form_wrap.addStretch()

        scroll.setWidget(content)
        body.addWidget(scroll, 1)
        root.addLayout(body)
        self._set_form_enabled(False)

    def _set_form_enabled(self, on: bool):
        for w in (self.name, self.site_type, self.num_blocks, self.customer,
                  self.om_company, self.oem, self.equipment,
                  self.project_capacity_str, self.plant_capacity_mw,
                  self.per_block_capacity_mw, self.contractual_plant_capacity_mw,
                  self.availability_basis_mwh, self.redundancy_threshold_pct,
                  self.yearly_cycle_target, self.prepared_by, self.reviewed_by,
                  self.save_btn):
            w.setEnabled(on)

    # ── Data ────────────────────────────────────────────────────────────────
    def _reload_projects(self, select_pid=None):
        self.proj_list.blockSignals(True)
        self.proj_list.clear()
        for p in rw.list_projects():
            it = QListWidgetItem(p['name'])
            it.setData(Qt.UserRole, p['id'])
            self.proj_list.addItem(it)
        self.proj_list.blockSignals(False)
        if select_pid is not None:
            for r in range(self.proj_list.count()):
                if self.proj_list.item(r).data(Qt.UserRole) == select_pid:
                    self.proj_list.setCurrentRow(r); return
        if self.proj_list.count():
            self.proj_list.setCurrentRow(0)

    def _on_select(self, cur, _prev=None):
        if cur is None:
            self._current_pid = None; self._set_form_enabled(False); return
        pid = cur.data(Qt.UserRole)
        self._current_pid = pid
        self._set_form_enabled(True)
        proj = rw.get_project(pid) or {}
        cfg = rw.get_project_config(pid) or {}
        self.name.setText(proj.get('name', ''))
        self.num_blocks.setValue(int(proj.get('num_blocks') or 1))
        i = self.site_type.findText((cfg.get('site_type') or 'tashkent'))
        self.site_type.setCurrentIndex(max(0, i))
        self.customer.setText(cfg.get('customer') or '')
        self.om_company.setText(cfg.get('om_company') or '')
        self.oem.setText(cfg.get('oem') or '')
        self.equipment.setText(cfg.get('equipment') or '')
        self.project_capacity_str.setText(cfg.get('project_capacity_str') or '')
        self.plant_capacity_mw.setValue(float(cfg.get('plant_capacity_mw') or 0))
        self.per_block_capacity_mw.setValue(float(cfg.get('per_block_capacity_mw') or 0))
        self.contractual_plant_capacity_mw.setValue(float(cfg.get('contractual_plant_capacity_mw') or 0))
        self.availability_basis_mwh.setValue(float(cfg.get('availability_basis_mwh') or 0))
        self.redundancy_threshold_pct.setValue(float(cfg.get('redundancy_threshold_pct') or 100))
        self.yearly_cycle_target.setValue(float(cfg.get('yearly_cycle_target') or 365))
        self.prepared_by.setText(cfg.get('prepared_by') or '')
        self.reviewed_by.setText(cfg.get('reviewed_by') or '')

    def set_current_project(self, pid):
        """Called by the shell when a project is opened in the launcher."""
        if pid is None:
            return
        for r in range(self.proj_list.count()):
            if self.proj_list.item(r).data(Qt.UserRole) == pid:
                self.proj_list.setCurrentRow(r)
                return

    def _new_project(self):
        name, ok = QInputDialog.getText(self, "New Project", "Project name:")
        if not ok or not name.strip():
            return
        try:
            pid = rw.create_project(name.strip())
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not create project:\n{e}")
            return
        self._reload_projects(select_pid=pid)

    def _save(self):
        if self._current_pid is None:
            return
        if not self.name.text().strip():
            QMessageBox.warning(self, "Required", "Project name is required."); return
        from database.db_manager import get_connection
        try:
            conn = get_connection()
            conn.execute("UPDATE projects SET name=?, num_blocks=? WHERE id=?",
                         (self.name.text().strip(), self.num_blocks.value(), self._current_pid))
            conn.commit(); conn.close()
            rw.save_project_config(
                self._current_pid,
                site_type=self.site_type.currentText(),
                customer=self.customer.text().strip(),
                om_company=self.om_company.text().strip(),
                oem=self.oem.text().strip(),
                equipment=self.equipment.text().strip(),
                project_capacity_str=self.project_capacity_str.text().strip(),
                plant_capacity_mw=self.plant_capacity_mw.value() or None,
                per_block_capacity_mw=self.per_block_capacity_mw.value() or None,
                contractual_plant_capacity_mw=self.contractual_plant_capacity_mw.value() or None,
                availability_basis_mwh=self.availability_basis_mwh.value() or None,
                redundancy_threshold_pct=self.redundancy_threshold_pct.value(),
                yearly_cycle_target=self.yearly_cycle_target.value(),
                prepared_by=self.prepared_by.text().strip(),
                reviewed_by=self.reviewed_by.text().strip(),
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not save:\n{e}"); return
        self._reload_projects(select_pid=self._current_pid)
        QMessageBox.information(self, "Saved", "Project saved.")
