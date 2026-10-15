"""One naming scheme for the whole library, whatever the source.

    <album artist> - <album>/<NN> - <title>.mp3          Spotify, single disc
    <album artist> - <album>/<D>-<NN> - <title>.mp3      Spotify, several discs
    <album artist>/<album>/<NN> - <title>.mp3            SoundCloud (file name template
                                                         configurable, see config.py)

SoundCloud and Spotify files go through the same functions, so the same
characters, padding and truncation apply to every path component. Names are
file names only: tags keep their own text.
"""
from __future__ import annotations

import re
from collections import Counter

from .config import DEFAULT_FILENAME_TEMPLATE, FILENAME_FIELDS
from .text_utils import collapse, normalize_name, normalize_unicode, strip_symbols

VARIOUS_ARTISTS = "Various Artists"
MAX_NAME_BYTES = 180          # per path component, extension included
MIN_TRACK_DIGITS = 2

_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10)),
}
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

# -- Spotify title suffixes ----------------------------------------------------
# Spotify writes versions as "Song - Remastered 2011"; SoundCloud and tags use
# "Song (Remastered 2011)".
_SUFFIX_RE = re.compile(
    r"^(?:.*\b(?:remaster\w*|mix|edit|version|live|mono|stereo|demo|session|acoustic|instrumental|"
    r"bonus track|reprise|radio|extended|remix|sped up|slowed|vip|rework|bootleg|a cappella|acapella)\b.*"
    r"|from\s+[\"“].*|(?:19|20)\d\d\s+(?:remaster\w*|mix|version|edit))$",
    re.IGNORECASE,
)
_DASH_TAIL_RE = re.compile(r"\s+[-–—]\s+(?=[^-–—]+$)")
_FEAT_BRACKET_RE = re.compile(r"([\(\[])\s*(?:feat\.?|ft\.?|featuring)\s+", re.IGNORECASE)
_FEAT_TAIL_RE = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+([^()\[\]]+)$", re.IGNORECASE)


def clean_text(text: str | None) -> str:
    """Unicode and symbol cleanup shared by every name: NFC / compatibility
    forms, no zero-width characters, no emoji or decorations, plain quotes,
    collapsed whitespace."""
    return strip_symbols(normalize_unicode(text))


def feat_style(title: str) -> str:
    """One style for featured artists: "(feat. X)"."""
    title = _FEAT_BRACKET_RE.sub(lambda m: f"{m.group(1)}feat. ", title)
    tail = _FEAT_TAIL_RE.search(title)
    if tail:
        title = f"{title[:tail.start()]} (feat. {tail.group(1).strip()})"
    return title


def spotify_title(title: str | None) -> str:
    """"Song - Remastered 2011" -> "Song (Remastered 2011)"; "feat." style
    unified. Only a last " - part" that reads like a version note moves
    into brackets, so "Song - Part 1 - Intro" style titles stay readable."""
    value = clean_text(title)
    match = _DASH_TAIL_RE.search(value)
    if match and _SUFFIX_RE.match(value[match.end():].strip()):
        value = f"{value[:match.start()]} ({value[match.end():].strip()})"
    return feat_style(value)


def safe_name(text: str | None, fallback: str = "Unknown") -> str:
    """A string valid as a file or folder name on Linux, Windows and
    FAT/exFAT cards, without look-alike characters. Not truncated."""
    value = clean_text(text)
    value = _CONTROL_RE.sub("", value)
    value = value.replace('"', "'")
    value = re.sub(r"(?<=\d):(?=\d)", "-", value)              # "12:30" -> "12-30"
    value = re.sub(r"\s*:\s+|\s*:\s*$", " - ", value)         # "Song: Live" -> "Song - Live"
    value = re.sub(r"\s*[\\/|]\s*", lambda m: " - " if " " in m.group(0) else "-", value)
    value = re.sub(r"[:*?<>]", "", value)
    value = re.sub(r"(?:\s*-\s*){2,}", " - ", value)
    value = collapse(value).strip(" .-")
    if value.split(".")[0].casefold() in _WINDOWS_RESERVED:
        value = f"_{value}"
    return value or fallback


