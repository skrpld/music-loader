"""Validation and classification of input links.

Links used to be recognized by a substring test ("soundcloud.com" anywhere
in the text), so `https://any-site/?soundcloud.com` was handed to yt-dlp,
which supports well over a thousand other sites, and a line starting with
"-" reached spotdl/yt-dlp as a command-line option. Only real Spotify and
SoundCloud URLs are accepted now.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SPOTIFY_TYPES = {"track", "album", "playlist", "artist"}
_SPOTIFY_ID_RE = re.compile(r"^[A-Za-z0-9]{22}$")
_SPOTIFY_URI_RE = re.compile(r"^spotify:(track|album|playlist|artist):([A-Za-z0-9]{22})$")

_SOUNDCLOUD_HOSTS = {"soundcloud.com", "www.soundcloud.com", "m.soundcloud.com"}
_SOUNDCLOUD_SHORT_HOSTS = {"on.soundcloud.com", "snd.sc"}
_SOUNDCLOUD_API_HOSTS = {"api.soundcloud.com", "api-v2.soundcloud.com"}

# Profile sub-pages yt-dlp understands.
SOUNDCLOUD_SECTIONS = {"tracks", "albums", "sets", "reposts", "likes", "spotlight"}
# First path segments that are SoundCloud pages, not user names.
_RESERVED = {
    "discover", "charts", "search", "stream", "you", "feed", "upload", "pages",
    "settings", "messages", "notifications", "stations", "people", "terms-of-use",
    "jobs", "imprint", "mobile", "tags", "popular", "pro", "go", "signin", "logout",
}
_SECRET_SEGMENT_RE = re.compile(r"^s-[A-Za-z0-9]+$")


@dataclass(frozen=True)
class Link:
    service: str              # "spotify" | "soundcloud"
    url: str                  # normalized URL handed to the tools
    kind: str                 # spotify: track/album/playlist/artist;
                              # soundcloud: track/set/profile/section/short/api
    user: str = ""            # SoundCloud user slug (profile/section/track/set)
    section: str = ""         # SoundCloud profile section


def _parse_spotify(text: str) -> Link | None:
    uri = _SPOTIFY_URI_RE.match(text)
    if uri:
        kind, item_id = uri.groups()
        return Link("spotify", f"https://open.spotify.com/{kind}/{item_id}", kind)
    parts = urlsplit(text)
    if parts.scheme not in {"http", "https"} or parts.hostname != "open.spotify.com":
        return None
    segments = [segment for segment in parts.path.split("/") if segment]
    if segments and segments[0].startswith("intl-"):
        segments = segments[1:]
    if len(segments) != 2 or segments[0] not in _SPOTIFY_TYPES:
        return None
    if not _SPOTIFY_ID_RE.match(segments[1]):
        return None
    return Link("spotify", f"https://open.spotify.com/{segments[0]}/{segments[1]}", segments[0])


def _parse_soundcloud(text: str) -> Link | None:
    parts = urlsplit(text)
    if parts.scheme not in {"http", "https"}:
        return None
    host = (parts.hostname or "").lower()
    if host in _SOUNDCLOUD_SHORT_HOSTS:
        if not parts.path.strip("/"):
            return None
        return Link("soundcloud", urlunsplit(("https", host, parts.path, "", "")), "short")
    if host in _SOUNDCLOUD_API_HOSTS:
        return Link("soundcloud", urlunsplit(("https", host, parts.path, parts.query, "")), "api")
    if host not in _SOUNDCLOUD_HOSTS:
        return None

    segments = [segment for segment in parts.path.split("/") if segment]
    if not segments or segments[0].lower() in _RESERVED and segments[0].lower() != "discover":
        return None
    # Keep only the query parameters that change what the link points to
    # (a private link's token); "?in=..." / "?si=..." / utm tags are noise.
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if k == "secret_token"])
    url = urlunsplit(("https", "soundcloud.com", "/" + "/".join(segments), query, ""))

    if segments[0].lower() == "discover":
        return Link("soundcloud", url, "set")
    user = segments[0]
    if len(segments) == 1:
        return Link("soundcloud", url, "profile", user=user)
    second = segments[1].lower()
    if len(segments) == 2 and second in SOUNDCLOUD_SECTIONS:
        return Link("soundcloud", url, "section", user=user, section=second)
    if second == "sets" and len(segments) >= 3:
        return Link("soundcloud", url, "set", user=user)
    if len(segments) == 2 or (len(segments) == 3 and _SECRET_SEGMENT_RE.match(segments[2])):
        return Link("soundcloud", url, "track", user=user)
    return None


def parse_link(text: str) -> Link | None:
    """Returns a validated, normalized link, or None for anything else."""
    text = (text or "").strip()
    if not text or text.startswith("-"):
        return None
    return _parse_spotify(text) or _parse_soundcloud(text)


def redact_url(url: str | None) -> str:
    """Drops a private link's secret token, so it never ends up in file tags,
    shared playlists or logs."""
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    segments = parts.path.split("/")
    segments = [segment for segment in segments if not _SECRET_SEGMENT_RE.match(segment)]
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if k != "secret_token"])
    return urlunsplit((parts.scheme, parts.netloc, "/".join(segments), query, parts.fragment))
