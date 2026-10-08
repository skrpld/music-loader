"""Spotify pipeline: the reachability check in front of spotDL."""
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from music_loader import spotify
from music_loader.config import AppConfig
from music_loader.links import parse_link


@pytest.fixture(autouse=True)
def _direct_connections(monkeypatch):
    """The tests talk to 127.0.0.1: never through a proxy of the environment."""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _Dashboard:
    def __init__(self):
        self.errors: list[str] = []
        self.finished = 0

    def log(self, message): pass
    def start_file(self, *args, **kwargs): pass
    def update_file(self, *args, **kwargs): pass
    def finish_file(self, *args, **kwargs): self.finished += 1

    def log_error(self, source, message):
        self.errors.append(f"{source}: {message}")


class _Forbidden(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(403)
        self.end_headers()

    def log_message(self, *args):
        pass


def test_unreachable_reports_a_server_that_never_answers():
    # Accepts the connection and says nothing, like a blocked route.
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        url = f"http://127.0.0.1:{server.getsockname()[1]}/"
        found = spotify._unreachable((url,), timeout=0.5, attempts=2)
    assert found is not None
    assert found[0] == url
    assert found[1]


def test_unreachable_accepts_any_http_answer():
    server = HTTPServer(("127.0.0.1", 0), _Forbidden)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/"
        assert spotify._unreachable((url,), timeout=5, attempts=1) is None
    finally:
        server.shutdown()
        server.server_close()


def test_unreachable_names_the_first_dead_url():
    dead = f"http://127.0.0.1:{_closed_port()}/"
    found = spotify._unreachable((dead, dead + "other"), timeout=0.5, attempts=1)
    assert found is not None
    assert found[0] == dead


def test_unreachable_treats_a_garbled_answer_as_unreachable():
    # A captive portal or a broken proxy answering with something that is not HTTP.
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)

        def garble():
            conn, _ = server.accept()
            conn.sendall(b"this is not http\r\n\r\n")
            conn.close()

        threading.Thread(target=garble, daemon=True).start()
        url = f"http://127.0.0.1:{server.getsockname()[1]}/"
        assert spotify._unreachable((url,), timeout=2, attempts=1) is not None


def test_download_stops_before_spotdl_when_spotify_is_unreachable(monkeypatch, tmp_path: Path):
    asked = []

    def unreachable(urls, *args, **kwargs):
        asked.append(urls)
        return urls[0], "timed out"

    def never(*args, **kwargs):
        raise AssertionError("spotdl must not start")

    monkeypatch.setattr(spotify, "tool_command", lambda name: ["spotdl"])
    monkeypatch.setattr(spotify, "spotdl_version", lambda command: (4, 5, 2))
    monkeypatch.setattr(spotify, "_unreachable", unreachable)
    monkeypatch.setattr(spotify, "run_streamed", never)
    dashboard = _Dashboard()
    config = AppConfig.from_output_dir(tmp_path)
    link = parse_link("https://open.spotify.com/track/2IdsniWGsU5oGhvyWDa4qG")

    assert spotify.download_spotify(link, config, dashboard) is False

    assert asked == [spotify._BUILTIN_CLIENT_URLS]
    assert len(dashboard.errors) == 1
    assert "Cannot reach https://open.spotify.com/ (timed out)" in dashboard.errors[0]
    assert "VPN" in dashboard.errors[0]
    assert dashboard.finished == 1


def test_official_api_credentials_check_the_official_hosts(monkeypatch, tmp_path: Path):
    asked = []
    monkeypatch.setattr(spotify, "tool_command", lambda name: ["spotdl"])
    monkeypatch.setattr(spotify, "spotdl_version", lambda command: (4, 5, 2))
    monkeypatch.setattr(spotify, "_unreachable", lambda urls, *a, **k: asked.append(urls) or (urls[0], "x"))
    config = AppConfig.from_output_dir(tmp_path)
    config.spotify_client_id, config.spotify_client_secret = "id", "secret"
    link = parse_link("https://open.spotify.com/track/2IdsniWGsU5oGhvyWDa4qG")

    spotify.download_spotify(link, config, _Dashboard())

    assert asked == [spotify._OFFICIAL_API_URLS]


@pytest.mark.parametrize("first_output", [False, True])
def test_slow_resolve_hint_is_shown_once(first_output):
    messages: list[str] = []

    class Dash(_Dashboard):
        def log(self, message):
            messages.append(message)

    run = spotify._Run(Dash(), Path("/nonexistent"), count_tracks=False)
    run.first_output = first_output
    for seconds in (20, 40, 65, 85, 105):
        run.last_heartbeat = 0.0  # every call passes the throttle
        run.on_idle(seconds)
    hints = [m for m in messages if "Still resolving" in m]
    assert len(hints) == 1
    assert "65s" in hints[0]
