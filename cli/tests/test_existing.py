"""Track ids in hidden tags, the format version, binding files without an
id, and the "Existing tracks" setting (keep / outdated / all)."""
import io
import json
import threading

import pytest

pytest.importorskip("mutagen")
from mutagen.id3 import ID3, TALB, TIT2, TPE1, TYER, WOAS  # noqa: E402
from rich.console import Console  # noqa: E402

from music_loader import existing as ex  # noqa: E402
from music_loader import soundcloud as sc  # noqa: E402
from music_loader import spotify  # noqa: E402
from music_loader import spotify_index  # noqa: E402
from music_loader import tags as tg  # noqa: E402
from music_loader import worker  # noqa: E402
from music_loader.artists import PRIORITY_SPOTIFY, PRIORITY_TITLE, ArtistRegistry  # noqa: E402
from music_loader.cli import outdated_note, parse_args, print_summary  # noqa: E402
from music_loader.config import (  # noqa: E402
    EXISTING_ALL,
    EXISTING_KEEP,
    EXISTING_OUTDATED,
    FORMAT_VERSION,
    AppConfig,
    existing_from_options,
)
from music_loader.events import EventDashboard  # noqa: E402
from music_loader.soundcloud_index import SoundCloudArchive, SoundCloudIndex  # noqa: E402
from music_loader.ui import Dashboard  # noqa: E402

SPOTIFY_URL = "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC"


def _tags(artists=("Alpha",), **kw):
    return tg.TrackTags(title="Song", artists=list(artists), album="Alb", album_artist=artists[0], **kw)


def _file(tmp_path, name="a.mp3"):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def _foreign(path, version=3, url=None):
    """A file written by another tool: ID3v2.3, no ids."""
    id3 = ID3()
    id3.add(TIT2(encoding=1, text="Song"))
    id3.add(TPE1(encoding=1, text="Alpha/Beta"))
    id3.add(TALB(encoding=1, text="Alb"))
    id3.add(TYER(encoding=1, text="2020"))
    if url:
        id3.add(WOAS(url=url))
    id3.save(str(path), v2_version=version)


def _frames(path, skip=()):
    id3 = ID3(str(path), translate=False)
    return id3.version, {key: repr(id3[key]) for key in id3.keys() if key not in skip}


# -- tags ------------------------------------------------------------------------------------------
def test_write_tags_stamps_the_format_and_read_tags_returns_the_ids(tmp_path):
    path = _file(tmp_path)
    tg.write_tags(path, _tags(soundcloud_id="123"))
    assert tg.bind_ids(path, spotify_url=SPOTIFY_URL)
    tags = tg.read_tags(path)
    assert tags["format"] == FORMAT_VERSION == tg.format_version(path)
    assert tags["soundcloud_id"] == "123"
    assert tags["spotify_id"] == "4uLU6hMCjMI75M1A2tKUQC"
    assert tags["url"] == SPOTIFY_URL
    assert list(ID3(str(path))[f"TXXX:{tg.FORMAT_DESC}"].text) == [str(FORMAT_VERSION)]


def test_a_file_without_the_frame_is_version_0(tmp_path):
    path = _file(tmp_path)
    _foreign(path)
    assert tg.read_tags(path)["format"] == 0 == tg.format_version(path)
    assert tg.format_version(tmp_path / "missing.mp3") == 0


def test_spotify_track_id():
    assert tg.spotify_track_id(SPOTIFY_URL) == "4uLU6hMCjMI75M1A2tKUQC"
    assert tg.spotify_track_id(SPOTIFY_URL + "?si=x") == "4uLU6hMCjMI75M1A2tKUQC"
    assert tg.spotify_track_id("https://open.spotify.com/album/abc") == ""
    assert tg.spotify_track_id("https://open.spotify.com/track/../x") == ""


@pytest.mark.parametrize("version", [3, 4])
def test_binding_writes_only_the_id_frames(tmp_path, version):
    path = _file(tmp_path)
    _foreign(path, version=version)
    before = _frames(path)
    assert tg.bind_ids(path, soundcloud_id="987")
    after = _frames(path, skip={f"TXXX:{tg.SOUNDCLOUD_ID_DESC}"})
    assert after == before   # same ID3 version, every other frame unchanged
    assert tg.read_tags(path)["soundcloud_id"] == "987"
    assert tg.read_tags(path)["format"] == 0   # binding is not a rewrite


