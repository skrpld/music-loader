"""One naming scheme for both sources: title cleanup, folder and file names,
multi-disc, Unicode, truncation, compilations, and the --recheck helpers."""
import json

import pytest

from music_loader import naming, spotify
from music_loader.config import AppConfig
from music_loader.playlist import apply_moves
from music_loader.text_utils import clean_promo, parse_soundcloud_title, variant_markers


# -- SoundCloud title parsing (examples from the text_utils docstring) ---------------------------
@pytest.mark.parametrize("raw, uploader, artists, featured, title", [
    ("TARABANDZ + platov + WHITENER - under heaven [prod. haru matsui]", "x",
     ["TARABANDZ", "platov", "WHITENER"], [], "under heaven"),
    ("WHITENER, platov - heroin chic (feat aquakey) hexd", "x",
     ["WHITENER", "platov"], ["aquakey"], "heroin chic (feat. aquakey) hexd"),
    ("haru matsui - godline (prod. hm9600) *music video in description*", "x",
     ["haru matsui"], [], "godline"),
    ("I Shot The Sheriff (p. systematik)", "x", ["x"], [], "I Shot The Sheriff"),
    ("WHITENER+BENJAMINBENZ+AQUAKEY+TARABANDZ-STONE COLD(Prod SPACE NIKExQIO)", "x",
     ["WHITENER", "BENJAMINBENZ", "AQUAKEY", "TARABANDZ"], [], "STONE COLD"),
    ("✦ Artist ✦ - Song 🔥 #phonk | Some Label", "u", ["Artist"], [], "Song"),
    ("Artist - Song (Sped Up) [2019] OUT NOW", "u", ["Artist"], [], "Song (Sped Up)"),
])
def test_parse_titles(raw, uploader, artists, featured, title):
    parsed = parse_soundcloud_title(raw, uploader)
    assert (parsed.artists, parsed.featured, parsed.title) == (artists, featured, title)


@pytest.mark.parametrize("raw, expected", [
    ("Song #phonk #drift", "Song"),
    ("Track #1", "Track #1"),
    ("Song | Some Label", "Song"),
    ("Song | Phonk Records", "Song"),
    ("Song | Live in Paris", "Song | Live in Paris"),
    ("Song | Part Two", "Song | Part Two"),
    ("Song | Sped Up", "Song | Sped Up"),
    ("Song [2019]", "Song"),
    ("Song (1999 Remaster)", "Song (1999 Remaster)"),
    ("Song (Explicit)", "Song"),
    ("Song OUT NOW", "Song"),
    ("Song - BUY = FREE DL", "Song"),
    ("Song (BUY = FREE DL)", "Song"),
    ("Song | FREE DOWNLOAD", "Song"),
    ("✦ Song ★ 🔥🔥", "Song"),
    ("Song (Clean Bandit Remix)", "Song (Clean Bandit Remix)"),
    ("Buy U a Drank", "Buy U a Drank"),
])
def test_clean_promo(raw, expected):
    assert clean_promo(raw) == expected


def test_variant_markers_survive_cleanup():
    for title in ("Song (Sped Up)", "Song (Slowed + Reverb)", "Song (VIP)", "Song (Live)"):
        assert variant_markers(clean_promo(title)) == variant_markers(title)


# -- Unicode ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("raw, expected", [
    ("Ｔｉｔｌｅ　１２３", "Title 123"),
    ("Café", "Café"),                       # NFC
    ("zero​width﻿", "zerowidth"),
    ("“Quoted” ‘text’", '"Quoted" \'text\''),
    ("✦ Star ★ ☆ ♥ ❤️ 🔥", "Star"),
    ("Привет, мир", "Привет, мир"),                          # Cyrillic stays
    ("日本語のタイトル", "日本語のタイトル"),                      # CJK stays
    ("Beyoncé – Déjà Vu", "Beyoncé - Déjà Vu"),
    ("  many   spaces\t here ", "many spaces here"),
    ("30° C♯ minor", "30° C♯ minor"),
])
def test_clean_text(raw, expected):
    assert naming.clean_text(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("AC/DC", "AC-DC"),
    ("Song: Live", "Song - Live"),
    ('Why? "Not*"', "Why 'Not'"),
    ("A | B", "A - B"),
    ("12:30", "12-30"),
    ("name...", "name"),
    ("CON", "_CON"),
    ("", "Unknown"),
    ("???", "Unknown"),
])
def test_safe_name(raw, expected):
    assert naming.safe_name(raw) == expected


