"""Library check: `music-loader check` and server jobs of type "check".

Reads every audio file of the library, works out which track it is (its id
in the tags, else its index entry or Spotify link) and checks it against a
set of rules. By default it only reports; `--fix` is a deliberate second
step that solves what can be solved automatically and then checks again.

Rules (each can be switched off):

* ``id`` - the file carries its SoundCloud or Spotify id. A file the index
  or its Spotify link still recognises gets the id written into it (--fix).
* ``cover`` - an embedded cover. --fix rewrites the track, which downloads
  the cover again.
* ``lyrics`` - lyrics (an .lrc next to the file or embedded) or a recorded
  search that found nothing. --fix searches with the current settings.
* ``artist`` - the artists are written the way the artist registry and the
  account decisions (accounts.py) say now; --fix rewrites the track (tags
  and folder). Reuploads whose artist could not be determined are listed
  too; nothing fixes them automatically.
* ``format`` - the file was written under the current rules
  (config.FORMAT_VERSION); --fix rewrites it like "Update outdated".
* ``duplicates`` - no two files carry the same id. Only reported.
* ``available`` - the track still exists on its platform. Needs the
  network (left out with --offline), cached per id for a week, only
  reported: nothing is ever deleted or changed because of it.

The format and artist rules only apply to files that belong to a track (an
id in the tags or a recognised one); a file music-loader knows nothing about
is reported once, as not linked.

--fix goes through the download pipelines with "Existing tracks: all" for
exactly the tracks that need a rewrite (tags, cover, folder and file name,
moves with their .lrc, index updates, the same Ctrl+C cleanup), with
`AppConfig.keep_files` set: a fix never deletes an audio file.
"""
from __future__ import annotations

import dataclasses
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from . import __version__, debuglog
from .accounts import RunAccounts
from .artists import get_registry
from .config import (
    ARTISTS_FILENAME,
    AUDIO_EXTENSIONS,
    AVAILABILITY_CACHE_SECONDS,
    AVAILABILITY_FILENAME,
    EXISTING_ALL,
    FORMAT_VERSION,
    LOGS_DIRNAME,
    LYRICS_ATTEMPTS_FILENAME,
    SPOTIFY_LYRICS_ATTEMPTS_FILENAME,
    AppConfig,
)
from .existing import account_state, artists_changed
from .links import redact_url
from .lyrics import LyricsRequest, LyricsService, get_attempts
from .net import http_get
from .process import wait_future
from .soundcloud_index import get_index
from .tags import audio_duration, bind_ids, read_tags, spotify_track_id
from .text_utils import looks_instrumental

RULES = ("id", "cover", "lyrics", "artist", "format", "duplicates", "available")
# Rules that need the network (left out with --offline).
ONLINE_RULES = ("available",)


@dataclass(frozen=True)
class Kind:
    """One kind of problem: a group of the report."""
    name: str
    rule: str
    label: str


KINDS = (
    Kind("no_id", "id", "Not linked to its track"),
    Kind("no_cover", "cover", "No cover"),
    Kind("no_lyrics", "lyrics", "No lyrics"),
    Kind("artist_changed", "artist", "Artist written differently"),
    Kind("artist_unknown", "artist", "Artist not determined"),
    Kind("old_format", "format", "Written by an older version"),
    Kind("duplicate", "duplicates", "Duplicate files"),
    Kind("removed", "available", "Removed from the platform"),
)
KINDS_BY_NAME = {kind.name: kind for kind in KINDS}
# Problems --fix solves by rewriting the track.
REWRITE_KINDS = frozenset({"no_cover", "artist_changed", "old_format"})

# Tracks per group in the job result; the log file lists every one.
REPORT_ITEMS_KEPT = 500
# Spotify tracks resolved by one `spotdl save` during --fix.
SPOTIFY_FIX_BATCH = 50
# SoundCloud tracks per availability request (the API's batch size).
_SOUNDCLOUD_BATCH = 50
_SPOTIFY_WORKERS = 4
_PROGRESS_EVERY = 25

STATE_OK = "ok"
STATE_REMOVED = "removed"
STATE_BLOCKED = "blocked"
_KNOWN_STATES = (STATE_OK, STATE_REMOVED, STATE_BLOCKED)