def test_binding_a_spotify_file_adds_the_url_and_id(tmp_path):
    path = _file(tmp_path)
    _foreign(path)
    before = _frames(path)
    assert tg.bind_ids(path, spotify_url=SPOTIFY_URL)
    assert _frames(path, skip={f"TXXX:{tg.SPOTIFY_ID_DESC}", "WOAS"}) == before
    assert tg.read_woas(path) == SPOTIFY_URL
    assert tg.read_txxx(path, tg.SPOTIFY_ID_DESC) == "4uLU6hMCjMI75M1A2tKUQC"


def test_binding_nothing_or_an_unreadable_file_fails(tmp_path):
    path = _file(tmp_path)
    assert not tg.bind_ids(path)
    assert not tg.bind_ids(tmp_path / "missing.mp3", soundcloud_id="1")


# -- outdated -------------------------------------------------------------------------------------
class Accounts:
    def __init__(self, aliases=None, reuploaders=()):
        self.aliases = aliases or {}
        self.reuploaders = set(reuploaders)

    def alias(self, account_id):
        return self.aliases.get(account_id)

    def is_reuploader(self, account_id):
        return account_id in self.reuploaders


def test_outdated_missing_frame_and_older_version(tmp_path, monkeypatch):
    current = {"format": FORMAT_VERSION, "artists": ["Alpha"], "album_artist": "Alpha"}
    assert ex.outdated_reason(current) is None
    assert ex.outdated_reason({**current, "format": 0}) == ex.REASON_FORMAT
    monkeypatch.setattr(ex, "FORMAT_VERSION", FORMAT_VERSION + 1)
    assert ex.outdated_reason(current) == ex.REASON_FORMAT


def test_outdated_when_the_registry_spells_an_artist_differently(tmp_path):
    registry = ArtistRegistry(None)
    registry.register("whitener", PRIORITY_TITLE)
    tags = {"format": FORMAT_VERSION, "artists": ["whitener", "Unknown Guest"], "album_artist": "whitener"}
    assert ex.outdated_reason(tags, registry) is None
    registry.register("WHITENER", PRIORITY_SPOTIFY)
    assert ex.outdated_reason(tags, registry) == ex.REASON_ARTISTS
    assert ex.outdated_reason({**tags, "artists": ["WHITENER"], "album_artist": "WHITENER"}, registry) is None


def test_outdated_when_the_account_decision_changed():
    tags = {"format": FORMAT_VERSION, "artists": ["@aquagwap"], "album_artist": ""}
    assert ex.account_state(Accounts(), "1") == ""
    assert ex.account_state(Accounts({"1": "AQUAKEY"}), "1") == "own:AQUAKEY"
    assert ex.account_state(Accounts(reuploaders={"1"}), "1") == "reuploader"
    assert ex.account_state(None, "1") == ""
    assert ex.outdated_reason(tags, None, "", "") is None
    assert ex.outdated_reason(tags, None, "", "own:AQUAKEY") == ex.REASON_ACCOUNT
    assert ex.outdated_reason(tags, None, "own:AQUAKEY", "reuploader") == ex.REASON_ACCOUNT
    # Not known what the file was written with: not compared.
    assert ex.outdated_reason(tags, None, None, "own:AQUAKEY") is None


@pytest.mark.parametrize("mode, current, outdated", [
    (EXISTING_KEEP, False, False),
    (EXISTING_OUTDATED, False, True),
    (EXISTING_ALL, True, True),
])
def test_the_three_settings(tmp_path, mode, current, outdated):
    config = AppConfig(music_dir=tmp_path, soundcloud_dir=tmp_path, existing=mode)
    assert config.rewrites(False) is current
    assert config.rewrites(True) is outdated
    assert config.rewrite_all is (mode == EXISTING_ALL)


# -- command line, server, worker -------------------------------------------------------------------
def test_command_line_existing_and_the_recheck_alias(capsys):
    assert parse_args(["-o", "x", "l"]).existing == EXISTING_KEEP
    assert parse_args(["-o", "x", "--existing", "outdated", "l"]).existing == EXISTING_OUTDATED
    assert parse_args(["-o", "x", "--recheck", "l"]).existing == EXISTING_ALL
    assert parse_args(["-o", "x", "--recheck", "--existing", "all", "l"]).existing == EXISTING_ALL
    with pytest.raises(SystemExit):
        parse_args(["-o", "x", "--recheck", "--existing", "keep", "l"])
    with pytest.raises(SystemExit):
        parse_args(["-o", "x", "--existing", "some", "l"])


