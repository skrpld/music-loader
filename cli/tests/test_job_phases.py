"""A job resolves every link first (in parallel), then downloads the tracks of
all links together; the track total is final before the first download."""
import io
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from music_loader import cli, server
from music_loader import soundcloud as sc
from music_loader import spotify
from music_loader.config import AppConfig
from music_loader.events import EventDashboard
from music_loader.links import parse_link
from music_loader.ui import PHASE_DOWNLOADING, PHASE_RESOLVING, Dashboard

SP_ALBUM = "https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy"
SP_TRACK = "https://open.spotify.com/track/2IdsniWGsU5oGhvyWDa4qG"
SC_SET = "https://soundcloud.com/artist/sets/album"
SC_TRACK = "https://soundcloud.com/artist/song"


@dataclass
class Plan:
    link: object
    tracks: list[str] = field(default_factory=list)
    resolved: bool = True


class FakeBatch:
    """Stands in for SpotifyBatch / SoundCloudBatch."""

    def __init__(self, service, dashboard, tracks, fail=(), barrier=None, abort=None):
        self.service = service
        self.dashboard = dashboard
        self.tracks = tracks
        self.fail = set(fail)
        self.barrier = barrier
        self.abort = abort
        self.downloaded: list[list[Plan]] = []
        self.total_at_download: int | None = None

    def resolve(self, link, slot=0):
        if self.barrier is not None:
            self.barrier.wait(timeout=5)
        if link.url in self.fail:
            raise RuntimeError("listing broke")
        return Plan(link, list(self.tracks[link.url]))

    @staticmethod
    def track_count(plans):
        return len({track for plan in plans for track in plan.tracks})

    def download(self, plans, *_lyrics):
        stats = self.dashboard.stats
        self.total_at_download = stats.spotify_tracks_total + stats.soundcloud_tracks_total
        self.downloaded.append(plans)
        for _ in {track for plan in plans for track in plan.tracks}:
            self.dashboard.record_track(self.service, "done")
        return [True for _ in plans]


def run_job(monkeypatch, tmp_path, links, tracks, fail=(), barrier=None):
    stream = io.StringIO()
    dashboard = EventDashboard(stream)
    made: dict[str, FakeBatch] = {}

    def factory(service):
        def make(config, dash, *args):
            made[service] = FakeBatch(service, dash, tracks, fail, barrier, args[-1])
            return made[service]
        return make

    monkeypatch.setattr(cli, "SpotifyBatch", factory("spotify"))
    monkeypatch.setattr(cli, "SoundCloudBatch", factory("soundcloud"))
    config = AppConfig.from_output_dir(tmp_path)
    config.lyrics_enabled = False
    cli.process_links([parse_link(url) for url in links], config, dashboard)
    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    return dashboard, made, events


def test_total_is_final_and_deduplicated_before_the_first_download(monkeypatch, tmp_path):
    tracks = {
        SP_ALBUM: ["sp1", "sp2", "sp3"],
        SP_TRACK: ["sp2"],                       # also on the album
        SC_SET: ["sc1", "sc2"],
        SC_TRACK: ["sc3"],
    }
    dashboard, made, events = run_job(monkeypatch, tmp_path, list(tracks), tracks)

    assert made["spotify"].total_at_download == 6
    assert made["soundcloud"].total_at_download == 6
    # One download run per service, with every link of it.
    assert [len(plans) for plans in made["spotify"].downloaded] == [2]
    assert [len(plans) for plans in made["soundcloud"].downloaded] == [2]
    # In the event stream the totals come before the first track outcome.
    stats = [event["stats"] for event in events if event["type"] == "stats"]
    first_done = next(i for i, s in enumerate(stats) if s["spotify_tracks_done"] + s["soundcloud_tracks_done"])
    assert stats[first_done - 1]["spotify_tracks_total"] == 3
    assert stats[first_done - 1]["soundcloud_tracks_total"] == 3
    s = dashboard.stats
    assert s.spotify_tracks_done == s.spotify_tracks_total == 3
    assert s.soundcloud_tracks_done == s.soundcloud_tracks_total == 3
    assert (s.spotify_ok, s.soundcloud_ok) == (2, 2)


def test_links_are_resolved_in_parallel(monkeypatch, tmp_path):
    tracks = {SC_SET: ["a"], SC_TRACK: ["b"], SP_ALBUM: ["c"]}
    # Every resolve waits until all three run at the same time.
    barrier = threading.Barrier(3)
    dashboard, made, _ = run_job(monkeypatch, tmp_path, list(tracks), tracks, barrier=barrier)

    assert not barrier.broken
    assert dashboard.stats.soundcloud_tracks_done == 2
    assert dashboard.stats.spotify_tracks_done == 1


