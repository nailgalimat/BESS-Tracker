"""
ui/monthly_reports_page.py
---------------------------
Monthly report workspace. Pick a Project + Month, then fill in that month's
data across tabs — PM, unavailability (exclusions / manual / balancing),
narrative — attach the month's SCADA files and generate the report.

Static per-project inputs (customer, capacities, availability basis, …) come
from the Projects page and are pulled automatically at generation time.
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPushButton, QGroupBox,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QTabWidget, QDialog,
    QDialogButtonBox, QMessageBox, QProgressBar, QScrollArea,
)
from PyQt5.QtCore import Qt, QDate

from ui.components import PageHeader, PrimaryButton, SecondaryButton
import services.report_workflow_service as rw
import services.availability_service as av
from services.report_workflow_service import MONTHS_EN

from ui.scada_report_page import ExclusionDialog, BalancingDialog
from ui.block_report_page import (
    ManualUnavailabilityDialog, _FilePicker, _SavePathPicker, BlockReportWorker,
)


# ── PM dialog ─────────────────────────────────────────────────────────────────
class PMDialog(QDialog):
    def __init__(self, parent=None, existing=None):
        super().__init__(parent)
        self._existing = existing or None
        self.setWindowTitle("Edit PM Activity" if existing else "Add PM Activity")
        self.setMinimumWidth(480)
        lay = QVBoxLayout(self); form = QFormLayout()
        self.date_from = _date(); form.addRow("From *:", self.date_from)
        self.date_to = _date();   form.addRow("To *:", self.date_to)
        self.blocks = QLineEdit(); self.blocks.setPlaceholderText("e.g. 24  or  1,2,3  (empty = whole plant)")
        form.addRow("Block(s):", self.blocks)
        self.hours = QDoubleSpinBox(); self.hours.setRange(0, 100000); self.hours.setDecimals(1); self.hours.setSuffix(" h")
        form.addRow("Duration:", self.hours)
        self.desc = QTextEdit(); self.desc.setMaximumHeight(70)
        self.desc.setPlaceholderText("What was done (e.g. quarterly PM, fire-system test)…")
        form.addRow("Description:", self.desc)
        lay.addLayout(form)
        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept); btns.rejected.connect(self.reject)
        lay.addWidget(btns)
        if existing:
            self._prefill(existing)

    def _prefill(self, e):
        d1 = QDate.fromString(e.get('date_from', ''), "yyyy-MM-dd")
        if d1.isValid(): self.date_from.setDate(d1)
        d2 = QDate.fromString(e.get('date_to', ''), "yyyy-MM-dd")
        if d2.isValid(): self.date_to.setDate(d2)
        self.blocks.setText(e.get('affected_blocks', '') or '')
        self.hours.setValue(float(e.get('hours') or 0))
        self.desc.setPlainText(e.get('description', '') or '')

    def get_data(self):
        return {
            'affected_blocks': self.blocks.text().strip(),
            'date_from': self.date_from.date().toString("yyyy-MM-dd"),
            'date_to':   self.date_to.date().toString("yyyy-MM-dd"),
            'hours':     float(self.hours.value()),
            'description': self.desc.toPlainText().strip(),
        }


def _date():
    from PyQt5.QtWidgets import QDateEdit
    d = QDateEdit(); d.setCalendarPopup(True); d.setDisplayFormat("yyyy-MM-dd")
    d.setDate(QDate.currentDate()); return d


def _table(headers, widths):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.hideColumn(0)
    for i, w in enumerate(widths):
        t.setColumnWidth(i, w)
    t.horizontalHeader().setStretchLastSection(True)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setMaximumHeight(190); t.setAlternatingRowColors(True)
    return t


class MonthlyReportsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pid = None; self._year = None; self._month = None
        self._worker = None
        self._build_ui()
        self._reload_projects()

    # ── UI ────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(PageHeader("Monthly Reports",
                                  "Pick a project and month, fill in the month's data, attach SCADA and generate"))

        # ── Selector bar ────────────────────────────────────────────────
        bar = QHBoxLayout(); bar.setContentsMargins(16, 6, 16, 6)
        bar.addWidget(QLabel("Project:"))
        self.proj_combo = QComboBox(); self.proj_combo.setMinimumWidth(200)
        self.proj_combo.currentIndexChanged.connect(self._on_project)
        bar.addWidget(self.proj_combo)
        bar.addWidget(QLabel("Year:"))
        self.year_spin = QSpinBox(); self.year_spin.setRange(2020, 2100)
        self.year_spin.setValue(QDate.currentDate().year())
        bar.addWidget(self.year_spin)
        bar.addWidget(QLabel("Month:"))
        self.month_combo = QComboBox(); self.month_combo.addItems(MONTHS_EN[1:])
        self.month_combo.setCurrentIndex(QDate.currentDate().month() - 1)
        bar.addWidget(self.month_combo)
        load_btn = PrimaryButton("📂  Open month")
        load_btn.clicked.connect(self._open_month)
        bar.addWidget(load_btn)
        self.month_lbl = QLabel(""); self.month_lbl.setStyleSheet("color:#6B7A8D;font-style:italic;")
        bar.addWidget(self.month_lbl)
        bar.addStretch()
        root.addLayout(bar)

        self.tabs = QTabWidget(); self.tabs.setEnabled(False)
        root.addWidget(self.tabs, 1)
        self._build_pm_tab()
        self._build_unavail_tab()
        self._build_narrative_tab()
        self._build_scada_tab()

    # PM tab
    def _build_pm_tab(self):
        w = QWidget(); l = QVBoxLayout(w)
        g = QGroupBox("Preventive Maintenance (per block, with duration)")
        gl = QVBoxLayout(g)
        br = QHBoxLayout()
        b1 = PrimaryButton("➕  Add PM"); b1.clicked.connect(self._add_pm); br.addWidget(b1)
        b2 = SecondaryButton("✏️  Edit"); b2.clicked.connect(self._edit_pm); br.addWidget(b2)
        b3 = SecondaryButton("🗑  Delete"); b3.clicked.connect(self._del_pm); br.addWidget(b3)
        br.addStretch(); gl.addLayout(br)
        self.pm_table = _table(["ID", "From", "To", "Blocks", "Hours", "Description"],
                               [0, 100, 100, 120, 60])
        self.pm_table.doubleClicked.connect(lambda *_: self._edit_pm())
        gl.addWidget(self.pm_table); l.addWidget(g)

        cmg = QGroupBox("Corrective Maintenance (one per line)")
        cml = QVBoxLayout(cmg)
        self.cm_text = QTextEdit(); self.cm_text.setPlaceholderText("Corrective actions this month, one per line…")
        cml.addWidget(self.cm_text)
        save = SecondaryButton("💾  Save CM"); save.clicked.connect(self._save_narrative)
        r = QHBoxLayout(); r.addStretch(); r.addWidget(save); cml.addLayout(r)
        l.addWidget(cmg)
        self.tabs.addTab(w, "Works & PM")

    # Unavailability tab
    def _build_unavail_tab(self):
        w = QWidget(); outer = QVBoxLayout(w)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        inner = QWidget(); l = QVBoxLayout(inner)

        # Exclusions
        eg = QGroupBox("Availability Exclusions"); el = QVBoxLayout(eg)
        er = QHBoxLayout()
        for txt, fn in [("➕  Add", self._add_excl), ("✏️  Edit", self._edit_excl), ("🗑  Delete", self._del_excl)]:
            b = (PrimaryButton if txt.startswith("➕") else SecondaryButton)(txt); b.clicked.connect(fn); er.addWidget(b)
        er.addStretch(); el.addLayout(er)
        self.excl_table = _table(["ID", "Type", "From", "To", "Blocks", "Description"], [0, 150, 130, 130, 100])
        self.excl_table.doubleClicked.connect(lambda *_: self._edit_excl())
        el.addWidget(self.excl_table); l.addWidget(eg)

        # Manual unavailability
        mg = QGroupBox("Manual Unavailability"); ml = QVBoxLayout(mg)
        mr = QHBoxLayout()
        b = PrimaryButton("➕  Add"); b.clicked.connect(self._add_man); mr.addWidget(b)
        b = SecondaryButton("🗑  Delete"); b.clicked.connect(self._del_man); mr.addWidget(b)
        mr.addStretch(); ml.addLayout(mr)
        self.man_table = _table(["ID", "Block", "LC", "From", "To", "Hours", "Cause"], [0, 55, 90, 100, 100, 60])
        ml.addWidget(self.man_table); l.addWidget(mg)

        # Balancing
        bg = QGroupBox("Cycle-Balancing / Rested Blocks"); bl = QVBoxLayout(bg)
        brow = QHBoxLayout()
        for txt, fn in [("➕  Add", self._add_bal), ("✏️  Edit", self._edit_bal), ("🗑  Delete", self._del_bal)]:
            b = (PrimaryButton if txt.startswith("➕") else SecondaryButton)(txt); b.clicked.connect(fn); brow.addWidget(b)
        brow.addStretch(); bl.addLayout(brow)
        self.bal_table = _table(["ID", "From", "To", "Blocks", "Note"], [0, 110, 110, 150])
        self.bal_table.doubleClicked.connect(lambda *_: self._edit_bal())
        bl.addWidget(self.bal_table); l.addWidget(bg)

        l.addStretch(); scroll.setWidget(inner); outer.addWidget(scroll)
        self.tabs.addTab(w, "Unavailability")

    # Narrative tab
    def _build_narrative_tab(self):
        w = QWidget(); f = QFormLayout(w)
        self.report_number = QLineEdit(); f.addRow("Report No.:", self.report_number)
        self.site_visits = QTextEdit(); self.site_visits.setMaximumHeight(80); f.addRow("Site visits:", self.site_visits)
        self.recommendations = QTextEdit(); self.recommendations.setMaximumHeight(80); f.addRow("Recommendations:", self.recommendations)
        self.planned_next = QTextEdit(); self.planned_next.setMaximumHeight(80); f.addRow("Planned next period:", self.planned_next)
        self.safety_incidents = QTextEdit(); self.safety_incidents.setMaximumHeight(80); f.addRow("Safety incidents:", self.safety_incidents)
        save = PrimaryButton("💾  Save narrative"); save.clicked.connect(self._save_narrative)
        r = QHBoxLayout(); r.addStretch(); r.addWidget(save); f.addRow("", _wrap(r))
        self.tabs.addTab(w, "Narrative")

    # SCADA + generate tab
    def _build_scada_tab(self):
        w = QWidget(); outer = QVBoxLayout(w)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        inner = QWidget(); l = QVBoxLayout(inner)

        self.tk_group = QGroupBox("Tashkent SCADA files"); tf = QFormLayout(self.tk_group)
        self.tk = {}
        for key, lbl in [('working_status', 'LC Working Status'), ('pcs_cd', 'PCS Charge/Discharge'),
                         ('soc', 'SOC'), ('soh_snapshot', 'SOH snapshot'),
                         ('lc_charge', 'LC Daily Charge'), ('lc_discharge', 'LC Daily Discharge'),
                         ('hv_meter', 'HV Meter daily'), ('alarms', 'Alarm Report'),
                         ('cycles_first_day', 'Cycles first day (opt)'), ('cycles_last_day', 'Cycles last day (opt)'),
                         ('lc_total_charge', 'LC total charge (opt)'), ('lc_total_discharge', 'LC total discharge (opt)'),
                         ('pcs_fault', 'PCS fault status (opt)')]:
            self.tk[key] = _FilePicker(); tf.addRow(lbl + ":", self.tk[key])
        l.addWidget(self.tk_group)

        self.bk_group = QGroupBox("Bukhara SCADA files"); bf = QFormLayout(self.bk_group)
        self.bk = {}
        for key, lbl in [('site_kpi', 'Main KPI 5-min'), ('overall_lc', 'LC Data Total'),
                         ('battery_unit', 'Battery Unit data'), ('meter_daily', 'Main Meter daily'),
                         ('alarms', 'Alarm Report'), ('availability', 'System availability (opt)')]:
            self.bk[key] = _FilePicker(); bf.addRow(lbl + ":", self.bk[key])
        l.addWidget(self.bk_group)

        og = QGroupBox("Output"); of = QFormLayout(og)
        self.fmt = QComboBox(); self.fmt.addItems(['PDF', 'DOCX', 'Both PDF + DOCX'])
        of.addRow("Format:", self.fmt)
        self.out_path = _SavePathPicker("Where to save the report")
        of.addRow("Save to *:", self.out_path)
        l.addWidget(og)

        r = QHBoxLayout(); r.addStretch()
        self.gen_btn = PrimaryButton("⚡  Generate Report"); self.gen_btn.setMinimumWidth(200)
        self.gen_btn.clicked.connect(self._generate); r.addWidget(self.gen_btn)
        l.addLayout(r)
        self.progress = QProgressBar(); self.progress.setRange(0, 0); self.progress.setVisible(False)
        self.progress.setFixedHeight(6); l.addWidget(self.progress)
        self.log = QTextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(150)
        self.log.setStyleSheet("QTextEdit{background:#1A2B45;color:#8FA3BE;font-family:Consolas,monospace;font-size:11px;border-radius:4px;padding:6px;}")
        l.addWidget(self.log)

        l.addStretch(); scroll.setWidget(inner); outer.addWidget(scroll)
        self.tabs.addTab(w, "SCADA & Generate")

    # ── Data plumbing ────────────────────────────────────────────────────
    def _reload_projects(self):
        self.proj_combo.blockSignals(True); self.proj_combo.clear()
        for p in rw.list_projects():
            self.proj_combo.addItem(p['name'], p['id'])
        self.proj_combo.blockSignals(False)

    def showEvent(self, e):
        # refresh the project list every time the page is shown (new projects)
        cur = self.proj_combo.currentData()
        self._reload_projects()
        if cur is not None:
            i = self.proj_combo.findData(cur)
            if i >= 0: self.proj_combo.setCurrentIndex(i)
        super().showEvent(e)

    def _on_project(self, *_):
        pass  # month is opened explicitly via the button

    def _blocks_list(self):
        proj = rw.get_project(self._pid) or {}
        n = int(proj.get('num_blocks') or 15)
        return list(range(1, n + 1))

    def _open_month(self):
        pid = self.proj_combo.currentData()
        if pid is None:
            QMessageBox.information(self, "No project", "Create a project first (Projects page)."); return
        self._pid = pid
        self._year = self.year_spin.value()
        self._month = self.month_combo.currentIndex() + 1
        rw.ensure_report_month(self._pid, self._year, self._month)
        cfg = rw.get_project_config(self._pid)
        self._site_type = (cfg.get('site_type') or 'tashkent')
        self.tk_group.setVisible(self._site_type == 'tashkent')
        self.bk_group.setVisible(self._site_type == 'bukhara')
        self.tabs.setEnabled(True)
        self.month_lbl.setText(f"{self.proj_combo.currentText()} — {MONTHS_EN[self._month]} {self._year} · type: {self._site_type}")
        self._load_all()

    def _load_all(self):
        self._refresh_pm(); self._refresh_excl(); self._refresh_man(); self._refresh_bal()
        m = rw.get_report_month(self._pid, self._year, self._month) or {}
        self.report_number.setText(m.get('report_number', '') or '')
        self.cm_text.setPlainText(m.get('cm_activities', '') or '')
        self.site_visits.setPlainText(m.get('site_visits', '') or '')
        self.recommendations.setPlainText(m.get('recommendations', '') or '')
        self.planned_next.setPlainText(m.get('planned_next', '') or '')
        self.safety_incidents.setPlainText(m.get('safety_incidents', '') or '')

    def _save_narrative(self):
        if self._pid is None: return
        rw.save_report_month(self._pid, self._year, self._month,
            report_number=self.report_number.text().strip(),
            cm_activities=self.cm_text.toPlainText().strip(),
            site_visits=self.site_visits.toPlainText().strip(),
            recommendations=self.recommendations.toPlainText().strip(),
            planned_next=self.planned_next.toPlainText().strip(),
            safety_incidents=self.safety_incidents.toPlainText().strip())
        self.log.append("Saved month narrative.") if hasattr(self, 'log') else None

    # ── PM handlers ──────────────────────────────────────────────────────
    def _refresh_pm(self):
        self.pm_table.setRowCount(0)
        for r in rw.get_pm_activities(self._pid, self._year, self._month):
            row = self.pm_table.rowCount(); self.pm_table.insertRow(row)
            blk = r.get('affected_blocks') or 'All'
            for c, v in enumerate([str(r['id']), r.get('date_from', ''), r.get('date_to', ''),
                                   blk, f"{r.get('hours', 0):.1f}", r.get('description', '')]):
                self.pm_table.setItem(row, c, QTableWidgetItem(str(v)))

    def _add_pm(self):
        dlg = PMDialog(self)
        if dlg.exec_() != QDialog.Accepted: return
        d = dlg.get_data()
        rw.add_pm_activity(self._pid, self._year, self._month, **d)
        self._refresh_pm()

    def _edit_pm(self):
        r = self.pm_table.currentRow()
        if r < 0: return
        pm_id = int(self.pm_table.item(r, 0).text())
        existing = next((x for x in rw.get_pm_activities(self._pid, self._year, self._month) if x['id'] == pm_id), None)
        if not existing: return
        dlg = PMDialog(self, existing=existing)
        if dlg.exec_() != QDialog.Accepted: return
        rw.update_pm_activity(pm_id, **dlg.get_data()); self._refresh_pm()

    def _del_pm(self):
        r = self.pm_table.currentRow()
        if r < 0: return
        rw.delete_pm_activity(int(self.pm_table.item(r, 0).text())); self._refresh_pm()

    # ── Exclusion handlers ───────────────────────────────────────────────
    def _refresh_excl(self):
        self.excl_table.setRowCount(0)
        for e in av.get_exclusions(project_id=self._pid, year=self._year, month=self._month):
            row = self.excl_table.rowCount(); self.excl_table.insertRow(row)
            blk = e.get('affected_blocks') or 'All'
            for c, v in enumerate([str(e['id']), e.get('exclusion_type', ''),
                                   f"{e.get('date_from','')} {e.get('time_from','')}",
                                   f"{e.get('date_to','')} {e.get('time_to','')}", blk,
                                   e.get('description', '')]):
                self.excl_table.setItem(row, c, QTableWidgetItem(str(v)))

    def _add_excl(self):
        dlg = ExclusionDialog(self, blocks_list=self._blocks_list())
        if dlg.exec_() != QDialog.Accepted: return
        av.add_exclusion(**dlg.get_data(), project_id=self._pid, year=self._year, month=self._month)
        self._refresh_excl()

    def _edit_excl(self):
        r = self.excl_table.currentRow()
        if r < 0: return
        eid = int(self.excl_table.item(r, 0).text())
        existing = next((x for x in av.get_exclusions(project_id=self._pid, year=self._year, month=self._month) if x['id'] == eid), None)
        if not existing: return
        dlg = ExclusionDialog(self, blocks_list=self._blocks_list(), existing=existing)
        if dlg.exec_() != QDialog.Accepted: return
        av.update_exclusion(eid, **dlg.get_data(), year=self._year, month=self._month)
        self._refresh_excl()

    def _del_excl(self):
        r = self.excl_table.currentRow()
        if r < 0: return
        av.delete_exclusion(int(self.excl_table.item(r, 0).text())); self._refresh_excl()

    # ── Manual handlers ──────────────────────────────────────────────────
    def _refresh_man(self):
        self.man_table.setRowCount(0)
        for e in av.get_manual_unavailability(project_id=self._pid, year=self._year, month=self._month):
            row = self.man_table.rowCount(); self.man_table.insertRow(row)
            for c, v in enumerate([str(e['id']), str(e.get('block', '')),
                                   str(e.get('lc') or 'both'), e.get('date_from', ''),
                                   e.get('date_to', ''), f"{e.get('downtime_h', 0):.1f}",
                                   e.get('cause', '')]):
                self.man_table.setItem(row, c, QTableWidgetItem(str(v)))

    def _add_man(self):
        dlg = ManualUnavailabilityDialog(self)
        if dlg.exec_() != QDialog.Accepted: return
        av.add_manual_unavailability(**dlg.get_data(), project_id=self._pid, year=self._year, month=self._month)
        self._refresh_man()

    def _del_man(self):
        r = self.man_table.currentRow()
        if r < 0: return
        av.delete_manual_unavailability(int(self.man_table.item(r, 0).text())); self._refresh_man()

    # ── Balancing handlers ───────────────────────────────────────────────
    def _refresh_bal(self):
        self.bal_table.setRowCount(0)
        for b in av.get_balancing_periods(project_id=self._pid, year=self._year, month=self._month):
            row = self.bal_table.rowCount(); self.bal_table.insertRow(row)
            blk = b.get('affected_blocks') or 'All'
            for c, v in enumerate([str(b['id']), b.get('date_from', ''), b.get('date_to', ''),
                                   blk, b.get('note', '')]):
                self.bal_table.setItem(row, c, QTableWidgetItem(str(v)))

    def _add_bal(self):
        dlg = BalancingDialog(self, blocks_list=self._blocks_list())
        if dlg.exec_() != QDialog.Accepted: return
        av.add_balancing_period(**dlg.get_data(), project_id=self._pid, year=self._year, month=self._month)
        self._refresh_bal()

    def _edit_bal(self):
        r = self.bal_table.currentRow()
        if r < 0: return
        bid = int(self.bal_table.item(r, 0).text())
        existing = next((x for x in av.get_balancing_periods(project_id=self._pid, year=self._year, month=self._month) if x['id'] == bid), None)
        if not existing: return
        dlg = BalancingDialog(self, blocks_list=self._blocks_list(), existing=existing)
        if dlg.exec_() != QDialog.Accepted: return
        av.update_balancing_period(bid, **dlg.get_data(), year=self._year, month=self._month)
        self._refresh_bal()

    def _del_bal(self):
        r = self.bal_table.currentRow()
        if r < 0: return
        av.delete_balancing_period(int(self.bal_table.item(r, 0).text())); self._refresh_bal()

    # ── Generate ─────────────────────────────────────────────────────────
    def _pm_as_strings(self):
        out = []
        for r in rw.get_pm_activities(self._pid, self._year, self._month):
            blk = r.get('affected_blocks') or 'all blocks'
            hrs = r.get('hours') or 0
            desc = r.get('description', '') or 'PM'
            out.append(f"Block(s) {blk}: {desc} ({hrs:.0f} h, {r.get('date_from','')}→{r.get('date_to','')})")
        return out

    def _lines(self, text):
        return [ln.strip() for ln in (text or '').splitlines() if ln.strip()]

    def _generate(self):
        if self._pid is None:
            QMessageBox.information(self, "No month", "Open a project + month first."); return
        if not self.out_path.path():
            QMessageBox.warning(self, "Missing", "Choose where to save the report."); return
        self._save_narrative()
        cfg = rw.get_project_config(self._pid)
        proj = rw.get_project(self._pid) or {}
        pd = {}
        for label, key in [('Customer', 'customer'), ('O&M Company', 'om_company'),
                           ('OEM', 'oem'), ('Equipment', 'equipment'),
                           ('Project Capacity', 'project_capacity_str')]:
            if cfg.get(key): pd[label] = cfg[key]

        common = dict(
            site_name=proj.get('name', ''),
            project_details=pd,
            output_path=self.out_path.path(),
            output_format={0: 'pdf', 1: 'docx', 2: 'both'}[self.fmt.currentIndex()],
            plant_capacity_mw=cfg.get('plant_capacity_mw') or None,
            per_block_capacity_mw=cfg.get('per_block_capacity_mw') or None,
            contractual_plant_capacity_mw=cfg.get('contractual_plant_capacity_mw') or None,
            redundancy_threshold_pct=cfg.get('redundancy_threshold_pct') or 100,
            yearly_cycle_target=cfg.get('yearly_cycle_target') or 365.0,
            prepared_by=cfg.get('prepared_by') or None,
            reviewed_by=cfg.get('reviewed_by') or None,
            report_number=self.report_number.text().strip() or None,
            pm_activities=self._pm_as_strings() or None,
            cm_activities=self._lines(self.cm_text.toPlainText()) or None,
            site_visits=self._lines(self.site_visits.toPlainText()) or None,
            recommendations=self._lines(self.recommendations.toPlainText()) or None,
            planned_next_period=self._lines(self.planned_next.toPlainText()) or None,
            exclusions=av.get_exclusions(project_id=self._pid, year=self._year, month=self._month) or None,
            manual_unavailability=av.get_manual_unavailability(project_id=self._pid, year=self._year, month=self._month) or None,
            balancing_periods=av.get_balancing_periods(project_id=self._pid, year=self._year, month=self._month) or None,
        )
        if self._site_type == 'bukhara':
            miss = [k for k in ('site_kpi', 'overall_lc', 'battery_unit', 'meter_daily', 'alarms') if not self.bk[k].path()]
            if miss:
                QMessageBox.warning(self, "Missing SCADA", "Attach: " + ", ".join(miss)); return
            params = dict(common, **{k: v.path() for k, v in self.bk.items() if v.path()})
        else:
            miss = [k for k in ('working_status', 'pcs_cd', 'soc', 'soh_snapshot', 'lc_charge', 'lc_discharge', 'hv_meter', 'alarms') if not self.tk[k].path()]
            if miss:
                QMessageBox.warning(self, "Missing SCADA", "Attach: " + ", ".join(miss)); return
            params = dict(common, **{k: v.path() for k, v in self.tk.items() if v.path()})

        self.log.clear(); self.progress.setVisible(True); self.gen_btn.setEnabled(False)
        self._worker = BlockReportWorker(self._site_type, params)
        self._worker.progress.connect(lambda m: self.log.append(m))
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_err)
        self._worker.start()

    def _on_done(self, out):
        self.progress.setVisible(False); self.gen_btn.setEnabled(True)
        self.log.append(f"✅ Done: {out}")
        QMessageBox.information(self, "Report ready", f"Saved:\n{out}")

    def _on_err(self, msg):
        self.progress.setVisible(False); self.gen_btn.setEnabled(True)
        self.log.append(f"❌ {msg}")
        QMessageBox.critical(self, "Error", msg)


def _wrap(layout):
    w = QWidget(); w.setLayout(layout); return w
