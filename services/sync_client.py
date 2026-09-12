"""
services/sync_client.py
------------------------
REST client that connects the desktop app to the Sync API.

Handles:
  - Login / token refresh
  - Push pending work_log_entries and images
  - Pull delta from server and apply to local SQLite
  - Auto-retry with refreshed token on 401

Returns SyncResult (namedtuple) for every sync cycle.
"""

import json
import os
import uuid
from collections import namedtuple
from datetime import datetime, timezone
from typing import Optional

import requests
from requests.exceptions import RequestException

from database.db_manager import get_connection
from services.sync_config import sync_config
from services.image_service import get_images_for_log

TIMEOUT = 15   # seconds per request
RETRIES = 3    # attempts for transient TLS / connection drops
RETRY_WAIT = 1.5


def _with_retry(fn):
    """Run a request, retrying transient TLS/connection drops.

    Render's edge occasionally resets a TLS connection mid-handshake, which
    surfaces as SSLEOFError ('UNEXPECTED_EOF_WHILE_READING') even though the
    server is healthy. A browser retries silently; requests does not, so a
    perfectly good login looked like a hard failure. Only connection-level
    errors are retried — an HTTP error response is returned as-is.
    """
    import time
    from requests.exceptions import SSLError, ConnectionError as _ConnErr, Timeout
    last = None
    for attempt in range(RETRIES):
        try:
            return fn()
        except (SSLError, _ConnErr, Timeout) as ex:
            last = ex
            if attempt < RETRIES - 1:
                time.sleep(RETRY_WAIT * (attempt + 1))
    raise last

SyncResult = namedtuple("SyncResult", [
    "pushed", "pulled", "conflicts", "errors", "skipped"
])


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _headers() -> dict:
    return {"Authorization": f"Bearer {sync_config.access_token}"}


# ── Auth ──────────────────────────────────────────────────────────────────────

def login(server_url: str, username: str, password: str) -> dict:
    """
    Authenticate against the server.
    Updates sync_config tokens and saves.
    Returns the user dict on success, raises on failure.
    """
    url = server_url.strip().rstrip("/")
    # Be forgiving: the PWA lives at …/app but the REST API is at the root.
    # If someone pastes the phone URL (…/app), posting to /app/auth/login hits
    # the static mount and returns 405 — so strip a trailing /app here.
    if url.endswith("/app"):
        url = url[:-4].rstrip("/")
    resp = _with_retry(lambda: requests.post(
        f"{url}/auth/login",
        json={"username": username, "password": password,
              "device_id": sync_config.device_id},
        timeout=TIMEOUT,
    ))
    resp.raise_for_status()
    data = resp.json()

    sync_config.server_url    = url
    sync_config.username      = username
    sync_config.access_token  = data["access_token"]
    sync_config.refresh_token = data["refresh_token"]
    sync_config.enabled       = True
    sync_config.save()
    return data["user"]


def refresh_access_token() -> bool:
    """Try to refresh the access token. Returns True on success."""
    if not sync_config.refresh_token:
        return False
    try:
        resp = requests.post(
            f"{sync_config.server_url}/auth/refresh",
            json={"refresh_token": sync_config.refresh_token,
                  "device_id": sync_config.device_id},
            timeout=TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            sync_config.access_token = data["access_token"]
            # Server rotates the refresh token — store the new one
            if data.get("refresh_token"):
                sync_config.refresh_token = data["refresh_token"]
            sync_config.save()
            return True
    except RequestException:
        pass
    return False


def _request(method: str, path: str, extra_headers: Optional[dict] = None, **kwargs):
    """Make an authenticated request; retry once after token refresh on 401."""
    url = f"{sync_config.server_url}{path}"
    def _hdrs():
        h = _headers()
        if extra_headers:
            h.update(extra_headers)
        return h
    resp = _with_retry(
        lambda: getattr(requests, method)(url, headers=_hdrs(), timeout=TIMEOUT, **kwargs))
    if resp.status_code == 401:
        if refresh_access_token():
            resp = _with_retry(
                lambda: getattr(requests, method)(url, headers=_hdrs(),
                                                  timeout=TIMEOUT, **kwargs))
    return resp


def ping() -> bool:
    """Returns True if the server is reachable and the token is valid."""
    try:
        resp = _request("get", "/auth/me")
        return resp.status_code == 200
    except RequestException:
        return False


# ── Push ──────────────────────────────────────────────────────────────────────

def _get_pending_entries() -> list:
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM work_log_entries
            WHERE sync_status IN ('local', 'pending')
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _get_pending_images() -> list:
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT i.*, e.sync_status AS entry_sync
            FROM work_log_images i
            JOIN work_log_entries e ON i.work_log_id = e.id
            WHERE i.upload_status = 'local'
              AND e.deleted_at IS NULL
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _get_tags_for_entry(entry_id: str) -> list:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT tag FROM work_log_tags WHERE work_log_id=?", (entry_id,)
        ).fetchall()
        return [r["tag"] for r in rows]
    finally:
        conn.close()


