# Music Loader

[![CLI](https://github.com/skrpld/music-loader/actions/workflows/cli.yml/badge.svg)](https://github.com/skrpld/music-loader/actions/workflows/cli.yml)
[![Android](https://github.com/skrpld/music-loader/actions/workflows/android.yml/badge.svg)](https://github.com/skrpld/music-loader/actions/workflows/android.yml)
[![License](https://img.shields.io/github/license/skrpld/music-loader)](LICENSE)

Music Loader builds a clean, well-tagged local music library from **Spotify**
and **SoundCloud** links - ready for a player such as
[Symfonium](https://symfonium.app/).

Paste an album, playlist, artist, profile or single track, and Music Loader
downloads it, writes full metadata and cover art, files it into one folder per
album, and adds **verified** lyrics (synced `.lrc` when the timing provably
fits). Run it from the terminal, or as a small server on the machine that holds
your library and queue downloads from your phone with the Android app.

## Features

- **Spotify** via [spotDL](https://github.com/spotDL/spotify-downloader) and
  **SoundCloud** via [yt-dlp](https://github.com/yt-dlp/yt-dlp): tracks,
  albums, playlists, artists, profiles (with optional reposts and likes).
- **Real metadata**: artists, album artist, album, track/disc numbers, date,
  genre, ISRC, cover. SoundCloud credits hidden in titles
  (`A + B - song [prod. X]`, `ft.`, `W`) are parsed into proper tags; one
  artist is spelled the same way across the whole library.
- **Verified lyrics** from LRCLIB, Musixmatch and Genius: strict matching by
  artist, title, version (sped up / remix / live) and length - no lyrics is
  better than the wrong song's lyrics.
- **Reliable library**: every download is checked (Go+ previews, cut-off and
  interrupted files are rejected), duplicates and moved files are detected,
  `--recheck` brings old downloads up to the current rules.
- **Live terminal dashboard** with speed, ETA and statistics; a persistent
  failure log per run.
- **Server mode + Android app** (Material 3 Expressive): queue links from the
  phone, share links straight from the Spotify/SoundCloud apps, follow progress
  live.

## Components

| Directory | What | Docs |
|---|---|---|
| [`cli/`](cli) | Python package `music-loader`: command-line tool and HTTP server | [cli/README.md](cli/README.md) |
| [`android/`](android) | Android client for the server (Kotlin, Jetpack Compose) | [android/README.md](android/README.md) |
| [`docs/`](docs) | Behaviour in depth, server API, releasing, security | see below |

## Quick start

**Requirements:** Python 3.10+ and [ffmpeg](https://ffmpeg.org/) in `PATH`.

Install the CLI from the latest [`cli-v*` release](https://github.com/skrpld/music-loader/releases)
(the `.whl` asset), or straight from the repository:

```bash
pipx install "git+https://github.com/skrpld/music-loader#subdirectory=cli"
# or: pip install "git+https://github.com/skrpld/music-loader#subdirectory=cli"
```

Download:

```bash
music-loader "https://open.spotify.com/album/..." -o ~/Music
music-loader links.txt "https://soundcloud.com/..." -o ~/Music   # file: one link per line, # = comment
music-loader                                                       # interactive
```

Use it from the phone:

```bash
music-loader serve -o ~/Music     # prints the server address and an access token
```

Install the APK from the latest [`android-v*` release](https://github.com/skrpld/music-loader/releases),
open **Settings → Server**, enter the address and token, **Test → Save**.

All options: [cli/README.md](cli/README.md).

## Documentation

- [How it works](docs/how-it-works.md) - library layout, metadata rules,
  lyrics matching, download checks and `--recheck`, SoundCloud and Spotify
  specifics.
- [Server mode and API](docs/server.md) - `music-loader serve`, tokens,
  deployment, HTTP API.
- [Releasing](docs/releasing.md) - tags, versions, CI workflows, Android
  signing secrets.
- [Security](docs/security.md) - what is protected and how secrets are
  handled.

## Repository layout

```
music-loader/
├── cli/                         # Python package (CLI + server)
│   ├── pyproject.toml
│   ├── music_loader/
│   └── tests/
├── android/                     # Android app (Gradle project)
├── docs/                        # detailed documentation
└── .github/
    ├── workflows/cli.yml        # tests, wheel; release on cli-v* tags
    ├── workflows/android.yml    # APK; release on android-v* tags
    └── scripts/publish-release.sh
```

## Development

```bash
# CLI
cd cli
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check . && pytest

# Android (JDK 17+, Android SDK)
cd android
./gradlew assembleDebug
```

## License

[Apache License 2.0](LICENSE).

## Disclaimer

Music Loader is a personal tool for building a local library. Download only
content you have the right to download and respect the terms of service of
Spotify, SoundCloud, YouTube and the lyrics providers.
