"""Runs an external command while streaming its output line by line through a
callback, so the dashboard can be updated in real time instead of only seeing
the result once the process exits."""
import subprocess
from typing import Callable

LineHandler = Callable[[str], None]


def run_streamed(cmd: list[str], on_line: LineHandler, timeout: int = 3600) -> int:
    """Runs `cmd`, calling `on_line` for every output line (stdout+stderr merged).
    Returns the process return code, or -1 if `timeout` seconds were exceeded."""
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    try:
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.strip()
            if line:
                on_line(line)
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        return -1
    finally:
        if process.stdout:
            process.stdout.close()

    return process.returncode
