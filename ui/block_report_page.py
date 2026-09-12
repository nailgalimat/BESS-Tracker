"""
ui/block_report_page.py
------------------------
Block-level monthly operations report page.

Unified UI for the two new report engines (Bukhara, Tashkent). The operator
picks a site type, fills in capacity + narrative fields and selects the source
files. The report is written as a Word document — it is edited before it goes
to the customer, and a PDF is printed from Word at the end.
"""
import os
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QPushButton, QTextEdit, QGroupBox, QFileDialog, QMessageBox, QFrame,
    QProgressBar, QScrollArea, QRadioButton, QButtonGroup,
    QDoubleSpinBox, QComboBox, QSpinBox, QDateEdit,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QDialog, QHeaderView,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QDate

from ui.components import PageHeader, PrimaryButton, SecondaryButton
from services.availability_service import (
    get_exclusions, add_exclusion, update_exclusion, delete_exclusion,
    _exclusion_hours,
    get_manual_unavailability, add_manual_unavailability,
    delete_manual_unavailability,
    get_balancing_periods, add_balancing_period,
    update_balancing_period, delete_balancing_period,
)
# Reuse the SCADA-page Add/Edit dialog so the two pages stay in lockstep
from ui.scada_report_page import ExclusionDialog, BalancingDialog


# ── Worker thread that runs the appropriate generator ────────────────────────

class BlockReportWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(object)   # may be path str or [path1, path2]
    error    = pyqtSignal(str)

    def __init__(self, site_type: str, params: dict):
        super().__init__()
        self.site_type = site_type
        self.params = params

    def run(self):
        try:
            cb = lambda msg: self.progress.emit(msg)
            if self.site_type == 'bukhara':
                from services.bukhara_report_service import generate_bukhara_report
                out = generate_bukhara_report(
                    site_kpi_path     = self.params['site_kpi'],
                    overall_lc_path   = self.params['overall_lc'],
                    battery_unit_path = self.params['battery_unit'],
                    meter_daily_path  = self.params['meter_daily'],
                    alarm_path        = self.params['alarms'],
                    output_path       = self.params['output_path'],
                    site_name         = self.params['site_name'],
                    project_details   = self.params['project_details'],
                    plant_capacity_mw       = self.params.get('plant_capacity_mw'),
                    per_block_capacity_mw   = self.params.get('per_block_capacity_mw'),
                    contractual_plant_capacity_mw = self.params.get('contractual_plant_capacity_mw'),
                    redundancy_threshold_pct = self.params.get('redundancy_threshold_pct', 100),
                    pm_activities     = self.params.get('pm_activities') or None,
                    cm_activities     = self.params.get('cm_activities') or None,
                    site_visits       = self.params.get('site_visits') or None,
                    recommendations   = self.params.get('recommendations') or None,
                    planned_next_period = self.params.get('planned_next_period') or None,
                    output_format     = self.params.get('output_format', 'docx'),
                    exclusions        = self.params.get('exclusions') or None,
                    yearly_cycle_target = self.params.get('yearly_cycle_target', 365.0),
                    report_number       = self.params.get('report_number') or None,
                    prepared_by         = self.params.get('prepared_by') or None,
                    reviewed_by         = self.params.get('reviewed_by') or None,
                    breakdown_incidents = self.params.get('breakdown_incidents') or None,
                    progress_callback = cb,
                )
            else:   # tashkent
                from services.tashkent_report_service import generate_tashkent_report
                out = generate_tashkent_report(
                    working_status_path = self.params['working_status'],
                    pcs_cd_path         = self.params['pcs_cd'],
                    soc_path            = self.params['soc'],
                    soh_snapshot_path   = self.params['soh_snapshot'],
                    lc_charge_path      = self.params['lc_charge'],
                    lc_discharge_path   = self.params['lc_discharge'],
                    hv_meter_daily_path = self.params['hv_meter'],
                    alarm_path          = self.params['alarms'],
                    cycles_first_day_path   = self.params.get('cycles_first_day'),
                    cycles_last_day_path    = self.params.get('cycles_last_day'),
                    lc_total_charge_path    = self.params.get('lc_total_charge'),
                    lc_total_discharge_path = self.params.get('lc_total_discharge'),
                    pcs_fault_path          = self.params.get('pcs_fault'),
                    output_path         = self.params['output_path'],
                    site_name           = self.params['site_name'],
                    project_details     = self.params['project_details'],
                    plant_capacity_mw       = self.params.get('plant_capacity_mw'),
                    per_block_capacity_mw   = self.params.get('per_block_capacity_mw'),
                    contractual_plant_capacity_mw = self.params.get('contractual_plant_capacity_mw'),
                    redundancy_threshold_pct = self.params.get('redundancy_threshold_pct', 100),
                    pm_activities     = self.params.get('pm_activities') or None,
                    cm_activities     = self.params.get('cm_activities') or None,
                    site_visits       = self.params.get('site_visits') or None,
                    recommendations   = self.params.get('recommendations') or None,
                    planned_next_period = self.params.get('planned_next_period') or None,
                    output_format     = self.params.get('output_format', 'docx'),
                    exclusions        = self.params.get('exclusions') or None,
                    yearly_cycle_target = self.params.get('yearly_cycle_target', 365.0),
                    report_number       = self.params.get('report_number') or None,
                    prepared_by         = self.params.get('prepared_by') or None,
                    reviewed_by         = self.params.get('reviewed_by') or None,
                    breakdown_incidents = self.params.get('breakdown_incidents') or None,
                    manual_unavailability = self.params.get('manual_unavailability') or None,
                    balancing_periods = self.params.get('balancing_periods') or None,
                    # when set, the month's alarms are kept as equipment history
                    project_id        = self.params.get('project_id'),
                    progress_callback = cb,
                )
            self.finished.emit(out)
        except Exception as e:
            import traceback
            self.error.emit(f"{e}\n{traceback.format_exc()}")


# ── Compact file picker ──────────────────────────────────────────────────────

class _FilePicker(QWidget):
    def __init__(self, placeholder='', filter_str='Excel Files (*.xlsx *.xls *.XLSX)'):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(6)
        self._filter = filter_str
        self.path_input = QLineEdit()
        self.path_input.setPlaceholderText(placeholder)
        self.path_input.setReadOnly(True)
        layout.addWidget(self.path_input)
        btn = QPushButton("📂  Browse")
        btn.setFixedWidth(100); btn.setFixedHeight(30)
        btn.setObjectName("SecondaryButton")
        btn.clicked.connect(self._browse)
        layout.addWidget(btn)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select File", "", self._filter)
        if path: self.path_input.setText(path)

    def path(self): return self.path_input.text().strip()
    def set_path(self, p): self.path_input.setText(p)


