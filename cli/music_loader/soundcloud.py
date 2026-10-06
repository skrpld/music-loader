"""SoundCloud downloads: discovery, parallel downloads, conversion, tagging,
validation and lyrics.

Discovery uses flat listings (one cheap request per list). What a link
covers:

* a track - that track;
* a set - its tracks; an album/EP/single set also gives every track its
  album, album artist and track number;
* a profile - the artist's own albums (with album context) and own tracks;
  reposts and likes only with --soundcloud-reposts / --soundcloud-likes;
* a profile page link (.../likes, .../reposts, .../albums, .../sets,
  .../tracks) - exactly that page.

Sets inside a listing (an album in the likes, a playlist in the reposts) are
expanded; they used to be handed to the downloader as if they were one track,
which kept the first track of the set and silently dropped the rest.

Pipeline: `--soundcloud-download-workers` downloads run in parallel (yt-dlp
fetches the source audio only), a pool of post-processing workers converts to
MP3, tags and validates, and lyrics run in their own pool. The bounded hand-
over keeps at most a few unconverted downloads on disk.

Every new file is checked before it enters the library: a length that does
not match SoundCloud's (a 30-second Go+ preview, a cut-off download) fails the
track instead of being filed as complete. `--recheck` applies the current
rules to tracks that are already there: tags, folder, broken files, lyrics.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue
from typing import Any
from urllib.parse import urlsplit

from .artists import get_registry
from .config import (
    ARCHIVE_FILENAME,
    ARTISTS_FILENAME,
    COVER_MAX_BYTES,
    COVER_MAX_SIZE,
    DURATION_TOLERANCE_RATIO,
    DURATION_TOLERANCE_SECONDS,
    LYRICS_ATTEMPTS_FILENAME,
    RAW_EXTENSIONS,
    STAGING_DIRNAME,
    STALE_STAGING_SECONDS,
    SUBPROCESS_TIMEOUT_SECONDS,
    AppConfig,
)
from .links import Link, parse_link, redact_url
from .lyrics import LyricsRequest, LyricsService, get_attempts
from .net import http_get
from .paths import move_with_sidecars, prune_empty_dirs, remove_with_sidecars
from .playlist import update_soundcloud_playlist, write_named_playlist
from .process import run_captured, run_streamed, tool_command
from .soundcloud_index import SoundCloudArchive, get_index
from .soundcloud_meta import AlbumContext, SoundCloudApi, TrackMeta, build_meta, context_from_set
from .tags import audio_duration, read_tags, write_tags
from .text_utils import parse_soundcloud_title

# yt-dlp's default (--newline) progress line, e.g.:
# [download] 45.2% of ~3.45MiB at 1.23MiB/s ETA 00:02
_PROGRESS_RE = re.compile(
    r"\[download\]\s+(\d{1,3}(?:\.\d)?)%\s+of\s+~?\s*[\d.]+\w+\s+at\s+"
    r"([\d.]+\w+/s|Unknown speed)\s+ETA\s+(\S+)"
)
_ERROR_RE = re.compile(r"^ERROR:", re.IGNORECASE)
_RATE_LIMIT_RE = re.compile(r"\b429\b|too many requests|rate limit", re.IGNORECASE)
_NO_FORMAT_RE = re.compile(r"requested format is not available", re.IGNORECASE)
# SoundCloud track/playlist ids are numeric; anything else is never used to
# build a path (a crafted id like "../.." would otherwise escape the folder).
_ID_RE = re.compile(r"^[0-9A-Za-z_-]{1,64}$")

# Never the 30-second Go+ preview, never an "original download" that is an
# archive instead of audio.
_FORMAT = "bestaudio[format_id!*=preview][ext!=zip]/best[format_id!*=preview][ext!=zip]"
# SoundCloud allows roughly 600 API requests per 10 minutes; back off instead
# of failing the rest of the queue.
_RETRY_ARGS = [
    "--retries", "10", "--fragment-retries", "10", "--extractor-retries", "3",
    "--retry-sleep", "http:exp=1:30", "--retry-sleep", "fragment:exp=1:30",
    "--retry-sleep", "extractor:exp=2:60",
]
_MAX_RATE_LIMIT_RETRIES = 2


class DownloadAborted(Exception):
    pass


# -- data ------------------------------------------------------------------------------------
@dataclass
class TrackJob:
    info: dict[str, Any]
    album: AlbumContext | None = None
    lookup_album: bool = True

    @property
    def track_id(self) -> str:
        return str(self.info.get("id") or "")

    @property
    def url(self) -> str:
        return str(self.info.get("webpage_url") or self.info.get("url") or "")


@dataclass
class Discovery:
    jobs: list[TrackJob] = field(default_factory=list)
    playlist_title: str = ""


@dataclass
class _Context:
    """Everything the workers of one link share."""
    config: AppConfig
    soundcloud_dir: Path
    staging_dir: Path
    dashboard: Any
    lyrics: LyricsService | None
    abort: threading.Event
    yt: list[str]
    api: SoundCloudApi
    gate: "_RateGate"
    index: Any = None
    archive: Any = None
    registry: Any = None
    attempts: Any = None
    results: dict[str, Path] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    had_failure: bool = False

    def fail(self) -> None:
        with self.lock:
            self.had_failure = True


class _RateGate:
    """Shared pause after SoundCloud answered "429 Too Many Requests"."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._until = 0.0
        self._strikes = 0

    def wait(self, abort: threading.Event) -> None:
        while not abort.is_set():
            with self._lock:
                remaining = self._until - time.monotonic()
            if remaining <= 0:
                return
            abort.wait(min(remaining, 1.0))

    def trip(self) -> int:
        with self._lock:
            self._strikes += 1
            delay = min(30 * 2 ** (self._strikes - 1), 300)
            self._until = max(self._until, time.monotonic() + delay)
            return delay

    def relax(self) -> None:
        with self._lock:
            self._strikes = 0

    @property
    def tripped(self) -> bool:
        with self._lock:
            return self._strikes > 0


