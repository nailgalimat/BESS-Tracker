"""
ui/scada_report_page.py
------------------------
SCADA Report page — file pickers + exclusions table + generate.

Two availability metrics are calculated:
  Raw       — pure (RUNNING+STANDBY)/total
  Technical — excludes scheduled maintenance, grid outages, force majeure, major faults

Exclusions are entered manually before generating the report.
"""

import os
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit,
    QGroupBox, QFileDialog, QMessageBox, QFrame,
    QProgressBar, QSizePolicy, QScrollArea,
    QTableWidget, QTableWidgetItem, QComboBox, QDateEdit,
    QHeaderView, QAbstractItemView, QDialog, QDialogButtonBox,
    QSpinBox, QDoubleSpinBox, QListWidget, QListWidgetItem,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QDate, QTime, QSize
from PyQt5.QtGui import QFont, QColor

from ui.components import PageHeader, PrimaryButton, SecondaryButton
from services.availability_service import (
    get_exclusions, add_exclusion, delete_exclusion, EXCLUSION_TYPES
)


# ── Background worker thread ───────────────────────────────────────────────────

class ReportWorker(QThread):
    progress  = pyqtSignal(str)
    finished  = pyqtSignal(str)
    error     = pyqtSignal(str)

    def __init__(self, params: dict):
        super().__init__()
        self.params = params

    def run(self):
        try:
            from services.scada_report_service import generate_scada_report
            generate_scada_report(
                working_status_path   = self.params['working_status'],
                charge_discharge_path = self.params['charge_discharge'],
                alarm_path            = self.params['alarms'],
                exported_path         = self.params['exported'],
                imported_path         = self.params['imported'],
                output_path           = self.params['output'],
                site_name             = self.params['site_name'],
                progress_callback     = lambda msg: self.progress.emit(msg),
                exclusions            = self.params.get('exclusions') or None,
                lc_charge_path        = self.params.get('lc_charge') or None,
                lc_discharge_path     = self.params.get('lc_discharge') or None,
                cycle_targets_path    = self.params.get('cycle_targets') or None,
                default_cycle_target  = self.params.get('default_target', 1),
                project_blocks        = self.params.get('project_blocks'),
                plant_capacity_mw     = self.params.get('plant_capacity_mw'),
                per_block_capacity_mw = self.params.get('per_block_capacity_mw'),
                redundancy_threshold_pct = self.params.get('redundancy_threshold_pct', 100),
                contractual_plant_capacity_mw = self.params.get('contractual_plant_capacity_mw'),
            )
            self.finished.emit(self.params['output'])
        except Exception as e:
            self.error.emit(str(e))


# ── File picker row helper ─────────────────────────────────────────────────────

class FilePicker(QWidget):
    def __init__(self, label: str, placeholder: str = '', save_mode: bool = False,
                 filter_str: str = 'Excel Files (*.xlsx *.xls *.XLSX)',
                 parent=None):
        super().__init__(parent)
        self._save_mode  = save_mode
        self._filter_str = filter_str
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.path_input = QLineEdit()
        self.path_input.setPlaceholderText(placeholder)
        self.path_input.setReadOnly(True)
        layout.addWidget(self.path_input)
        browse_btn = QPushButton("📂  Browse")
        browse_btn.setFixedWidth(100)
        browse_btn.setFixedHeight(30)
        browse_btn.setObjectName("SecondaryButton")
        browse_btn.clicked.connect(self._browse)
        layout.addWidget(browse_btn)

    def _browse(self):
        if self._save_mode:
            path, _ = QFileDialog.getSaveFileName(
                self, "Save PDF Report", "BESS_Operations_Report.pdf",
                "PDF Files (*.pdf)"
            )
        else:
            path, _ = QFileDialog.getOpenFileName(
                self, "Select File", "", self._filter_str
            )
        if path:
            self.path_input.setText(path)

    def path(self) -> str:
        return self.path_input.text().strip()

    def set_path(self, p: str):
        self.path_input.setText(p)


# ── Exclusion entry dialog ────────────────────────────────────────────────────

