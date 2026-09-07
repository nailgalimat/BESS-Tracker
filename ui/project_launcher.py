"""
ui/project_launcher.py
-----------------------
The app's front door. Instead of opening straight onto a dashboard, the user
first picks a project — everything after that is scoped to it.

Emits:
  project_selected(int, str)  — a project card was clicked (id, name)
  new_project_requested()     — the "New project" affordance was clicked

Call reload() whenever the project list changes.
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QFrame,
    QScrollArea, QSizePolicy,
)
from PyQt5.QtCore import Qt, pyqtSignal

import services.report_workflow_service as rw


# Project-type → badge colour (background, text)
_BADGE = {
    'BESS':          ("#E8F0FD", "#0071E3"),
    'PV':            ("#FFF3E0", "#B26A00"),
    'PV+BESS':       ("#EAF7EE", "#1A7A3A"),
    'Commissioning': ("#FFF3E0", "#B26A00"),
}


class LauncherCard(QFrame):
    """A single clickable project tile."""
    clicked = pyqtSignal()

    def __init__(self, proj: dict, cfg: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("LauncherCard")
        self.setProperty("hover", "false")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(128)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._build(proj, cfg)

    def _build(self, proj, cfg):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(6)

        # ── top row: name + type badge ──────────────────────────────────
        top = QHBoxLayout(); top.setSpacing(8)
        name = QLabel(proj.get('name', '—'))
        name.setStyleSheet("font-size:16px;font-weight:bold;color:#1A2B45;background:transparent;")
        name.setWordWrap(True)
        top.addWidget(name, 1)

        ptype = (proj.get('project_type') or 'BESS').strip() or 'BESS'
        bg, fg = _BADGE.get(ptype, _BADGE['BESS'])
        badge = QLabel(ptype)
        badge.setStyleSheet(
            f"background:{bg};color:{fg};border-radius:9px;padding:2px 10px;"
            f"font-size:10px;font-weight:bold;")
        badge.setAlignment(Qt.AlignCenter)
        top.addWidget(badge, 0, Qt.AlignTop)
        lay.addLayout(top)

        # ── capacity / size line ────────────────────────────────────────
        cap = (cfg.get('project_capacity_str') or '').strip()
        nblk = int(proj.get('num_blocks') or 0)
        if not cap:
            cap = f"{nblk} block{'s' if nblk != 1 else ''}" if nblk else "—"
        cap_lbl = QLabel(cap)
        cap_lbl.setStyleSheet("font-size:13px;color:#3C4B56;background:transparent;")
        lay.addWidget(cap_lbl)

        lay.addStretch()

        # ── footer: report template + open hint ─────────────────────────
        foot = QHBoxLayout()
        tmpl = (cfg.get('site_type') or '').strip()
        tmpl_txt = f"{tmpl.capitalize()} report" if tmpl else "Not configured"
        meta = QLabel(tmpl_txt)
        meta.setStyleSheet("font-size:11px;color:#6B7A8D;background:transparent;")
        foot.addWidget(meta)
        foot.addStretch()
        openhint = QLabel("Open  →")
        openhint.setObjectName("OpenHint")
        openhint.setStyleSheet("font-size:12px;color:#0071E3;font-weight:bold;background:transparent;")
        foot.addWidget(openhint)
        lay.addLayout(foot)

        # let all clicks fall through to the frame
        for ch in self.findChildren(QLabel):
            ch.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def enterEvent(self, e):
        self._set_hover(True); super().enterEvent(e)

    def leaveEvent(self, e):
        self._set_hover(False); super().leaveEvent(e)

    def _set_hover(self, on):
        self.setProperty("hover", "true" if on else "false")
        self.style().unpolish(self); self.style().polish(self)


class NewProjectCard(QFrame):
    """Dashed 'add' tile at the end of the grid."""
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("NewCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(128)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        lay = QVBoxLayout(self); lay.setAlignment(Qt.AlignCenter)
        plus = QLabel("＋"); plus.setAlignment(Qt.AlignCenter)
        plus.setStyleSheet("font-size:26px;color:#0071E3;background:transparent;")
        txt = QLabel("New project"); txt.setAlignment(Qt.AlignCenter)
        txt.setStyleSheet("font-size:13px;color:#0071E3;font-weight:bold;background:transparent;")
        lay.addWidget(plus); lay.addWidget(txt)
        for ch in self.findChildren(QLabel):
            ch.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class ProjectLauncher(QWidget):
    project_selected = pyqtSignal(int, str)
    new_project_requested = pyqtSignal()

    COLUMNS = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self.reload()

    def _build_ui(self):
        self.setStyleSheet("""
            #LauncherCard {
                background:#FFFFFF; border:1px solid #E0E4EA; border-radius:10px;
            }
            #LauncherCard[hover="true"] {
                border:1px solid #0071E3; background:#FBFDFF;
            }
            #NewCard {
                background:transparent; border:2px dashed #C8CDD6; border-radius:10px;
            }
            #NewCard:hover { border-color:#0071E3; }
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── header band ─────────────────────────────────────────────────
        head = QWidget(); head.setObjectName("LauncherHead")
        head.setStyleSheet(
            "#LauncherHead{background:#FFFFFF;border-bottom:1px solid #E0E4EA;}")
        hl = QVBoxLayout(head); hl.setContentsMargins(28, 20, 28, 18); hl.setSpacing(2)
        t = QLabel("Select a project")
        t.setStyleSheet("font-size:22px;font-weight:bold;color:#1A2B45;background:transparent;")
        hl.addWidget(t)
        s = QLabel("Everything you do — daily logs, work reports, PM, unavailability, the monthly report — lives inside the project you pick.")
        s.setStyleSheet("font-size:12px;color:#6B7A8D;background:transparent;")
        s.setWordWrap(True)
        hl.addWidget(s)
        root.addWidget(head)

        # ── scrollable grid ─────────────────────────────────────────────
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;background:#F0F2F5;}")
        self._content = QWidget()
        self._grid = QGridLayout(self._content)
        self._grid.setContentsMargins(28, 24, 28, 28)
        self._grid.setHorizontalSpacing(16)
        self._grid.setVerticalSpacing(16)
        self._grid.setAlignment(Qt.AlignTop)
        for c in range(self.COLUMNS):
            self._grid.setColumnStretch(c, 1)
        scroll.setWidget(self._content)
        root.addWidget(scroll, 1)

        self._empty = QLabel(
            "No projects yet — click “New project” to create your first one.")
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setStyleSheet("color:#6B7A8D;font-size:13px;background:transparent;")

    # ── data ────────────────────────────────────────────────────────────
    def reload(self):
        # clear grid
        while self._grid.count():
            it = self._grid.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)

        try:
            projects = rw.list_projects()
        except Exception:
            projects = []

        r = c = 0
        for p in projects:
            try:
                cfg = rw.get_project_config(p['id']) or {}
            except Exception:
                cfg = {}
            card = LauncherCard(p, cfg)
            pid, pname = p['id'], p.get('name', '')
            card.clicked.connect(
                lambda _pid=pid, _pn=pname: self.project_selected.emit(_pid, _pn))
            self._grid.addWidget(card, r, c)
            c += 1
            if c >= self.COLUMNS:
                c = 0; r += 1

        # trailing "new project" tile
        newc = NewProjectCard()
        newc.clicked.connect(self.new_project_requested.emit)
        self._grid.addWidget(newc, r, c)
