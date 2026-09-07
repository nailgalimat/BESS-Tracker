"""
services/availability_service.py
---------------------------------
Manages availability exclusions for monthly reports.

Exclusion types:
  - Scheduled Maintenance — planned outage by O&M team
  - Grid Outage           — utility-side downtime
  - Force Majeure         — natural disasters, etc.
  - Major Fault           — exceptional events to be reviewed separately

Each exclusion has:
  date_from, date_to    — date range
  time_from, time_to    — HH:MM range within the date range
                          ('00:00' to '23:59' = full day)
"""

from database.db_manager import get_connection
from typing import Optional, List
from datetime import datetime, timedelta
import pandas as pd

EXCLUSION_TYPES = [
    "Scheduled Maintenance",
    "Grid Outage",
    "Force Majeure",
    "Major Fault",
]


def get_exclusions(project_id: Optional[int] = None,
                    date_from: Optional[str] = None,
                    date_to: Optional[str] = None,
                    year: Optional[int] = None,
                    month: Optional[int] = None) -> List[dict]:
    """Returns exclusions optionally filtered by project, report-month and/or
    date range."""
    conn = get_connection()
    try:
        query = "SELECT * FROM availability_exclusions WHERE 1=1"
        params = []
        if project_id is not None:
            query += " AND (project_id=? OR project_id IS NULL)"
            params.append(project_id)
        if year is not None:
            query += " AND year=?"; params.append(year)
        if month is not None:
            query += " AND month=?"; params.append(month)
        if date_from:
            query += " AND date_to >= ?"
            params.append(date_from)
        if date_to:
            query += " AND date_from <= ?"
            params.append(date_to)
        query += " ORDER BY date_from DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def add_exclusion(exclusion_type: str, date_from: str, date_to: str,
                   time_from: str = "00:00", time_to: str = "23:59",
                   affected_blocks: str = "",
                   description: str = "",
                   project_id: Optional[int] = None,
                   year: Optional[int] = None,
                   month: Optional[int] = None) -> int:
    """Returns the new exclusion id."""
    if exclusion_type not in EXCLUSION_TYPES:
        raise ValueError(f"Invalid exclusion type: {exclusion_type}")

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO availability_exclusions
                (project_id, exclusion_type, date_from, date_to,
                 time_from, time_to, affected_blocks, description, year, month)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (project_id, exclusion_type, date_from, date_to,
              time_from, time_to, affected_blocks, description, year, month))
        new_id = cur.lastrowid
        conn.commit()
        return new_id
    finally:
        conn.close()


def update_exclusion(exclusion_id: int, exclusion_type: str, date_from: str,
                     date_to: str, time_from: str = "00:00",
                     time_to: str = "23:59", affected_blocks: str = "",
                     description: str = "", year: Optional[int] = None,
                     month: Optional[int] = None):
    """Update an existing exclusion in place (project_id left unchanged).
    year/month are updated only when provided (None leaves them as-is)."""
    if exclusion_type not in EXCLUSION_TYPES:
        raise ValueError(f"Invalid exclusion type: {exclusion_type}")
    conn = get_connection()
    try:
        if year is not None or month is not None:
            conn.execute("""
                UPDATE availability_exclusions
                   SET exclusion_type=?, date_from=?, date_to=?,
                       time_from=?, time_to=?, affected_blocks=?, description=?,
                       year=?, month=?
                 WHERE id=?
            """, (exclusion_type, date_from, date_to, time_from, time_to,
                  affected_blocks, description, year, month, int(exclusion_id)))
        else:
            conn.execute("""
                UPDATE availability_exclusions
                   SET exclusion_type=?, date_from=?, date_to=?,
                       time_from=?, time_to=?, affected_blocks=?, description=?
                 WHERE id=?
            """, (exclusion_type, date_from, date_to, time_from, time_to,
                  affected_blocks, description, int(exclusion_id)))
        conn.commit()
    finally:
        conn.close()


