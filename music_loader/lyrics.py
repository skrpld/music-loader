"""Synced lyrics (.lrc) lookup for tracks that don't already have one."""
from pathlib import Path

try:
    import syncedlyrics
except ImportError:
    syncedlyrics = None

from .config import LYRICS_PROVIDERS
from .text_utils import build_lyrics_query


def fetch_lyrics(audio_path: Path, artist: str, title: str, dashboard) -> bool:
    """Returns True if lyrics were found and saved next to `audio_path`.

    `artist` and `title` should come from the track's actual metadata (tags
    or platform info), not from the audio filename — a search query without
    the artist is prone to matching an unrelated song that happens to share
    the same title.
    """
    if syncedlyrics is None:
        return False

    query = build_lyrics_query(artist, title)
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
