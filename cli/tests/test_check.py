"""Library check (check.py): the rules, rule selection, availability and its
cache, the report and its log file, --fix, the command line, the worker and
the server's check jobs."""
import io
import json
import threading

import pytest

pytest.importorskip("mutagen")
from mutagen.id3 import ID3, TIT2, TPE1, WOAS  # noqa: E402
from rich.console import Console  # noqa: E402

from music_loader import check  # noqa: E402
from music_loader import cli  # noqa: E402
from music_loader import server  # noqa: E402
from music_loader import soundcloud as sc  # noqa: E402
from music_loader import spotify  # noqa: E402
from music_loader import tags as tg  # noqa: E402
from music_loader import worker  # noqa: E402
from music_loader.accounts import KIND_OWN, KIND_REUPLOADER  # noqa: E402
from music_loader.artists import PRIORITY_SPOTIFY, get_registry  # noqa: E402
from music_loader.config import (  # noqa: E402
    ARTISTS_FILENAME,
    AVAILABILITY_CACHE_SECONDS,
    EXISTING_ALL,
    FORMAT_VERSION,
    LOGS_DIRNAME,
    LYRICS_ATTEMPTS_FILENAME,
    SPOTIFY_LYRICS_ATTEMPTS_FILENAME,
    AppConfig,
)
from music_loader.events import EventDashboard  # noqa: E402
from music_loader.lyrics import get_attempts  # noqa: E402
from music_loader.soundcloud_index import get_index  # noqa: E402

SPOTIFY_ID = "4uLU6hMCjMI75M1A2tKUQC"
SPOTIFY_URL = f"https://open.spotify.com/track/{SPOTIFY_ID}"
LOCAL = tuple(rule for rule in check.RULES if rule != "available")


class Events:
    """The dashboard methods the check and the fix call, recorded."""

    def __init__(self):
        self.lines: list[str] = []
        self.errors: list[str] = []
        self.report = None

    def log(self, text):
        self.lines.append(text)

    def log_error(self, source, text):
        self.errors.append(text)

    def start_file(self, label, slot=0):
        pass

    def update_file(self, percent=None, label=None, speed=None, eta=None, slot=0):
        pass

    def finish_file(self, slot=0):
        pass

    def record_check(self, report):
        self.report = report

    def add_tracks_total(self, *args):
        pass

    def record(self, *args, **kwargs):
        pass


@pytest.fixture
def library(tmp_path):
    config = AppConfig.from_output_dir(tmp_path / "Music")
    config.ensure_dirs()
    config.lyrics_enabled = False
    return config


def _track(path, artists=("Alpha",), title="Song", soundcloud_id="", spotify=False, cover=True,
           lyrics=False):
    """A file as music-loader writes it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    tags = tg.TrackTags(title=title, artists=list(artists), album="Alb", album_artist=artists[0],
                        soundcloud_id=soundcloud_id)
    tg.write_tags(path, tags, b"jpeg" if cover else None)
    if spotify:
        tg.bind_ids(path, spotify_url=SPOTIFY_URL)
    if lyrics:
        tg.embed_lyrics(path, "words")
    return path


def _foreign(path, url=None):
    """A file of another tool: no ids, no format."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    id3 = ID3()
    id3.add(TIT2(encoding=3, text="Other"))
    id3.add(TPE1(encoding=3, text="Beta"))
    if url:
        id3.add(WOAS(url=url))
    id3.save(str(path), v2_version=4)
    return path


def _kinds(report):
    return {(problem.kind, problem.track.relative, problem.fixable) for problem in report.problems}


# -- rules ------------------------------------------------------------------------------------------
def test_a_current_file_has_no_problems(library):
    _track(library.soundcloud_dir / "A" / "song.mp3", soundcloud_id="1", lyrics=True)
    report = check.scan(library, LOCAL)
    assert report.tracks == 1 and report.problems == []
    assert report.fix_note() == "No problems found"


