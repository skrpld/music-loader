"""Spotify downloads via spotDL.

The Spotify links of a job are processed in two steps (`SpotifyBatch`):

1. `spotdl save` resolves each link (an album, a playlist, a whole artist
   discography) into a list of songs with their metadata and writes, through
   `--m3u`, the exact path every song gets under the output template. The
   links of a job are resolved in parallel, before anything is downloaded.
2. One `spotdl download` downloads the saved songs of all links together,
   each song once - no second round of Spotify API calls, and spotDL's
   `--threads` work across links instead of within one link at a time.

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
import shlex
import shutil
import sys
import tempfile
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .artists import PRIORITY_SPOTIFY, get_registry
from .availability import RATE_LIMIT_RE, FailureCategory, failure_category
from .config import (
    ARTISTS_FILENAME,
    AUDIO_EXTENSIONS,
    SPOTIFY_LYRICS_ATTEMPTS_FILENAME,
    SUBPROCESS_TIMEOUT_SECONDS,
    AppConfig,
)
from .links import Link
from .lyrics import LyricsRequest, LyricsService, get_attempts
from .net import probe
from .naming import album_artist_for, album_folder, spotify_title, track_filename
from .paths import move_with_sidecars, prune_empty_dirs, remove_with_sidecars
from .process import (
    run_captured,
    run_streamed,
    runs_in_this_python,
    tool_command,
    wait_future,
    ytdlp_extra_args,
)
from .spotify_index import get_library
from .tags import artists_current, audio_duration, read_woas, set_album_artist, set_artists

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
# Quiet for this long while resolving a link: say that Spotify may not answer.
_SLOW_RESOLVE_SECONDS = 60.0

# What spotDL talks to: the built-in client scrapes the web player, the official
# API (own credentials) uses these two hosts.
_BUILTIN_CLIENT_URLS = ("https://open.spotify.com/",)
_OFFICIAL_API_URLS = ("https://accounts.spotify.com/", "https://api.spotify.com/")
_REACHABLE_TIMEOUT = 10.0
_REACHABLE_ATTEMPTS = 2


def _not_reachable_hint() -> str:
    hint = "Spotify does not answer from this network: check the internet connection, VPN or proxy"
    if hasattr(sys, "getandroidapilevel"):
        hint += " (a VPN app must include Music Loader)"
    return f"{hint}, then run the link again."

# spotdl's own template variables; "{output-ext}" is its extension variable
# (an unknown name such as "{ext}" is written into the file name verbatim).
OUTPUT_TEMPLATE = "{album-artist} - {album}/{track-number} - {title}.{output-ext}"

_SECRET_ENV = "MUSIC_LOADER_SPOTIFY_CLIENT_SECRET"
# Runs spotdl in this interpreter with the client secret taken from the
# environment, so it never shows up in the process list, and with the cache of
# the web player's query hashes in place (spotify_cache.py).
_BOOTSTRAP = (
    "import os, runpy, sys\n"
    f"secret = os.environ.pop({_SECRET_ENV!r}, '')\n"
    "sys.argv = ['spotdl'] + sys.argv[1:] + (['--client-secret', secret] if secret else [])\n"
    "try:\n"
    "    from music_loader import spotify_cache\n"
    "    spotify_cache.install()\n"
    "except Exception as exc:\n"
    "    print('music-loader: Spotify hash cache not active:', exc, file=sys.stderr)\n"
    "runpy.run_module('spotdl', run_name='__main__', alter_sys=True)\n"
)


@dataclass
class _Song:
    data: dict[str, Any]
    # Where the song belongs in the library (naming.py), and where spotDL
    # writes it (its own template; the file is renamed after the download).
    path: Path | None
    work: Path | None = None
    album_artist: str = ""

    @property
    def url(self) -> str:
        return str(self.data.get("url") or "")

    @property
    def name(self) -> str:
        artists = ", ".join(self.data.get("artists") or [])
        return f"{artists} - {self.data.get('name') or ''}".strip(" -")


def spotdl_version(spotdl: list[str]) -> tuple[int, int, int] | None:
    if runs_in_this_python(spotdl):
        try:
            match = _VERSION_RE.search(importlib.metadata.version("spotdl"))
        except importlib.metadata.PackageNotFoundError:
            match = None
    else:
        code, stdout, stderr = run_captured(spotdl + ["--version"], timeout=60)
        match = _VERSION_RE.search(stdout + stderr) if code == 0 else None
    return tuple(int(part) for part in match.groups()) if match else None


def _command(spotdl: list[str]) -> list[str]:
    if spotdl[:2] == [sys.executable, "-m"]:
        return [sys.executable, "-c", _BOOTSTRAP]
    # An in-process run is set up by inprocess.py; a spotdl of another Python
    # environment cannot import this package.
    return list(spotdl)


def _credential_args(config: AppConfig, version: tuple[int, int, int] | None, spotdl: list[str]) -> tuple[list[str], dict[str, str]]:
    if not (config.spotify_client_id and config.spotify_client_secret):
        return [], {}
    args = ["--client-id", config.spotify_client_id]
    env: dict[str, str] = {}
    if spotdl[:2] == [sys.executable, "-m"]:
        env[_SECRET_ENV] = config.spotify_client_secret
    else:
        # A spotdl outside this Python environment cannot use the bootstrap;
        # an in-process run has no command line others could read.
        args +=["--client-secret", config.spotify_client_secret]
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


def _unreachable(
    urls: tuple[str, ...],
    timeout: float = _REACHABLE_TIMEOUT,
    attempts: int = _REACHABLE_ATTEMPTS,
) -> tuple[str, str] | None:
    """(url, reason) of the first URL that does not answer, None when all do.
    Any HTTP status is an answer: spotDL itself deals with refusals.

    Without this check an unreachable Spotify (blocked, a VPN that does not
    carry the app, no route) shows up as spotdl printing nothing for minutes:
    its client retries every stalled request several times."""
    for url in urls:
        reason = ""
        for _ in range(max(1, attempts)):
            try:
                probe(url, timeout=timeout)
                break
            except OSError as exc:
                reason = str(exc) or type(exc).__name__
        else:
            return url, reason
    return None


class _Run:
    """Line parsing and dashboard updates for one spotdl invocation."""

    def __init__(self, dashboard, music_dir: Path, total: int = 0, count_tracks: bool = True,
                 slot: int = 0):
        self.dashboard = dashboard
        self.slot = slot
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
        self.slow_hint_shown = False

    def _progress(self) -> None:
        if self.total:
            processed = self.done + self.skipped + self.failed
            self.dashboard.update_file(percent=min(100, processed * 100 / self.total), slot=self.slot)

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
            self.dashboard.update_file(label=f"Spotify: {status.group('name')[:50]} - {status.group('status')}",
                                       slot=self.slot)
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
        elif not self.count_tracks and idle_seconds >= _SLOW_RESOLVE_SECONDS and not self.slow_hint_shown:
            # An artist or a long playlist takes minutes; a stalled connection looks the same.
            self.slow_hint_shown = True
            self.dashboard.log(
                f"[Spotify] Still resolving after {int(idle_seconds)}s. Large artists and playlists "
                "take a while; if nothing changes, Spotify may not be reachable (VPN / network)."
            )
        elif not self.first_output:
            self.dashboard.update_file(label=f"Spotify: resolving the link... ({int(idle_seconds)}s)",
                                       slot=self.slot)
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
        for path in {song.path, song.work}:
            if path is not None and path.exists() and _is_complete(path, song.url) is False:
                remove_with_sidecars(path)
                dashboard.log(f"[Spotify] Removed unfinished file: {path.name}")


@dataclass
class SpotifyPlan:
    """One resolved link of a job."""
    link: Link
    songs: list[_Song] = field(default_factory=list)
    # False: nothing to download (the link could not be resolved).
    resolved: bool = False


@dataclass
class _Tool:
    base: list[str]
    common: list[str]
    env: dict[str, str]


class SpotifyBatch:
    """The Spotify links of one job: each link is resolved by its own
    `spotdl save` run (`resolve`, called from several threads at once), then
    every song of every link is downloaded by one `spotdl download` run
    (`download`), each song once however many links contain it."""

    def __init__(self, config: AppConfig, dashboard, abort: threading.Event | None = None):
        self.config = config
        self.dashboard = dashboard
        self.abort = abort or threading.Event()
        self._lock = threading.Lock()
        self._ready = False
        self._tool: _Tool | None = None
        self._unreachable = False

    def _setup(self) -> _Tool | None:
        """spotdl's command line, and whether Spotify answers at all; checked
        once per job (None: spotdl is missing or Spotify is not reachable)."""
        with self._lock:
            if self._ready:
                return self._tool
            self._ready = True
            config, dashboard = self.config, self.dashboard
            spotdl = tool_command("spotdl")
            if spotdl is None:
                dashboard.log_error("Spotify", "spotdl is not installed")
                return None
            version = spotdl_version(spotdl)
            cred_args, env = _credential_args(config, version, spotdl)
            # Credentials in cred_args: spotDL talks to the official API, else to the web player.
            blocked = _unreachable(_OFFICIAL_API_URLS if cred_args else _BUILTIN_CLIENT_URLS)
            if blocked is not None:
                dashboard.log_error("Spotify", f"Cannot reach {blocked[0]} ({blocked[1][:150]}). {_not_reachable_hint()}")
                self._unreachable = True
                return None
            common = [
                "--output", f"{config.music_dir}/{OUTPUT_TEMPLATE}", "--format", "mp3",
                # No spotdl lyrics: they are looked up and verified afterwards.
                "--lyrics",
                "--simple-tui", "--threads", str(max(1, config.spotify_threads)),
                # YouTube Music first; a song it does not return (it answers differently
                # by region and network) is looked up on YouTube, where spotDL applies the
                # same name / artist / duration checks.
                "--audio", "youtube-music", "youtube",
            ] + cred_args
            extra = ytdlp_extra_args()
            if extra:
                common += ["--yt-dlp-args", shlex.join(extra)]
            self._tool = _Tool(_command(spotdl), common, env)
            return self._tool

    def resolve(self, link: Link, slot: int = 0) -> SpotifyPlan:
        """`spotdl save` of one link: its songs with their metadata and the
        path spotDL gives each. Raises KeyboardInterrupt when the job is being
        cancelled; every other problem fails only this link."""
        plan = SpotifyPlan(link)
        url = link.url
        dashboard = self.dashboard
        dashboard.log(f"[Spotify] Resolving: {url}")
        dashboard.start_file(label="Spotify: preparing...", slot=slot)
        work_dir: Path | None = None
        try:
            if not self._ready:
                dashboard.update_file(label="Spotify: checking the connection...", slot=slot)
            tool = self._setup()
            if tool is None:
                if self._unreachable:
                    dashboard.record_failure("spotify", FailureCategory.NETWORK, url)
                return plan
            dashboard.update_file(label=f"Spotify: resolving {url[:60]}", slot=slot)
            work_dir = Path(tempfile.mkdtemp(prefix="music-loader-spotify-"))
            save_file = work_dir / "query.spotdl"
            m3u_file = work_dir / "paths.m3u8"
            run = _Run(dashboard, self.config.music_dir, count_tracks=False, slot=slot)
            code = run_streamed(
                tool.base + ["save", url, "--save-file", str(save_file), "--m3u", str(m3u_file)] + tool.common,
                run.on_line, on_idle=run.on_idle, timeout=SUBPROCESS_TIMEOUT_SECONDS, env=tool.env,
                should_abort=self.abort.is_set,
            )
            if code == -2:
                raise KeyboardInterrupt
            if code != 0 or not save_file.exists():
                reason = "timed out with no response" if code == -1 else f"spotdl exited with code {code}"
                dashboard.log_error("Spotify", f"Could not resolve '{url}': {reason}")
                run.report_tail()
                _refusal_hint(run.refused, self.config, dashboard)
                # The link as a whole failed; a retry runs it again.
                category = (FailureCategory.RATE_LIMITED if run.refused
                            else FailureCategory.NETWORK if code == -1
                            else failure_category(run.tail))
                dashboard.record_failure("spotify", category, url)
                return plan
            try:
                raw = json.loads(save_file.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                dashboard.log_error("Spotify", f"Could not read spotdl's song list: {exc}")
                return plan
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
                return plan
            plan.songs = songs
            plan.resolved = True
            dashboard.log(f"[Spotify] Found {len(songs)} track(s) in {url}")
            return plan
        finally:
            dashboard.finish_file(slot)
            if work_dir is not None:
                shutil.rmtree(work_dir, ignore_errors=True)

    @staticmethod
    def _unique(plans: list[SpotifyPlan]) -> list[_Song]:
        songs: dict[str, _Song] = {}
        for plan in plans:
            for song in plan.songs:
                songs.setdefault(song.url or f"#{id(song)}", song)
        return list(songs.values())

    @classmethod
    def track_count(cls, plans: list[SpotifyPlan]) -> int:
        """Songs the job will process: every song once, however many links
        contain it."""
        return len(cls._unique(plans))

    def download(self, plans: list[SpotifyPlan], lyrics: LyricsService | None = None) -> list[bool]:
        """Downloads the songs of every resolved link in one spotdl run.
        Returns, per plan, whether the link went through without failures."""
        songs = self._unique(plans)
        if self._tool is None or not songs:
            return [False for _ in plans]
        failed = self._download(songs, lyrics)
        if failed is None:
            return [False for _ in plans]
        return [plan.resolved and not any(song.url in failed for song in plan.songs) for plan in plans]

    def _download(self, songs: list[_Song], lyrics: LyricsService | None) -> set[str] | None:
        """URLs of the songs that failed; None when the whole run failed."""
        config, dashboard, tool = self.config, self.dashboard, self._tool
        assert tool is not None
        music_dir = config.music_dir
        dashboard.log(f"[Spotify] {len(songs)} track(s) to check")
        work_dir = Path(tempfile.mkdtemp(prefix="music-loader-spotify-"))
        save_file = work_dir / "query.spotdl"
        try:
            registry = get_registry(music_dir / ARTISTS_FILENAME)
            for song in songs:
                for artist in song.data.get("artists") or []:
                    registry.register(str(artist), PRIORITY_SPOTIFY)
                if song.data.get("album_artist"):
                    registry.register(str(song.data["album_artist"]), PRIORITY_SPOTIFY)
            registry.flush()
            _assign_targets(songs, music_dir, registry)

            # -- check what is already there ------------------------------------------------
            existed = _prepare(songs, config, dashboard)

            # -- download -----------------------------------------------------------------------
            run = _Run(dashboard, music_dir, total=len(songs))
            todo = _stage_for_spotdl(songs, existed, config, save_file, dashboard)
            code = 0
            if todo:
                dashboard.start_file(label="Spotify: downloading...")
                overwrite = "metadata" if config.recheck else "skip"
                code = run_streamed(
                    tool.base + ["download", str(save_file), "--bitrate", "320k", "--overwrite", overwrite,
                                 "--print-errors"] + tool.common,
                    run.on_line, on_idle=run.on_idle, timeout=SUBPROCESS_TIMEOUT_SECONDS, env=tool.env,
                )
                dashboard.finish_file()
            _place_downloads(songs, config, dashboard)

            # -- verify on disk -------------------------------------------------------------------
            failed = _verify(songs, existed, run, dashboard)
            if code != 0:
                reason = "timed out with no response" if code == -1 else f"spotdl exited with code {code}"
                dashboard.log_error("Spotify", f"{reason} ({len(todo)} track(s))")
                run.report_tail()
            if failed is None or failed or code != 0:
                _refusal_hint(run.refused, config, dashboard)

            # -- artist tags ----------------------------------------------------------------------
            _tag_artists(songs, existed, config, registry, dashboard)

            # -- lyrics ---------------------------------------------------------------------------
            if lyrics is not None and config.lyrics_enabled:
                _lyrics(songs, config, dashboard, lyrics)
            return None if code != 0 else failed
        except KeyboardInterrupt:
            # spotDL writes straight into the final file; never leave a half
            # converted one behind to be "skipped" next time.
            _cleanup_unfinished(songs, dashboard)
            raise
        finally:
            dashboard.finish_file()
            shutil.rmtree(work_dir, ignore_errors=True)


def download_spotify(
    link: Link,
    config: AppConfig,
    dashboard,
    lyrics: LyricsService | None = None,
) -> bool:
    """Downloads one Spotify link. Returns False when anything failed."""
    batch = SpotifyBatch(config, dashboard)
    plan = batch.resolve(link)
    if not plan.resolved:
        return False
    dashboard.add_tracks_total("spotify", batch.track_count([plan]))
    return batch.download([plan], lyrics)[0]


def _assign_targets(songs: list[_Song], music_dir: Path, registry) -> None:
    """Computes the library path of every song (naming.py). spotDL's own path
    becomes `work`; without the path mapping nothing is renamed."""
    albums: dict[str, list[list[str]]] = {}
    for song in songs:
        albums.setdefault(str(song.data.get("album_id") or ""), []).append(
            [str(name) for name in song.data.get("artists") or [] if name]
        )
    for song in songs:
        if song.path is None:
            continue
        data = song.data
        artist = registry.canonical(str(data.get("album_artist") or "")) or "Unknown Artist"
        group = albums.get(str(data.get("album_id") or ""), [])
        song.album_artist = album_artist_for(artist, group if data.get("album_id") else [])
        song.work = song.path
        song.path = music_dir / album_folder(song.album_artist, str(data.get("album_name") or "")) / track_filename(
            spotify_title(str(data.get("name") or "")),
            _int(data.get("track_number"), 1), _int(data.get("tracks_count"), 1),
            _int(data.get("disc_number"), 1), _int(data.get("disc_count"), 1),
            song.work.suffix or ".mp3",
        )


def _int(value: Any, default: int) -> int:
    try:
        return int(value) if value else default
    except (TypeError, ValueError):
        return default


def _stage_for_spotdl(songs: list[_Song], existed: set[str], config: AppConfig,
                      save_file: Path, dashboard) -> list[_Song]:
    """spotDL looks for a song at its own path. Complete songs are left out
    of its run; with --recheck they are put at its path for the metadata
    refresh and renamed back afterwards (a crash in between is repaired by
    the lookup by URL on the next run)."""
    todo: list[_Song] = []
    for song in songs:
        if song.url not in existed or song.path is None or song.work is None:
            todo.append(song)
            continue
        if not config.recheck:
            continue
        if song.work != song.path:
            try:
                move_with_sidecars(song.path, song.work)
            except OSError as exc:
                dashboard.log_error("Spotify", f"Could not stage '{song.path.name}' for the refresh: {exc}")
                continue
        todo.append(song)
    if len(todo) != len(songs):
        try:
            save_file.write_text(json.dumps([song.data for song in todo]), encoding="utf-8")
        except OSError as exc:
            dashboard.log_error("Spotify", f"Could not narrow spotdl's song list: {exc}")
            return songs
    return todo


def _place_downloads(songs: list[_Song], config: AppConfig, dashboard) -> None:
    """Renames what spotDL wrote to the library path."""
    for song in songs:
        work, target = song.work, song.path
        if work is None or target is None or work == target or not work.exists():
            continue
        state = _is_complete(work, song.url)
        if state is False:
            remove_with_sidecars(work)
            continue
        if state is None:
            continue
        if target.exists() and _is_complete(target, song.url) is None:
            # Another song already has this name.
            tail = song.url.rstrip("/").rsplit("/", 1)[-1]
            target = target.with_name(f"{target.stem} [{tail}]{target.suffix}")
            song.path = target
        try:
            move_with_sidecars(work, target)
        except OSError as exc:
            dashboard.log_error("Spotify", f"Could not rename '{work.name}' to '{target.name}': {exc}")
            song.path = work
            continue
        prune_empty_dirs(work.parent, config.music_dir)


def _prepare(songs: list[_Song], config: AppConfig, dashboard) -> set[str]:
    """Returns the URLs of songs that are complete before the download."""
    existed: set[str] = set()
    library = get_library(config.music_dir, [config.soundcloud_dir])
    for song in songs:
        path = song.path
        if path is None or not song.url:
            continue
        work = song.work
        if work is not None and work != path and work.exists() and _is_complete(work, song.url) is False:
            # Left by an interrupted run; spotDL would skip it as "existing".
            remove_with_sidecars(work)
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


def _tag_artists(songs: list[_Song], existed: set[str], config: AppConfig,
                 registry, dashboard) -> None:
    """spotDL writes ID3v2.3 with "/" between artists. Rewrites the artist
    frames of new files (and of every file with --recheck, which migrates an
    existing library) as "A, B" plus the real list in TXXX:ARTISTS."""
    changed = 0
    for song in songs:
        path = song.path
        if path is None or not path.exists() or not _is_complete(path, song.url):
            continue
        if song.url in existed and not config.recheck:
            continue
        names = [registry.canonical(str(name)) for name in song.data.get("artists") or [] if name]
        if song.album_artist and song.album_artist != str(song.data.get("album_artist") or ""):
            if not set_album_artist(path, song.album_artist):
                dashboard.log_error("Spotify", f"Could not write the album artist of {path.name}")
        if not names or artists_current(path, names):
            continue
        if set_artists(path, names):
            changed += 1
        else:
            dashboard.log_error("Spotify", f"Could not write the artist tags of {path.name}")
    if changed:
        dashboard.log(f"[Spotify] Artist tags written for {changed} file(s)")


def _verify(songs: list[_Song], existed: set[str], run: _Run, dashboard) -> set[str] | None:
    """Checks every song on disk and replaces the live counts with the exact
    result. Returns the URLs of the songs that failed; None when songs failed
    that cannot be told apart (no path mapping at all)."""
    if not any(song.path for song in songs):
        # No path mapping: fall back to what spotdl printed.
        for track_url, reason in run.errors.items():
            dashboard.record_failure("spotify", _song_category(reason, run), track_url)
        return None if run.failed and not run.errors else set(run.errors)
    done = skipped = 0
    failed: set[str] = set()
    for song in songs:
        if song.path is None:
            # A link whose paths could not be mapped: only spotdl's word is left.
            if song.url in run.errors:
                failed.add(song.url)
                dashboard.log_error("Spotify", f"{song.name}: {run.errors[song.url][:300]}")
                dashboard.record_failure("spotify", _song_category(run.errors[song.url], run),
                                         song.url, song.name)
            else:
                done += 1
            continue
        if song.path.exists() and _is_complete(song.path, song.url):
            if song.url in existed:
                skipped += 1
            else:
                done += 1
            continue
        failed.add(song.url)
        state = _is_complete(song.path, song.url) if song.path.exists() else False
        if song.url in run.errors:
            reason = run.errors[song.url]
        elif state is None:
            reason = f"another song already uses the file name '{song.path.name}'"
        else:
            reason = "file missing after the download"
        dashboard.log_error("Spotify", f"{song.name}: {reason[:300]}")
        dashboard.record_failure("spotify", _song_category(reason, run, missing=song.url not in run.errors),
                                 song.url, song.name)
        if song.path.exists() and state is False:
            remove_with_sidecars(song.path)
    # Replace the live estimate with the exact result.
    dashboard.record_track("spotify", "done", done - run.done)
    dashboard.record_track("spotify", "skipped", skipped - run.skipped)
    dashboard.record_track("spotify", "failed", len(failed) - run.failed)
    dashboard.log(f"[Spotify] Done: {done} downloaded, {skipped} already had, {len(failed)} failed")
    return failed


def _song_category(reason: str, run: "_Run", missing: bool = False) -> FailureCategory:
    """Why one song failed. A song that is just gone after a run in which
    Spotify refused requests was most likely one of the refused ones."""
    if RATE_LIMIT_RE.search(reason) or _REFUSED_RE.search(reason):
        return FailureCategory.RATE_LIMITED
    category = failure_category([reason])
    if category is FailureCategory.FAILED and missing and run.refused:
        return FailureCategory.RATE_LIMITED
    return category


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
    pool = ThreadPoolExecutor(max_workers=max(1, config.lyrics_workers), thread_name_prefix="sp-lyrics")
    aborted = False
    try:
        for future in [pool.submit(service.process, request, attempts, dashboard) for request in requests]:
            try:
                wait_future(future)
            except Exception as exc:
                dashboard.log_error("Lyrics", f"Lyrics worker failed: {exc}")
    except BaseException:
        # Ctrl+C: the lookups still queued are dropped, not waited for.
        aborted = True
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=aborted)


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