class ExclusionDialog(QDialog):
    def __init__(self, parent=None, blocks_list=None, existing=None):
        """
        blocks_list: optional list of int block IDs. If None, the affected-
                     blocks selector is hidden (legacy behaviour).
        existing:    optional exclusion dict to pre-fill the form (EDIT mode).
        """
        super().__init__(parent)
        self._blocks_list = blocks_list or []
        self._existing = existing or None
        is_edit = self._existing is not None
        self.setWindowTitle("Edit Availability Exclusion" if is_edit
                            else "Add Availability Exclusion")
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        form   = QFormLayout()
        form.setSpacing(10)

        self.type_combo = QComboBox()
        self.type_combo.addItems(EXCLUSION_TYPES)
        form.addRow("Type *:", self.type_combo)

        # From date + time
        from PyQt5.QtWidgets import QTimeEdit
        from_row = QHBoxLayout()
        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDate(QDate.currentDate())
        self.date_from.setDisplayFormat("yyyy-MM-dd")
        from_row.addWidget(self.date_from)
        from_row.addWidget(QLabel(" at "))
        self.time_from = QTimeEdit()
        self.time_from.setDisplayFormat("HH:mm")
        self.time_from.setTime(QTime(0, 0))
        self.time_from.setFixedWidth(80)
        from_row.addWidget(self.time_from)
        from_row.addStretch()
        form.addRow("From *:", from_row)

        # To date + time
        to_row = QHBoxLayout()
        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDate(QDate.currentDate())
        self.date_to.setDisplayFormat("yyyy-MM-dd")
        to_row.addWidget(self.date_to)
        to_row.addWidget(QLabel(" at "))
        self.time_to = QTimeEdit()
        self.time_to.setDisplayFormat("HH:mm")
        self.time_to.setTime(QTime(23, 59))
        self.time_to.setFixedWidth(80)
        to_row.addWidget(self.time_to)
        to_row.addStretch()
        form.addRow("To *:", to_row)

        # Quick fill button: full day
        full_day_btn = QPushButton("Set times to full day (00:00 → 23:59)")
        full_day_btn.setStyleSheet("padding:4px 12px;")
        full_day_btn.clicked.connect(self._set_full_day)
        form.addRow("", full_day_btn)

        # Affected Blocks selector — drag to rubber-band select, Ctrl/Shift to
        # add/extend. The selected (highlighted) tiles are the affected blocks.
        if self._blocks_list:
            self.blocks_list_widget = QListWidget()
            self.blocks_list_widget.setViewMode(QListWidget.IconMode)
            self.blocks_list_widget.setResizeMode(QListWidget.Adjust)
            self.blocks_list_widget.setMovement(QListWidget.Static)
            self.blocks_list_widget.setSelectionMode(
                QAbstractItemView.ExtendedSelection)
            self.blocks_list_widget.setSelectionRectVisible(True)
            self.blocks_list_widget.setDragEnabled(False)
            self.blocks_list_widget.setUniformItemSizes(True)
            self.blocks_list_widget.setSpacing(3)
            self.blocks_list_widget.setGridSize(QSize(80, 26))
            self.blocks_list_widget.setMinimumHeight(150)
            self.blocks_list_widget.setMaximumHeight(230)
            self.blocks_list_widget.setStyleSheet(
                "QListWidget{background:#FFFFFF;border:1px solid #D5DBE3;"
                "border-radius:6px;}"
                "QListWidget::item{border:1px solid #D5DBE3;border-radius:4px;"
                "padding:3px 0;color:#2B3A4B;}"
                "QListWidget::item:selected{background:#0071E3;color:#FFFFFF;"
                "border-color:#0071E3;}"
            )
            for b in self._blocks_list:
                it = QListWidgetItem(f"Block {int(b)}")
                it.setData(Qt.UserRole, int(b))
                it.setTextAlignment(Qt.AlignCenter)
                it.setSizeHint(QSize(76, 22))
                self.blocks_list_widget.addItem(it)

            hint = QLabel("Tip: drag to select a range · Ctrl-click to add · "
                          "Shift-click for a run")
            hint.setStyleSheet("color:#8A97A6;font-size:11px;")

            btn_row = QHBoxLayout()
            self.btn_all = QPushButton("Select all")
            self.btn_all.clicked.connect(self._select_all_blocks)
            self.btn_none = QPushButton("Clear")
            self.btn_none.clicked.connect(self._clear_block_selection)
            self.blocks_summary_label = QLabel()
            self.blocks_summary_label.setStyleSheet(
                "color:#6B7A8D;font-style:italic;")
            btn_row.addWidget(self.btn_all)
            btn_row.addWidget(self.btn_none)
            btn_row.addStretch()
            btn_row.addWidget(self.blocks_summary_label)

            self.blocks_list_widget.itemSelectionChanged.connect(
                self._update_blocks_summary)
            form.addRow("Affected Blocks:", self.blocks_list_widget)
            form.addRow("", hint)
            form.addRow("", btn_row)

        self.desc_input = QTextEdit()
        self.desc_input.setMaximumHeight(70)
        self.desc_input.setPlaceholderText("Reason / description...")
        form.addRow("Description:", self.desc_input)

        layout.addLayout(form)

        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        if is_edit:
            btns.button(QDialogButtonBox.Save).setText("Update")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        if is_edit:
            self._prefill(self._existing)
        self._update_blocks_summary()

    # ── Pre-fill for edit ────────────────────────────────────────────────────
    def _prefill(self, exc: dict):
        et = exc.get("exclusion_type", "")
        i = self.type_combo.findText(et)
        if i >= 0:
            self.type_combo.setCurrentIndex(i)
        df = QDate.fromString(exc.get("date_from", ""), "yyyy-MM-dd")
        if df.isValid():
            self.date_from.setDate(df)
        dt = QDate.fromString(exc.get("date_to", ""), "yyyy-MM-dd")
        if dt.isValid():
            self.date_to.setDate(dt)
        tf = QTime.fromString((exc.get("time_from") or "00:00")[:5], "HH:mm")
        if tf.isValid():
            self.time_from.setTime(tf)
        tt = QTime.fromString((exc.get("time_to") or "23:59")[:5], "HH:mm")
        if tt.isValid():
            self.time_to.setTime(tt)
        self.desc_input.setPlainText(exc.get("description", "") or "")
        # Pre-select the previously affected blocks
        if hasattr(self, "blocks_list_widget"):
            raw = (exc.get("affected_blocks") or "").strip()
            want = set()
            if raw and raw.lower() not in ("all", "all blocks"):
                for tok in raw.split(","):
                    tok = tok.strip()
                    if tok.isdigit():
                        want.add(int(tok))
            if want:
                self.blocks_list_widget.blockSignals(True)
                for r in range(self.blocks_list_widget.count()):
                    it = self.blocks_list_widget.item(r)
                    it.setSelected(it.data(Qt.UserRole) in want)
                self.blocks_list_widget.blockSignals(False)

    def _set_full_day(self):
        self.time_from.setTime(QTime(0, 0))
        self.time_to.setTime(QTime(23, 59))

    def _select_all_blocks(self):
        if hasattr(self, 'blocks_list_widget'):
            self.blocks_list_widget.selectAll()

    def _clear_block_selection(self):
        if hasattr(self, 'blocks_list_widget'):
            self.blocks_list_widget.clearSelection()

    def _update_blocks_summary(self):
        if not hasattr(self, 'blocks_summary_label'):
            return
        n_sel = len(self.blocks_list_widget.selectedItems())
        if n_sel == 0:
            self.blocks_summary_label.setText(
                f"None selected → applies to ALL {len(self._blocks_list)} blocks")
        else:
            self.blocks_summary_label.setText(
                f"{n_sel} of {len(self._blocks_list)} blocks selected")

    def _selected_blocks_csv(self) -> str:
        """Comma-separated selected block IDs (sorted), or '' = all blocks."""
        if not hasattr(self, 'blocks_list_widget'):
            return ""
        ids = sorted(it.data(Qt.UserRole)
                     for it in self.blocks_list_widget.selectedItems())
        return ",".join(str(i) for i in ids)

    def get_data(self) -> dict:
        return {
            "exclusion_type":  self.type_combo.currentText(),
            "date_from":       self.date_from.date().toString("yyyy-MM-dd"),
            "date_to":         self.date_to.date().toString("yyyy-MM-dd"),
            "time_from":       self.time_from.time().toString("HH:mm"),
            "time_to":         self.time_to.time().toString("HH:mm"),
            "affected_blocks": self._selected_blocks_csv(),
            "description":     self.desc_input.toPlainText().strip(),
        }


