# Music Loader

Download music with full metadata from Spotify and SoundCloud. Fetches synced lyrics (.lrc), embeds thumbnails, and displays live progress in the terminal.

## Quick Start

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

# Interactive mode
music-loader
```

## Features

- **Spotify** – albums, playlists, artists (via spotDL)
- **SoundCloud** – full tracks and collections (via yt-dlp)
- **Synced lyrics** (.lrc) – automatic lookup and embedding
- **Metadata** – artist, album, duration, thumbnails
- **Live dashboard** – progress bars, activity log, stats
- **Duplicate detection** – persistent index prevents re-downloads
- **Failure logging** – errors saved to `.music-loader-logs/`

## Parallel Workers

Control concurrency for post-processing and lyrics:

```bash
music-loader links.txt -o /path/to/Music \
  --soundcloud-workers 4 \
  --lyrics-workers 2
```

## How It Works

- **Spotify:** Downloads via spotDL with MP3 320kbps and Genius lyrics
- **SoundCloud:** Producer/worker pipeline — yt-dlp downloads audio, workers handle MP3 conversion, metadata, and thumbnails in parallel
- **Lyrics:** Fetched from Musixmatch, NetEase, Lrclib, Genius
- **Deduplication:** SoundCloud ID index prevents duplicate downloads; legacy metadata fallback for older files

## Output

```
Music/
├── Spotify downloads (organized by artist/album)
├── SoundCloud/
│   ├── track files
│   ├── SoundCloud_New.m3u8 (playlist)
│   └── .sc_index.json (dedup index)
└── .music-loader-logs/
    └── failures-YYYYMMDD-HHMMSS.log
```