# -- Spotify titles ----------------------------------------------------------------------------------
@pytest.mark.parametrize("raw, expected", [
    ("Song - Remastered 2011", "Song (Remastered 2011)"),
    ("Heroes - 2017 Remaster", "Heroes (2017 Remaster)"),
    ("Song - Radio Edit", "Song (Radio Edit)"),
    ('Song - From "The Movie"', 'Song (From "The Movie")'),
    ("Song - Live at Wembley", "Song (Live at Wembley)"),
    ("Song (ft. Guest)", "Song (feat. Guest)"),
    ("Song [featuring Guest]", "Song [feat. Guest]"),
    ("Song ft. A & B", "Song (feat. A & B)"),
    ("Song (feat. Guest)", "Song (feat. Guest)"),
    ("Mr. Brightside", "Mr. Brightside"),
    ("Love - With You", "Love - With You"),
    ("Song - 2000 Miles", "Song - 2000 Miles"),
    ("Song - Live - Remastered 2011", "Song - Live (Remastered 2011)"),
    ("Song - Intro", "Song - Intro"),
])
def test_spotify_title(raw, expected):
    assert naming.spotify_title(raw) == expected


# -- folders and files -----------------------------------------------------------------------------
def test_album_folder_and_filename():
    assert naming.album_folder("AC/DC", "Back in Black") == "AC-DC - Back in Black"
    assert naming.track_filename("Hells Bells", 1, 10) == "01 - Hells Bells.mp3"
    assert naming.track_filename("Song", 5) == "05 - Song.mp3"
    assert naming.track_filename("Song", 5, total=120) == "005 - Song.mp3"


def test_multi_disc_only_when_needed():
    assert naming.track_filename("Song", 1, 12, disc=2, disc_total=2) == "2-01 - Song.mp3"
    assert naming.track_filename("Song", 1, 12, disc=1, disc_total=1) == "01 - Song.mp3"
    assert naming.track_filename("A", 1, 5, 1, 2) != naming.track_filename("A", 1, 5, 2, 2)


def test_truncation_keeps_words_extension_and_brackets():
    name = naming.track_filename("long word " * 40 + "(tail part that is cut)", 3)
    assert name.endswith(".mp3") and len(name.encode()) <= naming.MAX_NAME_BYTES
    assert name.startswith("03 - long word")
    assert name.count("(") == name.count(")")
    cyrillic = naming.track_filename("слово " * 60, 3)
    assert len(cyrillic.encode()) <= naming.MAX_NAME_BYTES
    assert cyrillic.endswith("слово.mp3")
    folder = naming.album_folder("Artist", "Album " * 60)
    assert len(folder.encode()) <= naming.MAX_NAME_BYTES


def test_compilation_detection():
    various = [["A"], ["B"], ["C"], ["D"], ["E"]]
    assert naming.album_artist_for("Label Mix", various) == naming.VARIOUS_ARTISTS
    assert naming.album_artist_for("Various Artists", [["A"]]) == naming.VARIOUS_ARTISTS
    assert naming.album_artist_for("Artist", [["Artist"], ["Artist", "Guest"], ["Artist"], ["Artist"]]) == "Artist"
    assert naming.album_artist_for("Artist", [["Artist"], ["X"]]) == "Artist"       # too few tracks
    guests = [["Artist", "A"], ["Artist", "B"], ["Artist", "C"], ["Artist", "D"], ["Artist", "E"]]
    assert naming.album_artist_for("Artist", guests) == "Artist"


def test_same_rules_for_both_sources():
    from music_loader.soundcloud_meta import AlbumContext, build_meta
    from music_loader.artists import ArtistRegistry
    info = {"id": 1, "title": "Artist - Wh*t? Song: Live", "uploader": "Artist",
            "webpage_url": "https://soundcloud.com/a/b"}
    album = AlbumContext(title="Album: Deluxe", artist="Artist", set_type="album", position=3, total=12)
    meta = build_meta(info, album, ArtistRegistry(None))
    assert meta.folder == naming.album_folder("Artist", "Album: Deluxe")
    assert meta.filename == naming.track_filename(meta.title, 3, 12)
    compilation = AlbumContext(title="Mix", artist="Label", set_type="compilation", position=1, total=9)
    assert build_meta(info, compilation, ArtistRegistry(None)).tags.album_artist == "Various Artists"


