"""SoundCloud title parsing on real-world titles, and the formatting settings.

The first table comes from a debug log of real runs (`--debug-log`, the
`title_parsed` / `final_meta` events and the "Already exists" file names of
an older library); the second from the usual shapes of SoundCloud uploads
(labels, repost channels, promo noise, versions, Cyrillic).
"""
import pytest

from music_loader import naming, server, worker
from music_loader.artists import ArtistRegistry
from music_loader.cli import parse_args
from music_loader.config import SoundCloudFormat, validate_filename_template
from music_loader.soundcloud_meta import AlbumContext, build_meta
from music_loader.text_utils import (
    clean_promo,
    description_producers,
    parse_soundcloud_title,
    strip_decorations,
    variant_markers,
)

KNOWN = {"haru matsui", "platov", "aquakey", "benjamingotbenz", "madhoe2000", "elya14062001", "хестон"}


def _known(name: str) -> bool:
    return name.casefold() in KNOWN


PROD_DESC = "prod by sg, haru matsui, playasin\nmix by haru matsui\ncover by platov"

# raw title, uploader, description, artists, featured, producers, title, version markers
CASES = [
    # -- from the debug log ------------------------------------------------------------------
    ("best summer  (whyjuzo)", "platov  (@platovoplomo)", "",
     ["platov"], [], [], "best summer (whyjuzo)", []),
    ("platov - manchester bloodfruit juice", "platov  (@platovoplomo)", PROD_DESC,
     ["platov"], [], [], "manchester bloodfruit juice", []),
    ("platov - wpcwe (Skit)", "platov  (@platovoplomo)", PROD_DESC,
     ["platov"], [], [], "wpcwe (Skit)", []),
    ("platov - nocturne (feat elya14062001)", "platov  (@platovoplomo)", PROD_DESC,
     ["platov"], ["elya14062001"], [], "nocturne (feat. elya14062001)", []),
    ("platov - moscow never sleep 3k55", "platov  (@platovoplomo)", PROD_DESC,
     ["platov"], [], [], "moscow never sleep 3k55", []),
    ("platov, haru matsui - diesel (playasin)", "platov  (@platovoplomo)", "",
     ["platov", "haru matsui"], [], [], "diesel (playasin)", []),
    # The same credit is recognized once the description names the producer.
    ("platov, haru matsui - diesel (playasin)", "platov  (@platovoplomo)", PROD_DESC,
     ["platov", "haru matsui"], [], ["playasin"], "diesel", []),
    ("platov - polo raincoat ( whyjuzo, yui;seven envyy j)", "platov  (@platovoplomo)", "",
     ["platov"], [], [], "polo raincoat (whyjuzo, yui;seven envyy j)", []),
    ("platov - waterfall  (manyice)", "platov  (@platovoplomo)", "",
     ["platov"], [], [], "waterfall (manyice)", []),
    ("yung millionaire ((haru matsui + jack13))", "metlast", "",
     ["metlast"], [], ["haru matsui", "jack13"], "yung millionaire", []),
    ("thraxx interlude ((me ft. haru matsui))", "metlast", "",
     ["metlast"], ["haru matsui"], ["me"], "thraxx interlude (feat. haru matsui)", []),
    ("get2thebag ((metlast ft. haru matsui))", "metlast", "",
     ["metlast"], ["haru matsui"], ["metlast"], "get2thebag (feat. haru matsui)", []),
    ("splash ((metlast + me)) mv in desc", "metlast", "",
     ["metlast"], [], ["metlast", "me"], "splash", []),
    ("1million ((cryst)) ALBUM OTW", "metlast", "",
     ["metlast"], [], ["cryst"], "1million", []),
    ("WW3 ((hm + me)) new mixtape otw", "metlast", "",
     ["metlast"], [], ["hm", "me"], "WW3", []),
    ("zooted (me + sg) mv on yt", "metlast", "",
     ["metlast"], [], [], "zooted (me + sg)", []),
    ("im ok prod subjxct 5", "metlast", "",
     ["metlast"], [], ["subjxct 5"], "im ok", []),
    ("CAST A SPELL (🎃", "WHITENER", "",
     ["WHITENER"], [], [], "CAST A SPELL", []),
    ("VAMPFLOW #UNRELEASED!", "WHITENER", "",
     ["WHITENER"], [], [], "VAMPFLOW", []),
    ("Морозы (+elya14062001)", "platov", "",
     ["platov"], ["elya14062001"], [], "Морозы (feat. elya14062001)", []),
    ("you n me (+ benjamingotbenz)", "platov", "",
     ["platov"], ["benjamingotbenz"], [], "you n me (feat. benjamingotbenz)", []),
    ("Mudstock feat. CODE80 (mxstrakh)", "TARABANDZ", "",
     ["TARABANDZ"], ["CODE80"], [], "Mudstock (feat. CODE80) (mxstrakh)", []),
    ("Ksubi feat. TARABANDZ, beatbotanic (mxstrakh)", "WHITENER", "",
     ["WHITENER"], ["TARABANDZ", "beatbotanic"], [], "Ksubi (feat. TARABANDZ, beatbotanic) (mxstrakh)", []),
    ("TREAD-ANTHEM (feat. JUNKIE MANE x TRENCHB66F)", "WHITENER", "",
     ["WHITENER"], ["JUNKIE MANE", "TRENCHB66F"], [], "TREAD-ANTHEM (feat. JUNKIE MANE, TRENCHB66F)", []),
    ("Heavy Rain Romantic (feat. хабип мацуевич, AQUAKEY)", "WHITENER", "",
     ["WHITENER"], ["хабип мацуевич", "AQUAKEY"], [], "Heavy Rain Romantic (feat. хабип мацуевич, AQUAKEY)", []),
    ("35 hp + хестон + platov", "WHITENER", "",
     ["WHITENER"], ["хестон", "platov"], [], "35 hp (feat. хестон, platov)", []),
    ("хестон - Тесно (Garcon Maigre Cover)", "хестон", "",
     ["хестон"], [], [], "Тесно (Garcon Maigre Cover)", ["cover"]),
    ("09. Pitstop W Aquakey & Platov", "TARABANDZ", "",
     ["TARABANDZ"], ["Aquakey", "Platov"], [], "Pitstop (feat. Aquakey, Platov)", []),
    # -- usual shapes --------------------------------------------------------------------------
    ("Lil Foo - Night Drive (feat. Big Bar)", "Nightfall Records", "",
     ["Lil Foo"], ["Big Bar"], [], "Night Drive (feat. Big Bar)", []),
    ("Lil Foo x Big Bar - Night Drive", "Trap Nation", "",
     ["Lil Foo", "Big Bar"], [], [], "Night Drive", []),
    ("Night Drive (prod. Zed)", "Lil Foo", "",
     ["Lil Foo"], [], ["Zed"], "Night Drive", []),
    ("Night Drive [Free DL]", "Lil Foo", "",
     ["Lil Foo"], [], [], "Night Drive", []),
    ("Lil Foo - Night Drive (Official Audio)", "Lil Foo", "",
     ["Lil Foo"], [], [], "Night Drive", []),
    ("🔥 Night Drive 🔥 [FREE DOWNLOAD]", "Lil Foo", "",
     ["Lil Foo"], [], [], "Night Drive", []),
    ("Night Drive (Remix)", "Lil Foo", "",
     ["Lil Foo"], [], [], "Night Drive (Remix)", ["remix"]),
    ("Lil Foo - Night Drive (Big Bar Remix)", "Big Bar", "",
     ["Lil Foo"], [], [], "Night Drive (Big Bar Remix)", ["remix"]),
    ("Night Drive (Sped Up)", "Lil Foo", "",
     ["Lil Foo"], [], [], "Night Drive (Sped Up)", ["sped up"]),
    ("Night Drive (Slowed + Reverb)", "Lil Foo", "",
     ["Lil Foo"], [], [], "Night Drive (Slowed + Reverb)", ["slowed"]),
    ("Lil Foo - Night Drive (Live)", "Lil Foo", "",
     ["Lil Foo"], [], [], "Night Drive (Live)", ["live"]),
    ("ПЛАТОВ х хестон - Морозы", "хестон", "",
     ["ПЛАТОВ", "хестон"], [], [], "Морозы", []),
    ("Lil Foo - Night Drive | Nightfall Records", "Nightfall Records", "",
     ["Lil Foo"], [], [], "Night Drive", []),
    ("Night Drive", "Lil Foo  (@lilfoo)", "",
     ["Lil Foo"], [], [], "Night Drive", []),
    ("Lil Foo – Night Drive (feat. Big Bar) [prod. Zed]", "Lil Foo", "",
     ["Lil Foo"], ["Big Bar"], ["Zed"], "Night Drive (feat. Big Bar)", []),
]


