# Music Loader

Downloads music with full metadata from Spotify (via spotDL) and SoundCloud
(via yt-dlp), looks up synced lyrics (.lrc), embeds thumbnails and prepares the
library for Symfonium. Progress, speed and statistics are shown in a live
terminal interface (based on `rich`).

## Quick start

**Requirements:** Python 3.10+, ffmpeg, spotdl, yt-dlp

```bash
pip install -e .
```

Install ffmpeg:

```bash
# Ubuntu/Debian
sudo apt install ffmpeg
# macOS
brew install ffmpeg
```

**Usage:**

```bash
# Single link
music-loader "https://open.spotify.com/album/..." -o /path/to/Music

# From file (one link per line)
music-loader links.txt -o /path/to/Music

# Several sources at once
music-loader links.txt "https://soundcloud.com/..." -o /path/to/Music

# Interactive mode
music-loader
```

Without installing the package:

```bash
python3 -m music_loader links.txt -o ./Music
```

## Project layout

```
music-loader/
├── pyproject.toml          # dependencies and entry point
├── README.md
└── music_loader/           # package source
    ├── __main__.py         # python -m music_loader
    ├── cli.py              # command-line arguments, main loop
    ├── config.py           # paths and constants
    ├── deps.py             # ffmpeg / spotdl / yt-dlp checks
    ├── process.py          # subprocess execution with streamed output parsing
    ├── spotify.py          # Spotify download logic
    ├── soundcloud.py       # SoundCloud download logic
    ├── lyrics.py           # .lrc lyrics lookup
    ├── playlist.py         # SoundCloud .m3u8 playlist updates
    ├── runlog.py           # persistent per-run failure log
    ├── text_utils.py       # track title cleanup, lyrics query building
    └── ui.py               # live dashboard (rich): progress bars, log, statistics
```

## Features

- **Spotify** – albums, playlists, artists (via spotDL, MP3 320 kbps, Genius lyrics)
- **SoundCloud** – single tracks, playlists and whole profiles (via yt-dlp)
- **Synced lyrics** (.lrc) – Musixmatch, NetEase, Lrclib, Genius
- **Metadata** – artist, album, duration, embedded thumbnails
- **Live dashboard** – progress bars, activity log, link/track statistics
- **Duplicate detection** – persistent ID index prevents re-downloads
- **Failure logging** – errors saved to `.music-loader-logs/`

## Parallel workers

```bash
music-loader links.txt -o /path/to/Music \
  --soundcloud-workers 4 \
  --lyrics-workers 2
```

Defaults: 4 post-processing workers, 2 lyrics workers.

## SoundCloud pipeline and duplicate detection

Track discovery uses a flat playlist listing (`--flat-playlist`), so a large
playlist or a whole user profile resolves with a single cheap request instead
of a per-track metadata lookup. The full metadata needed for tagging (title,
uploader, thumbnail, description) is printed by yt-dlp while the track is being
downloaded, so nothing is resolved twice. While resolving, the progress line
shows the elapsed time so a slow link never looks frozen.

Downloads use a producer/worker pipeline: yt-dlp downloads only the source
audio, then a configurable pool performs MP3 conversion, metadata and thumbnail
embedding. Lyrics are queued independently and never block subsequent audio
downloads.

A hidden `.sc_index.json` in the SoundCloud directory maps the SoundCloud track
ID to the actual local file. A legacy compatibility scan of embedded
artist/title/duration metadata recognizes files created before the index
existed; that scan is expensive, so it runs lazily — only when an ID lookup
misses — and at most once per run, shared across all links. Recognized legacy
files are promoted into the exact ID mapping, and `.sc_archive.txt` is updated
only after a successful post-processing step.

Interrupted runs can leave partial files in the hidden `.sc_downloads` staging
folder; leftovers older than 24 hours are removed automatically at the start of
the next run.

## Lyrics search

The lyrics search query is built from the track's real artist and title tags
(read back from the downloaded file, or from platform metadata as a fallback)
rather than from the filename, so a short/common title is disambiguated by
its artist instead of matching an unrelated song.

If a lyrics search finds nothing for a track, that result is remembered in a
hidden `.sc_lyrics_attempts.json` in the SoundCloud directory. The same track
is not searched again until 7 days have passed, so already-downloaded tracks
with no available lyrics don't cost search time on every run. Once lyrics are
found and saved as a `.lrc` file, that file's presence alone is enough to skip
the track in future runs.

## Note on progress parsing

SoundCloud progress and speed (yt-dlp) are parsed from the standard output
format `[download] 45.2% of 3.45MiB at 1.23MiB/s ETA 00:02`, which is stable.
Spotify progress (spotdl) is detected best-effort from percentage values in the
output, because spotdl's format can differ slightly between versions. If a
percentage cannot be recognized, the bar shows an activity indicator and the
exact messages remain visible in the "Activity" panel.

## Output

```
Music/
├── Spotify downloads (organized by artist/album)
├── SoundCloud/
│   ├── track files
│   ├── SoundCloud_New.m3u8 (playlist)
│   ├── .sc_index.json (dedup index)
│   └── .sc_lyrics_attempts.json (lyrics retry cooldown)
└── .music-loader-logs/
    └── failures-YYYYMMDD-HHMMSS.log
```
