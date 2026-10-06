"""Canonical spelling of artist names across the whole library.

The same artist is written differently from upload to upload on SoundCloud
("BENJAMINGOTBENZ", "benjamingotbenz", "✦ platov ✦"), and a player groups
artists by the exact tag value, so one person ends up as several artists.
The registry remembers one display form per name (compared case- and
punctuation-insensitively) and every tag is written with that form.

Sources rank by reliability: Spotify's own artist list first, then a
SoundCloud account name, then a name parsed out of a title. A better source
replaces a worse spelling; `--recheck` then rewrites older tags.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from .text_utils import normalize_name, strip_decorations

PRIORITY_TITLE = 1
PRIORITY_UPLOADER = 2
PRIORITY_SPOTIFY = 3


class ArtistRegistry:
    def __init__(self, path: Path | None):
        self.path = path
        self._lock = threading.Lock()
        self._dirty = False
        self._data: dict[str, dict] = {}
        if path is not None:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._data = {
                        key: value for key, value in raw.items()
                        if isinstance(value, dict) and isinstance(value.get("name"), str)
                    }
            except (OSError, ValueError):
                self._data = {}

    def register(self, name: str | None, priority: int) -> str:
        """Records a spelling and returns the canonical one."""
        display = strip_decorations(name)
        key = normalize_name(display)
        if not key:
            return display
        with self._lock:
            entry = self._data.get(key)
            if entry is None or priority > int(entry.get("priority", 0)):
                self._data[key] = {"name": display, "priority": priority}
                self._dirty = True
                return display
            return entry["name"]

    def canonical(self, name: str | None) -> str:
        display = strip_decorations(name)
        key = normalize_name(display)
        with self._lock:
            entry = self._data.get(key)
        return entry["name"] if entry else display

    def is_known(self, name: str | None) -> bool:
        key = normalize_name(strip_decorations(name))
        if not key:
            return False
        with self._lock:
            return key in self._data

    def flush(self) -> None:
        if self.path is None:
            return
        with self._lock:
            if not self._dirty:
                return
            payload = json.dumps(self._data, ensure_ascii=False, indent=1, sort_keys=True)
            self._dirty = False
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


_REGISTRIES: dict[Path, ArtistRegistry] = {}
_REGISTRIES_LOCK = threading.Lock()


def get_registry(path: Path) -> ArtistRegistry:
    """One shared registry per file for the whole run."""
    key = path.resolve()
    with _REGISTRIES_LOCK:
        registry = _REGISTRIES.get(key)
        if registry is None:
            registry = ArtistRegistry(key)
            _REGISTRIES[key] = registry
        return registry