def _mark_entry_synced(entry_id: str, server_version=None):
    """Mark pushed, and remember which server version the row now matches —
    the base the next edit will be sent against."""
    conn = get_connection()
    try:
        if server_version is not None:
            conn.execute(
                "UPDATE work_log_entries SET sync_status='synced', version=? WHERE id=?",
                (int(server_version), entry_id))
        else:
            conn.execute(
                "UPDATE work_log_entries SET sync_status='synced' WHERE id=?",
                (entry_id,))
        conn.commit()
    finally:
        conn.close()


def _mark_entry_conflict(entry_id: str):
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE work_log_entries SET sync_status='conflict' WHERE id=?",
            (entry_id,)
        )
        conn.commit()
    finally:
        conn.close()


def _mark_image_uploaded(image_id: str):
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE work_log_images SET upload_status='uploaded' WHERE id=?",
            (image_id,)
        )
        conn.commit()
    finally:
        conn.close()


def push_pending() -> dict:
    """Push all pending entries and images to the server."""
    entries = _get_pending_entries()
    stats = {"pushed": 0, "conflicts": 0, "errors": 0}

    if entries:
        changes = []
        for e in entries:
            tags   = _get_tags_for_entry(e["id"])
            action = "delete" if e.get("deleted_at") else "upsert"
            changes.append({
                "entity":  "work_log",
                "id":      e["id"],
                "action":  action,
                "version": e.get("version", 1),
                "payload": {
                    "project_id":       e["project_id"],
                    "container_id":     e.get("container_id"),
                    "equipment_serial": e.get("equipment_serial", ""),
                    "site_location":    e.get("site_location", ""),
                    "category":         e.get("category", "maintenance"),
                    "description":      e.get("description", ""),
                    "fault_name":       e.get("fault_name", ""),
                    "status":           e.get("status", ""),
                    "sap_ticket":       e.get("sap_ticket", ""),
                    "spare_parts":      e.get("spare_parts", ""),
                    "log_date":         e["log_date"],
                    "created_at":       e.get("created_at", _now()),
                    "deleted_at":       e.get("deleted_at"),
                    "tags":             tags,
                },
            })

        try:
            resp = _request("post", "/sync/push", json={
                "device_id":       sync_config.device_id,
                "changes":         changes,
                "idempotency_key": str(uuid.uuid4()),
            })
            if resp.status_code == 200:
                sent = {c["id"]: c["action"] for c in changes}
                for result in resp.json()["results"]:
                    outcome = result["outcome"]
                    if outcome == "applied":
                        _mark_entry_synced(result["id"], result.get("server_version"))
                        stats["pushed"] += 1
                    elif outcome == "skipped" and sent.get(result["id"]) == "delete":
                        # nothing to delete on the server (never reached it)
                        _mark_entry_synced(result["id"])
                    elif outcome in ("conflict", "skipped"):
                        # "skipped" is how a server from before this fix
                        # answered an edit it could not place. Re-sending it
                        # every minute helped nobody; as a conflict the user
                        # sees it and settles it.
                        _mark_entry_conflict(result["id"])
                        stats["conflicts"] += 1
                    else:
                        stats["errors"] += 1
            else:
                stats["errors"] += len(entries)
        except RequestException as ex:
            stats["errors"] += len(entries)

    # Push pending images
    images = _get_pending_images()
    for img in images:
        file_path = img.get("file_path", "")
        if not file_path or not os.path.isfile(file_path):
            _mark_image_uploaded(img["id"])   # file gone, skip
            continue
        try:
            filename = img.get("filename") or os.path.basename(file_path)
            with open(file_path, "rb") as f:
                resp = _request(
                    "post",
                    f"/worklogs/{img['work_log_id']}/images",
                    extra_headers={"X-Image-ID": img["id"]},   # idempotency
                    files={"file": (filename, f, "image/jpeg")},
                )
            if resp.status_code in (200, 201):
                _mark_image_uploaded(img["id"])
                stats["pushed"] += 1
            elif resp.status_code == 404:
                # Parent entry not on server yet — will retry next cycle
                pass
        except RequestException:
            pass

    return stats


