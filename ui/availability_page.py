"""ui/availability_page.py — the month's availability inputs, as a page.

Everything that moves a month's availability besides the SCADA data used to be
a tab inside Monthly Reports, reachable only after pressing "Open month":
phone events waiting for the desktop, PM records, manual unavailability and
the exclusion windows. It is a page of its own now, in the menu under PLANT —
the Monthly report links to it and no longer carries the tab.

The editor itself is not rewritten or copied: this page **hosts** the widget
MonthlyReportsPage builds (`_inputs_tab`) together with its handlers and its
month lock, so there is exactly one implementation of the tables, the dialogs
and the validation. The page adds what the tab could not show:

  * a summary of what the month's inputs currently charge;
  * for every waiting phone event, what SCADA saw around it — the times the
    plant actually tripped and came back, to confirm against;
  * a way in that does not start with "Open month".

Availability *itself* (the percentage) is deliberately not recomputed here.
It comes out of the report generator, which owns the maths the customer sees;
a second implementation on this page could disagree with the report, and the
one number the customer reads must have one source.
"""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QScrollArea,
    QSizePolicy, QPushButton,
)

import services.availability_inputs_service as avi
import services.report_workflow_service as rw
from ui.components import PageHeader, SecondaryButton

MONTHS_EN = ['', 'January', 'February', 'March', 'April', 'May', 'June', 'July',
             'August', 'September', 'October', 'November', 'December']


class _Kpi(QFrame):
    def __init__(self, label, parent=None):
        super().__init__(parent)
        self.setObjectName("Kpi")
        self.setStyleSheet("QFrame#Kpi{background:#FFFFFF;border:1px solid #E0E4EA;"
                           "border-radius:6px;}")
        l = QVBoxLayout(self)
        l.setContentsMargins(12, 8, 12, 10)
        l.setSpacing(2)
        cap = QLabel(label)
        cap.setStyleSheet("color:#6B7A8D;font-size:11px;background:transparent;")
        l.addWidget(cap)
        self.value = QLabel("—")
        self.value.setStyleSheet("font-size:20px;font-weight:bold;background:transparent;")
        l.addWidget(self.value)
        self.sub = QLabel("")
        self.sub.setStyleSheet("color:#6B7A8D;font-size:11px;background:transparent;")
        self.sub.setWordWrap(True)
        l.addWidget(self.sub)

    def set(self, value, sub=''):
        self.value.setText(str(value))
        self.sub.setText(sub)


