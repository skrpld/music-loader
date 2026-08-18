"""Spotify downloads via spotDL, with best-effort progress parsing.

spotDL's own console output format can vary slightly between versions, so
parsing here is best-effort: percentages and track titles are shown when
they can be recognized, and every raw line is still visible in the
dashboard's activity log regardless.
"""
import re
from pathlib import Path

from .process import run_streamed

_FOUND_RE = re.compile(r"Found (\d+) songs? in", re.IGNORECASE)
_DOWNLOADING_RE = re.compile(r"^Downloading[:\s]+(.+)$", re.IGNORECASE)
_PERCENT_RE = re.compile(r"(\d{1,3})%")
_DONE_RE = re.compile(r"^(Downloaded|Skipping)\b", re.IGNORECASE)
_ERROR_RE = re.compile(r"^(Error|LookupError|Failed)\b", re.IGNORECASE)


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

    def on_line(line: str) -> None:
        nonlocal total_tracks, done_tracks

        found = _FOUND_RE.search(line)
        if found:
            total_tracks = int(found.group(1))
            dashboard.log(f"[Spotify] Found {total_tracks} track(s)")

        downloading_match = _DOWNLOADING_RE.match(line)
        if downloading_match:
            dashboard.update_file(label=f"Spotify: {downloading_match.group(1)[:60]}")

        percent_match = _PERCENT_RE.search(line)
        if percent_match:
            dashboard.update_file(percent=int(percent_match.group(1)))

        if _DONE_RE.match(line):
            done_tracks += 1
            if total_tracks:
                dashboard.log(f"[Spotify] {line} ({done_tracks}/{total_tracks})")
            else:
                dashboard.log(f"[Spotify] {line}")

        if _ERROR_RE.match(line):
            dashboard.log(f"[Spotify][!] {line}")

    returncode = run_streamed(cmd, on_line)
    dashboard.finish_file()

    if returncode != 0:
        dashboard.log(f"[Spotify][!] spotdl exited with code {returncode}")
        return False
    return True