def parse_rules(value: Any) -> tuple[str, ...]:
    """"cover,lyrics" or ["cover", "lyrics"] -> ("cover", "lyrics"): known
    names only, each once, at least one, in the order of RULES. Raises
    ValueError with a readable reason."""
    items = value.split(",") if isinstance(value, str) else value
    if not isinstance(items, (list, tuple)):
        raise ValueError("the rules must be a list")
    names: list[str] = []
    for item in items:
        name = item.strip().lower() if isinstance(item, str) else None
        if name not in RULES:
            raise ValueError(f"unknown rule {item!r}; use {', '.join(RULES)}")
        if name not in names:
            names.append(name)
    if not names:
        raise ValueError("at least one rule is needed")
    return tuple(rule for rule in RULES if rule in names)


def select_rules(rules: Iterable[str] | None = None, skip: Iterable[str] = (),
                 offline: bool = False) -> tuple[str, ...]:
    """The rules a check runs: `rules` (default: all) without `skip`, and
    without the online ones when `offline`. Raises ValueError when none is left."""
    wanted = set(rules or RULES) - set(skip)
    if offline:
        wanted -= set(ONLINE_RULES)
    chosen = tuple(rule for rule in RULES if rule in wanted)
    if not chosen:
        raise ValueError("no rule is left to check")
    return chosen


# -- the library ----------------------------------------------------------------------------------
@dataclass
class TrackFile:
    path: Path
    relative: str                 # to the Music folder, with "/"
    tags: dict[str, Any]
    service: str = ""             # "soundcloud", "spotify" or "" (unknown)
    track_id: str = ""            # the id in the file's tags
    found_id: str = ""            # an id recognised without it (index entry, Spotify link)
    page: str = ""                # the track's page, when known

    @property
    def id(self) -> str:
        return self.track_id or self.found_id

    @property
    def key(self) -> str:
        """"soundcloud:123" / "spotify:abc"; empty without an id."""
        return f"{self.service}:{self.id}" if self.service and self.id else ""

    @property
    def link(self) -> str:
        if not self.id:
            return ""
        if self.service == "spotify":
            return f"https://open.spotify.com/track/{self.id}"
        return self.page or f"https://api.soundcloud.com/tracks/{self.id}"

    @property
    def artist(self) -> str:
        return ", ".join(self.tags.get("artists") or [])

    @property
    def title(self) -> str:
        return str(self.tags.get("title") or "") or self.path.stem

    @property
    def name(self) -> str:
        return f"{self.artist} — {self.title}" if self.artist else self.title


@dataclass
class Problem:
    kind: str
    track: TrackFile
    fixable: bool
    detail: str = ""


@dataclass
class Report:
    music_dir: Path
    rules: tuple[str, ...]
    tracks: int = 0
    problems: list[Problem] = field(default_factory=list)
    # Tracks whose availability could not be checked this time.
    unchecked: int = 0
    fix: bool = False
    # With --fix: how many problems the fix solved, and how many there were.
    fixed: int | None = None
    before: int | None = None
    log_path: Path | None = None

    @property
    def count(self) -> int:
        return len(self.problems)

    @property
    def fixable(self) -> int:
        return sum(problem.fixable for problem in self.problems)

    def groups(self) -> list[tuple[Kind, list[Problem]]]:
        """The non-empty groups, in the order of KINDS; tracks by name."""
        result = []
        for kind in KINDS:
            items = [problem for problem in self.problems if problem.kind == kind.name]
            if items:
                items.sort(key=lambda problem: (problem.track.name.casefold(), problem.track.relative))
                result.append((kind, items))
        return result

    def fix_note(self) -> str:
        """The last line of the report: what --fix would fix, or did."""
        if self.fixed is not None:
            left = f"; {self.count} problem(s) left" if self.count else ""
            return f"--fix fixed {self.fixed} of {self.before} problem(s){left}"
        if not self.count:
            return "No problems found"
        if not self.fixable:
            return f"--fix cannot fix any of the {self.count} problem(s) automatically"
        return f"--fix can fix {self.fixable} of {self.count} problem(s)"

    def to_json(self, limit: int = REPORT_ITEMS_KEPT) -> dict[str, Any]:
        """The job result of the server (docs/reference/server.md)."""
        return {
            "rules": list(self.rules),
            "offline": not any(rule in self.rules for rule in ONLINE_RULES),
            "fix": self.fix,
            "tracks": self.tracks,
            "problems": self.count,
            "fixable": self.fixable,
            "fixed": self.fixed,
            "before": self.before,
            "unchecked": self.unchecked,
            "log": str(self.log_path) if self.log_path is not None else None,
            "groups": [
                {
                    "kind": kind.name, "rule": kind.rule, "label": kind.label,
                    "count": len(items), "fixable": sum(item.fixable for item in items),
                    "items": [_item_json(item) for item in items[:limit]],
                }
                for kind, items in self.groups()
            ],
        }

    def summary(self) -> dict[str, Any]:
        """The few numbers a job summary carries."""
        return {"tracks": self.tracks, "problems": self.count, "fixable": self.fixable,
                "fixed": self.fixed, "fix": self.fix}