def delete_exclusion(exclusion_id: int):
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM availability_exclusions WHERE id=?",
            (exclusion_id,)
        )
        conn.commit()
    finally:
        conn.close()


# ── Cycle-balancing / rested blocks (operator-recorded, Tashkent report) ─────
# Blocks intentionally held out of dispatch to balance equivalent full cycles
# across the fleet (annual 365-cycle budget). Informational only: it does NOT
# change the 770 MWh contractual availability. The report uses it to annotate
# these blocks as *deliberately rested* so their low cycles / low RTE are not
# flagged as underperformance.

def get_balancing_periods(project_id: Optional[int] = None,
                          date_from: Optional[str] = None,
                          date_to: Optional[str] = None,
                          year: Optional[int] = None,
                          month: Optional[int] = None) -> List[dict]:
    """Returns balancing periods, optionally filtered by project, report-month
    and/or date range."""
    conn = get_connection()
    try:
        query = "SELECT * FROM balancing_periods WHERE 1=1"
        params = []
        if project_id is not None:
            query += " AND (project_id=? OR project_id IS NULL)"
            params.append(project_id)
        if year is not None:
            query += " AND year=?"; params.append(year)
        if month is not None:
            query += " AND month=?"; params.append(month)
        if date_from:
            query += " AND date_to >= ?"
            params.append(date_from)
        if date_to:
            query += " AND date_from <= ?"
            params.append(date_to)
        query += " ORDER BY date_from DESC"
        return [dict(r) for r in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def add_balancing_period(date_from: str, date_to: str,
                         affected_blocks: str = "", note: str = "",
                         project_id: Optional[int] = None,
                         year: Optional[int] = None,
                         month: Optional[int] = None) -> int:
    """Returns the new balancing-period id."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO balancing_periods
                (project_id, date_from, date_to, affected_blocks, note, year, month)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (project_id, date_from, date_to, affected_blocks, note, year, month))
        new_id = cur.lastrowid
        conn.commit()
        return new_id
    finally:
        conn.close()


def update_balancing_period(period_id: int, date_from: str, date_to: str,
                            affected_blocks: str = "", note: str = "",
                            year: Optional[int] = None,
                            month: Optional[int] = None):
    """Update an existing balancing period in place (project_id unchanged)."""
    conn = get_connection()
    try:
        if year is not None or month is not None:
            conn.execute("""
                UPDATE balancing_periods
                   SET date_from=?, date_to=?, affected_blocks=?, note=?, year=?, month=?
                 WHERE id=?
            """, (date_from, date_to, affected_blocks, note, year, month, int(period_id)))
        else:
            conn.execute("""
                UPDATE balancing_periods
                   SET date_from=?, date_to=?, affected_blocks=?, note=?
                 WHERE id=?
            """, (date_from, date_to, affected_blocks, note, int(period_id)))
        conn.commit()
    finally:
        conn.close()


def delete_balancing_period(period_id: int):
    conn = get_connection()
    try:
        conn.execute("DELETE FROM balancing_periods WHERE id=?", (period_id,))
        conn.commit()
    finally:
        conn.close()


def rested_blocks_for_period(balancing_periods: List[dict],
                             period_start, period_end) -> set:
    """Set of block ids marked as intentionally rested that overlap the given
    reporting window [period_start, period_end]. `affected_blocks` empty/'all'
    means the whole fleet. Robust to '1,2,3' and '1-5' range syntax."""
    ps = pd.to_datetime(period_start).normalize()
    pe = pd.to_datetime(period_end).normalize()
    out = set()
    for b in (balancing_periods or []):
        try:
            d_from = pd.to_datetime(b['date_from']).normalize()
            d_to   = pd.to_datetime(b['date_to']).normalize()
        except Exception:
            continue
        if d_to < ps or d_from > pe:          # no overlap with report month
            continue
        aff = (b.get('affected_blocks') or '').strip()
        if not aff or aff.lower() in ('all', 'all blocks'):
            return set(range(1, 71))          # whole fleet rested
        for tok in aff.split(','):
            tok = tok.strip()
            if not tok:
                continue
            if '-' in tok:                    # range "a-b"
                try:
                    a, z = (int(x) for x in tok.split('-', 1))
                    out.update(range(min(a, z), max(a, z) + 1))
                except ValueError:
                    continue
            else:
                try:
                    out.add(int(tok))
                except ValueError:
                    continue
    return out


# ── Manual unavailability (operator-recorded downtime, Tashkent report) ─────
# Same UX/storage pattern as exclusions. Rows feed the 4.4.1 table, the
# availability heatmap and the contractual availability; entries dated outside
# the reported month are ignored by the report engine automatically.

def get_manual_unavailability(project_id: Optional[int] = None,
                              year: Optional[int] = None,
                              month: Optional[int] = None) -> List[dict]:
    """Operator-recorded unavailability entries, newest first. When year/month
    (and optionally project_id) are given, only that report-month's rows are
    returned (rows with NULL year match nothing under a month filter)."""
    conn = get_connection()
    try:
        q = "SELECT * FROM manual_unavailability WHERE 1=1"
        p = []
        if project_id is not None:
            q += " AND (project_id=? OR project_id IS NULL)"; p.append(project_id)
        if year is not None:
            q += " AND year=?"; p.append(year)
        if month is not None:
            q += " AND month=?"; p.append(month)
        q += " ORDER BY date_from DESC"
        return [dict(r) for r in conn.execute(q, p).fetchall()]
    finally:
        conn.close()


def add_manual_unavailability(block: int, date_from: str, date_to: str,
                               downtime_h: float, lc: Optional[int] = None,
                               cause: str = "",
                               project_id: Optional[int] = None,
                               year: Optional[int] = None,
                               month: Optional[int] = None) -> int:
    """Returns the new entry id. lc None = whole block (both LCs)."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO manual_unavailability
                (block, lc, date_from, date_to, downtime_h, cause,
                 project_id, year, month)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (int(block), lc, date_from, date_to, float(downtime_h), cause,
              project_id, year, month))
        new_id = cur.lastrowid
        conn.commit()
        return new_id
    finally:
        conn.close()


