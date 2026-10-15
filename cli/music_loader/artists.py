"""Canonical spelling of artist names across the whole library.

The same artist is written differently from upload to upload on SoundCloud
("BENJAMINGOTBENZ", "benjamingotbenz", "✦ platov ✦"), and a player groups
artists by the exact tag value, so one person ends up as several artists.
The registry remembers one display form per name (compared case- and
punctuation-insensitively) and every tag is written with that form.

Sources rank by reliability: Spotify's own artist list first, then a
SoundCloud account name, then a name parsed out of a title. A better source
replaces a worse spelling; `--recheck` then rewrites older tags.

The same file keeps the decision per SoundCloud account (accounts.py) under
the reserved key `ACCOUNTS_KEY`, which no normalized name can be. The entry
carries an empty "name", so older versions read it as a spelling nobody
looks up and keep it when they rewrite the file.

Two processes may write the file at the same time (a server job, and the
Artists screen changing an account through the server): `flush` merges with
what is on disk instead of overwriting it. A spelling keeps the better
source; an account keeps the newer change.
"""
from __future__ import annotations

import copy
import json
import threading
import time
from pathlib import Path
from typing import Any

from .text_utils import normalize_name, strip_decorations

PRIORITY_TITLE = 1
PRIORITY_UPLOADER = 2
PRIORITY_SPOTIFY = 3

ACCOUNTS_KEY = "_accounts"


class ArtistRegistry:
    def __init__(self, path: Path | None):
        self.path = path
        self._lock = threading.Lock()
        self._dirty = False
        self._data: dict[str, dict] = {}
        self._accounts: dict[str, dict[str, Any]] = {}
        # What this process changed, so a flush only overwrites those entries.
        self._changed_names: set[str] = set()
        self._changed_accounts: set[str] = set()
        if path is not None:
            self._data, self._accounts = self._read()

    def _read(self) -> tuple[dict[str, dict], dict[str, dict[str, Any]]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, AttributeError):
            return {}, {}
        if not isinstance(raw, dict):
            return {}, {}
        names = {
            key: value for key, value in raw.items()
            if key != ACCOUNTS_KEY and isinstance(value, dict) and isinstance(value.get("name"), str)
        }
        holder = raw.get(ACCOUNTS_KEY)
        accounts = holder.get("accounts") if isinstance(holder, dict) else None
        accounts = {
            str(key): value for key, value in (accounts or {}).items()
            if isinstance(value, dict)
        } if isinstance(accounts, dict) else {}
        return names, accounts

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
                self._changed_names.add(key)
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

    def priority(self, name: str | None) -> int:
        key = normalize_name(strip_decorations(name))
        with self._lock:
            entry = self._data.get(key)
        return int(entry.get("priority", 0)) if entry else 0

    # -- accounts (accounts.py) -------------------------------------------------------
    def account(self, account_id: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._accounts.get(account_id)
            return copy.deepcopy(entry) if entry is not None else None

    def accounts(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._accounts)

    def put_account(self, account_id: str, data: dict[str, Any]) -> None:
        entry = copy.deepcopy(data)
        entry["updated_at"] = time.time()
        with self._lock:
            self._accounts[account_id] = entry
            self._changed_accounts.add(account_id)
            self._dirty = True

    def reload(self) -> None:
        """Takes over what other processes wrote, keeping this one's changes."""
        if self.path is None:
            return
        names, accounts = self._read()
        with self._lock:
            self._merge_locked(names, accounts)

    def _merge_locked(self, names: dict[str, dict], accounts: dict[str, dict[str, Any]]) -> None:
        for key, entry in names.items():
            mine = self._data.get(key)
            if mine is None or key not in self._changed_names \
                    or int(entry.get("priority", 0)) > int(mine.get("priority", 0)):
                self._data[key] = entry
        for key, entry in accounts.items():
            mine = self._accounts.get(key)
            if mine is None or key not in self._changed_accounts \
                    or float(entry.get("updated_at") or 0) > float(mine.get("updated_at") or 0):
                self._accounts[key] = entry

    def flush(self) -> None:
        if self.path is None:
            return
        names, accounts = self._read()
        with self._lock:
            if not self._dirty:
                return
            self._merge_locked(names, accounts)
            payload_data: dict[str, Any] = dict(self._data)
            if self._accounts:
                payload_data[ACCOUNTS_KEY] = {"name": "", "priority": 0, "accounts": self._accounts}
            payload = json.dumps(payload_data, ensure_ascii=False, indent=1, sort_keys=True)
            self._dirty = False
            self._changed_names.clear()
            self._changed_accounts.clear()
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
