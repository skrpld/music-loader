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
| `--soundcloud-feat MODE` | `both` | Where featured artists go: `both` = `(feat. X)` in the title and X in the artist tag; `artist` = artist tag only; `title` = title only |
| `--soundcloud-keep-producers` | off | Keep producer credits in the title as `(prod. X)` |
| `--soundcloud-drop-versions` | off | Remove version markers (Remix, Sped Up, Slowed, Live…) from the title; lyrics matching still compares the real version |
| `--soundcloud-keep-promo` | off | Keep promotional noise (`[Free DL]`, `(Official Audio)`, hashtags, emoji) in the title |
| `--soundcloud-filename TEMPLATE` | `{track} - {title}` | SoundCloud file name: `{track}` (`{track:02}` sets the padding), `{title}` (exactly once), `{artist}`, `{album}`, `{album_artist}`; no `/` or `\`. The usual file name rules and length limit apply |
| `--debug-log [PATH]` | off | Write a detailed debug log of the run (see [Debug log](#debug-log)). Without `PATH`, or with a folder, the file is `.music-loader-logs/debug-YYYYMMDD-HHMMSS.jsonl` in the Music folder (or in that folder) |
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
│   ├── <album artist>/<album>/<NN> - <title>.mp3
│   ├── <artist>/<song> - Single/01 - <song>.mp3
│   └── *.m3u8                                              # playlists
└── .music-loader-logs/
    ├── failures-YYYYMMDD-HHMMSS.log
    ├── unavailable-YYYYMMDD-HHMMSS.log     # DRM / preview-only tracks that were skipped
    └── debug-YYYYMMDD-HHMMSS.jsonl         # only with --debug-log
```

Hidden index files (`.sc_index.json`, `.spotify_index.json`, `.music-loader-artists.json`, lyrics
attempt files) make re-runs fast and detect moved files. Naming, tagging and lyrics rules:
[How it works](../explanation/how-it-works.md).

## Debug log

`--debug-log` records why every track ended up the way it did, for analysing title parsing and
lyrics matching or for attaching to an issue. It is off by default; without it nothing extra is
written. The file is JSON lines, one event per line, written as the run goes (readable mid-run):

```json
{"time": "2026-10-15 08:00:00.123", "section": "lyrics", "event": "candidate", "track": "01 - Song.mp3",
 "provider": "LRCLIB", "artist": "Other", "title": "Song", "verdict": "rejected", "reason": "artist mismatch"}
```

| `section` | Events |
|---|---|
| `run` | `start` (version, options), every Activity line (`log`, `error`), `failure` with its category, `unavailable` with its reason, `finished` with the counters |
| `links` | every link with its service and kind, rejected entries |
| `soundcloud` | `resolved` (tracks of a link with id, title, uploader, length, album), `metadata` (raw yt-dlp fields: title, uploader, artist, description, length, publisher metadata, ...), `title_parsed` (raw title → artists, featured, producers, title, version markers; producers named in the description; the formatting settings), `final_meta` (tags, `<album artist>/<album>` folder, file name, path), `layout_migration` (tracks moved from the old `Artist - Album` folders), `retry`, `unavailable`, `fallback`, `track_result` |
| `spotify` | `resolved` (what spotDL returned per song: name, artists, album, ISRC, length, ...), `spotdl_output` (every line spotDL printed, including the match it downloaded), `track_result` (final path, title, artists, album artist) |
| `lyrics` | `request` (artists, title, album, length), every provider `query` with its parameters and HTTP status, every `candidate` with `verdict` (`synced`, `plain`, `instrumental`, `rejected`) and `reason` (artist mismatch, title mismatch, version mismatch, length difference, ...), `provider_skipped` / `provider_error`, `result` |

Secrets are masked before anything is written: private SoundCloud tokens (`/s-…`, `secret_token`),
client ids, user tokens, passwords and `Authorization` values; the Spotify credentials are never
logged. Long texts (descriptions) are cut at 2000 characters.

Put links before the flag or use `--debug-log=PATH`: a link right after `--debug-log` is still
treated as a link, not as the path.
