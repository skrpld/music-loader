"""Paths and constants shared across the project."""
from dataclasses import dataclass
from pathlib import Path

AUDIO_EXTENSIONS = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".webm"}
SOUNDCLOUD_SUBDIR = "SoundCloud"
ARCHIVE_FILENAME = ".sc_archive.txt"
PLAYLIST_FILENAME = "SoundCloud_New.m3u8"
LYRICS_PROVIDERS = ["Musixmatch", "NetEase", "Lrclib", "Genius"]

# Where per-run failure logs are written (see runlog.py). Kept as a hidden
# subfolder of the music library so it doesn't clutter the main view but is
# still easy to find (`ls -a`).
LOGS_DIRNAME = ".music-loader-logs"

# Long-running external commands (spotdl/yt-dlp) are killed if they produce
# no output *and* don't exit within this many seconds. Set generously high
# because a single link can be an entire artist discography (hundreds of
# tracks), which spotdl can take a long time to resolve before printing
# anything.
SUBPROCESS_TIMEOUT_SECONDS = 6 * 60 * 60  # 6 hours


@dataclass
class AppConfig:
    music_dir: Path
    soundcloud_dir: Path

    @classmethod
    def from_output_dir(cls, output_dir: Path) -> "AppConfig":
        music_dir = output_dir.expanduser().resolve()
        soundcloud_dir = music_dir / SOUNDCLOUD_SUBDIR
        return cls(music_dir=music_dir, soundcloud_dir=soundcloud_dir)

    def ensure_dirs(self) -> None:
        self.music_dir.mkdir(parents=True, exist_ok=True)
        self.soundcloud_dir.mkdir(parents=True, exist_ok=True)
