"""SoundCloud downloads via yt-dlp, followed by lyrics lookup and playlist refresh."""
import re
from pathlib import Path

from .config import ARCHIVE_FILENAME, AUDIO_EXTENSIONS
from .lyrics import fetch_lyrics
from .playlist import update_soundcloud_playlist
from .process import run_streamed

# Matches yt-dlp's default (--newline) progress line, e.g.:
# [download]  45.2% of ~3.45MiB at 1.23MiB/s ETA 00:02
_PROGRESS_RE = re.compile(
    r"\[download\]\s+(\d{1,3}(?:\.\d)?)%\s+of\s+~?\s*[\d.]+\w+\s+at\s+"
    r"([\d.]+\w+/s|Unknown speed)\s+ETA\s+(\S+)"
)
_DEST_RE = re.compile(r"\[download\] Destination:\s+(.+)$")
_ALREADY_RE = re.compile(r"has already been recorded in the archive")
_FINISHED_RE = re.compile(r"\[download\]\s+100(?:\.0)?%\s+of")
_ERROR_RE = re.compile(r"^ERROR:", re.IGNORECASE)
# Emitted by yt-dlp per item when the link is a playlist/user page, e.g.
# "[download] Downloading item 3 of 47" - this is the only place the *total*
# track count for a playlist link is available.
_ITEM_RE = re.compile(r"Downloading item (\d+) of (\d+)")

_HEARTBEAT_INTERVAL = 30.0  # seconds between "still working" log lines


def download_soundcloud(url: str, soundcloud_dir: Path, dashboard) -> bool:
    dashboard.log(f"[SoundCloud] Starting: {url}")
    dashboard.start_file(label="SoundCloud: preparing...")

    archive_file = soundcloud_dir / ARCHIVE_FILENAME
    output_template = str(soundcloud_dir / "%(title)s.%(ext)s")

    cmd = [
        "yt-dlp",
        "-x",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        "--embed-metadata",
        "--embed-thumbnail",
        # SoundCloud tracks often lack album/artist metadata, so fall back to
        # reasonable defaults instead of displaying "Unknown".
        "--parse-metadata", "%(uploader,artist,creator|Unknown Artist)s:%(meta_artist)s",
        "--parse-metadata", ":(?P<meta_album>SoundCloud)",
        "--postprocessor-args", "ffmpeg:-id3v2_version 3",
        "--download-archive", str(archive_file),
        "--no-overwrites",
        "--newline",
        "-o", output_template,
        url,
    ]

    had_error = False
    local_total_known = False
    current_track_counted = False
    first_output_seen = False
    last_heartbeat = 0.0

    def on_line(line: str) -> None:
        nonlocal had_error, local_total_known, current_track_counted, first_output_seen
        first_output_seen = True

        item_match = _ITEM_RE.search(line)
        if item_match and not local_total_known:
            dashboard.add_tracks_total("soundcloud", int(item_match.group(2)))
            local_total_known = True

        dest_match = _DEST_RE.search(line)
        if dest_match:
            current_track_counted = False
            filename = Path(dest_match.group(1)).name
            dashboard.update_file(label=f"SoundCloud: {filename[:60]}", percent=0, speed="", eta="")
            if not local_total_known:
                # No "item N of M" line was seen, so this is a single-track
                # link (not a playlist/user page) - it's exactly 1 track.
                dashboard.add_tracks_total("soundcloud", 1)
                local_total_known = True

        progress_match = _PROGRESS_RE.search(line)
        if progress_match:
            percent, speed, eta = progress_match.groups()
            dashboard.update_file(percent=float(percent), speed=speed, eta=f"ETA {eta}")

        if _ALREADY_RE.search(line):
            # This used to only produce a log line with nothing counted -
            # the track vanished from every counter. Now it's tracked as
            # "already had" so the totals actually add up.
            if not local_total_known:
                dashboard.add_tracks_total("soundcloud", 1)
                local_total_known = True
            dashboard.record_track("soundcloud", "skipped")
            current_track_counted = True
            dashboard.log("[SoundCloud] Already downloaded, skipping")

        if _FINISHED_RE.search(line) and not current_track_counted:
            current_track_counted = True
            dashboard.record_track("soundcloud", "done")
            dashboard.log("[SoundCloud] Download finished, converting...")

        if _ERROR_RE.match(line):
            had_error = True
            current_track_counted = True
            dashboard.record_track("soundcloud", "failed")
            dashboard.log_error("SoundCloud", line)

    def on_idle(idle_seconds: float) -> None:
        nonlocal last_heartbeat
        if not first_output_seen:
            dashboard.update_file(
                label=f"SoundCloud: preparing... (still working, {int(idle_seconds)}s, no output yet)"
            )
        if idle_seconds - last_heartbeat >= _HEARTBEAT_INTERVAL:
            dashboard.log(f"[SoundCloud] Still working, no output for {int(idle_seconds)}s...")
            last_heartbeat = idle_seconds

    returncode = run_streamed(cmd, on_line, on_idle=on_idle)
    dashboard.finish_file()

    if returncode == -1:
        dashboard.log_error(
            "SoundCloud", f"Timed out with no response while processing '{url}'"
        )
        return False
    if returncode != 0:
        dashboard.log_error("SoundCloud", f"yt-dlp exited with code {returncode} for '{url}'")
        return False

    lyrics_found = lyrics_missing = 0
    for audio_file in sorted(soundcloud_dir.iterdir()):
        if audio_file.is_file() and audio_file.suffix.lower() in AUDIO_EXTENSIONS:
            if audio_file.with_suffix(".lrc").exists():
                continue
            if fetch_lyrics(audio_file, dashboard):
                lyrics_found += 1
            else:
                lyrics_missing += 1

    dashboard.stats.lyrics_ok += lyrics_found
    dashboard.stats.lyrics_fail += lyrics_missing

    update_soundcloud_playlist(soundcloud_dir, dashboard)
    return not had_error
