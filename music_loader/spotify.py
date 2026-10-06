"""Spotify downloads via spotDL.

A link is processed in two spotdl runs:

1. `spotdl save` resolves the link (an album, a playlist, a whole artist
   discography) into a list of songs with their metadata and writes, through
   `--m3u`, the exact path every song gets under the output template.
2. `spotdl download` downloads that saved list - no second round of Spotify
   API calls.

Knowing every song and its path up front makes the run checkable:

* before the download, a file at the expected path without the URL spotDL
  embeds as the very last step is an interrupted download (spotDL converts
  straight into the final file and tags it afterwards; such a file used to be
  skipped as "already exists" forever) - it is deleted and downloaded again;
* a song that exists under an older folder layout is moved, with its
  lyrics, instead of being downloaded a second time;
* after the download every song is checked on disk, so the counters show
  what really happened: spotdl exits with 0 even when tracks failed, and its
  error lines come in many shapes.

Files are organized as "<album artist> - <album>/<NN> - <title>.mp3": the
album artist is the artist the album belongs to, so an album whose tracks
start with different artists stays in one folder.

spotDL's own lyrics (Genius matched at 55 % similarity, plus an unverified
.lrc search) are switched off; the verified lyrics step of music-loader runs
on the finished files instead.

Credentials: spotDL 4.5+ uses its built-in web client and needs none. Own
Spotify application credentials (`--spotify-client-id/--spotify-client-
secret` or the SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET variables) switch it
to the official Web API. The secret is handed to spotdl through the
environment, never on its command line, where every user of the machine
could read it in the process list.
"""
from __future__ import annotations

import importlib.metadata
import json
import re
import shutil
import sys
import tempfile
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .artists import PRIORITY_SPOTIFY, get_registry
from .config import (
    ARTISTS_FILENAME,
    AUDIO_EXTENSIONS,
    SPOTIFY_LYRICS_ATTEMPTS_FILENAME,
    SUBPROCESS_TIMEOUT_SECONDS,
    AppConfig,
)
from .links import Link
from .lyrics import LyricsRequest, LyricsService, get_attempts
from .paths import move_with_sidecars, prune_empty_dirs, remove_with_sidecars
from .process import run_captured, run_streamed, tool_command
from .spotify_index import get_library
from .tags import audio_duration, read_woas

_FOUND_RE = re.compile(r"Found (\d+) songs? in", re.IGNORECASE)
_STATUS_RE = re.compile(r"^(?P<name>.+): (?P<status>Searching for song|Downloading|Converting|"
                        r"Embedding metadata|Done|Error|Skipped)$")
_DOWNLOADED_RE = re.compile(r'^Downloaded\s+"(?P<name>.*)"', re.IGNORECASE)
_SKIPPING_RE = re.compile(r"^Skipping\b", re.IGNORECASE)
_UPDATED_RE = re.compile(r"^Updated metadata for\b", re.IGNORECASE)
_EXC_RE = re.compile(r"^(?:[A-Z]\w*(?:Error|Exception)|Error|Failed)\b[:\s]")
_URL_ERROR_RE = re.compile(r"^(https://open\.spotify\.com/track/\w+) - (.+)$")
_REFUSED_RE = re.compile(r"\b(?:429|403)\b|rate limit|too many requests", re.IGNORECASE)
_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")

_HEARTBEAT_INTERVAL = 20.0  # seconds between "still working" log lines
_TAIL_LINES = 20

# spotdl's own template variables; "{output-ext}" is its extension variable
# (an unknown name such as "{ext}" is written into the file name verbatim).
OUTPUT_TEMPLATE = "{album-artist} - {album}/{track-number} - {title}.{output-ext}"