def test_job_options_and_older_clients():
    from music_loader import server

    assert server._parse_options({})["existing"] == EXISTING_KEEP
    old = server._parse_options({"recheck": True})
    assert old["existing"] == EXISTING_ALL and old["recheck"] is True
    new = server._parse_options({"existing": "outdated"})
    assert new["existing"] == EXISTING_OUTDATED and new["recheck"] is False
    assert server._parse_options({"existing": "all"})["recheck"] is True
    for bad in ({"existing": "some"}, {"existing": 1}, {"recheck": True, "existing": "keep"}):
        with pytest.raises(server._RequestError):
            server._parse_options(bad)
    assert "existing_tracks" in server.FEATURES


def test_worker_reads_existing_and_recheck(tmp_path):
    def config(**spec):
        return worker.build_config({"output": str(tmp_path), **spec}).existing

    assert config() == EXISTING_KEEP
    assert config(recheck=True) == EXISTING_ALL
    assert config(existing="outdated") == EXISTING_OUTDATED
    assert config(existing="nonsense", recheck=False) == EXISTING_KEEP
    assert existing_from_options({"recheck": "yes"}) == EXISTING_KEEP


def test_summary_counts_the_outdated_tracks(tmp_path):
    console = Console(file=io.StringIO(), width=200)
    dashboard = Dashboard(console, source_label="1", output_dir=str(tmp_path))
    dashboard.stats.soundcloud_ok = 1
    print_summary(console, dashboard)
    assert "older format" not in console.file.getvalue()
    dashboard.stats.soundcloud_tracks_outdated = 2
    dashboard.stats.spotify_tracks_outdated = 1
    print_summary(console, dashboard)
    assert outdated_note(3) in console.file.getvalue()
    assert outdated_note(1).startswith("1 track is in an older format")


# -- SoundCloud -------------------------------------------------------------------------------------
def _sc_ctx(tmp_path, mode, accounts=None):
    config = AppConfig.from_output_dir(tmp_path / "Music")
    config.existing = mode
    config.ensure_dirs()
    out = io.StringIO()
    ctx = sc._Context(
        config=config, soundcloud_dir=config.soundcloud_dir, staging_dir=tmp_path / "staging",
        dashboard=EventDashboard(out), lyrics=None, abort=threading.Event(), yt=["yt-dlp"],
        api=None, gate=sc._RateGate(), index=SoundCloudIndex(config.soundcloud_dir),
        archive=SoundCloudArchive(config.soundcloud_dir / ".sc_archive.txt"),
        registry=ArtistRegistry(None), accounts=accounts,
    )
    return ctx, out


def _sc_file(ctx, name, track_id="", current=True):
    path = ctx.soundcloud_dir / "Alpha" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    if current:
        tg.write_tags(path, _tags(soundcloud_id=track_id))
    else:
        _foreign(path)
    return path


def _run(ctx, jobs, monkeypatch):
    """The pipeline with the network parts replaced: returns the tracks it
    rewrote."""
    rewritten = []

    def retag(job, path, info, c):
        rewritten.append(job.track_id)
        c.index.add(info, path, account=sc._account_now(info, c))
        return sc._Processed(True, path, True)

    monkeypatch.setattr(sc, "_fetch_full_info", lambda url, c: {"title": "Song", "uploader": "Alpha"})
    monkeypatch.setattr(sc, "_duration_ok", lambda path, expected: (True, 100.0))
    monkeypatch.setattr(sc, "_retag", retag)
    sc._run_pipeline(jobs, ctx)
    return rewritten


def _stats(out):
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    stats = [line["stats"] for line in lines if line["type"] == "stats"]
    return stats[-1] if stats else {}


@pytest.mark.parametrize("mode, expected", [
    (EXISTING_KEEP, []),
    (EXISTING_OUTDATED, ["2"]),
    (EXISTING_ALL, ["1", "2"]),
])
def test_soundcloud_existing_tracks(tmp_path, monkeypatch, mode, expected):
    ctx, out = _sc_ctx(tmp_path, mode)
    current = _sc_file(ctx, "current.mp3", "1")
    old = _sc_file(ctx, "old.mp3", current=False)
    ctx.index.add({"id": "1", "title": "Song", "uploader": "Alpha"}, current, account="")
    # An entry of an older version: the file carries no id and no format.
    ctx.index.add({"id": "2", "title": "Old", "uploader": "Alpha"}, old)
    before = _frames(old)
    jobs = [sc.TrackJob(info={"id": "1", "webpage_url": "u1"}), sc.TrackJob(info={"id": "2", "webpage_url": "u2"})]
    assert _run(ctx, jobs, monkeypatch) == expected
    stats = _stats(out)
    assert stats["soundcloud_tracks_skipped"] == 2
    assert stats["soundcloud_tracks_outdated"] == (1 if mode == EXISTING_KEEP else 0)
    if mode == EXISTING_KEEP:
        # Bound: the id is written, nothing else changes.
        assert tg.read_tags(old)["soundcloud_id"] == "2"
        assert _frames(old, skip={f"TXXX:{tg.SOUNDCLOUD_ID_DESC}"}) == before
        assert old.exists()


