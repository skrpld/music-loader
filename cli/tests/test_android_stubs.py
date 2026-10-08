"""The Android-only stand-ins (android/app/src/main/python), run in their own interpreter
because the desktop environment has the real packages under the same names."""
import socket
import subprocess
import sys
from pathlib import Path

STUBS = Path(__file__).resolve().parents[2] / "android" / "app" / "src" / "main" / "python"

_CLIENT = """
import sys
sys.path.insert(0, {stubs!r})
import curl_cffi
import curl_cffi.requests as cr

assert curl_cffi.__version__ == "0.0.0", "the stand-in was not imported"
assert cr.Session().timeout == (10, 30)
session = cr.Session(timeout=1)
try:
    session.get("http://127.0.0.1:{port}/")
except cr.RequestException:
    print("timed out")
"""


def test_curl_cffi_stand_in_does_not_wait_forever():
    # Accepts the connection and never answers: requests alone would wait for ever.
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        result = subprocess.run(
            [sys.executable, "-I", "-c", _CLIENT.format(stubs=str(STUBS), port=port)],
            capture_output=True, text=True, timeout=30,
        )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "timed out"