# -- discovery ----------------------------------------------------------------------------------
def _flat(url: str, ctx: _Context) -> dict[str, Any]:
    """One flat yt-dlp listing of `url`."""
    json_lines: list[str] = []
    other_lines: list[str] = []
    started = time.monotonic()

    def on_line(line: str) -> None:
        (json_lines if line.startswith("{") else other_lines).append(line)

    def on_idle(_idle: float) -> None:
        elapsed = int(time.monotonic() - started)
        ctx.dashboard.update_file(label=f"SoundCloud: resolving {redact_url(url)[:60]} ({elapsed}s)")

    ctx.gate.wait(ctx.abort)
    code = run_streamed(
        ctx.yt + ["--flat-playlist", "--dump-single-json", "--no-warnings", *_RETRY_ARGS, "--", url],
        on_line, on_idle=on_idle, timeout=SUBPROCESS_TIMEOUT_SECONDS, should_abort=ctx.abort.is_set,
    )
    if code == -2:
        raise DownloadAborted()
    if any(_RATE_LIMIT_RE.search(line) for line in other_lines):
        ctx.gate.trip()
    if code != 0 or not json_lines:
        detail = "\n".join(other_lines[-5:]) or f"yt-dlp exited with code {code}"
        raise RuntimeError(redact_url(detail.strip()))
    try:
        data = json.loads(json_lines[-1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not parse yt-dlp metadata: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected yt-dlp metadata")
    return data


def _entries(data: dict[str, Any]) -> list[dict[str, Any]]:
    entries = data.get("entries")
    if entries is None:
        return [data]
    result: list[dict[str, Any]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        if item.get("entries"):
            result.extend(_entries(item))
        else:
            result.append(item)
    return result


def _entry_link(entry: dict[str, Any]) -> Link | None:
    url = str(entry.get("webpage_url") or entry.get("url") or "")
    return parse_link(url)


def _track_job(entry: dict[str, Any], album: AlbumContext | None, lookup: bool) -> TrackJob | None:
    track_id = str(entry.get("id") or "")
    link = _entry_link(entry)
    if not _ID_RE.match(track_id) or link is None or link.service != "soundcloud":
        return None
    if link.kind not in {"track", "api", "short"}:
        return None
    info = dict(entry)
    info["id"] = track_id
    info["webpage_url"] = str(entry.get("webpage_url") or entry.get("url"))
    return TrackJob(info=info, album=album, lookup_album=lookup and album is None)


def _is_set_entry(entry: dict[str, Any]) -> bool:
    link = _entry_link(entry)
    if link is None or link.service != "soundcloud":
        return False
    return link.kind == "set" or "/playlists/" in urlsplit(link.url).path


def _expand_set(url: str, ctx: _Context) -> tuple[list[TrackJob], dict[str, Any]]:
    data = _flat(url, ctx)
    entries = [entry for entry in _entries(data) if not _is_set_entry(entry)]
    total = len(entries)
    jobs: list[TrackJob] = []
    for position, entry in enumerate(entries, start=1):
        album = context_from_set(data, position, total)
        job = _track_job(entry, album, lookup=album is None)
        if job is not None:
            jobs.append(job)
    return jobs, data


def _jobs_from_listing(data: dict[str, Any], ctx: _Context, lookup: bool) -> list[TrackJob]:
    """Tracks of a listing; sets in it are expanded."""
    jobs: list[TrackJob] = []
    for entry in _entries(data):
        if ctx.abort.is_set():
            raise DownloadAborted()
        if _is_set_entry(entry):
            link = _entry_link(entry)
            try:
                expanded, _ = _expand_set(link.url, ctx)
            except DownloadAborted:
                raise
            except Exception as exc:
                ctx.fail()
                ctx.dashboard.log_error("SoundCloud", f"Could not list set '{redact_url(link.url)}': {exc}")
                continue
            jobs.extend(expanded)
            continue
        job = _track_job(entry, None, lookup)
        if job is not None:
            jobs.append(job)
    return jobs


def _merge(groups: list[list[TrackJob]]) -> list[TrackJob]:
    """De-duplicates by id, keeping the first position but the best context
    (album information wins over none)."""
    merged: dict[str, TrackJob] = {}
    order: list[str] = []
    for group in groups:
        for job in group:
            existing = merged.get(job.track_id)
            if existing is None:
                merged[job.track_id] = job
                order.append(job.track_id)
            elif existing.album is None and job.album is not None:
                existing.album = job.album
                existing.lookup_album = False
    return [merged[track_id] for track_id in order]


def _playlist_name(data: dict[str, Any]) -> str:
    title = str(data.get("title") or data.get("playlist_title") or "").strip()
    uploader = str(data.get("uploader") or data.get("channel") or "").strip()
    if title and uploader and uploader.casefold() not in title.casefold():
        title = f"{uploader} - {title}"
    return title


def discover(link: Link, ctx: _Context) -> Discovery:
    config = ctx.config
    if link.kind == "profile":
        base = link.url.rstrip("/")
        groups: list[list[TrackJob]] = []
        sections = [("albums", False), ("tracks", False)]
        if config.soundcloud_reposts:
            sections.append(("reposts", True))
        if config.soundcloud_likes:
            sections.append(("likes", True))
        for section, lookup in sections:
            try:
                data = _flat(f"{base}/{section}", ctx)
            except DownloadAborted:
                raise
            except Exception as exc:
                if section in {"tracks"}:
                    raise
                ctx.fail()
                ctx.dashboard.log_error("SoundCloud", f"Could not list {section} of {base}: {exc}")
                continue
            groups.append(_jobs_from_listing(data, ctx, lookup))
        return Discovery(_merge(groups), "")

    if link.kind == "set":
        jobs, data = _expand_set(link.url, ctx)
        return Discovery(jobs, _playlist_name(data))

    data = _flat(link.url, ctx)
    if data.get("entries") is not None:
        lookup = link.section != "albums"
        if link.kind == "section" and link.section in {"albums", "sets"}:
            jobs = _jobs_from_listing(data, ctx, lookup)
        elif data.get("album_type") or link.kind in {"short", "api"}:
            # A short link that turned out to be a set.
            jobs = []
            entries = [entry for entry in _entries(data) if not _is_set_entry(entry)]
            for position, entry in enumerate(entries, start=1):
                album = context_from_set(data, position, len(entries))
                job = _track_job(entry, album, lookup=album is None)
                if job is not None:
                    jobs.append(job)
        else:
            jobs = _jobs_from_listing(data, ctx, lookup)
        return Discovery(_merge([jobs]), _playlist_name(data))

    job = _track_job(data, None, lookup=True)
    return Discovery([job] if job else [], "")


# -- download ----------------------------------------------------------------------------------
@dataclass
class _Download:
    ok: bool
    raw_path: Path | None = None
    info: dict[str, Any] = field(default_factory=dict)
    rate_limited: bool = False


def _merge_info(base: dict[str, Any], full: dict[str, Any] | None) -> dict[str, Any]:
    if not full:
        return base
    merged = dict(base)
    for key, value in full.items():
        if value is not None:
            merged[key] = value
    if not merged.get("webpage_url"):
        merged["webpage_url"] = base.get("webpage_url")
    merged["id"] = str(base.get("id") or merged.get("id") or "")
    return merged


def _fetch_full_info(url: str, ctx: _Context) -> dict[str, Any] | None:
    ctx.gate.wait(ctx.abort)
    code, stdout, stderr = run_captured(
        ctx.yt + ["--no-playlist", "--dump-single-json", "--skip-download", "--no-warnings",
                  *_RETRY_ARGS, "--", url],
        timeout=600,
    )
    if _RATE_LIMIT_RE.search(stderr or ""):
        ctx.gate.trip()
    if code != 0:
        return None
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _download_one(job: TrackJob, ctx: _Context, slot: int) -> _Download:
    track_id = job.track_id
    title = str(job.info.get("title") or track_id)
    # A partial file from an interrupted run must never be taken as finished:
    # with --no-part yt-dlp would report it as "already downloaded".
    for stale in ctx.staging_dir.glob(f"{track_id}.*"):
        if stale.is_file():
            stale.unlink(missing_ok=True)

    cmd = ctx.yt + [
        "--no-playlist", "--newline", "--progress", "--no-simulate", "--dump-json",
        "-f", _FORMAT, "--no-part", "--no-mtime", *_RETRY_ARGS,
        # The folder goes through -P: inside -o a "%" in the library path
        # would be read as a template field.
        "-P", f"home:{ctx.staging_dir}", "-P", f"temp:{ctx.staging_dir}",
        "-o", f"{track_id}.%(ext)s",
        "--", job.url,
    ]
    ctx.dashboard.start_file(label=f"SoundCloud: {title[:60]}", slot=slot)
    error_lines: list[str] = []
    full_info: dict[str, Any] | None = None

    def on_line(line: str) -> None:
        nonlocal full_info
        if full_info is None and line.startswith("{"):
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict) and parsed.get("id"):
                full_info = parsed
                label = str(parsed.get("title") or title)
                ctx.dashboard.update_file(label=f"SoundCloud: {label[:60]}", slot=slot)
                return
        match = _PROGRESS_RE.search(line)
        if match:
            percent, speed, eta = match.groups()
            ctx.dashboard.update_file(percent=float(percent), speed=speed, eta=f"ETA {eta}", slot=slot)
        if _ERROR_RE.match(line) or _RATE_LIMIT_RE.search(line):
            # yt-dlp prints ERROR lines it then recovers from; the exit code
            # and the resulting file decide success.
            error_lines.append(line)

    code = run_streamed(cmd, on_line, timeout=SUBPROCESS_TIMEOUT_SECONDS, should_abort=ctx.abort.is_set)

    candidates = sorted(
        path for path in ctx.staging_dir.glob(f"{track_id}.*")
        if path.is_file() and path.suffix.lower() not in {".part", ".tmp", ".ytdl"}
    )
    audio = [path for path in candidates if path.suffix.lower() in RAW_EXTENSIONS]
    merged = _merge_info(job.info, full_info)

    if code == -2:
        for path in candidates:
            path.unlink(missing_ok=True)
        raise DownloadAborted()

    if code != 0 or not audio:
        for path in candidates:
            path.unlink(missing_ok=True)
        rate_limited = any(_RATE_LIMIT_RE.search(line) for line in error_lines)
        if rate_limited:
            return _Download(False, None, merged, rate_limited=True)
        name = merged.get("title") or title
        if any(_NO_FORMAT_RE.search(line) for line in error_lines):
            ctx.dashboard.log_error(
                "SoundCloud",
                f"'{name}': only a 30-second preview is available (SoundCloud Go+), skipped",
            )
        else:
            for line in error_lines[-3:]:
                ctx.dashboard.log_error("SoundCloud", redact_url(line))
            if code == 0:
                ctx.dashboard.log_error(
                    "SoundCloud",
                    f"No usable audio file was produced for '{name}' "
                    f"(got: {', '.join(p.name for p in candidates) or 'nothing'})",
                )
        return _Download(False, None, merged)

    if full_info is None:
        # Rare: the metadata line was not printed. One extra request keeps
        # the tags correct instead of writing "Unknown Artist".
        merged = _merge_info(job.info, _fetch_full_info(job.url, ctx))
    for path in candidates:
        if path not in audio:
            path.unlink(missing_ok=True)
    return _Download(True, audio[0], merged)


# -- conversion / tagging -------------------------------------------------------------------------
_ALLOCATE_LOCK = threading.Lock()
# Names handed out to a worker but not yet written to disk, so two workers
# converting tracks with the same name at the same time cannot overwrite
# each other.
_ALLOCATED: set[Path] = set()


def _allocate_output(target: Path, track_id: str, current: Path | None = None) -> Path:
    def taken(path: Path) -> bool:
        if current is not None and path == current:
            return False
        return path in _ALLOCATED or path.exists()

    with _ALLOCATE_LOCK:
        candidate = target
        if taken(candidate):
            candidate = target.with_name(f"{target.stem} [{track_id}]{target.suffix}")
            n = 2
            while taken(candidate):
                candidate = target.with_name(f"{target.stem} [{track_id}] ({n}){target.suffix}")
                n += 1
        _ALLOCATED.add(candidate)
        return candidate


def _release_output(path: Path) -> None:
    with _ALLOCATE_LOCK:
        _ALLOCATED.discard(path)


def _cover_bytes(urls: list[str], work_dir: Path) -> bytes | None:
    for url in urls:
        try:
            response = http_get(url, timeout=30, max_bytes=COVER_MAX_BYTES)
        except OSError:
            continue
        if not response.ok or not response.body:
            continue
        source = work_dir / "cover.src"
        target = work_dir / "cover.jpg"
        try:
            work_dir.mkdir(parents=True, exist_ok=True)
            source.write_bytes(response.body)
            convert = subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(source), "-frames:v", "1",
                 "-vf", f"scale='min({COVER_MAX_SIZE},iw)':'min({COVER_MAX_SIZE},ih)'"
                        ":force_original_aspect_ratio=decrease",
                 "-q:v", "2", "-f", "mjpeg", str(target)],
                capture_output=True, stdin=subprocess.DEVNULL, timeout=120,
            )
            if convert.returncode == 0 and target.exists():
                return target.read_bytes()
        except (OSError, subprocess.SubprocessError):
            continue
        finally:
            for path in (source, target):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
    return None