def test_soundcloud_changed_account_makes_a_file_outdated(tmp_path, monkeypatch):
    accounts = Accounts()
    ctx, out = _sc_ctx(tmp_path, EXISTING_OUTDATED, accounts)
    path = _sc_file(ctx, "song.mp3", "1")
    ctx.index.add({"id": "1", "title": "Song", "uploader": "@aquagwap", "uploader_id": "77"}, path, account="")
    job = sc.TrackJob(info={"id": "1", "webpage_url": "u1"})
    assert _run(ctx, [job], monkeypatch) == []
    accounts.aliases["77"] = "AQUAKEY"   # changed in Artists
    assert _run(ctx, [job], monkeypatch) == ["1"]
    assert ctx.index.entry("1")["account"] == "own:AQUAKEY"
    # Rewritten with the new decision: current again.
    assert _run(ctx, [job], monkeypatch) == []


def test_precheck_hands_outdated_tracks_to_the_account_decision(tmp_path):
    seen = {}

    class Recorder(Accounts):
        def evaluate(self, infos, new_ids, abort):
            seen["new"] = new_ids

    class Api:
        disabled = True

    for mode, expected in ((EXISTING_KEEP, {"3"}), (EXISTING_OUTDATED, {"2", "3"}), (EXISTING_ALL, {"1", "2", "3"})):
        ctx, _ = _sc_ctx(tmp_path / mode, mode, Recorder())
        ctx.api = Api()
        ctx.index.add({"id": "1"}, _sc_file(ctx, "a.mp3", "1"))
        ctx.index.add({"id": "2"}, _sc_file(ctx, "b.mp3", current=False))
        jobs = [sc.TrackJob(info={"id": str(n), "title": "T", "uploader": "U", "uploader_id": "9"}) for n in (1, 2, 3)]
        sc._precheck(jobs, ctx)
        assert seen["new"] == expected, mode


def test_index_keeps_the_account_a_file_was_written_with(tmp_path):
    index = SoundCloudIndex(tmp_path)
    path = tmp_path / "a.mp3"
    path.write_bytes(b"")
    index.add({"id": "1", "title": "Song", "uploader_id": "9"}, path, account="own:X")
    index.add({"id": "1"}, path)   # found again or bound: the decision stays
    assert index.entry("1")["account"] == "own:X"
    assert index.entry("1")["title"] == "Song" and index.entry("1")["uploader_id"] == "9"
    index.add({"id": "1"}, path, account="")
    assert index.entry("1")["account"] == ""


# -- Spotify ---------------------------------------------------------------------------------------
class Log:
    def __init__(self):
        self.counts = {}
        self.lines = []

    def log(self, text):
        self.lines.append(text)

    def log_error(self, source, text):
        self.lines.append(text)

    def record_track(self, kind, status, amount=1):
        self.counts[(kind, status)] = self.counts.get((kind, status), 0) + amount


def _song(tmp_path, url=SPOTIFY_URL, name="Song", path="Alpha - Alb/01 - Song.mp3"):
    data = {"url": url, "name": name, "artists": ["Alpha", "Beta"], "artist": "Alpha", "duration": 200,
            "album_name": "Alb", "album_artist": "Alpha"}
    return spotify._Song(data, tmp_path / path, tmp_path / ".work" / "song.mp3", album_artist="Alpha")


