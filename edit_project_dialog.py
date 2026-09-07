"""
ui/edit_project_dialog.py
--------------------------
Edit an existing project's container configuration.

Two modes:
  1. ADD mode  — keep existing containers, add new ones
  2. RESET mode — wipe all containers and rebuild from scratch
                  (only allowed if NO log entries exist for the project)

Zone cards work exactly like project_dialog.py:
  Each zone card has TWO spinboxes:
    - Blocks         (how many blocks in this zone)
    - Cont/Block     (how many containers per block)

Colour coding in the table:
  Blue  = existing container (already in DB)
  Green = new (will be added on Save)
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QSpinBox, QPushButton, QComboBox,
    QTableWidget, QTableWidgetItem, QMessageBox,
    QGroupBox, QScrollArea, QWidget, QFrame
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor

from models.models import Container, CONTAINER_TYPES, default_container_type
from services.project_service import (
    get_all_projects, get_containers_for_project, save_containers
)
from services.edit_serials_service import update_container_serial_and_type
from database.db_manager import get_connection


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_total_logs(project_id: int) -> int:
    """Returns total number of log entries for a project."""
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM daily_logs WHERE project_id = ?",
        (project_id,)
    ).fetchone()
    conn.close()
    return row["cnt"] if row else 0


def _delete_all_containers(project_id: int):
    """Deletes ALL containers for a project. Only call after confirming zero logs."""
    conn = get_connection()
    conn.execute("DELETE FROM containers WHERE project_id = ?", (project_id,))
    conn.commit()
    conn.close()


# ── Dialog ────────────────────────────────────────────────────────────────────

class EditProjectDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Project Configuration")
        self.setMinimumSize(900, 720)

        self._project_id  = None
        self._existing    = []   # Container objects from DB
        self._all_rows    = []   # {is_new, zone, block, idx, table_row}
        self._zone_spins  = []   # list of (blocks_spin, cont_spin) per zone
        self._reset_mode  = False  # True = all containers will be wiped on save

        self._build_ui()
        self._load_projects()

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # ── Project selector ──────────────────────────────────────────────
        sel_group = QGroupBox("Select Project")
        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel("Project:"))
        self.proj_combo = QComboBox()
        self.proj_combo.setMinimumWidth(260)
        sel_row.addWidget(self.proj_combo)

        load_btn = QPushButton("📂  Load")
        load_btn.setStyleSheet(
            "QPushButton{background:#1976D2;color:white;border-radius:4px;padding:4px 14px;}"
            "QPushButton:hover{background:#0D47A1;}"
        )
        load_btn.clicked.connect(self._load_project)
        sel_row.addWidget(load_btn)
        sel_row.addStretch()
        sel_group.setLayout(sel_row)
        layout.addWidget(sel_group)

        # ── Zone configuration (same as project_dialog) ───────────────────
        self.zone_group = QGroupBox(
            "Zone Configuration  —  set Blocks and Containers/Block per zone"
        )
        self.zone_group.setEnabled(False)
        zone_outer = QVBoxLayout()

        zone_count_row = QHBoxLayout()
        zone_count_row.addWidget(QLabel("Number of Zones:"))
        self.zones_spin = QSpinBox()
        self.zones_spin.setRange(1, 50)
        self.zones_spin.setFixedWidth(65)
        zone_count_row.addWidget(self.zones_spin)
        apply_btn = QPushButton("Apply")
        apply_btn.setFixedWidth(65)
        apply_btn.clicked.connect(self._apply_zone_count)
        zone_count_row.addWidget(apply_btn)
        zone_count_row.addWidget(
            QLabel("  ← click Apply after changing zone count")
        )
        zone_count_row.addStretch()
        zone_outer.addLayout(zone_count_row)

        self.zone_scroll = QScrollArea()
        self.zone_scroll.setWidgetResizable(True)
        self.zone_scroll.setMaximumHeight(130)
        self.zone_scroll.setFrameShape(QFrame.StyledPanel)
        self.zone_inner = QWidget()
        self.zone_inner_layout = QHBoxLayout(self.zone_inner)
        self.zone_inner_layout.setAlignment(Qt.AlignLeft)
        self.zone_inner_layout.setSpacing(8)
        self.zone_scroll.setWidget(self.zone_inner)
        zone_outer.addWidget(self.zone_scroll)
        self.zone_group.setLayout(zone_outer)
        layout.addWidget(self.zone_group)

        # ── Action buttons ────────────────────────────────────────────────
        action_row = QHBoxLayout()

        self.gen_btn = QPushButton("⚙  Generate / Update Table")
        self.gen_btn.setEnabled(False)
        self.gen_btn.setFixedHeight(36)
        self.gen_btn.setStyleSheet(
            "QPushButton{background:#F57F17;color:white;font-weight:bold;"
            "border-radius:4px;padding:0 16px;}"
            "QPushButton:hover{background:#E65100;}"
            "QPushButton:disabled{background:#aaa;}"
        )
        self.gen_btn.clicked.connect(self._generate_table)
        action_row.addWidget(self.gen_btn)

        action_row.addSpacing(20)

        self.reset_btn = QPushButton("🗑  Reset & Reconfigure")
        self.reset_btn.setEnabled(False)
        self.reset_btn.setFixedHeight(36)
        self.reset_btn.setToolTip(
            "Deletes ALL containers and rebuilds from scratch.\n"
            "Only available if the project has no log entries."
        )
        self.reset_btn.setStyleSheet(
            "QPushButton{background:#C62828;color:white;font-weight:bold;"
            "border-radius:4px;padding:0 16px;}"
            "QPushButton:hover{background:#8B0000;}"
            "QPushButton:disabled{background:#aaa;}"
        )
        self.reset_btn.clicked.connect(self._confirm_reset)
        action_row.addWidget(self.reset_btn)

        action_row.addStretch()
        layout.addLayout(action_row)

        # ── Status label ──────────────────────────────────────────────────
        self.info_label = QLabel("Load a project to begin.")
        self.info_label.setStyleSheet("color:#555; font-style:italic;")
        layout.addWidget(self.info_label)

        # ── Container table ───────────────────────────────────────────────
        table_group = QGroupBox(
            "Containers   "
            "[ 🔵 Existing  |  🟢 New (added on Save) ]"
        )
        tl = QVBoxLayout()
        self.container_table = QTableWidget(0, 5)
        self.container_table.setHorizontalHeaderLabels([
            "Zone", "Block", "Container #", "Type", "Serial Number"
        ])
        self.container_table.setColumnWidth(0, 60)
        self.container_table.setColumnWidth(1, 60)
        self.container_table.setColumnWidth(2, 90)
        self.container_table.setColumnWidth(3, 180)
        self.container_table.setColumnWidth(4, 200)
        self.container_table.horizontalHeader().setStretchLastSection(True)
        self.container_table.setAlternatingRowColors(True)
        tl.addWidget(self.container_table)
        table_group.setLayout(tl)
        layout.addWidget(table_group)

        # ── Bottom buttons ────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        save_btn = QPushButton("💾  Save Changes")
        save_btn.setFixedHeight(40)
        save_btn.setStyleSheet(
            "QPushButton{background:#4CAF50;color:white;font-weight:bold;"
            "border-radius:4px;padding:0 20px;}"
            "QPushButton:hover{background:#388E3C;}"
        )
        save_btn.clicked.connect(self._save_changes)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    # ── Project loading ───────────────────────────────────────────────────

    def _load_projects(self):
        self.proj_combo.clear()
        self.proj_combo.addItem("— Select Project —", userData=None)
        for p in get_all_projects():
            self.proj_combo.addItem(p.name, userData=p.id)

    def _load_project(self):
        project_id = self.proj_combo.currentData()
        if project_id is None:
            QMessageBox.warning(self, "No Project", "Please select a project.")
            return

        self._project_id = project_id
        self._existing   = get_containers_for_project(project_id)
        self._reset_mode = False

        if not self._existing:
            QMessageBox.information(self, "Empty",
                "No containers found. Use ⚙ Generate to build the structure.")

        # Determine current structure from existing containers
        zones = sorted(set(c.zone_number for c in self._existing)) if self._existing else [1]
        num_zones = max(zones) if zones else 1
        self.zones_spin.setValue(num_zones)

        # Build zone cards from existing data
        self.zone_group.setEnabled(True)
        self._build_zone_cards_from_existing(num_zones)

        # Check if reset is allowed (no logs)
        total_logs = _get_total_logs(project_id)
        self.reset_btn.setEnabled(total_logs == 0)
        self.gen_btn.setEnabled(True)

        if total_logs > 0:
            self.reset_btn.setToolTip(
                f"Cannot reset — this project has {total_logs} log entries.\n"
                "Log history must be preserved."
            )

        # Populate table with existing containers
        self._populate_from_existing()

        total = len(self._existing)
        log_msg = f"  ({total_logs} log entries — Reset disabled)" if total_logs > 0 else "  (no logs — Reset available)"
        self.info_label.setText(
            f"Loaded {total} existing container{'s' if total!=1 else ''}."
            f"{log_msg}"
        )

    # ── Zone cards ────────────────────────────────────────────────────────

    def _build_zone_cards_from_existing(self, num_zones: int):
        """Builds zone cards pre-filled from existing container data."""
        while self.zone_inner_layout.count():
            item = self.zone_inner_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._zone_spins = []

        for z in range(1, num_zones + 1):
            # Determine current blocks and containers from existing data
            blocks_in_zone = [c.block_number for c in self._existing
                              if c.zone_number == z]
            cur_blocks = max(blocks_in_zone) if blocks_in_zone else 1

            # Most common containers/block in this zone
            from collections import Counter
            cont_counts = Counter(
                len([c for c in self._existing
                     if c.zone_number == z and c.block_number == b])
                for b in set(blocks_in_zone)
            ) if blocks_in_zone else Counter()
            cur_cont = cont_counts.most_common(1)[0][0] if cont_counts else 3

            self._add_zone_card(z, cur_blocks, cur_cont)

    def _apply_zone_count(self):
        """Adds or removes zone cards when zone count changes."""
        num_zones = self.zones_spin.value()
        old_vals  = [(b.value(), c.value()) for b, c in self._zone_spins]

        while self.zone_inner_layout.count():
            item = self.zone_inner_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._zone_spins = []

        for z in range(1, num_zones + 1):
            prev_b = old_vals[z-1][0] if z-1 < len(old_vals) else 1
            prev_c = old_vals[z-1][1] if z-1 < len(old_vals) else 3
            self._add_zone_card(z, prev_b, prev_c)

    def _add_zone_card(self, z: int, blocks: int, cont_per_block: int):
        """Adds one zone card with Blocks and Cont/Block spinboxes."""
        frame = QFrame()
        frame.setFrameShape(QFrame.Box)
        frame.setStyleSheet(
            "QFrame{border:1px solid #90CAF9;border-radius:6px;"
            "background:#E3F2FD;padding:4px;}"
        )
        col = QVBoxLayout(frame)
        col.setSpacing(4)
        col.setContentsMargins(8, 6, 8, 6)

        lbl = QLabel(f"Zone {z}")
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(
            "font-weight:bold;font-size:12px;color:#1565C0;"
            "border:none;background:transparent;"
        )
        col.addWidget(lbl)

        # Blocks row
        blocks_row = QHBoxLayout()
        bl = QLabel("Blocks:")
        bl.setStyleSheet("font-size:10px;color:#555;border:none;background:transparent;")
        bl.setFixedWidth(48)
        blocks_spin = QSpinBox()
        blocks_spin.setRange(1, 50)
        blocks_spin.setValue(blocks)
        blocks_spin.setFixedWidth(55)
        blocks_row.addWidget(bl)
        blocks_row.addWidget(blocks_spin)
        col.addLayout(blocks_row)

        # Cont/Block row
        cont_row = QHBoxLayout()
        cl = QLabel("Cont/Blk:")
        cl.setStyleSheet("font-size:10px;color:#555;border:none;background:transparent;")
        cl.setFixedWidth(48)
        cont_spin = QSpinBox()
        cont_spin.setRange(1, 20)
        cont_spin.setValue(cont_per_block)
        cont_spin.setFixedWidth(55)
        cont_row.addWidget(cl)
        cont_row.addWidget(cont_spin)
        col.addLayout(cont_row)

        self._zone_spins.append((blocks_spin, cont_spin))
        self.zone_inner_layout.addWidget(frame)

    # ── Reset logic ───────────────────────────────────────────────────────

    def _confirm_reset(self):
        """Asks for confirmation before wiping all containers."""
        answer = QMessageBox.question(
            self, "Reset & Reconfigure?",
            "This will DELETE all containers for this project\n"
            "and let you rebuild the structure from scratch.\n\n"
            "⚠️  This cannot be undone.\n\n"
            "Are you sure?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if answer == QMessageBox.Yes:
            self._reset_mode = True
            self._existing   = []
            self._all_rows   = []
            self.container_table.setRowCount(0)
            self.info_label.setText(
                "Reset mode — configure zones above and click ⚙ Generate."
            )
            self.info_label.setStyleSheet("color:#C62828; font-weight:bold;")

    # ── Table generation ──────────────────────────────────────────────────

    def _populate_from_existing(self):
        """Fills table with existing containers (blue rows)."""
        self.container_table.setRowCount(0)
        self._all_rows = []

        for c in self._existing:
            row = self.container_table.rowCount()
            self.container_table.insertRow(row)
            self._fill_row(row, c.zone_number, c.block_number,
                           c.container_index, c.container_type,
                           c.serial_number or "", is_new=False)
            self._all_rows.append({
                "is_new":       False,
                "container_id": c.id,
                "zone":  c.zone_number,
                "block": c.block_number,
                "idx":   c.container_index,
                "table_row": row,
            })

    def _generate_table(self):
        """
        Generates the container table from zone card values.

        In RESET mode:  builds entirely fresh (no existing rows).
        In ADD mode:    keeps existing rows, adds green rows for anything new.
        """
        if not self._zone_spins:
            QMessageBox.warning(self, "No zones",
                "Please load a project and set up zone configuration first.")
            return

        # Save existing type/serial from table
        saved = {}
        for r in self._all_rows:
            row = r["table_row"]
            t_w = self.container_table.cellWidget(row, 3)
            s_i = self.container_table.item(row, 4)
            saved[(r["zone"], r["block"], r["idx"])] = {
                "type":   t_w.currentText() if t_w else "Battery",
                "serial": s_i.text().strip() if s_i else "",
            }

        self.container_table.setRowCount(0)
        self._all_rows = []

        num_zones = self.zones_spin.value()
        existing_keys = set()  # (zone, block, idx) already in DB

        if not self._reset_mode:
            # First add all existing containers (blue)
            for c in self._existing:
                row = self.container_table.rowCount()
                self.container_table.insertRow(row)
                key = (c.zone_number, c.block_number, c.container_index)
                s = saved.get(key, {})
                self._fill_row(
                    row, c.zone_number, c.block_number, c.container_index,
                    s.get("type", c.container_type),
                    s.get("serial", c.serial_number or ""),
                    is_new=False
                )
                self._all_rows.append({
                    "is_new": False,
                    "container_id": c.id,
                    "zone": c.zone_number, "block": c.block_number,
                    "idx": c.container_index, "table_row": row,
                })
                existing_keys.add(key)

        # Now add new rows from zone card config
        added = 0
        for z in range(1, num_zones + 1):
            if z - 1 >= len(self._zone_spins):
                break
            blocks_spin, cont_spin = self._zone_spins[z - 1]
            for b in range(1, blocks_spin.value() + 1):
                for c in range(1, cont_spin.value() + 1):
                    key = (z, b, c)
                    if key in existing_keys:
                        continue  # already shown
                    row = self.container_table.rowCount()
                    self.container_table.insertRow(row)
                    s = saved.get(key, {})
                    self._fill_row(
                        row, z, b, c,
                        s.get("type", default_container_type(c)),
                        s.get("serial", ""),
                        is_new=True
                    )
                    self._all_rows.append({
                        "is_new": True,
                        "container_id": None,
                        "zone": z, "block": b, "idx": c,
                        "table_row": row,
                    })
                    added += 1

        mode = "Reset mode" if self._reset_mode else "Add mode"
        new_total = len(self._all_rows)
        self.info_label.setText(
            f"{mode}: {new_total} container{'s' if new_total!=1 else ''} total"
            + (f"  ({added} new)" if added and not self._reset_mode else "")
        )
        self.info_label.setStyleSheet("color:#2E7D32; font-weight:bold;")

    def _fill_row(self, row: int, zone: int, block: int, idx: int,
                  ctype: str, serial: str, is_new: bool):
        """Fills one table row. Blue = existing, Green = new."""
        bg = QColor("#E8F5E9") if is_new else QColor("#E3F2FD")

        for col, val in enumerate([str(zone), str(block), str(idx)]):
            item = QTableWidgetItem(val)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            item.setTextAlignment(Qt.AlignCenter)
            item.setBackground(bg)
            self.container_table.setItem(row, col, item)

        # Type dropdown
        type_combo = QComboBox()
        type_combo.addItems(CONTAINER_TYPES)
        type_combo.setCurrentText(ctype if ctype in CONTAINER_TYPES
                                  else default_container_type(idx))
        self.container_table.setCellWidget(row, 3, type_combo)

        # Serial number
        serial_item = QTableWidgetItem(serial)
        serial_item.setBackground(bg)
        self.container_table.setItem(row, 4, serial_item)

    # ── Save ──────────────────────────────────────────────────────────────

    def _save_changes(self):
        if self._project_id is None:
            QMessageBox.warning(self, "No project", "Load a project first.")
            return
        if not self._all_rows:
            QMessageBox.warning(self, "Nothing to save",
                "Generate the table first.")
            return

        updates      = []   # existing containers to update
        new_containers = []

        for r in self._all_rows:
            row = r["table_row"]
            t_w = self.container_table.cellWidget(row, 3)
            s_i = self.container_table.item(row, 4)
            ctype  = t_w.currentText() if t_w else "Battery"
            serial = s_i.text().strip() if s_i else ""

            if not r["is_new"]:
                updates.append((r["container_id"], ctype, serial))
            else:
                new_containers.append(Container(
                    project_id=self._project_id,
                    zone_number=r["zone"],
                    block_number=r["block"],
                    container_index=r["idx"],
                    container_type=ctype,
                    serial_number=serial,
                ))

        try:
            if self._reset_mode:
                # Wipe all existing containers first
                _delete_all_containers(self._project_id)

            if updates and not self._reset_mode:
                update_container_serial_and_type(updates)

            if new_containers:
                save_containers(new_containers)

            total = len(updates) + len(new_containers)
            QMessageBox.information(
                self, "Saved",
                f"Saved {total} container{'s' if total!=1 else ''} successfully."
                + ("\n(Full reconfiguration applied)" if self._reset_mode else "")
            )
            self.accept()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save:\n{str(e)}")
