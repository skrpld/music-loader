"""SoundCloud library layout "<album artist>/<album>/" and the migration of
libraries written in the old "<album artist> - <album>/" layout."""
import io
import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from music_loader import debuglog, naming
from music_loader import soundcloud as sc
from music_loader.artists import ArtistRegistry
from music_loader.config import AppConfig
from music_loader.events import EventDashboard
from music_loader.soundcloud_index import SoundCloudArchive, SoundCloudIndex
from music_loader.soundcloud_layout import JOURNAL_FILENAME, migrate_layout
from music_loader.soundcloud_meta import AlbumContext, build_meta
from music_loader.tags import TrackTags, read_tags, write_tags

pytest.importorskip("mutagen")


class Dash:
    def __init__(self):
        self.errors = []

    def log(self, *a):
        pass

    def log_error(self, source, message):
        self.errors.append(message)


# -- naming --------------------------------------------------------------------------------------
def test_album_path_is_artist_then_album():
    assert naming.album_path("AC/DC", "Back in Black") == "AC-DC/Back in Black"
    assert naming.album_path("", None) == "Unknown Artist/Unknown Album"
    artist, album = naming.album_path("Artist " * 60, "Album: Deluxe " * 60).split("/")
    assert len(artist.encode()) <= naming.MAX_NAME_BYTES and len(album.encode()) <= naming.MAX_NAME_BYTES
    assert artist.startswith("Artist") and album.startswith("Album - Deluxe")


def test_album_and_single_folders():
    info = {"id": 7, "title": "Artist - Song (feat. Guest)", "uploader": "Artist",
            "webpage_url": "https://soundcloud.com/a/song"}
    album = AlbumContext(title="Album", artist="Artist", set_type="album", position=2, total=9)
    assert build_meta(info, album, ArtistRegistry(None)).folder == "Artist/Album"
    single = build_meta(info, None, ArtistRegistry(None))
    assert single.folder == "Artist/Song - Single" and single.filename == "01 - Song (feat. Guest).mp3"
    compilation = AlbumContext(title="Mix", artist="Label", set_type="compilation", position=1, total=9)
    assert build_meta(info, compilation, ArtistRegistry(None)).folder == "Various Artists/Mix"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_new_download_lands_in_artist_album_folder(tmp_path):
    config = AppConfig.from_output_dir(tmp_path / "Music")
    config.ensure_dirs()
    staging = config.soundcloud_dir / ".sc_downloads"
    staging.mkdir()
    raw = staging / "42.mp3"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=2",
                    "-q:a", "9", str(raw)], check=True)
    ctx = sc._Context(
        config=config, soundcloud_dir=config.soundcloud_dir, staging_dir=staging,
        dashboard=EventDashboard(io.StringIO()), lyrics=None, abort=threading.Event(),
        yt=["yt-dlp"], api=None, gate=sc._RateGate(), index=SoundCloudIndex(config.soundcloud_dir),
        archive=SoundCloudArchive(config.soundcloud_dir / ".sc_archive.txt"),
        registry=ArtistRegistry(None),
    )
    info = {"id": "42", "title": "Artist - Song", "uploader": "Artist", "duration": 2.0,
            "webpage_url": "https://soundcloud.com/artist/song"}
    job = sc.TrackJob(info=info, album=AlbumContext(title="Album", artist="Artist", set_type="album",
                                                    position=1, total=3), lookup_album=False)
    result = sc._postprocess(job, raw, info, ctx)
    assert result.ok
    assert result.path == config.soundcloud_dir / "Artist" / "Album" / "01 - Song.mp3"
    assert result.path.exists()


# -- migration -----------------------------------------------------------------------------------
def _track(root: Path, relative: str, album_artist: str, album: str, track_id: str = "", lrc: bool = True) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    write_tags(path, TrackTags(title=path.stem, artists=[album_artist], album=album,
                               album_artist=album_artist, soundcloud_id=track_id))
    if lrc:
        path.with_suffix(".lrc").write_text("[00:01.00]la", encoding="utf-8")
    return path


def _snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def old_library(tmp_path):
    root = tmp_path / "SoundCloud"
    root.mkdir()
    _track(root, "Artist - Album/01 - One.mp3", "Artist", "Album", "1")
    _track(root, "Artist - Album/02 - Two.mp3", "Artist", "Album", "2", lrc=False)
    _track(root, "Artist - Song - Single/01 - Song.mp3", "Artist", "Song - Single", "3")
    # An older file without the id tag, known only to the index.
    _track(root, "Other - EP/01 - Old.mp3", "Other", "EP")
    # Not written by music-loader: left alone.
    _track(root, "Mine/song.mp3", "Someone", "Something", lrc=False)
    # Partial downloads.
    (root / ".sc_downloads").mkdir()
    (root / ".sc_downloads" / "9.mp3").write_bytes(b"partial")
    index = {
        "1": {"path": "Artist - Album/01 - One.mp3", "title": "One"},
        "2": {"path": "Artist - Album/02 - Two.mp3", "title": "Two"},
        "3": {"path": "Artist - Song - Single/01 - Song.mp3", "title": "Song"},
        "4": {"path": "Other - EP/01 - Old.mp3", "title": "Old"},
    }
    (root / ".sc_index.json").write_text(json.dumps(index), encoding="utf-8")
    (root / "Artist - Album.m3u8").write_text(
        "#EXTM3U\nArtist - Album/01 - One.mp3\nArtist - Album/02 - Two.mp3\n", encoding="utf-8")
    return root


