"""ui/today_page.py — the first three minutes of the day.

Replaces Overview, which counted the wrong things ("Open issues 0" with a
dozen open phone records) and showed a maintenance event from four months ago.

Eight blocks, in a fixed order, each built from today_service:

  1 Needs your decision   2 Month data and report   3 Open faults
  4 PM campaign           5 Happening now           6 Phones and sync
  7 Stock below minimum   8 Action list

Two rules the page keeps:
  * every count is `len(rows)` of the very list its link opens — the page
    never computes a number a second way;
  * an empty block collapses to one line instead of showing an empty table.
"""
import datetime

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QSizePolicy,
)

import services.today_service as ts

SEV_COLOR = {'crit': '#FF3B30', 'warn': '#FF9500', 'info': '#6B7A8D',
             'ok': '#34C759', 'pend': '#AF52DE'}

_PANEL = ("QFrame#Panel{background:#FFFFFF;border:1px solid #E0E4EA;"
          "border-radius:6px;}")


def _chip(text, kind='info'):
    lbl = QLabel(text)
    col = SEV_COLOR.get(kind, '#6B7A8D')
    lbl.setStyleSheet(
        f"color:{col};border:1px solid {col};border-radius:8px;"
        "padding:1px 7px;font-size:11px;background:transparent;")
    lbl.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    return lbl


class Panel(QFrame):
    """One block of Today: title, a count, an optional link, and rows."""

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setObjectName("Panel")
        self.setStyleSheet(_PANEL)
        self._l = QVBoxLayout(self)
        self._l.setContentsMargins(14, 10, 14, 12)
        self._l.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.title_lbl = QLabel(f"<b>{title}</b>")
        self.title_lbl.setStyleSheet("font-size:13px;background:transparent;")
        head.addWidget(self.title_lbl)
        self.count_lbl = QLabel("")
        self.count_lbl.setStyleSheet(
            "background:#F0F2F5;border-radius:9px;padding:0 8px;"
            "font-weight:bold;color:#1A2B45;")
        head.addWidget(self.count_lbl)
        head.addStretch()
        self.link_btn = QPushButton("")
        self.link_btn.setFlat(True)
        self.link_btn.setCursor(Qt.PointingHandCursor)
        self.link_btn.setStyleSheet(
            "QPushButton{color:#0071E3;border:none;background:transparent;"
            "text-align:right;padding:0;}QPushButton:hover{text-decoration:underline;}")
        self.link_btn.setVisible(False)
        head.addWidget(self.link_btn)
        self._l.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setSpacing(4)
        self._l.addLayout(self.body)

    # ── content ──────────────────────────────────────────────────────────
    def set_count(self, n):
        self.count_lbl.setText(str(n) if n else "")
        self.count_lbl.setVisible(bool(n))

    def set_link(self, text, slot):
        self.link_btn.setText(text)
        self.link_btn.setVisible(bool(text))
        try:
            self.link_btn.clicked.disconnect()
        except TypeError:
            pass
        if slot:
            self.link_btn.clicked.connect(lambda _=False: slot())

    def clear(self):
        while self.body.count():
            it = self.body.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
            elif it.layout() is not None:
                lay = it.layout()
                while lay.count():
                    sub = lay.takeAt(0).widget()
                    if sub is not None:
                        sub.setParent(None)

    def empty(self, text):
        """An empty block is one line, not an empty table."""
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#6B7A8D;background:transparent;")
        lbl.setWordWrap(True)
        self.body.addWidget(lbl)

    def row(self, left, right='', chip=None, chip_kind='info',
            action=None, action_slot=None):
        line = QHBoxLayout()
        line.setSpacing(8)
        if chip:
            line.addWidget(_chip(chip, chip_kind))
        lbl = QLabel(left)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("background:transparent;")
        line.addWidget(lbl, 1)
        if right:
            r = QLabel(right)
            r.setStyleSheet("color:#6B7A8D;background:transparent;")
            line.addWidget(r)
        if action:
            b = QPushButton(action)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(24)
            b.setStyleSheet(
                "QPushButton{border:1px solid #C7CDD6;border-radius:4px;"
                "padding:1px 10px;background:#FFFFFF;}"
                "QPushButton:hover{border-color:#0071E3;color:#0071E3;}")
            if action_slot:
                b.clicked.connect(lambda _=False, s=action_slot: s())
            line.addWidget(b)
        self.body.addLayout(line)


