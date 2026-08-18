# Music Loader

Downloads music with metadata from Spotify (via spotDL) and SoundCloud
(via yt-dlp), looks up synced lyrics (.lrc) and prepares the library for
Symfonium. Progress, speed and download statistics are shown in a live
terminal interface (based on `rich`).

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
    ├── text_utils.py       # track title cleanup
    └── ui.py               # live dashboard (rich): progress bars, log, statistics
```

The code is split into separate modules for one reason: it is easier to edit
or fix a single part (for example, only the yt-dlp output parsing) without
touching everything else. Everything lives under `music_loader/` because this
is an installable Python package, so it can be installed with
`pip install -e .` and invoked as `music-loader` from anywhere.

## Installation

```bash
cd music-loader
pip install -e .
```

A system-wide `ffmpeg` is also required (it is not installed via pip):

```bash
# Debian/Ubuntu
sudo apt install ffmpeg
# macOS
brew install ffmpeg
```

After `pip install -e .` the `music-loader` command becomes available
(see `pyproject.toml`, the `[project.scripts]` section).

## Usage

The links (or a file containing them) and the target Music folder are passed
on startup:

```bash
# A single link
music-loader "https://open.spotify.com/album/..." -o /path/to/Music

# A file with a list of links (one per line)
music-loader links.txt -o /path/to/Music

# Several sources at once
music-loader links.txt "https://soundcloud.com/..." -o /path/to/Music
```

If the link or the folder is omitted, the script asks for it interactively
once, before any downloading starts.

Alternative run without installing the package:

```bash
pip install -r <(python3 -c "import tomllib;print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))")
python3 -m music_loader links.txt -o ./Music
```

## Note on progress parsing

SoundCloud progress and speed (yt-dlp) are parsed from the standard output
format `[download] 45.2% of 3.45MiB at 1.23MiB/s ETA 00:02`, which is stable.
Spotify progress (spotdl) is detected best-effort from percentage values in
the output, because spotdl's format can differ slightly between versions. If a
percentage cannot be recognized, the bar shows an activity indicator and the
exact messages are still visible in the "Activity" panel.

## SoundCloud pipeline and duplicate detection

Track discovery uses a flat playlist listing (`--flat-playlist`), so a large
playlist or a whole user profile is resolved with a single cheap request
instead of a per-track metadata lookup. The full metadata needed for tagging
(title, uploader, thumbnail, description) is printed by yt-dlp while the track
is being downloaded, so nothing is resolved twice. While resolving, the
progress line shows the elapsed time so a slow link never looks frozen.

Downloads use a producer/worker pipeline: yt-dlp downloads only the source
audio, then a configurable pool performs MP3 conversion, metadata and
thumbnail embedding. The default is 4 post-processing workers and 2 lyrics
workers; configure them with `--soundcloud-workers N` and `--lyrics-workers N`.
Lyrics are queued independently and do not block subsequent audio downloads.

A hidden `.sc_index.json` in the SoundCloud directory maps the SoundCloud
track ID to the actual local file. A legacy compatibility scan of embedded
artist/title/duration metadata recognizes files created before the index
existed; that scan is expensive, so it runs lazily — only when an ID lookup
misses — and at most once per run, shared across all links. Recognized legacy
files are promoted into the exact ID mapping and the existing
`.sc_archive.txt` is updated only after a successful post-processing step.
Track statistics are counted once per discovered track as
`downloaded / already existed / failed`, and lyrics lookups are counted as
`found / missing`.

Interrupted runs can leave partial files in the hidden `.sc_downloads`
staging folder; leftovers older than 24 hours are removed automatically at the
start of the next run.