def _spotify_file(path, url=SPOTIFY_URL, current=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    _foreign(path, url=url)
    if current:
        tg.set_artists(path, ["Alpha", "Beta"])
        tg.bind_ids(path, spotify_url=url, format_version=FORMAT_VERSION)


@pytest.mark.parametrize("mode", [EXISTING_KEEP, EXISTING_OUTDATED, EXISTING_ALL])
def test_spotify_rewrite_set(tmp_path, mode):
    config = AppConfig(music_dir=tmp_path, soundcloud_dir=tmp_path / "SoundCloud", existing=mode)
    current = _song(tmp_path, SPOTIFY_URL, path="A/01.mp3")
    old = _song(tmp_path, SPOTIFY_URL[:-1] + "X", path="A/02.mp3")
    new = _song(tmp_path, SPOTIFY_URL[:-1] + "Y", path="A/03.mp3")
    _spotify_file(current.path, current.url)
    _spotify_file(old.path, old.url, current=False)
    registry = ArtistRegistry(None)
    log = Log()
    rewrite = spotify._to_rewrite([current, old, new], {current.url, old.url}, config, registry, log)
    assert rewrite == {EXISTING_KEEP: set(), EXISTING_OUTDATED: {old.url},
                       EXISTING_ALL: {current.url, old.url}}[mode]
    assert log.counts.get(("spotify", "outdated"), 0) == (1 if mode == EXISTING_KEEP else 0)


def test_spotify_kept_file_only_gets_its_id_and_written_files_the_format(tmp_path):
    registry = ArtistRegistry(None)
    kept = _song(tmp_path, SPOTIFY_URL, path="A/01.mp3")
    written = _song(tmp_path, SPOTIFY_URL[:-1] + "X", path="A/02.mp3")
    _spotify_file(kept.path, kept.url, current=False)
    _spotify_file(written.path, written.url, current=False)
    before = _frames(kept.path)
    spotify._tag_artists([kept, written], {kept.url, written.url}, {written.url}, registry, Log())
    assert _frames(kept.path, skip={f"TXXX:{tg.SPOTIFY_ID_DESC}"}) == before
    assert tg.read_tags(kept.path)["spotify_id"] == "4uLU6hMCjMI75M1A2tKUQC"
    assert tg.read_tags(written.path)["format"] == FORMAT_VERSION
    assert tg.read_tags(written.path)["artists"] == ["Alpha", "Beta"]


@pytest.mark.parametrize("mode", [EXISTING_KEEP, EXISTING_OUTDATED])
def test_spotify_file_without_url_is_recognised_and_bound(tmp_path, monkeypatch, mode):
    music = tmp_path / "Music"
    config = AppConfig(music_dir=music, soundcloud_dir=music / "SoundCloud", existing=mode)
    other = music / "From another tool" / "song.mp3"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"")
    _foreign(other)
    before = _frames(other)
    spotify_index._LIBRARIES.clear()
    monkeypatch.setattr(spotify_index, "audio_duration", lambda path: 201.5)
    song = _song(music)
    unrelated = _song(music, SPOTIFY_URL[:-1] + "Z", name="Another Song", path="Alpha - Alb/02 - Another Song.mp3")
    existed = spotify._prepare([song, unrelated], config, Log())
    assert existed == {song.url}
    if mode == EXISTING_KEEP:
        assert song.path == other   # kept where it is
        assert _frames(other, skip={f"TXXX:{tg.SPOTIFY_ID_DESC}", "WOAS"}) == before
    else:
        assert song.path == music / "Alpha - Alb" / "01 - Song.mp3" and song.path.exists()
        assert not other.exists()   # joins the current layout, then is rewritten
    assert tg.read_woas(song.path) == SPOTIFY_URL
    assert tg.read_txxx(song.path, tg.SPOTIFY_ID_DESC) == "4uLU6hMCjMI75M1A2tKUQC"
    # The cache keeps what the scan read; the next run finds the file by its URL.
    spotify_index._LIBRARIES.clear()
    assert spotify_index.get_library(music, [config.soundcloud_dir]).find(SPOTIFY_URL) == [song.path]


def test_spotify_match_needs_artist_title_and_length(tmp_path, monkeypatch):
    other = tmp_path / "x" / "song.mp3"
    other.parent.mkdir()
    other.write_bytes(b"")
    _foreign(other)
    monkeypatch.setattr(spotify_index, "audio_duration", lambda path: 200.0)
    library = spotify_index.SpotifyLibrary(tmp_path, [])
    assert library.find_match("Alpha", ["Song"], 201.9) == other
    assert library.find_match("beta", ["SONG"], 198.1) == other
    assert library.find_match("Alpha", ["Song"], 202.5) is None
    assert library.find_match("Gamma", ["Song"], 200) is None
    assert library.find_match("Alpha", ["Other"], 200) is None
    assert library.find_match("Alpha", ["Song"], None) is None
