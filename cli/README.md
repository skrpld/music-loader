# music-loader (CLI and server)

Downloads music with full metadata from Spotify (via spotDL) and SoundCloud
(via yt-dlp), looks up **verified** lyrics, and files everything into a
library ready for Symfonium. Progress is shown in a live terminal dashboard;
`music-loader serve` exposes the same engine to the Android app.

Project overview: <https://github.com/skrpld/music-loader>.

## Installation

**Requirements:** Python 3.10+, [ffmpeg](https://ffmpeg.org/) in `PATH`.
spotDL, yt-dlp (with `curl-cffi`), rich, mutagen and beautifulsoup4 are
installed as dependencies. Optional: [Deno](https://deno.com) or Node.js
(faster, more reliable YouTube extraction for Spotify tracks).

```bash
# ffmpeg
sudo apt install ffmpeg        # Debian/Ubuntu
brew install ffmpeg            # macOS
winget install ffmpeg          # Windows

# From a GitHub release (asset of a cli-v* release)
pipx install ./music_loader-<version>-py3-none-any.whl

# From the repository
pipx install "git+https://github.com/skrpld/music-loader#subdirectory=cli"

# From a checkout (development)
pip install -e "cli[dev]"
```

Without installing: `cd cli && python -m music_loader ...` (the dependencies
must be installed in that Python).

## Usage

```bash
# A single link
music-loader "https://open.spotify.com/album/..." -o /path/to/Music

# A file with one link per line ("#" starts a comment)
music-loader links.txt -o /path/to/Music

# Several sources at once
music-loader links.txt "https://soundcloud.com/..." -o /path/to/Music

# Interactive: asks for links and the target folder
music-loader

# Bring tracks downloaded by an older version up to the current rules
music-loader links.txt -o /path/to/Music --recheck
```

Accepted links: Spotify (`open.spotify.com/{track,album,playlist,artist}/...`,
`spotify:...` URIs) and SoundCloud (`soundcloud.com`, `m.`,
`on.soundcloud.com`, `snd.sc`, API URLs). Anything else is reported and
skipped.

| Link | Downloads |
|---|---|
| Spotify track / album / playlist | that item |
| Spotify artist | the whole discography |
| SoundCloud track | that track |
| SoundCloud set (`/sets/...`) | its tracks; album/EP/single sets also give album, album artist, track numbers |
| SoundCloud profile | the artist's own albums and tracks; reposts with `--soundcloud-reposts`, likes with `--soundcloud-likes` |
| SoundCloud profile page (`/tracks`, `/albums`, `/sets`, `/likes`, `/reposts`) | exactly that page |

## Options

| Option | Default | Meaning |
|---|---|---|
| `-o, --output` | asked | Target Music folder |
| `--no-lyrics` | off | Do not look up lyrics |
| `--lyrics-loose` | off | Loose lyrics matching; default is strict |
| `--lyrics-workers N` | 2 | Parallel lyrics lookups |
| `--recheck` | off | Check already downloaded tracks against the current rules again |
| `--soundcloud-reposts` | off | Profile link: also download the profile's reposts |
| `--soundcloud-likes` | off | Profile link: also download the profile's likes |
| `--soundcloud-download-workers N` | 2 | Parallel SoundCloud downloads |
| `--soundcloud-workers N` | 4 | Parallel SoundCloud conversion/tagging workers |
| `--spotify-threads N` | 4 | Parallel spotdl downloads |
| `--spotify-client-id`, `--spotify-client-secret` | none | Optional own Spotify app credentials (prefer the environment variables below) |
| `--version` | | Print the version |

### Environment variables

| Variable | Meaning |
|---|---|
| `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` | Own Spotify application credentials (<https://developer.spotify.com/dashboard>). Optional: spotDL 4.5+ works without them; with them spotdl uses the official Web API. |
| `MUSIC_LOADER_TOKEN` | Access token for `music-loader serve` |

Command-line arguments are visible to every user of the machine in the
process list; environment variables are the safer channel for secrets.

## Server mode

```bash
music-loader serve -o /path/to/Music
```

Prints the server address and an access token for the Android app. Options,
token handling, deployment and the HTTP API: [docs/server.md](https://github.com/skrpld/music-loader/blob/main/docs/server.md).

## What ends up on disk

```
Music/
├── <album artist> - <album>/<NN> - <title>.mp3 (+ .lrc)   # Spotify
├── SoundCloud/
│   ├── <album artist> - <album>/<NN> - <title>.mp3
│   ├── <artist> - <song> - Single/01 - <song>.mp3
│   └── *.m3u8                                              # playlists
└── .music-loader-logs/failures-YYYYMMDD-HHMMSS.log
```

Plus a few hidden index files (`.sc_index.json`, `.spotify_index.json`, ...)
that make re-runs fast and detect moved files. Details on metadata, lyrics
matching and checks: [docs/how-it-works.md](https://github.com/skrpld/music-loader/blob/main/docs/how-it-works.md).

## Development

```bash
pip install -e ".[dev]"
ruff check .
pytest
```

The version lives in `music_loader/__init__.py`; releases are cut by pushing
a `cli-v<version>` tag ([docs/releasing.md](https://github.com/skrpld/music-loader/blob/main/docs/releasing.md)).