def _item_json(problem: Problem) -> dict[str, Any]:
    track = problem.track
    return {
        "artist": track.artist, "title": track.title, "path": track.relative,
        "link": redact_url(track.link), "service": track.service,
        "fixable": problem.fixable, "detail": problem.detail,
    }


def library_files(music_dir: Path) -> list[Path]:
    """Every audio file of the library; hidden folders (partial downloads,
    logs) and hidden files are left out."""
    found: list[Path] = []
    for root, dirs, files in os.walk(music_dir):
        dirs[:] = sorted(name for name in dirs if not name.startswith("."))
        for name in sorted(files):
            if not name.startswith(".") and Path(name).suffix.lower() in AUDIO_EXTENSIONS:
                found.append(Path(root) / name)
    return found


# -- availability -----------------------------------------------------------------------------------
class RateLimited(Exception):
    """A platform refused further requests for now."""


def soundcloud_states(ids: list[str]) -> dict[str, str]:
    """State of up to 50 SoundCloud tracks in one request. A track the API
    leaves out is removed (or private); `policy: BLOCK` is blocked."""
    from .soundcloud_meta import SoundCloudApi

    api = SoundCloudApi()
    if api.disabled:
        raise OSError("yt-dlp is not installed")
    found = api.tracks(ids)
    states = {}
    for track_id in ids:
        data = found.get(track_id)
        if data is None:
            states[track_id] = STATE_REMOVED
        elif str(data.get("policy") or "").upper() == "BLOCK":
            states[track_id] = STATE_BLOCKED
        else:
            states[track_id] = STATE_OK
    return states


def spotify_state(track_id: str) -> str | None:
    """Whether a Spotify track still exists (its public oEmbed answer);
    None when Spotify did not say."""
    response = http_get("https://open.spotify.com/oembed",
                        params={"url": f"https://open.spotify.com/track/{track_id}"}, timeout=15)
    if response.status == 429:
        raise RateLimited("Spotify")
    if response.ok:
        return STATE_OK
    if response.status in (400, 404):
        return STATE_REMOVED
    return None


