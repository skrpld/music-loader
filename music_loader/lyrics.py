"""Synced lyrics (.lrc) lookup for tracks that don't already have one."""
from pathlib import Path

try:
    import syncedlyrics
except ImportError:
    syncedlyrics = None

from .config import LYRICS_PROVIDERS
from .text_utils import clean_track_title


def fetch_lyrics(
    audio_path: Path,
    dashboard,
    artist: str | None = None,
    title: str | None = None,
) -> bool:
    """Returns True if lyrics were found and saved next to `audio_path`.

    When `artist`/`title` are supplied (the track's real metadata) the search
    query is built from them instead of the filename. SoundCloud upload
    titles frequently don't match how a song is credited on lyrics providers
    (missing artist, reworded title, etc.), which used to cause both missed
    matches and wrong matches against an unrelated song with the same
    filename. Falls back to the filename only if no metadata is available.
    """
    if syncedlyrics is None:
        return False

    if title:
        raw_query = f"{artist} - {title}" if artist else title
    else:
        raw_query = audio_path.stem

    query = clean_track_title(raw_query)
    if not query:
        dashboard.log(f"[Lyrics] Could not build a search query for '{audio_path.name}'")
        return False

    dashboard.log(f"[Lyrics] Searching: {query}")
    try:
        lrc_content = syncedlyrics.search(query, providers=LYRICS_PROVIDERS)
    except Exception as exc:
        dashboard.log_error("Lyrics", f"Search failed for '{query}': {exc}")
        return False

    if not lrc_content:
        dashboard.log(f"[Lyrics] Not found: {query}")
        return False

    lrc_path = audio_path.with_suffix(".lrc")
    try:
        lrc_path.write_text(lrc_content, encoding="utf-8")
    except OSError as exc:
        dashboard.log_error("Lyrics", f"Could not save '{lrc_path.name}': {exc}")
        return False
    dashboard.log(f"[Lyrics] Saved: {lrc_path.name}")
    return True