def delete_manual_unavailability(entry_id: int):
    conn = get_connection()
    try:
        conn.execute("DELETE FROM manual_unavailability WHERE id=?",
                     (entry_id,))
        conn.commit()
    finally:
        conn.close()


def _exclusion_hours(exc: dict,
                      period_start=None,
                      period_end=None) -> float:
    """
    Returns the total HOURS covered by one exclusion event.

    Logic:
      - If date_from == date_to → just (time_to - time_from)
      - If multi-day → first day partial + full days + last day partial

    If period_start / period_end (date objects) are supplied, the
    exclusion is clipped to that window before the hours are summed.
    Hours falling outside the reporting period are not counted.
    """
    try:
        d_from = pd.to_datetime(exc["date_from"]).date()
        d_to   = pd.to_datetime(exc["date_to"]).date()
    except Exception:
        return 0.0

    t_from = exc.get("time_from") or "00:00"
    t_to   = exc.get("time_to")   or "23:59"
    try:
        h_from, m_from = map(int, t_from.split(":"))
        h_to,   m_to   = map(int, t_to.split(":"))
    except ValueError:
        h_from = m_from = 0
        h_to, m_to = 23, 59

    minutes_from = h_from * 60 + m_from
    minutes_to   = h_to   * 60 + m_to

    # ── Clip to reporting period (if provided) ────────────────────────────
    if period_start is not None and d_from < period_start:
        d_from        = period_start
        minutes_from  = 0
    if period_end is not None and d_to > period_end:
        d_to        = period_end
        minutes_to  = 24 * 60 - 1   # 23:59

    # Entire exclusion fell outside the window
    if d_from > d_to:
        return 0.0

    if d_from == d_to:
        diff_min = max(0, minutes_to - minutes_from + 1)
        return diff_min / 60.0

    # Multi-day: first partial day + full middle days + last partial day
    first_day_minutes = (24 * 60) - minutes_from
    last_day_minutes  = minutes_to + 1
    full_days         = (d_to - d_from).days - 1
    total_minutes     = first_day_minutes + last_day_minutes + (full_days * 24 * 60)
    return total_minutes / 60.0


