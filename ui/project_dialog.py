"""
ui/project_dialog.py
---------------------
Project creation wizard — supports three project types:

  BESS       — zones × blocks; a block holds the equipment ticked in
               "Each block has" (Transformer, RMU, SCC, LC, PCS) and a number
               of battery containers. A plant with the PCS inside the battery
               container and the LC inside the MVS simply leaves those unticked.
  PV String  — blocks × (1 MVS + N string inverters per block)
  PV Central — blocks × (N master units + N slave units + combiner boxes per unit)
               All units share one serial number per block.
               Combiner boxes have no serial numbers.
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QSpinBox, QTextEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QComboBox, QMessageBox,
    QGroupBox, QScrollArea, QWidget, QFrame, QButtonGroup,
    QRadioButton, QStackedWidget
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from ui.style import TOKENS as T

from models.models import (
    Project, Container, PROJECT_TYPES, PROJECT_TYPE_ICONS,
    CONTAINER_TYPES_BY_PROJECT, default_container_type
)
from services.project_service import create_project, save_containers


class ProjectDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create New Project")
        self.setMinimumSize(900, 700)
        self._project_type = "BESS"
        self._zone_spins   = []      # BESS only: (blocks_spin, batteries_spin) per zone
        self._container_rows = []    # all project types: row tracking
        self._build_ui()
        self._apply_zone_count()     # init BESS zone cards

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # ── Step 1: Project info + type ───────────────────────────────────
        info_group = QGroupBox("Step 1 — Project Information")
        form = QFormLayout()

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g.  Solar Farm Alpha")
        form.addRow("Project Name *:", self.name_input)

        self.desc_input = QTextEdit()
        self.desc_input.setMaximumHeight(50)
        self.desc_input.setPlaceholderText("Optional description...")
        form.addRow("Description:", self.desc_input)

        # Project type radio buttons
        type_row = QHBoxLayout()
        self._type_group = QButtonGroup(self)
        for ptype in PROJECT_TYPES:
            icon = PROJECT_TYPE_ICONS.get(ptype, "")
            rb = QRadioButton(f"{icon}  {ptype}")
            rb.setStyleSheet("font-size:12px; padding: 4px 12px;")
            if ptype == "BESS":
                rb.setChecked(True)
            self._type_group.addButton(rb)
            type_row.addWidget(rb)
            rb.toggled.connect(lambda checked, t=ptype: self._on_type_changed(t) if checked else None)
        type_row.addStretch()
        form.addRow("Project Type *:", type_row)
        info_group.setLayout(form)
        layout.addWidget(info_group)

        # ── Step 2: Type-specific configuration (stacked) ─────────────────
        self.config_stack = QStackedWidget()

        # Page 0: BESS
        self.bess_config = self._build_bess_config()
        self.config_stack.addWidget(self.bess_config)

        # Page 1: PV String
        self.pv_string_config = self._build_pv_string_config()
        self.config_stack.addWidget(self.pv_string_config)

        # Page 2: PV Central
        self.pv_central_config = self._build_pv_central_config()
        self.config_stack.addWidget(self.pv_central_config)

        layout.addWidget(self.config_stack)

        # ── Step 3: Container table ───────────────────────────────────────
        table_group = QGroupBox(
            "Step 3 — Container / Equipment Table  "
            "(fill in Type and Serial Number)"
        )
        tl = QVBoxLayout()
        self.container_table = QTableWidget(0, 5)
        self.container_table.setHorizontalHeaderLabels([
            "Block", "Seq #", "Equipment Type", "Label / Description", "Serial Number"
        ])
        self.container_table.setColumnWidth(0, 55)
        self.container_table.setColumnWidth(1, 55)
        self.container_table.setColumnWidth(2, 180)
        self.container_table.setColumnWidth(3, 200)
        self.container_table.setColumnWidth(4, 180)
        self.container_table.horizontalHeader().setStretchLastSection(True)
        self.container_table.setAlternatingRowColors(True)
        # the type drop-down needs the height, or its text is cut off
        self.container_table.verticalHeader().setDefaultSectionSize(36)
        tl.addWidget(self.container_table)
        table_group.setLayout(tl)
        layout.addWidget(table_group)

        # ── Buttons ───────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("SecondaryButton")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        save_btn = QPushButton("Save project")
        save_btn.setObjectName("PrimaryButton")
        save_btn.setFixedHeight(36)
        save_btn.clicked.connect(self._save_project)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    # ── BESS config panel ─────────────────────────────────────────────────

    def _build_bess_config(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        group = QGroupBox("Step 2 — Zone & Block Configuration")
        outer = QVBoxLayout()

        # Most plants have the same number of blocks in every zone, so that
        # is one row and one button; a zone that differs is changed on its
        # own card below, and stays as set until the button is pressed again.
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("Number of zones:"))
        self.zones_spin = QSpinBox()
        self.zones_spin.setRange(1, 50)
        self.zones_spin.setValue(1)
        self.zones_spin.setFixedWidth(65)
        row.addWidget(self.zones_spin)
        row.addSpacing(16)
        row.addWidget(QLabel("Blocks per zone:"))
        self.all_blocks_spin = QSpinBox()
        self.all_blocks_spin.setRange(1, 50)
        self.all_blocks_spin.setValue(1)
        self.all_blocks_spin.setFixedWidth(65)
        row.addWidget(self.all_blocks_spin)
        row.addWidget(QLabel("Batteries per block:"))
        self.all_batteries_spin = QSpinBox()
        self.all_batteries_spin.setRange(0, 20)
        self.all_batteries_spin.setValue(4)
        self.all_batteries_spin.setFixedWidth(65)
        row.addWidget(self.all_batteries_spin)
        self.apply_all_btn = QPushButton("Apply to all zones")
        self.apply_all_btn.setObjectName("SecondaryButton")
        self.apply_all_btn.setFixedHeight(30)
        self.apply_all_btn.setToolTip(
            "Give every zone this number of blocks and batteries. "
            "A single zone can still be changed on its card afterwards.")
        self.apply_all_btn.clicked.connect(self._apply_to_all_zones)
        row.addWidget(self.apply_all_btn)
        row.addStretch()
        outer.addLayout(row)
        hint = QLabel("A zone that differs from the rest can be changed on its own card below.")
        hint.setStyleSheet(f"color:{T['muted']};font-size:12px;")
        outer.addWidget(hint)

        self.zone_scroll = QScrollArea()
        self.zone_scroll.setWidgetResizable(True)
        self.zone_scroll.setMaximumHeight(140)
        self.zone_scroll.setFrameShape(QFrame.StyledPanel)
        self.zone_inner = QWidget()
        self.zone_inner_layout = QHBoxLayout(self.zone_inner)
        self.zone_inner_layout.setAlignment(Qt.AlignLeft)
        self.zone_inner_layout.setSpacing(8)
        self.zone_scroll.setWidget(self.zone_inner)
        outer.addWidget(self.zone_scroll)
        # the cards follow the zone count by themselves — no separate Apply
        self.zones_spin.valueChanged.connect(self._apply_zone_count)

        # What every block holds besides its battery containers. Plants
        # differ: some have a separate PCS and LC cabinet, in others the PCS
        # is inside the battery container and the controller is the SCC
        # cabinet of the MV station — so each is a choice, not a fixed slot.
        from PyQt5.QtWidgets import QCheckBox
        opt_row = QHBoxLayout()
        opt_row.addWidget(QLabel("Each block has:"))
        self._bess_options = []          # (checkbox, equipment type), in block order
        for attr, label, eq_type, on, tip in (
                ("bess_has_transformer", "Transformer", "Transformer", False, ""),
                ("bess_has_rmu", "RMU", "RMU", False, ""),
                ("bess_has_scc", "SCC cabinet", "SCC Cabinet", False,
                 "The controller cabinet inside the MV station — used instead "
                 "of a separate LC cabinet."),
                ("bess_has_lc", "LC cabinet", "LC Cabinet", True,
                 "Untick when the plant has no separate LC cabinet."),
                ("bess_has_pcs", "PCS", "PCS / Converter", True,
                 "Untick when the PCS is built into the battery container.")):
            cb = QCheckBox(label)
            cb.setChecked(on)
            if tip:
                cb.setToolTip(tip)
            setattr(self, attr, cb)
            self._bess_options.append((cb, eq_type))
            opt_row.addWidget(cb)
        opt_row.addStretch()
        outer.addLayout(opt_row)

        gen_row = QHBoxLayout()
        gen_btn = QPushButton("Generate table")
        gen_btn.setObjectName("SecondaryButton")
        gen_btn.setFixedHeight(34)
        gen_btn.clicked.connect(self._generate_bess_table)
        gen_row.addWidget(gen_btn)
        self.bess_summary = QLabel("")
        self.bess_summary.setStyleSheet(f"color:{T['muted']};")
        gen_row.addWidget(self.bess_summary)
        gen_row.addStretch()
        outer.addLayout(gen_row)

        group.setLayout(outer)
        layout.addWidget(group)
        return widget

    # ── PV String config panel ────────────────────────────────────────────

    def _build_pv_string_config(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        group = QGroupBox("Step 2 — PV String Configuration")
        form = QFormLayout()
        form.setSpacing(10)

        self.pvs_blocks_spin = QSpinBox()
        self.pvs_blocks_spin.setRange(1, 100)
        self.pvs_blocks_spin.setValue(1)
        self.pvs_blocks_spin.setFixedWidth(70)
        form.addRow("Number of Blocks:", self.pvs_blocks_spin)

        self.pvs_inv_spin = QSpinBox()
        self.pvs_inv_spin.setRange(1, 200)
        self.pvs_inv_spin.setValue(10)
        self.pvs_inv_spin.setFixedWidth(70)
        form.addRow("String Inverters per Block:", self.pvs_inv_spin)

        # Optional equipment
        from PyQt5.QtWidgets import QCheckBox
        opt_label = QLabel("Optional per block:")
        opt_label.setStyleSheet(f"font-size:12px;color:{T['muted']};")
        form.addRow("", opt_label)

        opt_row = QHBoxLayout()
        self.pvs_has_transformer = QCheckBox("Transformer")
        self.pvs_has_transformer.setChecked(False)
        self.pvs_has_rmu = QCheckBox("RMU")
        self.pvs_has_rmu.setChecked(False)
        self.pvs_has_logger = QCheckBox("Logger")
        self.pvs_has_logger.setChecked(False)
        opt_row.addWidget(self.pvs_has_transformer)
        opt_row.addWidget(self.pvs_has_rmu)
        opt_row.addWidget(self.pvs_has_logger)
        opt_row.addStretch()
        form.addRow("", opt_row)

        hint = QLabel(
            "Block order: Transformer → RMU → Logger → MVS → String Inverters\n"
            "All items get individual serial number fields."
        )
        hint.setStyleSheet(f"color:{T['muted']};font-size:12px;font-style:italic;")
        form.addRow("", hint)

        gen_btn = QPushButton("Generate table")
        gen_btn.setObjectName("SecondaryButton")
        gen_btn.setFixedHeight(34)
        gen_btn.clicked.connect(self._generate_pvs_table)
        form.addRow("", gen_btn)
        group.setLayout(form)
        layout.addWidget(group)
        return widget

    # ── PV Central config panel ───────────────────────────────────────────

    def _build_pv_central_config(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        group = QGroupBox("Step 2 — PV Central Inverter Configuration")
        form = QFormLayout()
        form.setSpacing(10)

        self.pvc_blocks_spin = QSpinBox()
        self.pvc_blocks_spin.setRange(1, 50)
        self.pvc_blocks_spin.setValue(1)
        self.pvc_blocks_spin.setFixedWidth(70)
        form.addRow("Number of Blocks:", self.pvc_blocks_spin)

        self.pvc_master_spin = QSpinBox()
        self.pvc_master_spin.setRange(1, 20)
        self.pvc_master_spin.setValue(4)
        self.pvc_master_spin.setFixedWidth(70)
        form.addRow("Master Units per Block:", self.pvc_master_spin)

        self.pvc_slave_spin = QSpinBox()
        self.pvc_slave_spin.setRange(0, 20)
        self.pvc_slave_spin.setValue(4)
        self.pvc_slave_spin.setFixedWidth(70)
        form.addRow("Slave Units per Block:", self.pvc_slave_spin)

        self.pvc_cb_spin = QSpinBox()
        self.pvc_cb_spin.setRange(0, 20)
        self.pvc_cb_spin.setValue(5)
        self.pvc_cb_spin.setFixedWidth(70)
        form.addRow("Combiner Boxes per Unit:", self.pvc_cb_spin)

        # Optional extra equipment checkboxes
        from PyQt5.QtWidgets import QCheckBox
        extras_label = QLabel("Optional equipment per block:")
        extras_label.setStyleSheet(f"font-size:12px;color:{T['muted']};")
        form.addRow("", extras_label)

        extra_row1 = QHBoxLayout()
        self.pvc_has_transformer = QCheckBox("Transformer")
        self.pvc_has_transformer.setChecked(True)
        self.pvc_has_rmu = QCheckBox("RMU")
        self.pvc_has_rmu.setChecked(False)
        extra_row1.addWidget(self.pvc_has_transformer)
        extra_row1.addWidget(self.pvc_has_rmu)
        extra_row1.addStretch()
        form.addRow("", extra_row1)

        extra_row2 = QHBoxLayout()
        self.pvc_has_scu_master = QCheckBox("SCU Master")
        self.pvc_has_scu_master.setChecked(False)
        self.pvc_has_scu_slave = QCheckBox("SCU Slave")
        self.pvc_has_scu_slave.setChecked(False)
        extra_row2.addWidget(self.pvc_has_scu_master)
        extra_row2.addWidget(self.pvc_has_scu_slave)
        extra_row2.addStretch()
        form.addRow("", extra_row2)

        hint = QLabel(
            "Each unit (Master, Slave, Transformer, RMU, SCU) gets its own\n"
            "serial number field — fill in as many or as few as needed.\n"
            "Combiner boxes have no serial numbers."
        )
        hint.setStyleSheet(f"color:{T['muted']};font-size:12px;font-style:italic;")
        form.addRow("", hint)

        gen_btn = QPushButton("Generate table")
        gen_btn.setObjectName("SecondaryButton")
        gen_btn.setFixedHeight(34)
        gen_btn.clicked.connect(self._generate_pvc_table)
        form.addRow("", gen_btn)
        group.setLayout(form)
        layout.addWidget(group)
        return widget

    # ── Type switching ────────────────────────────────────────────────────

    def _on_type_changed(self, ptype: str):
        self._project_type = ptype
        page = {"BESS": 0, "PV String": 1, "PV Central": 2}.get(ptype, 0)
        self.config_stack.setCurrentIndex(page)
        self.container_table.setRowCount(0)
        self._container_rows = []

    # ── BESS zone cards ───────────────────────────────────────────────────

    def _apply_to_all_zones(self):
        """One button: every zone gets the same blocks and batteries."""
        if len(self._zone_spins) != self.zones_spin.value():
            self._apply_zone_count()
        for blocks_spin, batteries_spin in self._zone_spins:
            blocks_spin.setValue(self.all_blocks_spin.value())
            batteries_spin.setValue(self.all_batteries_spin.value())

    def _apply_zone_count(self):
        num_zones = self.zones_spin.value()
        old = [(b.value(), c.value()) for b, c in self._zone_spins]
        while self.zone_inner_layout.count():
            item = self.zone_inner_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self._zone_spins = []
        for z in range(1, num_zones + 1):
            # an existing zone keeps what was set on its card; a new one
            # starts from the "same for all zones" row
            pb = old[z-1][0] if z-1 < len(old) else self.all_blocks_spin.value()
            pc = old[z-1][1] if z-1 < len(old) else self.all_batteries_spin.value()
            frame = QFrame()
            frame.setFrameShape(QFrame.Box)
            frame.setStyleSheet(
                f"QFrame{{border:1px solid {T['line']};border-radius:4px;"
                f"background:{T['sunken']};padding:4px;}}"
            )
            col = QVBoxLayout(frame)
            col.setSpacing(4)
            col.setContentsMargins(8, 6, 8, 6)
            lbl = QLabel(f"Zone {z}")
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet(
                f"font-weight:bold;font-size:12px;color:{T['ink']};"
                "border:none;background:transparent;"
            )
            col.addWidget(lbl)
            for sub_lbl, spin_val in [("Blocks:", pb), ("Batteries:", pc)]:
                r = QHBoxLayout()
                sl = QLabel(sub_lbl)
                sl.setStyleSheet(f"font-size:12px;color:{T['muted']};border:none;background:transparent;")
                sl.setFixedWidth(58)
                sp = QSpinBox()
                if "Blocks" in sub_lbl:
                    sp.setRange(1, 50)
                else:
                    sp.setRange(0, 20)
                    sp.setToolTip("Battery containers in each block of this zone")
                sp.setValue(spin_val)
                sp.setFixedWidth(55)
                r.addWidget(sl); r.addWidget(sp)
                col.addLayout(r)
                if "Blocks" in sub_lbl:
                    blocks_spin = sp
                else:
                    cont_spin = sp
            self._zone_spins.append((blocks_spin, cont_spin))
            self.zone_inner_layout.addWidget(frame)

    # ── Table generators ──────────────────────────────────────────────────

    def _clear_table(self):
        # Save existing type/serial
        saved = {}
        for r in self._container_rows:
            row = r["table_row"]
            t_w = self.container_table.cellWidget(row, 2)
            s_i = self.container_table.item(row, 4)
            # BESS rows are remembered by what they are ("Battery 1"), so a
            # serial follows its unit when an option shifts the places
            saved[(r.get("zone_block", r["block"]), r.get("label", r["idx"]))] = {
                "type":   t_w.currentText() if t_w else "",
                "serial": s_i.text().strip() if s_i else "",
                # what this place in the block was generated as (BESS)
                "gen":    r.get("gen", ""),
            }
        self.container_table.setRowCount(0)
        self._container_rows = []
        return saved

    def _add_row(self, block: int, idx: int, eq_type: str,
                 label: str, serial: str = "",
                 serial_editable: bool = True,
                 bg_color: str = None):
        """Adds one row to the container table."""
        row = self.container_table.rowCount()
        self.container_table.insertRow(row)
        bg = QColor(bg_color) if bg_color else None

        for col, val in enumerate([str(block), str(idx)]):
            item = QTableWidgetItem(val)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            item.setTextAlignment(Qt.AlignCenter)
            if bg: item.setBackground(bg)
            self.container_table.setItem(row, col, item)

        # Equipment type dropdown
        type_combo = QComboBox()
        types = CONTAINER_TYPES_BY_PROJECT.get(self._project_type, ["Other"])
        type_combo.addItems(types)
        if eq_type in types:
            type_combo.setCurrentText(eq_type)
        self.container_table.setCellWidget(row, 2, type_combo)

        # Label / Description
        label_item = QTableWidgetItem(label)
        if bg: label_item.setBackground(bg)
        self.container_table.setItem(row, 3, label_item)

        # Serial Number
        serial_item = QTableWidgetItem(serial)
        if not serial_editable:
            serial_item.setFlags(serial_item.flags() & ~Qt.ItemIsEditable)
            serial_item.setBackground(QColor(T['sunken']))
        elif bg:
            serial_item.setBackground(bg)
        self.container_table.setItem(row, 4, serial_item)

        self._container_rows.append({
            "block": block, "idx": idx,
            "table_row": row,
        })

    def _bess_block_layout(self, batteries: int):
        """Equipment of one block, in order: the ticked options, then the
        battery containers. [(equipment type, label)]"""
        out = [(eq_type, eq_type) for cb, eq_type in self._bess_options if cb.isChecked()]
        out += [("Battery", "Battery {}".format(n)) for n in range(1, batteries + 1)]
        return out

    def _generate_bess_table(self):
        num_zones = self.zones_spin.value()
        if len(self._zone_spins) != num_zones:
            self._apply_zone_count()         # the zone count was changed without Apply
        empty = [z for z in range(1, num_zones + 1)
                 if not self._bess_block_layout(self._zone_spins[z - 1][1].value())]
        if empty:
            QMessageBox.warning(
                self, "Nothing in the block",
                "Zone {} has no equipment in its blocks. Tick at least one item "
                "in \"Each block has\" or set the number of batteries."
                .format(", ".join(str(z) for z in empty)))
            return

        saved = self._clear_table()
        self._container_rows = []
        self.container_table.setRowCount(0)
        fixed = {"Transformer", "RMU", "SCC Cabinet"}

        block_num = 0
        for z in range(1, num_zones + 1):
            bs, cs = self._zone_spins[z - 1]
            layout = self._bess_block_layout(cs.value())
            for b in range(1, bs.value() + 1):
                block_num += 1
                for idx, (eq_type, label) in enumerate(layout, start=1):
                    # What was typed into a row is kept only while that
                    # place still holds the same kind of equipment. After an
                    # option is ticked or unticked the places shift, and a
                    # transformer's serial must not land on a battery.
                    was = saved.get((block_num, label), {})
                    row = self.container_table.rowCount()
                    self.container_table.insertRow(row)
                    self._fill_bess_row(
                        row, block_num, idx, z, b, label,
                        was.get("type", "") or eq_type,
                        was.get("serial", ""),
                        T['warnBg'] if eq_type in fixed else None)
                    self._container_rows.append({
                        "zone": z, "block": b, "zone_block": block_num,
                        "idx": idx, "table_row": row, "gen": eq_type, "label": label,
                    })

        # say what was built, so a wrong tick is seen before Save
        per_zone = {}
        for z in range(1, num_zones + 1):
            names = []
            for eq_type, _label in self._bess_block_layout(self._zone_spins[z - 1][1].value()):
                if eq_type == "Battery":
                    continue
                names.append(eq_type)
            n = self._zone_spins[z - 1][1].value()
            if n:
                names.append("{} × Battery".format(n))
            per_zone.setdefault(", ".join(names), []).append(z)
        if len(per_zone) == 1:
            what = "each block: " + next(iter(per_zone))
        else:
            what = "; ".join("zone {}: {}".format(", ".join(str(z) for z in zs), k)
                             for k, zs in per_zone.items())
        self.bess_summary.setText("{} block(s) · {} item(s) · {}".format(
            block_num, len(self._container_rows), what))

    def _fill_bess_row(self, row, block_num, idx, z, b, c,
                        eq_type, serial, bg_hex=None):
        """Helper to fill one BESS table row."""
        bg = QColor(bg_hex) if bg_hex else None
        for col, val in enumerate([str(block_num), str(idx)]):
            item = QTableWidgetItem(val)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            item.setTextAlignment(Qt.AlignCenter)
            if bg: item.setBackground(bg)
            self.container_table.setItem(row, col, item)

        type_combo = QComboBox()
        type_combo.addItems(CONTAINER_TYPES_BY_PROJECT["BESS"])
        type_combo.setCurrentText(eq_type if eq_type in CONTAINER_TYPES_BY_PROJECT["BESS"] else "Other")
        self.container_table.setCellWidget(row, 2, type_combo)

        label_item = QTableWidgetItem(f"Z{z}/B{b} — {c}")
        if bg: label_item.setBackground(bg)
        self.container_table.setItem(row, 3, label_item)

        serial_item = QTableWidgetItem(serial)
        if bg: serial_item.setBackground(bg)
        self.container_table.setItem(row, 4, serial_item)

    def _generate_pvs_table(self):
        """
        PV String: per block →
          Transformer (optional), RMU (optional), Logger (optional),
          1 × MVS, N × String Inverters
        All items get individual serial numbers.
        """
        saved         = self._clear_table()
        blocks        = self.pvs_blocks_spin.value()
        inv_per_block = self.pvs_inv_spin.value()

        has_transformer = self.pvs_has_transformer.isChecked()
        has_rmu         = self.pvs_has_rmu.isChecked()
        has_logger      = self.pvs_has_logger.isChecked()

        for b in range(1, blocks + 1):
            idx = 1

            if has_transformer:
                key = (b, idx)
                self._add_row(b, idx, "Transformer",
                              f"Block {b} — Transformer",
                              saved.get(key, {}).get("serial", ""),
                              serial_editable=True, bg_color=T['warnBg'])
                idx += 1

            if has_rmu:
                key = (b, idx)
                self._add_row(b, idx, "RMU",
                              f"Block {b} — RMU",
                              saved.get(key, {}).get("serial", ""),
                              serial_editable=True, bg_color=T['warnBg'])
                idx += 1

            if has_logger:
                key = (b, idx)
                self._add_row(b, idx, "Logger",
                              f"Block {b} — Logger",
                              saved.get(key, {}).get("serial", ""),
                              serial_editable=True, bg_color=T['sunken'])
                idx += 1

            # MVS (always present)
            key = (b, idx)
            self._add_row(b, idx, "MVS",
                          f"Block {b} — MVS",
                          saved.get(key, {}).get("serial", ""),
                          serial_editable=True, bg_color=T['sunken'])
            idx += 1

            # String Inverters
            for i in range(1, inv_per_block + 1):
                key = (b, idx)
                self._add_row(b, idx, "String Inverter",
                              f"Block {b} — INV {i}",
                              saved.get(key, {}).get("serial", ""))
                idx += 1

    def _generate_pvc_table(self):
        """
        PV Central: per block builds in this order:
          Transformer, RMU, SCU Master, SCU Slave (if checked)
          Master Unit 1..N  — each own serial
          Slave Unit 1..N   — each own serial
          Combiner Boxes    — no serial
        """
        saved       = self._clear_table()
        blocks      = self.pvc_blocks_spin.value()
        masters     = self.pvc_master_spin.value()
        slaves      = self.pvc_slave_spin.value()
        cb_per_unit = self.pvc_cb_spin.value()
        total_units = masters + slaves

        has_transformer = self.pvc_has_transformer.isChecked()
        has_rmu         = self.pvc_has_rmu.isChecked()
        has_scu_master  = self.pvc_has_scu_master.isChecked()
        has_scu_slave   = self.pvc_has_scu_slave.isChecked()

        for b in range(1, blocks + 1):
            idx = 1

            if has_transformer:
                key = (b, idx)
                self._add_row(b, idx, "Transformer",
                              f"Block {b} — Transformer",
                              saved.get(key, {}).get("serial", ""),
                              serial_editable=True, bg_color=T['warnBg'])
                idx += 1

            if has_rmu:
                key = (b, idx)
                self._add_row(b, idx, "RMU",
                              f"Block {b} — RMU",
                              saved.get(key, {}).get("serial", ""),
                              serial_editable=True, bg_color=T['warnBg'])
                idx += 1

            if has_scu_master:
                key = (b, idx)
                self._add_row(b, idx, "SCU Master",
                              f"Block {b} — SCU Master",
                              saved.get(key, {}).get("serial", ""),
                              serial_editable=True, bg_color=T['okBg'])
                idx += 1

            if has_scu_slave:
                key = (b, idx)
                self._add_row(b, idx, "SCU Slave",
                              f"Block {b} — SCU Slave",
                              saved.get(key, {}).get("serial", ""),
                              serial_editable=True, bg_color=T['okBg'])
                idx += 1

            for m in range(1, masters + 1):
                key = (b, idx)
                self._add_row(b, idx, "Master Unit",
                              f"Block {b} — Master Unit {m}",
                              saved.get(key, {}).get("serial", ""),
                              serial_editable=True, bg_color=T['sunken'])
                idx += 1

            for s in range(1, slaves + 1):
                key = (b, idx)
                self._add_row(b, idx, "Slave Unit",
                              f"Block {b} — Slave Unit {s}",
                              saved.get(key, {}).get("serial", ""),
                              serial_editable=True, bg_color=T['waitBg'])
                idx += 1

            cb_total = total_units * cb_per_unit
            for cb in range(1, cb_total + 1):
                unit_num   = ((cb - 1) // cb_per_unit) + 1
                cb_in_unit = ((cb - 1) %  cb_per_unit) + 1
                self._add_row(b, idx, "Combiner Box",
                              f"Block {b} — Unit {unit_num} CB{cb_in_unit}",
                              "", serial_editable=False, bg_color=T['warnBg'])
                idx += 1

    # ── Save ──────────────────────────────────────────────────────────────

    def _save_project(self):
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Validation", "Project name is required.")
            return
        if not self._container_rows:
            QMessageBox.warning(self, "Validation",
                                "Please generate the container table first.")
            return

        containers = []

        if self._project_type == "BESS":
            for r in self._container_rows:
                row = r["table_row"]
                t_w = self.container_table.cellWidget(row, 2)
                s_i = self.container_table.item(row, 4)
                containers.append(Container(
                    project_id=0,
                    zone_number=r.get("zone", 1),
                    block_number=r.get("block", 1),
                    container_index=r["idx"],
                    container_type=t_w.currentText() if t_w else "Battery",
                    serial_number=s_i.text().strip() if s_i else "",
                ))

        elif self._project_type == "PV String":
            for r in self._container_rows:
                row = r["table_row"]
                t_w = self.container_table.cellWidget(row, 2)
                s_i = self.container_table.item(row, 4)
                containers.append(Container(
                    project_id=0,
                    zone_number=1,
                    block_number=r["block"],
                    container_index=r["idx"],
                    container_type=t_w.currentText() if t_w else "String Inverter",
                    serial_number=s_i.text().strip() if s_i else "",
                ))

        elif self._project_type == "PV Central":
            # Each unit saves its own serial independently.
            # Combiner boxes always save with empty serial.
            for r in self._container_rows:
                row = r["table_row"]
                t_w = self.container_table.cellWidget(row, 2)
                eq_type = t_w.currentText() if t_w else "Master Unit"
                s_i = self.container_table.item(row, 4)
                serial = s_i.text().strip() if s_i else ""
                if eq_type == "Combiner Box":
                    serial = ""
                containers.append(Container(
                    project_id=0,
                    zone_number=1,
                    block_number=r["block"],
                    container_index=r["idx"],
                    container_type=eq_type,
                    serial_number=serial,
                ))

        project = Project(
            name=name,
            description=self.desc_input.toPlainText().strip(),
            num_zones=self.zones_spin.value() if self._project_type == "BESS" else 1,
            num_blocks=max(r.get("zone_block", r["block"]) for r in self._container_rows),
            num_containers=max(r["idx"] for r in self._container_rows),
            project_type=self._project_type,
        )

        try:
            project_id = create_project(project)
            for c in containers:
                c.project_id = project_id
            save_containers(containers)
            QMessageBox.information(
                self, "Success",
                f"Project '{name}' ({self._project_type}) created "
                f"with {len(containers)} equipment items."
            )
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save:\n{str(e)}")
