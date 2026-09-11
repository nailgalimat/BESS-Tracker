"""
services/sync_config.py
------------------------
Stores sync server settings and tokens in <app_dir>/sync_config.json.
Nothing sensitive is sent over the wire in plaintext — tokens are JWTs.
"""

import json
import os
import sys
import uuid
from typing import Optional


def _config_path() -> str:
    # Mirrors BESS_DB in db_manager. Tests point it at a config with sync off:
    # the real one is enabled and holds live tokens, so a test that opens the
    # main window would otherwise start a sync worker against the server.
    override = os.getenv("BESS_SYNC_CONFIG")
    if override:
        return override
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "sync_config.json")


_DEFAULTS = {
    "server_url":     "",
    "username":       "",
    "access_token":   "",
    "refresh_token":  "",
    "device_id":      f"desktop-{uuid.uuid4()}",
    "last_cursor":    "0",
    "last_sync_at":   "",
    "enabled":        False,
}


class SyncConfig:
    """Simple JSON-backed config for sync credentials."""

    def __init__(self):
        self._data: dict = dict(_DEFAULTS)
        self._load()

    def _load(self):
        path = _config_path()
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                self._data.update(saved)
            except Exception:
                pass

    def save(self):
        path = _config_path()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except Exception as e:
            print(f"[sync_config] Could not save: {e}")

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def server_url(self) -> str:
        return self._data.get("server_url", "").rstrip("/")

    @server_url.setter
    def server_url(self, v: str):
        self._data["server_url"] = v.rstrip("/")

    @property
    def username(self) -> str:
        return self._data.get("username", "")

    @username.setter
    def username(self, v: str):
        self._data["username"] = v

    @property
    def access_token(self) -> str:
        return self._data.get("access_token", "")

    @access_token.setter
    def access_token(self, v: str):
        self._data["access_token"] = v

    @property
    def refresh_token(self) -> str:
        return self._data.get("refresh_token", "")

    @refresh_token.setter
    def refresh_token(self, v: str):
        self._data["refresh_token"] = v

    @property
    def device_id(self) -> str:
        return self._data.get("device_id", f"desktop-{uuid.uuid4()}")

    @property
    def last_cursor(self) -> str:
        return self._data.get("last_cursor", "0")

    @last_cursor.setter
    def last_cursor(self, v: str):
        self._data["last_cursor"] = v

    @property
    def stock_cursor(self) -> str:
        return self._data.get("stock_cursor", "0")

    @stock_cursor.setter
    def stock_cursor(self, v: str):
        self._data["stock_cursor"] = v

    @property
    def field_cursor(self) -> str:
        return self._data.get("field_cursor", "0")

    @field_cursor.setter
    def field_cursor(self, v: str):
        self._data["field_cursor"] = v

    @property
    def last_sync_at(self) -> str:
        return self._data.get("last_sync_at", "")

    @last_sync_at.setter
    def last_sync_at(self, v: str):
        self._data["last_sync_at"] = v

    @property
    def enabled(self) -> bool:
        return bool(self._data.get("enabled", False))

    @enabled.setter
    def enabled(self, v: bool):
        self._data["enabled"] = v

    def is_configured(self) -> bool:
        return bool(self.server_url and self.access_token)

    def clear_tokens(self):
        self._data["access_token"]  = ""
        self._data["refresh_token"] = ""
        self._data["last_cursor"]   = "0"


# Singleton
sync_config = SyncConfig()
