"""Helpers for running external commands with live output handling.

Output is read by a dedicated reader thread instead of `selectors`, because
`selectors` cannot poll pipes on Windows and can also drop already-buffered
lines when the child process exits between two polls.
"""
import subprocess
import threading
import time
from queue import Empty, Queue
from typing import Callable, Optional

from .config import SUBPROCESS_TIMEOUT_SECONDS

LineHandler = Callable[[str], None]
IdleHandler = Callable[[float], None]

_EXIT_GRACE_SECONDS = 60


def run_captured(
    cmd: list[str], timeout: int = SUBPROCESS_TIMEOUT_SECONDS
) -> tuple[int, str, str]:
    """Runs a command and returns ``(returncode, stdout, stderr)``.

    A timeout (or a missing executable) is reported as returncode -1 with an
    explanatory message in stderr, so callers never have to handle exceptions.
    """
    try:
        process = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
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
        return -1, "", f"could not start '{cmd[0]}': {exc}"


def run_streamed(
    cmd: list[str],
    on_line: LineHandler,
    on_idle: Optional[IdleHandler] = None,
    idle_interval: float = 5.0,
    timeout: int = SUBPROCESS_TIMEOUT_SECONDS,
) -> int:
    """Run ``cmd``, forwarding merged stdout/stderr lines to ``on_line``.

    Returns the process exit code, or -1 if the command could not be started
    or had to be killed after exceeding ``timeout``.
    """
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError:
        return -1

    lines: "Queue[Optional[str]]" = Queue()

    def reader() -> None:
        try:
            assert process.stdout is not None
            for line in process.stdout:
                lines.put(line)
        except (OSError, ValueError):
            pass
        finally:
            lines.put(None)

    thread = threading.Thread(target=reader, name="proc-reader", daemon=True)
    thread.start()

    start = time.monotonic()
    last_output = start
    timed_out = False

    try:
        while True:
            if time.monotonic() - start > timeout:
                timed_out = True
                break

            try:
                line = lines.get(timeout=idle_interval)
            except Empty:
                if on_idle is not None:
                    on_idle(time.monotonic() - last_output)
                continue

            if line is None:
                break

            last_output = time.monotonic()
            stripped = line.strip()
            if stripped:
                on_line(stripped)

        if timed_out:
            process.kill()
            process.wait()
            return -1

        process.wait(timeout=_EXIT_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        return -1
    finally:
        if process.stdout is not None:
            try:
                process.stdout.close()
            except OSError:
                pass
        thread.join(timeout=5)

    return process.returncode
