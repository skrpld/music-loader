"""Text helpers for turning noisy file titles into clean lyric-search queries."""
import re

_JUNK_WORDS = (
    r"official|audio|video|lyric|lyrics|prod\.?|remix|out now|"
    r"free download|free dl|premiere|hq|hd|visualizer|explicit|clean|"
    r"full version|extended|radio edit|master|remaster(?:ed)?|"
    r"monstercat|nocopyrightsounds|ncs release"
)
_PATTERNS = [
    rf"[\(\[\{{][^\(\)\[\]\{{\}}]*(?:{_JUNK_WORDS})[^\(\)\[\]\{{\}}]*[\)\]\}}]",
    r"[\(\[\{]\s*[\)\]\}]",  # empty brackets left after cleaning
    r"\s[-|]\s*$",           # trailing dash/pipe
]

_UNKNOWN_ARTIST_NAMES = {"", "unknown artist", "unknown", "various artists"}

# "Artist - Song", "Artist – Song", "Artist | Song", "Artist: Song".
_SEPARATOR_RE = re.compile(r"\s+[-–—|]\s+|\s*:\s+")

# "feat. X", "ft X", "featuring X", "w/ X" - bracketed or not.
_FEAT_RE = re.compile(
    r"\s*[\(\[]?\s*(?:feat\.?|ft\.?|featuring|w/)\s+[^\)\]]*[\)\]]?\s*",
    re.IGNORECASE,
)
# "prod. by X", "prod X".
_PROD_RE = re.compile(
    r"\s*[\(\[]?\s*prod\.?\s*(?:by)?\s+[^\)\]]*[\)\]]?\s*",
    re.IGNORECASE,
)


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip(" -–—|:")


def clean_track_title(title: str) -> str:
    cleaned = title
    for pattern in _PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\.(mp3|flac|m4a|wav|opus|webm)$", "", cleaned, flags=re.IGNORECASE)
    return _collapse(cleaned)


def split_artist_title(title: str) -> tuple[str, str]:
    """Splits "Artist - Song" into its two parts.

    SoundCloud titles usually carry the real artist name themselves, while the
    uploader is a label, a repost channel or a personal account. Returns an
    empty artist when the title has no separator.
    """
    parts = _SEPARATOR_RE.split(_collapse(title), maxsplit=1)
    if len(parts) == 2:
        left, right = _collapse(parts[0]), _collapse(parts[1])
        if left and right:
            return left, right
    return "", _collapse(title)


def build_lyrics_queries(artist: str, title: str) -> list[str]:
    """Builds an ordered list of search queries, most specific first.

    A single query is fragile: if the uploader isn't the artist, or the title
    carries a "feat."/"prod." tail, the lyrics provider finds nothing even
    though the song is there. Splitting the title into parts and combining
    them in several ways gives the provider more than one chance to match,
    while keeping the artist in the query for as long as possible so a short
    or common song name doesn't match an unrelated track.
    """
    cleaned = clean_track_title(title or "")
    if not cleaned:
        return []

    without_prod = _collapse(_PROD_RE.sub(" ", cleaned))
    base = without_prod or cleaned

    title_artist, song = split_artist_title(base)
    song_core = _collapse(_FEAT_RE.sub(" ", song)) or song

    uploader = _collapse(artist or "")
    if uploader.casefold() in _UNKNOWN_ARTIST_NAMES:
        uploader = ""
    # An uploader that just repeats the artist part of the title adds nothing.
    if uploader and title_artist and uploader.casefold() == title_artist.casefold():
        uploader = ""

    candidates: list[str] = []
    if title_artist:
        candidates.append(f"{title_artist} - {song}")
        candidates.append(f"{title_artist} - {song_core}")
    if uploader:
        candidates.append(f"{uploader} - {song_core}")
    candidates.append(song_core)
    candidates.append(song)
    if title_artist:
        # Some uploads invert the order ("Song - Artist").
        candidates.append(f"{song_core} - {title_artist}")

    queries: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        value = _collapse(candidate)
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            queries.append(value)
    return queries


def build_lyrics_query(artist: str, title: str) -> str:
    """Single best-guess query (kept for callers that want just one)."""
    queries = build_lyrics_queries(artist, title)
    return queries[0] if queries else ""
