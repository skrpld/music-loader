# CLI

Command-line interface of the `music-loader` package (`music-loader` command, or
`python -m music_loader`). The server subcommand is described in [Server](server.md).

```text
music-loader [OPTIONS] [SOURCE ...]
music-loader serve -o <MUSIC_FOLDER> [OPTIONS]
music-loader artists -o <MUSIC_FOLDER> [--json] [ACTION ACCOUNT [NAME]]
music-loader check -o <MUSIC_FOLDER> [--fix] [--rules LIST] [--skip-rules LIST] [--offline]
```

`SOURCE` is a Spotify or SoundCloud link, or a path to a `.txt` file with one link per line
(`#` starts a comment). Without `SOURCE` or `-o`, the tool asks for them interactively.

## Options

| Option | Default | Description |
|---|---|---|
| `-o, --output` | asked | Target Music folder |
| `--no-lyrics` | off | Do not look up lyrics |
| `--lyrics-loose` | off | Loose lyrics matching: fuzzy artist/title match and up to 10 s length difference (plain text only when the timing may not fit). Default is strict: exact artist, title, version and length (±2 s) |
| `--lyrics-providers LIST` | `lrclib,musixmatch,genius` | Lyrics providers to ask, comma-separated, in this order |
| `--lyrics-synced-only` | off | Save synced lyrics only; by default plain text is embedded when no synced lyrics match |
| `--lyrics-length-tolerance SECONDS` | `10` | Loose mode: the largest length difference (2–60 s) for plain text. Synced lyrics always need ±2 s |
| `--lyrics-original-for-versions` | off | Strict mode: a sped-up, slowed, 8D or bass-boosted upload may take the original's plain lyrics, never its timestamps (loose mode always allows it) |
| `--lyrics-retry-days DAYS` | `7` | Search a track whose lyrics were not found again after this many days; `0` = on every run. A rewritten track (`--existing`) is always searched again |
| `--lyrics-workers N` | `2` | Parallel lyrics lookups |
| `--existing MODE` | `keep` | Tracks already in the library: `keep` leaves them as they are and only binds their id to the file; `outdated` rewrites the ones written in an older format or with an artist that changed since; `all` rewrites every one. A rewrite applies the current rules: tags, folder and file name, broken/preview files, lyrics. With `keep` the summary counts the outdated tracks of the run. See [Existing tracks](../explanation/how-it-works.md#existing-tracks-keep-update-outdated-update-all) |
| `--recheck` | off | The same as `--existing all` (cannot be combined with another `--existing`) |
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

## Artists

`music-loader artists` lists the SoundCloud accounts of a library and the artist name their tracks
get, and changes that decision (rules: [Accounts and reuploads](../explanation/how-it-works.md#accounts-and-reuploads)).

```text
music-loader artists -o ~/Music                         # the list (--json for JSON)
music-loader artists -o ~/Music accept @aquagwap        # take the suggested name
music-loader artists -o ~/Music set ak2angel AQUAKEY    # write the account as AQUAKEY
music-loader artists -o ~/Music reuploader slmndk       # each track gets the original's artist
```

| Action | Effect |
|---|---|
| `set ACCOUNT NAME` | Tracks of the account without an artist in the title, its albums and its folder use `NAME` |
| `keep ACCOUNT` | Keep the account's own name |
| `reuploader ACCOUNT` | A reupload account: each track gets the artist of its original upload |
| `accept ACCOUNT` | Take the suggested name |
| `reject ACCOUNT` | Drop the suggested name; it is never suggested again |
| `unlock ACCOUNT` | Give the account back to the automatic check of the next run |

`ACCOUNT` is the account's id, its profile link (`soundcloud.com/ak2angel`), its permalink or its
handle. Every action but `reject` and `unlock` locks the account: no run changes it again. The list
shows each account's state (confirmed, suggestion, reupload account, not checked, account name), the
name its tracks get, where that came from and when it was checked; suggestions come first. A change
applies to files downloaded from then on; `--existing outdated` applies it to the files already there.

## Library check

`music-loader check` checks every file of a library against a set of rules and lists what is wrong,
grouped by problem (rules and what `--fix` does: [Library check](../explanation/how-it-works.md#library-check)).
Without `--fix` nothing is changed.

```text
music-loader check -o ~/Music                          # every rule
music-loader check -o ~/Music --offline                # without asking SoundCloud and Spotify
music-loader check -o ~/Music --rules cover,lyrics     # only these rules
music-loader check -o ~/Music --fix                    # fix what can be fixed, then check again
```

| Option | Default | Description |
|---|---|---|
| `-o, --output` | asked | The Music folder |
| `--fix` | off | Fix what can be fixed automatically, then check again. Never deletes an audio file; duplicates and removed tracks are only reported |
| `--rules LIST` | all | Only these rules, comma-separated: `id`, `cover`, `lyrics`, `artist`, `format`, `duplicates`, `available` |
| `--skip-rules LIST` | none | Leave these rules out |
| `--offline` | off | No network: leaves out `available` |

The lyrics, SoundCloud worker, SoundCloud formatting and Spotify options of a download are accepted
too and apply to `--fix` (how rewritten tracks are written, how lyrics are searched); `--no-lyrics`
also makes missing lyrics not fixable.

The report is a table of the problem groups, then the first 10 tracks of each group ("Artist —
Title") and "and N more". Its last line says what `--fix` can fix ("--fix can fix 52 of 58
problem(s)"), or, after a fix, what it fixed and what is left. The full list, with paths, links and
reasons, is written to `.music-loader-logs/check-YYYYMMDD-HHMMSS.log` in the Music folder. Exit
code `0` when the check ran (whether or not it found problems), `130` when interrupted.

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
| `accounts` | every account decision (`evidence`, `check` / `recheck` with their matches, `verdict`), each Spotify / Deezer / SoundCloud `search` and `service_unavailable`, `original` (the original upload found for a reupload, or why none) |
| `lyrics` | `request` (artists, title, album, length, the searches it makes), every provider `query` with its parameters and HTTP status, every `candidate` with `verdict` (`synced`, `plain`, `instrumental`, `rejected`) and `reason` (artist mismatch, title mismatch, version mismatch, length difference, ...), `provider_skipped` (switched off or paused, with the reason) / `provider_error`, `result` |

Secrets are masked before anything is written: private SoundCloud tokens (`/s-…`, `secret_token`),
client ids, user tokens, passwords and `Authorization` values; the Spotify credentials are never
logged. Long texts (descriptions) are cut at 2000 characters.

Put links before the flag or use `--debug-log=PATH`: a link right after `--debug-log` is still
treated as a link, not as the path.
