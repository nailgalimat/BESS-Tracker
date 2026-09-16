"""ui/project_hub_page.py — Project: settings, people, sync and the archive.

The last of the nine navigation items. It gathers what used to sit as five
loose buttons at the bottom of the sidebar (New / Edit project, Edit serials,
Sync settings, Users) and it is the only door to the pages that left the menu
in the redesign: they keep working exactly as before, their data is untouched,
and this page lists them under "Archive of old pages".

The page owns no data of its own — it asks the shell (MainWindow) to navigate
or to open a dialog through two signals.
"""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QGroupBox, QScrollArea, QFrame, QSizePolicy,
)

from ui.components import PageHeader, PrimaryButton, SecondaryButton


class ProjectHubPage(QWidget):
    # page index the shell should open
    page_requested = pyqtSignal(int)
    # one of: new_project / edit_project / edit_serials / sync / users
    action_requested = pyqtSignal(str)

    def __init__(self, archive_items=None, settings_page=None, parent=None):
        """archive_items: [(label, page_index, one-line what-it-was)]
        settings_page:  page index of the report-settings page (Project Setup)"""
        super().__init__(parent)
        self._archive = archive_items or []
        self._settings_page = settings_page
        self._project_name = None
        self._build_ui()

    # ── UI ───────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.header = PageHeader("Project",
                                 "Report settings, people, synchronisation and the archive")
        root.addWidget(self.header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        l = QVBoxLayout(inner)
        l.setContentsMargins(24, 20, 24, 24)
        l.setSpacing(16)

        # ── Settings and tools ───────────────────────────────────────────
        g = QGroupBox("Settings and tools")
        gl = QGridLayout(g)
        gl.setHorizontalSpacing(10)
        gl.setVerticalSpacing(8)
        tools = [
            ("Report settings", "Customer, capacities, signatures, report number",
             lambda: self.page_requested.emit(self._settings_page), True),
            ("Edit project", "Name, blocks, containers of this project",
             lambda: self.action_requested.emit("edit_project"), False),
            ("New project", "Add another plant",
             lambda: self.action_requested.emit("new_project"), False),
            ("Serial numbers", "Bulk-edit container serial numbers",
             lambda: self.action_requested.emit("edit_serials"), False),
            ("Synchronisation", "Server, login, what is waiting from the phones",
             lambda: self.action_requested.emit("sync"), False),
            ("Users and roles", "Who may log in from a phone",
             lambda: self.action_requested.emit("users"), False),
        ]
        for i, (label, hint, slot, primary) in enumerate(tools):
            b = (PrimaryButton if primary else SecondaryButton)(label)
            b.setMinimumWidth(190)
            b.clicked.connect(lambda _=False, s=slot: s())
            if label == "Report settings" and self._settings_page is None:
                b.setEnabled(False)
            gl.addWidget(b, i // 2, (i % 2) * 2)
            h = QLabel(hint)
            h.setStyleSheet("color:#6B7A8D;")
            h.setWordWrap(True)
            gl.addWidget(h, i // 2, (i % 2) * 2 + 1)
        gl.setColumnStretch(1, 1)
        gl.setColumnStretch(3, 1)
        l.addWidget(g)

        # ── Archive ──────────────────────────────────────────────────────
        ag = QGroupBox("Archive of old pages")
        al = QVBoxLayout(ag)
        note = QLabel(
            "These pages left the menu in the new layout. <b>Nothing was deleted</b> — "
            "they open unchanged and their data is the same data the new pages read. "
            "They stay here for one release, while the new screens take over.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#6B7A8D;")
        al.addWidget(note)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        self._archive_buttons = []
        for i, (label, idx, hint) in enumerate(self._archive):
            b = QPushButton(label)
            b.setObjectName("ArchiveBtn")
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumHeight(32)
            b.setMinimumWidth(190)
            b.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            b.clicked.connect(lambda _=False, p=idx: self.page_requested.emit(p))
            grid.addWidget(b, i // 2, (i % 2) * 2)
            h = QLabel(hint)
            h.setStyleSheet("color:#6B7A8D;")
            h.setWordWrap(True)
            grid.addWidget(h, i // 2, (i % 2) * 2 + 1)
            self._archive_buttons.append((label, idx, b))
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        al.addLayout(grid)
        l.addWidget(ag)

        l.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

    # ── Shell hooks ──────────────────────────────────────────────────────
    def set_current_project(self, pid, name=None):
        self._project_name = name
        sub = self.header.findChild(QLabel, "PageSubtitle")
        if sub is not None:
            sub.setText((name + " · " if name else "")
                        + "Report settings, people, synchronisation and the archive")

    def archive_pages(self):
        """Page indices reachable from the archive — used by the shell and tests."""
        return [idx for _label, idx, _b in self._archive_buttons]
