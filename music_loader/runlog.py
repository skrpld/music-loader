"""Persistent log of failed operations for the current run.

The live dashboard only keeps the last few "Activity" lines on screen, so a
failure that happened at link #3 out of 40 is gone from view by the time the
run finishes. This module writes every failure to a plain-text file as it
happens, so nothing is lost — the file can be checked (or grepped) after a
long batch run to see exactly what needs retrying.
"""
import threading
from datetime import datetime
from pathlib import Path


class RunLog:
    """Append-only failure log for a single run of music-loader."""

    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path = directory / f"failures-{timestamp}.log"
        self.count = 0
        self._lock = threading.Lock()

    def record(self, source: str, message: str) -> None:
        """Appends one failure line. `source` is a short tag such as
        'Spotify', 'SoundCloud', or 'Lyrics'."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
