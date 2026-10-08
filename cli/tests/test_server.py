import io
import json
import os
import threading
import time
import urllib.error
import urllib.request

import pytest
from rich.console import Console

from music_loader import server
from music_loader.events import EventDashboard
from music_loader.availability import FailureCategory

TOKEN = "test-token-0123456789abcdef"
SPOTIFY = "https://open.spotify.com/album/4uLU6hMCjMI75M1A2tKUQC"


# -- request validation ----------------------------------------------------------
def test_job_request_splits_validates_and_deduplicates():
    links, options, rejected = server._parse_job_request(
        {"links": [f"{SPOTIFY} junk", f"{SPOTIFY}?si=1"], "options": {"lyrics": "off", "recheck": True}}
    )
    assert [link.url for link in links] == [SPOTIFY]
    assert rejected == ["junk"]
    assert options == {"lyrics": "off", "recheck": True, "soundcloud_reposts": False, "soundcloud_likes": False,
                       "soundcloud_fallback": False, "auto_retry": False}


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

    call.manager = manager
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


# -- failure categories --------------------------------------------------------------
TRACK_A = "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC"
TRACK_B = "https://open.spotify.com/track/5Z9KJZvQzH6PFmb8SNkxuk"
TRACK_C = "https://open.spotify.com/track/7ouMYWpwJ422jRcDASZB7P"
SC_TRACK = "https://soundcloud.com/artist/song"


def failure(url, category, title="", service="spotify"):
    return {"type": "failure", "service": service, "category": category, "url": url, "title": title}


def test_failures_are_recorded_with_their_category():
    job = server.Job(id="1", links=[], options={}, rejected=[])
    for event in (
        failure(TRACK_A, "rate_limited", "A"),
        failure(TRACK_B, "network", "B"),
        failure(TRACK_C, "failed", "C"),
    ):
        server.JobManager._apply(job, event)
    summary = job.summary()
    assert summary["failed_counts"] == {"rate_limited": 1, "network": 1, "failed": 1}
    # Only rate limits and network trouble are worth another try.
    assert summary["retryable_count"] == 2
    items = {item["url"]: item for item in job.detail()["failed_items"]}
    assert items[TRACK_A]["retryable"] is True and items[TRACK_A]["title"] == "A"
    assert items[TRACK_C]["retryable"] is False


def test_unavailable_tracks_and_bad_events_are_never_failures():
    job = server.Job(id="1", links=[], options={}, rejected=[])
    server.JobManager._apply(job, failure(TRACK_A, "unavailable"))          # DRM, preview: never retried
    server.JobManager._apply(job, failure("https://example.com/x", "network"))   # not a Spotify/SoundCloud link
    server.JobManager._apply(job, failure("", "network"))
    server.JobManager._apply(job, failure("-rf", "network"))
    assert job.failed == {}
    # An unknown category is a plain failure, not retryable.
    server.JobManager._apply(job, failure(TRACK_B, "something-new"))
    assert job.summary()["failed_counts"] == {"failed": 1}
    assert job.summary()["retryable_count"] == 0


def test_a_failed_track_is_listed_once_and_the_list_is_capped(monkeypatch):
    job = server.Job(id="1", links=[], options={}, rejected=[])
    server.JobManager._apply(job, failure(TRACK_A, "network"))
    server.JobManager._apply(job, failure(TRACK_A, "rate_limited"))
    assert job.summary()["failed_counts"] == {"rate_limited": 1}
    monkeypatch.setattr(server, "_FAILED_KEPT", 1)
    server.JobManager._apply(job, failure(TRACK_B, "network"))
    assert list(job.failed) == [TRACK_A]


def test_private_links_of_failed_tracks_stay_on_the_server():
    job = server.Job(id="1", links=[], options={}, rejected=[])
    private = "https://soundcloud.com/a/sets/b?secret_token=s-Hidden"
    server.JobManager._apply(job, failure(private, "rate_limited", service="soundcloud"))
    assert "s-Hidden" not in json.dumps(job.detail()) and "s-Hidden" not in json.dumps(job.summary())
    assert "s-Hidden" in job.retryable()[0]["url"]


