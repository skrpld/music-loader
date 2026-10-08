"""ID3v2.4 artist tags: TPE1 display string + TXXX:ARTISTS list."""
import pytest

pytest.importorskip("mutagen")
from mutagen.id3 import APIC, ID3, TPE1, USLT  # noqa: E402

from music_loader import tags as tg  # noqa: E402
from music_loader.artists import ArtistRegistry  # noqa: E402


def _tags(artists, **kw):
    return tg.TrackTags(title="Song", artists=artists, album="Alb", album_artist=artists[0], **kw)


def _file(tmp_path, name="a.mp3"):
    path = tmp_path / name
    path.write_bytes(b"")
    return path


def _legacy(path, artist_text, version=3):
    id3 = ID3()
    id3.add(TPE1(encoding=3, text=[artist_text]))
    id3.save(str(path), v2_version=version)


def test_multi_artist_round_trip(tmp_path):
    path = _file(tmp_path)
    tg.write_tags(path, _tags(["Alpha", "Beta"]))
    id3 = ID3(str(path))
    assert id3.version == (2, 4, 0)
    assert list(id3["TPE1"].text) == ["Alpha, Beta"]
    assert list(id3["TXXX:ARTISTS"].text) == ["Alpha", "Beta"]
    assert tg.read_tags(path)["artists"] == ["Alpha", "Beta"]


def test_name_with_slash_survives(tmp_path):
    path = _file(tmp_path)
    tg.write_tags(path, _tags(["AC/DC", "Beta"]))
    assert tg.read_tags(path)["artists"] == ["AC/DC", "Beta"]
    assert list(ID3(str(path))["TPE1"].text) == ["AC/DC, Beta"]


def test_legacy_v23_split_and_guard(tmp_path):
    path = _file(tmp_path)
    _legacy(path, "A/B")
    assert tg.read_tags(path)["artists"] == ["A", "B"]
    _legacy(path, "AC/DC/Beta")
    registry = ArtistRegistry(None)
    registry.register("AC/DC", 1)
    assert tg.read_tags(path, registry.is_known)["artists"] == ["AC/DC", "Beta"]
    _legacy(path, "AC/DC")
    assert tg.read_tags(path, registry.is_known)["artists"] == ["AC/DC"]


def test_v24_without_artists_frame_splits_on_comma_with_guard(tmp_path):
    path = _file(tmp_path)
    _legacy(path, "A, B", version=4)
    assert tg.read_tags(path)["artists"] == ["A", "B"]
    _legacy(path, "Tyler, The Creator", version=4)
    registry = ArtistRegistry(None)
    registry.register("Tyler, The Creator", 1)
    assert tg.read_tags(path, registry.is_known)["artists"] == ["Tyler, The Creator"]


def test_set_artists_migrates_v23_and_keeps_cover_and_lyrics(tmp_path):
    path = _file(tmp_path)
    id3 = ID3()
    id3.add(TPE1(encoding=3, text=["A/B"]))
    id3.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=b"img"))
    id3.add(USLT(encoding=3, lang="XXX", desc="", text="la la"))
    id3.save(str(path), v2_version=3)
    assert not tg.artists_current(path, ["A", "B"])
    assert tg.set_artists(path, ["A", "B"])
    assert tg.artists_current(path, ["A", "B"])
    out = ID3(str(path))
    assert out.version == (2, 4, 0)
    assert list(out["TPE1"].text) == ["A, B"]
    assert out.getall("APIC")[0].data == b"img"
    assert out.getall("USLT")[0].text == "la la"


def test_rewrite_keeps_lyrics_and_cover(tmp_path):
    path = _file(tmp_path)
    tg.write_tags(path, _tags(["A"]), cover=b"img")
    tg.embed_lyrics(path, "words")
    tg.write_tags(path, _tags(["A", "B"]))
    out = ID3(str(path))
    assert out.version == (2, 4, 0)
    assert out.getall("APIC")[0].data == b"img"
    assert tg.has_embedded_lyrics(path)
    assert tg.remove_lyrics(path)
    assert ID3(str(path)).version == (2, 4, 0)


def test_clean_artists_dedupes():
    assert tg.clean_artists(["A", " a ", "", "B"]) == ["A", "B"]
    assert tg.set_artists is not None and tg.format_artists([]) == ""
