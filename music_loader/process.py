"""Helpers for running external commands with live output handling."""
import os
import queue
import subprocess
import threading
import time
from typing import Callable, Mapping, Optional

LineHandler = Callable[[str], None]
IdleHandler = Callable[[float], None]

_DEFAULT_TIMEOUT = 6 * 60 * 60

# spotdl and yt-dlp are Python programs. When their stdout is a pipe instead of
# a terminal, CPython switches to block buffering, so their output only reaches
# us in 4-8 KiB chunks - which for a long-running download means the dashboard
# looks completely silent for minutes even though the child is talking. Forcing
# unbuffered/line-buffered output makes the live parsing actually live.
_CHILD_ENV = {
    "PYTHONUNBUFFERED": "1",
    "PYTHONIOENCODING": "utf-8",
}


def _child_environment(extra: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    env = os.environ.copy()
    env.update(_CHILD_ENV)
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
    env: Optional[Mapping[str, str]] = None,
) -> tuple[int, str, str]:
    """Runs a command and returns ``(returncode, stdout, stderr)``."""
    try:
        process = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            # Never let a child inherit the terminal and block on a prompt.
            stdin=subprocess.DEVNULL,
            env=_child_environment(env),
        )
        return process.returncode, process.stdout, process.stderr
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
    env: Optional[Mapping[str, str]] = None,
) -> int:
    """Run ``cmd``, forwarding merged stdout/stderr lines to ``on_line``.

    Output is consumed by a dedicated reader thread. The previous
    ``selectors``-based loop only read a single line per readiness event,
    so when a child printed a burst of lines the rest stayed stuck in
    Python's buffer until the next event - which for a downloader that goes
    quiet for minutes could mean the important line (the metadata JSON, an
    error) was seen far too late or not at all.

    The child also runs with unbuffered stdout (see ``_CHILD_ENV``), because a
    Python child writing to a pipe otherwise block-buffers its own output and
    nothing arrives until several kilobytes have accumulated.

    ``timeout`` is an *idle* timeout: the child is killed only if it neither
    prints anything nor exits for that long, so a legitimately slow but
    talkative job is never cut off.

    The child process is always terminated before this function returns,
    including when the caller is interrupted (Ctrl+C), so no orphaned
    yt-dlp/spotdl processes keep downloading in the background.
    """
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        # A child that inherits the terminal can silently block on an
        # interactive prompt, which looks exactly like "no output, forever".
        stdin=subprocess.DEVNULL,
        text=True,
        bufsize=1,
        env=_child_environment(env),
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

    try:
        while True:
            if time.monotonic() - last_output > timeout:
                _terminate(process)
                return -1

            try:
                raw_line = lines.get(timeout=idle_interval)
            except queue.Empty:
                if on_idle is not None:
                    on_idle(time.monotonic() - last_output)
                continue

            if raw_line is None:
                break

            last_output = time.monotonic()
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
