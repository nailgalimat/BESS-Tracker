"""
ui/edit_project_dialog.py  (UPDATED)
--------------------------------------
Edit an existing project's zone/block/container configuration.

NEW: Blocks can now be removed.
  - If a block has NO log entries  → deleted immediately after confirmation
  - If a block HAS log entries     → blocked with a clear warning message
                                     (history must be preserved)

Colour coding in the table:
  🔵 Blue  = existing container (already in DB)
  🟢 Green = new container (will be inserted on Save)
  🔴 Red   = marked for deletion (empty blocks only)
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QSpinBox, QPushButton,
    QTableWidget, QTableWidgetItem, QComboBox, QMessageBox,
    QGroupBox, QScrollArea, QWidget, QFrame, QTextEdit, QLineEdit
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor

from models.models import Container, CONTAINER_TYPES, default_container_type
from services.project_service import (
    get_all_projects, get_containers_for_project,
)
from services.edit_serials_service import update_container_serial_and_type
from services.project_service import save_containers
from database.db_manager import get_connection


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_log_count_for_block(project_id: int, zone: int, block: int) -> int:
    """Returns how many log entries exist for a given zone/block."""
    conn = get_connection()
    row = conn.execute("""
        SELECT COUNT(*) as cnt
        FROM daily_logs dl
        JOIN containers c ON dl.container_id = c.id
        WHERE dl.project_id = ? AND c.zone_number = ? AND c.block_number = ?
    """, (project_id, zone, block)).fetchone()
    conn.close()
    return row["cnt"] if row else 0


def _delete_block_from_db(project_id: int, zone: int, block: int):
    """Deletes all containers for a block (only call after confirming zero logs)."""
    conn = get_connection()
    conn.execute("""
        DELETE FROM containers
        WHERE project_id = ? AND zone_number = ? AND block_number = ?
    """, (project_id, zone, block))
    conn.commit()
    conn.close()


# ── Dialog ────────────────────────────────────────────────────────────────────

class EditProjectDialog(QDialog):
    """Edit zone/block/container configuration for an existing project."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Project Configuration")
        self.setMinimumSize(920, 700)

        self._project_id = None
        self._existing   = []   # Container objects from DB
        self._all_rows   = []   # All rows currently shown in table
        # Tracks blocks the user has chosen to delete (only empty ones)
        self._blocks_to_delete = set()  # set of (zone, block) tuples

        self._build_ui()
        self._load_projects()

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Project selector
        sel_group = QGroupBox("Select Project to Edit")
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

        # Zone / block controls
        self.adjust_group = QGroupBox(
            "Structure  —  adjust blocks per zone, then click ⚙ Regenerate"
        )
        self.adjust_group.setEnabled(False)
        adj_outer = QVBoxLayout()

        zone_row = QHBoxLayout()
        zone_row.addWidget(QLabel("Total Zones:"))
        self.zones_spin = QSpinBox()
        self.zones_spin.setRange(1, 50)
        self.zones_spin.setFixedWidth(65)
        zone_row.addWidget(self.zones_spin)

        regen_btn = QPushButton("⚙  Regenerate Table")
        regen_btn.setFixedHeight(34)
        regen_btn.setStyleSheet(
            "QPushButton{background:#F57F17;color:white;font-weight:bold;"
            "border-radius:4px;padding:0 14px;}"
            "QPushButton:hover{background:#E65100;}"
        )
        regen_btn.clicked.connect(self._regenerate)
        zone_row.addWidget(regen_btn)
        zone_row.addStretch()
        adj_outer.addLayout(zone_row)

        # Per-zone block spinboxes
        self.zone_scroll = QScrollArea()
        self.zone_scroll.setWidgetResizable(True)
        self.zone_scroll.setMaximumHeight(90)
        self.zone_scroll.setFrameShape(QFrame.StyledPanel)
        self.zone_inner = QWidget()
        self.zone_inner_layout = QHBoxLayout(self.zone_inner)
        self.zone_inner_layout.setAlignment(Qt.AlignLeft)
        self.zone_inner_layout.setSpacing(6)
        self.zone_scroll.setWidget(self.zone_inner)
        adj_outer.addWidget(self.zone_scroll)

        self._zone_block_spins = []   # list of (zone_number, QSpinBox)
        self.adjust_group.setLayout(adj_outer)
        layout.addWidget(self.adjust_group)

        # Info / status label
        self.info_label = QLabel("Load a project to begin editing.")
        self.info_label.setStyleSheet("color:#555; font-style:italic;")
        layout.addWidget(self.info_label)

        # Container table
        table_group = QGroupBox(
            "Containers   "
            "[ 🔵 Existing  |  🟢 New (added on Save)  |  🔴 Marked for deletion ]"
        )
        tl = QVBoxLayout()

        self.container_table = QTableWidget(0, 7)
        self.container_table.setHorizontalHeaderLabels([
            "Zone", "Block", "Cont/Block", "Container #",
            "Type", "Serial Number", "Action"
        ])
        self.container_table.setColumnWidth(0, 50)
        self.container_table.setColumnWidth(1, 50)
        self.container_table.setColumnWidth(2, 80)
        self.container_table.setColumnWidth(3, 90)
        self.container_table.setColumnWidth(4, 160)
        self.container_table.setColumnWidth(5, 170)
        self.container_table.setColumnWidth(6, 110)
        self.container_table.horizontalHeader().setStretchLastSection(False)
        tl.addWidget(self.container_table)
        table_group.setLayout(tl)
        layout.addWidget(table_group)

        # Bottom buttons
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

    # ── Loading ───────────────────────────────────────────────────────────

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
        self._blocks_to_delete.clear()
        self._existing = get_containers_for_project(project_id)

        if not self._existing:
            QMessageBox.information(self, "Empty",
                "No containers found. Use 'New Project' to create one.")
            return

        num_zones = max(c.zone_number for c in self._existing)
        self.adjust_group.setEnabled(True)
        self.zones_spin.setValue(num_zones)
        self._rebuild_zone_spins(num_zones)
        self._populate_table_from_existing()

        total = len(self._existing)
        self.info_label.setText(
            f"Loaded {total} container{'s' if total != 1 else ''}. "
            "Edit Type/Serial directly. Use ⚙ Regenerate after changing block counts."
        )

    def _rebuild_zone_spins(self, num_zones: int):
        """Rebuilds per-zone block-count spinboxes. Allows both increase AND decrease."""
        while self.zone_inner_layout.count():
            item = self.zone_inner_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._zone_block_spins = []

        for z in range(1, num_zones + 1):
            blocks_in_zone = [
                c.block_number for c in self._existing if c.zone_number == z
            ]
            current_max = max(blocks_in_zone) if blocks_in_zone else 1

            frame = QFrame()
            frame.setFrameShape(QFrame.Box)
            frame.setStyleSheet(
                "QFrame{border:1px solid #90CAF9;border-radius:4px;"
                "padding:2px;background:#E3F2FD;}"
            )
            col_layout = QVBoxLayout(frame)
            col_layout.setSpacing(1)

            lbl = QLabel(f"Zone {z}")
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet(
                "font-weight:bold;font-size:11px;border:none;background:transparent;"
            )
            col_layout.addWidget(lbl)

            sub = QLabel("Blocks:")
            sub.setAlignment(Qt.AlignCenter)
            sub.setStyleSheet(
                "font-size:10px;color:#555;border:none;background:transparent;"
            )
            col_layout.addWidget(sub)

            spin = QSpinBox()
            spin.setRange(1, 50)   # allow any value — we check logs before deleting
            spin.setValue(current_max)
            spin.setToolTip(
                f"Zone {z}: currently {current_max} block(s).\n"
                "Decrease to remove blocks (only allowed if block has no log entries).\n"
                "Increase to add new blocks."
            )
            spin.setFixedWidth(58)
            col_layout.addWidget(spin, alignment=Qt.AlignCenter)

            self._zone_block_spins.append((z, spin))
            self.zone_inner_layout.addWidget(frame)

    # ── Table population ──────────────────────────────────────────────────

    def _populate_table_from_existing(self):
        self.container_table.setRowCount(0)
        self._all_rows = []

        # Group by (zone, block)
        blocks: dict = {}
        for c in self._existing:
            blocks.setdefault((c.zone_number, c.block_number), []).append(c)

        for (z, b), conts in sorted(blocks.items()):
            num_c = len(conts)
            for i, c in enumerate(sorted(conts, key=lambda x: x.container_index)):
                row = self.container_table.rowCount()
                self.container_table.insertRow(row)
                # Only show the delete button on the FIRST container row of each block
                self._fill_existing_row(
                    row, c,
                    show_count=num_c if i == 0 else None,
                    show_delete_btn=i == 0
                )
                self._all_rows.append({
                    "is_new": False,
                    "container_id": c.id,
                    "zone": c.zone_number,
                    "block": c.block_number,
                    "idx": c.container_index,
                    "table_row": row,
                })

    def _fill_existing_row(self, row, c, show_count, show_delete_btn=False):
        blue = QColor("#E3F2FD")

        for col, val in enumerate([str(c.zone_number), str(c.block_number)]):
            item = QTableWidgetItem(val)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            item.setTextAlignment(Qt.AlignCenter)
            item.setBackground(blue)
            self.container_table.setItem(row, col, item)

        if show_count is not None:
            cnt_item = QTableWidgetItem(str(show_count))
            cnt_item.setFlags(cnt_item.flags() & ~Qt.ItemIsEditable)
            cnt_item.setTextAlignment(Qt.AlignCenter)
            cnt_item.setBackground(blue)
            self.container_table.setItem(row, 2, cnt_item)
        else:
            ph = QTableWidgetItem("")
            ph.setFlags(ph.flags() & ~Qt.ItemIsEditable)
            ph.setBackground(blue)
            self.container_table.setItem(row, 2, ph)

        ci = QTableWidgetItem(str(c.container_index))
        ci.setFlags(ci.flags() & ~Qt.ItemIsEditable)
        ci.setTextAlignment(Qt.AlignCenter)
        ci.setBackground(blue)
        self.container_table.setItem(row, 3, ci)

        type_combo = QComboBox()
        type_combo.addItems(CONTAINER_TYPES)
        type_combo.setCurrentText(c.container_type)
        self.container_table.setCellWidget(row, 4, type_combo)

        serial_item = QTableWidgetItem(c.serial_number or "")
        self.container_table.setItem(row, 5, serial_item)

        # Action column — Delete button on first row of each block
        if show_delete_btn:
            del_btn = QPushButton("🗑  Delete Block")
            del_btn.setStyleSheet(
                "QPushButton{background:#EF9A9A;color:#B71C1C;border-radius:3px;"
                "font-size:11px;padding:2px 6px;}"
                "QPushButton:hover{background:#EF5350;color:white;}"
            )
            del_btn.setToolTip(
                "Delete this entire block.\n"
                "Only possible if the block has NO log entries."
            )
            del_btn.clicked.connect(
                lambda _, z=c.zone_number, b=c.block_number: self._request_delete_block(z, b)
            )
            self.container_table.setCellWidget(row, 6, del_btn)
        else:
            ph = QTableWidgetItem("")
            ph.setFlags(ph.flags() & ~Qt.ItemIsEditable)
            ph.setBackground(blue)
            self.container_table.setItem(row, 6, ph)

    def _add_new_row(self, zone, block, idx):
        """Adds a green new-container row."""
        row = self.container_table.rowCount()
        self.container_table.insertRow(row)
        green = QColor("#E8F5E9")

        for col, val in enumerate([str(zone), str(block)]):
            item = QTableWidgetItem(val)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            item.setTextAlignment(Qt.AlignCenter)
            item.setBackground(green)
            self.container_table.setItem(row, col, item)

        # Col 2: show +/- buttons on first new row of each block
        # so user can add/remove containers without regenerating
        is_first_new = not any(
            r for r in self._all_rows
            if r["zone"] == zone and r["block"] == block and r["is_new"]
        )
        if is_first_new:
            btn_widget = self._make_add_remove_widget(zone, block)
            self.container_table.setCellWidget(row, 2, btn_widget)
        else:
            ph = QTableWidgetItem("")
            ph.setFlags(ph.flags() & ~Qt.ItemIsEditable)
            ph.setBackground(green)
            self.container_table.setItem(row, 2, ph)

        ci = QTableWidgetItem(str(idx))
        ci.setFlags(ci.flags() & ~Qt.ItemIsEditable)
        ci.setTextAlignment(Qt.AlignCenter)
        ci.setBackground(green)
        self.container_table.setItem(row, 3, ci)

        type_combo = QComboBox()
        type_combo.addItems(CONTAINER_TYPES)
        type_combo.setCurrentText(default_container_type(idx))
        self.container_table.setCellWidget(row, 4, type_combo)

        serial_item = QTableWidgetItem("")
        self.container_table.setItem(row, 5, serial_item)

        # No delete button for new rows
        ph = QTableWidgetItem("")
        ph.setFlags(ph.flags() & ~Qt.ItemIsEditable)
        ph.setBackground(green)
        self.container_table.setItem(row, 6, ph)

        self._all_rows.append({
            "is_new": True,
            "container_id": None,
            "zone": zone, "block": block, "idx": idx,
            "table_row": row,
        })

    def _make_add_remove_widget(self, zone: int, block: int) -> QWidget:
        """Creates a small +/- widget for adding/removing containers in a new block."""
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(2, 1, 2, 1)
        layout.setSpacing(3)

        add_btn = QPushButton("+")
        add_btn.setFixedSize(26, 22)
        add_btn.setToolTip("Add one container to this block")
        add_btn.setStyleSheet(
            "QPushButton{background:#4CAF50;color:white;font-weight:bold;"
            "border-radius:3px;font-size:14px;padding:0;}"
            "QPushButton:hover{background:#388E3C;}"
        )
        add_btn.clicked.connect(
            lambda _, z=zone, b=block: self._add_container_to_new_block(z, b)
        )

        rem_btn = QPushButton("−")
        rem_btn.setFixedSize(26, 22)
        rem_btn.setToolTip("Remove last container from this block")
        rem_btn.setStyleSheet(
            "QPushButton{background:#EF5350;color:white;font-weight:bold;"
            "border-radius:3px;font-size:14px;padding:0;}"
            "QPushButton:hover{background:#C62828;}"
        )
        rem_btn.clicked.connect(
            lambda _, z=zone, b=block: self._remove_container_from_new_block(z, b)
        )

        layout.addWidget(add_btn)
        layout.addWidget(rem_btn)
        return widget

    def _add_container_to_new_block(self, zone: int, block: int):
        """Adds one more container row to a new block."""
        rows_in_block = [r for r in self._all_rows
                         if r["zone"] == zone and r["block"] == block]
        next_idx = max(r["idx"] for r in rows_in_block) + 1 if rows_in_block else 1
        self._add_new_row(zone, block, next_idx)

    def _remove_container_from_new_block(self, zone: int, block: int):
        """Removes the last container row from a new block (minimum 1)."""
        new_rows = [r for r in self._all_rows
                    if r["zone"] == zone and r["block"] == block and r["is_new"]]
        if len(new_rows) <= 1:
            QMessageBox.information(self, "Minimum",
                "A block must have at least 1 container.")
            return
        # Remove the last one
        last = max(new_rows, key=lambda r: r["idx"])
        self.container_table.removeRow(last["table_row"])
        self._all_rows = [r for r in self._all_rows
                          if not (r["zone"] == zone and r["block"] == block
                                  and r["idx"] == last["idx"] and r["is_new"])]
        # Fix table_row indices for rows that shifted down
        for r in self._all_rows:
            if r["table_row"] > last["table_row"]:
                r["table_row"] -= 1

    # ── Delete block logic ────────────────────────────────────────────────

    def _request_delete_block(self, zone: int, block: int):
        """
        Called when user clicks the Delete button for a block.
        Checks for log entries first:
          - Has logs  → show warning, refuse deletion
          - No logs   → ask confirmation, mark rows red
        """
        if self._project_id is None:
            return

        log_count = _get_log_count_for_block(self._project_id, zone, block)

        if log_count > 0:
            # Cannot delete — has history
            QMessageBox.warning(
                self,
                "Cannot Delete Block",
                f"Zone {zone} / Block {block} has {log_count} log "
                f"entr{'ies' if log_count != 1 else 'y'} recorded.\n\n"
                "Blocks with maintenance history cannot be deleted\n"
                "to preserve your records.\n\n"
                "If you need to decommission this block, you can\n"
                "edit its container types to 'Other' instead."
            )
            return

        # No logs — confirm deletion
        answer = QMessageBox.question(
            self,
            "Delete Block?",
            f"Delete all containers in Zone {zone} / Block {block}?\n\n"
            "This block has no log entries, so it is safe to remove.\n"
            "This action will be applied when you click Save Changes.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if answer != QMessageBox.Yes:
            return

        # Mark rows red in the table
        self._blocks_to_delete.add((zone, block))
        red = QColor("#FFCDD2")

        for r in self._all_rows:
            if r["zone"] == zone and r["block"] == block:
                t_row = r["table_row"]
                for col in range(self.container_table.columnCount()):
                    item = self.container_table.item(t_row, col)
                    if item:
                        item.setBackground(red)
                # Replace delete button with undo button
                undo_btn = QPushButton("↩  Undo")
                undo_btn.setStyleSheet(
                    "QPushButton{background:#FFE0B2;color:#E65100;border-radius:3px;"
                    "font-size:11px;padding:2px 6px;}"
                    "QPushButton:hover{background:#FF9800;color:white;}"
                )
                undo_btn.clicked.connect(
                    lambda _, z=zone, b=block: self._undo_delete_block(z, b)
                )
                # Find the first row for this block to place the undo button
                first_row = min(
                    r["table_row"] for r in self._all_rows
                    if r["zone"] == zone and r["block"] == block
                )
                self.container_table.setCellWidget(first_row, 6, undo_btn)
                break

        self.info_label.setText(
            f"Zone {zone} / Block {block} marked for deletion. "
            "Click Save to apply, or ↩ Undo to cancel."
        )

    def _undo_delete_block(self, zone: int, block: int):
        """Removes the deletion mark and restores blue colour."""
        self._blocks_to_delete.discard((zone, block))
        blue = QColor("#E3F2FD")

        for r in self._all_rows:
            if r["zone"] == zone and r["block"] == block:
                t_row = r["table_row"]
                for col in range(self.container_table.columnCount()):
                    item = self.container_table.item(t_row, col)
                    if item:
                        item.setBackground(blue)

        # Restore the delete button on the first row
        first_row = min(
            r["table_row"] for r in self._all_rows
            if r["zone"] == zone and r["block"] == block
        )
        del_btn = QPushButton("🗑  Delete Block")
        del_btn.setStyleSheet(
            "QPushButton{background:#EF9A9A;color:#B71C1C;border-radius:3px;"
            "font-size:11px;padding:2px 6px;}"
            "QPushButton:hover{background:#EF5350;color:white;}"
        )
        del_btn.clicked.connect(
            lambda _, z=zone, b=block: self._request_delete_block(z, b)
        )
        self.container_table.setCellWidget(first_row, 6, del_btn)
        self.info_label.setText("Deletion cancelled.")

    # ── Regenerate ────────────────────────────────────────────────────────

    def _regenerate(self):
        """Adds new rows for any zone/block/container that doesn't exist yet."""
        if self._project_id is None:
            QMessageBox.warning(self, "No project", "Load a project first.")
            return

        num_zones = self.zones_spin.value()

        # Add spinboxes for any new zones
        existing_zone_nums = [z for z, _ in self._zone_block_spins]
        for z in range(max(existing_zone_nums, default=0) + 1, num_zones + 1):
            frame = QFrame()
            frame.setFrameShape(QFrame.Box)
            frame.setStyleSheet(
                "QFrame{border:1px solid #A5D6A7;border-radius:4px;"
                "padding:2px;background:#E8F5E9;}"
            )
            col_layout = QVBoxLayout(frame)
            col_layout.setSpacing(1)
            lbl = QLabel(f"Zone {z} 🆕")
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet(
                "font-weight:bold;font-size:11px;border:none;background:transparent;"
            )
            col_layout.addWidget(lbl)
            sub = QLabel("Blocks:")
            sub.setAlignment(Qt.AlignCenter)
            sub.setStyleSheet(
                "font-size:10px;color:#555;border:none;background:transparent;"
            )
            col_layout.addWidget(sub)
            spin = QSpinBox()
            spin.setRange(1, 50)
            spin.setValue(1)
            spin.setFixedWidth(58)
            col_layout.addWidget(spin, alignment=Qt.AlignCenter)
            self._zone_block_spins.append((z, spin))
            self.zone_inner_layout.addWidget(frame)

        # Build set of already-known (zone, block, idx)
        existing_keys = {(r["zone"], r["block"], r["idx"]) for r in self._all_rows}

        added = 0
        for z, block_spin in self._zone_block_spins:
            if z > num_zones:
                continue
            num_blocks = block_spin.value()
            for b in range(1, num_blocks + 1):
                rows_in_block = [
                    r for r in self._all_rows
                    if r["zone"] == z and r["block"] == b
                ]
                current_max = (
                    max(r["idx"] for r in rows_in_block)
                    if rows_in_block else 0
                )
                target = max(current_max, 3 if not rows_in_block else current_max)

                for c in range(1, target + 1):
                    if (z, b, c) not in existing_keys:
                        self._add_new_row(z, b, c)
                        existing_keys.add((z, b, c))
                        added += 1

        if added == 0:
            QMessageBox.information(self, "No changes",
                "No new containers to add.\n"
                "Increase a block count or zone count first.")
        else:
            self.info_label.setText(
                f"Added {added} new row{'s' if added != 1 else ''} (green). "
                "Fill in Type and Serial Number, then click Save."
            )

    # ── Save ──────────────────────────────────────────────────────────────

    def _save_changes(self):
        if self._project_id is None:
            QMessageBox.warning(self, "No project", "Load a project first.")
            return

        updates = []
        new_containers = []

        for r in self._all_rows:
            # Skip rows marked for deletion
            if (r["zone"], r["block"]) in self._blocks_to_delete:
                continue

            row = r["table_row"]
            type_widget  = self.container_table.cellWidget(row, 4)
            serial_item  = self.container_table.item(row, 5)
            ctype  = type_widget.currentText() if type_widget else "Battery"
            serial = serial_item.text().strip() if serial_item else ""

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
            # 1. Delete marked blocks from DB
            for (zone, block) in self._blocks_to_delete:
                _delete_block_from_db(self._project_id, zone, block)

            # 2. Update existing containers
            if updates:
                update_container_serial_and_type(updates)

            # 3. Insert new containers
            if new_containers:
                save_containers(new_containers)

            deleted = len(self._blocks_to_delete)
            total   = len(updates) + len(new_containers)
            msg     = f"Saved successfully.\n"
            if deleted:
                msg += f"  • {deleted} block{'s' if deleted != 1 else ''} deleted\n"
            if updates:
                msg += f"  • {len(updates)} container{'s' if len(updates) != 1 else ''} updated\n"
            if new_containers:
                msg += f"  • {len(new_containers)} new container{'s' if len(new_containers) != 1 else ''} added\n"

            QMessageBox.information(self, "Saved", msg.strip())
            self.accept()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save:\n{str(e)}")
