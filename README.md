# Music Loader

Auto-download music from **Spotify** and **SoundCloud**, fetch synced **Genius** lyrics (`.lrc`), skip duplicates, and prepare a clean library for **Symfonium**.

---

## Features

- **Spotify** (`spotDL`): downloads albums/tracks in 320 kbps MP3 with metadata, cover art, and `.lrc` lyrics. Organized as `Music/Artist - Album/01 - Title.mp3`.
- **SoundCloud** (`yt-dlp`): downloads tracks/playlists into `Music/SoundCloud/`. Keeps an archive file to skip duplicates. Auto-builds `SoundCloud_New.m3u8`.
- **Symfonium-ready**: folder structure, ID3 tags, M3U8 playlist, and `.lrc` karaoke subtitles out of the box.

---

## Requirements

- **Python 3.9+**
- **FFmpeg** (required for audio conversion and thumbnail embedding)
  - Windows: `winget install Gyan.FFmpeg` or `choco install ffmpeg`
  - macOS: `brew install ffmpeg`
  - Linux: `sudo apt update && sudo apt install ffmpeg`

## Installation

1. Clone or download this repo into a folder.
2. Install Python dependencies:

```bash
pip install spotdl>=4.2.0 yt-dlp>=2024.0.0 syncedlyrics>=0.10.0
```

---

## Usage

Three ways to run:

### A) `links.txt` (recommended)

Create `links.txt` next to the script, one URL per line. Mix Spotify and SoundCloud freely:

```text
https://open.spotify.com/album/4eLPsY3MfZB24A3A22
https://open.spotify.com/artist/06HL4z0CvFAxyv2nD0gBvH
https://soundcloud.com/artist-name/track-title
```

Then run:

```bash
python music_loader.py
```

### B) Command-line arguments

```bash
python music_loader.py "https://open.spotify.com/album/..." "https://soundcloud.com/..."
```

### C) Interactive mode

Run without arguments and without `links.txt`:

```bash
python music_loader.py
```

---

## Output Structure

```text
Music/
├── The Weeknd - After Hours/
│   ├── 01 - Alone.mp3
│   ├── 01 - Alone.lrc
│   ├── 02 - Too Late.mp3
│   └── 02 - Too Late.lrc
├── SoundCloud/
│   ├── .sc_archive.txt
│   ├── SoundCloud_New.m3u8
│   ├── Unreleased Track.mp3
│   └── Unreleased Track.lrc
```

---

## Symfonium Setup (Android)

1. **Sync folder**: copy `Music/` to your phone via USB, Syncthing, SMB, or local storage.
2. **Scan library**: open Symfonium → **Settings** → **Media Sources** → add the `Music` folder.
3. **Lyrics**: during playback, tap the lyrics icon. Symfonium auto-loads the matching `.lrc` file.
4. **Playlist**: go to **Playlists** in Symfonium; `SoundCloud_New` contains all SoundCloud tracks. Rename files/tags directly in the app if needed.

---

## Duplicate Handling

- **Spotify**: spotDL checks existing tracks by metadata; skips re-downloads.
- **SoundCloud**: yt-dlp writes unique track IDs into `.sc_archive.txt` and skips any already present.