def test_event_dashboard_reports_failures_with_their_category():
    out = io.StringIO()
    events = EventDashboard(out)
    events.record_failure("spotify", FailureCategory.RATE_LIMITED, TRACK_A, "Artist - Song")
    events.record_failure("spotify", FailureCategory.NETWORK, "")
    (line,) = out.getvalue().splitlines()
    assert json.loads(line) == {"type": "failure", "service": "spotify", "category": "rate_limited",
                                "url": TRACK_A, "title": "Artist - Song"}


# -- retry endpoint ------------------------------------------------------------------
def finish(manager, job_id, status="completed", failures=()):
    """Ends a queued job the way the runner would (the runner is not started here)."""
    with manager._cond:
        job = manager._jobs[job_id]
        if job_id in manager._pending:
            manager._pending.remove(job_id)
        for event in failures:
            manager._apply(job, event)
        job.status = status
        job.finished_at = server._now_ms()
        manager._schedule_retry(job)
        manager._changed()


def queue(api, links=(SPOTIFY,), **options):
    status, created = api("POST", "/jobs", {"links": list(links), "options": options})
    assert status == 201
    return created["job"]["id"]


def test_info_and_state_announce_the_retry_feature(api):
    status, info = api("GET", "/info")
    assert status == 200 and {"retry", "auto_retry"} <= set(info["features"])
    assert set(api.manager._state()["features"]) == set(server.FEATURES)


def test_retry_requires_the_token(api):
    job_id = queue(api)
    finish(api.manager, job_id, failures=[failure(TRACK_A, "network")])
    assert api("POST", f"/jobs/{job_id}/retry", {"scope": "failed"}, token=None)[0] == 401
    assert api("POST", f"/jobs/{job_id}/retry", {"scope": "failed"}, token="wrong-token-0123456789")[0] == 401
    assert len(api("GET", "/jobs")[1]["jobs"]) == 1


def test_retry_unknown_job_and_bad_requests(api):
    assert api("POST", "/jobs/nope/retry", {"scope": "all"})[0] == 404
    job_id = queue(api)
    finish(api.manager, job_id, failures=[failure(TRACK_A, "network")])
    for body in ({}, {"scope": "some"}, {"scope": 1}, [], None):
        assert api("POST", f"/jobs/{job_id}/retry", body)[0] == 400
    assert api("GET", "/jobs")[1]["jobs"].__len__() == 1


@pytest.mark.parametrize("scope", ["all", "failed"])
def test_retry_of_a_job_that_has_not_finished_is_refused(api, scope):
    job_id = queue(api)                                   # still queued
    status, body = api("POST", f"/jobs/{job_id}/retry", {"scope": scope})
    assert status == 409 and "not finished" in body["error"]
    with api.manager._cond:                               # and one that is running
        api.manager._pending.remove(job_id)
        api.manager._jobs[job_id].status = "running"
    assert api("POST", f"/jobs/{job_id}/retry", {"scope": scope})[0] == 409
    assert len(api("GET", "/jobs")[1]["jobs"]) == 1


def test_retry_all_queues_the_original_links_with_the_same_options(api):
    job_id = queue(api, links=[SPOTIFY, SC_TRACK], lyrics="loose", recheck=True)
    finish(api.manager, job_id, failures=[failure(TRACK_A, "network")])
    status, body = api("POST", f"/jobs/{job_id}/retry", {"scope": "all"})
    assert status == 201
    new = body["job"]
    assert new["id"] != job_id and new["status"] == "queued" and new["retry_of"] == job_id
    assert [link["url"] for link in new["links"]] == [SPOTIFY, SC_TRACK]
    assert new["options"]["lyrics"] == "loose" and new["options"]["recheck"] is True
    assert new["attempt"] == 0


def test_retry_all_also_works_for_a_job_without_failures_or_a_cancelled_one(api):
    job_id = queue(api)
    finish(api.manager, job_id, status="cancelled")
    assert api("POST", f"/jobs/{job_id}/retry", {"scope": "all"})[0] == 201


def test_retry_failed_queues_only_the_retryable_tracks(api):
    job_id = queue(api, links=[SPOTIFY, SC_TRACK])
    finish(api.manager, job_id, failures=[
        failure(TRACK_A, "rate_limited"),
        failure(TRACK_B, "network"),
        failure(TRACK_C, "failed"),                       # a permanent error
        failure("https://soundcloud.com/a/drm", "unavailable", service="soundcloud"),   # never retried
    ])
    status, body = api("POST", f"/jobs/{job_id}/retry", {"scope": "failed"})
    assert status == 201
    assert [link["url"] for link in body["job"]["links"]] == [TRACK_A, TRACK_B]
    # The first job is untouched and can be retried again.
    assert api("GET", f"/jobs/{job_id}")[1]["job"]["retryable_count"] == 2


