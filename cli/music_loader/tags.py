"""ID3 tag helpers (mutagen), shared by the SoundCloud tagging, the lyrics
writer and the library checks.

Tags are written as ID3v2.4. Several artists are stored twice, the way
MusicBrainz Picard does it: `TPE1` holds one display string ("A, B") that
every player shows cleanly, and `TXXX:ARTISTS` holds the real list for the
players that split artists. ID3v2.3 (what spotDL writes) can only glue
artists together with "/", which turns "AC/DC" into two artists; such files
are read with a guard for the names the artist registry knows and are
rewritten by `set_artists` / `--existing`.

Every file is bound to its track in hidden user-defined frames, which
players do not show: `TXXX:SOUNDCLOUD_ID`, `TXXX:SPOTIFY_ID` (next to the
`WOAS` URL) and `TXXX:MUSIC_LOADER_FORMAT`, the version of the rules the
file was written with (config.FORMAT_VERSION; missing = 0). `bind_ids`
writes only the id frames into a file recognised without them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import FORMAT_VERSION

try:
    from mutagen import File as mutagen_file
    from mutagen.id3 import (
        APIC, COMM, ID3, ID3NoHeaderError, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TPOS,
        TRCK, TXXX, USLT, WOAS,
    )
    MUTAGEN_AVAILABLE = True
except ImportError:  # pragma: no cover - dependency is declared in pyproject
    MUTAGEN_AVAILABLE = False

SOUNDCLOUD_ID_DESC = "SOUNDCLOUD_ID"
SPOTIFY_ID_DESC = "SPOTIFY_ID"
FORMAT_DESC = "MUSIC_LOADER_FORMAT"
# Set on a file that was not taken from SoundCloud itself (the fallback to
# YouTube Music): where it came from, and the page it was taken from.
SOURCE_DESC = "MUSIC_LOADER_SOURCE"
SOURCE_URL_DESC = "MUSIC_LOADER_SOURCE_URL"
ARTISTS_DESC = "ARTISTS"
ARTIST_SEPARATOR = ", "
ID3_VERSION = 4
_LYRICS_FRAMES = ("USLT", "SYLT")


@dataclass
class TrackTags:
    title: str
    artists: list[str]
    album: str
    album_artist: str
    track_number: int = 1
    track_total: int = 1
    disc_number: int = 1
    disc_total: int = 1
    date: str = ""            # "YYYY" or "YYYY-MM-DD"
    genre: str = ""
    url: str = ""             # public page of the track
    soundcloud_id: str = ""
    extra: dict[str, str] = field(default_factory=dict)


def clean_artists(artists) -> list[str]:
    """Non-empty names without repeats (case-insensitive), order kept."""
    result: list[str] = []
    seen: set[str] = set()
    for name in artists or []:
        name = str(name).strip()
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            result.append(name)
    return result


def format_artists(artists) -> str:
    """The display string of the artist frame: "A, B"."""
    return ARTIST_SEPARATOR.join(clean_artists(artists))


def split_artists(value: str, separator: str, is_known: Callable[[str], bool] | None = None) -> list[str]:
    """Splits `value` on `separator`, but never inside a name that
    `is_known` recognises ("AC/DC" stays one artist)."""
    parts = [part.strip() for part in value.split(separator)]
    parts = [part for part in parts if part]
    if len(parts) < 2 or is_known is None:
        return parts
    result: list[str] = []
    index = 0
    while index < len(parts):
        for end in range(len(parts), index, -1):
            joined = separator.join(parts[index:end])
            if end - index == 1 or is_known(joined):
                result.append(joined)
                index = end
                break
    return result


def _artist_frames(id3, artists: list[str]) -> None:
    id3.delall("TPE1")
    id3.delall(f"TXXX:{ARTISTS_DESC}")
    id3.add(TPE1(encoding=3, text=[format_artists(artists) or "Unknown Artist"]))
    if artists:
        id3.add(TXXX(encoding=3, desc=ARTISTS_DESC, text=list(artists)))


def set_artists(path: Path, artists) -> bool:
    """Rewrites only the artist frames (and saves as ID3v2.4), keeping
    everything else, cover and lyrics included. False when unreadable or
    when there is nothing to write."""
    names = clean_artists(artists)
    if not MUTAGEN_AVAILABLE or not names:
        return False
    try:
        id3 = ID3(str(path))
        _artist_frames(id3, names)
        id3.save(str(path), v2_version=ID3_VERSION)
    except Exception:
        return False
    return True


def set_album_artist(path: Path, name: str) -> bool:
    """Rewrites only the album artist frame (TPE2)."""
    if not MUTAGEN_AVAILABLE or not name:
        return False
    try:
        id3 = ID3(str(path))
        id3.delall("TPE2")
        id3.add(TPE2(encoding=3, text=[name]))
        id3.save(str(path), v2_version=ID3_VERSION)
    except Exception:
        return False
    return True


def artists_current(path: Path, artists) -> bool:
    """True when the file already has exactly this artist list in the
    ID3v2.4 layout (so a rewrite is not needed)."""
    names = clean_artists(artists)
    if not MUTAGEN_AVAILABLE or not names:
        return True
    try:
        id3 = ID3(str(path))
    except Exception:
        return False
    if id3.version[:2] != (2, ID3_VERSION) or "TPE1" not in id3:
        return False
    frames = id3.getall(f"TXXX:{ARTISTS_DESC}")
    listed = [str(item) for item in frames[0].text] if frames else []
    return listed == names and [str(item) for item in id3["TPE1"].text] == [format_artists(names)]


def audio_duration(path: Path) -> float | None:
    if not MUTAGEN_AVAILABLE:
        return None
    try:
        audio = mutagen_file(str(path))
        length = getattr(getattr(audio, "info", None), "length", None)
        return float(length) if length else None
    except Exception:
        return None


def _load_id3(path: Path):
    try:
        return ID3(str(path))
    except ID3NoHeaderError:
        return ID3()


def write_tags(path: Path, tags: TrackTags, cover: bytes | None = None) -> None:
    """(Re)writes every tag music-loader owns.

    Lyrics frames are kept (the lyrics step manages them), and so is an
    existing cover when no new one is supplied.
    """
    id3 = _load_id3(path)
    keep = {"USLT", "SYLT"} | (set() if cover else {"APIC"})
    for key in list(id3.keys()):
        if key.split(":", 1)[0] not in keep:
            del id3[key]

    id3.add(TIT2(encoding=3, text=tags.title))
    _artist_frames(id3, clean_artists(tags.artists))
    id3.add(TPE2(encoding=3, text=tags.album_artist or (tags.artists[0] if tags.artists else "")))
    id3.add(TALB(encoding=3, text=tags.album))
    total = max(tags.track_total, tags.track_number, 1)
    id3.add(TRCK(encoding=3, text=f"{max(tags.track_number, 1)}/{total}"))
    id3.add(TPOS(encoding=3, text=f"{max(tags.disc_number, 1)}/{max(tags.disc_total, tags.disc_number, 1)}"))
    if tags.date:
        id3.add(TDRC(encoding=3, text=tags.date))
    if tags.genre:
        id3.add(TCON(encoding=3, text=tags.genre))
    if tags.url:
        id3.add(WOAS(url=tags.url))
        id3.add(COMM(encoding=3, lang="eng", desc="", text=tags.url))
    if tags.soundcloud_id:
        id3.add(TXXX(encoding=3, desc=SOUNDCLOUD_ID_DESC, text=tags.soundcloud_id))
    id3.add(TXXX(encoding=3, desc=FORMAT_DESC, text=str(FORMAT_VERSION)))
    for desc, value in tags.extra.items():
        if value:
            id3.add(TXXX(encoding=3, desc=desc, text=value))
    if cover:
        id3.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover))
    id3.save(str(path), v2_version=ID3_VERSION)


def read_tags(path: Path, is_known: Callable[[str], bool] | None = None) -> dict:
    """The few fields the library checks need; empty values (and `readable`
    False) when the file has no readable ID3 tag.

    `is_known` (for example `ArtistRegistry.is_known`) keeps names that
    contain the separator, like "AC/DC", in one piece."""
    result = {
        "title": "", "artists": [], "album": "", "album_artist": "",
        "url": "", "soundcloud_id": "", "spotify_id": "", "format": 0, "has_lyrics": False,
        "has_cover": False, "readable": False,
    }
    if not MUTAGEN_AVAILABLE:
        return result
    try:
        id3 = ID3(str(path))
    except Exception:
        return result

    def text(key: str) -> list[str]:
        frame = id3.get(key)
        return [str(item) for item in frame.text] if frame is not None and hasattr(frame, "text") else []

    result["title"] = (text("TIT2") or [""])[0]
    result["artists"] = _read_artists(id3, text, is_known)
    result["album"] = (text("TALB") or [""])[0]
    result["album_artist"] = (text("TPE2") or [""])[0]
    woas = id3.getall("WOAS")
    result["url"] = str(woas[0].url) if woas else ""
    for frame in id3.getall("TXXX"):
        if not frame.text:
            continue
        if frame.desc == SOUNDCLOUD_ID_DESC:
            result["soundcloud_id"] = str(frame.text[0])
        elif frame.desc == SPOTIFY_ID_DESC:
            result["spotify_id"] = str(frame.text[0])
        elif frame.desc == FORMAT_DESC:
            result["format"] = _format_number(frame.text[0])
    result["has_lyrics"] = any(
        str(getattr(frame, "text", "")).strip() for frame in id3.getall("USLT")
    ) or bool(id3.getall("SYLT"))
    result["has_cover"] = any(getattr(frame, "data", b"") for frame in id3.getall("APIC"))
    result["readable"] = True
    return result


def _format_number(value) -> int:
    try:
        return max(0, int(str(value).strip()))
    except ValueError:
        return 0


def format_version(path: Path) -> int:
    """The MUSIC_LOADER_FORMAT of a file; 0 when missing or unreadable."""
    return _format_number(read_txxx(path, FORMAT_DESC) or 0)


def spotify_track_id(url: str) -> str:
    """"https://open.spotify.com/track/<id>" -> "<id>"; empty for anything else."""
    prefix = "https://open.spotify.com/track/"
    if not url.startswith(prefix):
        return ""
    track_id = url[len(prefix):].split("?", 1)[0].strip("/")
    return track_id if track_id.isalnum() else ""


def bind_ids(path: Path, soundcloud_id: str = "", spotify_url: str = "",
             format_version: int | None = None) -> bool:
    """Writes only the id frames (and, when given, the format version) into
    a file. Every other frame, the ID3 version, the name and the folder stay
    as they are. False when unreadable or when there is nothing to write."""
    values: dict[str, str] = {}
    if soundcloud_id:
        values[SOUNDCLOUD_ID_DESC] = soundcloud_id
    spotify_id = spotify_track_id(spotify_url)
    if spotify_id:
        values[SPOTIFY_ID_DESC] = spotify_id
    if format_version is not None:
        values[FORMAT_DESC] = str(format_version)
    if not MUTAGEN_AVAILABLE or not values:
        return False
    try:
        id3 = ID3(str(path), translate=False)
    except Exception:
        return False
    version = 3 if id3.version[:2] == (2, 3) else ID3_VERSION
    for desc, value in values.items():
        id3.delall(f"TXXX:{desc}")
        id3.add(TXXX(encoding=1 if version == 3 else 3, desc=desc, text=value))
    if spotify_url and not any(str(frame.url) == spotify_url for frame in id3.getall("WOAS")):
        id3.delall("WOAS")
        id3.add(WOAS(url=spotify_url))
    try:
        id3.save(str(path), v2_version=version)
    except Exception:
        return False
    return True


def _read_artists(id3, text, is_known) -> list[str]:
    listed = clean_artists(
        str(item) for frame in id3.getall(f"TXXX:{ARTISTS_DESC}") for item in frame.text
    )
    if listed:
        return listed
    # ID3v2.4 stores several values in one frame; v2.3 glues them with "/".
    separator = ARTIST_SEPARATOR if id3.version >= (2, 4, 0) else "/"
    artists: list[str] = []
    for value in text("TPE1"):
        artists.extend(split_artists(value, separator, is_known))
    return clean_artists(artists)


def read_txxx(path: Path, desc: str) -> str:
    """Value of one user-defined text frame, empty when missing."""
    if not MUTAGEN_AVAILABLE:
        return ""
    try:
        frames = ID3(str(path)).getall(f"TXXX:{desc}")
    except Exception:
        return ""
    return str(frames[0].text[0]) if frames and frames[0].text else ""


def read_woas(path: Path) -> str:
    """Source URL frame only (cheap: no audio parsing)."""
    if not MUTAGEN_AVAILABLE:
        return ""
    try:
        frames = ID3(str(path)).getall("WOAS")
    except Exception:
        return ""
    return str(frames[0].url) if frames else ""


def has_embedded_lyrics(path: Path) -> bool:
    return bool(read_tags(path)["has_lyrics"])


def embed_lyrics(path: Path, text: str) -> None:
    """Stores plain lyrics in the file (USLT), replacing older ones."""
    id3 = _load_id3(path)
    for frame in _LYRICS_FRAMES:
        id3.delall(frame)
    id3.add(USLT(encoding=3, lang="XXX", desc="", text=text))
    id3.save(str(path), v2_version=ID3_VERSION)


def remove_lyrics(path: Path) -> bool:
    """Drops embedded lyrics; True when something was removed."""
    if not MUTAGEN_AVAILABLE:
        return False
    try:
        id3 = ID3(str(path))
    except Exception:
        return False
    if not any(id3.getall(frame) for frame in _LYRICS_FRAMES):
        return False
    for frame in _LYRICS_FRAMES:
        id3.delall(frame)
    id3.save(str(path), v2_version=ID3_VERSION)
    return True
