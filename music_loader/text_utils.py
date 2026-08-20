"""Text helpers for turning noisy file titles into clean lyric-search queries."""
import re

_JUNK_WORDS = (
    r"official|audio|video|lyric|lyrics|prod\.?|remix|out now|"
    r"free download|premiere|hq|hd|visualizer|explicit|clean|"
    r"monstercat|nocopyrightsounds|ncs release"
)
_PATTERNS = [
    rf"[\(\[\{{][^\(\)\[\]\{{\}}]*(?:{_JUNK_WORDS})[^\(\)\[\]\{{\}}]*[\)\]\}}]",
    r"[\(\[\{]\s*[\)\]\}]",  # empty brackets left after cleaning
    r"\s[-|]\s*$",           # trailing dash/pipe
]

_UNKNOWN_ARTIST_NAMES = {"", "unknown artist", "unknown", "various artists"}


def clean_track_title(title: str) -> str:
    cleaned = title
    for pattern in _PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\.(mp3|flac|m4a|wav|opus|webm)$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" -|")


def build_lyrics_query(artist: str, title: str) -> str:
    """Builds a lyrics-provider search query from known track metadata.

    Searching by title alone is unreliable: a common/short title (e.g. a
    single word) matches unrelated songs from other artists, and the title
    as it appears on the source platform can differ from how the track is
    credited on lyrics sites. Including the artist narrows the search to
    the right song.
    """
    clean_title = clean_track_title(title or "")
    if not clean_title:
        return ""
    clean_artist = re.sub(r"\s+", " ", (artist or "")).strip()
    if clean_artist and clean_artist.casefold() not in _UNKNOWN_ARTIST_NAMES:
        return f"{clean_artist} - {clean_title}"
    return clean_title
