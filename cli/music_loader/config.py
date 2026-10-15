"""Paths and constants shared across the project."""
import re
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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


# -- SoundCloud formatting settings ---------------------------------------------------
# Where featured artists go: "(feat. X)" in the title and X in the artist tag
# (both), only in the artist tag, or only in the title.
FEAT_BOTH = "both"
FEAT_ARTIST = "artist"
FEAT_TITLE = "title"
FEAT_MODES = (FEAT_BOTH, FEAT_ARTIST, FEAT_TITLE)

DEFAULT_FILENAME_TEMPLATE = "{track} - {title}"
FILENAME_FIELDS = ("track", "title", "artist", "album", "album_artist")
_MAX_TEMPLATE_LENGTH = 200


def validate_filename_template(template: Any) -> str:
    """Checks a file name template ("{track} - {title}"): known fields
    only, exactly one {title}, a number format only for {track} ("{track:02}"),
    no folder separators. Raises ValueError with a readable reason."""
    if not isinstance(template, str) or not template.strip():
        raise ValueError("the file name template is empty")
    if len(template) > _MAX_TEMPLATE_LENGTH:
        raise ValueError(f"the file name template is longer than {_MAX_TEMPLATE_LENGTH} characters")
    if any(char in template for char in "/\\") or any(ord(char) < 32 for char in template):
        raise ValueError("the file name template must not contain '/', '\\' or control characters")
    try:
        parts = list(string.Formatter().parse(template))
    except ValueError as exc:
        raise ValueError(f"the file name template is not valid: {exc}") from None
    titles = 0
    for _literal, name, spec, conversion in parts:
        if name is None:
            continue
        if name not in FILENAME_FIELDS:
            raise ValueError(f"unknown field {{{name}}} in the file name template; "
                             f"use {', '.join('{' + item + '}' for item in FILENAME_FIELDS)}")
        if conversion or (spec and (name != "track" or not re.fullmatch(r"0?[1-9]", spec))):
            raise ValueError(f"{{{name}}} takes no format here; only {{track:02}} style padding is allowed")
        titles += name == "title"
    if titles != 1:
        raise ValueError("the file name template must contain {title} exactly once")
    return template


@dataclass(frozen=True)
class SoundCloudFormat:
    """How SoundCloud tracks are written. The defaults are the behaviour of
    versions before these settings existed. `--recheck` applies a change to
    tracks already in the library."""
    feat: str = FEAT_BOTH
    keep_producers: bool = False      # "(prod. X)" in the title
    keep_versions: bool = True        # "(Sped Up)", "(Remix)", "(Live)"... in the title
    strip_promo: bool = True          # "[Free DL]", "(Official Audio)", hashtags, emoji
    filename_template: str = DEFAULT_FILENAME_TEMPLATE

    @property
    def feat_in_title(self) -> bool:
        return self.feat in (FEAT_BOTH, FEAT_TITLE)

    @property
    def feat_in_artists(self) -> bool:
        return self.feat in (FEAT_BOTH, FEAT_ARTIST)

    @classmethod
    def from_args(cls, args: Any) -> "SoundCloudFormat":
        return cls(
            feat=args.soundcloud_feat,
            keep_producers=args.soundcloud_keep_producers,
            keep_versions=not args.soundcloud_drop_versions,
            strip_promo=not args.soundcloud_keep_promo,
            filename_template=args.soundcloud_filename,
        )

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> "SoundCloudFormat":
        """From server job options (already validated by the server); a
        missing or broken value means the default."""
        default = cls()
        feat = options.get("soundcloud_feat")
        template = options.get("soundcloud_filename")
        try:
            template = validate_filename_template(template)
        except ValueError:
            template = default.filename_template

        def flag(key: str, fallback: bool) -> bool:
            value = options.get(key)
            return value if isinstance(value, bool) else fallback

        return cls(
            feat=feat if feat in FEAT_MODES else default.feat,
            keep_producers=flag("soundcloud_keep_producers", default.keep_producers),
            keep_versions=flag("soundcloud_keep_versions", default.keep_versions),
            strip_promo=flag("soundcloud_strip_promo", default.strip_promo),
            filename_template=template,
        )

    def options(self) -> dict[str, Any]:
        """The server job options for these settings."""
        return {
            "soundcloud_feat": self.feat,
            "soundcloud_keep_producers": self.keep_producers,
            "soundcloud_keep_versions": self.keep_versions,
            "soundcloud_strip_promo": self.strip_promo,
            "soundcloud_filename": self.filename_template,
        }


# -- lyrics settings ------------------------------------------------------------------
LYRICS_PROVIDERS = ("lrclib", "musixmatch", "genius")
LYRICS_RETRY_DAYS_DEFAULT = LYRICS_RETRY_COOLDOWN_SECONDS // (24 * 60 * 60)
LYRICS_RETRY_DAYS_MAX = 365
# Bounds of --lyrics-length-tolerance (loose mode, plain text only).
LYRICS_LENGTH_TOLERANCE_MIN = LYRICS_STRICT_DURATION_TOLERANCE
LYRICS_LENGTH_TOLERANCE_MAX = 60.0