def calculate_excluded_hours(exclusions: List[dict],
                              all_dates: List,
                              all_blocks: List[int],
                              total_devices: int) -> dict:
    """
    Calculates total device-hours to exclude.

    Args:
        exclusions:    list of exclusion dicts from get_exclusions()
        all_dates:     list of dates in the reporting period
        all_blocks:    list of block numbers in the project
        total_devices: total devices

    Returns:
        excluded_device_hours: int
        breakdown: {exclusion_type: hours}
    """
    breakdown = {t: 0 for t in EXCLUSION_TYPES}
    total_excluded_device_hours = 0

    if not all_blocks:
        all_blocks = [1]

    devices_per_block = total_devices / len(all_blocks)

    period_start = min(all_dates) if all_dates else None
    period_end   = max(all_dates) if all_dates else None

    for exc in exclusions:
        # Skip if exclusion is entirely outside the reporting period
        try:
            d_from = pd.to_datetime(exc["date_from"]).date()
            d_to   = pd.to_datetime(exc["date_to"]).date()
        except Exception:
            continue
        if period_start and d_to   < period_start: continue
        if period_end   and d_from > period_end:   continue

        # Clip to the reporting window so multi-day exclusions that
        # straddle the boundary only contribute their in-period hours.
        hours = _exclusion_hours(exc,
                                  period_start=period_start,
                                  period_end=period_end)

        # Determine affected blocks
        affected_str = (exc.get("affected_blocks") or "").strip()
        if not affected_str or affected_str.lower() in ("all", "all blocks"):
            n_affected = len(all_blocks)
        else:
            try:
                n_affected = len([b for b in affected_str.split(",") if b.strip()])
            except ValueError:
                n_affected = len(all_blocks)

        affected_devices = n_affected * devices_per_block
        device_hours     = hours * affected_devices

        breakdown[exc["exclusion_type"]] = breakdown.get(
            exc["exclusion_type"], 0) + device_hours
        total_excluded_device_hours += device_hours

    return {
        "excluded_device_hours": int(total_excluded_device_hours),
        "breakdown": breakdown,
    }


def match_alarm_to_exclusions(activated_dt,
                                block_id,
                                exclusions: List[dict]) -> Optional[dict]:
    """
    Return the first exclusion that covers (activated_dt, block_id), or None.

    Match rules (all must hold):
      - activated_dt is between exclusion start datetime and end datetime
        (using the same date_from + time_from / date_to + time_to fields
         that _exclusion_hours uses, so semantics are consistent)
      - if the exclusion lists affected_blocks, block_id must be in the
        list; if affected_blocks is empty / "all" / "all blocks", any
        block_id matches (including None — that's how plant-wide alarms
        get covered by plant-wide exclusions)
      - if block_id is None and affected_blocks is non-empty, no match
        (a per-block exclusion should not cover an unattributable alarm)

    Returns the exclusion dict on first match; the alarm is considered
    excluded under that exclusion_type even if multiple overlap.
    """
    if activated_dt is None or pd.isna(activated_dt):
        return None
    activated_ts = pd.to_datetime(activated_dt, errors='coerce')
    if pd.isna(activated_ts):
        return None

    # Normalise block_id to int if numeric, else None
    block_id_int = None
    if block_id is not None and not (isinstance(block_id, float)
                                       and pd.isna(block_id)):
        try:
            block_id_int = int(block_id)
        except (TypeError, ValueError):
            block_id_int = None

    for exc in exclusions or []:
        try:
            d_from = pd.to_datetime(exc["date_from"])
            d_to   = pd.to_datetime(exc["date_to"])
        except Exception:
            continue
        t_from = exc.get("time_from") or "00:00"
        t_to   = exc.get("time_to")   or "23:59"
        try:
            hf, mf = map(int, t_from.split(":"))
            ht, mt = map(int, t_to.split(":"))
        except ValueError:
            hf = mf = 0
            ht, mt = 23, 59

        start_dt = d_from.normalize() + pd.Timedelta(hours=hf, minutes=mf)
        end_dt   = d_to.normalize()   + pd.Timedelta(hours=ht, minutes=mt)

        if not (start_dt <= activated_ts <= end_dt):
            continue

        affected_str = (exc.get("affected_blocks") or "").strip()
        if not affected_str or affected_str.lower() in ("all", "all blocks"):
            return exc

        if block_id_int is None:
            continue   # per-block exclusion can't cover an unattributable alarm
        try:
            affected_ids = {int(b.strip()) for b in affected_str.split(",")
                              if b.strip()}
        except ValueError:
            continue
        if block_id_int in affected_ids:
            return exc

    return None


