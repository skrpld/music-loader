"""Helpers for running external commands with live output handling."""
import os
import queue
import subprocess
import threading
import time
from typing import Callable, Optional

LineHandler = Callable[[str], None]
IdleHandler = Callable[[float], None]

_DEFAULT_TIMEOUT = 6 * 60 * 60


def child_env() -> dict[str, str]:
    """Environment for child processes.

    spotdl and yt-dlp are Python programs: when their stdout is a pipe
    instead of a terminal, Python block-buffers it, so several kilobytes of
    output pile up before anything reaches us. A long job then looks
    completely frozen from the dashboard's point of view, and progress
    lines arrive in useless bursts. PYTHONUNBUFFERED makes the child flush
    every line as it is printed.
    """
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("PYTHONIOENCODING", "utf-8")
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


def run_captured(cmd: list[str], timeout: int = _DEFAULT_TIMEOUT) -> tuple[int, str, str]:
    """Runs a command and returns ``(returncode, stdout, stderr)``."""
    try:
        process = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=child_env(),
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
) -> int:
    """Run ``cmd``, forwarding merged stdout/stderr lines to ``on_line``.

    Output is consumed by a dedicated reader thread. The previous
    ``selectors``-based loop only read a single line per readiness event,
    so when a child printed a burst of lines the rest stayed stuck in
    Python's buffer until the next event - which for a downloader that goes
    quiet for minutes could mean the important line (the metadata JSON, an
    error) was seen far too late or not at all.

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
        text=True,
        bufsize=1,
        env=child_env(),
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
