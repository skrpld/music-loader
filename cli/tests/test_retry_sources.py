"""What the Spotify and SoundCloud pipelines tell the dashboard about failed
tracks (so the server can retry the retryable ones), and the slower pace after
a SoundCloud rate limit."""
import threading

from music_loader import soundcloud as sc
from music_loader import spotify
from music_loader.availability import FailureCategory

TRACK = "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC"


class _Run:
    def __init__(self, refused=False):
        self.refused = refused


def test_spotify_failure_reasons_map_to_categories():
    category = spotify._song_category
    assert category("HTTP Error 429: Too Many Requests", _Run()) is FailureCategory.RATE_LIMITED
    assert category("403 Forbidden", _Run()) is FailureCategory.RATE_LIMITED
    assert category("Read timed out", _Run()) is FailureCategory.NETWORK
    assert category("AudioProviderError: no match found", _Run()) is FailureCategory.FAILED


def test_a_missing_file_after_a_refused_run_is_taken_for_a_refused_song():
    category = spotify._song_category
    reason = "file missing after the download"
    assert category(reason, _Run(refused=True), missing=True) is FailureCategory.RATE_LIMITED
    assert category(reason, _Run(refused=False), missing=True) is FailureCategory.FAILED
    # A reason spotdl gave is judged on its own text.
    assert category("no match found", _Run(refused=True), missing=False) is FailureCategory.FAILED


def test_soundcloud_failure_is_reported_with_the_track_link_and_title():
    seen = []

    class Dash:
        def record_failure(self, kind, category, url, title=""):
            seen.append((kind, category, url, title))

    job = sc.TrackJob({"id": 5, "title": "Song", "webpage_url": "https://soundcloud.com/a/song"})
    sc._note_failure(Dash(), job, FailureCategory.RATE_LIMITED)
    assert seen == [("soundcloud", FailureCategory.RATE_LIMITED, "https://soundcloud.com/a/song", "Song")]


def test_downloads_drop_to_one_at_a_time_after_a_rate_limit():
    gate = sc._RateGate()
    limiter = sc._Limiter(3, gate)
    abort = threading.Event()
    assert limiter.limit == 3
    assert all(limiter.acquire(abort) for _ in range(3))
    for _ in range(3):
        limiter.release()
    gate.trip()
    gate.relax()                       # a good download later does not restore the pace...
    assert limiter.limit == 1
    assert limiter.acquire(abort)
    abort.set()
    assert limiter.acquire(abort) is False       # ...and a second one waits, or gives up on abort
    limiter.release()
    gate.reset_throttle()              # ...but the next run starts at full speed
    assert limiter.limit == 3


def test_android_spotify_credentials_reach_the_worker_config_only_when_both_are_set(monkeypatch, tmp_path):
    from music_loader import android, worker

    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("SPOTIFY_CLIENT_SECRET", raising=False)
    spec = {"output": str(tmp_path)}
    android.set_spotify_credentials(" my-id ", " my-secret ")
    config = worker.build_config(spec)
    assert (config.spotify_client_id, config.spotify_client_secret) == ("my-id", "my-secret")
    android.set_spotify_credentials("my-id", "")           # half a pair is no pair
    config = worker.build_config(spec)
    assert config.spotify_client_id is None and config.spotify_client_secret is None
    android.set_spotify_credentials("", "")
    assert worker.build_config(spec).spotify_client_secret is None


def test_worker_announces_the_official_api_once_without_the_secret(monkeypatch, tmp_path):
    from music_loader import cli, worker
    from music_loader.events import EventDashboard

    monkeypatch.setattr(cli, "process_links", lambda links, config, dashboard: None)
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "my-id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "my-very-secret")
    import io
    out = io.StringIO()
    spec = {"output": str(tmp_path), "links": ["https://open.spotify.com/album/4uLU6hMCjMI75M1A2tKUQC",
                                                "https://open.spotify.com/album/5Z9KJZvQzH6PFmb8SNkxuk"]}
    worker.run(spec, EventDashboard(out))
    text = out.getvalue()
    assert text.count("official Spotify API") == 1
    assert "my-very-secret" not in text