def test_a_link_that_cannot_be_resolved_does_not_block_the_others(monkeypatch, tmp_path):
    tracks = {SC_SET: ["a", "b"], SC_TRACK: ["c"], SP_TRACK: ["d"]}
    dashboard, made, events = run_job(monkeypatch, tmp_path, list(tracks), tracks, fail={SC_SET})

    assert [plan.link.url for plan in made["soundcloud"].downloaded[0]] == [SC_TRACK]
    s = dashboard.stats
    assert (s.soundcloud_ok, s.soundcloud_fail, s.spotify_ok) == (1, 1, 1)
    assert s.soundcloud_tracks_total == s.soundcloud_tracks_done == 1
    assert any("Unexpected error while resolving" in e.get("text", "") for e in events if e["type"] == "log")
    queue = [event for event in events if event["type"] == "queue"]
    assert queue[0] == {"type": "queue", "completed": 0, "total": 3, "phase": PHASE_RESOLVING}
    assert {"type": "queue", "completed": 3, "total": 3, "phase": PHASE_RESOLVING} in queue
    assert queue[-1] == {"type": "queue", "completed": 3, "total": 3, "phase": PHASE_DOWNLOADING}


def test_ctrl_c_while_resolving_stops_the_resolves_in_flight(monkeypatch, tmp_path):
    started = threading.Event()
    stopped: list[str] = []

    class Waiting(FakeBatch):
        def resolve(self, link, slot=0):
            started.set()
            # A resolve in flight: a spotdl/yt-dlp run polling the abort flag.
            if not self.abort.wait(timeout=10):
                raise AssertionError("never told to stop")
            stopped.append(link.url)
            raise sc.DownloadAborted()

    made: list[Waiting] = []

    def make(config, dash, *args):
        made.append(Waiting("soundcloud", dash, {}, abort=args[-1]))
        return made[-1]

    monkeypatch.setattr(cli, "SpotifyBatch", make)
    monkeypatch.setattr(cli, "SoundCloudBatch", make)

    def interrupted(future):
        started.wait(timeout=5)
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "wait_future", interrupted)
    config = AppConfig.from_output_dir(tmp_path)
    config.lyrics_enabled = False
    links = [parse_link(SC_SET), parse_link(SC_TRACK)]

    with pytest.raises(KeyboardInterrupt):
        cli.process_links(links, config, EventDashboard(io.StringIO()))

    assert made[0].abort.is_set()
    assert stopped                                  # the pool waited for them to stop
    assert not any(batch.downloaded for batch in made)


# -- the services ---------------------------------------------------------------------------------
def _sc_job(track_id):
    return sc.TrackJob(info={"id": track_id, "webpage_url": f"https://soundcloud.com/a/{track_id}"})


def test_soundcloud_batch_counts_and_downloads_each_track_once_and_judges_each_link(tmp_path, monkeypatch):
    config = AppConfig.from_output_dir(tmp_path / "Music")
    config.ensure_dirs()
    dashboard = EventDashboard(io.StringIO())
    batch = sc.SoundCloudBatch(config, dashboard)
    monkeypatch.setattr(sc, "tool_command", lambda name: ["yt-dlp"])
    ctx = batch._context()
    plans = [
        sc.SoundCloudPlan(parse_link(SC_SET), [_sc_job("1"), _sc_job("2")], resolved=True),
        sc.SoundCloudPlan(parse_link(SC_TRACK), [_sc_job("2")], resolved=True),
        sc.SoundCloudPlan(parse_link("https://soundcloud.com/artist/other"), [_sc_job("3")], resolved=True),
    ]
    assert sc.SoundCloudBatch.track_count(plans) == 3

    seen: list[list[str]] = []
    monkeypatch.setattr(sc, "_precheck", lambda jobs, c: None)

    def pipeline(jobs, c):
        seen.append([job.track_id for job in jobs])
        c.fail(next(job for job in jobs if job.track_id == "3"))

    monkeypatch.setattr(sc, "_run_pipeline", pipeline)

    assert batch.download(plans) == [True, True, False]
    assert seen == [["1", "2", "3"]]
    assert ctx.failed_ids == {"3"}


def test_soundcloud_partly_listed_link_still_downloads_but_fails(tmp_path, monkeypatch):
    config = AppConfig.from_output_dir(tmp_path / "Music")
    config.ensure_dirs()
    batch = sc.SoundCloudBatch(config, EventDashboard(io.StringIO()))
    monkeypatch.setattr(sc, "tool_command", lambda name: ["yt-dlp"])

    def discover(link, ctx):
        ctx.fail()                                   # e.g. the likes could not be listed
        return sc.Discovery([_sc_job("1")], "")

    monkeypatch.setattr(sc, "discover", discover)
    monkeypatch.setattr(sc, "_precheck", lambda jobs, c: None)
    monkeypatch.setattr(sc, "_run_pipeline", lambda jobs, c: None)
    plan = batch.resolve(parse_link("https://soundcloud.com/artist"), slot=2)

    assert plan.resolved and plan.partial
    assert not batch._ctx.had_failure                # the shared state is untouched
    assert batch.download([plan]) == [False]