def test_id_rule_binds_what_is_recognised(library):
    _foreign(library.music_dir / "unknown.mp3")
    _foreign(library.music_dir / "Spotify" / "song.mp3", url=SPOTIFY_URL)
    indexed = _foreign(library.soundcloud_dir / "A" / "old.mp3")
    get_index(library.soundcloud_dir).add({"id": "5", "title": "Other", "uploader": "Beta"}, indexed)
    report = check.scan(library, ("id",))
    assert _kinds(report) == {
        ("no_id", "unknown.mp3", False),
        ("no_id", "Spotify/song.mp3", True),
        ("no_id", "SoundCloud/A/old.mp3", True),
    }
    spotify_track = next(p.track for p in report.problems if p.track.service == "spotify")
    assert spotify_track.link == SPOTIFY_URL


def test_files_without_a_track_only_count_as_not_linked(library):
    _foreign(library.music_dir / "unknown.mp3")
    report = check.scan(library, ("id", "artist", "format"))
    assert [problem.kind for problem in report.problems] == ["no_id"]


def test_cover_rule(library):
    _track(library.soundcloud_dir / "A" / "with.mp3", soundcloud_id="1")
    _track(library.soundcloud_dir / "A" / "without.mp3", soundcloud_id="2", cover=False)
    assert _kinds(check.scan(library, ("cover",))) == {("no_cover", "SoundCloud/A/without.mp3", True)}


def test_lyrics_rule(library):
    library.lyrics_enabled = True
    folder = library.soundcloud_dir / "A"
    _track(folder / "embedded.mp3", soundcloud_id="1", lyrics=True)
    _track(folder / "lrc.mp3", soundcloud_id="2").with_suffix(".lrc").write_text("[00:01.00]x")
    _track(folder / "searched.mp3", soundcloud_id="3")
    get_attempts(library.soundcloud_dir / LYRICS_ATTEMPTS_FILENAME).record("3", "strict")
    _track(folder / "beat.mp3", soundcloud_id="4", title="Night (Instrumental)")
    _track(folder / "missing.mp3", soundcloud_id="5")
    _track(library.music_dir / "Alpha - Alb" / "spotify.mp3", spotify=True)
    get_attempts(library.music_dir / SPOTIFY_LYRICS_ATTEMPTS_FILENAME).record(SPOTIFY_URL, "strict")
    assert _kinds(check.scan(library, ("lyrics",))) == {("no_lyrics", "SoundCloud/A/missing.mp3", True)}
    library.lyrics_enabled = False
    (problem,) = check.scan(library, ("lyrics",)).problems
    assert not problem.fixable and problem.detail == "lyrics are switched off"


def test_artist_rule_spelling_account_and_undetermined(library):
    registry = get_registry(library.music_dir / ARTISTS_FILENAME)
    registry.register("ALPHA", PRIORITY_SPOTIFY)
    index = get_index(library.soundcloud_dir)
    folder = library.soundcloud_dir / "A"
    _track(folder / "spelling.mp3", soundcloud_id="1")
    renamed = _track(folder / "renamed.mp3", artists=("@gamma",), soundcloud_id="2")
    index.add({"id": "2", "title": "Song", "uploader": "@gamma", "uploader_id": "77"}, renamed, account="")
    registry.put_account("77", {"kind": KIND_OWN, "alias": "GAMMA", "handle": "@gamma"})
    reupload = _track(folder / "reupload.mp3", artists=("@reup",), soundcloud_id="3")
    index.add({"id": "3", "title": "Song", "uploader": "@reup", "uploader_id": "88"}, reupload,
              account="reuploader")
    registry.put_account("88", {"kind": KIND_REUPLOADER, "handle": "@reup"})
    index.set_original("3", {"artists": [], "featured": [], "source": None})
    report = check.scan(library, ("artist",))
    assert _kinds(report) == {
        ("artist_changed", "SoundCloud/A/spelling.mp3", True),
        ("artist_changed", "SoundCloud/A/renamed.mp3", True),
        ("artist_unknown", "SoundCloud/A/reupload.mp3", False),
    }


