"""Spotify downloads via spotDL, with best-effort progress parsing.

spotDL's rich-based TUI redraws a live progress area instead of printing
plain lines, so from the outside it can look completely silent for minutes
while tracks are in fact already being written to disk. `--simple-tui` is
therefore forced: it makes spotdl print one line per event, which is what
the parsing below relies on.

As a second safety net, the idle handler counts audio files that appeared
in the target folder since the run started, so even a version whose output
we cannot parse still shows real progress instead of "no output".

A link can be a single track, an album, a playlist, or an entire artist's
discography - the latter can mean spotdl needs a while to enumerate tracks
before it prints anything at all.
"""
import re
import time
from pathlib import Path

from .config import AUDIO_EXTENSIONS, SUBPROCESS_TIMEOUT_SECONDS
from .process import run_streamed

_FOUND_RE = re.compile(r"Found (\d+) songs? in", re.IGNORECASE)
_DOWNLOADING_RE = re.compile(r"^Downloading[:\s]+(.+)$", re.IGNORECASE)
_PERCENT_RE = re.compile(r"(\d{1,3})%")
_DOWNLOADED_RE = re.compile(r'^Downloaded\b[:\s]*"?(.*?)"?\s*[:.]?\s*$', re.IGNORECASE)
_SKIPPING_RE = re.compile(r"^Skipping\b", re.IGNORECASE)
_ERROR_RE = re.compile(r"^(Error|LookupError|AudioProviderError|Failed)\b", re.IGNORECASE)

_HEARTBEAT_INTERVAL = 20.0  # seconds between "still working" log lines

# spotDL's own template variables. The extension placeholder is
# "{output-ext}"; an unknown placeholder such as "{ext}" is left in the name
# verbatim, which produces files ending in a bogus extension that ffmpeg
# cannot even write ("unable to find a suitable output format") - every
# track of the link then fails and nothing appears in the library.
_OUTPUT_TEMPLATE = "{artist} - {album}/{track-number} - {title}.{output-ext}"


def _count_new_files(music_dir: Path, since: float) -> int:
    """Number of audio files written into the library since `since`.

    Used only to prove that something is happening while spotdl is quiet.
    """
    count = 0
    try:
        for path in music_dir.rglob("*"):
            try:
                if path.suffix.lower() not in AUDIO_EXTENSIONS or not path.is_file():
                    continue
                if path.stat().st_mtime >= since:
                    count += 1
            except OSError:
                continue
    except OSError:
        return count
    return count


def download_spotify(url: str, music_dir: Path, dashboard) -> bool:
    dashboard.log(f"[Spotify] Starting: {url}")
    dashboard.start_file(label="Spotify: preparing...")

    output_template = f"{music_dir}/{_OUTPUT_TEMPLATE}"
    cmd = [
        "spotdl", "download", url,
        "--output", output_template,
        "--format", "mp3",
        "--bitrate", "320k",
        # "synced" is what actually produces timestamped .lrc files;
        # genius/musixmatch stay as fallbacks for plain embedded lyrics.
        "--lyrics", "synced", "musixmatch", "genius",
        "--generate-lrc",
        "--overwrite", "skip",
        # Print the reason a song could not be downloaded instead of
        # failing silently, so the failure log says something useful.
        "--print-errors",
        # Plain, line-based output instead of the redrawn live progress area.
        "--simple-tui",
    ]

    started = time.time()
    total_tracks = 0
    done_tracks = 0
    first_output_seen = False
    last_heartbeat = 0.0  # monotonic timestamp of the last heartbeat line
    last_seen_on_disk = 0

    def _sync_percent() -> None:
        if total_tracks > 0:
            percent = min(100, int(done_tracks * 100 / total_tracks))
            dashboard.update_file(percent=percent)

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

        downloaded_match = _DOWNLOADED_RE.match(line)
        if downloaded_match:
            done_tracks += 1
            dashboard.record_track("spotify", "done")
            name = downloaded_match.group(1).strip()
            if name:
                dashboard.update_file(label=f"Spotify: {name[:60]}")
            _sync_percent()
            dashboard.log(f"[Spotify] {line} ({done_tracks}/{total_tracks or '?'})")
            return

        if _SKIPPING_RE.match(line):
            # Track already exists on disk - this used to vanish from the
            # counters entirely. Now it counts as "already had".
            done_tracks += 1
            dashboard.record_track("spotify", "skipped")
            _sync_percent()
            dashboard.log(f"[Spotify] {line} ({done_tracks}/{total_tracks or '?'})")
            return

        if _ERROR_RE.match(line):
            dashboard.record_track("spotify", "failed")
            dashboard.log_error("Spotify", line)
            return

        percent_match = _PERCENT_RE.search(line)
        if percent_match and total_tracks == 0:
            dashboard.update_file(percent=min(100, int(percent_match.group(1))))

    def on_idle(idle_seconds: float) -> None:
        nonlocal last_heartbeat, last_seen_on_disk
        now = time.monotonic()
        # Compared against a real timestamp, not against the idle counter:
        # the counter restarts from zero every time the child prints
        # something, which used to silence the heartbeat for good after the
        # first long quiet stretch.
        if now - last_heartbeat < _HEARTBEAT_INTERVAL:
            return
        last_heartbeat = now

        # spotdl may be silent while it is either resolving the link or
        # actually writing files. Looking at the folder tells the difference.
        on_disk = _count_new_files(music_dir, started)
        if on_disk:
            new_since_last = on_disk - last_seen_on_disk
            last_seen_on_disk = on_disk
            dashboard.update_file(
                label=f"Spotify: downloading... ({on_disk} file(s) written so far)"
            )
            dashboard.log(
                f"[Spotify] Working: {on_disk} file(s) written so far "
                f"(+{new_since_last} since the last check)"
            )
            return

        if not first_output_seen:
            dashboard.update_file(
                label=f"Spotify: resolving the link... ({int(idle_seconds)}s)"
            )
            dashboard.log(
                f"[Spotify] Resolving the link, no output yet ({int(idle_seconds)}s)..."
            )
        else:
            dashboard.log(
                f"[Spotify] Working, no new output for {int(idle_seconds)}s "
                f"and no file written yet..."
            )

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