def push_projects():
    """
    Publish the local project list to the server so mobile clients
    can pick a project. Replace-all semantics; failures are non-fatal.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, name, project_type, num_blocks FROM projects ORDER BY id"
        ).fetchall()
        projects = [dict(r) for r in rows]
    finally:
        conn.close()
    try:
        _request("put", "/projects", json={"projects": projects})
    except RequestException:
        pass


def push_stock():
    """Publish per-project warehouse stock to the server so the phone can see
    what's on the shelf. Replace-all semantics; failures are non-fatal."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT w.project_id            AS project_id,
                   s.material_number       AS material_number,
                   COALESCE(m.description,'') AS description,
                   s.quantity              AS quantity,
                   s.unit                  AS unit,
                   s.min_quantity          AS min_quantity
            FROM stock_items s
            JOIN warehouses w ON s.warehouse_id = w.id
            LEFT JOIN materials m ON m.material_number = s.material_number
            WHERE w.project_id IS NOT NULL
        """).fetchall()
        items = [dict(r) for r in rows]
    finally:
        conn.close()
    try:
        _request("put", "/stock", json={"items": items})
    except RequestException:
        pass


# ── Inbox: pulled items that could not be applied yet ─────────────────────────
# The pull cursors move past every item the server sends. An item that failed
# to apply — a write-off for a project with no warehouse here yet, an event
# with a bad date — used to be dropped on the spot and never offered again,
# while the phone showed it as sent. Now it waits in sync_inbox and is retried
# at the start of every pull until it applies.

def _inbox_put(kind: str, item: dict, error) -> None:
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO sync_inbox (kind, item_id, payload, error)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(kind, item_id) DO UPDATE SET
                payload=excluded.payload, error=excluded.error,
                attempts=sync_inbox.attempts + 1, last_try=datetime('now')
        """, (kind, str(item["id"]), json.dumps(item, default=str), str(error)[:300]))
        conn.commit()
    finally:
        conn.close()


def _inbox_done(kind: str, item_id) -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM sync_inbox WHERE kind=? AND item_id=?", (kind, str(item_id)))
        conn.commit()
    finally:
        conn.close()


def _inbox_items(kind: str) -> list:
    conn = get_connection()
    try:
        return [json.loads(r["payload"]) for r in conn.execute(
            "SELECT payload FROM sync_inbox WHERE kind=? ORDER BY first_seen", (kind,))]
    finally:
        conn.close()


def inbox_waiting() -> list:
    """What is waiting to be applied, for display: [{kind, item_id, error, attempts}]."""
    conn = get_connection()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT kind, item_id, error, attempts, first_seen FROM sync_inbox "
            "ORDER BY first_seen")]
    finally:
        conn.close()


def _retry_inbox(kind: str, apply_fn, stats: dict) -> None:
    for item in _inbox_items(kind):
        try:
            if apply_fn(item):
                stats["applied"] += 1
            _inbox_done(kind, item["id"])
        except Exception as ex:
            _inbox_put(kind, item, ex)


