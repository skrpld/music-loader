"""Maintains .m3u8 playlists for downloaded SoundCloud tracks.

Two kinds of playlist are written:
- the rolling playlist containing everything in the SoundCloud folder and its
  album sub-folders (PLAYLIST_FILENAME);
- one playlist per SoundCloud playlist/album link, named after the source
  playlist and keeping its original track order.
"""
import re
from pathlib import Path

from .config import AUDIO_EXTENSIONS, PLAYLIST_FILENAME

_UNSAFE_RE = re.compile(r"[\\/:*?\"<>|\x00-\x1f]")


def safe_playlist_name(name: str) -> str:
    cleaned = _UNSAFE_RE.sub("_", name or "").strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:120] or "SoundCloud playlist"


def _library_files(soundcloud_dir: Path) -> list[str]:
    """Every audio file below the SoundCloud folder (album sub-folders
    included, hidden staging folders excluded), as relative paths."""
    entries: list[str] = []
    for path in soundcloud_dir.rglob("*"):
        try:
            relative = path.relative_to(soundcloud_dir)
        except ValueError:
            continue
        if any(part.startswith(".") for part in relative.parts):
            continue
        if path.suffix.lower() in AUDIO_EXTENSIONS and path.is_file():
            entries.append(relative.as_posix())
    return sorted(entries, key=str.casefold)


def update_soundcloud_playlist(soundcloud_dir: Path, dashboard) -> int:
    playlist_path = soundcloud_dir / PLAYLIST_FILENAME
    try:
        audio_files = _library_files(soundcloud_dir)
    except OSError as exc:
        dashboard.log_error("Playlist", f"Could not list '{soundcloud_dir}': {exc}")
        return 0

    try:
        with open(playlist_path, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for name in audio_files:
                f.write(f"{name}\n")
    except OSError as exc:
        dashboard.log_error("Playlist", f"Could not write '{playlist_path}': {exc}")
        return 0

    dashboard.log(f"[Playlist] Updated: {playlist_path.name} ({len(audio_files)} tracks)")
    return len(audio_files)


def write_named_playlist(
    soundcloud_dir: Path,
    name: str,
    paths: list[Path],
    dashboard,
) -> Path | None:
    """Writes a playlist file for one source link, preserving track order.

    Entries are stored relative to the playlist file so the folder can be
    copied or synced to a phone without breaking the paths.
    """
    entries: list[str] = []
    seen: set[str] = set()
    for path in paths:
        if path is None:
            continue
        try:
            if not path.exists():
                continue
            entry = path.relative_to(soundcloud_dir).as_posix()
        except (OSError, ValueError):
            continue
        if entry in seen:
            continue
        seen.add(entry)
        entries.append(entry)

    if not entries:
        return None

    playlist_path = soundcloud_dir / f"{safe_playlist_name(name)}.m3u8"
    try:
        with open(playlist_path, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for entry in entries:
                f.write(f"{entry}\n")
    except OSError as exc:
        dashboard.log_error("Playlist", f"Could not write '{playlist_path}': {exc}")
        return None

    dashboard.log(f"[Playlist] Saved: {playlist_path.name} ({len(entries)} tracks)")
    return playlist_path
