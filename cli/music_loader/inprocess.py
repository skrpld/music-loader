"""Runs spotdl and yt-dlp inside this interpreter instead of as child processes.

On Android the interpreter is embedded in the app (Chaquopy): there is no
Python executable that `python -m yt_dlp` could be started with. There, the
pipelines still build the same command lines, and `process.run_streamed` /
`process.run_captured` hand commands that start with `MARK` to this module.

Every run gets its own thread. Output is separated per run: `sys.stdout` and
`sys.stderr` are replaced by routers that send whatever a thread prints to the
run that thread belongs to; threads a tool starts (spotdl's download workers,
its event loop executor) inherit their parent's run. So two yt-dlp downloads
can run side by side and their progress lines never mix, exactly like two
child processes.

Stopping a run (cancel, idle timeout) raises `ToolAborted` - a
KeyboardInterrupt, so the tools' own Ctrl+C cleanup runs - in each of its
threads and kills the ffmpeg processes it started.

Desktop runs use real child processes; MUSIC_LOADER_IN_PROCESS=1 switches to
this mode there too (for tests).
"""
from __future__ import annotations

import ctypes
import importlib.util
import logging
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
import types
from typing import Callable, Optional

ENABLED = os.environ.get("MUSIC_LOADER_IN_PROCESS") == "1" or hasattr(sys, "getandroidapilevel")
MARK = "<in-process>"

# How long a stopped run may take to unwind before it is given up on.
_STOP_GRACE_SECONDS = 15.0
_RESTOP_INTERVAL = 3.0
_ATTR = "_music_loader_run"


class ToolAborted(KeyboardInterrupt):
    """Raised inside the threads of a run that is being stopped."""


class _Run:
    """Output, threads and child processes of one tool invocation."""

    def __init__(self, merge: bool):
        # merge: one line stream of stdout and stderr (run_streamed);
        # otherwise both are collected separately (run_captured).
        self.merge = merge
        self.lines: "queue.Queue[Optional[str]]" = queue.Queue()
        self.captured: dict[str, list[str]] = {"out": [], "err": []}
        self.returncode: Optional[int] = None
        self.done = threading.Event()
        self.stopping = False
        self._partial = {"out": "", "err": ""}
        self._threads: list[threading.Thread] = []
        self._processes: list[subprocess.Popen] = []
        self._lock = threading.Lock()

    # -- output -------------------------------------------------------------------
    def feed(self, stream: str, text: str) -> None:
        if not text:
            return
        with self._lock:
            if not self.merge:
                self.captured[stream].append(text)
                return
            data = self._partial[stream] + text.replace("\r\n", "\n").replace("\r", "\n")
            *complete, self._partial[stream] = data.split("\n")
        for line in complete:
            self.lines.put(line)

    def finish(self, code: int) -> None:
        with self._lock:
            rest = [self._partial[name] for name in ("out", "err") if self._partial[name]]
            self._partial = {"out": "", "err": ""}
        if self.merge:
            for line in rest:
                self.lines.put(line)
        self.returncode = code
        self.lines.put(None)
        self.done.set()

    # -- ownership ------------------------------------------------------------------
    def add_thread(self, thread: threading.Thread) -> None:
        with self._lock:
            self._threads = [item for item in self._threads if item.is_alive()]
            self._threads.append(thread)

    def add_process(self, process: subprocess.Popen) -> None:
        with self._lock:
            self._processes = [item for item in self._processes if item.poll() is None]
            self._processes.append(process)
            stopping = self.stopping
        if stopping:
            _kill(process)

    # -- stopping -------------------------------------------------------------------
    def stop(self) -> None:
        with self._lock:
            self.stopping = True
            processes = list(self._processes)
            threads = list(self._threads)
        for process in processes:
            _kill(process)
        for thread in threads:
            raise_in(thread)

    def stop_and_wait(self, grace: float = _STOP_GRACE_SECONDS) -> bool:
        """Stops the run; True when it ended within `grace` seconds."""
        deadline = time.monotonic() + grace
        while not self.done.is_set():
            self.stop()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            self.done.wait(min(_RESTOP_INTERVAL, remaining))
        return True


def _kill(process: subprocess.Popen) -> None:
    try:
        if process.poll() is None:
            process.kill()
    except OSError:
        pass


_set_async_exc = None


def _async_exc_function():
    global _set_async_exc
    if _set_async_exc is None:
        try:
            function = ctypes.pythonapi.PyThreadState_SetAsyncExc
        except AttributeError:
            # An app that loads libpython itself (Chaquopy) may not export its
            # symbols to the process.
            library = ctypes.PyDLL("libpython%d.%d.so" % sys.version_info[:2])
            function = library.PyThreadState_SetAsyncExc
        _set_async_exc = function
    return _set_async_exc