def _apply_writeoff(wo: dict) -> bool:
    """Apply one phone write-off as an OUT stock transaction on the project's
    warehouse. False if it was already applied (dedup on reference 'MOB-<id>');
    raises if it cannot be applied yet."""
    from services.stock_service import get_project_warehouse_id, record_transaction
    ref = f"MOB-{wo['id']}"
    conn = get_connection()
    try:
        seen = conn.execute(
            "SELECT 1 FROM stock_transactions WHERE reference=? LIMIT 1", (ref,)).fetchone()
    finally:
        conn.close()
    if seen:
        return False
    wh_id = get_project_warehouse_id(wo["project_id"])
    if not wh_id:
        raise RuntimeError(f"project {wo['project_id']} has no warehouse on this desktop yet")
    note = "Mobile write-off"
    if wo.get("block"):
        note += f" · block {wo['block']}"
    if wo.get("note"):
        note += f" · {wo['note']}"
    record_transaction(
        warehouse_id=wh_id,
        material_number=wo["material_number"],
        transaction_type="OUT",
        quantity=float(wo.get("quantity") or 0),
        transaction_date=wo.get("log_date") or _now()[:10],
        project_id=wo["project_id"],
        reference=ref,
        notes=note,
    )
    return True


def pull_writeoffs() -> dict:
    """Pull mobile material write-offs and apply each as an OUT stock
    transaction on the matching project warehouse. Idempotent: a write-off is
    only applied once (dedup on reference 'MOB-<id>'). One that cannot be
    applied yet waits in the inbox instead of being dropped."""
    stats = {"applied": 0, "errors": 0}
    _retry_inbox("writeoff", _apply_writeoff, stats)
    cursor = str(sync_config.stock_cursor or "0")   # never None — see below

    while True:
        try:
            resp = _request("get", "/stock/writeoffs", params={"since": cursor})
            if resp.status_code != 200:
                stats["errors"] += 1
                break
            body = resp.json()
        except RequestException:
            stats["errors"] += 1
            break

        for wo in body.get("writeoffs", []):
            try:
                if _apply_writeoff(wo):
                    stats["applied"] += 1
            except Exception as ex:
                _inbox_put("writeoff", wo, ex)
                stats["errors"] += 1

        new_cursor = body.get("cursor")
        if new_cursor and new_cursor > cursor:
            cursor = new_cursor
            sync_config.stock_cursor = cursor
            sync_config.save()
        if not body.get("has_more", False):
            break

    return stats


def _field_event_seen(event_id: str) -> bool:
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT 1 FROM synced_field_events WHERE event_id=? LIMIT 1",
            (event_id,)).fetchone() is not None
    finally:
        conn.close()


def _mark_field_event(event_id: str, kind: str):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO synced_field_events (event_id, kind) VALUES (?, ?)",
            (event_id, kind))
        conn.commit()
    finally:
        conn.close()


def _apply_field_event(ev: dict) -> bool:
    """Route one phone event into its report table. False if it was already
    applied or is of a kind this desktop doesn't handle; raises if it cannot be
    applied."""
    from services.availability_service import (
        add_manual_unavailability, add_exclusion, EXCLUSION_TYPES, parse_block_spec)
    from services.report_workflow_service import add_pm_activity
    if _field_event_seen(ev["id"]):
        return False
    pid = ev["project_id"]
    df  = ev.get("date_from") or ""
    dt  = ev.get("date_to") or df
    hrs = float(ev.get("hours") or 0)
    desc = ev.get("description") or ""
    blocks_csv = ev.get("blocks") or ""
    year  = int(df[:4]) if len(df) >= 4 else None
    month = int(df[5:7]) if len(df) >= 7 else None
    kind = ev.get("kind")

    if kind == "pm":
        add_pm_activity(pid, year, month,
                        affected_blocks=blocks_csv, date_from=df,
                        date_to=dt, hours=hrs, description=desc)
    elif kind == "counts":
        for b in sorted(parse_block_spec(blocks_csv) or []) or [0]:
            add_manual_unavailability(
                block=b, date_from=df, date_to=dt, downtime_h=hrs,
                lc=None, cause=desc or "Field-reported",
                project_id=pid, year=year, month=month)
    elif kind == "excluded":
        et = ev.get("exclusion_type") or "Major Fault"
        if et not in EXCLUSION_TYPES:
            et = "Major Fault"
        total_min = min(int(round(hrs * 60)), 1439)
        time_to = f"{total_min // 60:02d}:{total_min % 60:02d}" if hrs > 0 else "23:59"
        add_exclusion(exclusion_type=et, date_from=df, date_to=dt,
                      time_from="00:00", time_to=time_to,
                      affected_blocks=blocks_csv, description=desc,
                      project_id=pid, year=year, month=month)
    else:
        return False

    _mark_field_event(ev["id"], kind or "")
    return True


