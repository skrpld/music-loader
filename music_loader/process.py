"""Helpers for running external commands with live output handling."""
import importlib.util
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from typing import Callable, Optional

LineHandler = Callable[[str], None]
IdleHandler = Callable[[float], None]
AbortCheck = Callable[[], bool]

_DEFAULT_TIMEOUT = 6 * 60 * 60

# Tools that are Python packages are run with the interpreter music-loader is
# running on. A bare `yt-dlp` from PATH may be a different, outdated install
# (a distro package) than the one pip installed next to music-loader - and
# SoundCloud breaks old yt-dlp versions regularly.
_PYTHON_TOOLS = {
    "yt-dlp": "yt_dlp",
    "spotdl": "spotdl",
}


def module_available(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def tool_command(name: str) -> list[str] | None:
    """Command prefix for an external tool, or None when it is not installed."""
    module = _PYTHON_TOOLS.get(name)
    if module and module_available(module):
        return [sys.executable, "-m", module]
    path = shutil.which(name)
    return [path] if path else None


def child_env(extra: Optional[dict[str, str]] = None) -> dict[str, str]:
    """Environment for child processes.

    spotdl and yt-dlp are Python programs: when their stdout is a pipe
    instead of a terminal, Python block-buffers it, so several kilobytes of
    output pile up before anything reaches us. PYTHONUNBUFFERED makes the
    child flush every line as it is printed. The output is always UTF-8 so a
    Cyrillic title survives on systems whose locale encoding is not.

    spotdl prints through `rich`, which wraps lines at 80 columns when it is
    not attached to a terminal; a wide virtual terminal keeps one event per
    line, which the progress parsing relies on.
    """
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["COLUMNS"] = "4000"
    if extra:
        env.update(extra)
    return env


def _terminate(process: subprocess.Popen) -> None:
    """Stops a child process without leaving it running in the background."""
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
    except OSError:
        pass


def run_captured(
    cmd: list[str],
    timeout: int = _DEFAULT_TIMEOUT,
    env: Optional[dict[str, str]] = None,
) -> tuple[int, str, str]:
    """Runs a command and returns ``(returncode, stdout, stderr)``."""
    try:
        process = subprocess.run(
            cmd,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=child_env(env),
        )
        return process.returncode, process.stdout or "", process.stderr or ""
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", "replace")
        return -1, stdout, stderr or f"command timed out after {timeout}s"
    except OSError as exc:
        return -1, "", str(exc)


def run_streamed(
    cmd: list[str],
    on_line: LineHandler,
    on_idle: Optional[IdleHandler] = None,
    idle_interval: float = 5.0,
    timeout: int = _DEFAULT_TIMEOUT,
    should_abort: Optional[AbortCheck] = None,
    env: Optional[dict[str, str]] = None,
) -> int:
    """Run ``cmd``, forwarding merged stdout/stderr lines to ``on_line``.

    Output is consumed by a dedicated reader thread, so a burst of lines is
    never left sitting in a buffer waiting for the next readiness event.
    Output is decoded as UTF-8 with replacement: decoding with the locale
    encoding (cp1251 on a Russian Windows) garbled every Cyrillic title and
    could even stop the reader on an undecodable byte.

    ``timeout`` is an *idle* timeout: the child is killed only if it neither
    prints anything nor exits for that long, so a legitimately slow but
    talkative job is never cut off.

    ``should_abort`` is polled regularly; once it returns True the child is
    terminated and -2 is returned. Worker threads never see Ctrl+C
    themselves, so this is how an interrupted run stops their children.

    The child process is always terminated before this function returns,
    including when the caller is interrupted (Ctrl+C), so no orphaned
    yt-dlp/spotdl processes keep downloading in the background.
    """
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=child_env(env),
    )

    lines: "queue.Queue[Optional[str]]" = queue.Queue()

    def reader() -> None:
        try:
            assert process.stdout is not None
            for raw_line in process.stdout:
                lines.put(raw_line)
        except (OSError, ValueError):
            pass
        finally:
            lines.put(None)

    thread = threading.Thread(target=reader, name="proc-reader", daemon=True)
    thread.start()

    last_output = time.monotonic()
    last_idle_report = last_output
    poll = min(idle_interval, 0.5) if should_abort is not None else idle_interval

    try:
        while True:
            if should_abort is not None and should_abort():
                _terminate(process)
                return -2
            now = time.monotonic()
            if now - last_output > timeout:
                _terminate(process)
                return -1

            try:
                raw_line = lines.get(timeout=poll)
            except queue.Empty:
                now = time.monotonic()
                if on_idle is not None and now - last_idle_report >= idle_interval:
                    last_idle_report = now
                    on_idle(now - last_output)
                continue

            if raw_line is None:
                break

            last_output = last_idle_report = time.monotonic()
            stripped = raw_line.strip()
            if stripped:
                on_line(stripped)

        process.wait(timeout=60)
    except subprocess.TimeoutExpired:
        _terminate(process)
        return -1
    except BaseException:
        # Includes KeyboardInterrupt: never leave the child process behind.
        _terminate(process)
        raise
    finally:
        if process.stdout:
            try:
                process.stdout.close()
            except OSError:
                pass
        thread.join(timeout=5)

    return process.returncode