_SECRET_ENV = "MUSIC_LOADER_SPOTIFY_CLIENT_SECRET"
# Runs spotdl in this interpreter with the client secret taken from the
# environment, so it never shows up in the process list.
_BOOTSTRAP = (
    "import os, runpy, sys\n"
    f"secret = os.environ.pop({_SECRET_ENV!r}, '')\n"
    "sys.argv = ['spotdl'] + sys.argv[1:] + (['--client-secret', secret] if secret else [])\n"
    "runpy.run_module('spotdl', run_name='__main__', alter_sys=True)\n"
)


@dataclass
class _Song:
    data: dict[str, Any]
    path: Path | None

    @property
    def url(self) -> str:
        return str(self.data.get("url") or "")

    @property
    def name(self) -> str:
        artists = ", ".join(self.data.get("artists") or [])
        return f"{artists} - {self.data.get('name') or ''}".strip(" -")


def spotdl_version(spotdl: list[str]) -> tuple[int, int, int] | None:
    if spotdl[:2] == [sys.executable, "-m"]:
        try:
            match = _VERSION_RE.search(importlib.metadata.version("spotdl"))
        except importlib.metadata.PackageNotFoundError:
            match = None
    else:
        code, stdout, stderr = run_captured(spotdl + ["--version"], timeout=60)
        match = _VERSION_RE.search(stdout + stderr) if code == 0 else None
    return tuple(int(part) for part in match.groups()) if match else None


def _command(spotdl: list[str], has_secret: bool) -> list[str]:
    if has_secret and spotdl[:2] == [sys.executable, "-m"]:
        return [sys.executable, "-c", _BOOTSTRAP]
    return list(spotdl)


def _credential_args(config: AppConfig, version: tuple[int, int, int] | None, spotdl: list[str]) -> tuple[list[str], dict[str, str]]:
    if not (config.spotify_client_id and config.spotify_client_secret):
        return [], {}
    args = ["--client-id", config.spotify_client_id]
    env: dict[str, str] = {}
    if spotdl[:2] == [sys.executable, "-m"]:
        env[_SECRET_ENV] = config.spotify_client_secret
    else:
        # A spotdl outside this Python environment cannot use the bootstrap.
        args += ["--client-secret", config.spotify_client_secret]
    if version is not None and version >= (4, 5, 0):
        # Since 4.5 spotdl ignores credentials unless told to use the
        # official Web API (older versions do not know the option).
        args.append("--use-official-api")
    # Otherwise spotdl keeps using the token cached for other credentials.
    args.append("--no-cache")
    return args, env


