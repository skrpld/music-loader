import io
import json
import os
import threading
import urllib.error
import urllib.request

import pytest
from rich.console import Console

from music_loader import server

TOKEN = "test-token-0123456789abcdef"
SPOTIFY = "https://open.spotify.com/album/4uLU6hMCjMI75M1A2tKUQC"


# -- request validation ----------------------------------------------------------
def test_job_request_splits_validates_and_deduplicates():
    links, options, rejected = server._parse_job_request(
        {"links": [f"{SPOTIFY} junk", f"{SPOTIFY}?si=1"], "options": {"lyrics": "off", "recheck": True}}
    )
    assert [link.url for link in links] == [SPOTIFY]
    assert rejected == ["junk"]
    assert options == {"lyrics": "off", "recheck": True, "soundcloud_reposts": False, "soundcloud_likes": False, "soundcloud_fallback": False}


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {"links": 5},
        {"links": ["only text"]},
        {"links": [SPOTIFY], "options": {"lyrics": "fuzzy"}},
        {"links": [SPOTIFY], "options": {"recheck": "yes"}},
        {"links": [SPOTIFY] * (server._MAX_LINKS_PER_JOB + 1)},
    ],
)
def test_bad_job_requests_are_refused(body):
    with pytest.raises(server._RequestError):
        server._parse_job_request(body)


# -- token -----------------------------------------------------------------------
@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv(server.TOKEN_ENV, raising=False)
    return tmp_path


def test_generated_token_is_stored_privately_and_reused(config_home):
    token, source = server.load_token(None, renew=False)
    path = config_home / "music-loader" / server.TOKEN_FILENAME
    assert source == str(path)
    assert path.read_text(encoding="utf-8").strip() == token
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600
    assert server.load_token(None, renew=False)[0] == token
    assert server.load_token(None, renew=True)[0] != token


def test_explicit_and_environment_tokens_win(config_home, monkeypatch):
    monkeypatch.setenv(server.TOKEN_ENV, "from-environment-0123456789")
    assert server.load_token(None, renew=False) == ("from-environment-0123456789", server.TOKEN_ENV)
    assert server.load_token(TOKEN, renew=False) == (TOKEN, "--token")
    with pytest.raises(ValueError):
        server.load_token("short", renew=False)


# -- HTTP API (the job runner is not started, jobs stay queued) ----------------------
@pytest.fixture
def api(tmp_path):
    manager = server.JobManager(tmp_path, {}, Console(file=io.StringIO()))
    httpd = server.make_server("127.0.0.1", 0, manager, TOKEN)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}/api/v1"

    def call(method, path, body=None, token=TOKEN):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(base + path, data=data, method=method)
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"null")

    yield call
    httpd.shutdown()
    httpd.server_close()


def test_requests_without_the_right_token_are_refused(api):
    assert api("GET", "/info", token=None)[0] == 401
    assert api("GET", "/info", token="wrong-token-0123456789")[0] == 401


def test_job_lifecycle(api):
    status, info = api("GET", "/info")
    assert status == 200 and info["api"] == server.API_VERSION and info["busy"] is False

    status, created = api("POST", "/jobs", {"links": [SPOTIFY]})
    assert status == 201
    job_id = created["job"]["id"]
    assert created["job"]["status"] == "queued"

    status, listing = api("GET", "/jobs")
    assert status == 200 and [job["id"] for job in listing["jobs"]] == [job_id]

    assert api("DELETE", f"/jobs/{job_id}")[0] == 409
    status, cancelled = api("POST", f"/jobs/{job_id}/cancel")
    assert status == 200 and cancelled["job"]["status"] == "cancelled"
    assert api("DELETE", f"/jobs/{job_id}")[0] == 204
    assert api("GET", f"/jobs/{job_id}")[0] == 404


def test_private_tokens_never_reach_the_client(api):
    status, created = api("POST", "/jobs", {"links": ["https://soundcloud.com/a/sets/b?secret_token=s-Hidden"]})
    assert status == 201
    assert "s-Hidden" not in json.dumps(created)


# -- SoundCloud fallback option ---------------------------------------------------
def test_fallback_option_is_off_by_default_and_must_be_a_boolean():
    _, options, _ = server._parse_job_request({"links": [SPOTIFY], "options": {"soundcloud_fallback": True}})
    assert options["soundcloud_fallback"] is True
    with pytest.raises(server._RequestError):
        server._parse_job_request({"links": [SPOTIFY], "options": {"soundcloud_fallback": "yes"}})


def test_worker_reads_the_fallback_option(tmp_path):
    from music_loader import worker

    assert worker.build_config({"output": str(tmp_path)}).soundcloud_fallback is False
    assert worker.build_config({"output": str(tmp_path), "soundcloud_fallback": True}).soundcloud_fallback is True
    assert worker.build_config({"output": str(tmp_path), "soundcloud_fallback": "yes"}).soundcloud_fallback is False


def test_job_reports_the_unavailable_tracks_file():
    job = server.Job(id="1", links=[], options={}, rejected=[])
    server.JobManager._apply(job, {"type": "stats", "stats": {"soundcloud_tracks_unavailable": 3}})
    server.JobManager._apply(job, {"type": "unavailable_log", "path": "/m/.music-loader-logs/unavailable-1.log"})
    detail = job.detail()
    assert detail["stats"]["soundcloud_tracks_unavailable"] == 3
    assert detail["unavailable_log"] == "/m/.music-loader-logs/unavailable-1.log"
    assert job.error_count == 0