class _SavePathPicker(QWidget):
    def __init__(self, placeholder=''):
        super().__init__()
        layout = QHBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(6)
        self.path_input = QLineEdit(); self.path_input.setPlaceholderText(placeholder)
        self.path_input.setReadOnly(True)
        layout.addWidget(self.path_input)
        btn = QPushButton("📂  Browse")
        btn.setFixedWidth(100); btn.setFixedHeight(30); btn.setObjectName("SecondaryButton")
        btn.clicked.connect(self._browse)
        layout.addWidget(btn)

    def _browse(self):
        # Word only: the report is edited before it goes to the customer, and
        # a PDF is printed from Word at the end.
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Report Output",
            "BlockReport.docx", "Word Documents (*.docx)"
        )
        if path:
            if not path.lower().endswith('.docx'):
                path = path.rsplit('.', 1)[0] + '.docx' if '.' in os.path.basename(path) \
                    else path + '.docx'
            self.path_input.setText(path)

    def path(self): return self.path_input.text().strip()
    def set_path(self, p): self.path_input.setText(p)


# ── Manual Unavailability dialog (mirrors the ExclusionDialog UX) ─────────────

class ManualUnavailabilityDialog(QDialog):
    """Operator-recorded downtime entry for the Tashkent report. Feeds the
    4.4.1 table (marked *), the availability heatmap and the contractual
    availability."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Manual Unavailability")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        form = QFormLayout(); form.setSpacing(10)

        self.block_spin = QSpinBox()
        self.block_spin.setRange(1, 999)
        self.block_spin.setFixedWidth(90)
        form.addRow("Block *:", self.block_spin)

        self.lc_combo = QComboBox()
        self.lc_combo.addItems(["Both LCs (whole block)", "LC 1", "LC 2"])
        self.lc_combo.setFixedWidth(200)
        form.addRow("LC:", self.lc_combo)

        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDate(QDate.currentDate())
        self.date_from.setDisplayFormat("yyyy-MM-dd")
        self.date_from.setFixedWidth(130)
        form.addRow("From *:", self.date_from)

        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDate(QDate.currentDate())
        self.date_to.setDisplayFormat("yyyy-MM-dd")
        self.date_to.setFixedWidth(130)
        form.addRow("To *:", self.date_to)

        self.hours_spin = QDoubleSpinBox()
        self.hours_spin.setRange(0.1, 8784.0)
        self.hours_spin.setDecimals(1)
        self.hours_spin.setValue(1.0)
        self.hours_spin.setSuffix(" h")
        self.hours_spin.setFixedWidth(110)
        form.addRow("Downtime *:", self.hours_spin)

        self.cause_edit = QLineEdit()
        self.cause_edit.setPlaceholderText(
            "e.g. HVAC failure — container overheated")
        form.addRow("Cause *:", self.cause_edit)

        layout.addLayout(form)
        btn_row = QHBoxLayout(); btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        ok_btn = QPushButton("Add")
        ok_btn.setDefault(True)
        ok_btn.setObjectName("PrimaryButton")
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

    def get_data(self) -> dict:
        lc_idx = self.lc_combo.currentIndex()   # 0 = both, 1/2 = LC number
        return {
            'block':      int(self.block_spin.value()),
            'lc':         (lc_idx if lc_idx in (1, 2) else None),
            'date_from':  self.date_from.date().toString("yyyy-MM-dd"),
            'date_to':    self.date_to.date().toString("yyyy-MM-dd"),
            'downtime_h': float(self.hours_spin.value()),
            'cause':      self.cause_edit.text().strip(),
        }


# ── Main page ─────────────────────────────────────────────────────────────────

class BlockReportPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        # The open project. Everything this page reads and writes — exclusions,
        # manual downtime, balancing — is scoped to it; without it the page took
        # every project's rows, so a Tashkent report could pick up Bukhara's
        # downtime. None (no project open) keeps the old, unscoped behaviour.
        self._pid = None
        self._build_ui()
        self._on_site_type_changed()
        self._refresh_exclusions()
        self._refresh_manual_unavail()
        self._refresh_balancing()

    def set_current_project(self, project_id):
        self._pid = project_id
        self._refresh_exclusions()
        self._refresh_manual_unavail()
        self._refresh_balancing()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)
        layout.addWidget(PageHeader(
            "Block Performance Report",
            "Sungrow-style monthly operations report — Bukhara or Tashkent format"
        ))

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget(); cl = QVBoxLayout(content)
        cl.setContentsMargins(24, 20, 24, 24); cl.setSpacing(16)

        # ── Site type ────────────────────────────────────────────────────
        type_group = QGroupBox("Report Type")
        tgl = QHBoxLayout()
        self.radio_bukhara = QRadioButton("Bukhara (Sungrow 15-block)")
        self.radio_bukhara.setChecked(True)
        self.radio_tashkent = QRadioButton("Tashkent (70-block LC200)")
        self.radio_grp = QButtonGroup(self)
        self.radio_grp.addButton(self.radio_bukhara, 0)
        self.radio_grp.addButton(self.radio_tashkent, 1)
        self.radio_bukhara.toggled.connect(self._on_site_type_changed)
        tgl.addWidget(self.radio_bukhara); tgl.addWidget(self.radio_tashkent); tgl.addStretch()
        type_group.setLayout(tgl)
        cl.addWidget(type_group)

        # ── Site & capacity info ─────────────────────────────────────────
        site_group = QGroupBox("Site Information & Plant Capacity")
        sf = QFormLayout(); sf.setSpacing(8)
        self.site_name = QLineEdit()
        self.site_name.setPlaceholderText("e.g.  Nur Bukhara 63MW/126MWh BESS Plant")
        sf.addRow("Site / Project Name *:", self.site_name)
        self.customer = QLineEdit(); self.customer.setPlaceholderText("e.g.  MASDAR")
        sf.addRow("Customer:", self.customer)
        self.om_company = QLineEdit(); self.om_company.setPlaceholderText("e.g.  MASDAR MSTS")
        sf.addRow("O&M Company:", self.om_company)
        self.project_capacity_str = QLineEdit()
        self.project_capacity_str.setPlaceholderText("e.g.  63 MW / 126 MWh")
        sf.addRow("Project Capacity (text):", self.project_capacity_str)
        self.equipment = QLineEdit()
        self.equipment.setPlaceholderText(
            "e.g.  ST5015UX-S-2H_V152 (30); SC5500UD-MV_V129 (15); SCC_V152 (15)")
        sf.addRow("Equipment:", self.equipment)

        # Report metadata header fields
        self.report_number = QLineEdit()
        self.report_number.setPlaceholderText("e.g.  001-APRS")
        sf.addRow("Report No.:", self.report_number)
        self.prepared_by = QLineEdit()
        self.prepared_by.setPlaceholderText("Name of preparer")
        sf.addRow("Prepared by:", self.prepared_by)
        self.reviewed_by = QLineEdit()
        self.reviewed_by.setPlaceholderText("Name of reviewer")
        sf.addRow("Reviewed by:", self.reviewed_by)

        # Yearly cycle target (default 365, customer-configurable)
        self.yearly_cycle_target = QDoubleSpinBox()
        self.yearly_cycle_target.setRange(1, 10000)
        self.yearly_cycle_target.setDecimals(0)
        self.yearly_cycle_target.setValue(365)
        self.yearly_cycle_target.setFixedWidth(110)
        sf.addRow("Yearly cycle target:", self.yearly_cycle_target)

        # Capacity numerics
        cap_row = QHBoxLayout()
        cap_row.addWidget(QLabel("Plant total:"))
        self.plant_total_mw = QDoubleSpinBox()
        self.plant_total_mw.setRange(0, 10000); self.plant_total_mw.setDecimals(1)
        self.plant_total_mw.setSuffix(" MW"); self.plant_total_mw.setFixedWidth(110)
        cap_row.addWidget(self.plant_total_mw)
        cap_row.addWidget(QLabel("    Per-block:"))
        self.per_block_mw = QDoubleSpinBox()
        self.per_block_mw.setRange(0, 1000); self.per_block_mw.setDecimals(2)
        self.per_block_mw.setSuffix(" MW"); self.per_block_mw.setFixedWidth(120)
        cap_row.addWidget(self.per_block_mw)
        cap_row.addWidget(QLabel("    Contractual:"))
        self.contractual_mw = QDoubleSpinBox()
        self.contractual_mw.setRange(0, 10000); self.contractual_mw.setDecimals(1)
        self.contractual_mw.setSuffix(" MW"); self.contractual_mw.setFixedWidth(110)
        cap_row.addWidget(self.contractual_mw)
        cap_row.addWidget(QLabel("    Redundancy:"))
        self.redundancy_pct = QDoubleSpinBox()
        self.redundancy_pct.setRange(0, 100); self.redundancy_pct.setDecimals(0)
        self.redundancy_pct.setSuffix(" %"); self.redundancy_pct.setValue(100)
        self.redundancy_pct.setFixedWidth(85)
        cap_row.addWidget(self.redundancy_pct); cap_row.addStretch()
        sf.addRow("Capacity (MW):", cap_row)

        cap_hint = QLabel(
            "When Contractual is set, the plant is considered available whenever "
            "block-aggregate capacity ≥ Contractual MW (SLA-based). If Contractual = 0, "
            "the Redundancy threshold × Plant total is used instead. Leave all at 0 to "
            "skip plant-level availability and report container-level only."
        )
        cap_hint.setStyleSheet("color:#6B7A8D;font-size:11px;font-style:italic;")
        cap_hint.setWordWrap(True)
        sf.addRow("", cap_hint)
        site_group.setLayout(sf)
        cl.addWidget(site_group)

        # ── Bukhara file pickers ─────────────────────────────────────────
        self.bukhara_group = QGroupBox("Bukhara Input Files")
        bf = QFormLayout(); bf.setSpacing(8)
        self.fp_b_site_kpi     = _FilePicker("Bukhara_Main KPI_5 minute data_*.xlsx")
        self.fp_b_overall_lc   = _FilePicker("Bukhara_Overall_LC data_*.xlsx (multi-sheet)")
        self.fp_b_battery_unit = _FilePicker("Overall_Battery Unit Data_*.xlsx (multi-sheet)")
        self.fp_b_meter_daily  = _FilePicker("Main_Meter_daily_*.xlsx")
        self.fp_b_alarms       = _FilePicker("Monthly Alarm Report_*.XLSX")
        bf.addRow("Main KPI (5-min) *:",  self.fp_b_site_kpi)
        bf.addRow("Overall LC data *:",   self.fp_b_overall_lc)
        bf.addRow("Battery Unit data *:", self.fp_b_battery_unit)
        bf.addRow("Main Meter (daily) *:",self.fp_b_meter_daily)
        bf.addRow("Alarm Report *:",       self.fp_b_alarms)
        self.bukhara_group.setLayout(bf)
        cl.addWidget(self.bukhara_group)

        # ── Tashkent file pickers ─────────────────────────────────────────
        self.tashkent_group = QGroupBox("Tashkent Input Files")
        tf = QFormLayout(); tf.setSpacing(8)
        self.fp_t_working   = _FilePicker("LC working status.xlsx")
        self.fp_t_pcs_cd    = _FilePicker("PCS Charge_Discharge status.xlsx")
        self.fp_t_soc       = _FilePicker("SOC <month>.xlsx")
        self.fp_t_soh       = _FilePicker("SOH last day of month.xlsx")
        self.fp_t_lc_chg    = _FilePicker("LC daily charge.xlsx")
        self.fp_t_lc_dis    = _FilePicker("LC Daily discharge.xlsx")
        self.fp_t_hv_meter  = _FilePicker("HV meter daily import and export.xlsx")
        self.fp_t_alarms    = _FilePicker("Alarm report.XLSX")
        self.fp_t_cycles_first = _FilePicker("Cycles  First day of month.xlsx (optional)")
        self.fp_t_cycles_last  = _FilePicker("Cycles last day of the month.xlsx (optional)")
        self.fp_t_lc_total_chg = _FilePicker("LC total charge.xlsx (optional — canonical monthly totalizer)")
        self.fp_t_lc_total_dis = _FilePicker("LC total discharge.xlsx (optional — canonical monthly totalizer)")
        self.fp_t_pcs_fault = _FilePicker("PCS fault status.xlsx (optional — per-unit fault flags)")
        tf.addRow("LC Working Status *:",  self.fp_t_working)
        tf.addRow("PCS Charge/Discharge *:", self.fp_t_pcs_cd)
        tf.addRow("PCS Fault Status (optional):", self.fp_t_pcs_fault)
        tf.addRow("SOC (monthly) *:",       self.fp_t_soc)
        tf.addRow("SOH (snapshot) *:",      self.fp_t_soh)
        tf.addRow("LC Daily Charge *:",     self.fp_t_lc_chg)
        tf.addRow("LC Daily Discharge *:",  self.fp_t_lc_dis)
        tf.addRow("HV Meter (daily) *:",    self.fp_t_hv_meter)
        tf.addRow("Alarm Report *:",         self.fp_t_alarms)
        tf.addRow("Cycles — First Day (optional):", self.fp_t_cycles_first)
        tf.addRow("Cycles — Last Day (optional):",  self.fp_t_cycles_last)
        tf.addRow("LC Total Charge (optional):",    self.fp_t_lc_total_chg)
        tf.addRow("LC Total Discharge (optional):", self.fp_t_lc_total_dis)
        self.tashkent_group.setLayout(tf)
        cl.addWidget(self.tashkent_group)

        # ── Operator narrative content ───────────────────────────────────
        narr_group = QGroupBox("Operator-Supplied Narrative (Optional)")
        nf = QFormLayout(); nf.setSpacing(8)
        def _make_textedit(placeholder, height=70):
            t = QTextEdit(); t.setMaximumHeight(height)
            t.setPlaceholderText(placeholder)
            return t
        self.pm_text = _make_textedit("One activity per line — e.g.\nQ1 PM round on blocks 1-15\n…")
        self.cm_text = _make_textedit("One activity per line — leave blank to auto-generate from alarms")
        self.visits_text = _make_textedit("One entry per line — Sungrow on-site 1-13 April, etc.")
        self.rec_text = _make_textedit("One recommendation per line")
        self.planned_text = _make_textedit("One activity per line — e.g. PM 12-26 May 2026")
        self.breakdowns_text = _make_textedit(
            "One incident per line, pipe-separated:\n"
            "incident | date+time | type | temp solution | final solution | closure date\n"
            "Leave blank if none.", height=80)
        nf.addRow("PM Activities:",       self.pm_text)
        nf.addRow("CM Activities:",       self.cm_text)
        nf.addRow("Site Visits:",         self.visits_text)
        nf.addRow("Recommendations:",     self.rec_text)
        nf.addRow("Planned Next Period:", self.planned_text)
        nf.addRow("Breakdowns / Incidents:", self.breakdowns_text)
        narr_group.setLayout(nf)
        cl.addWidget(narr_group)

        # ── Availability Exclusions ──────────────────────────────────────
        # Mirrors the SCADA Report page. Exclusions are stored globally
        # (no project_id filter) so adding one here is also visible to the
        # SCADA Report page and vice-versa.
        excl_group = QGroupBox(
            "Availability Exclusions (Optional)  —  "
            "scheduled maintenance, grid outages, force majeure, major faults"
        )
        el = QVBoxLayout()
        excl_btn_row = QHBoxLayout()
        add_excl_btn = PrimaryButton("➕  Add Exclusion")
        add_excl_btn.clicked.connect(self._add_exclusion)
        excl_btn_row.addWidget(add_excl_btn)
        edit_excl_btn = SecondaryButton("✏️  Edit Selected")
        edit_excl_btn.clicked.connect(self._edit_exclusion)
        excl_btn_row.addWidget(edit_excl_btn)
        del_excl_btn = SecondaryButton("🗑  Delete Selected")
        del_excl_btn.clicked.connect(self._delete_exclusion)
        excl_btn_row.addWidget(del_excl_btn)
        excl_btn_row.addStretch()
        el.addLayout(excl_btn_row)

        self.excl_table = QTableWidget(0, 7)
        self.excl_table.setHorizontalHeaderLabels([
            "ID","Type","From","To","Hours","Blocks","Description"
        ])
        self.excl_table.hideColumn(0)
        self.excl_table.setColumnWidth(1, 160)
        self.excl_table.setColumnWidth(2, 130)
        self.excl_table.setColumnWidth(3, 130)
        self.excl_table.setColumnWidth(4, 70)
        self.excl_table.setColumnWidth(5, 110)
        self.excl_table.horizontalHeader().setStretchLastSection(True)
        self.excl_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.excl_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.excl_table.setMaximumHeight(180)
        self.excl_table.setAlternatingRowColors(True)
        self.excl_table.doubleClicked.connect(lambda *_: self._edit_exclusion())
        el.addWidget(self.excl_table)

        excl_hint = QLabel(
            "Exclusion hours are capped at the actual plant outage hours and "
            "removed from the denominator of plant-level availability "
            "(mirrors the SCADA Report methodology). If no exclusions are "
            "entered, availability is calculated exactly as before."
        )
        excl_hint.setStyleSheet("color:#6B7A8D;font-size:11px;font-style:italic;")
        excl_hint.setWordWrap(True)
        el.addWidget(excl_hint)
        excl_group.setLayout(el)
        cl.addWidget(excl_group)

        # ── Manual Unavailability (operator-recorded downtime, Tashkent) ──
        # Same UX as exclusions: stored in the database, Add/Delete + table.
        man_group = QGroupBox(
            "Manual Unavailability (Optional — Tashkent report)  —  "
            "downtime the SCADA data did not capture"
        )
        mnl = QVBoxLayout()
        man_btn_row = QHBoxLayout()
        add_man_btn = PrimaryButton("➕  Add Entry")
        add_man_btn.clicked.connect(self._add_manual_unavail)
        man_btn_row.addWidget(add_man_btn)
        del_man_btn = SecondaryButton("🗑  Delete Selected")
        del_man_btn.clicked.connect(self._delete_manual_unavail)
        man_btn_row.addWidget(del_man_btn)
        man_btn_row.addStretch()
        mnl.addLayout(man_btn_row)

        self.man_table = QTableWidget(0, 7)
        self.man_table.setHorizontalHeaderLabels(
            ["ID", "Block", "LC", "From", "To", "Hours", "Cause"])
        self.man_table.hideColumn(0)
        self.man_table.setColumnWidth(1, 60)
        self.man_table.setColumnWidth(2, 100)
        self.man_table.setColumnWidth(3, 110)
        self.man_table.setColumnWidth(4, 110)
        self.man_table.setColumnWidth(5, 70)
        self.man_table.horizontalHeader().setStretchLastSection(True)
        self.man_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.man_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.man_table.setMaximumHeight(160)
        self.man_table.setAlternatingRowColors(True)
        mnl.addWidget(self.man_table)

        man_hint = QLabel(
            "Entries appear in table 4.4.1 (marked *), on the availability "
            "heatmap and in the contractual availability. Rows dated outside "
            "the reported month are ignored automatically, so old entries "
            "don't leak into new reports."
        )
        man_hint.setStyleSheet("color:#6B7A8D;font-size:11px;font-style:italic;")
        man_hint.setWordWrap(True)
        mnl.addWidget(man_hint)
        man_group.setLayout(mnl)
        cl.addWidget(man_group)

        # ── Cycle-Balancing / Rested Blocks (Tashkent) ───────────────────────
        # Blocks intentionally held out of dispatch to balance equivalent full
        # cycles across the fleet. Informational only — does NOT change the
        # 770 MWh contractual availability; it tells the report these low
        # cycles / low RTE are deliberate, so they aren't flagged as faults.
        bal_group = QGroupBox(
            "Cycle-Balancing / Rested Blocks (Optional — Tashkent report)  —  "
            "blocks you deliberately rested to balance cycles"
        )
        bll = QVBoxLayout()
        bal_btn_row = QHBoxLayout()
        add_bal_btn = PrimaryButton("➕  Add Rested Period")
        add_bal_btn.clicked.connect(self._add_balancing)
        bal_btn_row.addWidget(add_bal_btn)
        edit_bal_btn = SecondaryButton("✏️  Edit Selected")
        edit_bal_btn.clicked.connect(self._edit_balancing)
        bal_btn_row.addWidget(edit_bal_btn)
        del_bal_btn = SecondaryButton("🗑  Delete Selected")
        del_bal_btn.clicked.connect(self._delete_balancing)
        bal_btn_row.addWidget(del_bal_btn)
        bal_btn_row.addStretch()
        bll.addLayout(bal_btn_row)

        self.bal_table = QTableWidget(0, 5)
        self.bal_table.setHorizontalHeaderLabels(
            ["ID", "From", "To", "Blocks", "Note"])
        self.bal_table.hideColumn(0)
        self.bal_table.setColumnWidth(1, 110)
        self.bal_table.setColumnWidth(2, 110)
        self.bal_table.setColumnWidth(3, 160)
        self.bal_table.horizontalHeader().setStretchLastSection(True)
        self.bal_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.bal_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.bal_table.setMaximumHeight(160)
        self.bal_table.setAlternatingRowColors(True)
        self.bal_table.doubleClicked.connect(lambda *_: self._edit_balancing())
        bll.addWidget(self.bal_table)

        bal_hint = QLabel(
            "Blocks listed here are reported as *intentionally rested for cycle "
            "balancing* — their lower cycles / RTE are shown as deliberate, not "
            "as underperformance. Availability stays on the 770 MWh basis. Rows "
            "dated outside the reported month are ignored automatically."
        )
        bal_hint.setStyleSheet("color:#6B7A8D;font-size:11px;font-style:italic;")
        bal_hint.setWordWrap(True)
        bll.addWidget(bal_hint)
        bal_group.setLayout(bll)
        cl.addWidget(bal_group)

        # ── Output ────────────────────────────────────────────────────────
        out_group = QGroupBox("Output")
        of = QFormLayout(); of.setSpacing(8)
        self.fp_output = _SavePathPicker("Where to save the report (.docx)")
        of.addRow("Save Report to *:", self.fp_output)
        out_group.setLayout(of)
        cl.addWidget(out_group)

        # ── Generate button + progress ────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.clear_btn = SecondaryButton("🔄  Clear")
        self.clear_btn.clicked.connect(self._clear_all)
        btn_row.addWidget(self.clear_btn)
        self.gen_btn = PrimaryButton("⚡  Generate Report")
        self.gen_btn.setFixedHeight(42); self.gen_btn.setMinimumWidth(220)
        self.gen_btn.clicked.connect(self._generate)
        btn_row.addWidget(self.gen_btn)
        cl.addLayout(btn_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0); self.progress_bar.setVisible(False)
        self.progress_bar.setFixedHeight(6)
        cl.addWidget(self.progress_bar)

        log_group = QGroupBox("Progress Log")
        ll = QVBoxLayout()
        self.log_output = QTextEdit(); self.log_output.setReadOnly(True)
        self.log_output.setMaximumHeight(160)
        self.log_output.setStyleSheet(
            "QTextEdit{background:#1A2B45;color:#8FA3BE;font-family:Consolas,monospace;"
            "font-size:11px;border:none;padding:8px;border-radius:4px;}"
        )
        ll.addWidget(self.log_output); log_group.setLayout(ll)
        cl.addWidget(log_group)

        cl.addStretch()
        scroll.setWidget(content); layout.addWidget(scroll)

    # ── Show/hide the file pickers depending on radio selection ───────────

    def _on_site_type_changed(self):
        is_bukhara = self.radio_bukhara.isChecked()
        self.bukhara_group.setVisible(is_bukhara)
        self.tashkent_group.setVisible(not is_bukhara)

    def _site_type(self):
        return 'bukhara' if self.radio_bukhara.isChecked() else 'tashkent'

    # ── Helpers ──────────────────────────────────────────────────────────

    def _log(self, msg):
        self.log_output.append(f"▸  {msg}")
        self.log_output.verticalScrollBar().setValue(
            self.log_output.verticalScrollBar().maximum())

    def _clear_all(self):
        for w in (self.fp_b_site_kpi, self.fp_b_overall_lc, self.fp_b_battery_unit,
                  self.fp_b_meter_daily, self.fp_b_alarms,
                  self.fp_t_working, self.fp_t_pcs_cd, self.fp_t_soc, self.fp_t_soh,
                  self.fp_t_lc_chg, self.fp_t_lc_dis, self.fp_t_hv_meter, self.fp_t_alarms,
                  self.fp_t_cycles_first, self.fp_t_cycles_last,
                  self.fp_t_lc_total_chg, self.fp_t_lc_total_dis,
                  self.fp_t_pcs_fault,
                  self.fp_output):
            w.set_path("")
        for w in (self.site_name, self.customer, self.om_company,
                  self.project_capacity_str, self.equipment,
                  self.report_number, self.prepared_by, self.reviewed_by):
            w.clear()
        for w in (self.plant_total_mw, self.per_block_mw,
                  self.contractual_mw):
            w.setValue(0)
        self.redundancy_pct.setValue(100)
        self.yearly_cycle_target.setValue(365)
        for w in (self.pm_text, self.cm_text, self.visits_text,
                  self.rec_text, self.planned_text, self.breakdowns_text):
            w.clear()
        self.log_output.clear()

    def _gather_lines(self, text_edit) -> list:
        raw = text_edit.toPlainText().strip()
        if not raw: return []
        return [ln.strip() for ln in raw.split('\n') if ln.strip()]

    def _gather_breakdowns(self) -> list:
        """Parse the Breakdowns/Incidents textarea.

        Format: one incident per line, fields pipe-separated:
            incident | date+time | type | temp solution | final solution | closure
        Missing trailing fields are treated as empty.
        """
        raw = self.breakdowns_text.toPlainText().strip()
        if not raw:
            return []
        keys = ('incident', 'date_time', 'breakdown_type',
                'temporary_solution', 'final_solution', 'closure_date')
        out = []
        for ln in raw.split('\n'):
            ln = ln.strip()
            if not ln: continue
            parts = [p.strip() for p in ln.split('|')]
            out.append({k: (parts[i] if i < len(parts) else '')
                          for i, k in enumerate(keys)})
        return out

    # ── Exclusions (shared globally with SCADA Report page) ──────────────

    def _refresh_exclusions(self):
        """Reload the table from the database."""
        self.excl_table.setRowCount(0)
        try:
            exclusions = get_exclusions(project_id=self._pid)
        except Exception:
            exclusions = []
        for exc in exclusions:
            row = self.excl_table.rowCount()
            self.excl_table.insertRow(row)
            t_from = exc.get("time_from", "00:00") or "00:00"
            t_to   = exc.get("time_to",   "23:59") or "23:59"
            hours  = _exclusion_hours(exc)
            blocks_val = exc.get("affected_blocks", "")
            if not blocks_val or blocks_val.lower() in ("all", "all blocks"):
                blocks_display = "All blocks"
            else:
                n = len([b for b in blocks_val.split(",") if b.strip()])
                blocks_display = (f"{n} block(s): {blocks_val[:30]}"
                                   f"{'…' if len(blocks_val) > 30 else ''}")
            for col, val in enumerate([
                str(exc["id"]),
                exc.get("exclusion_type", ""),
                f"{exc.get('date_from','')} {t_from}",
                f"{exc.get('date_to','')} {t_to}",
                f"{hours:.1f}",
                blocks_display,
                exc.get("description", ""),
            ]):
                self.excl_table.setItem(row, col, QTableWidgetItem(val))

    def _project_block_ids(self) -> list:
        """Block numbers 1..N for the plant, matching the SCADA block index the
        monthly report uses.

        The report reads block_id straight from the SCADA 'LC200 BB.CC' columns,
        which are numbered contiguously 1..70 for Tashkent. The exclusion /
        manual-unavailability selector must present the SAME 1..N numbering, or a
        chosen block won't line up with the report.

        The previous (zone-1)*num_blocks + block flattening left gaps whenever a
        zone held fewer than num_blocks blocks — Tashkent zones 3 and 8 have 7,
        not 8 — producing IDs 1..72 with 24 and 64 missing. So N is simply the
        count of distinct (zone, block) pairs in the project."""
        try:
            from database.db_manager import get_connection
            c = get_connection()
            try:
                n = c.execute(
                    "SELECT COUNT(*) FROM ("
                    "  SELECT DISTINCT zone_number, block_number FROM containers"
                    "  WHERE block_number IS NOT NULL"
                    "    AND project_id = (SELECT id FROM projects ORDER BY id LIMIT 1))"
                ).fetchone()[0]
                if n:
                    return list(range(1, n + 1))
            finally:
                c.close()
        except Exception as e:
            print(f"Warning: could not load project blocks: {e}")
        return list(range(1, 16))   # sensible default: 15 blocks (Bukhara)

    def _add_exclusion(self):
        from PyQt5.QtCore import QDate
        dlg = ExclusionDialog(self, blocks_list=self._project_block_ids())
        if dlg.exec_() != QDialog.Accepted:
            return
        data = dlg.get_data()
        if not self._validate_exclusion(data):
            return
        try:
            add_exclusion(**data, project_id=self._pid)
            self._refresh_exclusions()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to add:\n{e}")

    def _edit_exclusion(self):
        row = self.excl_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Select",
                "Click an exclusion row first, then Edit.")
            return
        id_item = self.excl_table.item(row, 0)
        if not id_item:
            return
        try:
            exc_id = int(id_item.text())
        except (TypeError, ValueError):
            return
        existing = next((e for e in get_exclusions(project_id=self._pid)
                         if int(e.get("id", -1)) == exc_id), None)
        if not existing:
            QMessageBox.warning(self, "Not found",
                "This exclusion could not be loaded (was it deleted?).")
            self._refresh_exclusions()
            return
        dlg = ExclusionDialog(self, blocks_list=self._project_block_ids(),
                              existing=existing)
        if dlg.exec_() != QDialog.Accepted:
            return
        data = dlg.get_data()
        if not self._validate_exclusion(data, exclude_id=exc_id):
            return
        try:
            update_exclusion(exc_id, **data)
            self._refresh_exclusions()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to update:\n{e}")

    def _validate_exclusion(self, data: dict, exclude_id: int = None) -> bool:
        """Validation on top of the dialog: end ≥ start, duplicate detection,
        unusually large span warning."""
        from datetime import datetime
        try:
            start_dt = datetime.strptime(
                f"{data['date_from']} {data['time_from']}", "%Y-%m-%d %H:%M")
            end_dt   = datetime.strptime(
                f"{data['date_to']} {data['time_to']}",   "%Y-%m-%d %H:%M")
        except ValueError:
            QMessageBox.warning(self, "Invalid timestamp",
                "Could not parse the From / To date+time values.")
            return False
        if end_dt < start_dt:
            QMessageBox.warning(self, "Invalid range",
                "The exclusion's end datetime must be on or after its start.")
            return False
        span_days = (end_dt - start_dt).total_seconds() / 86400.0
        if span_days > 14:
            ans = QMessageBox.question(self, "Unusually long exclusion",
                f"This exclusion spans {span_days:.1f} days. "
                "That's unusually long — proceed anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ans != QMessageBox.Yes:
                return False
        # Duplicate detection
        existing = []
        try:
            existing = get_exclusions(project_id=self._pid)
        except Exception:
            pass
        for exc in existing:
            if exclude_id is not None and int(exc.get('id', -1)) == exclude_id:
                continue   # don't flag the row being edited as a duplicate of itself
            same = (
                exc.get('exclusion_type') == data['exclusion_type']
                and exc.get('date_from')  == data['date_from']
                and exc.get('date_to')    == data['date_to']
                and (exc.get('time_from') or '00:00') == data['time_from']
                and (exc.get('time_to')   or '23:59') == data['time_to']
                and (exc.get('affected_blocks') or '') ==
                    (data.get('affected_blocks') or '')
            )
            if same:
                ans = QMessageBox.question(self, "Duplicate exclusion",
                    "An exclusion with identical type, date+time range, "
                    "and affected blocks already exists. Add another?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                return ans == QMessageBox.Yes
        return True

    def _delete_exclusion(self):
        row = self.excl_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Select",
                "Click an exclusion row first.")
            return
        id_item = self.excl_table.item(row, 0)
        if not id_item:
            return
        excl_id = int(id_item.text())
        ans = QMessageBox.question(self, "Delete?",
            "Delete this exclusion?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ans == QMessageBox.Yes:
            delete_exclusion(excl_id)
            self._refresh_exclusions()

    # ── Manual Unavailability (operator-recorded downtime) ────────────────

    def _refresh_manual_unavail(self):
        """Reload the manual-unavailability table from the database."""
        self.man_table.setRowCount(0)
        try:
            entries = get_manual_unavailability(project_id=self._pid)
        except Exception:
            entries = []
        for e in entries:
            row = self.man_table.rowCount()
            self.man_table.insertRow(row)
            lc_disp = f"LC {int(e['lc'])}" if e.get('lc') else "Both LCs"
            for col, val in enumerate([
                str(e["id"]),
                str(e.get("block", "")),
                lc_disp,
                e.get("date_from", ""),
                e.get("date_to", ""),
                f"{float(e.get('downtime_h') or 0):.1f}",
                e.get("cause", ""),
            ]):
                self.man_table.setItem(row, col, QTableWidgetItem(val))

    def _add_manual_unavail(self):
        dlg = ManualUnavailabilityDialog(self)
        if dlg.exec_() != QDialog.Accepted:
            return
        data = dlg.get_data()
        from datetime import datetime as _dt
        if data['date_to'] < data['date_from']:
            QMessageBox.warning(self, "Invalid range",
                "The end date must be on or after the start date.")
            return
        n_days = (_dt.strptime(data['date_to'], "%Y-%m-%d")
                  - _dt.strptime(data['date_from'], "%Y-%m-%d")).days + 1
        if data['downtime_h'] > n_days * 24:
            QMessageBox.warning(self, "Too many hours",
                f"{data['downtime_h']:.1f} h does not fit into the "
                f"{n_days}-day range (maximum {n_days * 24} h).")
            return
        if not data['cause']:
            QMessageBox.warning(self, "Missing cause",
                "Please describe the cause of the downtime.")
            return
        try:
            add_manual_unavailability(**data, project_id=self._pid)
            self._refresh_manual_unavail()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to add:\n{e}")

    def _delete_manual_unavail(self):
        row = self.man_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Select",
                "Click an entry row first.")
            return
        id_item = self.man_table.item(row, 0)
        if not id_item:
            return
        ans = QMessageBox.question(self, "Delete?",
            "Delete this manual unavailability entry?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ans == QMessageBox.Yes:
            delete_manual_unavailability(int(id_item.text()))
            self._refresh_manual_unavail()

    # ── Cycle-balancing / rested blocks ──────────────────────────────────

    def _refresh_balancing(self):
        self.bal_table.setRowCount(0)
        try:
            periods = get_balancing_periods(project_id=self._pid)
        except Exception:
            periods = []
        for b in periods:
            row = self.bal_table.rowCount()
            self.bal_table.insertRow(row)
            blocks_val = (b.get("affected_blocks") or "").strip()
            if not blocks_val or blocks_val.lower() in ("all", "all blocks"):
                blocks_display = "All blocks"
            else:
                n = len([x for x in blocks_val.split(",") if x.strip()])
                blocks_display = (f"{n}: {blocks_val[:26]}"
                                   f"{'…' if len(blocks_val) > 26 else ''}")
            for col, val in enumerate([
                str(b["id"]),
                b.get("date_from", ""),
                b.get("date_to", ""),
                blocks_display,
                b.get("note", ""),
            ]):
                self.bal_table.setItem(row, col, QTableWidgetItem(val))

    def _validate_balancing(self, data: dict) -> bool:
        from datetime import datetime
        try:
            d1 = datetime.strptime(data["date_from"], "%Y-%m-%d")
            d2 = datetime.strptime(data["date_to"], "%Y-%m-%d")
        except ValueError:
            QMessageBox.warning(self, "Invalid", "Dates must be valid.")
            return False
        if d2 < d1:
            QMessageBox.warning(self, "Invalid",
                "'Rested to' must be on or after 'Rested from'.")
            return False
        if not (data.get("affected_blocks") or "").strip():
            ans = QMessageBox.question(self, "All blocks?",
                "No blocks selected — this marks the WHOLE fleet as rested. "
                "Continue?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ans != QMessageBox.Yes:
                return False
        return True

    def _add_balancing(self):
        dlg = BalancingDialog(self, blocks_list=self._project_block_ids())
        if dlg.exec_() != QDialog.Accepted:
            return
        data = dlg.get_data()
        if not self._validate_balancing(data):
            return
        try:
            add_balancing_period(**data, project_id=self._pid)
            self._refresh_balancing()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to add:\n{e}")

    def _edit_balancing(self):
        row = self.bal_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Select",
                "Click a rested-period row first, then Edit.")
            return
        id_item = self.bal_table.item(row, 0)
        if not id_item:
            return
        try:
            bal_id = int(id_item.text())
        except (TypeError, ValueError):
            return
        existing = next((b for b in get_balancing_periods(project_id=self._pid)
                         if int(b.get("id", -1)) == bal_id), None)
        if not existing:
            QMessageBox.warning(self, "Not found",
                "This period could not be loaded (was it deleted?).")
            self._refresh_balancing()
            return
        dlg = BalancingDialog(self, blocks_list=self._project_block_ids(),
                              existing=existing)
        if dlg.exec_() != QDialog.Accepted:
            return
        data = dlg.get_data()
        if not self._validate_balancing(data):
            return
        try:
            update_balancing_period(bal_id, **data)
            self._refresh_balancing()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to update:\n{e}")

    def _delete_balancing(self):
        row = self.bal_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Select", "Click a row first.")
            return
        id_item = self.bal_table.item(row, 0)
        if not id_item:
            return
        ans = QMessageBox.question(self, "Delete?",
            "Delete this rested / balancing period?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ans == QMessageBox.Yes:
            delete_balancing_period(int(id_item.text()))
            self._refresh_balancing()

    def _validate(self) -> bool:
        missing = []
        if not self.site_name.text().strip():
            missing.append("Site / Project Name")
        if not self.fp_output.path():
            missing.append("Output path")
        site = self._site_type()
        required_pickers = []
        if site == 'bukhara':
            required_pickers = [
                ('Main KPI',          self.fp_b_site_kpi),
                ('Overall LC data',   self.fp_b_overall_lc),
                ('Battery Unit data', self.fp_b_battery_unit),
                ('Main Meter daily',  self.fp_b_meter_daily),
                ('Alarm Report',      self.fp_b_alarms),
            ]
        else:
            required_pickers = [
                ('LC Working Status',  self.fp_t_working),
                ('PCS Charge/Discharge', self.fp_t_pcs_cd),
                ('SOC',                self.fp_t_soc),
                ('SOH snapshot',       self.fp_t_soh),
                ('LC Daily Charge',    self.fp_t_lc_chg),
                ('LC Daily Discharge', self.fp_t_lc_dis),
                ('HV Meter daily',     self.fp_t_hv_meter),
                ('Alarm Report',       self.fp_t_alarms),
            ]
        for label, fp in required_pickers:
            if not fp.path():
                missing.append(label)
            elif not os.path.exists(fp.path()):
                QMessageBox.warning(self, "File Not Found",
                    f"{label} file not found:\n{fp.path()}")
                return False
        if missing:
            QMessageBox.warning(self, "Missing Fields",
                "Please fill in:\n• " + "\n• ".join(missing))
            return False
        return True

    def _output_format(self):
        return 'docx'

    # ── Generate ─────────────────────────────────────────────────────────

    def _generate(self):
        if not self._validate(): return
        self.gen_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.log_output.clear()
        self._log(f"Starting {self._site_type()} report generation...")

        # Load current exclusions (shared globally with the SCADA Report page)
        try:
            exclusions = get_exclusions(project_id=self._pid)
        except Exception:
            exclusions = []
        if exclusions:
            self._log(f"Including {len(exclusions)} availability exclusion(s)")

        # Manual unavailability entries (stored in the DB, like exclusions).
        # Entries dated outside the reported month are filtered out by the
        # report engine itself.
        try:
            manual_unavail = get_manual_unavailability(project_id=self._pid)
        except Exception:
            manual_unavail = []
        if manual_unavail:
            self._log(f"Including {len(manual_unavail)} manual "
                      f"unavailability entrie(s)")

        # Cycle-balancing / rested-blocks periods (informational; the report
        # filters to the reported month itself).
        try:
            balancing = get_balancing_periods(project_id=self._pid)
        except Exception:
            balancing = []
        if balancing:
            self._log(f"Including {len(balancing)} cycle-balancing period(s)")

        # Build the project_details dict
        project_details = {}
        if self.customer.text().strip():
            project_details['Customer'] = self.customer.text().strip()
        if self.om_company.text().strip():
            project_details['O&M Company'] = self.om_company.text().strip()
        if self.project_capacity_str.text().strip():
            project_details['Project Capacity'] = self.project_capacity_str.text().strip()
        if self.equipment.text().strip():
            project_details['Equipment'] = self.equipment.text().strip()

        common = {
            'site_name':       self.site_name.text().strip(),
            'project_details': project_details,
            'output_path':     self.fp_output.path(),
            'output_format':   self._output_format(),
            'plant_capacity_mw':     self.plant_total_mw.value() or None,
            'per_block_capacity_mw': self.per_block_mw.value() or None,
            'contractual_plant_capacity_mw': self.contractual_mw.value() or None,
            'redundancy_threshold_pct': self.redundancy_pct.value(),
            'pm_activities':       self._gather_lines(self.pm_text),
            'cm_activities':       self._gather_lines(self.cm_text),
            'site_visits':         self._gather_lines(self.visits_text),
            'recommendations':     self._gather_lines(self.rec_text),
            'planned_next_period': self._gather_lines(self.planned_text),
            'exclusions':          exclusions or None,
            'yearly_cycle_target': self.yearly_cycle_target.value(),
            'report_number':       self.report_number.text().strip(),
            'prepared_by':         self.prepared_by.text().strip(),
            'reviewed_by':         self.reviewed_by.text().strip(),
            'breakdown_incidents': self._gather_breakdowns(),
            'manual_unavailability': manual_unavail,
            'balancing_periods':   balancing or None,
        }

        if self._site_type() == 'bukhara':
            params = dict(common,
                site_kpi     = self.fp_b_site_kpi.path(),
                overall_lc   = self.fp_b_overall_lc.path(),
                battery_unit = self.fp_b_battery_unit.path(),
                meter_daily  = self.fp_b_meter_daily.path(),
                alarms       = self.fp_b_alarms.path(),
            )
        else:
            params = dict(common,
                working_status    = self.fp_t_working.path(),
                pcs_cd            = self.fp_t_pcs_cd.path(),
                soc               = self.fp_t_soc.path(),
                soh_snapshot      = self.fp_t_soh.path(),
                lc_charge         = self.fp_t_lc_chg.path(),
                lc_discharge      = self.fp_t_lc_dis.path(),
                hv_meter          = self.fp_t_hv_meter.path(),
                alarms            = self.fp_t_alarms.path(),
                cycles_first_day   = self.fp_t_cycles_first.path() or None,
                cycles_last_day    = self.fp_t_cycles_last.path()  or None,
                lc_total_charge    = self.fp_t_lc_total_chg.path() or None,
                lc_total_discharge = self.fp_t_lc_total_dis.path() or None,
                pcs_fault          = self.fp_t_pcs_fault.path() or None,
            )

        self._worker = BlockReportWorker(self._site_type(), params)
        self._worker.progress.connect(self._log)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_finished(self, output):
        self.progress_bar.setVisible(False)
        self.gen_btn.setEnabled(True)
        paths = output if isinstance(output, list) else [output]
        for p in paths:
            self._log(f"✅  Saved: {p}")
        reply = QMessageBox.question(self, "Report Generated",
            "Report saved successfully!\n\n" + "\n".join(paths) +
            "\n\nOpen the first one now?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if reply == QMessageBox.Yes and paths:
            import subprocess, sys
            try:
                if sys.platform == 'win32':
                    os.startfile(paths[0])
                elif sys.platform == 'darwin':
                    subprocess.run(['open', paths[0]])
                else:
                    subprocess.run(['xdg-open', paths[0]])
            except Exception:
                pass

    def _on_error(self, error_msg):
        self.progress_bar.setVisible(False)
        self.gen_btn.setEnabled(True)
        self._log(f"❌  Error: {error_msg[:200]}")
        QMessageBox.critical(self, "Report Failed",
            f"Failed to generate report:\n\n{error_msg[:1500]}")