class TodayPage(QWidget):
    """Signals the shell listens to; the argument is a menu label."""
    open_page = pyqtSignal(str)              # 'Work' / 'Availability' / …
    open_filtered = pyqtSignal(str, dict)    # menu label + a filter for it

    TARGET_LABEL = {ts.T_AVAILABILITY: 'Availability', ts.T_WORK: 'Work',
                    ts.T_REPORT: 'Monthly report', ts.T_PLAN: 'Plan',
                    ts.T_STOCK: 'Spare parts'}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pid = None
        self._name = ''
        self._year = None
        self._month = None
        self._data = None
        self._build_ui()

    # ── UI ───────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QHBoxLayout()
        bar.setContentsMargins(20, 10, 20, 4)
        self.head_lbl = QLabel("")
        self.head_lbl.setStyleSheet("color:#6B7A8D;background:transparent;")
        bar.addWidget(self.head_lbl)
        bar.addStretch()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.clicked.connect(self.refresh)
        bar.addWidget(self.refresh_btn)
        root.addLayout(bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        grid = QGridLayout(inner)
        grid.setContentsMargins(20, 6, 20, 20)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)

        self.p_decisions = Panel("Needs your decision")
        self.p_month = Panel("Month data and report")
        self.p_faults = Panel("Open faults")
        self.p_pm = Panel("PM campaign")
        self.p_now = Panel("Happening now")
        self.p_phones = Panel("Phones and sync")
        self.p_stock = Panel("Stock below minimum")
        self.p_actions = Panel("Action list")

        self._grid = grid
        self._cols = 0
        self._lay_out(3)
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

    # The window is not always maximised, and a three-column board on a
    # 1200 px window cut the right-hand panels off the screen. Lay the same
    # panels out in as many columns as the width really has.
    def _lay_out(self, cols: int):
        if cols == self._cols:
            return
        self._cols = cols
        grid = self._grid
        while grid.count():
            grid.takeAt(0)
        # these carry lists, so they get the wide slot
        wide = [self.p_decisions, self.p_faults, self.p_actions]
        order = [self.p_decisions, self.p_month, self.p_faults, self.p_pm,
                 self.p_now, self.p_phones, self.p_stock, self.p_actions]
        for c in range(3):
            grid.setColumnStretch(c, 0)
        if cols >= 3:
            grid.addWidget(self.p_decisions, 0, 0, 1, 2)
            grid.addWidget(self.p_month,     0, 2, 1, 1)
            grid.addWidget(self.p_faults,    1, 0, 1, 2)
            grid.addWidget(self.p_pm,        1, 2, 1, 1)
            grid.addWidget(self.p_now,       2, 0, 1, 1)
            grid.addWidget(self.p_phones,    2, 1, 1, 1)
            grid.addWidget(self.p_stock,     2, 2, 1, 1)
            grid.addWidget(self.p_actions,   3, 0, 1, 3)
            grid.setColumnStretch(0, 3)
            grid.setColumnStretch(1, 3)
            grid.setColumnStretch(2, 2)
            grid.setRowStretch(4, 1)
            return
        row, col = 0, 0
        for panel in order:
            span = 2 if (cols == 2 and panel in wide) else 1
            if col + span > cols:
                row, col = row + 1, 0
            grid.addWidget(panel, row, col, 1, span)
            col += span
            if col >= cols:
                row, col = row + 1, 0
        for c in range(cols):
            grid.setColumnStretch(c, 1)
        grid.setRowStretch(row + 1, 1)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        w = self.width()
        self._lay_out(3 if w >= 1180 else (2 if w >= 760 else 1))

    # ── shell hooks ──────────────────────────────────────────────────────
    def set_current_project(self, pid, name=None):
        self._pid = pid
        self._name = name or ''
        self.refresh()

    def set_month(self, year, month):
        self._year, self._month = year, month
        self.refresh()

    # ── data ─────────────────────────────────────────────────────────────
    def _open(self, target, flt=None):
        label = self.TARGET_LABEL.get(target)
        if not label:
            return
        if flt:
            self.open_filtered.emit(label, flt)
        else:
            self.open_page.emit(label)

    def refresh(self):
        for p in (self.p_decisions, self.p_month, self.p_faults, self.p_pm,
                  self.p_now, self.p_phones, self.p_stock, self.p_actions):
            p.clear()
            p.set_count(0)
            p.set_link("", None)
        if self._pid is None:
            self.head_lbl.setText("No project open")
            return
        today = datetime.date.today()
        if not self._year:
            self._year, self._month = today.year, today.month
        try:
            data = ts.summary(self._pid, self._year, self._month, today=today)
        except Exception as e:                           # noqa: BLE001
            self.p_decisions.empty(f"Could not read today's state: {e}")
            return
        self._data = data
        quiet = not (data['decisions'] or data['faults'] or data['now']
                     or data['stock'] or data.get('actions'))
        sync = data['phones']
        self.head_lbl.setText(
            f"{self._name} · {today.strftime('%d.%m.%Y')}"
            + (f" · synced {sync['last_sync'][11:16]}" if sync.get('last_sync') else
               " · sync off" if not sync.get('enabled') else "")
            + ("  ·  All quiet" if quiet else ""))

        self._fill_decisions(data['decisions'])
        self._fill_month(data['month'])
        self._fill_faults(data['faults'], data['stale'])
        self._fill_pm(data['pm'])
        self._fill_now(data['now'])
        self._fill_phones(sync)
        self._fill_stock(data['stock'])
        self._fill_actions(data.get('actions') or [])

    # ── blocks ───────────────────────────────────────────────────────────
    def _fill_decisions(self, rows):
        p = self.p_decisions
        p.set_count(len(rows))
        if not rows:
            p.empty("Nothing waiting for a decision.")
            return
        for r in rows[:6]:
            p.row(f"<b>{r['title']}</b> — {r['detail']}", chip=r['kind'].title(),
                  chip_kind=r['severity'],
                  action=r['action'] or "Open",
                  action_slot=lambda t=r['target'], f=r.get('filter'): self._open(t, f))
        if len(rows) > 6:
            p.row(f"+ {len(rows) - 6} more")
        p.set_link("Availability →", lambda: self._open(ts.T_AVAILABILITY))

    def _fill_month(self, m):
        p = self.p_month
        cov = f"{m['days_covered']} of {m['days_expected']} days loaded" \
            if m['days_expected'] else "no data yet"
        p.row("1 · Data", cov, chip="Data", chip_kind='warn' if
              m['days_covered'] < m['days_expected'] else 'ok')
        miss = len(m['missing'])
        p.row("Required files missing", str(miss) if miss else "none",
              chip_kind='crit' if miss else 'ok')
        vers = m['versions']
        p.row("Versions", f"v{len(vers)}" if vers else "none yet")
        if m['sent']:
            p.row("Sent", str(m['sent'].get('sent_original_name') or '')[:40],
                  chip="Sent", chip_kind='ok')
        elif m['locked']:
            p.row("Month locked", "", chip="Locked", chip_kind='ok')
        p.set_link("Open the report →", lambda: self._open(ts.T_REPORT))

    def _fill_faults(self, rows, stale):
        p = self.p_faults
        p.set_count(len(rows))
        if not rows:
            p.empty("No open fault records.")
            p.set_link("All records →", lambda: self._open(ts.T_WORK))
            return
        for r in rows[:6]:
            age = r.get('age_days')
            p.row(f"{r['node']} — {r['title'] or r['work_done'] or '(no text)'}",
                  f"{age} d" if age is not None else "",
                  chip=r['status'],
                  chip_kind='crit' if r['status'] == 'Needs visit' else 'warn')
        if len(rows) > 6:
            p.row(f"+ {len(rows) - 6} more")
        if stale:
            p.row(f"<b>{len(stale)}</b> older than 7 days", chip="Old",
                  chip_kind='crit')
        p.set_link(f"Open {len(rows)} open records →",
                   lambda: self._open(ts.T_WORK, {'tab': 'open', 'kind': 'Fault'}))

    def _fill_pm(self, pm):
        p = self.p_pm
        items = pm['items']
        p.set_count(len(items))
        if not items and not pm['pm_records']:
            p.empty("No PM planned this month.")
            p.set_link("Plan →", lambda: self._open(ts.T_PLAN))
            return
        p.row("Planned jobs", f"{len(pm['done'])} of {len(items)} done")
        if pm['overdue']:
            p.row("Overdue", str(len(pm['overdue'])), chip="Late", chip_kind='crit')
        if pm['today']:
            p.row("Today", ", ".join(
                f"block {i.get('block')}" for i in pm['today'][:4]))
        p.row("PM records", f"{len(pm['pm_records'])} · {pm['hours']:g} h")
        p.set_link("Open the plan →", lambda: self._open(ts.T_PLAN))

    def _fill_now(self, rows):
        p = self.p_now
        p.set_count(len(rows))
        if not rows:
            p.empty("Nothing open right now.")
            return
        for r in rows[:4]:
            p.row(f"<b>{r['title']}</b>", chip=r['kind'].title(),
                  chip_kind=r['severity'])
            p.row(r['detail'])
        p.set_link("Open →", lambda: self._open(rows[0]['target']))

    def _fill_phones(self, s):
        p = self.p_phones
        p.set_count(s['waiting'] + s['conflicts'] + len(s['stuck']))
        if not s['enabled']:
            p.empty("Sync is off — phone records are not coming in. "
                    "Project → Synchronisation.")
        else:
            p.row("Last sync", (s['last_sync'] or "never")[:16])
        p.row("Records waiting to send", str(s['waiting']),
              chip_kind='warn' if s['waiting'] else 'ok')
        if s['conflicts']:
            p.row("In conflict", str(s['conflicts']), chip="Conflict",
                  chip_kind='crit',
                  action="Resolve", action_slot=lambda: self._open(
                      ts.T_WORK, {'tab': 'conflict'}))
        for it in s['stuck'][:3]:
            p.row(f"{it['kind']} refused by the server",
                  str(it.get('error') or '')[:40], chip="Error", chip_kind='crit')

    def _fill_stock(self, rows):
        p = self.p_stock
        p.set_count(len(rows))
        if not rows:
            p.empty("Everything above its minimum.")
            return
        for r in rows[:5]:
            p.row(f"<b>{r.get('description') or r.get('material_number')}</b>",
                  f"{r.get('quantity'):g} left · min {r.get('min_quantity'):g}",
                  chip="Reorder", chip_kind='crit')
        p.set_link("Spare parts →", lambda: self._open(ts.T_STOCK))

    def _fill_actions(self, rows):
        """The office's own list — confirmations, BOMs, certificates. Nothing
        here is plant work, so nothing here reaches the customer's report."""
        p = self.p_actions
        p.set_count(len(rows))
        if not rows:
            p.empty("Nothing due in the next seven days.")
            p.set_link("Action list →",
                       lambda: self._open(ts.T_PLAN, {'tab': 'actions'}))
            return
        for r in rows[:6]:
            who = r.get('assigned_name') or ''
            p.row(f"<b>{r.get('topic') or '(no topic)'}</b>"
                  + (f" — {(r.get('todo') or '').splitlines()[0]}"
                     if r.get('todo') else ''),
                  (r.get('due_date') or '') + (f" · {who}" if who else ''),
                  chip="Overdue" if r.get('overdue') else "Due",
                  chip_kind='crit' if r.get('overdue') else 'warn')
        if len(rows) > 6:
            p.row(f"+ {len(rows) - 6} more")
        # The filter opens exactly these rows: the page asks the same service
        # function this count came from.
        p.set_link(f"Open {len(rows)} action item(s) →",
                   lambda: self._open(ts.T_PLAN, {'tab': 'actions',
                                                  'due': 'soon'}))
