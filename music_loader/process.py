"""Helpers for running external commands with live output handling."""
import selectors
import subprocess
import time
from typing import Callable, Optional

LineHandler = Callable[[str], None]
IdleHandler = Callable[[float], None]

_DEFAULT_TIMEOUT = 6 * 60 * 60


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
        return -1, stdout, stderr or f"command timed out after {timeout}s"


def run_streamed(
    cmd: list[str],
    on_line: LineHandler,
    on_idle: Optional[IdleHandler] = None,
    idle_interval: float = 5.0,
    timeout: int = _DEFAULT_TIMEOUT,
) -> int:
    """Run ``cmd``, forwarding merged stdout/stderr lines to ``on_line``."""
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

    try:
        assert process.stdout is not None
        sel.register(process.stdout, selectors.EVENT_READ)

        while True:
            if time.monotonic() - start > timeout:
                process.kill()
                process.wait()
                return -1

            events = sel.select(timeout=idle_interval)
            if events:
                line = process.stdout.readline()
                if line == "":
                    break
                last_output = time.monotonic()
                stripped = line.strip()
                if stripped:
                    on_line(stripped)
            else:
                if on_idle is not None:
                    on_idle(time.monotonic() - last_output)
                if process.poll() is not None:
                    break

        process.wait(timeout=60)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        return -1
    finally:
        sel.close()
        if process.stdout:
            process.stdout.close()

    return process.returncode
