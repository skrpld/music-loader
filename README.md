# Music Loader

Downloads music with metadata from Spotify (via spotDL) and SoundCloud (via
yt-dlp), looks up synced lyrics (.lrc) and prepares the library for Symfonium.
Progress, speed and download statistics are shown in a live terminal interface
(built on `rich`).

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
    ├── process.py          # subprocess runner with live output parsing
    ├── spotify.py          # Spotify download logic
    ├── soundcloud.py       # SoundCloud download logic
    ├── lyrics.py           # .lrc lyrics lookup
    ├── playlist.py         # SoundCloud .m3u8 playlist updates
    ├── runlog.py           # persistent per-run failure log
    ├── text_utils.py       # track title cleanup
    └── ui.py               # live dashboard (rich): progress bars, log, stats
```

The code is split into small files for one reason: it is easier to edit or fix
a single part (for example, only the yt-dlp output parsing) without touching
everything else. Everything lives under `music_loader/` because it is an
installable Python package, so it can be installed with `pip install -e .` and
run as `music-loader` from anywhere.

## Installation

```bash
cd music-loader
pip install -e .
```

A system `ffmpeg` is also required (it is not installed via pip):

```bash
# Debian/Ubuntu
sudo apt install ffmpeg
# macOS
brew install ffmpeg
```

After `pip install -e .` the `music-loader` command becomes available (see
`pyproject.toml`, the `[project.scripts]` section).

## Usage

Links (or a file containing them) and the target Music folder are passed at
startup:

```bash
# A single link
music-loader "https://open.spotify.com/album/..." -o /path/to/Music

# A file with one link per line
music-loader links.txt -o /path/to/Music

# Several sources at once
music-loader links.txt "https://soundcloud.com/..." -o /path/to/Music
```

If no link or folder is given, the program asks for them interactively once,
before any download starts.

Running without installing the package:

```bash
pip install -r <(python3 -c "import tomllib;print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))")
python3 -m music_loader links.txt -o ./Music
```

Exit codes: `0` on success, `1` on a startup problem (missing dependency, no
links, unusable output folder), `2` if at least one link failed, `130` if
interrupted with Ctrl-C.

## A note on progress parsing

SoundCloud (yt-dlp) progress and speed are parsed from the standard output
format `[download] 45.2% of 3.45MiB at 1.23MiB/s ETA 00:02`. Percentage, speed
and ETA are matched independently, so the bar keeps moving even when yt-dlp
omits the size or speed part. Spotify (spotdl) progress is best-effort:
spotdl's output format varies slightly between versions, so if a percentage
cannot be recognized the bar falls back to an activity indicator while the
exact messages stay visible in the "Activity" panel.

## SoundCloud pipeline and duplicate detection

SoundCloud downloads use a producer/worker pipeline: yt-dlp downloads only the
source audio, then a configurable pool performs MP3 conversion, metadata and
thumbnail embedding. The default is 4 post-processing workers and 2 lyrics
workers; configure them with `--soundcloud-workers N` and `--lyrics-workers N`.
Lyrics are queued independently and do not block subsequent audio downloads.

A hidden `.sc_index.json` in the SoundCloud directory maps the SoundCloud track
ID to the actual local file. The downloader also performs a legacy
compatibility scan using embedded artist/title/duration metadata, so files
created before the index existed can be recognized without relying only on
filenames or `--no-overwrites`. That scan is expensive, so it runs lazily (only
when an ID lookup misses) and is reused for the whole run. Recognized legacy
files are promoted into the exact ID mapping and the existing
`.sc_archive.txt` is updated only after a successful post-processing step.
Track statistics are counted once per discovered track as
`downloaded / already existed / failed`.

Raw downloads are staged in a hidden `.sc_downloads/` directory and removed
once conversion succeeds or the track fails, so nothing is left behind between
runs.

## Failure log

Every failure is appended to `<Music>/.music-loader-logs/failures-<timestamp>.log`
as it happens, so failures that scrolled off the "Activity" panel can still be
reviewed (or grepped) after a long batch run. The path is shown in the
dashboard and again in the final summary.