def test_format_rule(library, monkeypatch):
    current = _track(library.soundcloud_dir / "A" / "current.mp3", soundcloud_id="1")
    old = _track(library.soundcloud_dir / "A" / "old.mp3", soundcloud_id="2")
    tg.bind_ids(old, format_version=0)
    assert _kinds(check.scan(library, ("format",))) == {("old_format", "SoundCloud/A/old.mp3", True)}
    monkeypatch.setattr(check, "FORMAT_VERSION", tg.format_version(current) + 1)
    assert len(check.scan(library, ("format",)).problems) == 2


def test_duplicates_are_only_reported(library):
    _track(library.soundcloud_dir / "A" / "one.mp3", soundcloud_id="1")
    _track(library.soundcloud_dir / "B" / "two.mp3", soundcloud_id="1")
    _track(library.soundcloud_dir / "B" / "other.mp3", soundcloud_id="2")
    report = check.scan(library, ("duplicates",))
    assert _kinds(report) == {("duplicate", "SoundCloud/A/one.mp3", False),
                              ("duplicate", "SoundCloud/B/two.mp3", False)}


def test_hidden_folders_and_files_are_left_out(library):
    _foreign(library.soundcloud_dir / ".sc_downloads" / "partial.mp3")
    _foreign(library.music_dir / ".hidden.mp3")
    (library.music_dir / "cover.jpg").write_bytes(b"x")
    assert check.scan(library, check.RULES[:1]).tracks == 0


# -- rule selection --------------------------------------------------------------------------------
def test_parse_and_select_rules():
    assert check.parse_rules("lyrics, cover") == ("cover", "lyrics")
    assert check.parse_rules(["available", "id", "id"]) == ("id", "available")
    for bad in ("", "colour", ["id", 3], 5):
        with pytest.raises(ValueError):
            check.parse_rules(bad)
    assert check.select_rules() == check.RULES
    assert check.select_rules(offline=True) == LOCAL
    assert check.select_rules(("cover", "available"), skip=("cover",)) == ("available",)
    with pytest.raises(ValueError):
        check.select_rules(("available",), offline=True)


def test_only_the_chosen_rules_run(library):
    _track(library.soundcloud_dir / "A" / "song.mp3", soundcloud_id="1", cover=False)
    tg.bind_ids(library.soundcloud_dir / "A" / "song.mp3", format_version=0)
    assert {p.kind for p in check.scan(library, ("cover",)).problems} == {"no_cover"}
    assert {p.kind for p in check.scan(library, ("format",)).problems} == {"old_format"}


def test_command_line_rule_options(capsys):
    assert cli.parse_check_args(["-o", "M"]).rules == check.RULES
    assert cli.parse_check_args(["-o", "M", "--offline"]).rules == LOCAL
    assert cli.parse_check_args(["-o", "M", "--rules", "cover,lyrics", "--skip-rules", "lyrics"]).rules == ("cover",)
    for argv in (["--rules", "colour"], ["--rules", "available", "--offline"]):
        with pytest.raises(SystemExit):
            cli.parse_check_args(argv)
    capsys.readouterr()


# -- availability ------------------------------------------------------------------------------------
class Lookups:
    def __init__(self, soundcloud=None, spotify=None):
        self.soundcloud_states = soundcloud or {}
        self.spotify_states = spotify or {}
        self.asked: list = []

    def soundcloud(self, ids):
        self.asked.append(("soundcloud", tuple(sorted(ids))))
        return {track_id: self.soundcloud_states.get(track_id, check.STATE_REMOVED) for track_id in ids}

    def spotify(self, track_id):
        self.asked.append(("spotify", track_id))
        state = self.spotify_states.get(track_id)
        if isinstance(state, Exception):
            raise state
        return state