def _duration_ok(path: Path, expected: Any) -> tuple[bool, float | None]:
    actual = audio_duration(path)
    try:
        expected = float(expected) if expected else None
    except (TypeError, ValueError):
        expected = None
    if actual is None:
        return False, None
    if expected is None:
        return True, actual
    tolerance = max(DURATION_TOLERANCE_SECONDS, expected * DURATION_TOLERANCE_RATIO)
    return abs(actual - expected) <= tolerance, actual


def _album_for(job: TrackJob, info: dict[str, Any], ctx: _Context) -> AlbumContext | None:
    if job.album is not None or not job.lookup_album or ctx.gate.tripped:
        return job.album
    return ctx.api.album_for(job.track_id, str(info.get("uploader") or ""))


def _target_path(meta: TrackMeta, ctx: _Context) -> Path:
    return ctx.soundcloud_dir / meta.folder / meta.filename


@dataclass
class _Processed:
    ok: bool
    path: Path | None = None
    existed: bool = False
    meta: TrackMeta | None = None


def _postprocess(job: TrackJob, raw_path: Path, info: dict[str, Any], ctx: _Context) -> _Processed:
    track_id = job.track_id
    existing = ctx.index.find(info)
    if existing is not None:
        # Recognized only after the download (a file from an older version
        # matched by its tags). Drop the redundant source; with --recheck the
        # old file is brought up to date instead.
        raw_path.unlink(missing_ok=True)
        if ctx.config.recheck:
            return _retag(job, existing, info, ctx)
        ctx.index.add(info, existing)
        ctx.archive.add(track_id)
        return _Processed(True, existing, True)

    meta = build_meta(info, _album_for(job, info, ctx), ctx.registry)
    output = _allocate_output(_target_path(meta, ctx), track_id)
    work_dir = ctx.staging_dir / f"{track_id}.work"
    tmp_output = ctx.staging_dir / f"{track_id}.converted.mp3"
    title = meta.title
    try:
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(raw_path),
            "-map", "0:a:0", "-vn", "-map_metadata", "-1",
            "-c:a", "libmp3lame", "-q:a", "0", "-write_xing", "1",
            "-id3v2_version", "3", "-f", "mp3", str(tmp_output),
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, stdin=subprocess.DEVNULL, text=True,
                encoding="utf-8", errors="replace", timeout=SUBPROCESS_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            ctx.dashboard.log_error("SoundCloud", f"Conversion failed for '{title}': {exc}")
            return _Processed(False)
        if result.returncode != 0 or not tmp_output.exists():
            message = (result.stderr or result.stdout or "ffmpeg failed").strip()
            ctx.dashboard.log_error("SoundCloud", f"Conversion failed for '{title}': {message[:500]}")
            return _Processed(False)

        ok, actual = _duration_ok(tmp_output, info.get("duration"))
        if not ok:
            ctx.dashboard.log_error(
                "SoundCloud",
                f"'{title}' is {actual or 0:.0f}s long but SoundCloud lists "
                f"{float(info.get('duration') or 0):.0f}s (preview or cut-off download), not saved",
            )
            return _Processed(False)

        cover = _cover_bytes(meta.artwork_urls, work_dir)
        write_tags(tmp_output, meta.tags, cover)
        output.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp_output, output)
    except OSError as exc:
        ctx.dashboard.log_error("SoundCloud", f"Could not save '{title}': {exc}")
        return _Processed(False)
    finally:
        raw_path.unlink(missing_ok=True)
        tmp_output.unlink(missing_ok=True)
        try:
            work_dir.rmdir()
        except OSError:
            pass
        _release_output(output)

    ctx.index.add(info, output)
    ctx.archive.add(track_id)
    return _Processed(True, output, False, meta)


