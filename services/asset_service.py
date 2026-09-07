"""
services/asset_service.py
--------------------------
Asset details per container: Manufacturer, Model, Firmware.
"""

from database.db_manager import get_connection
from typing import Optional


def get_asset(container_id: int) -> dict:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM asset_details WHERE container_id=?",
            (container_id,)
        ).fetchone()
        if row:
            return dict(row)
        return {"container_id": container_id, "manufacturer": "",
                "model": "", "firmware_version": ""}
    finally:
        conn.close()


def save_asset(container_id: int, manufacturer: str = "",
               model: str = "", firmware_version: str = ""):
    conn = get_connection()
    try:
        exists = conn.execute(
            "SELECT id FROM asset_details WHERE container_id=?",
            (container_id,)
        ).fetchone()
        if exists:
            conn.execute("""
                UPDATE asset_details
                SET manufacturer=?, model=?, firmware_version=?,
                    updated_at=datetime('now')
                WHERE container_id=?
            """, (manufacturer, model, firmware_version, container_id))
        else:
            conn.execute("""
                INSERT INTO asset_details
                    (container_id, manufacturer, model, firmware_version)
                VALUES (?, ?, ?, ?)
            """, (container_id, manufacturer, model, firmware_version))
        conn.commit()
    finally:
        conn.close()


def get_assets_for_project(project_id: int) -> list:
    """Returns all containers with their asset details for a project."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT
                c.id AS container_id,
                c.zone_number, c.block_number, c.container_index,
                c.container_type, c.serial_number,
                COALESCE(a.manufacturer,     '') AS manufacturer,
                COALESCE(a.model,            '') AS model,
                COALESCE(a.firmware_version, '') AS firmware_version
            FROM containers c
            LEFT JOIN asset_details a ON a.container_id = c.id
            WHERE c.project_id = ?
            ORDER BY c.zone_number, c.block_number, c.container_index
        """, (project_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def bulk_save_assets(assets: list):
    """
    assets: list of dicts with keys:
      container_id, manufacturer, model, firmware_version
    """
    conn = get_connection()
    try:
        for a in assets:
            exists = conn.execute(
                "SELECT id FROM asset_details WHERE container_id=?",
                (a["container_id"],)
            ).fetchone()
            if exists:
                conn.execute("""
                    UPDATE asset_details
                    SET manufacturer=?, model=?, firmware_version=?,
                        updated_at=datetime('now')
                    WHERE container_id=?
                """, (a.get("manufacturer",""), a.get("model",""),
                      a.get("firmware_version",""), a["container_id"]))
            else:
                conn.execute("""
                    INSERT INTO asset_details
                        (container_id, manufacturer, model, firmware_version)
                    VALUES (?, ?, ?, ?)
                """, (a["container_id"], a.get("manufacturer",""),
                      a.get("model",""), a.get("firmware_version","")))
        conn.commit()
    finally:
        conn.close()
