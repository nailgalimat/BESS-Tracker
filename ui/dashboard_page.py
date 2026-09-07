"""
ui/dashboard_page.py
---------------------
Dashboard — KPI cards + recent events table.

FIXES:
  - N+1 query removed: container count now uses one SQL query
  - All DB calls in refresh() are batched, not looped
  - Dashboard does NOT auto-refresh on startup — user clicks Refresh
    (avoids slow startup when database is large)
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QSizePolicy, QFrame, QScrollArea
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont

from ui.components import KPICard, PageHeader, make_table, SecondaryButton
from ui.style import STATUS_ROW_COLORS, STATUS_TEXT_COLORS
from database.db_manager import get_connection


def _get_dashboard_stats() -> dict:
    """
    Single DB call that fetches ALL dashboard stats at once.
    No loops, no N+1 queries.
    """
    conn = get_connection()
    try:
        stats = {}

        stats['num_projects'] = conn.execute(
            "SELECT COUNT(*) FROM projects"
        ).fetchone()[0]

        stats['num_containers'] = conn.execute(
            "SELECT COUNT(*) FROM containers"
        ).fetchone()[0]

        stats['total_work_logs'] = conn.execute(
            "SELECT COUNT(*) FROM work_logs"
        ).fetchone()[0]

        # Status counts in one query
        rows = conn.execute("""
            SELECT status, COUNT(*) as cnt
            FROM work_logs
            GROUP BY status
        """).fetchall()
        status_counts = {r["status"]: r["cnt"] for r in rows}
        stats['status_counts'] = status_counts
        stats['open_issues'] = (
            status_counts.get("Not resolved", 0) +
            status_counts.get("Escalated", 0)
        )

        # Recent 20 work logs
        recent = conn.execute("""
            SELECT
                wl.id, wl.date, p.name AS project,
                wl.zone_number AS zone, wl.block_number AS block,
                wl.container_index AS container_num,
                wl.serial_number, wl.fault_description,
                wl.status, wl.sap_ticket
            FROM work_logs wl
            JOIN projects p ON wl.project_id = p.id
            ORDER BY wl.date DESC, wl.created_at DESC
            LIMIT 20
        """).fetchall()
        stats['recent_logs'] = [dict(r) for r in recent]

        return stats
    finally:
        conn.close()


class DashboardPage(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        # Do NOT auto-refresh on init — let user click Refresh
        # This keeps startup fast even with large databases

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = PageHeader("Dashboard", "Overview of your PV/BESS operations")
        refresh_btn = SecondaryButton("↻  Refresh")
        refresh_btn.clicked.connect(self.refresh)
        header.add_action(refresh_btn)
        layout.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:#F0F2F5;border:none;}")

        content = QWidget()
        content.setObjectName("PageArea")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(24, 20, 24, 24)
        cl.setSpacing(16)

        # ── KPI row 1: project/container/logs/issues ──────────────────────
        row1 = QHBoxLayout()
        row1.setSpacing(16)
        self.card_projects   = KPICard("Projects",          "—", icon="📁",  accent_color="#0071E3")
        self.card_containers = KPICard("Total Containers",  "—", icon="🔋",  accent_color="#34C759")
        self.card_work_logs  = KPICard("Work Logs",         "—", icon="🔧",  accent_color="#FF9500")
        self.card_issues     = KPICard("Open Issues",       "—", icon="⚠️", accent_color="#FF3B30")
        for c in [self.card_projects, self.card_containers,
                  self.card_work_logs, self.card_issues]:
            row1.addWidget(c)
        cl.addLayout(row1)

        # ── KPI row 2: status breakdown ───────────────────────────────────
        row2 = QHBoxLayout()
        row2.setSpacing(16)
        self.card_fixed      = KPICard("Fixed",        "—", icon="✅", accent_color="#34C759")
        self.card_monitoring = KPICard("Monitoring",   "—", icon="👁", accent_color="#FF9500")
        self.card_unresolved = KPICard("Not Resolved", "—", icon="❌", accent_color="#FF3B30")
        self.card_escalated  = KPICard("Escalated",    "—", icon="🚨", accent_color="#AF52DE")
        for c in [self.card_fixed, self.card_monitoring,
                  self.card_unresolved, self.card_escalated]:
            row2.addWidget(c)
        cl.addLayout(row2)

        # ── Recent events card ────────────────────────────────────────────
        events_card = QWidget()
        events_card.setObjectName("Card")
        ev_layout = QVBoxLayout(events_card)
        ev_layout.setContentsMargins(0, 0, 0, 0)
        ev_layout.setSpacing(0)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(16, 12, 16, 8)
        card_title = QLabel("Recent Maintenance Events")
        card_title.setStyleSheet("font-size:13px;font-weight:bold;color:#1A2B45;")
        title_row.addWidget(card_title)
        title_row.addStretch()
        sub = QLabel("Last 20 entries")
        sub.setStyleSheet("font-size:11px;color:#6B7A8D;")
        title_row.addWidget(sub)
        ev_layout.addLayout(title_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color:#F0F2F5;margin:0;")
        ev_layout.addWidget(sep)

        self.events_table = make_table([
            "ID","Date","Project","Zone","Block",
            "Container","Serial","Fault","Status","SAP Ticket"
        ], hide_id=True)
        self.events_table.setMinimumHeight(260)
        self.events_table.setStyleSheet(
            "QTableWidget{border:none;border-radius:0 0 8px 8px;}"
        )
        ev_layout.addWidget(self.events_table)
        cl.addWidget(events_card)
        cl.addStretch()

        scroll.setWidget(content)
        layout.addWidget(scroll)

    def refresh(self):
        """Fetches all stats in a single DB call and updates cards."""
        try:
            stats = _get_dashboard_stats()
            sc = stats['status_counts']

            self.card_projects.update_value(str(stats['num_projects']))
            self.card_containers.update_value(str(stats['num_containers']))
            self.card_work_logs.update_value(str(stats['total_work_logs']))
            self.card_issues.update_value(str(stats['open_issues']))
            self.card_fixed.update_value(str(sc.get("Fixed", 0)))
            self.card_monitoring.update_value(str(sc.get("Monitoring", 0)))
            self.card_unresolved.update_value(str(sc.get("Not resolved", 0)))
            self.card_escalated.update_value(str(sc.get("Escalated", 0)))

            self._populate_events(stats['recent_logs'])
        except Exception as e:
            print(f"[Dashboard] Error: {e}")

    def _populate_events(self, logs: list):
        self.events_table.setRowCount(0)
        cols = ["id","date","project","zone","block","container_num",
                "serial_number","fault_description","status","sap_ticket"]
        for row_data in logs:
            row_idx = self.events_table.rowCount()
            self.events_table.insertRow(row_idx)
            status_val = row_data.get("status","Fixed")
            bg = QColor(STATUS_ROW_COLORS.get(status_val,"#FFFFFF"))
            fg = QColor(STATUS_TEXT_COLORS.get(status_val,"#1A2B45"))
            for col_idx, key in enumerate(cols):
                val = str(row_data.get(key,""))
                display = val[:60]+"…" if len(val) > 60 else val
                item = QTableWidgetItem(display)
                item.setTextAlignment(Qt.AlignCenter)
                item.setBackground(bg)
                if key == "status":
                    item.setForeground(fg)
                    f = item.font(); f.setBold(True); item.setFont(f)
                self.events_table.setItem(row_idx, col_idx, item)
