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
before it prints anything at all. An artist link also issues far more
Spotify API calls than a single album, which is exactly the case where
spotDL's shared default API credentials get rate limited or rejected. The
last lines spotdl printed are therefore kept and written to the failure log,
so the real reason is visible instead of only an exit code.
"""
import re
import time
from collections import deque
from pathlib import Path

from .config import AUDIO_EXTENSIONS, SUBPROCESS_TIMEOUT_SECONDS
from .process import run_streamed

_FOUND_RE = re.compile(r"Found (\d+) songs? in", re.IGNORECASE)
_DOWNLOADING_RE = re.compile(r"^Downloading[:\s]+(.+)$", re.IGNORECASE)
_PERCENT_RE = re.compile(r"(\d{1,3})%")
_DOWNLOADED_RE = re.compile(r'^Downloaded\b[:\s]*"?(.*?)"?\s*[:.]?\s*$', re.IGNORECASE)
_SKIPPING_RE = re.compile(r"^Skipping\b", re.IGNORECASE)
_ERROR_RE = re.compile(r"^(Error|LookupError|Failed)\b", re.IGNORECASE)

_HEARTBEAT_INTERVAL = 20.0  # seconds between "still working" log lines
_TAIL_LINES = 20  # how much of spotdl's output is kept for failure reports

# spotdl's own file-extension variable. Any other name (for example "{ext}")
# is not a known template variable, so spotdl leaves it in the file name
# verbatim: files end up called "01 - Title.{ext}" instead of ".mp3", which
# no player recognizes, the library scan cannot see, and every later run
# downloads again because the expected .mp3 is never there.
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


def _build_command(
    url: str,
    music_dir: Path,
    client_id: str | None,
    client_secret: str | None,
) -> list[str]:
    cmd = [
        "spotdl", "download", url,
        "--output", f"{music_dir}/{_OUTPUT_TEMPLATE}",
        "--format", "mp3",
        "--bitrate", "320k",
        "--lyrics", "genius",
        "--generate-lrc",
        "--overwrite", "skip",
        # Plain, line-based output instead of the redrawn live progress area.
        "--simple-tui",
        # Makes spotdl list the tracks it failed on at the end instead of
        # letting them disappear with the progress area.
        "--print-errors",
    ]
    if client_id and client_secret:
        # Own Spotify application credentials. The shared defaults are the
        # usual reason a large query (an artist discography) fails with 403
        # or a rate limit while a single track still works.
        # `--no-cache` is needed too: spotdl otherwise reuses the token
        # cached from the previous credentials until it expires, so the new
        # ones would silently have no effect.
        cmd += [
            "--client-id", client_id,
            "--client-secret", client_secret,
            "--no-cache",
        ]
    return cmd


def download_spotify(
    url: str,
    music_dir: Path,
    dashboard,
    client_id: str | None = None,
    client_secret: str | None = None,
) -> bool:
    dashboard.log(f"[Spotify] Starting: {url}")
    dashboard.start_file(label="Spotify: preparing...")

    cmd = _build_command(url, music_dir, client_id, client_secret)

    started = time.time()
    total_tracks = 0
    done_tracks = 0
    first_output_seen = False
    last_heartbeat_at = 0.0
    last_seen_on_disk = 0
    tail: deque[str] = deque(maxlen=_TAIL_LINES)

    def _sync_percent() -> None:
        if total_tracks > 0:
            percent = min(100, int(done_tracks * 100 / total_tracks))
            dashboard.update_file(percent=percent)

    def on_line(line: str) -> None:
        nonlocal total_tracks, done_tracks, first_output_seen
        first_output_seen = True
        tail.append(line)

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
        nonlocal last_heartbeat_at, last_seen_on_disk
        # Throttled against the wall clock, not against `idle_seconds`: the
        # idle counter restarts whenever the child prints something, so
        # comparing the two made the heartbeat go permanently silent after
        # the first long quiet stretch - which is exactly when a long
        # discography run needs it most.
        now = time.monotonic()
        if now - last_heartbeat_at < _HEARTBEAT_INTERVAL:
            return
        last_heartbeat_at = now

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
            dashboard.log(f"[Spotify] Working, no output for {int(idle_seconds)}s...")

    returncode = run_streamed(cmd, on_line, on_idle=on_idle, timeout=SUBPROCESS_TIMEOUT_SECONDS)
    dashboard.finish_file()

    def report_tail() -> None:
        """Writes the last lines spotdl printed into the failure log.

        Without this a failed artist/playlist link left nothing but an exit
        code, while the actual cause (an HTTP 403 from the Spotify API, a
        rate limit, a missing dependency) was printed right before it.
        """
        for line in list(tail)[-6:]:
            dashboard.log_error("Spotify", f"spotdl: {line[:300]}")

    if returncode == -1:
        dashboard.log_error(
            "Spotify", f"Timed out with no response while processing '{url}'"
        )
        report_tail()
        return False
    if returncode != 0:
        dashboard.log_error("Spotify", f"spotdl exited with code {returncode} for '{url}'")
        report_tail()
        if not client_id or not client_secret:
            dashboard.log_error(
                "Spotify",
                "Large queries such as an artist discography often fail with spotDL's "
                "shared API credentials. Provide your own with --spotify-client-id / "
                "--spotify-client-secret, or via the SPOTIFY_CLIENT_ID / "
                "SPOTIFY_CLIENT_SECRET environment variables.",
            )
        return False
    return True
