# Music Loader

[![CLI](https://github.com/skrpld/music-loader/actions/workflows/cli.yml/badge.svg)](https://github.com/skrpld/music-loader/actions/workflows/cli.yml)
[![Android](https://github.com/skrpld/music-loader/actions/workflows/android.yml/badge.svg)](https://github.com/skrpld/music-loader/actions/workflows/android.yml)
[![License](https://img.shields.io/github/license/skrpld/music-loader)](LICENSE)

Builds a clean, well-tagged local music library from Spotify and SoundCloud links, with verified lyrics.

Paste an album, playlist, artist, profile or single track, and Music Loader downloads it
([spotDL](https://github.com/spotDL/spotify-downloader) for Spotify,
[yt-dlp](https://github.com/yt-dlp/yt-dlp) for SoundCloud), writes full metadata and cover art,
files it into one folder per album, and adds lyrics from LRCLIB, Musixmatch and Genius only when
artist, title, version and length match (synced `.lrc` when the timing provably fits). The result
is ready for a player such as [Symfonium](https://symfonium.app/).

- **Metadata**: artists, album artist, album, track/disc numbers, date, genre, ISRC, cover.
  SoundCloud credits hidden in titles (`A + B - song [prod. X]`, `ft.`, `W`) become proper tags;
  one artist is spelled the same way across the whole library.
- **Checks**: Go+ previews, cut-off and interrupted files are rejected; duplicates and moved files
  are detected; `--recheck` brings old downloads up to the current rules.
- **Three front-ends**: a terminal tool with a live dashboard, an Android app that downloads on the
  phone itself (the same Python code runs inside it), and a server mode that lets the app queue
  downloads on the machine holding the library.

The repository holds two independently released components: [`cli/`](cli) (Python package
`music-loader`: command-line tool and HTTP server) and [`android/`](android) (Android app that
embeds the package).

## Table of Contents

- [Install](#install)
- [Usage](#usage)
- [Development](#development)
- [Documentation](#documentation)
- [Disclaimer](#disclaimer)
- [Contributing](#contributing)
- [License](#license)

## Install

Command-line tool and server: requires Python 3.10+ and [ffmpeg](https://ffmpeg.org/) in `PATH`.

```sh
pipx install "git+https://github.com/skrpld/music-loader#subdirectory=cli"
```

Or install the `.whl` asset of the latest [`cli-v*` release](https://github.com/skrpld/music-loader/releases)
with `pipx install ./music_loader-<VERSION>-py3-none-any.whl`. Details and ffmpeg installation:
[cli/README.md](cli/README.md).

Android app: requires Android 8.0 (API 26)+ on an `arm64-v8a` device. Install
`music-loader-<VERSION>.apk` from the latest [`android-v*` release](https://github.com/skrpld/music-loader/releases);
see [android/README.md](android/README.md).

## Usage

Download an album into `~/Music`:

```sh
music-loader "https://open.spotify.com/album/..." -o ~/Music
```

The album lands in `~/Music/<album artist> - <album>/`, tagged, with verified lyrics where found.

On the phone, allow storage access when asked and paste links on the **Download** screen; the music
lands in the phone's `Music` folder.

### CLI

```text
music-loader [OPTIONS] [SOURCE ...]
music-loader serve -o <MUSIC_FOLDER> [OPTIONS]
```

`SOURCE` is a link or a `.txt` file with one link per line (`#` starts a comment); without it the
tool asks interactively.

```sh
music-loader links.txt "https://soundcloud.com/..." -o ~/Music
```

Download on a computer and queue from the phone: run the server, then enter the printed address and
token in the app under **Settings → Where to download → Server** ([guide](docs/how-to/run-a-server.md)).

```sh
music-loader serve -o ~/Music
```

All options: [docs/reference/cli.md](docs/reference/cli.md).

## Development

```sh
# CLI
cd cli
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check . && pytest

# Android (JDK 17+, Android SDK, Python 3.13 in PATH)
cd android
./gradlew assembleDebug
```

```text
music-loader/
├── cli/                         # Python package (CLI + server): pyproject.toml, music_loader/, tests/
├── android/                     # Android app (Gradle project, embeds cli/music_loader)
├── docs/                        # documentation
└── .github/
    ├── workflows/cli.yml        # tests, wheel; release on cli-v* tags
    ├── workflows/android.yml    # APK; release on android-v* tags
    └── scripts/publish-release.sh
```

Every change under `cli/music_loader/` also ships in the Android app. Releases:
[Publish a release](docs/how-to/publish-a-release.md).

## Documentation

See [docs/](docs/README.md).

## Disclaimer

Music Loader is a personal tool for building a local library. Download only content you have the
right to download and respect the terms of service of Spotify, SoundCloud, YouTube and the lyrics
providers.

## Contributing

Questions and bug reports: [issues](https://github.com/skrpld/music-loader/issues). Pull requests
are welcome; commits follow [Conventional Commits](https://www.conventionalcommits.org/) with a
scope such as `cli`, `server`, `android`, `spotify` or `soundcloud`. Code, comments, docs and commit
messages are in English.

## License

[Apache-2.0](LICENSE) © skrpld
