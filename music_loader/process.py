"""Helpers for running external commands with live output handling."""
import selectors
import subprocess
import time
from typing import Callable, Optional

LineHandler = Callable[[str], None]
IdleHandler = Callable[[float], None]

_DEFAULT_TIMEOUT = 6 * 60 * 60


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
    )

    start = time.monotonic()
    last_output = start
    sel = selectors.DefaultSelector()

    def emit(raw_line: str) -> None:
        stripped = raw_line.strip()
        if stripped:
            on_line(stripped)

    def drain() -> None:
        # Data can still sit in Python's buffer after the process exits;
        # without this final read the last lines (often the error message)
        # would be silently dropped.
        try:
            for pending in process.stdout:  # type: ignore[union-attr]
                emit(pending)
        except (OSError, ValueError):
            pass

    try:
        assert process.stdout is not None
        sel.register(process.stdout, selectors.EVENT_READ)

        while True:
            if time.monotonic() - start > timeout:
                _terminate(process)
                return -1

            events = sel.select(timeout=idle_interval)
            if events:
                line = process.stdout.readline()
                if line == "":
                    break
                last_output = time.monotonic()
                emit(line)
            else:
                if on_idle is not None:
                    on_idle(time.monotonic() - last_output)
                if process.poll() is not None:
                    drain()
                    break

        process.wait(timeout=60)
    except subprocess.TimeoutExpired:
        _terminate(process)
        return -1
    except BaseException:
        # Includes KeyboardInterrupt: never leave the child process behind.
        _terminate(process)
        raise
    finally:
        sel.close()
        if process.stdout:
            try:
                process.stdout.close()
            except OSError:
                pass

    return process.returncode
