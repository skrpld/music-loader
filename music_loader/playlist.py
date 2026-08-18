"""Maintains the .m3u8 playlist for downloaded SoundCloud tracks."""
from pathlib import Path

from .config import AUDIO_EXTENSIONS, PLAYLIST_FILENAME


def update_soundcloud_playlist(soundcloud_dir: Path, dashboard) -> int:
    playlist_path = soundcloud_dir / PLAYLIST_FILENAME
    try:
        audio_files = sorted(
            f.name for f in soundcloud_dir.iterdir()
            if f.is_file()
            and not f.name.startswith(".")
            and f.suffix.lower() in AUDIO_EXTENSIONS
        )
    except OSError as exc:
        dashboard.log_error("Playlist", f"Could not read '{soundcloud_dir}': {exc}")
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
