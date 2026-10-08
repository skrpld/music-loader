"""Persistent log of failed operations for the current run.

The live dashboard only keeps the last few "Activity" lines on screen, so a
failure that happened at link #3 out of 40 is gone from view by the time the
run finishes. This module writes every failure to a plain-text file as it
happens, so nothing is lost — the file can be checked (or grepped) after a
long batch run to see exactly what needs retrying.
"""
import re
import threading
from datetime import datetime
from pathlib import Path

from .availability import UnavailableTrack

# Private SoundCloud links carry a secret token ("/s-AbC123",
# "secret_token=..."); the log file must not keep it.
_SECRET_RE = re.compile(r"(/s-|secret_token=)[A-Za-z0-9]+")


def redact_secrets(text: str) -> str:
    """Masks private SoundCloud tokens anywhere in a free-form message."""
    return _SECRET_RE.sub(r"\1***", str(text))


class RunLog:
    """Append-only failure log for a single run of music-loader.

    Tracks that are skipped because they cannot be downloaded at all (DRM,
    preview only, blocked) are not failures; they go to their own file,
    `unavailable-<timestamp>.log`, next to the failure log, created when the
    first one is recorded.
    """

    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path = directory / f"failures-{timestamp}.log"
        self.unavailable_path = directory / f"unavailable-{timestamp}.log"
        self.count = 0
        self.unavailable_count = 0
        self._lock = threading.Lock()

    def record_unavailable(self, track: UnavailableTrack) -> None:
        """One line per track: `[time] [source] reason | artist - title | url`,
        so the list can be grepped or fed to another tool."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        name = f"{track.artist} - {track.title}" if track.artist else track.title
        line = redact_secrets(
            f"[{timestamp}] [{track.source}] {track.reason.value} | {name} | {track.url}"
        ).replace("\n", " ") + "\n"
        with self._lock:
            self.unavailable_count += 1
            try:
                with open(self.unavailable_path, "a", encoding="utf-8") as f:
                    f.write(line)
            except OSError:
                pass

    def record(self, source: str, message: str) -> None:
        """Appends one failure line. `source` is a short tag such as
        'Spotify', 'SoundCloud', or 'Lyrics'."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = redact_secrets(message)
        line = f"[{timestamp}] [{source}] {message}\n"
        # Called from several worker threads at once, so the write is
        # serialized. Opened/closed per call (not kept open) so the file is
        # always flushed to disk and readable mid-run, and so a crash doesn't
        # lose buffered lines.
        with self._lock:
            self.count += 1
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line)
            except OSError:
                # Losing a log line must never take the whole run down.
                pass