def _availability(library, lookups, clock=lambda: 1_000_000.0):
    return check.Availability(library.music_dir / ".music-loader-availability.json",
                              soundcloud=lookups.soundcloud, spotify=lookups.spotify, clock=clock)


def test_available_rule_reports_removed_tracks_and_caches_the_answer(library):
    _track(library.soundcloud_dir / "A" / "here.mp3", soundcloud_id="1", cover=False)
    _track(library.soundcloud_dir / "A" / "gone.mp3", soundcloud_id="2", cover=False)
    _track(library.music_dir / "Alpha - Alb" / "song.mp3", spotify=True)
    lookups = Lookups({"1": check.STATE_OK}, {SPOTIFY_ID: check.STATE_OK})
    report = check.scan(library, ("cover", "available"), availability=_availability(library, lookups))
    assert {(p.kind, p.track.relative, p.fixable) for p in report.problems} == {
        ("no_cover", "SoundCloud/A/here.mp3", True),
        # A removed track cannot be rewritten from its platform.
        ("no_cover", "SoundCloud/A/gone.mp3", False),
        ("removed", "SoundCloud/A/gone.mp3", False),
    }
    assert sorted(lookups.asked) == [("soundcloud", ("1", "2")), ("spotify", SPOTIFY_ID)]

    # Within a week the answers come from the cache file.
    again = Lookups()
    report = check.scan(library, ("available",), availability=_availability(library, again))
    assert again.asked == [] and [p.track.relative for p in report.problems] == ["SoundCloud/A/gone.mp3"]
    later = Lookups({"1": check.STATE_OK, "2": check.STATE_OK}, {SPOTIFY_ID: check.STATE_OK})
    clock = lambda: 1_000_000.0 + AVAILABILITY_CACHE_SECONDS + 1  # noqa: E731
    assert check.scan(library, ("available",), availability=_availability(library, later, clock)).problems == []
    assert len(later.asked) == 2


def test_availability_that_cannot_be_asked_is_unchecked_not_removed(library):
    _track(library.soundcloud_dir / "A" / "one.mp3", soundcloud_id="1")
    _track(library.music_dir / "Alpha - Alb" / "song.mp3", spotify=True)

    def broken(ids):
        raise OSError("no network")

    lookups = Lookups(spotify={SPOTIFY_ID: check.RateLimited("Spotify")})
    availability = check.Availability(library.music_dir / "cache.json", soundcloud=broken,
                                      spotify=lookups.spotify)
    events = Events()
    report = check.scan(library, ("available",), events, availability)
    assert report.problems == [] and report.unchecked == 2
    assert any("could not be asked" in line for line in events.lines)


def test_offline_never_asks(library, monkeypatch):
    _track(library.soundcloud_dir / "A" / "one.mp3", soundcloud_id="1")

    def no_network(*args, **kwargs):
        raise AssertionError("asked the network")

    monkeypatch.setattr(check, "soundcloud_states", no_network)
    monkeypatch.setattr(check, "spotify_state", no_network)
    assert check.scan(library, check.select_rules(offline=True)).problems != []


def test_spotify_state_reads_the_oembed_answer(monkeypatch):
    from music_loader.net import Response

    answers = {200: check.STATE_OK, 404: check.STATE_REMOVED, 500: None}
    for status, expected in answers.items():
        monkeypatch.setattr(check, "http_get", lambda url, **kw: Response(status, b"", url))
        assert check.spotify_state(SPOTIFY_ID) == expected
    monkeypatch.setattr(check, "http_get", lambda url, **kw: Response(429, b"", url))
    with pytest.raises(check.RateLimited):
        check.spotify_state(SPOTIFY_ID)


