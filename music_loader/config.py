"""Paths and constants shared across the project."""
from dataclasses import dataclass
from pathlib import Path

AUDIO_EXTENSIONS = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".webm"}
# Source formats yt-dlp may hand over before conversion to MP3.
RAW_EXTENSIONS = AUDIO_EXTENSIONS | {".wav", ".aac", ".mp4", ".m4b"}

SOUNDCLOUD_SUBDIR = "SoundCloud"
ARCHIVE_FILENAME = ".sc_archive.txt"
PLAYLIST_FILENAME = "SoundCloud_New.m3u8"
INDEX_FILENAME = ".sc_index.json"
STAGING_DIRNAME = ".sc_downloads"
LYRICS_PROVIDERS = ["Musixmatch", "NetEase", "Lrclib", "Genius"]

# Remembers when a lyrics search last found nothing for a track, so a track
# whose lyrics simply aren't available anywhere isn't re-searched on every
# single run.
LYRICS_ATTEMPTS_FILENAME = ".sc_lyrics_attempts.json"

# How long to wait before retrying a previously-failed lyrics search for the
# same track.
LYRICS_RETRY_COOLDOWN_SECONDS = 7 * 24 * 60 * 60

# Leftovers of an interrupted run older than this are deleted at the start of
# the next run.
STALE_STAGING_SECONDS = 24 * 60 * 60

# Where per-run failure logs are written (see runlog.py). Kept as a hidden
# subfolder of the music library so it doesn't clutter the main view but is
# still easy to find (`ls -a`).
LOGS_DIRNAME = ".music-loader-logs"

# Long-running external commands (spotdl/yt-dlp) are killed if they produce
# no output *and* don't exit within this many seconds. Set generously high
# because a single link can be an entire artist discography (hundreds of
# tracks), which spotdl can take a long time to resolve before printing
# anything.
SUBPROCESS_TIMEOUT_SECONDS = 6 * 60 * 60


@dataclass
class AppConfig:
    music_dir: Path
    soundcloud_dir: Path
    soundcloud_postprocess_workers: int = 4
    lyrics_workers: int = 2

    @classmethod
    def from_output_dir(cls, output_dir: Path) -> "AppConfig":
        music_dir = output_dir.expanduser().resolve()
        soundcloud_dir = music_dir / SOUNDCLOUD_SUBDIR
        return cls(music_dir=music_dir, soundcloud_dir=soundcloud_dir)

    def ensure_dirs(self) -> None:
        self.music_dir.mkdir(parents=True, exist_ok=True)
        self.soundcloud_dir.mkdir(parents=True, exist_ok=True)
