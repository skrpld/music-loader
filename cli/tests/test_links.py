import pytest

from music_loader.links import parse_link, redact_url

SPOTIFY_ID = "4uLU6hMCjMI75M1A2tKUQC"


@pytest.mark.parametrize(
    "text, url, kind",
    [
        (f"https://open.spotify.com/track/{SPOTIFY_ID}?si=abc", f"https://open.spotify.com/track/{SPOTIFY_ID}", "track"),
        (f"https://open.spotify.com/intl-de/album/{SPOTIFY_ID}", f"https://open.spotify.com/album/{SPOTIFY_ID}", "album"),
        (f"spotify:playlist:{SPOTIFY_ID}", f"https://open.spotify.com/playlist/{SPOTIFY_ID}", "playlist"),
        (f"  https://open.spotify.com/artist/{SPOTIFY_ID}  ", f"https://open.spotify.com/artist/{SPOTIFY_ID}", "artist"),
    ],
)
def test_spotify_links_are_normalized(text, url, kind):
    link = parse_link(text)
    assert link is not None
    assert (link.service, link.url, link.kind) == ("spotify", url, kind)


@pytest.mark.parametrize(
    "text, url, kind, section",
    [
        ("https://soundcloud.com/artist/song?in=x&utm_source=y", "https://soundcloud.com/artist/song", "track", ""),
        ("https://m.soundcloud.com/artist", "https://soundcloud.com/artist", "profile", ""),
        ("https://soundcloud.com/artist/likes", "https://soundcloud.com/artist/likes", "section", "likes"),
        ("https://soundcloud.com/artist/sets/album", "https://soundcloud.com/artist/sets/album", "set", ""),
        ("https://soundcloud.com/artist/song/s-AbC123", "https://soundcloud.com/artist/song/s-AbC123", "track", ""),
        ("https://on.soundcloud.com/xyz", "https://on.soundcloud.com/xyz", "short", ""),
    ],
)
def test_soundcloud_links_are_normalized(text, url, kind, section):
    link = parse_link(text)
    assert link is not None
    assert (link.service, link.url, link.kind, link.section) == ("soundcloud", url, kind, section)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "-o /etc",
        "--exec=rm",
        "https://example.com/?soundcloud.com",
        "https://evil.example/open.spotify.com/track/" + SPOTIFY_ID,
        "https://open.spotify.com/track/short",
        "ftp://soundcloud.com/artist",
        "https://soundcloud.com/search",
        "https://soundcloud.com/a/b/c/d",
    ],
)
def test_other_input_is_rejected(text):
    assert parse_link(text) is None


def test_private_soundcloud_token_is_kept_for_download_but_redacted_for_display():
    link = parse_link("https://soundcloud.com/artist/sets/demo?secret_token=s-XyZ&si=1")
    assert link is not None
    assert link.url == "https://soundcloud.com/artist/sets/demo?secret_token=s-XyZ"
    assert redact_url(link.url) == "https://soundcloud.com/artist/sets/demo"
    assert redact_url("https://soundcloud.com/artist/song/s-AbC123") == "https://soundcloud.com/artist/song"
    assert redact_url(None) == ""
