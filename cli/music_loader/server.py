"""Server mode: `music-loader serve -o /path/to/Music`.

Runs music-loader on the machine that holds the library and lets the Android
app (or any HTTP client) queue links and follow the progress from another
device. Jobs run one after another; each one runs in its own worker process
(worker.py), so cancelling a job goes through the same cleanup as Ctrl+C in
the command-line tool.

Every request needs `Authorization: Bearer <token>`. The token comes from
--token, the MUSIC_LOADER_TOKEN environment variable, or a file created on
the first start (printed at startup).

    GET    /api/v1/info               server version, library folder, queue state
    GET    /api/v1/jobs               all jobs, newest first (summaries)
    POST   /api/v1/jobs               queue links: {"links": [...], "options": {...}}
    GET    /api/v1/jobs/<id>          one job with its log, errors and active downloads
    POST   /api/v1/jobs/<id>/cancel   cancel a queued or running job, or the pending automatic retry
                                      of a finished one
    POST   /api/v1/jobs/<id>/retry    queue a finished job again: {"scope": "all" | "failed"}
    GET    /api/v1/jobs/<id>/debug-log  the job's debug log (JSON lines), when it was queued with
                                      "debug_log": true
    DELETE /api/v1/jobs/<id>          remove a finished job from the list
    GET    /api/v1/events             Server-Sent Events: a "state" event on every change
    GET    /api/v1/artists            SoundCloud accounts and their artist names (feature "artists")
    POST   /api/v1/artists/<id>/<action>  change one: action "set" ({"name": "..."}), "keep",
                                      "reuploader", "accept", "reject" or "unlock"

Job options: {"lyrics": "strict" | "loose" | "off", "existing": "keep" | "outdated" | "all",
"soundcloud_reposts": false, "soundcloud_likes": false, "soundcloud_fallback": false,
"auto_retry": false, "debug_log": false, "soundcloud_feat": "both" | "artist" | "title",
"soundcloud_keep_producers": false, "soundcloud_keep_versions": true,
"soundcloud_strip_promo": true, "soundcloud_filename": "{track} - {title}",
"lyrics_providers": ["lrclib", "musixmatch", "genius"], "lyrics_plain": true,
"lyrics_length_tolerance": 10, "lyrics_original_for_versions": false,
"lyrics_retry_days": 7}.

Existing tracks (the "existing" option, feature "existing_tracks"): the same
setting as the command line's --existing. "keep" (default) skips a track
that is already in the library and only binds its id to the file,
"outdated" rewrites it when it is outdated (an older format, a changed
artist), "all" always rewrites it. The older option "recheck": true is
"existing": "all", and a job lists both ("recheck" is true for "all"), so
older clients keep working; "recheck": true with another "existing" is
refused with 400. With "keep" the stats count the outdated tracks the run
kept (`spotify_tracks_outdated`, `soundcloud_tracks_outdated`).

Lyrics settings (the "lyrics_providers" ... "lyrics_retry_days" options,
feature "lyrics_settings"): the same settings as the command line's
--lyrics-providers, --lyrics-synced-only, --lyrics-length-tolerance,
--lyrics-original-for-versions and --lyrics-retry-days. A missing option
means the default; a wrong value is refused with 400 and the reason.

SoundCloud formatting (the "soundcloud_feat" ... "soundcloud_filename"
options, feature "soundcloud_format"): the same settings as the command
line's --soundcloud-feat, --soundcloud-keep-producers,
--soundcloud-drop-versions, --soundcloud-keep-promo and
--soundcloud-filename. A missing option means the default; a bad file name
template is refused with 400 and the reason.

Debug log: with `debug_log` the job writes a detailed log of the run (see
debuglog.py) to the library's log folder; the job shows its path as
`debug_log` and `/debug-log` sends the file, so a client on another device
can save or share it.

Retrying: a track (or link) that failed is recorded with a category (see
availability.py). Every failure is retryable, whatever its category
(`failed`, `rate_limited`, `network`); an unavailable track (DRM, preview,
blocked, removed) is never listed as failed at all, so it is never retried.
`/retry` creates a new job with the same options: scope "all" queues the
original links again, scope "failed" only the failures. Tracks
already in the library are skipped either way, so a retry is cheap. With the
job option `auto_retry` a job that ends with failures is queued again
by the server after 15, then 30, then 60 minutes (three attempts at most; the
retry is queued once the running job is done, so the time is a lower bound).
The job shows `retry_at` while it waits and `/cancel` stops the wait.

History: the jobs are kept in a JSON file in the server's state folder
(`server-jobs.json` next to the stored token, or `--state-dir`; the app's own
storage on Android), written whenever a job is queued, starts, ends, is
cancelled or deleted. After a restart the finished jobs are back with their
log, failures and options, so their retry actions keep working, and a pending
automatic retry keeps its time (an overdue one is queued right away). A job
that was queued or running when the process died is never run again by
itself: it comes back as cancelled or failed with an "Interrupted" message. A
file that cannot be read is ignored.

Artists (feature "artists"): the decision per SoundCloud account (see
accounts.py), the same list and actions as `music-loader artists`. A change
is written to the library's `.music-loader-artists.json` at once and applies
from the next job (the running one keeps what it decided); `existing:
"outdated"` applies it to files already downloaded. A finished job lists its decisions in
`artists`: {"alias": [...], "suggestion": [...], "undetermined": [...]}.

`GET /api/v1/info` (and every `state` event) lists the server's `features`
("retry", "auto_retry", "debug_log"); a client talking to an older server
finds the list (or an entry) missing and leaves those actions out.

The server speaks plain HTTP: use it in a trusted network or a VPN
(Tailscale, WireGuard), or put an HTTPS reverse proxy in front of it.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import queue
import re
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from rich.console import Console
from rich.markup import escape

from . import __version__
from .cli import _positive_int
from .accounts import ACTIONS as _ARTIST_ACTIONS
from .accounts import AccountBook, AccountError, apply_action, sorted_accounts
from .artists import ArtistRegistry
from .availability import FailureCategory
from .config import (
    ARTISTS_FILENAME,
    EXISTING_ALL,
    EXISTING_KEEP,
    EXISTING_MODES,
    FEAT_MODES,
    LOGS_DIRNAME,
    LYRICS_MODE_LOOSE,
    LYRICS_MODE_STRICT,
    LyricsSettings,
    SoundCloudFormat,
    parse_lyrics_providers,
    validate_filename_template,
    validate_length_tolerance,
    validate_retry_days,
)
from .deps import check_dependencies
from .links import Link, parse_link, redact_url
from .ui import ARTIST_NOTE_KINDS, PHASE_DOWNLOADING, PHASE_RESOLVING, Stats

API_VERSION = 1
# Optional capabilities, for clients that may talk to an older server.
FEATURES = ("retry", "auto_retry", "debug_log", "soundcloud_format", "lyrics_settings", "artists",
            "existing_tracks")
DEFAULT_PORT = 8765
TOKEN_ENV = "MUSIC_LOADER_TOKEN"
TOKEN_FILENAME = "server-token"
JOBS_FILENAME = "server-jobs.json"
_JOBS_FILE_VERSION = 1

_TOKEN_RE = re.compile(r"[A-Za-z0-9._~+/=-]{16,512}")
_JOB_ID_RE = re.compile(r"[0-9a-f]{1,64}")
_ACCOUNT_ID_RE = re.compile(r"[0-9]{1,20}")
_LYRICS_MODES = (LYRICS_MODE_STRICT, LYRICS_MODE_LOOSE, "off")
_FINISHED = {"completed", "cancelled", "failed"}
_BOOL_OPTIONS = ("recheck", "soundcloud_reposts", "soundcloud_likes", "soundcloud_fallback", "auto_retry",
                 "debug_log")
_INTERRUPTED_QUEUED = "Interrupted: the server stopped before the job started"
_INTERRUPTED_RUNNING = "Interrupted: the server stopped while the job was running"
_RETRY_SCOPES = ("all", "failed")

_MAX_BODY_BYTES = 256 * 1024
_MAX_LINKS_PER_JOB = 2000
_MAX_REJECTED_KEPT = 100
_MAX_QUEUED_JOBS = 100
_MAX_FINISHED_JOBS = 50
_MAX_TEXT = 2000
_PREVIEW_LINKS = 3
# A job's full detail keeps this much; the event stream sends less, it is
# pushed up to twice a second.
_LOG_KEPT = 300
_ERRORS_KEPT = 500
# Failed tracks of one job that can be retried; the most a job can be queued with.
_FAILED_KEPT = _MAX_LINKS_PER_JOB
# Pause before the 1st, 2nd and 3rd automatic retry (seconds).
_AUTO_RETRY_DELAYS = (15 * 60, 30 * 60, 60 * 60)
# The runner re-reads the clock at least this often while a retry is pending.
_RETRY_POLL_SECONDS = 30.0
# A due retry that finds the queue full is tried again after this long.
_QUEUE_FULL_RECHECK_SECONDS = 60
_ARTIST_NOTES_KEPT = 200
_STREAM_LOG = 50
_STREAM_ERRORS = 20
_STREAM_INTERVAL = 0.5
_STREAM_KEEPALIVE = 15.0
# How long a cancelled worker may clean up before it is killed.
_CANCEL_GRACE_SECONDS = 60
_SOCKET_TIMEOUT = 120


def _now_ms() -> int:
    return int(time.time() * 1000)


def _text(value: Any) -> str:
    return value[:_MAX_TEXT] if isinstance(value, str) else ""


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _queue_state(data: dict[str, Any]) -> dict[str, Any]:
    """Link progress of a job; an unknown phase (an older worker or file) is
    the download phase, which is all older versions had."""
    phase = data.get("phase")
    return {"completed": _int(data.get("completed")), "total": _int(data.get("total")),
            "phase": phase if phase in (PHASE_RESOLVING, PHASE_DOWNLOADING) else PHASE_DOWNLOADING}


def _link_json(link: Link) -> dict[str, str]:
    # A private link's token stays on the server; the worker needs it, the
    # app does not.
    return {"url": redact_url(link.url), "service": link.service, "kind": link.kind}


@dataclass
class Job:
    id: str
    links: list[Link]
    options: dict[str, Any]
    rejected: list[str]
    created_at: int = field(default_factory=_now_ms)
    status: str = "queued"
    started_at: Optional[int] = None
    finished_at: Optional[int] = None
    message: Optional[str] = None
    # "phase": "resolving" while the links are resolved (completed = resolved
    # links), then "downloading" (completed = finished links).
    queue: dict[str, Any] = field(default_factory=lambda: {"completed": 0, "total": 0})
    stats: dict[str, int] = field(default_factory=lambda: asdict(Stats()))
    files: dict[int, dict[str, Any]] = field(default_factory=dict)
    log: deque = field(default_factory=lambda: deque(maxlen=_LOG_KEPT))
    errors: deque = field(default_factory=lambda: deque(maxlen=_ERRORS_KEPT))
    error_count: int = 0
    log_seq: int = 0
    runlog: Optional[str] = None
    unavailable_log: Optional[str] = None
    debug_log: Optional[str] = None
    cancel_requested: bool = False
    # Failed tracks (or whole links) by their link, with the reason; the full
    # link is kept for the retry, clients only get it redacted.
    failed: dict[str, dict[str, str]] = field(default_factory=dict)
    # 0 for a job a person queued, n for the nth automatic retry of it.
    attempt: int = 0
    retry_of: Optional[str] = None
    # When the server queues the failed tracks again (epoch ms), if it waits for that.
    retry_at: Optional[int] = None
    # Artist decisions of the run (ui.ARTIST_NOTE_KINDS -> texts).
    artists: dict[str, list[str]] = field(default_factory=lambda: {kind: [] for kind in ARTIST_NOTE_KINDS})

    @property
    def finished(self) -> bool:
        return self.status in _FINISHED

    def retryable(self) -> list[dict[str, str]]:
        return [item for item in self.failed.values() if FailureCategory(item["category"]).retryable]

    def summary(self) -> dict[str, Any]:
        services: dict[str, int] = {}
        for link in self.links:
            services[link.service] = services.get(link.service, 0) + 1
        return {
            "id": self.id,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "message": self.message,
            "link_count": len(self.links),
            "services": services,
            "links": [_link_json(link) for link in self.links[:_PREVIEW_LINKS]],
            "options": dict(self.options),
            "queue": dict(self.queue),
            "stats": dict(self.stats),
            "error_count": self.error_count,
            "cancel_requested": self.cancel_requested,
            "failed_counts": self._failed_counts(),
            "retryable_count": len(self.retryable()),
            "attempt": self.attempt,
            "max_attempts": len(_AUTO_RETRY_DELAYS),
            "retry_of": self.retry_of,
            "retry_at": self.retry_at,
        }

    def _failed_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.failed.values():
            counts[item["category"]] = counts.get(item["category"], 0) + 1
        return counts

    def detail(self, log_limit: int = _LOG_KEPT, error_limit: int = _ERRORS_KEPT) -> dict[str, Any]:
        data = self.summary()
        data.update(
            links=[_link_json(link) for link in self.links],
            rejected=list(self.rejected),
            files=[{"slot": slot, **row} for slot, row in sorted(self.files.items())],
            log=list(self.log)[-log_limit:],
            errors=list(self.errors)[-error_limit:],
            runlog=self.runlog,
            unavailable_log=self.unavailable_log,
            debug_log=self.debug_log,
            artists={kind: list(items) for kind, items in self.artists.items()},
            failed_items=[
                {"url": redact_url(item["url"]), "service": item["service"], "title": item["title"],
                 "category": item["category"],
                 "retryable": FailureCategory(item["category"]).retryable}
                for item in self.failed.values()
            ],
        )
        return data

    def state(self) -> dict[str, Any]:
        """Everything a restarted server needs, full links included (the file
        stays on the server, like the job itself)."""
        return {
            "id": self.id,
            "links": [link.url for link in self.links],
            "options": dict(self.options),
            "rejected": list(self.rejected),
            "created_at": self.created_at,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "message": self.message,
            "queue": dict(self.queue),
            "stats": dict(self.stats),
            "log": list(self.log),
            "errors": list(self.errors),
            "error_count": self.error_count,
            "log_seq": self.log_seq,
            "runlog": self.runlog,
            "unavailable_log": self.unavailable_log,
            "debug_log": self.debug_log,
            "failed": list(self.failed.values()),
            "attempt": self.attempt,
            "retry_of": self.retry_of,
            "retry_at": self.retry_at,
            "artists": {kind: list(items) for kind, items in self.artists.items()},
        }


def _opt_int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _opt_text(value: Any) -> Optional[str]:
    return _text(value) or None


def _log_entries(raw: Any) -> list[dict[str, Any]]:
    entries = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict):
            entries.append({
                "seq": _int(item.get("seq")),
                "time": _int(item.get("time")),
                "level": "error" if item.get("level") == "error" else "info",
                "source": _opt_text(item.get("source")),
                "text": _text(item.get("text")),
            })
    return entries


def _job_from_state(data: Any, saved_at: Optional[int]) -> Optional[Job]:
    """Rebuilds a job from the jobs file; None when the entry is unusable.
    A job that was queued or running comes back finished, marked interrupted."""
    if not isinstance(data, dict):
        return None
    job_id = data.get("id")
    if not isinstance(job_id, str) or not _JOB_ID_RE.fullmatch(job_id):
        return None
    raw_links = data.get("links")
    links = [link for link in (parse_link(url) for url in raw_links if isinstance(url, str))
             if link is not None] if isinstance(raw_links, list) else []
    created_at = _opt_int(data.get("created_at"))
    status = data.get("status")
    if not links or created_at is None or status not in (*_FINISHED, "queued", "running"):
        return None
    try:
        options = _parse_options(data.get("options") or {})
    except _RequestError:
        return None
    raw_rejected = data.get("rejected")
    rejected = [_text(item)[:200] for item in raw_rejected if isinstance(item, str)] \
        if isinstance(raw_rejected, list) else []
    job = Job(id=job_id, links=links[:_MAX_LINKS_PER_JOB], options=options,
              rejected=rejected[:_MAX_REJECTED_KEPT], created_at=created_at)
    job.started_at = _opt_int(data.get("started_at"))
    job.finished_at = _opt_int(data.get("finished_at"))
    job.message = _opt_text(data.get("message"))
    queue_state = data.get("queue")
    if isinstance(queue_state, dict):
        job.queue = _queue_state(queue_state)
    stats = data.get("stats")
    if isinstance(stats, dict):
        for key in job.stats:
            job.stats[key] = _int(stats.get(key))
    job.log.extend(_log_entries(data.get("log")))
    job.errors.extend(_log_entries(data.get("errors")))
    job.error_count = max(_int(data.get("error_count")), len(job.errors))
    job.log_seq = max([_int(data.get("log_seq"))] + [entry["seq"] for entry in job.log])
    job.runlog = _opt_text(data.get("runlog"))
    job.unavailable_log = _opt_text(data.get("unavailable_log"))
    job.debug_log = _opt_text(data.get("debug_log"))
    raw_failed = data.get("failed")
    for item in raw_failed if isinstance(raw_failed, list) else []:
        if isinstance(item, dict):
            JobManager._apply_failure(job, item)
    raw_artists = data.get("artists")
    if isinstance(raw_artists, dict):
        for kind in ARTIST_NOTE_KINDS:
            items = raw_artists.get(kind)
            if isinstance(items, list):
                job.artists[kind] = [_text(item)[:300] for item in items if isinstance(item, str)][:_ARTIST_NOTES_KEPT]
    attempt = _int(data.get("attempt"))
    job.attempt = min(max(attempt, 0), len(_AUTO_RETRY_DELAYS))
    retry_of = data.get("retry_of")
    job.retry_of = retry_of if isinstance(retry_of, str) and _JOB_ID_RE.fullmatch(retry_of) else None
    if status in _FINISHED:
        job.status = status
        if job.finished_at is None:
            job.finished_at = job.started_at or job.created_at
        if status != "cancelled" and job.retryable():
            job.retry_at = _opt_int(data.get("retry_at"))
    else:
        # Never run again by itself: the person decides, with /retry.
        job.status = "cancelled" if status == "queued" else "failed"
        job.message = _INTERRUPTED_QUEUED if status == "queued" else _INTERRUPTED_RUNNING
        job.finished_at = max(saved_at or 0, job.started_at or job.created_at)
    return job


class QueueFull(Exception):
    pass


class JobNotFound(Exception):
    pass


class JobConflict(Exception):
    """The request is fine but the job is not in a state it can be applied to."""


class JobManager:
    """Job queue and the single runner thread that executes it.

    Each job normally runs in a worker process (worker.py). With
    `in_process` (Android: there is no Python executable to start) it runs in
    a thread of this process instead, and cancelling raises KeyboardInterrupt
    in that thread - the same cleanup the worker's SIGINT triggers.

    With `state_dir` the jobs are kept in JOBS_FILENAME there and restored by
    the next manager on the same folder; without it they live in memory only."""

    def __init__(self, music_dir: Path, worker_options: dict[str, Any], console: Console,
                 in_process: bool = False, clock: Callable[[], float] = time.time,
                 state_dir: Optional[Path] = None):
        # `clock` (epoch seconds) times the automatic retries; tests hand in a fake one.
        self._clock = clock
        self.jobs_file = state_dir / JOBS_FILENAME if state_dir is not None else None
        self._save_failed = False
        self.music_dir = music_dir
        self.worker_options = worker_options
        self.console = console
        self.in_process = in_process
        self.revision = 0
        self.closed = False
        self._cond = threading.Condition()
        self._jobs: dict[str, Job] = {}
        self._pending: deque[str] = deque()
        self._running: Optional[Job] = None
        self._process: Optional[subprocess.Popen] = None
        self._job_thread: Optional[threading.Thread] = None
        self._give_up_at: Optional[float] = None
        self._thread = threading.Thread(target=self._run_loop, name="job-runner", daemon=True)
        self._load()

    def start(self) -> None:
        self._thread.start()

    # -- queries ----------------------------------------------------------------
    def info(self) -> dict[str, Any]:
        with self._cond:
            return {
                "name": "music-loader",
                "version": __version__,
                "api": API_VERSION,
                "music_dir": str(self.music_dir),
                "busy": self._running is not None,
                "queued": len(self._pending),
                "lyrics_modes": list(_LYRICS_MODES),
                "features": list(FEATURES),
            }

    def jobs(self) -> list[dict[str, Any]]:
        with self._cond:
            return [job.summary() for job in reversed(self._jobs.values())]

    def job(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._cond:
            job = self._jobs.get(job_id)
            return job.detail() if job is not None else None

    def debug_log(self, job_id: str) -> Optional[Path]:
        """The job's debug log file. Raises JobNotFound for an unknown job;
        None when the job has none (any more). Only a file in the library's
        log folder is ever handed out."""
        with self._cond:
            job = self._jobs.get(job_id)
            if job is None:
                raise JobNotFound(job_id)
            recorded = job.debug_log
        if not recorded:
            return None
        try:
            path = Path(recorded).resolve()
            logs = (self.music_dir / LOGS_DIRNAME).resolve()
        except OSError:
            return None
        if path.parent != logs or not path.name.startswith("debug-") or not path.is_file():
            return None
        return path

    # -- artists --------------------------------------------------------------------
    def _artist_book(self) -> AccountBook:
        return AccountBook(ArtistRegistry(self.music_dir / ARTISTS_FILENAME))

    def artists(self) -> list[dict[str, Any]]:
        return [item.public() for item in sorted_accounts(self._artist_book().all())]

    def artist_action(self, account_id: str, action: str, name: Optional[str]) -> dict[str, Any]:
        """Applies a user action to an account. Raises JobNotFound for an
        unknown account, AccountError for an action that does not apply."""
        book = self._artist_book()
        if book.get(account_id) is None:
            raise JobNotFound(account_id)
        account = apply_action(book, action, account_id, name)
        book.registry.flush()
        return account.public()

    def _state(self) -> dict[str, Any]:
        running = self._running
        return {
            "revision": self.revision,
            "busy": running is not None,
            "features": list(FEATURES),
            "jobs": [job.summary() for job in reversed(self._jobs.values())],
            "active": running.detail(_STREAM_LOG, _STREAM_ERRORS) if running is not None else None,
        }

    def wait_for_change(self, revision: int, timeout: float) -> tuple[int, Optional[dict[str, Any]]]:
        """Blocks until the state differs from `revision`; returns the new
        revision and state, or the same revision and None on timeout."""
        with self._cond:
            self._cond.wait_for(lambda: self.revision != revision or self.closed, timeout)
            if self.revision == revision or self.closed:
                return self.revision, None
            return self.revision, self._state()

    # -- changes ----------------------------------------------------------------
    def _changed(self, save: bool = False) -> None:
        """Caller holds the lock. `save` for changes the next server must see;
        progress (log lines, counters) is saved with the next of those."""
        self.revision += 1
        self._cond.notify_all()
        if save:
            self._save()

    # -- jobs file --------------------------------------------------------------
    def _load(self) -> None:
        path = self.jobs_file
        if path is None:
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            self.console.print(f"[yellow]Job history '{escape(str(path))}' ignored: {escape(str(exc))}[/yellow]")
            return
        if not isinstance(data, dict) or data.get("version") != _JOBS_FILE_VERSION \
                or not isinstance(data.get("jobs"), list):
            self.console.print(f"[yellow]Job history '{escape(str(path))}' ignored: unknown format[/yellow]")
            return
        saved_at = _opt_int(data.get("saved_at"))
        restored = interrupted = 0
        for entry in data["jobs"]:
            try:
                job = _job_from_state(entry, saved_at)
            except (TypeError, ValueError, AttributeError):
                job = None
            if job is None or job.id in self._jobs:
                continue
            restored += 1
            if entry.get("status") in ("queued", "running"):
                interrupted += 1
            self._jobs[job.id] = job
        self._trim_history()
        if restored:
            line = f"[cyan]Restored {len(self._jobs)} job(s) from the history[/cyan]"
            if interrupted:
                line += f" [yellow]({interrupted} interrupted)[/yellow]"
            self.console.print(line)
            # The file says "interrupted" from now on, whatever happens next.
            self._save()

    def _save(self) -> None:
        """Writes the jobs file atomically (caller holds the lock). The file
        holds full links (private ones too), so only its owner may read it."""
        path = self.jobs_file
        if path is None:
            return
        data = {"version": _JOBS_FILE_VERSION, "saved_at": _now_ms(),
                "jobs": [job.state() for job in self._jobs.values()]}
        tmp: Optional[str] = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # mkstemp creates the file with mode 0600.
            handle, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, path)
            tmp = None
            self._save_failed = False
        except OSError as exc:
            if not self._save_failed:
                # Once per run of failures: the server keeps working without a history.
                self.console.print(f"[red]Could not save the job history to '{escape(str(path))}': "
                                   f"{escape(str(exc))}[/red]")
            self._save_failed = True
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    def submit(self, links: list[Link], options: dict[str, Any], rejected: list[str],
               attempt: int = 0, retry_of: Optional[str] = None) -> Job:
        with self._cond:
            if self.closed:
                raise QueueFull("the server is shutting down")
            if len(self._pending) >= _MAX_QUEUED_JOBS:
                raise QueueFull(f"at most {_MAX_QUEUED_JOBS} jobs can wait in the queue")
            job_id = secrets.token_hex(6)
            while job_id in self._jobs:
                job_id = secrets.token_hex(6)
            job = Job(id=job_id, links=links, options=options, rejected=rejected,
                      attempt=attempt, retry_of=retry_of)
            self._jobs[job.id] = job
            self._pending.append(job.id)
            self._trim_history()
            self._changed(save=True)
        self.console.print(f"[cyan]Job {job.id} queued:[/cyan] {len(links)} link(s)")
        return job

    def cancel(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._cond:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.status == "queued":
                self._pending.remove(job_id)
                job.status = "cancelled"
                job.finished_at = _now_ms()
                self._changed(save=True)
            elif job.status == "running" and not job.cancel_requested:
                job.cancel_requested = True
                self._interrupt()
                self._changed()
            elif job.finished and job.retry_at is not None:
                # Cancelling a job that waits for its automatic retry.
                job.retry_at = None
                self._changed(save=True)
            return job.summary()

    def retry(self, job_id: str, scope: str) -> Job:
        """Queues a finished job again: all its links, or only its failed
        tracks (every category; unavailable tracks are never failures). Raises JobNotFound, JobConflict or
        QueueFull."""
        with self._cond:
            job = self._jobs.get(job_id)
            if job is None:
                raise JobNotFound(job_id)
            if not job.finished:
                raise JobConflict("The job has not finished yet")
            if scope == "all":
                links = list(job.links)
            else:
                links = []
                for item in job.retryable():
                    link = parse_link(item["url"])
                    if link is not None:
                        links.append(link)
            if not links:
                raise JobConflict("The job has no failed tracks that can be retried")
            new = self.submit(links, dict(job.options), [], retry_of=job.id)
            # What a person queued by hand replaces the automatic retry.
            job.retry_at = None
            self._changed(save=True)
            return new

    def tick(self) -> list[str]:
        """Queues the automatic retries that are due; returns the new job ids.
        The runner thread calls it, tests call it with a fake clock."""
        queued: list[str] = []
        with self._cond:
            if self.closed:
                # The timers stay in the jobs file for the next server.
                return queued
            now = int(self._clock() * 1000)
            for job in list(self._jobs.values()):
                if job.retry_at is None or job.retry_at > now:
                    continue
                job.retry_at = None
                links = [link for link in map(parse_link, (i["url"] for i in job.retryable()))
                         if link is not None]
                if not links:
                    self._changed(save=True)
                    continue
                try:
                    new = self.submit(links, dict(job.options), [], attempt=job.attempt + 1,
                                      retry_of=job.id)
                except QueueFull:
                    # Not lost: look again shortly, when the queue may have room.
                    job.retry_at = now + _QUEUE_FULL_RECHECK_SECONDS * 1000
                    self._changed(save=True)
                    continue
                queued.append(new.id)
        return queued

    def _schedule_retry(self, job: Job) -> None:
        """Called under the lock when a job finished: plans the next automatic attempt."""
        if (job.status not in ("completed", "failed") or not job.options.get("auto_retry") or self.closed
                or job.attempt >= len(_AUTO_RETRY_DELAYS) or not job.retryable()):
            return
        job.retry_at = int(self._clock() * 1000) + _AUTO_RETRY_DELAYS[job.attempt] * 1000

    def _retry_wait(self) -> Optional[float]:
        """How long the runner may sleep: until the next automatic retry (but
        re-reading the clock regularly), forever when none is planned."""
        due = [job.retry_at for job in self._jobs.values() if job.retry_at is not None]
        if not due:
            return None
        remaining = min(due) / 1000 - self._clock()
        return min(max(remaining, 0.0), _RETRY_POLL_SECONDS)

    def delete(self, job_id: str) -> Optional[bool]:
        """None: no such job; False: it has not finished yet."""
        with self._cond:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if not job.finished:
                return False
            del self._jobs[job_id]
            self._changed(save=True)
            return True

    def shutdown(self, timeout: float) -> None:
        with self._cond:
            self.closed = True
            for job_id in self._pending:
                job = self._jobs[job_id]
                job.status = "cancelled"
                job.finished_at = _now_ms()
            self._pending.clear()
            if self._running is not None:
                self._running.cancel_requested = True
                self._interrupt()
            self._changed(save=True)
        if self._thread.ident is not None:
            self._thread.join(timeout)

    def _trim_history(self) -> None:
        # A job that waits for its automatic retry is still needed.
        finished = [job_id for job_id, job in self._jobs.items()
                    if job.finished and job.retry_at is None]
        for job_id in finished[:max(0, len(finished) - _MAX_FINISHED_JOBS)]:
            del self._jobs[job_id]

    # -- runner -----------------------------------------------------------------
    def _run_loop(self) -> None:
        while True:
            with self._cond:
                # Also between back-to-back jobs, so a due retry waits for the
                # running job at most, not for an empty queue.
                self.tick()
                while not self._pending and not self.closed:
                    self.tick()
                    if self._pending or self.closed:
                        break
                    self._cond.wait(self._retry_wait())
                if self.closed:
                    return
                job = self._jobs[self._pending.popleft()]
                job.status = "running"
                job.started_at = _now_ms()
                job.queue = {"completed": 0, "total": len(job.links), "phase": PHASE_RESOLVING}
                self._running = job
                self._changed(save=True)
            self.console.print(f"[cyan]Job {job.id} started[/cyan]")
            try:
                status, message = self._execute(job)
            except Exception as exc:
                status, message = "failed", f"Could not run the job: {exc}"
            with self._cond:
                job.status = status
                job.message = message
                job.files.clear()
                job.finished_at = _now_ms()
                self._running = None
                self._process = None
                self._job_thread = None
                self._give_up_at = None
                self._schedule_retry(job)
                self._trim_history()
                self._changed(save=True)
            style = {"completed": "green", "cancelled": "yellow"}.get(status, "red")
            line = f"[{style}]Job {job.id} {status}[/{style}]"
            if message:
                line += f": {escape(message)}"
            self.console.print(line)
            if job.retry_at is not None:
                self.console.print(f"[yellow]Job {job.id}: {len(job.retryable())} failed track(s) will be "
                                   f"retried automatically (attempt {job.attempt + 1} of "
                                   f"{len(_AUTO_RETRY_DELAYS)})[/yellow]")

    def _execute(self, job: Job) -> tuple[str, Optional[str]]:
        spec = {
            "links": [link.url for link in job.links],
            "output": str(self.music_dir),
            **job.options,
            **self.worker_options,
        }
        if self.in_process:
            return self._execute_in_process(job, spec)
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        # The worker must import this very copy of the package, wherever the
        # server was started from.
        package_root = str(Path(__file__).resolve().parent.parent)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [package_root, env.get("PYTHONPATH")]))
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            # Own process group: cancelling interrupts the worker and its
            # yt-dlp/spotdl children together, like Ctrl+C in a terminal.
            kwargs["start_new_session"] = True
        process = subprocess.Popen(
            [sys.executable, "-m", "music_loader.worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
            **kwargs,
        )
        with self._cond:
            self._process = process
            if job.cancel_requested:
                self._interrupt()

        stderr_tail: deque[str] = deque(maxlen=20)

        def drain_stderr() -> None:
            assert process.stderr is not None
            for raw in process.stderr:
                if raw.strip():
                    stderr_tail.append(raw.rstrip())

        drainer = threading.Thread(target=drain_stderr, name="worker-stderr", daemon=True)
        drainer.start()
        try:
            assert process.stdin is not None
            process.stdin.write(json.dumps(spec))
            process.stdin.close()
        except OSError:
            pass

        finished: Optional[dict[str, Any]] = None
        assert process.stdout is not None
        for raw in process.stdout:
            event = self._event(job, raw)
            if event is not None:
                finished = event
        code = process.wait()
        drainer.join(timeout=5)

        if finished is not None and finished.get("status") in _FINISHED:
            return finished["status"], _text(finished.get("message")) or None
        if job.cancel_requested:
            return "cancelled", None
        detail = stderr_tail[-1] if stderr_tail else ""
        return "failed", f"Worker exited with code {code}" + (f": {detail}" if detail else "")

    def _event(self, job: Job, raw: str) -> Optional[dict[str, Any]]:
        """Applies one event line of a job; returns it when it is the final one."""
        try:
            event = json.loads(raw)
        except ValueError:
            return None
        if not isinstance(event, dict):
            return None
        if event.get("type") == "finished":
            return event
        with self._cond:
            # A job given up on after a cancel may still be unwinding.
            if self._running is job:
                self._apply(job, event)
                self._changed()
        return None

    def _execute_in_process(self, job: Job, spec: dict[str, Any]) -> tuple[str, Optional[str]]:
        from . import inprocess, worker
        from .events import EventDashboard

        inprocess.install()
        _reset_run_caches()
        # The job thread only queues its event lines; this thread applies
        # them, so a KeyboardInterrupt raised in the job thread can never land
        # while it holds the manager's lock.
        lines: "queue.SimpleQueue[str]" = queue.SimpleQueue()

        class _Stream:
            def write(self, text: str) -> int:
                lines.put(text)
                return len(text)

            def flush(self) -> None:
                pass

        events = EventDashboard(_Stream())

        def run() -> None:
            try:
                worker.run(spec, events)
            except KeyboardInterrupt:
                events.emit({"type": "finished", "status": "cancelled", "message": None})
            except BaseException as exc:
                events.emit({"type": "finished", "status": "failed",
                             "message": f"{type(exc).__name__}: {exc}"})

        thread = threading.Thread(target=run, name=f"job-{job.id}", daemon=True)
        with self._cond:
            self._job_thread = thread
            thread.start()
            if job.cancel_requested:
                self._interrupt()

        finished: Optional[dict[str, Any]] = None
        while True:
            try:
                text = lines.get(timeout=0.5)
            except queue.Empty:
                if not thread.is_alive():
                    break
                with self._cond:
                    give_up = self._give_up_at is not None and time.monotonic() > self._give_up_at
                if give_up:
                    self.console.print(f"[red]Job {job.id} did not stop in time and was left behind[/red]")
                    break
                continue
            for raw in text.splitlines():
                event = self._event(job, raw)
                if event is not None and finished is None:
                    finished = event

        if finished is not None and finished.get("status") in _FINISHED:
            return finished["status"], _text(finished.get("message")) or None
        if job.cancel_requested:
            return "cancelled", None
        return "failed", "The job ended without a result"

    @staticmethod
    def _apply(job: Job, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "log":
            job.log_seq += 1
            level = "error" if event.get("level") == "error" else "info"
            entry = {
                "seq": job.log_seq,
                "time": _now_ms(),
                "level": level,
                "source": _text(event.get("source")) or None,
                "text": _text(event.get("text")),
            }
            job.log.append(entry)
            if level == "error":
                job.errors.append(entry)
                job.error_count += 1
        elif kind == "queue":
            job.queue = _queue_state(event)
        elif kind == "stats" and isinstance(event.get("stats"), dict):
            for key, value in event["stats"].items():
                if key in job.stats:
                    job.stats[key] = _int(value)
        elif kind == "file":
            slot = _int(event.get("slot"))
            if event.get("visible"):
                percent = event.get("percent")
                job.files[slot] = {
                    "label": _text(event.get("label")),
                    "percent": float(percent) if isinstance(percent, (int, float)) else 0.0,
                    "speed": _text(event.get("speed")),
                    "eta": _text(event.get("eta")),
                }
            else:
                job.files.pop(slot, None)
        elif kind == "failure":
            JobManager._apply_failure(job, event)
        elif kind == "runlog":
            job.runlog = _text(event.get("path")) or None
        elif kind == "unavailable_log":
            job.unavailable_log = _text(event.get("path")) or None
        elif kind == "debug_log":
            job.debug_log = _text(event.get("path")) or None
        elif kind == "artists":
            notes = job.artists.get(_text(event.get("kind")))
            text = _text(event.get("text"))[:300]
            if notes is not None and text and text not in notes and len(notes) < _ARTIST_NOTES_KEPT:
                notes.append(text)

    @staticmethod
    def _apply_failure(job: Job, event: dict[str, Any]) -> None:
        link = parse_link(_text(event.get("url")))
        try:
            category = FailureCategory(event.get("category"))
        except ValueError:
            category = FailureCategory.FAILED
        if category is FailureCategory.UNAVAILABLE:
            # Unavailable tracks are reported through their own event, never as failures.
            return
        if link is None or (link.url not in job.failed and len(job.failed) >= _FAILED_KEPT):
            return
        service = _text(event.get("service"))
        job.failed[link.url] = {
            "url": link.url,
            "service": service if service in ("spotify", "soundcloud") else link.service,
            "title": _text(event.get("title"))[:300],
            "category": category.value,
        }

    def _interrupt(self) -> None:
        """Asks the running worker to stop (caller holds the lock)."""
        if self.in_process:
            thread = self._job_thread
            if thread is not None and thread.is_alive():
                from .inprocess import raise_in

                raise_in(thread, KeyboardInterrupt)
                if self._give_up_at is None:
                    self._give_up_at = time.monotonic() + _CANCEL_GRACE_SECONDS
            return
        process = self._process
        if process is None or process.poll() is not None:
            return
        try:
            if os.name == "nt":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(process.pid, signal.SIGINT)
        except OSError:
            pass
        timer = threading.Timer(_CANCEL_GRACE_SECONDS, _kill_tree, args=(process,))
        timer.daemon = True
        timer.start()


def _reset_run_caches() -> None:
    """Library indexes and registries are cached for the run of one worker
    process; an in-process job starts from the files on disk just the same."""
    from . import artists, lyrics, soundcloud, soundcloud_index, spotify_index

    with artists._REGISTRIES_LOCK:
        artists._REGISTRIES.clear()
    with lyrics._ATTEMPTS_LOCK:
        lyrics._ATTEMPTS.clear()
    with soundcloud_index._INDEX_CACHE_LOCK:
        soundcloud_index._INDEX_CACHE.clear()
    spotify_index._LIBRARIES.clear()
    # A new job starts with all its parallel downloads, not with the reduced
    # number an earlier rate limit caused.
    soundcloud._SHARED_GATE.reset_throttle()


def _kill_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                           capture_output=True, check=False)
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        pass


# -- HTTP ---------------------------------------------------------------------------
class _RequestError(Exception):
    def __init__(self, status: HTTPStatus, message: str, **extra: Any):
        super().__init__(message)
        self.status = status
        self.payload = {"error": message, **extra}


def _parse_job_request(body: Any) -> tuple[list[Link], dict[str, Any], list[str]]:
    if not isinstance(body, dict):
        raise _RequestError(HTTPStatus.BAD_REQUEST, "Expected a JSON object")
    raw_links = body.get("links")
    if isinstance(raw_links, str):
        raw_links = [raw_links]
    if not isinstance(raw_links, list) or not all(isinstance(item, str) for item in raw_links):
        raise _RequestError(HTTPStatus.BAD_REQUEST, "'links' must be a list of strings")

    # Pasted text may hold several links per entry.
    entries = [part for item in raw_links for part in item.split()]
    if len(entries) > _MAX_LINKS_PER_JOB:
        raise _RequestError(HTTPStatus.BAD_REQUEST, f"At most {_MAX_LINKS_PER_JOB} links per job")
    links: list[Link] = []
    rejected: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        link = parse_link(entry)
        if link is None:
            if len(rejected) < _MAX_REJECTED_KEPT:
                rejected.append(entry[:200])
        elif link.url not in seen:
            seen.add(link.url)
            links.append(link)

    options = _parse_options(body.get("options") or {})
    if not links:
        raise _RequestError(HTTPStatus.BAD_REQUEST, "No Spotify or SoundCloud links", rejected=rejected)
    return links, options, rejected


def _parse_options(raw_options: Any) -> dict[str, Any]:
    if not isinstance(raw_options, dict):
        raise _RequestError(HTTPStatus.BAD_REQUEST, "'options' must be an object")
    lyrics = raw_options.get("lyrics", LYRICS_MODE_STRICT)
    if lyrics not in _LYRICS_MODES:
        raise _RequestError(HTTPStatus.BAD_REQUEST, f"'lyrics' must be one of {', '.join(_LYRICS_MODES)}")
    options: dict[str, Any] = {"lyrics": lyrics}
    for key in _BOOL_OPTIONS:
        value = raw_options.get(key, False)
        if not isinstance(value, bool):
            raise _RequestError(HTTPStatus.BAD_REQUEST, f"'{key}' must be true or false")
        options[key] = value
    existing = raw_options.get("existing")
    if existing is None:
        existing = EXISTING_ALL if options["recheck"] else EXISTING_KEEP
    elif existing not in EXISTING_MODES:
        raise _RequestError(HTTPStatus.BAD_REQUEST, f"'existing' must be one of {', '.join(EXISTING_MODES)}")
    elif options["recheck"] and existing != EXISTING_ALL:
        raise _RequestError(HTTPStatus.BAD_REQUEST, "'recheck' is 'existing': 'all' and contradicts "
                                                    f"'existing': '{existing}'")
    options["existing"] = existing
    options["recheck"] = existing == EXISTING_ALL
    default = SoundCloudFormat().options()
    feat = raw_options.get("soundcloud_feat", default["soundcloud_feat"])
    if feat not in FEAT_MODES:
        raise _RequestError(HTTPStatus.BAD_REQUEST, f"'soundcloud_feat' must be one of {', '.join(FEAT_MODES)}")
    options["soundcloud_feat"] = feat
    for key in ("soundcloud_keep_producers", "soundcloud_keep_versions", "soundcloud_strip_promo"):
        value = raw_options.get(key, default[key])
        if not isinstance(value, bool):
            raise _RequestError(HTTPStatus.BAD_REQUEST, f"'{key}' must be true or false")
        options[key] = value
    try:
        options["soundcloud_filename"] = validate_filename_template(
            raw_options.get("soundcloud_filename", default["soundcloud_filename"]))
    except ValueError as exc:
        raise _RequestError(HTTPStatus.BAD_REQUEST, f"'soundcloud_filename': {exc}")
    lyrics_default = LyricsSettings().options()
    checks = (
        ("lyrics_providers", parse_lyrics_providers, list),
        ("lyrics_length_tolerance", validate_length_tolerance, float),
        ("lyrics_retry_days", validate_retry_days, int),
    )
    if not isinstance(raw_options.get("lyrics_providers", []), list):
        raise _RequestError(HTTPStatus.BAD_REQUEST, "'lyrics_providers' must be a list")
    for key, validate, convert in checks:
        try:
            options[key] = convert(validate(raw_options.get(key, lyrics_default[key])))
        except ValueError as exc:
            raise _RequestError(HTTPStatus.BAD_REQUEST, f"'{key}': {exc}")
    for key in ("lyrics_plain", "lyrics_original_for_versions"):
        value = raw_options.get(key, lyrics_default[key])
        if not isinstance(value, bool):
            raise _RequestError(HTTPStatus.BAD_REQUEST, f"'{key}' must be true or false")
        options[key] = value
    return options


def _parse_retry_request(body: bytes) -> str:
    try:
        request = json.loads(body.decode("utf-8") or "null")
    except (UnicodeDecodeError, ValueError):
        raise _RequestError(HTTPStatus.BAD_REQUEST, "Body is not valid JSON")
    scope = request.get("scope") if isinstance(request, dict) else None
    if scope not in _RETRY_SCOPES:
        raise _RequestError(HTTPStatus.BAD_REQUEST, "'scope' must be one of " + ", ".join(_RETRY_SCOPES))
    return scope


def _parse_artist_request(body: bytes) -> str:
    try:
        request = json.loads(body.decode("utf-8") or "null")
    except (UnicodeDecodeError, ValueError):
        raise _RequestError(HTTPStatus.BAD_REQUEST, "Body is not valid JSON")
    name = request.get("name") if isinstance(request, dict) else None
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        raise _RequestError(HTTPStatus.BAD_REQUEST, "'name' must be the artist name (1-200 characters)")
    return name


class _Handler(BaseHTTPRequestHandler):
    server_version = f"music-loader/{__version__}"
    protocol_version = "HTTP/1.1"
    timeout = _SOCKET_TIMEOUT
    server: "_Server"

    def log_message(self, format: str, *args: Any) -> None:
        # The app polls and streams; logging every request would bury the
        # job messages.
        pass

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    # -- plumbing ---------------------------------------------------------------
    def _send_json(self, status: HTTPStatus, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        try:
            handle = open(path, "rb")
        except OSError:
            raise _RequestError(HTTPStatus.NOT_FOUND, "The file is gone")
        with handle:
            size = os.fstat(handle.fileno()).st_size
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            # A running job keeps appending: send what was there when asked.
            remaining = size
            while remaining > 0:
                chunk = handle.read(min(remaining, 64 * 1024))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)
            if remaining:
                # The file shrank under us; the client must not reuse the connection.
                self.close_connection = True

    def _send_empty(self, status: HTTPStatus) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        scheme, _, value = header.partition(" ")
        if scheme.lower() != "bearer":
            return False
        return hmac.compare_digest(value.strip().encode(), self.server.token.encode())

    def _read_body(self) -> bytes:
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            raise _RequestError(HTTPStatus.LENGTH_REQUIRED, "Send the body with a Content-Length")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > _MAX_BODY_BYTES:
            self.close_connection = True
            raise _RequestError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body too large")
        return self.rfile.read(length) if length else b""

    def _dispatch(self, method: str) -> None:
        try:
            path = urlsplit(self.path).path.rstrip("/")
            parts = path.split("/")[1:]
            if parts[:2] != ["api", "v1"]:
                # Any body is left unread, so the connection cannot be reused.
                self.close_connection = True
                raise _RequestError(HTTPStatus.NOT_FOUND, "Not found")
            if not self._authorized():
                # The body of an unauthenticated request is never read.
                self.close_connection = True
                time.sleep(0.5)
                self.send_response(HTTPStatus.UNAUTHORIZED)
                self.send_header("WWW-Authenticate", 'Bearer realm="music-loader"')
                body = b'{"error": "Missing or wrong token"}'
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            body = self._read_body() if method in ("POST", "DELETE") else b""
            self._route(method, parts[2:], body)
        except _RequestError as exc:
            self._send_json(exc.status, exc.payload)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True

    def _route(self, method: str, parts: list[str], body: bytes) -> None:
        manager = self.server.manager
        if parts == ["info"] and method == "GET":
            return self._send_json(HTTPStatus.OK, manager.info())
        if parts == ["events"] and method == "GET":
            return self._stream()
        if parts == ["jobs"]:
            if method == "GET":
                return self._send_json(HTTPStatus.OK, {"jobs": manager.jobs()})
            if method == "POST":
                try:
                    request = json.loads(body.decode("utf-8") or "null")
                except (UnicodeDecodeError, ValueError):
                    raise _RequestError(HTTPStatus.BAD_REQUEST, "Body is not valid JSON")
                links, options, rejected = _parse_job_request(request)
                try:
                    job = manager.submit(links, options, rejected)
                except QueueFull as exc:
                    raise _RequestError(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
                detail = manager.job(job.id)
                return self._send_json(HTTPStatus.CREATED, {"job": detail, "rejected": rejected})
        if len(parts) == 2 and parts[0] == "jobs":
            if method == "GET":
                detail = manager.job(parts[1])
                if detail is None:
                    raise _RequestError(HTTPStatus.NOT_FOUND, "No such job")
                return self._send_json(HTTPStatus.OK, {"job": detail})
            if method == "DELETE":
                deleted = manager.delete(parts[1])
                if deleted is None:
                    raise _RequestError(HTTPStatus.NOT_FOUND, "No such job")
                if not deleted:
                    raise _RequestError(HTTPStatus.CONFLICT, "The job has not finished yet")
                return self._send_empty(HTTPStatus.NO_CONTENT)
        if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "cancel" and method == "POST":
            summary = manager.cancel(parts[1])
            if summary is None:
                raise _RequestError(HTTPStatus.NOT_FOUND, "No such job")
            return self._send_json(HTTPStatus.OK, {"job": summary})
        if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "debug-log" and method == "GET":
            try:
                path = manager.debug_log(parts[1])
            except JobNotFound:
                raise _RequestError(HTTPStatus.NOT_FOUND, "No such job")
            if path is None:
                raise _RequestError(HTTPStatus.NOT_FOUND, "The job has no debug log")
            return self._send_file(path, "application/x-ndjson; charset=utf-8")
        if parts == ["artists"] and method == "GET":
            return self._send_json(HTTPStatus.OK, {"accounts": manager.artists()})
        if len(parts) == 3 and parts[0] == "artists" and method == "POST":
            account_id, action = parts[1], parts[2]
            if not _ACCOUNT_ID_RE.fullmatch(account_id) or action not in _ARTIST_ACTIONS:
                raise _RequestError(HTTPStatus.NOT_FOUND, "Not found")
            name = _parse_artist_request(body) if action == "set" else None
            try:
                account = manager.artist_action(account_id, action, name)
            except JobNotFound:
                raise _RequestError(HTTPStatus.NOT_FOUND, "No such account")
            except AccountError as exc:
                raise _RequestError(HTTPStatus.BAD_REQUEST, str(exc))
            return self._send_json(HTTPStatus.OK, {"account": account})
        if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "retry" and method == "POST":
            scope = _parse_retry_request(body)
            try:
                job = manager.retry(parts[1], scope)
            except JobNotFound:
                raise _RequestError(HTTPStatus.NOT_FOUND, "No such job")
            except JobConflict as exc:
                raise _RequestError(HTTPStatus.CONFLICT, str(exc))
            except QueueFull as exc:
                raise _RequestError(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
            return self._send_json(HTTPStatus.CREATED, {"job": manager.job(job.id)})
        raise _RequestError(HTTPStatus.NOT_FOUND, "Not found")

    def _stream(self) -> None:
        manager = self.server.manager
        self.close_connection = True
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        # Keeps a reverse proxy (nginx) from buffering the stream.
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        revision = -1
        while not manager.closed:
            new_revision, state = manager.wait_for_change(revision, _STREAM_KEEPALIVE)
            if manager.closed:
                break
            if state is None:
                chunk = ": keep-alive\n\n"
            else:
                data = json.dumps(state, ensure_ascii=False)
                chunk = f"id: {new_revision}\nevent: state\ndata: {data}\n\n"
            self.wfile.write(chunk.encode("utf-8"))
            self.wfile.flush()
            revision = new_revision
            time.sleep(_STREAM_INTERVAL)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], manager: JobManager, token: str):
        self.manager = manager
        self.token = token
        super().__init__(address, _Handler)


def make_server(host: str, port: int, manager: JobManager, token: str) -> _Server:
    server_class = _Server
    if ":" in host:
        server_class = type("_Server6", (_Server,), {"address_family": socket.AF_INET6})
    return server_class((host, port), manager, token)


# -- token & startup ----------------------------------------------------------------
def _config_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "music-loader"


def load_token(explicit: Optional[str], renew: bool) -> tuple[str, str]:
    """Returns (token, where it comes from)."""
    for token, source in ((explicit, "--token"), (os.environ.get(TOKEN_ENV), TOKEN_ENV)):
        if token:
            if not _TOKEN_RE.fullmatch(token):
                raise ValueError(f"the token from {source} must be at least 16 characters of "
                                 f"letters, digits and ._~+/=-")
            return token, source
    path = _config_dir() / TOKEN_FILENAME
    if not renew:
        try:
            token = path.read_text(encoding="utf-8").strip()
        except OSError:
            token = ""
        if _TOKEN_RE.fullmatch(token):
            return token, str(path)
    token = secrets.token_urlsafe(24)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(token + "\n")
    return token, str(path)


def _addresses(host: str) -> list[str]:
    if host not in ("", "0.0.0.0", "::"):
        return [host]
    found = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            # Picks the interface of the default route; nothing is sent.
            probe.connect(("10.255.255.255", 1))
            found.append(probe.getsockname()[0])
    except OSError:
        pass
    return found or ["127.0.0.1"]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="music-loader serve",
        description="Runs music-loader as a server for the Android app: links are queued "
                    "over HTTP and downloaded into the library on this machine.",
    )
    parser.add_argument("-o", "--output", required=True, help="Target Music folder.")
    parser.add_argument("--host", default="0.0.0.0",
                        help="Address to listen on (default: 0.0.0.0, every interface). "
                             "Use 127.0.0.1 behind a reverse proxy.")
    parser.add_argument("--port", type=_positive_int, default=DEFAULT_PORT,
                        help=f"Port to listen on (default: {DEFAULT_PORT}).")
    parser.add_argument("--token", default=None,
                        help=f"Access token for the app. Prefer the {TOKEN_ENV} environment "
                             f"variable; by default a token is generated once and kept in "
                             f"{_config_dir() / TOKEN_FILENAME}.")
    parser.add_argument("--new-token", action="store_true",
                        help="Generate a new stored token (the app has to be paired again).")
    parser.add_argument("--state-dir", default=None,
                        help=f"Folder for the job history ({JOBS_FILENAME}); default: "
                             f"{_config_dir()}. Give each server its own when several run "
                             f"on one machine.")
    parser.add_argument("--lyrics-workers", type=_positive_int, default=2,
                        help="Number of parallel lyrics workers (default: 2).")
    parser.add_argument("--soundcloud-download-workers", type=_positive_int, default=2,
                        help="Number of parallel SoundCloud downloads (default: 2).")
    parser.add_argument("--soundcloud-workers", type=_positive_int, default=4,
                        help="Number of parallel SoundCloud conversion/tagging workers (default: 4).")
    parser.add_argument("--spotify-threads", type=_positive_int, default=4,
                        help="Number of parallel spotdl downloads (default: 4).")
    return parser.parse_args(argv)


def _raise_interrupt(signum, frame) -> None:
    raise KeyboardInterrupt


def main(argv: Optional[list[str]] = None) -> int:
    console = Console()
    args = parse_args(argv if argv is not None else sys.argv[1:])
    if not check_dependencies(console):
        return 1

    music_dir = Path(args.output).expanduser().resolve()
    try:
        music_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        console.print(f"[red]Could not use the target folder '{escape(str(music_dir))}': "
                      f"{escape(str(exc))}[/red]")
        return 1
    try:
        token, token_source = load_token(args.token, args.new_token)
    except (OSError, ValueError) as exc:
        console.print(f"[red]Token: {escape(str(exc))}[/red]")
        return 1

    manager = JobManager(
        music_dir,
        {
            "lyrics_workers": args.lyrics_workers,
            "soundcloud_download_workers": args.soundcloud_download_workers,
            "soundcloud_workers": args.soundcloud_workers,
            "spotify_threads": args.spotify_threads,
        },
        console,
        state_dir=Path(args.state_dir).expanduser() if args.state_dir else _config_dir(),
    )
    try:
        server = make_server(args.host, args.port, manager, token)
    except OSError as exc:
        console.print(f"[red]Could not listen on {escape(args.host)}:{args.port}: {escape(str(exc))}[/red]")
        return 1

    # A service manager stops the server with SIGTERM: cancel the running
    # job cleanly, as on Ctrl+C. Handling SIGINT here also means the workers
    # start with its default action even when the server was launched with
    # SIGINT ignored (nohup, some init scripts), so cancelling reaches them.
    signal.signal(signal.SIGTERM, _raise_interrupt)
    signal.signal(signal.SIGINT, signal.default_int_handler)
    manager.start()
    console.rule(f"music-loader {__version__} server")
    console.print(f"[bold]Library:[/bold] {escape(str(music_dir))}")
    for address in _addresses(args.host):
        shown = f"[{address}]" if ":" in address else address
        console.print(f"[bold]Server address:[/bold] http://{shown}:{args.port}")
    console.print(f"[bold]Token:[/bold] {escape(token)} [dim]({escape(token_source)})[/dim]")
    console.print("[dim]Enter the address and the token in the app. Plain HTTP: use a trusted "
                  "network, a VPN or an HTTPS reverse proxy. Ctrl+C stops the server.[/dim]")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopping: cancelling the running job...[/yellow]")
    finally:
        manager.shutdown(timeout=_CANCEL_GRACE_SECONDS + 10)
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