def calculate_plant_excluded_hours(exclusions: List[dict],
                                     all_dates: List,
                                     all_blocks: List[int]) -> dict:
    """
    Plant-wall-clock variant of calculate_excluded_hours, used by the block-
    level monthly reports (Bukhara, Tashkent). Returns hours weighted by
    the fraction of blocks affected, so the result is directly comparable
    to plant_outage_hours from calc_plant_hours_availability.

    Args:
        exclusions: list of exclusion dicts from get_exclusions()
        all_dates:  list of dates in the reporting period
        all_blocks: list of block numbers in the project (full plant)

    Returns:
        {
            "excluded_hours": float,         # plant-wall-clock hours
            "breakdown":      {type: hours},
            "events":         [ {exc_dict, hours_in_period, weighted_hours}, ... ],
        }

    The weighting mirrors SCADA's calculate_excluded_hours:
        weighted = raw_event_hours × (n_affected_blocks / total_blocks)
    so that an exclusion affecting all blocks contributes its full duration
    and one affecting only some blocks contributes proportionally.
    """
    breakdown = {t: 0.0 for t in EXCLUSION_TYPES}
    events: List[dict] = []
    total_excluded_hours = 0.0

    if not all_blocks:
        all_blocks = [1]
    n_total_blocks = len(all_blocks)

    period_start = min(all_dates) if all_dates else None
    period_end   = max(all_dates) if all_dates else None

    for exc in exclusions or []:
        try:
            d_from = pd.to_datetime(exc["date_from"]).date()
            d_to   = pd.to_datetime(exc["date_to"]).date()
        except Exception:
            continue
        if period_start and d_to   < period_start: continue
        if period_end   and d_from > period_end:   continue

        raw_hours = _exclusion_hours(exc,
                                       period_start=period_start,
                                       period_end=period_end)
        if raw_hours <= 0:
            continue

        affected_str = (exc.get("affected_blocks") or "").strip()
        if not affected_str or affected_str.lower() in ("all", "all blocks"):
            n_affected = n_total_blocks
        else:
            try:
                n_affected = len([b for b in affected_str.split(",") if b.strip()])
            except ValueError:
                n_affected = n_total_blocks
        n_affected = max(0, min(n_affected, n_total_blocks))

        weighted = raw_hours * (n_affected / n_total_blocks) if n_total_blocks else 0.0

        breakdown[exc["exclusion_type"]] = breakdown.get(
            exc["exclusion_type"], 0.0) + weighted
        total_excluded_hours += weighted
        events.append({
            "exclusion":      exc,
            "hours_in_period": raw_hours,
            "weighted_hours":  weighted,
            "n_affected":     n_affected,
        })

    return {
        "excluded_hours": total_excluded_hours,
        "breakdown":      breakdown,
        "events":         events,
    }
