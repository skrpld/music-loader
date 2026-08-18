"""Synced lyrics (.lrc) lookup for tracks that don't already have one."""
from pathlib import Path

try:
    import syncedlyrics
except ImportError:
    syncedlyrics = None

from .config import LYRICS_PROVIDERS
from .text_utils import clean_track_title


def fetch_lyrics(audio_path: Path, dashboard) -> bool:
    """Returns True if lyrics were found and saved next to `audio_path`."""
    if syncedlyrics is None:
        return False

    query = clean_track_title(audio_path.stem)
    if not query:
        dashboard.log(f"[Lyrics] Could not build a search query for '{audio_path.name}'")
        return False

    dashboard.log(f"[Lyrics] Searching: {query}")
    try:
        lrc_content = syncedlyrics.search(query, providers=LYRICS_PROVIDERS)
    except Exception as exc:
        dashboard.log(f"[Lyrics] Error for '{query}': {exc}")
        return False

    if not lrc_content:
        dashboard.log(f"[Lyrics] Not found: {query}")
        return False

    lrc_path = audio_path.with_suffix(".lrc")
    lrc_path.write_text(lrc_content, encoding="utf-8")
    dashboard.log(f"[Lyrics] Saved: {lrc_path.name}")
    return True