# -- report --------------------------------------------------------------------------------------------
def test_report_groups_json_and_log_file(library):
    for number in range(3):
        _track(library.soundcloud_dir / "A" / f"{number}.mp3", artists=(f"Artist {number}",),
               soundcloud_id=str(number + 1), cover=False)
    _foreign(library.music_dir / "unknown.mp3")
    events = Events()
    report = check.run(library, ("id", "cover"), False, events)
    assert [kind.name for kind, _ in report.groups()] == ["no_id", "no_cover"]
    data = events.report
    assert data["problems"] == 5 and data["fixable"] == 3 and data["fixed"] is None and not data["fix"]
    no_cover = data["groups"][1]
    assert no_cover["label"] == "No cover" and no_cover["count"] == 4
    assert no_cover["items"][0] == {
        "artist": "Artist 0", "title": "Song", "path": "SoundCloud/A/0.mp3",
        "link": "https://api.soundcloud.com/tracks/1", "service": "soundcloud", "fixable": True, "detail": "",
    }
    assert len(report.to_json(limit=2)["groups"][1]["items"]) == 2
    assert report.fix_note() == "--fix can fix 3 of 5 problem(s)"
    log = report.log_path.read_text(encoding="utf-8")
    assert report.log_path.parent == library.music_dir / LOGS_DIRNAME
    assert report.log_path.name.startswith("check-")
    assert "No cover - 4 (3 can be fixed with --fix)" in log
    assert "Artist 2 — Song | SoundCloud/A/2.mp3 | https://api.soundcloud.com/tracks/3 | fixable" in log
    assert log.rstrip().endswith("--fix can fix 3 of 5 problem(s)")


def test_command_line_report(library, capsys):
    for number in range(12):
        _track(library.soundcloud_dir / "A" / f"{number:02}.mp3", artists=(f"Artist {number:02}",),
               soundcloud_id=str(number + 1), cover=False, lyrics=True)
    console = Console(file=io.StringIO(), width=200)
    assert cli.check_main(["-o", str(library.music_dir), "--offline"], console) == 0
    text = console.file.getvalue()
    assert "No cover — 12" in text and "Artist 09 — Song" in text and "Artist 10" not in text
    assert "and 2 more" in text and "--fix can fix 12 of 12 problem(s)" in text
    assert "check-" in text


# -- fix -------------------------------------------------------------------------------------------------
def test_fix_binds_ids_rewrites_and_searches_lyrics_without_deleting_anything(library, monkeypatch):
    library.lyrics_enabled = True
    folder = library.soundcloud_dir / "A"
    no_cover = _track(folder / "no-cover.mp3", soundcloud_id="1", cover=False, lyrics=True)
    no_lyrics = _track(folder / "no-lyrics.mp3", soundcloud_id="2")
    unbound = _foreign(library.music_dir / "Spotify" / "song.mp3", url=SPOTIFY_URL)
    unknown = _foreign(library.music_dir / "unknown.mp3")
    rewritten, searched = [], []

    def rewrite(tracks, config, dashboard, lyrics):
        rewritten.extend(track.relative for track in tracks)
        for track in tracks:
            sc_id = track.id if track.service == "soundcloud" else ""
            tg.write_tags(track.path, tg.TrackTags(title="Song", artists=["Alpha"], album="A",
                                                   album_artist="Alpha", soundcloud_id=sc_id), b"jpeg")
            tg.bind_ids(track.path, spotify_url=track.link if track.service == "spotify" else "",
                        format_version=FORMAT_VERSION)
            tg.embed_lyrics(track.path, "words")

    def lyrics(tracks, config, dashboard, service):
        searched.extend(track.relative for track in tracks)
        for track in tracks:
            tg.embed_lyrics(track.path, "words")

    monkeypatch.setattr(check, "rewrite_tracks", rewrite)
    monkeypatch.setattr(check, "search_lyrics", lyrics)
    events = Events()
    report = check.run(library, ("id", "cover", "lyrics", "format"), True, events)
    assert tg.read_tags(unbound)["spotify_id"] == SPOTIFY_ID
    # The Spotify file got its id but had no format: it is rewritten too.
    assert sorted(rewritten) == ["SoundCloud/A/no-cover.mp3", "Spotify/song.mp3"]
    assert searched == ["SoundCloud/A/no-lyrics.mp3"]
    assert all(path.exists() for path in (no_cover, no_lyrics, unbound, unknown))
    # no-cover: cover; no-lyrics: lyrics; the Spotify file: id, cover, lyrics, format;
    # unknown.mp3: id, cover, lyrics (none of them fixable without an id).
    assert report.fix and report.before == 9
    left = {(p.kind, p.track.relative) for p in report.problems}
    assert left == {("no_id", "unknown.mp3"), ("no_cover", "unknown.mp3"), ("no_lyrics", "unknown.mp3")}
    assert report.fixed == 6 and events.report["fixed"] == 6
    assert report.fix_note() == "--fix fixed 6 of 9 problem(s); 3 problem(s) left"


