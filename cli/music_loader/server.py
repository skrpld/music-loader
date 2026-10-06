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
    POST   /api/v1/jobs/<id>/cancel   cancel a queued or running job
    DELETE /api/v1/jobs/<id>          remove a finished job from the list
    GET    /api/v1/events             Server-Sent Events: a "state" event on every change

Job options: {"lyrics": "strict" | "loose" | "off", "recheck": false,
"soundcloud_reposts": false, "soundcloud_likes": false}.

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
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

from rich.console import Console
from rich.markup import escape

from . import __version__
from .cli import _positive_int
from .config import LYRICS_MODE_LOOSE, LYRICS_MODE_STRICT
from .deps import check_dependencies
from .links import Link, parse_link, redact_url
from .ui import Stats

API_VERSION = 1
DEFAULT_PORT = 8765
TOKEN_ENV = "MUSIC_LOADER_TOKEN"
TOKEN_FILENAME = "server-token"

_TOKEN_RE = re.compile(r"[A-Za-z0-9._~+/=-]{16,512}")
_LYRICS_MODES = (LYRICS_MODE_STRICT, LYRICS_MODE_LOOSE, "off")
_FINISHED = {"completed", "cancelled", "failed"}

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
    queue: dict[str, int] = field(default_factory=lambda: {"completed": 0, "total": 0})
    stats: dict[str, int] = field(default_factory=lambda: asdict(Stats()))
    files: dict[int, dict[str, Any]] = field(default_factory=dict)
    log: deque = field(default_factory=lambda: deque(maxlen=_LOG_KEPT))
    errors: deque = field(default_factory=lambda: deque(maxlen=_ERRORS_KEPT))
    error_count: int = 0
    log_seq: int = 0
    runlog: Optional[str] = None
    cancel_requested: bool = False

    @property
    def finished(self) -> bool:
        return self.status in _FINISHED

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
        }

    def detail(self, log_limit: int = _LOG_KEPT, error_limit: int = _ERRORS_KEPT) -> dict[str, Any]:
        data = self.summary()
        data.update(
            links=[_link_json(link) for link in self.links],
            rejected=list(self.rejected),
            files=[{"slot": slot, **row} for slot, row in sorted(self.files.items())],
            log=list(self.log)[-log_limit:],
            errors=list(self.errors)[-error_limit:],
            runlog=self.runlog,
        )
        return data


class QueueFull(Exception):
    pass


class JobManager:
    """Job queue and the single runner thread that executes it.

    Each job normally runs in a worker process (worker.py). With
    `in_process` (Android: there is no Python executable to start) it runs in
    a thread of this process instead, and cancelling raises KeyboardInterrupt
    in that thread - the same cleanup the worker's SIGINT triggers."""

    def __init__(self, music_dir: Path, worker_options: dict[str, Any], console: Console,
                 in_process: bool = False):
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
            }

    def jobs(self) -> list[dict[str, Any]]:
        with self._cond:
            return [job.summary() for job in reversed(self._jobs.values())]

    def job(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._cond:
            job = self._jobs.get(job_id)
            return job.detail() if job is not None else None

    def _state(self) -> dict[str, Any]:
        running = self._running
        return {
            "revision": self.revision,
            "busy": running is not None,
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
    def _changed(self) -> None:
        self.revision += 1
        self._cond.notify_all()

    def submit(self, links: list[Link], options: dict[str, Any], rejected: list[str]) -> Job:
        with self._cond:
            if self.closed:
                raise QueueFull("the server is shutting down")
            if len(self._pending) >= _MAX_QUEUED_JOBS:
                raise QueueFull(f"at most {_MAX_QUEUED_JOBS} jobs can wait in the queue")
            job_id = secrets.token_hex(6)
            while job_id in self._jobs:
                job_id = secrets.token_hex(6)
            job = Job(id=job_id, links=links, options=options, rejected=rejected)
            self._jobs[job.id] = job
            self._pending.append(job.id)
            self._trim_history()
            self._changed()
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
                self._changed()
            elif job.status == "running" and not job.cancel_requested:
                job.cancel_requested = True
                self._interrupt()
                self._changed()
            return job.summary()

    def delete(self, job_id: str) -> Optional[bool]:
        """None: no such job; False: it has not finished yet."""
        with self._cond:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if not job.finished:
                return False
            del self._jobs[job_id]
            self._changed()
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
            self._changed()
        self._thread.join(timeout)

    def _trim_history(self) -> None:
        finished = [job_id for job_id, job in self._jobs.items() if job.finished]
        for job_id in finished[:max(0, len(finished) - _MAX_FINISHED_JOBS)]:
            del self._jobs[job_id]

    # -- runner -----------------------------------------------------------------
    def _run_loop(self) -> None:
        while True:
            with self._cond:
                self._cond.wait_for(lambda: self._pending or self.closed)
                if self.closed:
                    return
                job = self._jobs[self._pending.popleft()]
                job.status = "running"
                job.started_at = _now_ms()
                job.queue = {"completed": 0, "total": len(job.links)}
                self._running = job
                self._changed()
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
                self._trim_history()
                self._changed()
            style = {"completed": "green", "cancelled": "yellow"}.get(status, "red")
            line = f"[{style}]Job {job.id} {status}[/{style}]"
            if message:
                line += f": {escape(message)}"
            self.console.print(line)

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
            job.queue = {"completed": _int(event.get("completed")), "total": _int(event.get("total"))}
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
        elif kind == "runlog":
            job.runlog = _text(event.get("path")) or None

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
    from . import artists, lyrics, soundcloud_index, spotify_index

    with artists._REGISTRIES_LOCK:
        artists._REGISTRIES.clear()
    with lyrics._ATTEMPTS_LOCK:
        lyrics._ATTEMPTS.clear()
    with soundcloud_index._INDEX_CACHE_LOCK:
        soundcloud_index._INDEX_CACHE.clear()
    spotify_index._LIBRARIES.clear()


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

    raw_options = body.get("options") or {}
    if not isinstance(raw_options, dict):
        raise _RequestError(HTTPStatus.BAD_REQUEST, "'options' must be an object")
    lyrics = raw_options.get("lyrics", LYRICS_MODE_STRICT)
    if lyrics not in _LYRICS_MODES:
        raise _RequestError(HTTPStatus.BAD_REQUEST, f"'lyrics' must be one of {', '.join(_LYRICS_MODES)}")
    options: dict[str, Any] = {"lyrics": lyrics}
    for key in ("recheck", "soundcloud_reposts", "soundcloud_likes"):
        value = raw_options.get(key, False)
        if not isinstance(value, bool):
            raise _RequestError(HTTPStatus.BAD_REQUEST, f"'{key}' must be true or false")
        options[key] = value

    if not links:
        raise _RequestError(HTTPStatus.BAD_REQUEST, "No Spotify or SoundCloud links", rejected=rejected)
    return links, options, rejected


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