class Availability:
    """Whether tracks still exist on their platform, cached per id for
    AVAILABILITY_CACHE_SECONDS in the library's AVAILABILITY_FILENAME."""

    def __init__(self, path: Path,
                 soundcloud: Callable[[list[str]], dict[str, str]] = soundcloud_states,
                 spotify: Callable[[str], str | None] = spotify_state,
                 clock: Callable[[], float] = time.time):
        self.path = path
        self._soundcloud = soundcloud
        self._spotify = spotify
        self._clock = clock
        self._lock = threading.Lock()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        self._data: dict[str, dict[str, Any]] = {
            str(key): value for key, value in (raw.items() if isinstance(raw, dict) else [])
            if isinstance(value, dict) and value.get("state") in _KNOWN_STATES
        }

    def cached(self, key: str) -> str | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        try:
            fresh = self._clock() - float(entry.get("at", 0)) < AVAILABILITY_CACHE_SECONDS
        except (TypeError, ValueError):
            fresh = False
        return entry["state"] if fresh else None

    def _store(self, key: str, state: str) -> None:
        with self._lock:
            self._data[key] = {"state": state, "at": int(self._clock())}

    def check(self, tracks: list[TrackFile], dashboard: Any = None) -> tuple[dict[str, str], int]:
        """States by track key, and how many tracks could not be checked."""
        states: dict[str, str] = {}
        soundcloud: list[str] = []
        spotify: list[str] = []
        unchecked = 0
        for track in tracks:
            if not track.key or track.key in states:
                continue
            state = self.cached(track.key)
            if state is not None:
                states[track.key] = state
            elif track.service == "soundcloud" and track.id.isdigit() and redact_url(track.page) == track.page:
                soundcloud.append(track.id)
            elif track.service == "spotify":
                spotify.append(track.id)
            else:
                # A private SoundCloud link: the API does not list it by id.
                unchecked += 1
        soundcloud = list(dict.fromkeys(soundcloud))
        spotify = list(dict.fromkeys(spotify))
        total = len(soundcloud) + len(spotify)
        done = 0
        if total and dashboard is not None:
            dashboard.start_file(label=f"Checking {total} track(s) on their platforms...")
        try:
            for start in range(0, len(soundcloud), _SOUNDCLOUD_BATCH):
                batch = soundcloud[start:start + _SOUNDCLOUD_BATCH]
                try:
                    found = self._soundcloud(batch)
                except Exception as exc:
                    unchecked += len(soundcloud) - start
                    _note(dashboard, f"[Check] SoundCloud could not be asked whether tracks still exist: {exc}")
                    break
                for track_id, state in found.items():
                    if state in _KNOWN_STATES:
                        states[f"soundcloud:{track_id}"] = state
                        self._store(f"soundcloud:{track_id}", state)
                done += len(batch)
                _progress(dashboard, done, total)
            unchecked += self._check_spotify(spotify, states, dashboard, done, total)
        finally:
            self.save()
            if total and dashboard is not None:
                dashboard.finish_file()
        return states, unchecked

    def _check_spotify(self, ids: list[str], states: dict[str, str], dashboard: Any,
                       done: int, total: int) -> int:
        if not ids:
            return 0
        stop = threading.Event()
        unchecked = [0]
        lock = threading.Lock()

        def one(track_id: str) -> None:
            if stop.is_set():
                with lock:
                    unchecked[0] += 1
                return
            try:
                state = self._spotify(track_id)
            except RateLimited:
                stop.set()
                state = None
            except Exception:
                state = None
            with lock:
                if state in _KNOWN_STATES:
                    states[f"spotify:{track_id}"] = state
                    self._store(f"spotify:{track_id}", state)
                else:
                    unchecked[0] += 1

        pool = ThreadPoolExecutor(max_workers=_SPOTIFY_WORKERS, thread_name_prefix="check-spotify")
        aborted = False
        try:
            for index, future in enumerate([pool.submit(one, track_id) for track_id in ids], start=1):
                wait_future(future)
                if index % _PROGRESS_EVERY == 0:
                    _progress(dashboard, done + index, total)
        except BaseException:
            aborted = True
            stop.set()
            raise
        finally:
            pool.shutdown(wait=True, cancel_futures=aborted)
        if stop.is_set():
            _note(dashboard, "[Check] Spotify refused further requests; the rest is checked next time")
        return unchecked[0]

    def save(self) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        with self._lock:
            payload = dict(self._data)
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def _note(dashboard: Any, text: str) -> None:
    if dashboard is not None:
        dashboard.log(text)


def _progress(dashboard: Any, done: int, total: int) -> None:
    if dashboard is not None and total:
        dashboard.update_file(percent=done * 100 / total)


# -- scan ---------------------------------------------------------------------------------------------
@dataclass
class _Context:
    config: AppConfig
    rules: tuple[str, ...]
    registry: Any
    accounts: Any
    index: Any
    soundcloud_attempts: Any
    spotify_attempts: Any


def _identify(path: Path, tags: dict[str, Any], ctx: _Context, indexed: dict[Path, str]) -> TrackFile:
    config = ctx.config
    try:
        relative = path.relative_to(config.music_dir).as_posix()
    except ValueError:
        relative = str(path)
    track = TrackFile(path=path, relative=relative, tags=tags)
    if tags["soundcloud_id"]:
        track.service, track.track_id = "soundcloud", tags["soundcloud_id"]
    elif tags["spotify_id"]:
        track.service, track.track_id = "spotify", tags["spotify_id"]
    elif spotify_track_id(tags["url"]):
        track.service, track.found_id = "spotify", spotify_track_id(tags["url"])
    elif path in indexed:
        track.service, track.found_id = "soundcloud", indexed[path]
    if track.service == "soundcloud":
        page = ctx.index.webpage_url(track.id)
        if not page and "soundcloud.com/" in tags["url"]:
            page = tags["url"]
        track.page = page
    return track


