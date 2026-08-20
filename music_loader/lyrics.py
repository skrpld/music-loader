"""Synced lyrics (.lrc) lookup for tracks that don't already have one."""
from pathlib import Path

try:
    import syncedlyrics
except ImportError:
    syncedlyrics = None

from .config import LYRICS_MAX_QUERY_VARIANTS, LYRICS_PROVIDERS
from .text_utils import build_lyrics_queries


def fetch_lyrics(audio_path: Path, artist: str, title: str, dashboard) -> bool:
    """Returns True if lyrics were found and saved next to `audio_path`.

    `artist` and `title` should come from the track's actual metadata (tags
    or platform info), not from the audio filename.

    SoundCloud naming is inconsistent - the uploader is often a label rather
    than the artist, and the artist is part of the title instead. So the title
    is split into parts and several queries are tried in order, from the most
    specific (artist + song) to the loosest (song name alone), stopping at the
    first provider hit.
    """
    if syncedlyrics is None:
        return False

    queries = build_lyrics_queries(artist, title)[:LYRICS_MAX_QUERY_VARIANTS]
    if not queries:
        dashboard.log(f"[Lyrics] Could not build a search query for '{audio_path.name}'")
        return False

    for position, query in enumerate(queries, start=1):
        dashboard.log(f"[Lyrics] Searching ({position}/{len(queries)}): {query}")
        try:
            lrc_content = syncedlyrics.search(query, providers=LYRICS_PROVIDERS)
        except Exception as exc:
            dashboard.log_error("Lyrics", f"Search failed for '{query}': {exc}")
            continue

        if not lrc_content:
            continue

        lrc_path = audio_path.with_suffix(".lrc")
        try:
            lrc_path.write_text(lrc_content, encoding="utf-8")
        except OSError as exc:
            dashboard.log_error("Lyrics", f"Could not save '{lrc_path.name}': {exc}")
            return False
        dashboard.log(f"[Lyrics] Saved: {lrc_path.name}")
        return True

    dashboard.log(f"[Lyrics] Not found after {len(queries)} attempt(s): {queries[0]}")
    return False