def truncate(text: str, max_bytes: int) -> str:
    """Cuts at a word boundary (never inside a multi-byte character, never
    leaving an unclosed bracket or a dangling separator)."""
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    cut = text.encode("utf-8")[:max_bytes].decode("utf-8", "ignore")
    boundary = max(cut.rfind(" "), cut.rfind("-"))
    if boundary >= len(cut) * 0.5:
        cut = cut[:boundary]
    for opener, closer in ("()", "[]"):
        if cut.count(opener) > cut.count(closer):
            cut = cut[:cut.rfind(opener)]
    return cut.rstrip(" .-,;:&+(") or text.encode("utf-8")[:max_bytes].decode("utf-8", "ignore")


def track_label(number: int, total: int = 1, disc: int = 1, disc_total: int = 1) -> str:
    """"07", "007" for 100+ tracks, "2-07" on a multi-disc album."""
    digits = max(MIN_TRACK_DIGITS, len(str(max(total, number, 1))))
    label = f"{max(number, 1):0{digits}d}"
    return f"{max(disc, 1)}-{label}" if disc_total > 1 else label


class _TrackNumber(int):
    """{track} in a file name template: the usual label ("07", "2-07"),
    or the plain number with a format of its own ("{track:03}")."""

    def __new__(cls, number: int, label: str):
        value = super().__new__(cls, number)
        value.label = label
        return value

    def __format__(self, spec: str) -> str:
        return format(int(self), spec) if spec else self.label


_TITLE_MARK = "\x00title\x00"


def track_filename(title: str | None, number: int, total: int = 1, disc: int = 1,
                   disc_total: int = 1, ext: str = ".mp3", template: str | None = None,
                   fields: dict[str, str] | None = None) -> str:
    """"<NN> - <title>.mp3", or a file name template (config.py checks it)
    with {track}, {title} and the `fields` (artist, album, album_artist).
    Only the title is shortened when the name is too long."""
    label = track_label(number, total, disc, disc_total)
    budget = MAX_NAME_BYTES - len(ext.encode("utf-8"))
    if not template or template == DEFAULT_FILENAME_TEMPLATE:
        prefix = f"{label} - "
        return prefix + truncate(safe_name(title, "Unknown Title"), budget - len(prefix.encode("utf-8"))) + ext
    values = {name: clean_text(value) for name, value in (fields or {}).items()}
    for name in FILENAME_FIELDS:
        values.setdefault(name, "")
    values.update(track=_TrackNumber(max(number, 1), label), title=_TITLE_MARK)
    prefix, _, suffix = template.format(**values).partition(_TITLE_MARK)
    song = safe_name(title, "Unknown Title")
    name = safe_name(prefix + song + suffix, "Unknown Title")
    while len(name.encode("utf-8")) > budget and song:
        excess = len(name.encode("utf-8")) - budget
        shorter = truncate(song, max(len(song.encode("utf-8")) - excess, 0))
        song = shorter if len(shorter) < len(song) else song[:-1]
        name = safe_name(prefix + song + suffix, "Unknown Title")
    return truncate(name, budget) + ext


def album_folder(album_artist: str | None, album: str | None) -> str:
    artist, name = safe_name(album_artist, "Unknown Artist"), safe_name(album, "Unknown Album")
    return truncate(f"{artist} - {name}", MAX_NAME_BYTES)


def album_path(album_artist: str | None, album: str | None) -> str:
    """"<album artist>/<album>" (SoundCloud): one folder per artist, the
    albums and singles inside it. Each component is sanitized and truncated
    on its own, so neither can hold a separator."""
    artist = truncate(safe_name(album_artist, "Unknown Artist"), MAX_NAME_BYTES)
    name = truncate(safe_name(album, "Unknown Album"), MAX_NAME_BYTES)
    return f"{artist}/{name}"


def album_artist_for(album_artist: str | None, track_artists: list[list[str]]) -> str:
    """"Various Artists" for a compilation: an album of several tracks in
    which neither its own album artist nor any other artist is on more than
    40% of them."""
    name = clean_text(album_artist)
    if normalize_name(name) in {"various artists", "various", "va", "разные исполнители"}:
        return VARIOUS_ARTISTS
    if len(track_artists) < 4:
        return name
    counts = Counter(
        normalize_name(artist) for artists in track_artists for artist in set(map(normalize_name, artists)) if artist
    )
    own = counts.get(normalize_name(name), 0)
    if len(counts) >= 4 and max(own, counts.most_common(1)[0][1]) * 5 <= len(track_artists) * 2:
        return VARIOUS_ARTISTS
    return name