def raise_in(thread: threading.Thread, exc: type = ToolAborted) -> None:
    """Raises `exc` in `thread` as soon as it runs Python code again."""
    ident = thread.ident
    if ident is None or not thread.is_alive() or thread is threading.current_thread():
        return
    set_async_exc = _async_exc_function()
    if set_async_exc(ctypes.c_ulong(ident), ctypes.py_object(exc)) > 1:
        # Never happens with a valid id; undo rather than hit several threads.
        set_async_exc(ctypes.c_ulong(ident), None)


def current_run() -> Optional[_Run]:
    return getattr(threading.current_thread(), _ATTR, None)


# -- process-wide hooks ---------------------------------------------------------------------
class _Router:
    """sys.stdout / sys.stderr replacement: a thread that belongs to a run
    writes into that run, every other thread into the original stream.

    Deliberately without `buffer`: yt-dlp writes to `out.buffer` when there is
    one, which would bypass the routing."""

    encoding = "utf-8"
    errors = "replace"
    closed = False

    def __init__(self, name: str, fallback):
        self._name = name
        self._fallback = fallback

    def write(self, text) -> int:
        if not isinstance(text, str):
            text = str(text)
        run = current_run()
        if run is not None:
            run.feed(self._name, text)
            return len(text)
        if self._fallback is None:
            return len(text)
        return self._fallback.write(text)

    def writelines(self, lines) -> None:
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        if current_run() is None and self._fallback is not None:
            try:
                self._fallback.flush()
            except (OSError, ValueError):
                pass

    def isatty(self) -> bool:
        return False

    def writable(self) -> bool:
        return True

    def readable(self) -> bool:
        return False

    def fileno(self) -> int:
        if self._fallback is None:
            raise OSError("no file descriptor")
        return self._fallback.fileno()


_INSTALL_LOCK = threading.Lock()
_installed = False


def install() -> None:
    """Installs the output routing and the thread/process tracking."""
    global _installed
    with _INSTALL_LOCK:
        # Again on every run: something else may have replaced the streams since.
        if not isinstance(sys.stdout, _Router):
            sys.stdout = _Router("out", sys.stdout)
        if not isinstance(sys.stderr, _Router):
            sys.stderr = _Router("err", sys.stderr)
        if _installed:
            return
        _installed = True

        original_start = threading.Thread.start

        def start(self, *args, **kwargs):
            run = current_run()
            if run is not None and getattr(self, _ATTR, None) is None:
                setattr(self, _ATTR, run)
                run.add_thread(self)
            original_start(self, *args, **kwargs)
            own = getattr(self, _ATTR, None)
            if own is not None and own.stopping:
                raise_in(self)

        threading.Thread.start = start

        original_init = subprocess.Popen.__init__

        def popen_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            run = current_run()
            if run is not None:
                run.add_process(self)

        subprocess.Popen.__init__ = popen_init

        # asyncio probes pidfd_open to watch child processes (spotdl runs
        # ffmpeg through asyncio). Android before 12 kills the app on that
        # syscall instead of failing it; the thread-based watcher works
        # everywhere.
        try:
            import asyncio.unix_events as unix_events
        except ImportError:
            unix_events = None
        if unix_events is not None and hasattr(unix_events, "can_use_pidfd"):
            unix_events.can_use_pidfd = lambda: False