def parse_lyrics_providers(value: Any) -> tuple[str, ...]:
    """"lrclib,genius" or ["lrclib", "genius"] -> ("lrclib", "genius"):
    known names only, each once, at least one. Raises ValueError."""
    items = value.split(",") if isinstance(value, str) else value
    if not isinstance(items, (list, tuple)):
        raise ValueError("the lyrics providers must be a list")
    names: list[str] = []
    for item in items:
        name = item.strip().lower() if isinstance(item, str) else None
        if name not in LYRICS_PROVIDERS:
            raise ValueError(f"unknown lyrics provider {item!r}; use {', '.join(LYRICS_PROVIDERS)}")
        if name in names:
            raise ValueError(f"lyrics provider '{name}' is listed twice")
        names.append(name)
    if not names:
        raise ValueError("at least one lyrics provider is needed")
    return tuple(names)


def validate_length_tolerance(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("the length tolerance must be a number of seconds")
    if not LYRICS_LENGTH_TOLERANCE_MIN <= float(value) <= LYRICS_LENGTH_TOLERANCE_MAX:
        raise ValueError(f"the length tolerance must be between {LYRICS_LENGTH_TOLERANCE_MIN:g} "
                         f"and {LYRICS_LENGTH_TOLERANCE_MAX:g} seconds")
    return float(value)


def validate_retry_days(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= LYRICS_RETRY_DAYS_MAX:
        raise ValueError(f"the retry interval must be a whole number of days from 0 to {LYRICS_RETRY_DAYS_MAX}")
    return value


@dataclass(frozen=True)
class LyricsSettings:
    """Finer lyrics settings on top of the strict/loose mode. The defaults
    are the behaviour of versions before these settings existed. Synced
    lyrics always need the length within 2 s, whatever is set here."""
    providers: tuple[str, ...] = LYRICS_PROVIDERS   # tried in this order
    plain: bool = True                 # plain text when no synced lyrics match
    # Loose mode: the largest length difference for plain text.
    length_tolerance: float = LYRICS_LOOSE_DURATION_TOLERANCE
    # Strict mode: a sped-up/slowed/8D/bass-boosted upload may take the
    # original's plain text (loose mode always allows it).
    original_for_versions: bool = False
    # A track whose search found nothing is searched again after this many
    # days; 0 searches again on every run.
    retry_days: int = LYRICS_RETRY_DAYS_DEFAULT

    @property
    def retry_seconds(self) -> int:
        return self.retry_days * 24 * 60 * 60

    @classmethod
    def from_args(cls, args: Any) -> "LyricsSettings":
        return cls(
            providers=args.lyrics_providers,
            plain=not args.lyrics_synced_only,
            length_tolerance=args.lyrics_length_tolerance,
            original_for_versions=args.lyrics_original_for_versions,
            retry_days=args.lyrics_retry_days,
        )

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> "LyricsSettings":
        """From server job options (already validated by the server); a
        missing or broken value means the default."""
        default = cls()

        def checked(key: str, validate, fallback):
            try:
                return validate(options[key]) if key in options else fallback
            except ValueError:
                return fallback

        def flag(key: str, fallback: bool) -> bool:
            value = options.get(key)
            return value if isinstance(value, bool) else fallback

        return cls(
            providers=checked("lyrics_providers", parse_lyrics_providers, default.providers),
            plain=flag("lyrics_plain", default.plain),
            length_tolerance=checked("lyrics_length_tolerance", validate_length_tolerance,
                                     default.length_tolerance),
            original_for_versions=flag("lyrics_original_for_versions", default.original_for_versions),
            retry_days=checked("lyrics_retry_days", validate_retry_days, default.retry_days),
        )

    def options(self) -> dict[str, Any]:
        """The server job options for these settings."""
        return {
            "lyrics_providers": list(self.providers),
            "lyrics_plain": self.plain,
            "lyrics_length_tolerance": self.length_tolerance,
            "lyrics_original_for_versions": self.original_for_versions,
            "lyrics_retry_days": self.retry_days,
        }


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
    lyrics_settings: LyricsSettings = field(default_factory=LyricsSettings)
    recheck: bool = False
    soundcloud_reposts: bool = False
    soundcloud_likes: bool = False
    # Opt-in: a track SoundCloud does not give out (DRM, preview, blocked) is
    # looked up on YouTube Music and taken from there when artist, title,
    # version and length all match.
    soundcloud_fallback: bool = False
    soundcloud_format: SoundCloudFormat = field(default_factory=SoundCloudFormat)

    @classmethod
    def from_output_dir(cls, output_dir: Path) -> "AppConfig":
        music_dir = output_dir.expanduser().resolve()
        soundcloud_dir = music_dir / SOUNDCLOUD_SUBDIR
        return cls(music_dir=music_dir, soundcloud_dir=soundcloud_dir)

    def ensure_dirs(self) -> None:
        self.music_dir.mkdir(parents=True, exist_ok=True)
        self.soundcloud_dir.mkdir(parents=True, exist_ok=True)