def _problems(track: TrackFile, ctx: _Context) -> list[Problem]:
    rules, tags = ctx.rules, track.tags
    problems: list[Problem] = []
    has_id = bool(track.id)
    if "id" in rules and not track.track_id:
        how = "found by its index entry" if track.service == "soundcloud" else "found by its Spotify link"
        problems.append(Problem("no_id", track, has_id and tags["readable"],
                                how if has_id else "music-loader does not know this file"))
    if not tags["readable"]:
        return problems
    if "cover" in rules and not tags["has_cover"]:
        problems.append(Problem("no_cover", track, has_id))
    if "lyrics" in rules and _lyrics_missing(track, ctx):
        enabled = ctx.config.lyrics_enabled
        problems.append(Problem("no_lyrics", track, enabled and has_id and bool(tags["artists"]),
                                "" if enabled else "lyrics are switched off"))
    if not has_id:
        return problems
    if "artist" in rules:
        problems.extend(_artist_problems(track, ctx))
    if "format" in rules and int(tags.get("format") or 0) < FORMAT_VERSION:
        problems.append(Problem("old_format", track, True, f"format {int(tags.get('format') or 0)}"))
    return problems


def _lyrics_missing(track: TrackFile, ctx: _Context) -> bool:
    if track.tags["has_lyrics"] or track.path.with_suffix(".lrc").exists():
        return False
    title = str(track.tags.get("title") or "")
    if not title or looks_instrumental(title):
        return False
    attempts = ctx.soundcloud_attempts if track.service == "soundcloud" else ctx.spotify_attempts
    return not attempts.has(_lyrics_key(track))


def _lyrics_key(track: TrackFile) -> str:
    """The key of the lyrics attempts file (the pipelines use the same)."""
    if not track.id:
        return ""
    return track.id if track.service == "soundcloud" else track.link


def _artist_problems(track: TrackFile, ctx: _Context) -> list[Problem]:
    if artists_changed(track.tags, ctx.registry):
        return [Problem("artist_changed", track, True, "the artist registry spells a name differently")]
    if track.service != "soundcloud":
        return []
    entry = ctx.index.entry(track.id) or {}
    account_id = str(entry.get("uploader_id") or "")
    written = entry.get("account")
    if isinstance(written, str) and written != account_state(ctx.accounts, account_id):
        return [Problem("artist_changed", track, True, "the decision for the uploader's account changed")]
    original = ctx.index.original(track.id)
    if account_id and ctx.accounts.is_reuploader(account_id) and original is not None \
            and not original.get("artists"):
        return [Problem("artist_unknown", track, False, "a reupload whose original artist was not found")]
    return []


def scan(config: AppConfig, rules: tuple[str, ...], dashboard: Any = None,
         availability: Availability | None = None) -> Report:
    """Checks every file of the library against `rules`; changes nothing
    except the availability cache."""
    registry = get_registry(config.music_dir / ARTISTS_FILENAME)
    index = get_index(config.soundcloud_dir) if config.soundcloud_dir.is_dir() else _NoIndex()
    ctx = _Context(
        config=config, rules=rules, registry=registry, accounts=RunAccounts(registry), index=index,
        soundcloud_attempts=get_attempts(config.soundcloud_dir / LYRICS_ATTEMPTS_FILENAME),
        spotify_attempts=get_attempts(config.music_dir / SPOTIFY_LYRICS_ATTEMPTS_FILENAME),
    )
    report = Report(music_dir=config.music_dir, rules=rules)
    paths = library_files(config.music_dir)
    total = len(paths)
    indexed = index.ids_by_path()
    if dashboard is not None:
        dashboard.start_file(label=f"Checking the library: 0 of {total} file(s)")
    tracks: list[TrackFile] = []
    try:
        for number, path in enumerate(paths, start=1):
            tags = read_tags(path, registry.is_known)
            track = _identify(path, tags, ctx, indexed)
            tracks.append(track)
            report.problems.extend(_problems(track, ctx))
            if dashboard is not None and (number % _PROGRESS_EVERY == 0 or number == total):
                dashboard.update_file(percent=number * 100 / max(total, 1),
                                      label=f"Checking the library: {number} of {total} file(s)")
    finally:
        if dashboard is not None:
            dashboard.finish_file()
    report.tracks = total

    if "duplicates" in rules:
        by_key: dict[str, list[TrackFile]] = {}
        for track in tracks:
            if track.key:
                by_key.setdefault(track.key, []).append(track)
        for copies in by_key.values():
            if len(copies) > 1:
                for track in copies:
                    report.problems.append(Problem("duplicate", track, False, f"{len(copies)} files, id {track.id}"))

    if "available" in rules:
        availability = availability or Availability(config.music_dir / AVAILABILITY_FILENAME)
        states, report.unchecked = availability.check([track for track in tracks if track.key], dashboard)
        gone = set()
        for track in tracks:
            state = states.get(track.key)
            if state in (STATE_REMOVED, STATE_BLOCKED):
                gone.add(track.key)
                detail = "blocked on SoundCloud" if state == STATE_BLOCKED else "no longer on the platform"
                report.problems.append(Problem("removed", track, False, detail))
        # A track that is gone cannot be rewritten from its platform.
        for problem in report.problems:
            if problem.kind in REWRITE_KINDS and problem.track.key in gone:
                problem.fixable = False
                problem.detail = "removed from the platform"
    debuglog.event("check", "scanned", tracks=total, problems=report.count, fixable=report.fixable)
    return report