def test_retry_failed_without_retryable_tracks_is_refused(api):
    job_id = queue(api)
    finish(api.manager, job_id, failures=[failure(TRACK_A, "failed")])
    status, body = api("POST", f"/jobs/{job_id}/retry", {"scope": "failed"})
    assert status == 409 and "retried" in body["error"]
    assert len(api("GET", "/jobs")[1]["jobs"]) == 1


def test_retry_keeps_a_private_link_working_but_hidden(api):
    private = "https://soundcloud.com/a/sets/b?secret_token=s-Hidden"
    job_id = queue(api, links=[SC_TRACK])
    finish(api.manager, job_id, failures=[failure(private, "rate_limited", service="soundcloud")])
    status, body = api("POST", f"/jobs/{job_id}/retry", {"scope": "failed"})
    assert status == 201 and "s-Hidden" not in json.dumps(body)
    with api.manager._cond:
        (link,) = api.manager._jobs[body["job"]["id"]].links
    assert "s-Hidden" in link.url                        # the worker still gets the real link


def test_retry_fails_with_503_when_the_queue_is_full(api, monkeypatch):
    job_id = queue(api)
    finish(api.manager, job_id, failures=[failure(TRACK_A, "network")])
    monkeypatch.setattr(server, "_MAX_QUEUED_JOBS", 0)
    assert api("POST", f"/jobs/{job_id}/retry", {"scope": "all"})[0] == 503


# -- automatic retry (fake clock: nothing sleeps) ----------------------------------------
class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def timed(tmp_path):
    clock = Clock()
    return server.JobManager(tmp_path, {}, Console(file=io.StringIO()), clock=clock), clock


def submit(manager, auto_retry=True, attempt=0):
    options = {"lyrics": "off", "auto_retry": auto_retry}
    return manager.submit([server.parse_link(SPOTIFY)], options, [], attempt=attempt)


def end(manager, job, status="completed", failures=(failure(TRACK_A, "rate_limited"),)):
    finish(manager, job.id, status=status, failures=failures)


def test_auto_retry_is_planned_after_the_first_cooldown(timed):
    manager, clock = timed
    job = submit(manager)
    end(manager, job)
    assert job.retry_at == int((clock.now + 15 * 60) * 1000)
    assert job.summary()["retry_at"] == job.retry_at
    assert manager._retry_wait() == server._RETRY_POLL_SECONDS     # re-reads the clock regularly


@pytest.mark.parametrize(
    "attempt, delay",
    [(0, 15 * 60), (1, 30 * 60), (2, 60 * 60), (3, None)],
)
def test_auto_retry_schedule_is_15_30_60_minutes_and_three_attempts(timed, attempt, delay):
    manager, clock = timed
    job = submit(manager, attempt=attempt)
    end(manager, job)
    assert job.retry_at == (None if delay is None else int((clock.now + delay) * 1000))


def test_auto_retry_waits_for_the_cooldown_then_queues_only_the_retryable_tracks(timed):
    manager, clock = timed
    job = submit(manager)
    end(manager, job, failures=[failure(TRACK_A, "rate_limited"), failure(TRACK_B, "failed")])
    clock.advance(15 * 60 - 1)
    assert manager.tick() == []
    assert job.retry_at is not None
    clock.advance(1)
    (new_id,) = manager.tick()
    new = manager._jobs[new_id]
    assert job.retry_at is None
    assert new.attempt == 1 and new.retry_of == job.id and new.status == "queued"
    assert [link.url for link in new.links] == [TRACK_A]
    assert new.options == job.options                     # keeps auto_retry on
    assert manager.tick() == []                           # fires once


