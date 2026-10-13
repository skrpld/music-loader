# CLI

Command-line interface of the `music-loader` package (`music-loader` command, or
`python -m music_loader`). The server subcommand is described in [Server](server.md).

```text
music-loader [OPTIONS] [SOURCE ...]
music-loader serve -o <MUSIC_FOLDER> [OPTIONS]
```

`SOURCE` is a Spotify or SoundCloud link, or a path to a `.txt` file with one link per line
(`#` starts a comment). Without `SOURCE` or `-o`, the tool asks for them interactively.

## Options

| Option | Default | Description |
|---|---|---|
| `-o, --output` | asked | Target Music folder |
| `--no-lyrics` | off | Do not look up lyrics |
| `--lyrics-loose` | off | Loose lyrics matching: fuzzy artist/title match and up to 10 s length difference (plain text only when the timing may not fit). Default is strict: exact artist, title, version and length (±2 s) |
| `--lyrics-workers N` | `2` | Parallel lyrics lookups |
| `--recheck` | off | Check already downloaded tracks against the current rules again: tags, folder and file name, broken/preview files, lyrics |
| `--soundcloud-reposts` | off | Profile link: also download the profile's reposts |
| `--soundcloud-likes` | off | Profile link: also download the profile's likes |
| `--soundcloud-fallback` | off | For a track SoundCloud does not give out (DRM, preview only, blocked), take the same track from YouTube Music when artist, title, version and length match; the tags say where it came from |
| `--soundcloud-download-workers N` | `2` | Parallel SoundCloud downloads. SoundCloud allows about 600 requests per 10 minutes; more workers hit that limit sooner |
| `--soundcloud-workers N` | `4` | Parallel SoundCloud conversion/tagging workers |
| `--spotify-threads N` | `4` | Parallel spotDL downloads |
| `--spotify-client-id` | none | Own Spotify application client id |
| `--spotify-client-secret` | none | Own Spotify application client secret |
| `--version` | | Print the version and exit |
| `-h, --help` | | Print usage and exit |

`N` must be 1 or more.

## Environment variables

| Variable | Description |
|---|---|
| `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` | Own Spotify application credentials (<https://developer.spotify.com/dashboard>). Optional: spotDL 4.5+ works without them; with them it uses the official Spotify Web API |
| `MUSIC_LOADER_TOKEN` | Access token for `music-loader serve` |

Precedence for Spotify credentials: `--spotify-client-id` / `--spotify-client-secret` > environment
variable. Command-line arguments are visible to every user of the machine in the process list; the
environment variables are the safer channel.

## Accepted links

Spotify: `open.spotify.com/{track,album,playlist,artist}/...` and `spotify:...` URIs. SoundCloud:
`soundcloud.com`, `m.soundcloud.com`, `on.soundcloud.com`, `snd.sc` and API URLs. Anything else is
reported and skipped.

| Link | Downloads |
|---|---|
| Spotify track / album / playlist | that item |
| Spotify artist | the whole discography |
| SoundCloud track | that track |
| SoundCloud set (`/sets/...`) | its tracks; album/EP/single sets also give album, album artist, track numbers |
| SoundCloud profile | the artist's own albums and tracks; reposts with `--soundcloud-reposts`, likes with `--soundcloud-likes` |
| SoundCloud profile page (`/tracks`, `/albums`, `/sets`, `/likes`, `/reposts`) | exactly that page |

## Output

```text
Music/
├── <album artist> - <album>/<NN> - <title>.mp3 (+ .lrc)   # Spotify
├── SoundCloud/
│   ├── <album artist> - <album>/<NN> - <title>.mp3
│   ├── <artist> - <song> - Single/01 - <song>.mp3
│   └── *.m3u8                                              # playlists
└── .music-loader-logs/
    ├── failures-YYYYMMDD-HHMMSS.log
    └── unavailable-YYYYMMDD-HHMMSS.log     # DRM / preview-only tracks that were skipped
```

Hidden index files (`.sc_index.json`, `.spotify_index.json`, `.music-loader-artists.json`, lyrics
attempt files) make re-runs fast and detect moved files. Naming, tagging and lyrics rules:
[How it works](../explanation/how-it-works.md).
