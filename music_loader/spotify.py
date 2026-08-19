"""Spotify downloads via spotDL, with best-effort progress parsing.

spotDL's own console output format can vary slightly between versions, so
parsing here is best-effort: percentages and track titles are shown when
they can be recognized, and every raw line is still visible in the
dashboard's activity log regardless.

A link can be a single track, an album, a playlist, or an entire artist's
discography - the latter can mean spotdl needs a while to enumerate tracks
before it prints anything at all. `on_idle` below turns that silent wait
into a visible "still working" status instead of letting it look frozen.
"""
import re
from pathlib import Path

from .config import SUBPROCESS_TIMEOUT_SECONDS
from .process import run_streamed

_FOUND_RE = re.compile(r"Found (\d+) songs? in", re.IGNORECASE)
_DOWNLOADING_RE = re.compile(r"^Downloading[:\s]+(.+)$", re.IGNORECASE)
_PERCENT_RE = re.compile(r"(\d{1,3})%")
_DOWNLOADED_RE = re.compile(r"^Downloaded\b", re.IGNORECASE)
_SKIPPING_RE = re.compile(r"^Skipping\b", re.IGNORECASE)
_ERROR_RE = re.compile(r"^(Error|LookupError|Failed)\b", re.IGNORECASE)

_HEARTBEAT_INTERVAL = 30.0  # seconds between "still working" log lines


def download_spotify(url: str, music_dir: Path, dashboard) -> bool:
    dashboard.log(f"[Spotify] Starting: {url}")
    dashboard.start_file(label="Spotify: preparing...")

    output_template = f"{music_dir}/{{artist}} - {{album}}/{{track-number}} - {{title}}.{{ext}}"
    cmd = [
        "spotdl", "download", url,
        "--output", output_template,
        "--format", "mp3",
        "--bitrate", "320k",
        "--lyrics", "genius",
        "--generate-lrc",
        "--overwrite", "skip",
    ]

    total_tracks = 0
    done_tracks = 0
    first_output_seen = False
    last_heartbeat = 0.0

    def on_line(line: str) -> None:
        nonlocal total_tracks, done_tracks, first_output_seen
        first_output_seen = True

        found = _FOUND_RE.search(line)
        if found:
            count = int(found.group(1))
            total_tracks += count
            dashboard.add_tracks_total("spotify", count)
            dashboard.log(f"[Spotify] Found {count} track(s)")

        downloading_match = _DOWNLOADING_RE.match(line)
        if downloading_match:
            dashboard.update_file(label=f"Spotify: {downloading_match.group(1)[:60]}")

        percent_match = _PERCENT_RE.search(line)
        if percent_match:
            dashboard.update_file(percent=min(100, int(percent_match.group(1))))

        if _DOWNLOADED_RE.match(line):
            done_tracks += 1
            dashboard.record_track("spotify", "done")
            dashboard.log(f"[Spotify] {line} ({done_tracks}/{total_tracks or '?'})")
        elif _SKIPPING_RE.match(line):
            # Track already exists on disk - this used to vanish from the
            # counters entirely. Now it counts as "already had".
            done_tracks += 1
            dashboard.record_track("spotify", "skipped")
            dashboard.log(f"[Spotify] {line} ({done_tracks}/{total_tracks or '?'})")
        elif _ERROR_RE.match(line):
            dashboard.record_track("spotify", "failed")
            dashboard.log_error("Spotify", line)

    def on_idle(idle_seconds: float) -> None:
        nonlocal last_heartbeat
        if not first_output_seen:
            # Nothing printed yet at all - most likely spotdl is still
            # resolving the link (e.g. enumerating a whole discography).
            dashboard.update_file(
                label=f"Spotify: preparing... (still working, {int(idle_seconds)}s, no output yet)"
            )
        if idle_seconds - last_heartbeat >= _HEARTBEAT_INTERVAL:
            dashboard.log(f"[Spotify] Still working, no output for {int(idle_seconds)}s...")
            last_heartbeat = idle_seconds

    returncode = run_streamed(cmd, on_line, on_idle=on_idle, timeout=SUBPROCESS_TIMEOUT_SECONDS)
    dashboard.finish_file()

    if returncode == -1:
        dashboard.log_error(
            "Spotify", f"Timed out with no response while processing '{url}'"
        )
        return False
    if returncode != 0:
        dashboard.log_error("Spotify", f"spotdl exited with code {returncode} for '{url}'")
        return False
    return True