@pytest.mark.parametrize("raw, uploader, description, artists, featured, producers, title, versions", CASES)
def test_real_titles(raw, uploader, description, artists, featured, producers, title, versions):
    parsed = parse_soundcloud_title(raw, uploader, [], _known, producer_hints=description_producers(description))
    assert parsed.artists == artists
    assert parsed.featured == featured
    assert parsed.producers == producers
    assert parsed.title == title
    assert sorted(variant_markers(parsed.title)) == versions


def test_enough_real_titles():
    assert len(CASES) >= 30


@pytest.mark.parametrize("raw, expected", [
    ("#J20 XTREME SPORT", "#J20 XTREME SPORT"),          # an album name starting with a hashtag
    ("VAMPFLOW #UNRELEASED!", "VAMPFLOW"),
    ("Song #phonk #drift", "Song"),
    ("splash ((metlast + me)) mv in desc", "splash ((metlast + me))"),
    ("Song new mixtape otw", "Song"),
    ("Help Is On The Way", "Help Is On The Way"),
    ("CAST A SPELL (🎃", "CAST A SPELL"),
    ("polo raincoat ( whyjuzo )", "polo raincoat (whyjuzo)"),
])
def test_clean_promo_real_cases(raw, expected):
    assert clean_promo(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("platov  (@platovoplomo)", "platov"),
    ("✦ platov ✦ [@platov]", "platov"),
    ("Some (Band)", "Some (Band)"),
])
def test_display_name_handle(raw, expected):
    assert strip_decorations(raw) == expected