def test_auto_retry_chain_ends_after_three_attempts(timed):
    manager, clock = timed
    job = submit(manager)
    waited = []
    for _ in range(4):
        end(manager, job)
        if job.retry_at is None:
            break
        waited.append(job.retry_at / 1000 - clock.now)
        clock.advance(job.retry_at / 1000 - clock.now)
        (new_id,) = manager.tick()
        job = manager._jobs[new_id]
    assert waited == [15 * 60, 30 * 60, 60 * 60]
    assert job.attempt == 3 and job.retry_at is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"auto_retry": False},                                                  # the option is off
        {"status": "cancelled"},                                                # a person stopped it
        {"status": "failed"},                                                   # the worker broke
        {"failures": [failure(TRACK_A, "failed")]},                             # nothing worth retrying
        {"failures": []},
        {"failures": [failure(TRACK_A, "unavailable")]},
    ],
)
def test_auto_retry_is_not_planned_when_it_makes_no_sense(timed, kwargs):
    manager, _ = timed
    job = submit(manager, auto_retry=kwargs.pop("auto_retry", True))
    end(manager, job, **kwargs)
    assert job.retry_at is None


def test_cancelling_the_waiting_job_cancels_the_automatic_retry(timed):
    manager, clock = timed
    job = submit(manager)
    end(manager, job)
    summary = manager.cancel(job.id)
    assert summary["retry_at"] is None and job.status == "completed"
    clock.advance(24 * 3600)
    assert manager.tick() == []


def test_a_manual_retry_replaces_the_automatic_one(timed):
    manager, clock = timed
    job = submit(manager)
    end(manager, job)
    manager.retry(job.id, "failed")
    assert job.retry_at is None
    clock.advance(24 * 3600)
    assert manager.tick() == []


def test_deleting_the_waiting_job_drops_the_automatic_retry(timed):
    manager, clock = timed
    job = submit(manager)
    end(manager, job)
    assert manager.delete(job.id) is True
    clock.advance(24 * 3600)
    assert manager.tick() == []


def test_a_job_waiting_for_its_retry_is_not_trimmed_from_the_history(timed, monkeypatch):
    manager, _ = timed
    monkeypatch.setattr(server, "_MAX_FINISHED_JOBS", 1)
    waiting = submit(manager)
    end(manager, waiting)
    for _ in range(3):
        end(manager, submit(manager, auto_retry=False), failures=[])
    assert waiting.id in manager._jobs


def test_a_full_queue_drops_the_automatic_retry_without_crashing(timed, monkeypatch):
    manager, clock = timed
    job = submit(manager)
    end(manager, job)
    monkeypatch.setattr(server, "_MAX_QUEUED_JOBS", 0)
    clock.advance(15 * 60)
    assert manager.tick() == []
    assert job.retry_at is None


def wait_until(manager, condition, timeout=10.0):
    """Waits for the manager's change notifications; never sleeps blindly."""
    deadline = time.monotonic() + timeout
    while True:
        with manager._cond:
            if condition():
                return
            revision = manager.revision
        remaining = deadline - time.monotonic()
        assert remaining > 0, "timed out waiting for the job runner"
        manager.wait_for_change(revision, min(remaining, 1.0))


def test_the_runner_queues_the_automatic_retry_when_it_is_due(tmp_path, monkeypatch):
    clock = Clock()
    manager = server.JobManager(tmp_path, {}, Console(file=io.StringIO()), clock=clock)
    runs = []

    def fake_execute(self, job):
        runs.append(job.id)
        if len(runs) == 1:
            with self._cond:
                self._apply(job, failure(TRACK_A, "rate_limited"))
                self._apply(job, failure(TRACK_B, "failed"))
        return "completed", None

    monkeypatch.setattr(server.JobManager, "_execute", fake_execute)
    manager.start()
    try:
        first = submit(manager)
        wait_until(manager, lambda: first.retry_at is not None)
        assert runs == [first.id]
        clock.advance(15 * 60)
        (second_id,) = manager.tick()
        wait_until(manager, lambda: manager._jobs[second_id].status == "completed")
        second = manager._jobs[second_id]
        assert runs == [first.id, second_id]
        assert second.attempt == 1 and [link.url for link in second.links] == [TRACK_A]
        assert second.retry_at is None                      # the second run had no failures
    finally:
        manager.shutdown(timeout=5)


def test_auto_retry_option_must_be_a_boolean():
    _, options, _ = server._parse_job_request({"links": [SPOTIFY], "options": {"auto_retry": True}})
    assert options["auto_retry"] is True
    with pytest.raises(server._RequestError):
        server._parse_job_request({"links": [SPOTIFY], "options": {"auto_retry": "yes"}})