def pull_field_events() -> dict:
    """Pull phone-captured PM / downtime / exclusion events and route each into
    the matching report table. Deduped via synced_field_events so re-pulling
    never double-counts. One that cannot be applied waits in the inbox and is
    retried every sync — PM hours from the phone feed the customer's
    availability, and used to vanish on a single failure."""
    stats = {"applied": 0, "errors": 0}
    _retry_inbox("field_event", _apply_field_event, stats)
    # Always a string: a missing/None cursor would blow up the comparison
    # below and take the whole sync with it.
    cursor = str(sync_config.field_cursor or "0")

    while True:
        try:
            resp = _request("get", "/events", params={"since": cursor})
            if resp.status_code != 200:
                stats["errors"] += 1
                break
            body = resp.json()
        except RequestException:
            stats["errors"] += 1
            break

        for ev in body.get("events", []):
            try:
                if _apply_field_event(ev):
                    stats["applied"] += 1
            except Exception as ex:
                _inbox_put("field_event", ev, ex)
                stats["errors"] += 1

        new_cursor = body.get("cursor")
        if new_cursor and new_cursor > cursor:
            cursor = new_cursor
            sync_config.field_cursor = cursor
            sync_config.save()
        if not body.get("has_more", False):
            break

    return stats


# ── Pull ──────────────────────────────────────────────────────────────────────

_ENTRY_COLS = ("project_id", "container_id", "equipment_serial", "site_location",
               "category", "description", "fault_name", "status", "sap_ticket",
               "spare_parts", "log_date", "created_at", "updated_at",
               "deleted_at", "version")


def _store_server_entry(conn, data: dict):
    """Write the server's copy of an entry over the local one — in place.

    Never INSERT OR REPLACE: REPLACE deletes the row first, and with foreign
    keys on that cascades to its spare-parts rows (the links to the stock
    transactions they were deducted by), its photos and its tags. A full
    re-pull — which every log-out/log-in triggers, by resetting the cursor —
    silently stripped all of them.
    """
    vals = (
        data.get("project_id") or None,          # None for phone entries
        data.get("container_id"),
        data.get("equipment_serial", ""),
        data.get("site_location", ""),
        data.get("category", "other"),
        data.get("description", ""),
        data.get("fault_name", ""),
        data.get("status", ""),
        data.get("sap_ticket", ""),
        data.get("spare_parts", ""),
        data.get("log_date", ""),
        data.get("created_at", _now()),
        data.get("updated_at", _now()),
        data.get("deleted_at"),
        data.get("version", 1),
    )
    cols = ", ".join(_ENTRY_COLS)
    marks = ", ".join("?" for _ in _ENTRY_COLS)
    sets = ", ".join(f"{c}=excluded.{c}" for c in _ENTRY_COLS)
    conn.execute(f"""
        INSERT INTO work_log_entries (id, {cols}, sync_status)
        VALUES (?, {marks}, 'synced')
        ON CONFLICT(id) DO UPDATE SET {sets}, sync_status='synced'
    """, (data["id"],) + vals)
    conn.execute("DELETE FROM work_log_tags WHERE work_log_id=?", (data["id"],))
    for tag in data.get("tags", []):
        conn.execute(
            "INSERT OR IGNORE INTO work_log_tags (work_log_id, tag) VALUES (?,?)",
            (data["id"], tag))