def test_fix_rewrites_an_artist_written_differently(library, monkeypatch):
    get_registry(library.music_dir / ARTISTS_FILENAME).register("ALPHA", PRIORITY_SPOTIFY)
    path = _track(library.soundcloud_dir / "A" / "song.mp3", soundcloud_id="1", lyrics=True)

    def rewrite(tracks, config, dashboard, lyrics):
        assert [track.path for track in tracks] == [path]
        _track(path, artists=("ALPHA",), soundcloud_id="1", lyrics=True)

    monkeypatch.setattr(check, "rewrite_tracks", rewrite)
    report = check.run(library, ("artist",), True, Events())
    assert report.before == 1 and report.fixed == 1 and report.problems == []
    assert tg.read_tags(path)["artists"] == ["ALPHA"]


def test_fix_with_nothing_fixable_changes_nothing(library, monkeypatch):
    _foreign(library.music_dir / "unknown.mp3")
    monkeypatch.setattr(check, "fix", lambda *args: pytest.fail("nothing to fix"))
    report = check.run(library, ("id",), True, Events())
    assert report.fixed == 0 and report.count == 1


def test_rewrite_runs_the_pipelines_with_existing_all_and_keep_files(library, monkeypatch):
    seen = {}

    class Batch:
        def __init__(self, config, dashboard, abort=None):
            seen["config"] = config
            seen["batches"] = []

        def resolve_urls(self, urls, slot=0):
            seen["batches"].append(list(urls))
            plan = spotify.SpotifyPlan(None)
            plan.resolved = True
            return plan

        @staticmethod
        def track_count(plans):
            return 0

        def download(self, plans, lyrics=None):
            return [True for _ in plans]

    monkeypatch.setattr(spotify, "SpotifyBatch", Batch)
    tracks = [check.TrackFile(path=library.music_dir / f"{n}.mp3", relative=f"{n}.mp3", tags={},
                              service="spotify", track_id=f"{n:022d}") for n in range(60)]
    check.rewrite_tracks(tracks, library, Events(), None)
    assert [len(batch) for batch in seen["batches"]] == [50, 10]
    assert seen["config"].existing == EXISTING_ALL and seen["config"].keep_files
    assert library.existing != EXISTING_ALL and not library.keep_files