# -- tools ------------------------------------------------------------------------------------
def _module_available(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _run_yt_dlp(args: list[str]) -> int:
    import yt_dlp

    yt_dlp.main(list(args))  # always ends with SystemExit
    return 0


# spotdl keeps process-wide state (its Spotify client is a singleton, its
# argument parser reads sys.argv): one run at a time.
_SPOTDL_LOCK = threading.Lock()


def _no_web(*_args, **_kwargs) -> None:
    raise RuntimeError("spotdl's web interface is not available here")


def _prepare_spotdl() -> None:
    # spotdl's console package imports its web interface (FastAPI, uvicorn,
    # pydantic) at import time; those are not installed on Android.
    if "spotdl.console.web" not in sys.modules and not _module_available("fastapi"):
        stub = types.ModuleType("spotdl.console.web")
        stub.web = _no_web
        sys.modules["spotdl.console.web"] = stub
    try:
        from . import spotify_cache

        spotify_cache.install()
    except Exception as exc:  # only a slower start
        print("music-loader: Spotify hash cache not active:", exc, file=sys.stderr)


def check_spotdl() -> None:
    """Imports what every spotDL run needs, so broken packaging fails the start
    with one error instead of every Spotify job."""
    with _SPOTDL_LOCK:
        _prepare_spotdl()
        try:
            import pykakasi
            import spotdl  # noqa: F401

            # spotDL's formatter needs pykakasi's dictionaries (loaded once).
            pykakasi.kakasi()
        except Exception as exc:
            raise RuntimeError(f"spotDL does not load: {type(exc).__name__}: {exc}") from exc


def _run_spotdl(args: list[str]) -> int:
    """`spotdl <args>` without the console entry point's signal handlers, web
    interface and ffmpeg download (written against spotdl 4.5)."""
    with _SPOTDL_LOCK:
        _prepare_spotdl()
        import asyncio

        from spotdl.console.entry_point import OPERATIONS
        from spotdl.download.downloader import Downloader
        from spotdl.utils.arguments import parse_arguments
        from spotdl.utils.config import create_settings
        from spotdl.utils.console import generate_initial_config
        from spotdl.utils.ffmpeg import FFmpegError, is_ffmpeg_installed
        from spotdl.utils.logging import init_logging
        from spotdl.utils.spotify import SpotifyClient

        spotdl_logger = logging.getLogger("spotdl")
        handlers = list(spotdl_logger.handlers)
        saved_argv = sys.argv
        sys.argv = ["spotdl", *args]
        downloader = None
        try:
            generate_initial_config()
            arguments = parse_arguments()
            spotify_settings, downloader_settings, _web_settings = create_settings(arguments)
            init_logging(downloader_settings["log_level"], downloader_settings["log_format"])
            if is_ffmpeg_installed(downloader_settings["ffmpeg"]) is False:
                raise FFmpegError("FFmpeg is not installed")
            SpotifyClient._instance = None
            SpotifyClient.init(**spotify_settings)
            downloader = Downloader(downloader_settings)
            try:
                OPERATIONS[arguments.operation](query=arguments.query, downloader=downloader)
            except Exception:
                logging.getLogger("spotdl.console.entry_point").exception("An error occurred")
                return 1
            return 0
        finally:
            sys.argv = saved_argv
            if downloader is not None:
                try:
                    downloader.progress_handler.close()
                except Exception:
                    pass
                try:
                    # Also ends the loop's executor threads.
                    downloader.loop.close()
                except Exception:
                    pass
                asyncio.set_event_loop(None)
            SpotifyClient._instance = None
            for handler in list(spotdl_logger.handlers):
                if handler not in handlers:
                    spotdl_logger.removeHandler(handler)


_TOOLS: dict[str, Callable[[list[str]], int]] = {
    "yt_dlp": _run_yt_dlp,
    "spotdl": _run_spotdl,
}


def _exit_status(code, run: _Run) -> int:
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    run.feed("err", f"{code}\n")
    return 1


def _tool_main(run: _Run, module: str, args: list[str]) -> None:
    code = 1
    try:
        code = _TOOLS[module](args)
    except SystemExit as exc:
        code = _exit_status(exc.code, run)
    except KeyboardInterrupt:
        code = 130
    except BaseException:
        run.feed("err", traceback.format_exc())
        code = 1
    finally:
        run.finish(code)


def _start(module: str, args: list[str], merge: bool) -> _Run:
    if module not in _TOOLS:
        raise OSError(f"'{module}' cannot run in-process")
    install()
    run = _Run(merge)
    thread = threading.Thread(target=_tool_main, args=(run, module, list(args)),
                              name=f"{module}-run", daemon=True)
    setattr(thread, _ATTR, run)
    run.add_thread(thread)
    thread.start()
    return run


def command_module(cmd: list[str]) -> Optional[str]:
    """The tool module of an in-process command, None for anything else."""
    return cmd[1] if len(cmd) >= 2 and cmd[0] == MARK else None


def run_captured(module: str, args: list[str], timeout: int) -> tuple[int, str, str]:
    try:
        run = _start(module, args, merge=False)
    except OSError as exc:
        return -1, "", str(exc)
    try:
        if not run.done.wait(timeout):
            run.stop_and_wait()
            return -1, "".join(run.captured["out"]), f"command timed out after {timeout}s"
    except BaseException:
        run.stop_and_wait()
        raise
    code = run.returncode if run.returncode is not None else -1
    return code, "".join(run.captured["out"]), "".join(run.captured["err"])


def run_streamed(
    module: str,
    args: list[str],
    on_line: Callable[[str], None],
    on_idle: Optional[Callable[[float], None]],
    idle_interval: float,
    timeout: int,
    should_abort: Optional[Callable[[], bool]],
) -> int:
    from .process import pump_lines

    run = _start(module, args, merge=True)
    try:
        result = pump_lines(run.lines, on_line, on_idle, idle_interval, timeout, should_abort)
    except BaseException:
        # Includes the KeyboardInterrupt of a cancelled job.
        run.stop_and_wait()
        raise
    if result is not None:
        run.stop_and_wait()
        return result
    run.done.wait()
    return run.returncode if run.returncode is not None else -1
