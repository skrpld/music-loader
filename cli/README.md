# music-loader

Downloads music with metadata from Spotify and SoundCloud, fetches verified lyrics, prepares the library for Symfonium.

Downloads Spotify links via spotDL and SoundCloud links via yt-dlp, writes full metadata and
cover art, looks up **verified** lyrics, and files everything into one folder per album. Progress
is shown in a live terminal dashboard. `music-loader serve` exposes the same engine to the Android
app, which also embeds this package to download on the phone itself.

Project overview: <https://github.com/skrpld/music-loader>.

## Table of Contents

- [Install](#install)
- [Usage](#usage)
- [Development](#development)
- [Contributing](#contributing)
- [License](#license)

## Install

Requires Python 3.10+ and [ffmpeg](https://ffmpeg.org/) in `PATH`. The package is not on PyPI;
install it from a GitHub release (the `.whl` asset of a `cli-v*` release):

```sh
pipx install ./music_loader-<VERSION>-py3-none-any.whl
```

`<VERSION>` is the release version, for example `2.1.0`. Or install straight from the repository:

```sh
pipx install "git+https://github.com/skrpld/music-loader#subdirectory=cli"
```

### Dependencies

ffmpeg is installed by hand:

```sh
sudo apt install ffmpeg        # Debian/Ubuntu
brew install ffmpeg            # macOS
winget install ffmpeg          # Windows
```

spotDL, yt-dlp (with `curl-cffi`), rich, mutagen and beautifulsoup4 are installed as package
dependencies. Optional: [Deno](https://deno.com) or Node.js for faster, more reliable YouTube
extraction for Spotify tracks.

### Updating

Run the same `pipx install` command with `--force`, or `pipx upgrade music-loader` for an install
from the repository.

## Usage

```sh
music-loader "https://open.spotify.com/album/..." -o ~/Music
```

The album is downloaded into `~/Music/<album artist> - <album>/`, tagged, with lyrics next to the
tracks where a verified match exists.

### CLI

```text
music-loader [OPTIONS] [SOURCE ...]
music-loader serve -o <MUSIC_FOLDER> [OPTIONS]
```

A file with one link per line (`#` starts a comment), together with another link:

```sh
music-loader links.txt "https://soundcloud.com/..." -o ~/Music
```

Interactive mode, which asks for links and the target folder:

```sh
music-loader
```

Bring tracks downloaded by an older version up to the current rules (`--existing all` rewrites
every track, `--recheck` is the same):

```sh
music-loader links.txt -o ~/Music --existing outdated
```

Run as a server for the Android app (prints the address and an access token):

```sh
music-loader serve -o ~/Music
```

Run `music-loader --help` for all options. Full reference: [CLI](https://github.com/skrpld/music-loader/blob/main/docs/reference/cli.md),
[Server](https://github.com/skrpld/music-loader/blob/main/docs/reference/server.md). Naming,
metadata and lyrics rules: [How it works](https://github.com/skrpld/music-loader/blob/main/docs/explanation/how-it-works.md).

Without installing: `cd cli && python -m music_loader ...` (the dependencies must be installed in
that Python).

## Development

```sh
pip install -e ".[dev]"
ruff check .
pytest
```

Code stays compatible with Python 3.10 (CI tests 3.10 and 3.13). The version lives in
`music_loader/__init__.py`; releases are cut by pushing a `cli-v<VERSION>` tag or
by running the CLI workflow on `main` with `release=true`
([Publish a release](https://github.com/skrpld/music-loader/blob/main/docs/how-to/publish-a-release.md)).

## Contributing

Questions and bug reports: [issues](https://github.com/skrpld/music-loader/issues). Pull requests
are welcome; commits follow [Conventional Commits](https://www.conventionalcommits.org/) with a
scope such as `cli`, `server`, `spotify` or `soundcloud`.

## License

[Apache-2.0](LICENSE) © skrpld