def test_soundcloud_fix_rewrites_tags_and_cover_through_the_pipeline(library, monkeypatch):
    path = _track(library.soundcloud_dir / "Alpha" / "1 - Song.mp3", soundcloud_id="1", cover=False)
    tg.bind_ids(path, format_version=0)
    get_index(library.soundcloud_dir).add({"id": "1", "title": "Song", "uploader": "Alpha"}, path)
    info = {"id": "1", "title": "Song", "uploader": "Alpha", "uploader_id": "5", "duration": 100,
            "webpage_url": "https://soundcloud.com/alpha/song", "artwork_url": "https://i1.sndcdn.com/a.jpg"}
    monkeypatch.setattr(sc, "tool_command", lambda name: ["yt-dlp"])
    monkeypatch.setattr(sc, "_precheck", lambda jobs, ctx: None)
    monkeypatch.setattr(sc, "_fetch_full_info", lambda url, ctx: dict(info))
    monkeypatch.setattr(sc, "_album_for", lambda job, info, ctx: None)
    monkeypatch.setattr(sc, "_duration_ok", lambda path, expected: (True, 100.0))
    monkeypatch.setattr(sc, "_cover_bytes", lambda urls, work_dir: b"jpeg")
    report = check.run(library, ("cover", "format"), True, EventDashboard(io.StringIO()))
    assert report.before == 2 and report.count == 0 and report.fixed == 2
    files = sorted(library.soundcloud_dir.rglob("*.mp3"))
    assert len(files) == 1 and tg.read_tags(files[0])["has_cover"]


def test_keep_files_never_replaces_a_soundcloud_file(library, monkeypatch):
    path = _track(library.soundcloud_dir / "Alpha" / "song.mp3", soundcloud_id="1")
    get_index(library.soundcloud_dir).add({"id": "1", "title": "Song", "uploader": "Alpha"}, path)
    monkeypatch.setattr(sc, "tool_command", lambda name: ["yt-dlp"])
    monkeypatch.setattr(sc, "_precheck", lambda jobs, ctx: None)
    monkeypatch.setattr(sc, "_fetch_full_info", lambda url, ctx: {"title": "Song", "duration": 200})
    monkeypatch.setattr(sc, "_duration_ok", lambda path, expected: (False, 30.0))
    monkeypatch.setattr(sc, "_download_one", lambda *args, **kw: pytest.fail("downloaded again"))
    events = EventDashboard(io.StringIO())
    track = check.TrackFile(path=path, relative="SoundCloud/Alpha/song.mp3", tags={"title": "Song"},
                            service="soundcloud", track_id="1")
    check.rewrite_tracks([track], library, events, None)
    # The layout migration may move the file into its album folder; it is never removed.
    assert [p.name for p in library.soundcloud_dir.rglob("*.mp3")] == ["song.mp3"]
    assert events.stats.soundcloud_tracks_failed == 1


def test_keep_files_keeps_spotify_duplicates(tmp_path):
    config = AppConfig.from_output_dir(tmp_path)
    config.keep_files = True
    song = spotify._Song({"url": SPOTIFY_URL}, tmp_path / "a" / "1.mp3")
    copy = _track(tmp_path / "b" / "1.mp3", spotify=True)
    spotify._remove_duplicates([song], {SPOTIFY_URL}, config, Events())
    assert copy.exists()


# -- worker and server -------------------------------------------------------------------------------------
def _lines(out):
    return [json.loads(line) for line in out.getvalue().splitlines()]


def test_worker_runs_a_check_job(library):
    _track(library.soundcloud_dir / "A" / "song.mp3", soundcloud_id="1", cover=False, lyrics=True)
    out = io.StringIO()
    spec = {"type": "check", "output": str(library.music_dir), "rules": ["cover", "id"], "lyrics": "off"}
    assert worker.run(spec, EventDashboard(out)) == 0
    events = _lines(out)
    (report,) = [event["report"] for event in events if event["type"] == "check"]
    assert report["rules"] == ["id", "cover"] and report["problems"] == 1
    assert events[-1] == {"type": "finished", "status": "completed", "message": None}

    out = io.StringIO()
    assert worker.run({**spec, "rules": ["colour"]}, EventDashboard(out)) == 1
    assert _lines(out)[-1]["status"] == "failed"


@pytest.fixture
def manager(tmp_path):
    return server.JobManager(tmp_path, {}, Console(file=io.StringIO()), state_dir=tmp_path / "state")