@pytest.mark.parametrize("text, expected", [
    (PROD_DESC, ["sg", "haru matsui", "playasin"]),
    ("Prod. by: Zed & Nova\nout now", ["Zed", "Nova"]),
    ("produced by Zed", ["Zed"]),
    ("free download in the link", []),
    ("", []),
])
def test_description_producers(text, expected):
    assert description_producers(text) == expected


# -- build_meta: the debug-log tracks end up in one folder per real artist -------------------------------
def test_album_artist_without_handle():
    info = {"id": "2224182200", "title": "best summer  (whyjuzo)", "uploader": "platov  (@platovoplomo)",
            "webpage_url": "https://soundcloud.com/creamcreme/best-summer-whyjuzo-3"}
    album = AlbumContext(title="#J20 XTREME SPORT", artist="platov  (@platovoplomo)", set_type="album",
                         position=3, total=10)
    meta = build_meta(info, album, ArtistRegistry(None))
    assert meta.tags.album_artist == "platov"
    assert meta.tags.album == "#J20 XTREME SPORT"
    assert meta.folder == "platov/#J20 XTREME SPORT"
    single = build_meta(info, None, ArtistRegistry(None))
    assert single.folder.split("/")[0] == "platov"


# -- formatting settings --------------------------------------------------------------------------------
_INFO = {
    "id": "7", "uploader": "Lil Foo", "webpage_url": "https://soundcloud.com/lilfoo/night-drive",
    "title": "Lil Foo - Night Drive (feat. Big Bar) (Sped Up) [prod. Zed] [Free DL]",
}


def _meta(fmt: SoundCloudFormat | None = None, info=None, album=None):
    return build_meta(dict(info or _INFO), album, ArtistRegistry(None), fmt or SoundCloudFormat())


def test_default_format_keeps_todays_output():
    meta = _meta()
    assert meta.tags.title == "Night Drive (feat. Big Bar) (Sped Up)"
    assert meta.tags.artists == ["Lil Foo", "Big Bar"]
    assert meta.filename == "01 - Night Drive (feat. Big Bar) (Sped Up).mp3"
    assert meta.tags.album == "Night Drive (Sped Up) - Single"


def test_feat_in_artist_tag_only():
    meta = _meta(SoundCloudFormat(feat="artist"))
    assert meta.tags.title == "Night Drive (Sped Up)"
    assert meta.tags.artists == ["Lil Foo", "Big Bar"]


def test_feat_in_title_only():
    meta = _meta(SoundCloudFormat(feat="title"))
    assert meta.tags.title == "Night Drive (feat. Big Bar) (Sped Up)"
    assert meta.tags.artists == ["Lil Foo"]