def _apply_pulled_entry(data: dict, action: str) -> str:
    """Apply one pulled entry. Returns 'applied', 'conflict' or 'kept'.

    An entry holding a local edit that has not been pushed yet is not
    overwritten: someone else changed it meanwhile, so it is marked 'conflict'
    and keeps the local text until the user chooses (see resolve_conflict).
    Before, the server copy simply replaced it and the edit was gone.
    """
    conn = get_connection()
    try:
        local = conn.execute(
            "SELECT sync_status, version FROM work_log_entries WHERE id=?", (data["id"],)
        ).fetchone()
        if local is not None and local["sync_status"] in ("local", "pending", "conflict"):
            # A copy no newer than the one this edit was made on (a full
            # re-pull after log-in resends everything) changes nothing: the
            # edit is simply still to be pushed.
            if (local["sync_status"] == "pending"
                    and int(data.get("version") or 0) <= int(local["version"] or 0)):
                return "kept"
            conn.execute("UPDATE work_log_entries SET sync_status='conflict' WHERE id=?",
                         (data["id"],))
            conn.commit()
            return "conflict"
        if action == "delete":
            conn.execute("""
                UPDATE work_log_entries
                SET deleted_at=?, updated_at=?, version=?, sync_status='synced'
                WHERE id=?
            """, (data.get("deleted_at") or _now(), _now(),
                  data.get("version", 1), data["id"]))
        else:
            _store_server_entry(conn, data)
        conn.commit()
        return "applied"
    finally:
        conn.close()


def get_server_entry(entry_id: str) -> Optional[dict]:
    """The server's current copy of one entry, or None when it has none.

    Raises RuntimeError when the server cannot be asked — including a server
    from before /sync/entry existed, whose 404 means "no such route", not "no
    such entry". Mistaking one for the other would delete a real entry here.
    """
    try:
        resp = _request("get", f"/sync/entry/{entry_id}")
    except RequestException as ex:
        raise RuntimeError(f"Server not reachable: {ex}")
    if resp.status_code == 404:
        try:
            detail = resp.json().get("detail", "")
        except ValueError:
            detail = ""
        if detail == "Entry not found":
            return None
    if resp.status_code != 200:
        raise RuntimeError(
            f"The server could not return this entry (HTTP {resp.status_code}). "
            "If it has not been updated to this version yet, redeploy it first.")
    return resp.json()


def resolve_conflict(entry_id: str, keep: str) -> str:
    """Settle a sync conflict on one entry.

    keep='mine'   — the local text wins: rebase it on the server's current
                    version and queue it; the next push overwrites the server.
    keep='server' — the server's copy wins and replaces the local text.

    Reads the server's copy first, so the choice is made against what is on
    the server now, not when the conflict arose. Returns a short message.
    Raises RuntimeError when the server cannot be reached.
    """
    if keep not in ("mine", "server"):
        raise ValueError("keep must be 'mine' or 'server'")
    server = get_server_entry(entry_id)
    conn = get_connection()
    try:
        if server is None:
            if keep == "mine":
                # Not on the server (never arrived, or removed): send it as new.
                conn.execute("UPDATE work_log_entries SET sync_status='pending', version=1 "
                             "WHERE id=?", (entry_id,))
                conn.commit()
                return "Not on the server — it will be sent as a new entry."
            conn.execute("UPDATE work_log_entries SET deleted_at=?, sync_status='synced' "
                         "WHERE id=?", (_now(), entry_id))
            conn.commit()
            return "Not on the server any more — removed here too."
        if keep == "server":
            _store_server_entry(conn, server)
            conn.commit()
            return "Server version kept."
        conn.execute("UPDATE work_log_entries SET version=?, sync_status='pending' WHERE id=?",
                     (int(server.get("version", 1)), entry_id))
        conn.commit()
        return "Your version will replace the server's on the next sync."
    finally:
        conn.close()


def _apply_pulled_image(data: dict):
    """Store image metadata from server (no binary download yet)."""
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id FROM work_log_images WHERE id=?", (data["id"],)
        ).fetchone()
        if existing:
            return   # already have it
        conn.execute("""
            INSERT OR IGNORE INTO work_log_images
                (id, work_log_id, file_path, thumbnail_path, filename,
                 size_bytes, sha256, taken_at, uploaded_at, upload_status)
            VALUES (?, ?, '', NULL, ?, ?, ?, ?, ?, 'remote')
        """, (
            data["id"],
            data["work_log_id"],
            data.get("filename", ""),
            data.get("size_bytes", 0),
            data.get("sha256", ""),
            data.get("taken_at"),
            data.get("uploaded_at", _now()),
        ))
        conn.commit()
    finally:
        conn.close()


