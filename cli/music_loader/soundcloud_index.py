"""Persistent SoundCloud id -> local file mapping (`.sc_index.json`) and the
yt-dlp style archive file.

Paths are stored relative to the SoundCloud folder, so the library can be
moved or copied without every track being downloaded again.

A file the index does not know is recognized by its tags: files written by
this version carry the SoundCloud id (TXXX:SOUNDCLOUD_ID); files written by
older versions are matched by the uploader/title/duration they were tagged
with. That scan reads every file, so it runs lazily - only when an id lookup
misses - once per run, and outside the main lock.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any

from .config import AUDIO_EXTENSIONS, INDEX_FILENAME, INDEX_SAVE_INTERVAL_SECONDS
from .tags import MUTAGEN_AVAILABLE, audio_duration, read_tags



def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


class SoundCloudIndex:
    def __init__(self, soundcloud_dir: Path):
        self.dir = soundcloud_dir
        self.path = soundcloud_dir / INDEX_FILENAME
        self._lock = threading.RLock()
        self._legacy_lock = threading.RLock()
        self._data: dict[str, dict[str, Any]] = self._load()
        self._legacy_ids: dict[str, Path] | None = None
        self._legacy_prints: list[tuple[Path, str, str, float | None]] = []
        self._dirty = False
        self._last_save = 0.0

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return {str(k): v for k, v in raw.items() if isinstance(v, dict)}

    # -- paths ----------------------------------------------------------------
    def _resolve(self, stored: str) -> Path:
        path = Path(stored)
        return path if path.is_absolute() else self.dir / path

    def _store(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.dir.resolve()).as_posix()
        except (OSError, ValueError):
            return str(path)

    def known_paths(self) -> set[Path]:
        with self._lock:
            return {self._resolve(str(entry.get("path", ""))) for entry in self._data.values()}

    # -- legacy scan ------------------------------------------------------------
    def _scan_legacy(self) -> None:
        ids: dict[str, Path] = {}
        prints: list[tuple[Path, str, str, float | None]] = []
        if not MUTAGEN_AVAILABLE:
            self._legacy_ids, self._legacy_prints = ids, prints
            return
        known = {str(path) for path in self.known_paths()}
        try:
            candidates = list(self.dir.rglob("*"))
        except OSError:
            candidates = []
        for path in candidates:
            try:
                relative_parts = path.relative_to(self.dir).parts
            except ValueError:
                continue
            # Hidden folders hold partial downloads, not library files.
            if any(part.startswith(".") for part in relative_parts):
                continue
            try:
                if not path.is_file() or path.suffix.lower() not in AUDIO_EXTENSIONS:
                    continue
            except OSError:
                continue
            if str(path) in known:
                continue
            tags = read_tags(path)
            if tags["soundcloud_id"]:
                ids.setdefault(tags["soundcloud_id"], path)
                continue
            artists = tags["artists"]
            if tags["title"] and artists:
                prints.append((path, _norm(tags["title"]), _norm("/".join(artists)), audio_duration(path)))
        self._legacy_ids, self._legacy_prints = ids, prints

    def _ensure_legacy(self) -> None:
        with self._legacy_lock:
            if self._legacy_ids is None:
                self._scan_legacy()

    # -- lookups ----------------------------------------------------------------
    def get(self, track_id: str) -> Path | None:
        """Exact id lookup in the index only."""
        if not track_id:
            return None
        with self._lock:
            entry = self._data.get(track_id)
            if not entry:
                return None
            candidate = self._resolve(str(entry.get("path", "")))
            if candidate.exists():
                return candidate
            self._data.pop(track_id, None)
            self._dirty = True
            return None

    def find(self, info: dict[str, Any]) -> Path | None:
        track_id = str(info.get("id") or "")
        found = self.get(track_id)
        if found is not None:
            return found

        self._ensure_legacy()
        with self._legacy_lock:
            by_id = self._legacy_ids.get(track_id) if track_id and self._legacy_ids else None
            prints = list(self._legacy_prints)
        if by_id is not None and by_id.exists():
            return by_id

        # Files from versions that tagged artist = uploader and the raw
        # title. A flat listing has neither, so this only runs with full info.
        title = _norm(info.get("title"))
        artist = _norm(info.get("uploader"))
        if not title or not artist:
            return None
        duration = info.get("duration")
        for candidate, old_title, old_artist, old_duration in prints:
            if old_title != title or old_artist != artist:
                continue
            if duration is not None and old_duration is not None:
                try:
                    if abs(float(duration) - float(old_duration)) > 2.0:
                        continue
                except (TypeError, ValueError):
                    pass
            if candidate.exists():
                return candidate
        return None

    def has_unindexed_legacy_files(self) -> bool:
        """True when files of older versions exist that can only be matched
        with full metadata (title + uploader + duration)."""
        self._ensure_legacy()
        with self._legacy_lock:
            return bool(self._legacy_prints)

    def add(self, info: dict[str, Any], path: Path) -> None:
        track_id = str(info.get("id") or "")
        if not track_id:
            return
        with self._lock:
            self._data[track_id] = {
                "path": self._store(path),
                "title": info.get("title"),
                "artist": info.get("uploader"),
                "duration": info.get("duration"),
                "webpage_url": info.get("webpage_url"),
            }
            self._dirty = True
            self._maybe_save_locked()
        with self._legacy_lock:
            if self._legacy_ids is not None:
                self._legacy_ids.pop(track_id, None)
                self._legacy_prints = [item for item in self._legacy_prints if item[0] != path]

    def webpage_url(self, track_id: str) -> str:
        with self._lock:
            entry = self._data.get(track_id) or {}
        return str(entry.get("webpage_url") or "")

    def remove(self, track_id: str) -> None:
        with self._lock:
            if self._data.pop(track_id, None) is not None:
                self._dirty = True

    # -- persistence ----------------------------------------------------------------
    def _maybe_save_locked(self) -> None:
        if time.monotonic() - self._last_save < INDEX_SAVE_INTERVAL_SECONDS:
            return
        self._save_locked()

    def flush(self) -> None:
        with self._lock:
            if self._dirty:
                self._save_locked()

    def _save_locked(self) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
            self._dirty = False
            self._last_save = time.monotonic()
        except OSError:
            # A failing index write must never abort a finished download.
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


_INDEX_CACHE: dict[Path, SoundCloudIndex] = {}
_INDEX_CACHE_LOCK = threading.Lock()


def get_index(soundcloud_dir: Path) -> SoundCloudIndex:
    """One shared index per directory, so the expensive library scan happens
    at most once even when many links are processed in a row."""
    key = soundcloud_dir.resolve()
    with _INDEX_CACHE_LOCK:
        index = _INDEX_CACHE.get(key)
        if index is None:
            index = SoundCloudIndex(key)
            _INDEX_CACHE[key] = index
        return index


class SoundCloudArchive:
    """Keeps yt-dlp-compatible archive entries, committed only after success."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        try:
            self._lines = set(path.read_text(encoding="utf-8").splitlines())
        except OSError:
            self._lines = set()

    def add(self, track_id: str) -> None:
        if not track_id:
            return
        line = f"soundcloud {track_id}"
        with self._lock:
            if line in self._lines:
                return
            try:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                return
            self._lines.add(line)