class AvailabilityPage(QWidget):
    open_report = pyqtSignal()

    def __init__(self, inputs_source=None, parent=None):
        """inputs_source: the MonthlyReportsPage that owns the inputs editor."""
        super().__init__(parent)
        self._src = inputs_source
        self._pid = None
        self._name = ''
        self._year = None
        self._month = None
        self._build_ui()

    # ── UI ───────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.header = PageHeader(
            "Availability",
            "The month's inputs: waiting phone events, PM records, manual "
            "downtime and exclusion windows")
        self.to_report = SecondaryButton("Open the monthly report")
        self.to_report.clicked.connect(lambda: self.open_report.emit())
        self.header.add_action(self.to_report)
        root.addWidget(self.header)

        kpis = QHBoxLayout()
        kpis.setContentsMargins(16, 10, 16, 6)
        kpis.setSpacing(10)
        self.k_wait = _Kpi("Waiting for you")
        self.k_pm = _Kpi("PM charged")
        self.k_manual = _Kpi("Manual downtime")
        self.k_excl = _Kpi("Excluded windows")
        for k in (self.k_wait, self.k_pm, self.k_manual, self.k_excl):
            k.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            kpis.addWidget(k)
        root.addLayout(kpis)

        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color:#6B7A8D;padding:0 18px 4px;")
        root.addWidget(self.note)

        # what SCADA saw around each waiting event
        self.sugg = QFrame()
        self.sugg.setStyleSheet(
            "QFrame{background:#F6F0FF;border:1px solid #AF52DE;border-radius:6px;}")
        sl = QVBoxLayout(self.sugg)
        sl.setContentsMargins(12, 8, 12, 10)
        sl.setSpacing(4)
        self.sugg_title = QLabel("<b>Waiting phone events — what SCADA saw</b>")
        self.sugg_title.setStyleSheet("background:transparent;")
        sl.addWidget(self.sugg_title)
        self.sugg_body = QVBoxLayout()
        sl.addLayout(self.sugg_body)
        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(16, 0, 16, 6)
        wl.addWidget(self.sugg)
        root.addWidget(wrap)
        self.sugg.setVisible(False)
        self._sugg_wrap = wrap
        wrap.setVisible(False)

        self.host = QVBoxLayout()
        self.host.setContentsMargins(0, 0, 0, 0)
        holder = QWidget()
        holder.setLayout(self.host)
        root.addWidget(holder, 1)

        self.placeholder = QLabel(
            "Open a project to see the month's availability inputs.")
        self.placeholder.setStyleSheet("color:#6B7A8D;padding:20px;")
        self.placeholder.setAlignment(Qt.AlignTop)
        self.host.addWidget(self.placeholder)

    def adopt_editor(self, widget):
        """Take the inputs editor out of the Monthly report and show it here."""
        if widget is None:
            return
        self.placeholder.setVisible(False)
        self.host.addWidget(widget, 1)
        widget.setVisible(True)

    # ── shell hooks ──────────────────────────────────────────────────────
    def set_current_project(self, pid, name=None):
        self._pid = pid
        self._name = name or ''
        self.refresh()

    def set_month(self, year, month):
        self._year, self._month = year, month
        self.refresh()

    def refresh(self):
        if self._pid is None or not self._year:
            return
        src = self._src
        if src is not None:
            # the page opens the month itself: nobody should have to press
            # "Open month" on another page to see this one's tables
            try:
                if (src._pid, src._year, src._month) != (self._pid, self._year, self._month):
                    src.set_month(self._year, self._month)
                    i = src.proj_combo.findData(self._pid)
                    if i >= 0:
                        src.proj_combo.setCurrentIndex(i)
                    src._open_month()
                else:
                    src._refresh_inputs()
            except Exception as e:                       # noqa: BLE001
                self.note.setText(f"Could not open the month: {e}")
        self.header.findChild(QLabel, "PageSubtitle")
        self._fill_summary()

    # ── summary ──────────────────────────────────────────────────────────
    def _fill_summary(self):
        try:
            data = avi.month_inputs(self._pid, self._year, self._month)
        except Exception as e:                           # noqa: BLE001
            self.note.setText(f"Could not read the month's inputs: {e}")
            return
        t = data['totals']
        self.k_wait.set(t['pending'],
                        "phone events that do not count until you confirm them"
                        if t['pending'] else "nothing waiting")
        self.k_pm.set(f"{rw.fmt_hours(t['pm_hours'])} h",
                      f"{t['pm_records']} record(s) · {t['pm_block_days']} block-day(s)")
        man_h = sum(float(r.get('downtime_h') or 0) for r in data['manual'])
        self.k_manual.set(f"{rw.fmt_hours(man_h)} h",
                          f"{t['manual_rows']} row(s) SCADA does not show")
        try:
            import services.availability_service as av
            excl = av.get_exclusions(project_id=self._pid, year=self._year,
                                     month=self._month)
        except Exception:                                # noqa: BLE001
            excl = []
        self.k_excl.set(len(excl), "grid, force majeure, switching — not our downtime")

        flags = t.get('flags') or 0
        self.note.setText(
            f"{MONTHS_EN[self._month]} {self._year} · {self._name}"
            + (f" · {flags} row(s) flagged for a second look" if flags else "")
            + "  ·  The availability percentage is produced by the monthly "
              "report from these inputs and the SCADA data — it is not "
              "computed a second time here.")
        self._fill_suggestions(data['pending'])

    def _fill_suggestions(self, pending):
        while self.sugg_body.count():
            it = self.sugg_body.takeAt(0)
            if it.widget() is not None:
                it.widget().setParent(None)
        shown = 0
        for q in pending:
            ev = q['event']
            if (q.get('kind') or ev.get('kind')) == 'pm':
                continue
            s = None
            try:
                s = avi.scada_suggestion(self._pid, ev)
            except Exception:                            # noqa: BLE001
                pass
            line = QLabel(
                f"<b>{avi.event_summary(ev)}</b><br>"
                + (f"SCADA: first trip {s['from']} → last restart {s['to'] or '—'} "
                   f"({s['blocks']} block(s), {s['rows']} alarm rows)"
                   if s else "No alarm data imported for those days yet — "
                             "add the month's files on Monthly report → Data."))
            line.setWordWrap(True)
            line.setStyleSheet("background:transparent;")
            self.sugg_body.addWidget(line)
            shown += 1
        self.sugg.setVisible(bool(shown))
        self._sugg_wrap.setVisible(bool(shown))
