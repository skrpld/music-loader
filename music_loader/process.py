"""Runs an external command while streaming its output line by line through a
callback, so the dashboard can be updated in real time instead of only seeing
the result once the process exits.

Also detects periods of silence (no output for a while) and reports them
through an optional `on_idle` callback. This matters because some steps
(spotdl resolving a large artist discography before it prints anything, for
example) can legitimately take a long time without producing output - from
the UI's point of view that's indistinguishable from a genuine hang unless
something says "still working". `on_idle` lets the caller show that.
"""
import selectors
import subprocess
import time
from typing import Callable, Optional

LineHandler = Callable[[str], None]
# Called repeatedly (about every `idle_interval` seconds) while the process
# produces no output, with the number of seconds since the last line.
IdleHandler = Callable[[float], None]

_DEFAULT_TIMEOUT = 6 * 60 * 60  # 6 hours; see config.SUBPROCESS_TIMEOUT_SECONDS


def run_streamed(
    cmd: list[str],
    on_line: LineHandler,
    on_idle: Optional[IdleHandler] = None,
    idle_interval: float = 5.0,
    timeout: int = _DEFAULT_TIMEOUT,
) -> int:
    """Runs `cmd`, calling `on_line` for every output line (stdout+stderr
    merged) and `on_idle` (if given) every `idle_interval` seconds of
    silence. Returns the process return code, or -1 if `timeout` seconds
    were exceeded overall without the process finishing."""
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
                    # EOF - process has closed its output, it's finishing up.
                    break
                last_output = time.monotonic()
                stripped = line.strip()
                if stripped:
                    on_line(stripped)
            else:
                if on_idle is not None:
                    on_idle(time.monotonic() - last_output)
                if process.poll() is not None:
                    # Process exited without a final newline/EOF blip caught above.
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
