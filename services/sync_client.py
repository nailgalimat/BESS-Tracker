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
    url = server_url.rstrip("/")
    resp = requests.post(
        f"{url}/auth/login",
        json={"username": username, "password": password,
              "device_id": sync_config.device_id},
        timeout=TIMEOUT,
    )
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
    resp = getattr(requests, method)(url, headers=_hdrs(), timeout=TIMEOUT, **kwargs)
    if resp.status_code == 401:
        if refresh_access_token():
            resp = getattr(requests, method)(url, headers=_hdrs(), timeout=TIMEOUT, **kwargs)
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


def _mark_entry_synced(entry_id: str):
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE work_log_entries SET sync_status='synced' WHERE id=?",
            (entry_id,)
        )
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
                for result in resp.json()["results"]:
                    if result["outcome"] == "applied":
                        _mark_entry_synced(result["id"])
                        stats["pushed"] += 1
                    elif result["outcome"] == "conflict":
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
            "SELECT id, name, project_type FROM projects ORDER BY id"
        ).fetchall()
        projects = [dict(r) for r in rows]
    finally:
        conn.close()
    try:
        _request("put", "/projects", json={"projects": projects})
    except RequestException:
        pass


# ── Pull ──────────────────────────────────────────────────────────────────────

def _apply_pulled_entry(data: dict, action: str):
    """INSERT OR REPLACE a server entry into local SQLite."""
    conn = get_connection()
    try:
        if action == "delete":
            conn.execute("""
                UPDATE work_log_entries
                SET deleted_at=?, updated_at=?, sync_status='synced'
                WHERE id=?
            """, (data.get("deleted_at") or _now(), _now(), data["id"]))
        else:
            # Check if project_id exists locally — if not, still store (foreign key is ON)
            conn.execute("""
                INSERT OR REPLACE INTO work_log_entries
                    (id, project_id, container_id, equipment_serial, site_location,
                     category, description, fault_name, status, sap_ticket,
                     spare_parts, log_date, created_at, updated_at,
                     deleted_at, version, sync_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'synced')
            """, (
                data["id"],
                data.get("project_id") or None,   # None for mobile entries without project
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
            ))
            # Tags
            conn.execute("DELETE FROM work_log_tags WHERE work_log_id=?", (data["id"],))
            for tag in data.get("tags", []):
                conn.execute(
                    "INSERT OR IGNORE INTO work_log_tags (work_log_id, tag) VALUES (?,?)",
                    (data["id"], tag)
                )
        conn.commit()
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
    stats = {"pulled": 0, "errors": 0}
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
                        _apply_pulled_entry(change["data"], change["action"])
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
    push_stats = push_pending()
    pull_stats  = pull_delta()
    download_pending_remote_images()

    sync_config.last_sync_at = _now()
    sync_config.save()

    return SyncResult(
        pushed    = push_stats["pushed"],
        pulled    = pull_stats["pulled"],
        conflicts = push_stats.get("conflicts", 0),
        errors    = push_stats.get("errors", 0) + pull_stats.get("errors", 0),
        skipped   = 0,
    )