def test_spotify_batch_counts_each_song_once_and_judges_each_link(tmp_path, monkeypatch):
    def song(url):
        return spotify._Song({"url": url}, None)

    config = AppConfig.from_output_dir(tmp_path)
    batch = spotify.SpotifyBatch(config, EventDashboard(io.StringIO()))
    batch._tool = spotify._Tool(["spotdl"], [], {})
    plans = [
        spotify.SpotifyPlan(parse_link(SP_ALBUM), [song("u1"), song("u2")], resolved=True),
        spotify.SpotifyPlan(parse_link(SP_TRACK), [song("u2")], resolved=True),
        spotify.SpotifyPlan(parse_link("https://open.spotify.com/track/0000000000000000000000"),
                            [song("u3")], resolved=True),
    ]
    assert spotify.SpotifyBatch.track_count(plans) == 3
    downloaded: list[list[str]] = []

    def fake_download(songs, lyrics):
        downloaded.append([s.url for s in songs])
        return {"u2"}

    monkeypatch.setattr(batch, "_download", fake_download)
    assert batch.download(plans) == [False, False, True]
    assert downloaded == [["u1", "u2", "u3"]]

    monkeypatch.setattr(batch, "_download", lambda songs, lyrics: None)     # spotdl itself failed
    assert batch.download(plans) == [False, False, False]


def test_spotify_resolve_stops_when_the_job_is_cancelled(tmp_path, monkeypatch):
    calls = []

    def run_streamed(command, on_line, **kwargs):
        calls.append(kwargs["should_abort"])
        return -2 if kwargs["should_abort"]() else 0

    monkeypatch.setattr(spotify, "tool_command", lambda name: ["spotdl"])
    monkeypatch.setattr(spotify, "spotdl_version", lambda command: (4, 5, 2))
    monkeypatch.setattr(spotify, "_unreachable", lambda *a, **k: None)
    monkeypatch.setattr(spotify, "run_streamed", run_streamed)
    batch = spotify.SpotifyBatch(AppConfig.from_output_dir(tmp_path), EventDashboard(io.StringIO()))
    batch.abort.set()

    with pytest.raises(KeyboardInterrupt):
        batch.resolve(parse_link(SP_TRACK))
    assert calls and calls[0]()


def test_spotify_connection_is_checked_once_and_fails_every_spotify_link(tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(spotify, "tool_command", lambda name: ["spotdl"])
    monkeypatch.setattr(spotify, "spotdl_version", lambda command: (4, 5, 2))
    monkeypatch.setattr(spotify, "_unreachable", lambda urls, *a, **k: asked.append(urls) or (urls[0], "x"))
    stream = io.StringIO()
    batch = spotify.SpotifyBatch(AppConfig.from_output_dir(tmp_path), EventDashboard(stream))

    plans = [batch.resolve(parse_link(SP_ALBUM)), batch.resolve(parse_link(SP_TRACK))]

    assert len(asked) == 1
    assert not any(plan.resolved for plan in plans)
    failures = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [e["url"] for e in failures if e["type"] == "failure"] == [SP_ALBUM, SP_TRACK]


# -- progress reporting -----------------------------------------------------------------------------
def test_both_dashboards_take_the_queue_phase():
    from rich.console import Console

    terminal = Dashboard(Console(file=io.StringIO()), "x", "y")
    terminal.set_queue(1, 3, PHASE_RESOLVING)
    assert terminal.queue_progress.tasks[0].fields["label"].startswith("Resolve")
    terminal.set_queue(1, 3)
    assert terminal.queue_progress.tasks[0].fields["label"].startswith("Queue")

    stream = io.StringIO()
    EventDashboard(stream).set_queue(1, 3, PHASE_RESOLVING)
    assert json.loads(stream.getvalue()) == {"type": "queue", "completed": 1, "total": 3, "phase": "resolving"}


@pytest.mark.parametrize("phase, kept", [("resolving", "resolving"), ("downloading", "downloading"),
                                         (None, "downloading"), ("bogus", "downloading")])
def test_server_keeps_the_queue_phase(phase, kept):
    job = server.Job(id="abc", links=[parse_link(SC_TRACK)], options={}, rejected=[])
    event = {"type": "queue", "completed": 2, "total": 5}
    if phase is not None:
        event["phase"] = phase
    server.JobManager._apply(job, event)
    assert job.queue == {"completed": 2, "total": 5, "phase": kept}
    assert server._job_from_state({**job.state(), "status": "completed"}, None).queue["phase"] == kept


def test_dashboards_have_the_same_public_methods():
    def public(cls):
        return {name for name in vars(cls) if not name.startswith("_") and callable(getattr(cls, name))}

    assert public(Dashboard) - {"stats"} >= public(EventDashboard) - {"emit"}
    assert public(EventDashboard) - {"emit"} >= public(Dashboard) - {"stats"}


def test_cli_single_link_helpers_still_work(tmp_path: Path, monkeypatch):
    """download_spotify / download_soundcloud remain one-link shortcuts."""
    monkeypatch.setattr(sc, "tool_command", lambda name: None)
    config = AppConfig.from_output_dir(tmp_path)
    assert sc.download_soundcloud(parse_link(SC_TRACK), config, EventDashboard(io.StringIO())) is False