def _retag(job: TrackJob, existing: Path, info: dict[str, Any], ctx: _Context) -> _Processed:
    """--recheck of a track that is already in the library: current tags,
    current folder/name, lyrics redone by the caller."""
    meta = build_meta(info, _album_for(job, info, ctx), ctx.registry)
    target = _target_path(meta, ctx)
    work_dir = ctx.staging_dir / f"{job.track_id}.work"
    output = _allocate_output(target, job.track_id, current=existing)
    try:
        cover = _cover_bytes(meta.artwork_urls, work_dir)
        write_tags(existing, meta.tags, cover)
        if output != existing:
            move_with_sidecars(existing, output)
            prune_empty_dirs(existing.parent, ctx.soundcloud_dir)
    except OSError as exc:
        ctx.dashboard.log_error("SoundCloud", f"Recheck failed for '{meta.title}': {exc}")
        return _Processed(False)
    finally:
        try:
            work_dir.rmdir()
        except OSError:
            pass
        _release_output(output)
    ctx.index.add(info, output)
    ctx.archive.add(job.track_id)
    return _Processed(True, output, True, meta)


# -- lyrics -------------------------------------------------------------------------------------
def _lyrics_request(path: Path, track_id: str, meta: TrackMeta | None, force: bool) -> LyricsRequest:
    if meta is not None:
        main, featured, title, album = meta.main_artists, meta.featured, meta.title, meta.album
    else:
        tags = read_tags(path)
        if tags["soundcloud_id"]:
            artists = tags["artists"]
            main, featured, title, album = artists[:1], artists[1:], tags["title"], tags["album"]
        else:
            # Written by an older version: artist = uploader, raw title.
            uploader = "/".join(tags["artists"])
            parsed = parse_soundcloud_title(tags["title"], uploader)
            main, featured, title, album = parsed.artists, parsed.featured, parsed.title, ""
    return LyricsRequest(
        key=track_id, audio_path=path, main_artists=main, featured=featured,
        title=title, album=album, duration=audio_duration(path), force=force,
    )