def _read_m3u(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def _is_complete(path: Path, url: str) -> bool | None:
    """True: finished spotDL file of this song. False: unfinished file
    (spotDL embeds the URL last). None: a file of another song."""
    found = read_woas(path)
    if not found:
        return False
    return True if found == url else None


class _Run:
    """Line parsing and dashboard updates for one spotdl invocation."""

    def __init__(self, dashboard, music_dir: Path, total: int = 0, count_tracks: bool = True):
        self.dashboard = dashboard
        self.count_tracks = count_tracks
        self.music_dir = music_dir
        self.total = total
        self.done = self.skipped = self.failed = 0
        self.found = 0
        self.errors: dict[str, str] = {}
        self.refused = False
        self.tail: deque[str] = deque(maxlen=_TAIL_LINES)
        self.started = time.time()
        self.first_output = False
        self.last_heartbeat = 0.0
        self.last_on_disk = 0

    def _progress(self) -> None:
        if self.total:
            processed = self.done + self.skipped + self.failed
            self.dashboard.update_file(percent=min(100, processed * 100 / self.total))

    def on_line(self, line: str) -> None:
        self.first_output = True
        self.tail.append(line)
        if _REFUSED_RE.search(line):
            self.refused = True
        found = _FOUND_RE.search(line)
        if found:
            self.found += int(found.group(1))
            self.dashboard.log(f"[Spotify] {line}")
            return
        url_error = _URL_ERROR_RE.match(line)
        if url_error:
            self.errors[url_error.group(1)] = url_error.group(2)
            return
        status = _STATUS_RE.match(line)
        if status:
            self.dashboard.update_file(label=f"Spotify: {status.group('name')[:50]} - {status.group('status')}")
            return
        if not self.count_tracks:
            return
        downloaded = _DOWNLOADED_RE.match(line)
        if downloaded:
            self.done += 1
            self.dashboard.record_track("spotify", "done")
            self.dashboard.log(f"[Spotify] Downloaded: {downloaded.group('name')}")
            self._progress()
            return
        if _SKIPPING_RE.match(line) or _UPDATED_RE.match(line):
            self.skipped += 1
            self.dashboard.record_track("spotify", "skipped")
            self._progress()
            return
        if _EXC_RE.match(line):
            self.failed += 1
            self.dashboard.record_track("spotify", "failed")
            self.dashboard.log(f"[Spotify][!] {line[:300]}")
            self._progress()

    def on_idle(self, idle_seconds: float) -> None:
        # Throttled against the wall clock: the idle counter restarts with
        # every printed line, so it alone would go silent after a while.
        now = time.monotonic()
        if now - self.last_heartbeat < _HEARTBEAT_INTERVAL:
            return
        self.last_heartbeat = now
        on_disk = _count_new_files(self.music_dir, self.started)
        if on_disk:
            new = on_disk - self.last_on_disk
            self.last_on_disk = on_disk
            self.dashboard.log(f"[Spotify] Working: {on_disk} file(s) written so far (+{new})")
        elif not self.first_output:
            self.dashboard.update_file(label=f"Spotify: resolving the link... ({int(idle_seconds)}s)")
        else:
            self.dashboard.log(f"[Spotify] Working, no output for {int(idle_seconds)}s...")

    def report_tail(self) -> None:
        for line in list(self.tail)[-6:]:
            self.dashboard.log_error("Spotify", f"spotdl: {line[:300]}")


def _count_new_files(music_dir: Path, since: float) -> int:
    count = 0
    try:
        for path in music_dir.rglob("*"):
            try:
                if path.suffix.lower() in AUDIO_EXTENSIONS and path.is_file() and path.stat().st_mtime >= since:
                    count += 1
            except OSError:
                continue
    except OSError:
        pass
    return count


def _cleanup_unfinished(songs: list[_Song], dashboard) -> None:
    for song in songs:
        if song.path is not None and song.path.exists() and _is_complete(song.path, song.url) is False:
            remove_with_sidecars(song.path)
            dashboard.log(f"[Spotify] Removed unfinished file: {song.path.name}")


def download_spotify(
    link: Link,
    config: AppConfig,
    dashboard,
    lyrics: LyricsService | None = None,
) -> bool:
    url = link.url
    music_dir = config.music_dir
    dashboard.log(f"[Spotify] Starting: {url}")
    dashboard.start_file(label="Spotify: preparing...")

    spotdl = tool_command("spotdl")
    if spotdl is None:
        dashboard.log_error("Spotify", "spotdl is not installed")
        dashboard.finish_file()
        return False
    version = spotdl_version(spotdl)
    cred_args, env = _credential_args(config, version, spotdl)
    base = _command(spotdl, bool(env))
    template = f"{music_dir}/{OUTPUT_TEMPLATE}"
    common = [
        "--output", template, "--format", "mp3",
        # No spotdl lyrics: they are looked up and verified afterwards.
        "--lyrics",
        "--simple-tui", "--threads", str(max(1, config.spotify_threads)),
    ] + cred_args

    work_dir = Path(tempfile.mkdtemp(prefix="music-loader-spotify-"))
    save_file = work_dir / "query.spotdl"
    m3u_file = work_dir / "paths.m3u8"
    songs: list[_Song] = []
    try:
        # -- 1. resolve --------------------------------------------------------------
        resolve = _Run(dashboard, music_dir, count_tracks=False)
        code = run_streamed(
            base + ["save", url, "--save-file", str(save_file), "--m3u", str(m3u_file)] + common,
            resolve.on_line, on_idle=resolve.on_idle, timeout=SUBPROCESS_TIMEOUT_SECONDS, env=env,
        )
        if code != 0 or not save_file.exists():
            reason = "timed out with no response" if code == -1 else f"spotdl exited with code {code}"
            dashboard.log_error("Spotify", f"Could not resolve '{url}': {reason}")
            resolve.report_tail()
            _refusal_hint(resolve.refused, config, dashboard)
            return False
        try:
            raw = json.loads(save_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            dashboard.log_error("Spotify", f"Could not read spotdl's song list: {exc}")
            return False
        song_data = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
        paths = _read_m3u(m3u_file)
        if len(paths) != len(song_data):
            dashboard.log("[Spotify] Could not map songs to file paths; post-download checks are limited")
            paths = []
        songs = [
            _Song(data, Path(paths[index]) if paths else None)
            for index, data in enumerate(song_data)
        ]
        if not songs:
            dashboard.log_error("Spotify", f"spotdl found no tracks for '{url}'")
            return False
        dashboard.add_tracks_total("spotify", len(songs))
        dashboard.log(f"[Spotify] {len(songs)} track(s) to check")

        registry = get_registry(music_dir / ARTISTS_FILENAME)
        for song in songs:
            for artist in song.data.get("artists") or []:
                registry.register(str(artist), PRIORITY_SPOTIFY)
            if song.data.get("album_artist"):
                registry.register(str(song.data["album_artist"]), PRIORITY_SPOTIFY)
        registry.flush()

        # -- 2. check what is already there --------------------------------------------
        existed = _prepare(songs, config, dashboard)

        # -- 3. download -----------------------------------------------------------------
        run = _Run(dashboard, music_dir, total=len(songs))
        dashboard.start_file(label="Spotify: downloading...")
        overwrite = "metadata" if config.recheck else "skip"
        code = run_streamed(
            base + ["download", str(save_file), "--bitrate", "320k", "--overwrite", overwrite,
                    "--print-errors"] + common,
            run.on_line, on_idle=run.on_idle, timeout=SUBPROCESS_TIMEOUT_SECONDS, env=env,
        )
        dashboard.finish_file()

        # -- 4. verify on disk ------------------------------------------------------------
        ok = _verify(songs, existed, run, dashboard)
        if code != 0:
            ok = False
            reason = "timed out with no response" if code == -1 else f"spotdl exited with code {code}"
            dashboard.log_error("Spotify", f"{reason} for '{url}'")
            run.report_tail()
        if not ok:
            _refusal_hint(run.refused, config, dashboard)

        # -- 5. lyrics --------------------------------------------------------------------
        if lyrics is not None and config.lyrics_enabled:
            _lyrics(songs, config, dashboard, lyrics)
        return ok
    except KeyboardInterrupt:
        # spotDL writes straight into the final file; never leave a half
        # converted one behind to be "skipped" next time.
        _cleanup_unfinished(songs, dashboard)
        raise
    finally:
        dashboard.finish_file()
        shutil.rmtree(work_dir, ignore_errors=True)


def _prepare(songs: list[_Song], config: AppConfig, dashboard) -> set[str]:
    """Returns the URLs of songs that are complete before the download."""
    existed: set[str] = set()
    library = get_library(config.music_dir, [config.soundcloud_dir])
    for song in songs:
        path = song.path
        if path is None or not song.url:
            continue
        if path.exists():
            state = _is_complete(path, song.url)
            if state is True:
                existed.add(song.url)
            elif state is False:
                remove_with_sidecars(path)
                dashboard.log(f"[Spotify] Unfinished file from an earlier run, downloading again: {path.name}")
            continue
        others = library.find(song.url)
        if others:
            source = max(others, key=lambda item: item.stat().st_mtime)
            try:
                move_with_sidecars(source, path)
            except OSError as exc:
                dashboard.log_error("Spotify", f"Could not move '{source}' to '{path}': {exc}")
                continue
            library.moved(source, path, song.url)
            prune_empty_dirs(source.parent, config.music_dir)
            existed.add(song.url)
            dashboard.log(f"[Spotify] Moved to the current folder layout: {path.relative_to(config.music_dir)}")
    if config.recheck:
        # Copies of the same Spotify track under other paths (an older
        # layout, a renamed album) only clutter the library.
        for song in songs:
            if song.path is None or song.url not in existed:
                continue
            for duplicate in library.find(song.url):
                if duplicate != song.path and duplicate.exists():
                    remove_with_sidecars(duplicate)
                    library.removed(duplicate, song.url)
                    prune_empty_dirs(duplicate.parent, config.music_dir)
                    dashboard.log(f"[Spotify] Removed duplicate copy: {duplicate}")
    return existed


def _verify(songs: list[_Song], existed: set[str], run: _Run, dashboard) -> bool:
    if not any(song.path for song in songs):
        # No path mapping: fall back to what spotdl printed.
        return run.failed == 0 and not run.errors
    done = skipped = failed = 0
    for song in songs:
        if song.path is not None and song.path.exists() and _is_complete(song.path, song.url):
            if song.url in existed:
                skipped += 1
            else:
                done += 1
            continue
        failed += 1
        state = _is_complete(song.path, song.url) if song.path is not None and song.path.exists() else False
        if song.url in run.errors:
            reason = run.errors[song.url]
        elif state is None:
            reason = f"another song already uses the file name '{song.path.name}'"
        else:
            reason = "file missing after the download"
        dashboard.log_error("Spotify", f"{song.name}: {reason[:300]}")
        if song.path is not None and song.path.exists() and state is False:
            remove_with_sidecars(song.path)
    # Replace the live estimate with the exact result.
    dashboard.record_track("spotify", "done", done - run.done)
    dashboard.record_track("spotify", "skipped", skipped - run.skipped)
    dashboard.record_track("spotify", "failed", failed - run.failed)
    dashboard.log(f"[Spotify] Done: {done} downloaded, {skipped} already had, {failed} failed")
    return failed == 0


def _lyrics(songs: list[_Song], config: AppConfig, dashboard, service: LyricsService) -> None:
    attempts = get_attempts(config.music_dir / SPOTIFY_LYRICS_ATTEMPTS_FILENAME)
    requests: list[LyricsRequest] = []
    for song in songs:
        path = song.path
        if path is None or not path.exists() or not _is_complete(path, song.url):
            continue
        artists = [str(artist) for artist in song.data.get("artists") or [] if artist]
        main = [str(song.data.get("artist") or (artists[0] if artists else ""))]
        requests.append(LyricsRequest(
            key=song.url,
            audio_path=path,
            main_artists=[name for name in main if name],
            featured=[name for name in artists if name not in main],
            title=str(song.data.get("name") or ""),
            album=str(song.data.get("album_name") or ""),
            duration=audio_duration(path) or song.data.get("duration"),
            force=config.recheck,
        ))
    if not requests:
        return
    dashboard.start_file(label="Spotify: lyrics...")
    with ThreadPoolExecutor(max_workers=max(1, config.lyrics_workers), thread_name_prefix="sp-lyrics") as pool:
        for future in [pool.submit(service.process, request, attempts, dashboard) for request in requests]:
            try:
                future.result()
            except Exception as exc:
                dashboard.log_error("Lyrics", f"Lyrics worker failed: {exc}")


def _refusal_hint(refused: bool, config: AppConfig, dashboard) -> None:
    if not refused:
        return
    if config.spotify_client_id and config.spotify_client_secret:
        dashboard.log_error(
            "Spotify",
            "Spotify refused requests (rate limit / HTTP 403). Wait a while and run the link again.",
        )
    else:
        dashboard.log_error(
            "Spotify",
            "Spotify refused requests (rate limit / HTTP 403). Wait a while and run the link again, "
            "or use own application credentials (--spotify-client-id / --spotify-client-secret, "
            "or SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET), which switch spotdl to the official API.",
        )