def pull_delta() -> dict:
    """Pull all changes since last_cursor and apply to local DB."""
    stats = {"pulled": 0, "errors": 0, "conflicts": 0}
    cursor = sync_config.last_cursor

    while True:
        try:
            resp = _request("get", "/sync/pull", params={
                "since":     cursor,
                "device_id": sync_config.device_id,
            })
            if resp.status_code != 200:
                stats["errors"] += 1
                break

            body = resp.json()
            for change in body["changes"]:
                try:
                    if change["entity"] == "work_log":
                        got = _apply_pulled_entry(change["data"], change["action"])
                        if got == "conflict":
                            stats["conflicts"] += 1
                        elif got == "applied":
                            stats["pulled"] += 1
                    elif change["entity"] == "work_log_image":
                        _apply_pulled_image(change["data"])
                except Exception as exc:
                    stats["errors"] += 1
                    import logging
                    logging.getLogger("bess.sync").warning(
                        f"pull: failed to apply {change.get('entity')} "
                        f"id={change.get('id','?')[:8]}: {exc}"
                    )

            # Advance cursor
            if body["cursor"] > cursor:
                cursor = body["cursor"]
                sync_config.last_cursor = cursor
                sync_config.save()

            if not body.get("has_more", False):
                break

        except RequestException as ex:
            stats["errors"] += 1
            break

    return stats


# ── Full sync cycle ───────────────────────────────────────────────────────────

def download_image_file(image_id: str, dest_path: str) -> bool:
    """
    Download an image binary from the server to *dest_path*.
    Creates parent directories as needed.  Returns True on success.
    """
    if not sync_config.is_configured():
        return False
    try:
        url = f"{sync_config.server_url}/images/{image_id}/download"
        resp = requests.get(url, headers=_headers(), timeout=30)
        if resp.status_code == 401:
            if refresh_access_token():
                resp = requests.get(url, headers=_headers(), timeout=30)
        if resp.status_code == 200:
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            with open(dest_path, "wb") as fh:
                fh.write(resp.content)
            return True
    except RequestException:
        pass
    return False


def download_pending_remote_images() -> int:
    """
    Fetch the binaries for images pulled as metadata-only (upload_status
    ='remote') — e.g. photos a phone uploaded that this desktop has only heard
    about. Uses the on-demand downloader, which also writes the local file,
    generates a thumbnail and flips the row to 'uploaded'. Failures are left
    'remote' and retried on the next sync. Returns the count downloaded.
    """
    if not sync_config.is_configured():
        return 0
    from services.image_service import download_remote_image
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, work_log_id FROM work_log_images "
            "WHERE upload_status='remote'"
        ).fetchall()
    finally:
        conn.close()
    n = 0
    for r in rows:
        try:
            if download_remote_image(r["id"], r["work_log_id"]):
                n += 1
        except Exception:
            pass
    return n


def sync_now() -> SyncResult:
    """
    Full sync cycle: push pending → pull delta → download remote photos.
    Returns a SyncResult with counts.
    """
    if not sync_config.is_configured():
        return SyncResult(0, 0, 0, 0, 0)

    push_projects()
    # Write-offs first: the server lowers its stock mirror the moment a phone
    # writes off, and pushing the desktop's quantities before applying that
    # write-off here put the old figure back on the phones for a whole cycle.
    wo_stats = pull_writeoffs()
    push_stock()
    push_stats = push_pending()
    pull_stats  = pull_delta()
    ev_stats = pull_field_events()
    download_pending_remote_images()

    sync_config.last_sync_at = _now()
    sync_config.save()

    return SyncResult(
        pushed    = push_stats["pushed"],
        pulled    = pull_stats["pulled"],
        conflicts = push_stats.get("conflicts", 0) + pull_stats.get("conflicts", 0),
        # Phone events and write-offs count too — their failures used to be
        # dropped silently and the status bar read "up to date". Whatever is
        # still waiting in the inbox is reported until it applies.
        errors    = (push_stats.get("errors", 0) + pull_stats.get("errors", 0)
                     + max(len(inbox_waiting()),
                           wo_stats.get("errors", 0) + ev_stats.get("errors", 0))),
        skipped   = 0,
    )