# -- pipeline ------------------------------------------------------------------------------------
def _cleanup_stale_staging(staging_dir: Path) -> None:
    """Removes leftovers from runs that were interrupted long ago."""
    cutoff = time.time() - STALE_STAGING_SECONDS
    try:
        entries = list(staging_dir.iterdir())
    except OSError:
        return
    for entry in entries:
        try:
            if entry.is_file():
                if entry.stat().st_mtime < cutoff:
                    entry.unlink(missing_ok=True)
            elif entry.is_dir():
                for child in entry.iterdir():
                    if child.is_file() and child.stat().st_mtime < cutoff:
                        child.unlink(missing_ok=True)
                entry.rmdir()
        except OSError:
            continue


class _Slots:
    """Progress-row numbers for the parallel downloads."""

    def __init__(self, count: int):
        self._free: Queue[int] = Queue()
        for slot in range(count):
            self._free.put(slot)

    def acquire(self) -> int:
        return self._free.get()

    def release(self, slot: int) -> None:
        self._free.put(slot)


def download_soundcloud(
    link: Link,
    config: AppConfig,
    dashboard,
    lyrics: LyricsService | None = None,
) -> bool:
    """Downloads one SoundCloud link. Returns False when anything failed."""
    url = link.url
    dashboard.log(f"[SoundCloud] Starting: {redact_url(url)}")
    dashboard.start_file(label="SoundCloud: resolving tracks...")

    yt = tool_command("yt-dlp")
    if yt is None:
        dashboard.log_error("SoundCloud", "yt-dlp is not installed")
        dashboard.finish_file()
        return False

    soundcloud_dir = config.soundcloud_dir
    soundcloud_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = soundcloud_dir / STAGING_DIRNAME
    staging_dir.mkdir(exist_ok=True)
    _cleanup_stale_staging(staging_dir)

    ctx = _Context(
        config=config,
        soundcloud_dir=soundcloud_dir,
        staging_dir=staging_dir,
        dashboard=dashboard,
        lyrics=lyrics if config.lyrics_enabled else None,
        abort=threading.Event(),
        yt=yt,
        api=_shared_api(),
        gate=_SHARED_GATE,
        index=get_index(soundcloud_dir),
        archive=SoundCloudArchive(soundcloud_dir / ARCHIVE_FILENAME),
        registry=get_registry(config.music_dir / ARTISTS_FILENAME),
        attempts=get_attempts(soundcloud_dir / LYRICS_ATTEMPTS_FILENAME),
    )

    try:
        discovery = discover(link, ctx)
    except DownloadAborted:
        raise KeyboardInterrupt
    except Exception as exc:
        dashboard.log_error("SoundCloud", f"Could not resolve '{redact_url(url)}': {exc}")
        dashboard.finish_file()
        return False

    jobs = discovery.jobs
    dashboard.add_tracks_total("soundcloud", len(jobs))
    dashboard.log(f"[SoundCloud] Found {len(jobs)} track(s)")
    dashboard.finish_file()
    if not jobs:
        update_soundcloud_playlist(soundcloud_dir, dashboard)
        return not ctx.had_failure

    _run_pipeline(jobs, ctx)

    try:
        staging_dir.rmdir()
    except OSError:
        pass
    update_soundcloud_playlist(soundcloud_dir, dashboard)
    if discovery.playlist_title:
        ordered = [ctx.results.get(job.track_id) for job in jobs]
        write_named_playlist(
            soundcloud_dir, discovery.playlist_title,
            [path for path in ordered if path is not None], dashboard,
        )
    return not ctx.had_failure