def test_check_requests():
    options = server._parse_check_request({"type": "check"})
    assert options["fix"] is False and options["rules"] == list(check.RULES)
    assert options["auto_retry"] is False and options["existing"] == "keep"
    options = server._parse_check_request({"options": {"fix": True, "rules": ["lyrics", "id"], "lyrics": "loose"}})
    assert options["fix"] is True and options["rules"] == ["id", "lyrics"] and options["lyrics"] == "loose"
    for body in ({"links": ["x"]}, {"options": {"fix": "yes"}}, {"options": {"rules": "id"}},
                 {"options": {"rules": ["colour"]}}, {"options": {"rules": []}}):
        with pytest.raises(server._RequestError):
            server._parse_check_request(body)
    assert server._job_type({"type": "check"}) == "check" and server._job_type({}) == "download"
    with pytest.raises(server._RequestError):
        server._job_type({"type": "dance"})
    assert "library_check" in server.FEATURES


def test_check_job_report_retry_and_history(manager, tmp_path):
    options = server._parse_check_request({"options": {"fix": True}})
    job = manager.submit([], options, [], job_type="check")
    with manager._cond:
        manager._pending.remove(job.id)
        manager._apply(job, {"type": "failure", "service": "spotify", "category": "failed", "url": SPOTIFY_URL})
        manager._apply(job, {"type": "check", "report": {
            "rules": ["cover", "nonsense"], "tracks": 3, "problems": 1, "fixable": 1, "fixed": 2, "fix": True,
            "groups": [{"kind": "no_cover", "count": 1, "fixable": 1, "items": [
                {"artist": "A", "title": "T", "path": "a.mp3", "link": "https://soundcloud.com/a/t/s-Secret",
                 "service": "soundcloud", "fixable": True}]},
                {"kind": "unknown_kind", "items": []}],
        }})
        job.status = "completed"
        manager._schedule_retry(job)
    detail = manager.job(job.id)
    assert detail["type"] == "check" and detail["link_count"] == 0 and detail["failed_items"] == []
    assert detail["check"]["rules"] == ["cover"] and [g["kind"] for g in detail["check"]["groups"]] == ["no_cover"]
    assert "Secret" not in json.dumps(detail)
    assert detail["check"]["groups"][0]["label"] == "No cover"
    assert manager.jobs()[0]["check"] == {"tracks": 3, "problems": 1, "fixable": 1, "fixed": 2, "fix": True}
    assert detail["retry_at"] is None

    with pytest.raises(server.JobConflict):
        manager.retry(job.id, "failed")
    again = manager.retry(job.id, "all")
    assert again.type == "check" and again.options["fix"] is True and again.links == []

    with manager._cond:
        manager._save()
    restored = server.JobManager(tmp_path, {}, Console(file=io.StringIO()), state_dir=tmp_path / "state")
    loaded = restored.job(job.id)
    assert loaded["type"] == "check" and loaded["check"] == detail["check"]


def test_server_spec_of_a_check_job(manager, monkeypatch):
    seen = {}

    def execute(job, spec):
        seen.update(spec)
        return "completed", None

    monkeypatch.setattr(manager, "_execute_in_process", execute)
    manager.in_process = True
    job = manager.submit([], server._parse_check_request({}), [], job_type="check")
    assert manager._execute(job) == ("completed", None)
    assert seen["type"] == "check" and seen["links"] == [] and seen["rules"] == list(check.RULES)


def test_check_job_over_http(tmp_path):
    import urllib.request

    manager = server.JobManager(tmp_path, {}, Console(file=io.StringIO()))
    httpd = server.make_server("127.0.0.1", 0, manager, "test-token-0123456789abcdef")
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{httpd.server_address[1]}/api/v1/jobs",
            data=json.dumps({"type": "check", "options": {"rules": ["cover"]}}).encode(), method="POST",
            headers={"Authorization": "Bearer test-token-0123456789abcdef", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            created = json.loads(response.read())
        assert response.status == 201
        assert created["job"]["type"] == "check" and created["job"]["options"]["rules"] == ["cover"]
    finally:
        httpd.shutdown()
        httpd.server_close()