class _NoIndex:
    """A library without a SoundCloud folder."""

    def ids_by_path(self) -> dict[Path, str]:
        return {}

    def webpage_url(self, track_id: str) -> str:
        return ""

    def entry(self, track_id: str) -> None:
        return None

    def original(self, track_id: str) -> None:
        return None


# -- fix ------------------------------------------------------------------------------------------------
def fix(report: Report, config: AppConfig, dashboard: Any) -> None:
    """Solves the fixable problems of `report`: binds ids, rewrites the
    tracks that need it and searches the missing lyrics. Never deletes an
    audio file. Ctrl+C (KeyboardInterrupt) goes through the pipelines'
    cleanup."""
    fixable = [problem for problem in report.problems if problem.fixable]
    for problem in fixable:
        if problem.kind == "no_id":
            _bind(problem.track, dashboard)
    rewrite: dict[str, TrackFile] = {}
    for problem in fixable:
        if problem.kind in REWRITE_KINDS and problem.track.key:
            rewrite.setdefault(problem.track.key, problem.track)
    lyrics_only: dict[Path, TrackFile] = {}
    for problem in fixable:
        if problem.kind == "no_lyrics" and problem.track.key not in rewrite:
            lyrics_only.setdefault(problem.track.path, problem.track)
    service = None
    if config.lyrics_enabled and (rewrite or lyrics_only):
        service = LyricsService(config.lyrics_mode, settings=config.lyrics_settings)
    if rewrite:
        rewrite_tracks(list(rewrite.values()), config, dashboard, service)
    if lyrics_only and service is not None:
        search_lyrics(list(lyrics_only.values()), config, dashboard, service)


def _bind(track: TrackFile, dashboard: Any) -> None:
    if track.service == "spotify":
        ok = bind_ids(track.path, spotify_url=track.link)
    else:
        ok = bind_ids(track.path, soundcloud_id=track.id)
    if ok:
        track.track_id = track.id
        dashboard.log(f"[Check] Id written: {track.relative}")
    else:
        dashboard.log_error("Check", f"Could not write the id into {track.relative}")


def rewrite_tracks(tracks: list[TrackFile], config: AppConfig, dashboard: Any,
                   lyrics: LyricsService | None) -> None:
    """Rewrites the tracks through the download pipelines, as "Existing
    tracks: all" does, without deleting any audio file."""
    from .soundcloud import DownloadAborted, SoundCloudBatch
    from .spotify import SpotifyBatch

    config = dataclasses.replace(config, existing=EXISTING_ALL, keep_files=True)
    abort = threading.Event()
    spotify = list(dict.fromkeys(track.link for track in tracks if track.service == "spotify"))
    soundcloud = [track for track in tracks if track.service == "soundcloud"]
    dashboard.log(f"[Check] Rewriting {len(tracks)} track(s)")
    try:
        if spotify:
            batch = SpotifyBatch(config, dashboard, abort)
            plans = [batch.resolve_urls(spotify[start:start + SPOTIFY_FIX_BATCH])
                     for start in range(0, len(spotify), SPOTIFY_FIX_BATCH)]
            resolved = [plan for plan in plans if plan.resolved]
            dashboard.add_tracks_total("spotify", batch.track_count(resolved))
            if resolved:
                for ok in batch.download(resolved, lyrics):
                    dashboard.record("spotify", ok)
        if soundcloud:
            batch = SoundCloudBatch(config, dashboard, lyrics, abort)
            plan = batch.plan_tracks([
                {"id": track.id, "webpage_url": track.link, "title": track.tags.get("title") or None}
                for track in soundcloud
            ])
            dashboard.add_tracks_total("soundcloud", batch.track_count([plan]))
            if plan.resolved and plan.jobs:
                for ok in batch.download([plan]):
                    dashboard.record("soundcloud", ok)
    except DownloadAborted:
        raise KeyboardInterrupt


