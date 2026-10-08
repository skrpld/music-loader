"""Machine-readable progress for the server mode.

`EventDashboard` has the same public methods as the terminal `Dashboard`
(see ui.py), so the Spotify and SoundCloud pipelines run unchanged. Instead
of drawing a live view it writes every update as one JSON object per line;
the server (server.py) reads these lines from the worker process and builds
the job state the Android app shows.

Events:

    {"type": "runlog", "path": "..."}
    {"type": "unavailable_log", "path": "..."}      (when the first unavailable track is listed)
    {"type": "queue", "completed": 1, "total": 3}
    {"type": "stats", "stats": {...Stats fields...}}
    {"type": "log", "level": "info" | "error", "source": "Spotify" | null, "text": "..."}
    {"type": "file", "slot": 0, "visible": true, "label": "...", "percent": 42.0,
     "speed": "...", "eta": "..."}
    {"type": "finished", "status": "completed" | "cancelled" | "failed", "message": "..."}

Private SoundCloud tokens are masked in every text that leaves the process.
"""
from __future__ import annotations

import json
import threading
from dataclasses import asdict
from typing import Any, Optional, TextIO

from .availability import UnavailableTrack
from .runlog import RunLog, redact_secrets
from .ui import Stats


class EventDashboard:
    def __init__(self, stream: TextIO, runlog: Optional[RunLog] = None):
        self.stream = stream
        self.runlog = runlog
        self.stats = Stats()
        self._files: dict[int, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._unavailable_announced = False

    # -- output ---------------------------------------------------------------
    def emit(self, event: dict[str, Any]) -> None:
        line = json.dumps(event, ensure_ascii=False)
        with self._lock:
            try:
                self.stream.write(line + "\n")
                self.stream.flush()
            except (OSError, ValueError):
                # The server went away; the run itself must not crash on it.
                pass

    # -- Dashboard interface ----------------------------------------------------
    def log(self, message: str) -> None:
        self.emit({"type": "log", "level": "info", "source": None,
                   "text": redact_secrets(message)})

    def log_error(self, source: str, message: str) -> None:
        self.emit({"type": "log", "level": "error", "source": source,
                   "text": redact_secrets(message)})
        if self.runlog is not None:
            self.runlog.record(source, message)

    def set_queue(self, completed: int, total: int) -> None:
        self.emit({"type": "queue", "completed": completed, "total": max(total, 1)})

    def add_tracks_total(self, kind: str, count: int) -> None:
        if count <= 0:
            return
        self._bump(f"{kind}_tracks_total", count)

    def record_track(self, kind: str, status: str, amount: int = 1) -> None:
        if amount:
            self._bump(f"{kind}_tracks_{status}", amount)

    def record_unavailable(self, kind: str, track: UnavailableTrack, note: str = "") -> None:
        self.record_track(kind, "unavailable")
        self.log(f"[{track.source}] {track.message(note)}")
        if self.runlog is not None:
            self.runlog.record_unavailable(track)
            with self._lock:
                announce = not self._unavailable_announced
                self._unavailable_announced = True
            if announce:
                self.emit({"type": "unavailable_log", "path": str(self.runlog.unavailable_path)})

    def record_lyrics(self, found: bool) -> None:
        self._bump("lyrics_ok" if found else "lyrics_fail", 1)

    def record_lyrics_skipped(self) -> None:
        self._bump("lyrics_skipped", 1)

    def record(self, kind: str, ok: bool) -> None:
        self._bump(f"{kind}_{'ok' if ok else 'fail'}", 1)

    def _bump(self, attr: str, amount: int) -> None:
        with self._lock:
            if not hasattr(self.stats, attr):
                # An unknown counter name must never crash a worker thread.
                self.log(f"[UI][!] Unknown counter '{attr}'")
                return
            setattr(self.stats, attr, max(0, getattr(self.stats, attr) + amount))
            self.emit({"type": "stats", "stats": asdict(self.stats)})

    def start_file(self, label: str, slot: int = 0) -> None:
        with self._lock:
            row = {"label": redact_secrets(label), "percent": 0.0, "speed": "", "eta": ""}
            self._files[slot] = row
            self._emit_file(slot, row)

    def update_file(self, percent: float | None = None, label: str | None = None,
                    speed: str | None = None, eta: str | None = None, slot: int = 0) -> None:
        with self._lock:
            row = self._files.setdefault(slot, {"label": "", "percent": 0.0, "speed": "", "eta": ""})
            if label is not None:
                row["label"] = redact_secrets(label)
            if percent is not None:
                row["percent"] = float(percent)
            if speed is not None:
                row["speed"] = speed
            if eta is not None:
                row["eta"] = eta
            self._emit_file(slot, row)

    def finish_file(self, slot: int | None = 0) -> None:
        with self._lock:
            slots = list(self._files) if slot is None else [slot]
            for item in slots:
                if self._files.pop(item, None) is not None:
                    self.emit({"type": "file", "slot": item, "visible": False})

    def _emit_file(self, slot: int, row: dict[str, Any]) -> None:
        self.emit({"type": "file", "slot": slot, "visible": True, **row})