def test_old_layout_is_migrated(old_library):
    root = old_library
    dash = Dash()
    assert migrate_layout(SoundCloudIndex(root), dash) == 4
    assert not dash.errors

    assert (root / "Artist/Album/01 - One.mp3").exists()
    assert (root / "Artist/Album/01 - One.lrc").read_text(encoding="utf-8") == "[00:01.00]la"
    assert (root / "Artist/Album/02 - Two.mp3").exists()
    assert (root / "Artist/Song - Single/01 - Song.mp3").exists()
    assert (root / "Artist/Song - Single/01 - Song.lrc").exists()
    assert (root / "Other/EP/01 - Old.mp3").exists()
    assert read_tags(root / "Artist/Album/01 - One.mp3")["soundcloud_id"] == "1"
    # Old folders are gone, the foreign folder and partial downloads stay.
    for old in ("Artist - Album", "Artist - Song - Single", "Other - EP"):
        assert not (root / old).exists()
    assert (root / "Mine/song.mp3").exists()
    assert (root / ".sc_downloads/9.mp3").exists()
    assert not (root / JOURNAL_FILENAME).exists()

    index = json.loads((root / ".sc_index.json").read_text(encoding="utf-8"))
    assert {key: entry["path"] for key, entry in index.items()} == {
        "1": "Artist/Album/01 - One.mp3",
        "2": "Artist/Album/02 - Two.mp3",
        "3": "Artist/Song - Single/01 - Song.mp3",
        "4": "Other/EP/01 - Old.mp3",
    }
    assert index["1"]["title"] == "One"
    assert (root / "Artist - Album.m3u8").read_text(encoding="utf-8").splitlines() == [
        "#EXTM3U", "Artist/Album/01 - One.mp3", "Artist/Album/02 - Two.mp3",
    ]
    reloaded = SoundCloudIndex(root)
    assert reloaded.get("1") == root / "Artist/Album/01 - One.mp3"


def test_rerun_is_a_no_op(old_library):
    root = old_library
    migrate_layout(SoundCloudIndex(root), Dash())
    before = _snapshot(root)
    dirs = sorted(p for p in root.rglob("*") if p.is_dir())
    assert migrate_layout(SoundCloudIndex(root), Dash()) == 0
    assert _snapshot(root) == before
    assert sorted(p for p in root.rglob("*") if p.is_dir()) == dirs


def test_interrupted_migration_is_finished(old_library):
    root = old_library
    # Interrupted right after the first audio file moved: its .lrc, the
    # index and the playlist still point at the old place.
    journal = {"moves": [
        ["Artist - Album/01 - One.mp3", "Artist/Album/01 - One.mp3"],
        ["Artist - Album/02 - Two.mp3", "Artist/Album/02 - Two.mp3"],
    ]}
    (root / JOURNAL_FILENAME).write_text(json.dumps(journal), encoding="utf-8")
    (root / "Artist/Album").mkdir(parents=True)
    (root / "Artist - Album/01 - One.mp3").replace(root / "Artist/Album/01 - One.mp3")

    dash = Dash()
    assert migrate_layout(SoundCloudIndex(root), dash) == 4
    assert not dash.errors
    assert (root / "Artist/Album/01 - One.lrc").exists()
    assert not (root / "Artist - Album").exists()
    index = json.loads((root / ".sc_index.json").read_text(encoding="utf-8"))
    assert index["1"]["path"] == "Artist/Album/01 - One.mp3"
    assert "Artist/Album/01 - One.mp3" in (root / "Artist - Album.m3u8").read_text(encoding="utf-8")
    assert not (root / JOURNAL_FILENAME).exists()


def test_existing_file_is_never_overwritten(tmp_path):
    root = tmp_path / "SoundCloud"
    _track(root, "Artist - Album/01 - One.mp3", "Artist", "Album", "1")
    taken = _track(root, "Artist/Album/01 - One.mp3", "Artist", "Album", "5", lrc=False)
    assert migrate_layout(SoundCloudIndex(root), Dash()) == 1
    assert read_tags(taken)["soundcloud_id"] == "5"
    moved = root / "Artist/Album/01 - One [1].mp3"
    assert read_tags(moved)["soundcloud_id"] == "1"
    assert moved.with_suffix(".lrc").exists()


def test_artist_named_like_an_old_folder(tmp_path):
    # Artist "X - Y": the new folder is inside the old "X - Y" album folder.
    root = tmp_path / "SoundCloud"
    _track(root, "X - Y/01 - A.mp3", "X", "Y", "1")
    _track(root, "X - Y - Z/01 - B.mp3", "X - Y", "Z", "2")
    assert migrate_layout(SoundCloudIndex(root), Dash()) == 2
    assert (root / "X/Y/01 - A.mp3").exists()
    assert (root / "X - Y/Z/01 - B.mp3").exists()
    assert migrate_layout(SoundCloudIndex(root), Dash()) == 0


def test_migration_is_in_the_debug_log(old_library, tmp_path):
    log = debuglog.DebugLog.in_directory(tmp_path / "logs")
    debuglog.activate(log)
    try:
        migrate_layout(SoundCloudIndex(old_library), Dash())
    finally:
        debuglog.deactivate()
    events = [json.loads(line) for line in log.path.read_text(encoding="utf-8").splitlines()]
    (event,) = [e for e in events if e.get("event") == "layout_migration"]
    assert {"from": "Artist - Album/01 - One.mp3", "to": "Artist/Album/01 - One.mp3"} in event["moves"]