class BalancingDialog(QDialog):
    """Add/Edit a cycle-balancing (rested-blocks) period. Same drag-select block
    picker as ExclusionDialog, but a plain date range + note (no time, no type):
    it records which blocks were intentionally held out of dispatch to balance
    cycles. Informational — does not affect contractual availability."""

    def __init__(self, parent=None, blocks_list=None, existing=None):
        super().__init__(parent)
        self._blocks_list = blocks_list or []
        self._existing = existing or None
        is_edit = self._existing is not None
        self.setWindowTitle("Edit Rested / Balancing Blocks" if is_edit
                            else "Add Rested / Balancing Blocks")
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)

        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDate(QDate.currentDate())
        self.date_from.setDisplayFormat("yyyy-MM-dd")
        form.addRow("Rested from *:", self.date_from)

        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDate(QDate.currentDate())
        self.date_to.setDisplayFormat("yyyy-MM-dd")
        form.addRow("Rested to *:", self.date_to)

        if self._blocks_list:
            self.blocks_list_widget = QListWidget()
            self.blocks_list_widget.setViewMode(QListWidget.IconMode)
            self.blocks_list_widget.setResizeMode(QListWidget.Adjust)
            self.blocks_list_widget.setMovement(QListWidget.Static)
            self.blocks_list_widget.setSelectionMode(
                QAbstractItemView.ExtendedSelection)
            self.blocks_list_widget.setSelectionRectVisible(True)
            self.blocks_list_widget.setDragEnabled(False)
            self.blocks_list_widget.setUniformItemSizes(True)
            self.blocks_list_widget.setSpacing(3)
            self.blocks_list_widget.setGridSize(QSize(80, 26))
            self.blocks_list_widget.setMinimumHeight(150)
            self.blocks_list_widget.setMaximumHeight(230)
            self.blocks_list_widget.setStyleSheet(
                "QListWidget{background:#FFFFFF;border:1px solid #D5DBE3;"
                "border-radius:6px;}"
                "QListWidget::item{border:1px solid #D5DBE3;border-radius:4px;"
                "padding:3px 0;color:#2B3A4B;}"
                "QListWidget::item:selected{background:#0071E3;color:#FFFFFF;"
                "border-color:#0071E3;}"
            )
            for b in self._blocks_list:
                it = QListWidgetItem(f"Block {int(b)}")
                it.setData(Qt.UserRole, int(b))
                it.setTextAlignment(Qt.AlignCenter)
                it.setSizeHint(QSize(76, 22))
                self.blocks_list_widget.addItem(it)

            hint = QLabel("Tip: drag to select a range · Ctrl-click to add · "
                          "Shift-click for a run")
            hint.setStyleSheet("color:#8A97A6;font-size:11px;")

            btn_row = QHBoxLayout()
            self.btn_all = QPushButton("Select all")
            self.btn_all.clicked.connect(
                lambda: self.blocks_list_widget.selectAll())
            self.btn_none = QPushButton("Clear")
            self.btn_none.clicked.connect(
                lambda: self.blocks_list_widget.clearSelection())
            self.blocks_summary_label = QLabel()
            self.blocks_summary_label.setStyleSheet(
                "color:#6B7A8D;font-style:italic;")
            btn_row.addWidget(self.btn_all)
            btn_row.addWidget(self.btn_none)
            btn_row.addStretch()
            btn_row.addWidget(self.blocks_summary_label)

            self.blocks_list_widget.itemSelectionChanged.connect(
                self._update_blocks_summary)
            form.addRow("Rested Blocks *:", self.blocks_list_widget)
            form.addRow("", hint)
            form.addRow("", btn_row)

        self.note_input = QTextEdit()
        self.note_input.setMaximumHeight(60)
        self.note_input.setPlaceholderText(
            "Note (e.g. held to balance cycles / stay within 365-cycle budget)…")
        form.addRow("Note:", self.note_input)

        layout.addLayout(form)
        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        if is_edit:
            btns.button(QDialogButtonBox.Save).setText("Update")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        if is_edit:
            self._prefill(self._existing)
        self._update_blocks_summary()

    def _prefill(self, b: dict):
        df = QDate.fromString(b.get("date_from", ""), "yyyy-MM-dd")
        if df.isValid():
            self.date_from.setDate(df)
        dt = QDate.fromString(b.get("date_to", ""), "yyyy-MM-dd")
        if dt.isValid():
            self.date_to.setDate(dt)
        self.note_input.setPlainText(b.get("note", "") or "")
        if hasattr(self, "blocks_list_widget"):
            raw = (b.get("affected_blocks") or "").strip()
            want = set()
            if raw and raw.lower() not in ("all", "all blocks"):
                for tok in raw.split(","):
                    tok = tok.strip()
                    if tok.isdigit():
                        want.add(int(tok))
            if want:
                self.blocks_list_widget.blockSignals(True)
                for r in range(self.blocks_list_widget.count()):
                    it = self.blocks_list_widget.item(r)
                    it.setSelected(it.data(Qt.UserRole) in want)
                self.blocks_list_widget.blockSignals(False)

    def _update_blocks_summary(self):
        if not hasattr(self, 'blocks_summary_label'):
            return
        n_sel = len(self.blocks_list_widget.selectedItems())
        if n_sel == 0:
            self.blocks_summary_label.setText(
                f"None selected → applies to ALL {len(self._blocks_list)} blocks")
        else:
            self.blocks_summary_label.setText(
                f"{n_sel} of {len(self._blocks_list)} blocks selected")

    def _selected_blocks_csv(self) -> str:
        if not hasattr(self, 'blocks_list_widget'):
            return ""
        ids = sorted(it.data(Qt.UserRole)
                     for it in self.blocks_list_widget.selectedItems())
        return ",".join(str(i) for i in ids)

    def get_data(self) -> dict:
        return {
            "date_from": self.date_from.date().toString("yyyy-MM-dd"),
            "date_to":   self.date_to.date().toString("yyyy-MM-dd"),
            "affected_blocks": self._selected_blocks_csv(),
            "note":      self.note_input.toPlainText().strip(),
        }


