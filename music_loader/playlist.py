"""Maintains the .m3u8 playlist for downloaded SoundCloud tracks."""
from pathlib import Path

from .config import AUDIO_EXTENSIONS, PLAYLIST_FILENAME


def update_soundcloud_playlist(soundcloud_dir: Path, dashboard) -> int:
    playlist_path = soundcloud_dir / PLAYLIST_FILENAME
    audio_files = sorted(
        f.name for f in soundcloud_dir.iterdir()
        if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS
    )

    with open(playlist_path, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for name in audio_files:
            f.write(f"{name}\n")

    dashboard.log(f"Playlist updated: {playlist_path.name} ({len(audio_files)} tracks)")
    return len(audio_files)
