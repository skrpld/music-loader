"""Persistent log of failed operations for the current run.

The live dashboard only keeps the last few "Activity" lines on screen, so a
failure that happened at link #3 out of 40 is gone from view by the time the
run finishes. This module writes every failure to a plain-text file as it
happens, so nothing is lost — the file can be checked (or grepped) after a
long batch run to see exactly what needs retrying.
"""
from datetime import datetime
from pathlib import Path


class RunLog:
    """Append-only failure log for a single run of music-loader."""

    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path = directory / f"failures-{timestamp}.log"
        self.count = 0

    def record(self, source: str, message: str) -> None:
        """Appends one failure line. `source` is a short tag such as
        'Spotify', 'SoundCloud', or 'Lyrics'."""
        self.count += 1
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] [{source}] {message}\n"
        # Opened/closed per call (not kept open) so the file is always
        # flushed to disk and readable mid-run, and so a crash doesn't lose
        # buffered lines.
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line)