def search_lyrics(tracks: list[TrackFile], config: AppConfig, dashboard: Any, service: LyricsService) -> None:
    """The lyrics search of the pipelines for files that only lack lyrics."""
    soundcloud_attempts = get_attempts(config.soundcloud_dir / LYRICS_ATTEMPTS_FILENAME)
    spotify_attempts = get_attempts(config.music_dir / SPOTIFY_LYRICS_ATTEMPTS_FILENAME)
    dashboard.start_file(label=f"Lyrics for {len(tracks)} track(s)...")
    pool = ThreadPoolExecutor(max_workers=max(1, config.lyrics_workers), thread_name_prefix="check-lyrics")
    aborted = False
    try:
        futures = []
        for track in tracks:
            artists = list(track.tags.get("artists") or [])
            request = LyricsRequest(
                key=_lyrics_key(track), audio_path=track.path, main_artists=artists[:1],
                featured=artists[1:], title=str(track.tags.get("title") or ""),
                album=str(track.tags.get("album") or ""), duration=audio_duration(track.path),
            )
            attempts = soundcloud_attempts if track.service == "soundcloud" else spotify_attempts
            futures.append(pool.submit(service.process, request, attempts, dashboard))
        for future in futures:
            try:
                wait_future(future)
            except Exception as exc:
                dashboard.log_error("Lyrics", f"Lyrics worker failed: {exc}")
    except BaseException:
        aborted = True
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=aborted)
        dashboard.finish_file()


# -- report file -----------------------------------------------------------------------------------------
def write_log(report: Report, directory: Path) -> Path | None:
    """The full report, every track with its path and link:
    `.music-loader-logs/check-<time>.log`. None when it cannot be written."""
    now = datetime.now()
    path = directory / f"check-{now.strftime('%Y%m%d-%H%M%S')}.log"
    lines = [
        f"music-loader {__version__} library check, {now.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Library: {report.music_dir}",
        f"Rules: {', '.join(report.rules)}",
        f"Files: {report.tracks}",
    ]
    if report.unchecked:
        lines.append(f"Availability not checked this time: {report.unchecked} track(s)")
    for kind, items in report.groups():
        fixable = sum(item.fixable for item in items)
        lines += ["", f"{kind.label} - {len(items)} ({fixable} can be fixed with --fix)"]
        for item in items:
            parts = [item.track.name, item.track.relative]
            if item.track.link:
                parts.append(redact_url(item.track.link))
            if item.detail:
                parts.append(item.detail)
            parts.append("fixable" if item.fixable else "not fixable")
            lines.append("  " + " | ".join(part.replace("\n", " ") for part in parts))
    lines += ["", report.fix_note()]
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        return None
    return path


# -- one run ------------------------------------------------------------------------------------------------
def run(config: AppConfig, rules: tuple[str, ...], do_fix: bool, dashboard: Any,
        availability: Availability | None = None) -> Report:
    """A check, and with `do_fix` the fix and a second check whose report
    says how much was fixed. The report goes to the dashboard
    (`record_check`) and to the log file."""
    availability = availability or Availability(config.music_dir / AVAILABILITY_FILENAME)
    report = scan(config, rules, dashboard, availability)
    if do_fix:
        before = report.count
        if report.fixable:
            fix(report, config, dashboard)
            report = scan(config, rules, dashboard, availability)
        report.fix, report.before = True, before
        report.fixed = max(0, before - report.count)
    report.log_path = write_log(report, config.music_dir / LOGS_DIRNAME)
    dashboard.record_check(report.to_json())
    return report