# ── Main page ──────────────────────────────────────────────────────────────────

class ScadaReportPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._pid = None            # open project — see BlockReportPage
        self._build_ui()
        self._refresh_exclusions()

    def set_current_project(self, project_id):
        self._pid = project_id
        self._refresh_exclusions()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = PageHeader(
            "SCADA Report Generator",
            "Generate a monthly PDF operations report from SCADA export files"
        )
        layout.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 20, 24, 24)
        content_layout.setSpacing(16)

        # ── Site info ────────────────────────────────────────────────────
        site_group = QGroupBox("Report Information")
        site_form  = QFormLayout()
        self.site_name_input = QLineEdit()
        self.site_name_input.setPlaceholderText("e.g.  Tashkent BESS")
        site_form.addRow("Site / Project Name *:", self.site_name_input)

        # Plant capacity (used for plant-level redundancy availability)
        cap_row = QHBoxLayout()
        self.plant_capacity_input = QDoubleSpinBox()
        self.plant_capacity_input.setRange(0, 10000)
        self.plant_capacity_input.setDecimals(1)
        self.plant_capacity_input.setSuffix(" MW")
        self.plant_capacity_input.setFixedWidth(110)
        cap_row.addWidget(self.plant_capacity_input)
        cap_row.addWidget(QLabel("    Per-block:"))
        self.per_block_capacity_input = QDoubleSpinBox()
        self.per_block_capacity_input.setRange(0, 1000)
        self.per_block_capacity_input.setDecimals(2)
        self.per_block_capacity_input.setSuffix(" MW")
        self.per_block_capacity_input.setFixedWidth(120)
        cap_row.addWidget(self.per_block_capacity_input)
        cap_row.addWidget(QLabel("    Contractual:"))
        self.contractual_capacity_input = QDoubleSpinBox()
        self.contractual_capacity_input.setRange(0, 10000)
        self.contractual_capacity_input.setDecimals(1)
        self.contractual_capacity_input.setSuffix(" MW")
        self.contractual_capacity_input.setFixedWidth(110)
        cap_row.addWidget(self.contractual_capacity_input)
        cap_row.addWidget(QLabel("    Redundancy threshold:"))
        self.redundancy_threshold_input = QDoubleSpinBox()
        self.redundancy_threshold_input.setRange(0, 100)
        self.redundancy_threshold_input.setDecimals(0)
        self.redundancy_threshold_input.setSuffix(" %")
        self.redundancy_threshold_input.setValue(100)
        self.redundancy_threshold_input.setFixedWidth(85)
        cap_row.addWidget(self.redundancy_threshold_input)
        cap_row.addStretch()
        site_form.addRow("Plant Capacity (Total):", cap_row)

        cap_hint = QLabel(
            "Plant Capacity = nameplate (total installed MW). "
            "Contractual = SLA-guaranteed MW (when set, the plant is considered "
            "available whenever block-aggregate capacity ≥ contractual). "
            "If Contractual = 0, the Redundancy Threshold % of nameplate is used "
            "instead. Leave all values at 0 to skip the redundancy-based metric "
            "and report only container-level availability."
        )
        cap_hint.setStyleSheet("color:#6B7A8D;font-size:11px;font-style:italic;")
        cap_hint.setWordWrap(True)
        site_form.addRow("", cap_hint)

        site_group.setLayout(site_form)
        content_layout.addWidget(site_group)

        # ── Input files ──────────────────────────────────────────────────
        files_group = QGroupBox("Input Files")
        files_form  = QFormLayout()
        files_form.setSpacing(10)
        self.fp_working  = FilePicker("", "working_status.xlsx")
        self.fp_charge   = FilePicker("", "charge_discharge__status.xlsx")
        self.fp_alarms   = FilePicker("", "Alarm_report.xlsx")
        self.fp_exported = FilePicker("", "exported energy file (kWh)")
        self.fp_imported = FilePicker("", "imported energy file (kWh)")
        files_form.addRow("Working Status *:",   self.fp_working)
        files_form.addRow("Charge/Discharge *:", self.fp_charge)
        files_form.addRow("Alarm Report *:",      self.fp_alarms)
        files_form.addRow("Energy Exported *:",   self.fp_exported)
        files_form.addRow("Energy Imported *:",   self.fp_imported)
        files_group.setLayout(files_form)
        content_layout.addWidget(files_group)

        # ── Capacity Availability (Optional) ────────────────────────────
        cap_group = QGroupBox(
            "Capacity Availability (Optional)  —  "
            "per-LC daily charge/discharge cycle metric"
        )
        cap_form = QFormLayout()
        cap_form.setSpacing(10)
        self.fp_lc_charge    = FilePicker("", "LC daily charge.xlsx")
        self.fp_lc_discharge = FilePicker("", "LC Daily discharge.xlsx")
        self.fp_cycle_targets = FilePicker("", "cycle_targets.xlsx (optional)")
        cap_form.addRow("LC Daily Charge:",    self.fp_lc_charge)
        cap_form.addRow("LC Daily Discharge:", self.fp_lc_discharge)
        cap_form.addRow("Cycle Targets:",      self.fp_cycle_targets)

        self.default_target_spin = QSpinBox()
        self.default_target_spin.setRange(1, 4)
        self.default_target_spin.setValue(1)
        self.default_target_spin.setFixedWidth(60)
        cap_form.addRow("Default Cycle Target (per day):", self.default_target_spin)

        cap_hint = QLabel(
            "If both LC daily charge and discharge files are provided, a "
            "Capacity Availability section is added to the report. The "
            "cycle-targets file is optional — when missing days are found, "
            "the default cycle target above is used. Capacity is computed at "
            "both 4,954 kWh (operational, 5–95% SOC) and 5,504 kWh (nameplate)."
        )
        cap_hint.setStyleSheet("color:#6B7A8D;font-size:11px;font-style:italic;")
        cap_hint.setWordWrap(True)
        cap_form.addRow("", cap_hint)
        cap_group.setLayout(cap_form)
        content_layout.addWidget(cap_group)

        # ── Exclusions ──────────────────────────────────────────────────
        excl_group = QGroupBox(
            "Availability Exclusions (Optional)  —  "
            "scheduled maintenance, grid outages, force majeure, major faults"
        )
        el = QVBoxLayout()

        excl_btn_row = QHBoxLayout()
        add_excl_btn = PrimaryButton("➕  Add Exclusion")
        add_excl_btn.clicked.connect(self._add_exclusion)
        excl_btn_row.addWidget(add_excl_btn)
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
        el.addWidget(self.excl_table)

        hint = QLabel(
            "Exclusions are deducted from the denominator of the technical availability calculation. "
            "If no exclusions are entered, only raw availability is shown."
        )
        hint.setStyleSheet("color:#6B7A8D;font-size:11px;font-style:italic;")
        hint.setWordWrap(True)
        el.addWidget(hint)
        excl_group.setLayout(el)
        content_layout.addWidget(excl_group)

        # ── Output file ──────────────────────────────────────────────────
        out_group = QGroupBox("Output")
        out_form  = QFormLayout()
        self.fp_output = FilePicker("", "BESS_Operations_Report.pdf",
                                    save_mode=True,
                                    filter_str="PDF Files (*.pdf)")
        out_form.addRow("Save PDF to *:", self.fp_output)
        out_group.setLayout(out_form)
        content_layout.addWidget(out_group)

        # ── Generate button ──────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.clear_btn = SecondaryButton("🔄  Clear All")
        self.clear_btn.clicked.connect(self._clear_all)
        btn_row.addWidget(self.clear_btn)
        self.gen_btn = PrimaryButton("⚡  Generate PDF Report")
        self.gen_btn.setFixedHeight(42)
        self.gen_btn.setMinimumWidth(200)
        self.gen_btn.clicked.connect(self._generate)
        btn_row.addWidget(self.gen_btn)
        content_layout.addLayout(btn_row)

        # ── Progress ─────────────────────────────────────────────────────
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedHeight(6)
        content_layout.addWidget(self.progress_bar)

        log_group = QGroupBox("Progress Log")
        log_layout = QVBoxLayout()
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumHeight(140)
        self.log_output.setStyleSheet(
            "QTextEdit{background:#1A2B45;color:#8FA3BE;font-family:Consolas,monospace;"
            "font-size:11px;border:none;padding:8px;border-radius:4px;}"
        )
        self.log_output.setPlaceholderText("Progress messages will appear here...")
        log_layout.addWidget(self.log_output)
        log_group.setLayout(log_layout)
        content_layout.addWidget(log_group)

        content_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll)

    # ── Exclusions ────────────────────────────────────────────────────────

    def _refresh_exclusions(self):
        from services.availability_service import _exclusion_hours
        self.excl_table.setRowCount(0)
        try:
            exclusions = get_exclusions(project_id=self._pid)
        except Exception:
            exclusions = []
        for exc in exclusions:
            row = self.excl_table.rowCount()
            self.excl_table.insertRow(row)
            t_from = exc.get("time_from","00:00") or "00:00"
            t_to   = exc.get("time_to","23:59")   or "23:59"
            hours  = _exclusion_hours(exc)
            blocks_val = exc.get("affected_blocks","")
            if not blocks_val or blocks_val.lower() in ("all", "all blocks"):
                blocks_display = "All blocks"
            else:
                n = len([b for b in blocks_val.split(",") if b.strip()])
                blocks_display = f"{n} block(s): {blocks_val[:30]}{'…' if len(blocks_val) > 30 else ''}"
            for col, val in enumerate([
                str(exc["id"]),
                exc.get("exclusion_type",""),
                f"{exc.get('date_from','')} {t_from}",
                f"{exc.get('date_to','')} {t_to}",
                f"{hours:.1f}",
                blocks_display,
                exc.get("description",""),
            ]):
                item = QTableWidgetItem(val)
                self.excl_table.setItem(row, col, item)

    def _project_block_ids(self) -> list:
        """Return the list of block IDs (sorted) known to the project DB.
        Falls back to a sensible default if the DB isn't reachable yet."""
        try:
            from database.db_manager import get_connection
            c = get_connection()
            try:
                # Try a flat 1..N first via projects table; fall back to
                # distinct (zone, block) combinations in containers.
                row = c.execute(
                    "SELECT num_zones, num_blocks FROM projects ORDER BY id LIMIT 1"
                ).fetchone()
                # Use containers table for the authoritative count
                blocks = sorted({
                    int(r[0]) for r in c.execute(
                        "SELECT DISTINCT (zone_number-1)*? + block_number "
                        "FROM containers WHERE block_number IS NOT NULL",
                        (row['num_blocks'] if row else 8,)
                    ).fetchall()
                })
                if blocks:
                    return blocks
                if row:
                    return list(range(1, (row['num_zones'] or 1) *
                                          (row['num_blocks'] or 1) + 1))
            finally:
                c.close()
        except Exception as e:
            print(f"Warning: could not load project blocks: {e}")
        return list(range(1, 71))   # default: 70 blocks for Tashkent

    def _add_exclusion(self):
        dlg = ExclusionDialog(self, blocks_list=self._project_block_ids())
        if dlg.exec_() == QDialog.Accepted:
            data = dlg.get_data()
            try:
                add_exclusion(**data, project_id=self._pid)
                self._refresh_exclusions()
            except Exception as e:
                QMessageBox.critical(self,"Error",f"Failed to add:\n{e}")

    def _delete_exclusion(self):
        row = self.excl_table.currentRow()
        if row < 0:
            QMessageBox.information(self,"Select","Click an exclusion row first.")
            return
        id_item = self.excl_table.item(row, 0)
        if not id_item:
            return
        excl_id = int(id_item.text())
        ans = QMessageBox.question(self,"Delete?",
            "Delete this exclusion?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ans == QMessageBox.Yes:
            delete_exclusion(excl_id)
            self._refresh_exclusions()

    # ── Generate ─────────────────────────────────────────────────────────

    def _log(self, msg: str):
        self.log_output.append(f"▸  {msg}")
        self.log_output.verticalScrollBar().setValue(
            self.log_output.verticalScrollBar().maximum()
        )

    def _clear_all(self):
        for fp in [self.fp_working, self.fp_charge, self.fp_alarms,
                   self.fp_exported, self.fp_imported, self.fp_output,
                   self.fp_lc_charge, self.fp_lc_discharge, self.fp_cycle_targets]:
            fp.set_path("")
        self.site_name_input.clear()
        self.default_target_spin.setValue(1)
        self.plant_capacity_input.setValue(0)
        self.per_block_capacity_input.setValue(0)
        self.contractual_capacity_input.setValue(0)
        self.redundancy_threshold_input.setValue(100)
        self.log_output.clear()

    def _validate(self) -> bool:
        missing = []
        if not self.site_name_input.text().strip():
            missing.append("Site / Project Name")
        for label, fp in [
            ("Working Status file",      self.fp_working),
            ("Charge/Discharge file",    self.fp_charge),
            ("Alarm Report file",        self.fp_alarms),
            ("Energy Exported file",     self.fp_exported),
            ("Energy Imported file",     self.fp_imported),
            ("Output PDF path",          self.fp_output),
        ]:
            if not fp.path():
                missing.append(label)
        if missing:
            QMessageBox.warning(self,"Missing Fields",
                "Please fill in:\n• " + "\n• ".join(missing))
            return False
        for label, fp in [
            ("Working Status",   self.fp_working),
            ("Charge/Discharge", self.fp_charge),
            ("Alarm Report",     self.fp_alarms),
            ("Energy Exported",  self.fp_exported),
            ("Energy Imported",  self.fp_imported),
        ]:
            if not os.path.exists(fp.path()):
                QMessageBox.warning(self,"File Not Found",
                    f"{label} file not found:\n{fp.path()}")
                return False
        return True

    def _generate(self):
        if not self._validate():
            return

        self.gen_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.log_output.clear()
        self._log("Starting report generation...")

        # Get current exclusions from table
        exclusions = get_exclusions(project_id=self._pid)
        if exclusions:
            self._log(f"Including {len(exclusions)} availability exclusion(s)")

        params = {
            'site_name':        self.site_name_input.text().strip(),
            'working_status':   self.fp_working.path(),
            'charge_discharge': self.fp_charge.path(),
            'alarms':           self.fp_alarms.path(),
            'exported':         self.fp_exported.path(),
            'imported':         self.fp_imported.path(),
            'output':           self.fp_output.path(),
            'exclusions':       exclusions,
            'lc_charge':        self.fp_lc_charge.path(),
            'lc_discharge':     self.fp_lc_discharge.path(),
            'cycle_targets':    self.fp_cycle_targets.path(),
            'default_target':   self.default_target_spin.value(),
            'plant_capacity_mw':       self.plant_capacity_input.value() or None,
            'per_block_capacity_mw':   self.per_block_capacity_input.value() or None,
            'redundancy_threshold_pct': self.redundancy_threshold_input.value(),
            'contractual_plant_capacity_mw': self.contractual_capacity_input.value() or None,
            'project_blocks':          self._project_block_ids(),
        }
        self._worker = ReportWorker(params)
        self._worker.progress.connect(self._log)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_finished(self, output_path: str):
        self.progress_bar.setVisible(False)
        self.gen_btn.setEnabled(True)
        self._log(f"✅  Report saved to: {output_path}")
        reply = QMessageBox.question(self,"Report Generated",
            f"PDF report saved successfully!\n\n{output_path}\n\nOpen it now?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if reply == QMessageBox.Yes:
            import subprocess, sys
            try:
                if sys.platform == 'win32':
                    os.startfile(output_path)
                elif sys.platform == 'darwin':
                    subprocess.run(['open', output_path])
                else:
                    subprocess.run(['xdg-open', output_path])
            except Exception:
                pass

    def _on_error(self, error_msg: str):
        self.progress_bar.setVisible(False)
        self.gen_btn.setEnabled(True)
        self._log(f"❌  Error: {error_msg}")
        QMessageBox.critical(self,"Report Failed",
            f"Failed to generate report:\n\n{error_msg}")