def _run_pipeline(jobs: list[TrackJob], ctx: _Context) -> None:
    config = ctx.config
    dashboard = ctx.dashboard
    download_workers = max(1, int(config.soundcloud_download_workers))
    pp_workers = max(1, int(config.soundcloud_postprocess_workers))
    lyrics_workers = max(1, int(config.lyrics_workers))
    slots = _Slots(download_workers)
    # Bounded hand-over between downloads and conversion: at most this many
    # unconverted source files wait on disk.
    pp_capacity = threading.BoundedSemaphore(pp_workers * 2)
    pp_futures: list[Future] = []
    lyrics_futures: list[Future] = []

    pp_pool = ThreadPoolExecutor(max_workers=pp_workers, thread_name_prefix="sc-pp")
    lyrics_pool = ThreadPoolExecutor(max_workers=lyrics_workers, thread_name_prefix="sc-lyrics")
    dl_pool = ThreadPoolExecutor(max_workers=download_workers, thread_name_prefix="sc-dl")

    def submit_lyrics(path: Path, track_id: str, meta: TrackMeta | None, force: bool) -> None:
        if ctx.lyrics is None or ctx.abort.is_set():
            return

        def task() -> None:
            request = _lyrics_request(path, track_id, meta, force)
            ctx.lyrics.process(request, ctx.attempts, dashboard, ctx.abort)

        with ctx.lock:
            lyrics_futures.append(lyrics_pool.submit(task))

    def finished(job: TrackJob, result: _Processed, recheck: bool) -> None:
        if not result.ok or result.path is None:
            ctx.fail()
            dashboard.record_track("soundcloud", "failed")
            return
        with ctx.lock:
            ctx.results[job.track_id] = result.path
        if result.existed and not recheck:
            dashboard.record_track("soundcloud", "skipped")
            dashboard.log(f"[SoundCloud] Already exists: {result.path.name}")
        elif result.existed:
            dashboard.record_track("soundcloud", "skipped")
            dashboard.log(f"[SoundCloud] Rechecked: {result.path.relative_to(ctx.soundcloud_dir)}")
        else:
            dashboard.record_track("soundcloud", "done")
            dashboard.log(f"[SoundCloud] Ready: {result.path.relative_to(ctx.soundcloud_dir)}")
        submit_lyrics(result.path, job.track_id, result.meta, force=config.recheck)

    def pp_task(job: TrackJob, kind: str, path: Path, info: dict[str, Any]) -> None:
        try:
            if kind == "retag":
                result = _retag(job, path, info, ctx)
            else:
                result = _postprocess(job, path, info, ctx)
            finished(job, result, recheck=kind == "retag")
        except Exception as exc:
            ctx.fail()
            dashboard.record_track("soundcloud", "failed")
            dashboard.log_error("SoundCloud", f"Worker failed for '{info.get('title') or job.track_id}': {exc}")
            if kind != "retag":
                path.unlink(missing_ok=True)
        finally:
            pp_capacity.release()

    def hand_over(job: TrackJob, kind: str, path: Path, info: dict[str, Any]) -> None:
        while not pp_capacity.acquire(timeout=0.5):
            if ctx.abort.is_set():
                if kind != "retag":
                    path.unlink(missing_ok=True)
                raise DownloadAborted()
        with ctx.lock:
            pp_futures.append(pp_pool.submit(pp_task, job, kind, path, info))

    def track_task(job: TrackJob) -> None:
        if ctx.abort.is_set():
            return
        slot = slots.acquire()
        try:
            _track(job, slot)
        except DownloadAborted:
            return
        except Exception as exc:
            ctx.fail()
            dashboard.record_track("soundcloud", "failed")
            dashboard.log_error("SoundCloud", f"Download failed for '{job.info.get('title') or job.track_id}': {exc}")
        finally:
            dashboard.finish_file(slot)
            slots.release(slot)

    def _track(job: TrackJob, slot: int) -> None:
        info: dict[str, Any] | None = None
        existing = ctx.index.find(job.info)
        if existing is None and (config.recheck or ctx.index.has_unindexed_legacy_files()):
            # Files of older versions are recognized by their title/uploader/
            # length, which a flat listing does not have: fetch the track's
            # metadata first instead of downloading it a second time.
            dashboard.start_file(label=f"SoundCloud: checking {job.track_id}", slot=slot)
            full = _fetch_full_info(job.url, ctx)
            if full is not None:
                info = _merge_info(job.info, full)
                existing = ctx.index.find(info)
        if existing is not None and not config.recheck:
            if ctx.index.get(job.track_id) is None:
                # Promote a match found by tags into the exact id mapping.
                ctx.index.add(job.info, existing)
            ctx.archive.add(job.track_id)
            finished(job, _Processed(True, existing, True), recheck=False)
            return

        if existing is not None:
            dashboard.start_file(label=f"SoundCloud: rechecking {existing.name[:50]}", slot=slot)
            if info is None:
                full = _fetch_full_info(job.url, ctx)
                if full is None:
                    ctx.fail()
                    dashboard.record_track("soundcloud", "failed")
                    dashboard.log_error("SoundCloud", f"Recheck: could not fetch metadata for '{existing.name}'")
                    return
                info = _merge_info(job.info, full)
            ok, actual = _duration_ok(existing, info.get("duration"))
            if ok:
                hand_over(job, "retag", existing, info)
                return
            dashboard.log(
                f"[SoundCloud] Recheck: '{existing.name}' is {actual or 0:.0f}s instead of "
                f"{float(info.get('duration') or 0):.0f}s, downloading again"
            )
            remove_with_sidecars(existing)
            prune_empty_dirs(existing.parent, ctx.soundcloud_dir)
            ctx.index.remove(job.track_id)

        attempt = 0
        while True:
            ctx.gate.wait(ctx.abort)
            if ctx.abort.is_set():
                raise DownloadAborted()
            download = _download_one(job, ctx, slot)
            if download.ok or not download.rate_limited or attempt >= _MAX_RATE_LIMIT_RETRIES:
                break
            attempt += 1
            delay = ctx.gate.trip()
            dashboard.log(f"[SoundCloud] Rate limited by SoundCloud, pausing downloads for {delay}s")
        if download.rate_limited and not download.ok:
            dashboard.log_error(
                "SoundCloud",
                f"'{download.info.get('title') or job.track_id}': SoundCloud rate limit (HTTP 429), "
                f"try again later",
            )
        if not download.ok or download.raw_path is None:
            ctx.fail()
            dashboard.record_track("soundcloud", "failed")
            return
        ctx.gate.relax()
        hand_over(job, "new", download.raw_path, download.info)

    aborted = False
    try:
        dl_futures = [dl_pool.submit(track_task, job) for job in jobs]
        for future in dl_futures:
            future.result()
        # Conversion jobs may still be queued after the last download.
        while True:
            with ctx.lock:
                pending = [future for future in pp_futures if not future.done()]
            if not pending:
                break
            for future in pending:
                future.result()
        while True:
            with ctx.lock:
                pending = [future for future in lyrics_futures if not future.done()]
            if not pending:
                break
            for future in pending:
                try:
                    future.result()
                except Exception as exc:
                    dashboard.log_error("Lyrics", f"Lyrics worker failed: {exc}")
    except BaseException:
        # Ctrl+C or an unexpected error: stop children, drop queued work.
        aborted = True
        ctx.abort.set()
        ctx.fail()
        raise
    finally:
        for pool in (dl_pool, pp_pool, lyrics_pool):
            pool.shutdown(wait=True, cancel_futures=aborted)
        ctx.index.flush()
        ctx.registry.flush()
        dashboard.finish_file(None)


_SHARED_GATE = _RateGate()
_API: SoundCloudApi | None = None
_API_LOCK = threading.Lock()


def _shared_api() -> SoundCloudApi:
    global _API
    with _API_LOCK:
        if _API is None:
            _API = SoundCloudApi()
        return _API
