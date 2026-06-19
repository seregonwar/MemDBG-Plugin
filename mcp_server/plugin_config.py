#!/usr/bin/env python3
# MemDBG MCP Server - Plugin configuration persistence.
# SPDX-License-Identifier: GPL-3.0-or-later
#
# PluginConfig lets GUI plugins save/load settings as JSON files that
# survive across sessions.  The config directory is provided by the
# C++ frontend in the context JSON's ``paths.config`` field.
#
# Usage inside a plugin::
#
#     from mcp_server.config import PluginConfig
#     cfg = PluginConfig("my-plugin")
#     cfg.load()
#     last_address = cfg.get("last_address", "0x1000")
#     cfg.set("last_address", "0x2000")
#     cfg.save()
#
# The file is stored as ``<config_dir>/<plugin_name>.json``.

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional


class PluginConfig:
    """Key-value settings store backed by a JSON file on disk.

    The config file path is ``config_dir / f\"{name}.json\"`` where
    *config_dir* is read from ``paths.config`` in the MemDBG context
    (or overridden explicitly).
    """

    def __init__(self, name: str, config_dir: Optional[str] = None) -> None:
        """Create a config store for the named plugin.

        Args:
            name: Short identifier for the config file (e.g. \"my-plugin\").
            config_dir: Explicit config directory.  If ``None``, read from
                        ``paths.config`` in the MemDBG context JSON.
        """
        self._name = name
        self._data: Dict[str, Any] = {}

        if config_dir is not None:
            self._dir = Path(config_dir)
        else:
            self._dir = self._resolve_config_dir()

        self._path = self._dir / f"{self._name}.json"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> bool:
        """Load settings from the JSON file.

        Returns ``True`` if the file was loaded successfully, ``False`` if
        the file doesn't exist or couldn't be parsed (data is reset to empty
        in that case).
        """
        if not self._path.exists():
            self._data.clear()
            return False

        try:
            with self._path.open("r", encoding="utf-8") as fh:
                self._data = json.load(fh)
            if not isinstance(self._data, dict):
                self._data = {}
            return True
        except (ValueError, OSError):
            self._data.clear()
            return False

    def save(self) -> bool:
        """Persist current settings to the JSON file.

        Creates the parent directory if it doesn't exist.

        Returns ``True`` on success, ``False`` on I/O error.
        """
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            with self._path.open("w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2, sort_keys=True)
                fh.write("\n")
            return True
        except (OSError, TypeError):
            return False

    def get(self, key: str, default: Any = None) -> Any:
        """Return the value for *key*, or *default* if not present."""
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Store *value* under *key*."""
        self._data[key] = value

    def remove(self, key: str) -> None:
        """Remove *key* from the store (no-op if absent)."""
        self._data.pop(key, None)

    def clear(self) -> None:
        """Remove all keys from the in-memory store (does not touch disk)."""
        self._data.clear()

    def all(self) -> Dict[str, Any]:
        """Return a shallow copy of all settings."""
        return dict(self._data)

    @property
    def path(self) -> Path:
        """Filesystem path to the JSON config file."""
        return self._path

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_config_dir() -> Path:
        """Determine the config directory from the MemDBG context.

        Looks at ``paths.config`` in the context JSON, then falls back
        to a directory next to the plugin script.
        """
        # Try the context file
        from memdbg import load_context
        try:
            ctx = load_context()
            cfg = ctx.get("paths", {}).get("config")
            if cfg:
                return Path(cfg)
        except Exception:
            pass

        # Fallback: config dir next to this script
        return Path(__file__).resolve().parent.parent / "config"