def test_keep_producers():
    meta = _meta(SoundCloudFormat(keep_producers=True))
    assert meta.tags.title == "Night Drive (feat. Big Bar) (Sped Up) (prod. Zed)"


def test_drop_versions():
    meta = _meta(SoundCloudFormat(keep_versions=False))
    assert meta.tags.title == "Night Drive (feat. Big Bar)"
    assert meta.tags.album == "Night Drive - Single"
    # Lyrics and the fallback still compare the real version.
    assert variant_markers(meta.title) == {"sped up"}


def test_keep_promo():
    meta = _meta(SoundCloudFormat(strip_promo=False))
    assert meta.tags.title == "Night Drive (feat. Big Bar) (Sped Up) [Free DL]"


def test_filename_template():
    album = AlbumContext(title="Nights", artist="Lil Foo", set_type="album", position=3, total=12)
    meta = _meta(SoundCloudFormat(filename_template="{track:02} {artist} - {title}"), album=album)
    assert meta.filename == "03 Lil Foo - Night Drive (feat. Big Bar) (Sped Up).mp3"
    meta = _meta(SoundCloudFormat(filename_template="{title}"), album=album)
    assert meta.filename == "Night Drive (feat. Big Bar) (Sped Up).mp3"


def test_filename_template_keeps_name_limits():
    title = "word " * 80
    name = naming.track_filename(title, 3, template="{track} {album} - {title}", fields={"album": "LP"})
    assert len(name.encode("utf-8")) <= naming.MAX_NAME_BYTES
    assert name.startswith("03 LP - word")
    assert name.endswith(".mp3")


@pytest.mark.parametrize("template", ["{title", "{track}", "{title}/{track}", "{nope} {title}",
                                      "{title:>3}", "..\\{title}", ""])
def test_bad_filename_templates(template):
    with pytest.raises(ValueError):
        validate_filename_template(template)


@pytest.mark.parametrize("template", ["{track} - {title}", "{track:02} {title}", "{artist} - {title}",
                                      "{album_artist} - {album} - {track} {title}"])
def test_good_filename_templates(template):
    validate_filename_template(template)


# -- the settings reach the pipeline from every front-end ------------------------------------------------
def test_cli_flags():
    args = parse_args(["--soundcloud-feat", "artist", "--soundcloud-keep-producers",
                       "--soundcloud-drop-versions", "--soundcloud-keep-promo",
                       "--soundcloud-filename", "{track} {title}", "x"])
    fmt = SoundCloudFormat.from_args(args)
    assert fmt == SoundCloudFormat(feat="artist", keep_producers=True, keep_versions=False,
                                   strip_promo=False, filename_template="{track} {title}")
    assert SoundCloudFormat.from_args(parse_args(["x"])) == SoundCloudFormat()


def test_cli_rejects_bad_template():
    with pytest.raises(SystemExit):
        parse_args(["--soundcloud-filename", "{title}/x", "x"])


def test_server_options_and_worker():
    options = server._parse_options({"soundcloud_feat": "title", "soundcloud_keep_producers": True,
                                     "soundcloud_keep_versions": False, "soundcloud_strip_promo": False,
                                     "soundcloud_filename": "{track:02} {title}"})
    config = worker.build_config({"output": "/tmp/x", **options})
    assert config.soundcloud_format == SoundCloudFormat(
        feat="title", keep_producers=True, keep_versions=False, strip_promo=False,
        filename_template="{track:02} {title}")
    defaults = server._parse_options({})
    assert worker.build_config({"output": "/tmp/x", **defaults}).soundcloud_format == SoundCloudFormat()
    # An old job file (no formatting keys) gets today's behaviour.
    assert worker.build_config({"output": "/tmp/x"}).soundcloud_format == SoundCloudFormat()


@pytest.mark.parametrize("options", [
    {"soundcloud_feat": "nowhere"},
    {"soundcloud_keep_versions": "yes"},
    {"soundcloud_filename": "{title}/{track}"},
    {"soundcloud_filename": 5},
])
def test_server_rejects_bad_format_options(options):
    with pytest.raises(server._RequestError):
        server._parse_options(options)


def test_server_announces_formatting():
    assert "soundcloud_format" in server.FEATURES
