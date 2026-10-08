"""Paths and constants shared across the project."""
from dataclasses import dataclass
from pathlib import Path

AUDIO_EXTENSIONS = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".webm"}
# Source formats yt-dlp may hand over before conversion to MP3. A SoundCloud
# "original download" can be whatever the artist uploaded, AIFF included.
RAW_EXTENSIONS = AUDIO_EXTENSIONS | {
    ".wav", ".aac", ".mp4", ".m4b", ".aiff", ".aif", ".aifc", ".alac", ".mp2", ".wma", ".caf",
}

SOUNDCLOUD_SUBDIR = "SoundCloud"
ARCHIVE_FILENAME = ".sc_archive.txt"
PLAYLIST_FILENAME = "SoundCloud_New.m3u8"
INDEX_FILENAME = ".sc_index.json"
STAGING_DIRNAME = ".sc_downloads"

# Canonical spelling of artist names ("WHITENER" vs "whitener"), shared by the
# Spotify and SoundCloud parts of the library so one artist is not split into
# several entries in the player.
ARTISTS_FILENAME = ".music-loader-artists.json"

# Spotify URL -> local file, used to find files written under an older folder
# layout instead of downloading them again.
SPOTIFY_INDEX_FILENAME = ".spotify_index.json"

# Tagging every SoundCloud upload with a shared "SoundCloud" album collapsed
# the whole library into one fake album. A standalone upload gets its own
# album named after the song, following the usual "<song> - Single" convention.
SINGLE_ALBUM_SUFFIX = " - Single"

# Remembers when a lyrics search last found nothing for a track, so a track
# whose lyrics simply aren't available anywhere isn't re-searched on every
# single run.
LYRICS_ATTEMPTS_FILENAME = ".sc_lyrics_attempts.json"
SPOTIFY_LYRICS_ATTEMPTS_FILENAME = ".spotify_lyrics_attempts.json"

# How long to wait before retrying a previously-failed lyrics search for the
# same track.
LYRICS_RETRY_COOLDOWN_SECONDS = 7 * 24 * 60 * 60

# Strict lyrics matching: a synced text is only accepted when the provider's
# track length is within this many seconds of the local file, otherwise the
# timestamps belong to a different cut (sped up, extended, radio edit...).
LYRICS_STRICT_DURATION_TOLERANCE = 2.0
# Loose mode accepts a wider gap, but saves only plain text once the gap is
# larger than the strict tolerance, so misaligned timestamps never reach the
# player.
LYRICS_LOOSE_DURATION_TOLERANCE = 10.0

# The dedup index is rewritten in full on every save. With hundreds of tracks
# per run that becomes the dominant cost, so writes are coalesced: at most one
# write per this many seconds, plus a final flush at the end of a link.
INDEX_SAVE_INTERVAL_SECONDS = 5.0

# Leftovers of an interrupted run older than this are deleted at the start of
# the next run.
STALE_STAGING_SECONDS = 24 * 60 * 60

# A downloaded track whose length differs from the length SoundCloud reports
# by more than this is treated as broken (a 30-second Go+ preview, a cut-off
# download) instead of being filed into the library.
DURATION_TOLERANCE_SECONDS = 3.0
DURATION_TOLERANCE_RATIO = 0.03

# The fallback to another source takes a track only when its length is this
# close to SoundCloud's (a different edit or master is off by more).
FALLBACK_DURATION_TOLERANCE_SECONDS = 3.0
# How many search results of the fallback are looked at, best first.
FALLBACK_MAX_CANDIDATES = 5

# Embedded covers are downscaled to at most this size; SoundCloud "original"
# artwork can be several thousand pixels and megabytes per file.
COVER_MAX_SIZE = 1200
# Hard cap for a downloaded cover, protects against a broken/hostile URL.
COVER_MAX_BYTES = 20 * 1024 * 1024

# Where per-run failure logs are written (see runlog.py). Kept as a hidden
# subfolder of the music library so it doesn't clutter the main view but is
# still easy to find (`ls -a`).
LOGS_DIRNAME = ".music-loader-logs"

# Environment variables checked when no Spotify credentials are passed on the
# command line. The environment is the safer channel: command-line arguments
# are visible to every user of the machine through the process list.
SPOTIFY_CLIENT_ID_ENV = "SPOTIFY_CLIENT_ID"
SPOTIFY_CLIENT_SECRET_ENV = "SPOTIFY_CLIENT_SECRET"

# Long-running external commands (spotdl/yt-dlp) are killed if they produce
# no output *and* don't exit within this many seconds. Set generously high
# because a single link can be an entire artist discography (hundreds of
# tracks), which spotdl can take a long time to resolve before printing
# anything.
SUBPROCESS_TIMEOUT_SECONDS = 6 * 60 * 60

LYRICS_MODE_STRICT = "strict"
LYRICS_MODE_LOOSE = "loose"


@dataclass
class AppConfig:
    music_dir: Path
    soundcloud_dir: Path
    soundcloud_postprocess_workers: int = 4
    soundcloud_download_workers: int = 2
    lyrics_workers: int = 2
    spotify_threads: int = 4
    spotify_client_id: str | None = None
    spotify_client_secret: str | None = None
    lyrics_enabled: bool = True
    lyrics_mode: str = LYRICS_MODE_STRICT
    recheck: bool = False
    soundcloud_reposts: bool = False
    soundcloud_likes: bool = False
    # Opt-in: a track SoundCloud does not give out (DRM, preview, blocked) is
    # looked up on YouTube Music and taken from there when artist, title,
    # version and length all match.
    soundcloud_fallback: bool = False

    @classmethod
    def from_output_dir(cls, output_dir: Path) -> "AppConfig":
        music_dir = output_dir.expanduser().resolve()
        soundcloud_dir = music_dir / SOUNDCLOUD_SUBDIR
        return cls(music_dir=music_dir, soundcloud_dir=soundcloud_dir)

    def ensure_dirs(self) -> None:
        self.music_dir.mkdir(parents=True, exist_ok=True)
        self.soundcloud_dir.mkdir(parents=True, exist_ok=True)