# -- Spotify: library path and renaming spotDL's output ------------------------------------------------
def _song(**extra):
    data = {
        "name": "Hell: Remastered - Remastered 2011", "artists": ["Band"], "album_artist": "Band",
        "album_name": "Album", "album_id": "al1", "track_number": 1, "tracks_count": 14,
        "disc_number": 2, "disc_count": 2, "url": "https://open.spotify.com/track/abc",
    }
    data.update(extra)
    return data


def test_assign_targets_multi_disc(tmp_path):
    from music_loader.artists import ArtistRegistry
    song = spotify._Song(_song(), tmp_path / "Band - Album" / "01 - Hell.mp3")
    spotify._assign_targets([song], tmp_path, ArtistRegistry(None))
    assert song.work == tmp_path / "Band - Album" / "01 - Hell.mp3"
    assert song.path == tmp_path / "Band - Album" / "2-01 - Hell - Remastered (Remastered 2011).mp3"


def test_place_downloads_renames_and_resolves_collisions(tmp_path):
    pytest.importorskip("mutagen")
    from mutagen.id3 import ID3, WOAS
    config = AppConfig(music_dir=tmp_path, soundcloud_dir=tmp_path / "sc")

    def make(path, url):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        id3 = ID3()
        id3.add(WOAS(url=url))
        id3.save(str(path))

    class Log:
        def log(self, *a): pass
        def log_error(self, *a): pass

    first = spotify._Song({"url": "u1"}, tmp_path / "New" / "01 - Song.mp3", tmp_path / "Old" / "01 - Song.mp3")
    second = spotify._Song({"url": "u2/x"}, tmp_path / "New" / "01 - Song.mp3", tmp_path / "Old" / "02 - Song.mp3")
    make(first.work, "u1")
    make(second.work, "u2/x")
    spotify._place_downloads([first, second], config, Log())
    assert first.path.exists() and not first.work.exists()
    assert second.path.name == "01 - Song [x].mp3" and second.path.exists()
    assert not (tmp_path / "Old").exists()


def test_stage_for_spotdl_narrows_the_song_list(tmp_path):
    config = AppConfig(music_dir=tmp_path, soundcloud_dir=tmp_path / "sc")
    done = spotify._Song({"url": "u1"}, tmp_path / "A" / "01 - X.mp3", tmp_path / "B" / "01 - X.mp3")
    new = spotify._Song({"url": "u2"}, tmp_path / "A" / "02 - Y.mp3", tmp_path / "B" / "02 - Y.mp3")
    save = tmp_path / "q.spotdl"
    save.write_text("[]")

    class Log:
        def log_error(self, *a): pass

    todo = spotify._stage_for_spotdl([done, new], {"u1"}, config, save, Log())
    assert todo == [new]
    assert json.loads(save.read_text()) == [{"url": "u2"}]


# -- playlists and sidecars follow a rename --------------------------------------------------------------
def test_apply_moves_updates_playlists(tmp_path):
    (tmp_path / "set.m3u8").write_text("#EXTM3U\nOld - A/01 - x.mp3\nkeep/02 - y.mp3\n", encoding="utf-8")
    (tmp_path / "all.m3u8").write_text("#EXTM3U\nOld - A/01 - x.mp3\n", encoding="utf-8")
    moves = [(tmp_path / "Old - A" / "01 - x.mp3", tmp_path / "New - A" / "01 - x.mp3")]
    assert apply_moves(tmp_path, moves) == 2
    assert (tmp_path / "set.m3u8").read_text(encoding="utf-8") == "#EXTM3U\nNew - A/01 - x.mp3\nkeep/02 - y.mp3\n"
    assert apply_moves(tmp_path, []) == 0


def test_move_with_sidecars_takes_the_lrc(tmp_path):
    from music_loader.paths import move_with_sidecars
    source = tmp_path / "a" / "x.mp3"
    source.parent.mkdir()
    source.write_bytes(b"1")
    source.with_suffix(".lrc").write_text("[00:01.00]hi")
    target = tmp_path / "b" / "y.mp3"
    move_with_sidecars(source, target)
    assert target.exists() and target.with_suffix(".lrc").exists()
    assert not source.with_suffix(".lrc").exists()
