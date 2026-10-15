# How it works

What Music Loader does with a link, how it names and tags files, how lyrics
are verified, and which checks keep the library consistent. For installation
and options see the [CLI reference](../reference/cli.md).

## Library layout

Both services keep one folder per album, singles included. Spotify albums
sit directly in the music folder; SoundCloud groups them by artist first.

```
Music/
├── <album artist> - <album>/<NN> - <title>.mp3          # Spotify
├── SoundCloud/
│   ├── <album artist>/
│   │   ├── <album>/<NN> - <title>.mp3                    # SoundCloud album/EP track
│   │   └── <song> - Single/01 - <song>.mp3               # standalone SoundCloud upload
│   ├── SoundCloud_New.m3u8          (all SoundCloud tracks)
│   ├── <uploader> - <playlist>.m3u8 (one per playlist link, original order)
│   ├── .sc_index.json               (SoundCloud id -> file)
│   └── .sc_lyrics_attempts.json     (lyrics retry cooldown)
├── .music-loader-artists.json       (canonical artist spelling)
├── .spotify_index.json              (Spotify URL -> file cache)
├── .spotify_lyrics_attempts.json
└── .music-loader-logs/
    ├── failures-YYYYMMDD-HHMMSS.log     (what went wrong)
    ├── unavailable-YYYYMMDD-HHMMSS.log  (tracks SoundCloud does not give out)
    └── debug-YYYYMMDD-HHMMSS.jsonl      (optional debug log, --debug-log)
```

The album artist decides the folder - the artist the album belongs to, not
the first artist of a track - so an album whose tracks start with different
artists stays together. Lyrics (`.lrc`) sit next to the audio file.

On SoundCloud a single gets its own folder under its artist, like an album
(`<artist>/<song> - Single/`), so a single that later appears on an album
does not share a folder with it. A compilation goes under `Various Artists/`.

### Moving a SoundCloud library to the artist folders

Before 2.7.0 SoundCloud used Spotify's layout, `SoundCloud/<album artist> -
<album>/`. Every SoundCloud run first moves such a library to the current
layout ([`soundcloud_layout.py`](../../cli/music_loader/soundcloud_layout.py)):

- A track is in the old layout when it sits directly in a first-level folder
  (the current layout never puts audio there). It moves when music-loader
  wrote it (SoundCloud id tag, or an entry in `.sc_index.json`) and it has
  album and album artist tags; its new folder is built from those tags, its
  file name is kept. Other files are left where they are.
- The `.lrc` moves with the track, `.sc_index.json` and the `.m3u8`
  playlists follow it (paths stay relative), and old folders that are empty
  afterwards are removed. A file is never overwritten: a name already taken
  gets the SoundCloud id appended.
- The planned moves are written to `.sc_layout_migration.json` before the
  first file is touched, and the file is deleted when everything is done. An
  interrupted migration is finished on the next run; on a library in the
  current layout the step only lists the first-level folders and moves
  nothing.

### Names

[`naming.py`](../../cli/music_loader/naming.py) builds every folder and file
name, for Spotify and SoundCloud alike (spotDL writes to its own path first
and the file is renamed right after the download):

- Track numbers are zero-padded (`01`, `007` for 100+ tracks). An album with
  more than one disc uses `<disc>-<NN>` (`2-01 - Title.mp3`); a single-disc
  album has no disc part.
- Names are NFC; compatibility forms (full-width letters, ligatures) are
  folded, zero-width characters, emoji and decorations (`✦ ★ ☆ 🔥`) are
  removed, quotes and dashes become plain ones. Cyrillic, CJK and accents stay.
- Characters not allowed on FAT/Windows are replaced the same way for both
  sources: `/ \ |` become `-`, `:` becomes ` - `, `? * < >` are dropped, `"`
  becomes `'` (no look-alike characters).
- Spotify version suffixes move into brackets (`Song - Remastered 2011` ->
  `Song (Remastered 2011)`), and `ft.` / `featuring` become `feat.`.
- Long names are cut at a word boundary (180 bytes per component, extension
  included), never inside a bracket. A name that is already taken by another
  track gets `[<track id>]` appended.
- An album whose tracks have no common artist (4+ tracks, nobody on more than
  40% of them), a SoundCloud *compilation* set or a Spotify "Various Artists"
  album is filed under `Various Artists`.
- SoundCloud titles lose hashtags (`#phonk`; one that starts the name, as in
  the album `#J20 XTREME SPORT`, stays), emoji, `OUT NOW`, `BUY = FREE DL`,
  trailing promo words after the title (`mv in desc`, `ALBUM OTW`,
  `new mixtape otw`, `mv on yt`), a trailing `| Label`, `(Explicit)` and
  whole-bracket years (`[2019]`). Version markers (`Sped Up`, `VIP`, `Live`,
  ...) stay unless `--soundcloud-drop-versions` is set (see
  [Formatting settings](#formatting-settings)).
- SoundCloud file names follow `--soundcloud-filename` (default
  `{track} - {title}`); the same character and length rules apply, and only
  the title is shortened when the name is too long.

Artist photos are never downloaded: only album/track artwork is embedded.

## Metadata

### Spotify

Tags come from Spotify through spotDL: artists, album artist, album, track
and disc numbers, date, genre, ISRC, album cover. spotDL writes ID3v2.3 with
`A/B`; after each download music-loader rewrites the artist frames, see
"Several artists" below.

### Several artists

All files are ID3v2.4. A track with several artists has two frames, the same
way MusicBrainz Picard writes them:

- `TPE1` - one display string, `A, B` (main artists, then featured ones, in
  the canonical spelling of the artist registry). Players without
  multi-artist support show it as it is.
- `TXXX:ARTISTS` - the real list (`A`, `B`). Players that split artists use it.

The album artist (`TPE2`) stays a single name. Reading prefers `ARTISTS`;
without it, ID3v2.4 values are split on `\0` and `, `, and old ID3v2.3 files
on `/` - never inside a name the artist registry knows (`AC/DC`).

### SoundCloud

SoundCloud has no structured credits for most uploads; they are written into
the title. Examples from the DOOM RUSHAZ collective and how they are tagged:

| Title on SoundCloud (uploader) | Artist tag | Title tag |
|---|---|---|
| `TARABANDZ + platov + WHITENER - under heaven [prod. haru matsui]` (DOOM RUSHAZ) | TARABANDZ/platov/WHITENER | under heaven |
| `WHITENER, platov - heroin chic (feat aquakey) hexd` | WHITENER/platov/aquakey | heroin chic (feat. aquakey) hexd |
| `sip doomstation ft. benjamingotbenz, haru matsui, sg, platov` (DOOM RUSHAZ) | DOOM RUSHAZ/benjamingotbenz/… | sip doomstation (feat. benjamingotbenz, haru matsui, sg, platov) |
| `haru matsui - godline (prod. hm9600) *music video in description*` | haru matsui | godline |
| `09. Pitstop W Aquakey & Platov` (benjamingotbenz) | benjamingotbenz/Aquakey/Platov | Pitstop (feat. Aquakey, Platov) |
| `Right Now! - Outro` (benjamingotbenz) | benjamingotbenz | Right Now! - Outro |

More shapes, taken from a debug log of real runs (the full table is in
`cli/tests/test_soundcloud_titles.py`):

| Title on SoundCloud (uploader) | Artist tag | Title tag | Producers |
|---|---|---|---|
| `best summer  (whyjuzo)` (`platov  (@platovoplomo)`) | platov | best summer (whyjuzo) | |
| `yung millionaire ((haru matsui + jack13))` | uploader | yung millionaire | haru matsui, jack13 |
| `thraxx interlude ((me ft. haru matsui))` | uploader/haru matsui | thraxx interlude (feat. haru matsui) | me |
| `1million ((cryst)) ALBUM OTW` | uploader | 1million | cryst |
| `platov, haru matsui - diesel (playasin)`, description `prod by sg, haru matsui, playasin` | platov/haru matsui | diesel | playasin |
| `Морозы (+elya14062001)` | uploader/elya14062001 | Морозы (feat. elya14062001) | |
| `Mudstock feat. CODE80 (mxstrakh)` | uploader/CODE80 | Mudstock (feat. CODE80) (mxstrakh) | |
| `TREAD-ANTHEM (feat. JUNKIE MANE x TRENCHB66F)` | uploader/JUNKIE MANE/TRENCHB66F | TREAD-ANTHEM (feat. JUNKIE MANE, TRENCHB66F) | |
| `CAST A SPELL (🎃` | uploader | CAST A SPELL | |

Rules:

- **Artist**: SoundCloud's own artist field (set for label releases) →
  `Artist - Song` from the title → the uploader. Featured artists follow the
  main artists, as on Spotify. A right-hand side that is only a suffix
  ("Outro", "Remix", "Pt. 2") is not split off. Ambiguous credits - a bare
  `W` ("Pitstop W Aquakey & Platov"), guests appended with `+`/`x` to a title
  without an artist part ("35 hp + хестон + platov") - count as featured
  artists only when every name is already known (from Spotify, an account
  name or another title); otherwise the title is kept as written.
- **Account names**: a SoundCloud display name that carries the handle
  (`platov  (@platovoplomo)`) is used without it, as artist and album
  artist, so an artist's albums and singles share one folder.
- **Title**: promotional noise (`[Free DL]`, `(Official Video)`,
  `*music video in description*`, `mv in desc`), producer credits and a
  leading `09.` are removed; `(feat. …)`, `(+ X)` and `feat. X (…)` become
  one `(feat. …)` part; `x` separates guests inside it; extra spaces inside
  brackets and a bracket left open at the end go. Version markers
  (`(Sped Up)`, `hexd`, `(Remix)`) stay.
- **Producers**: `(prod. X)`, `[Prod by X]`, `(p. X)`, a trailing `prod X`,
  a double bracket `((X + Y))`, and a bracket that holds only names the
  description credits on a `prod by …` line (`diesel (playasin)`). A plain
  `(name)` without such a hint stays in the title: `(Skit)` or `(ACID RAIN)`
  look the same.
- **Album**: the album/EP/single the track belongs to - with its owner as
  album artist and the position as track number. A track from a link that
  does not say it (a single track, a playlist, likes, reposts) is looked up
  on SoundCloud. Everything else is its own single, `<song> - Single`.
  A regular playlist never becomes an album.
- **Date, genre, cover**: release (or upload) date, genre, the track's
  artwork or else the album's - never the uploader's avatar. Covers are
  downscaled to 1200 px.
- **Canonical spelling**: one artist is written one way everywhere
  (`BENJAMINGOTBENZ` → `benjamingotbenz`). Spotify spellings win, then
  SoundCloud account names, then names from titles; decorations such as
  `✦ platov ✦` are dropped.
- The SoundCloud id is stored in the file (`TXXX:SOUNDCLOUD_ID`), and the page
  URL without a private link's secret token.

### Formatting settings

How SoundCloud tracks are written can be changed (command line, server job
options, the app's settings). The defaults are the behaviour before these
settings existed.

| Setting | Command line | Job option | Default |
|---|---|---|---|
| Featured artists: title and artist tag / artist tag only / title only | `--soundcloud-feat both\|artist\|title` | `soundcloud_feat` | `both` |
| `(prod. X)` in the title | `--soundcloud-keep-producers` | `soundcloud_keep_producers` | off |
| Version markers (Remix, Sped Up, Slowed, Live…) in the title | `--soundcloud-drop-versions` | `soundcloud_keep_versions` | kept |
| Strip promo noise and emoji | `--soundcloud-keep-promo` | `soundcloud_strip_promo` | on |
| File name template | `--soundcloud-filename` | `soundcloud_filename` | `{track} - {title}` |

The settings change what is written - the title tag, the artist tag, the
single's album name and the file name. Lyrics and the YouTube Music fallback
still compare the full parsed title, so a sped-up upload written without
`(Sped Up)` is not matched against the original's lyrics. Unbracketed version
words (`WINDRIDER REMIX`) cannot be told from the title and stay.

The file name template takes `{track}` (the usual `01`, `001` for 100+
tracks; `{track:02}` sets the padding), `{title}` (exactly once), `{artist}`
(main artists), `{album}` and `{album_artist}`. No `/` or `\`: the folder
layout is fixed.

Settings apply to new downloads. `--recheck` (or *Recheck* in the app)
brings tracks already in the library in line - tags, folder and file name,
with `.sc_index.json`, the `.lrc` files and the playlists following the moved
files.

## Lyrics

Lyrics are looked up by default in **strict** mode; nothing is better than
another song's lyrics.

Strict (default) - a candidate from LRCLIB, Musixmatch or Genius is accepted
only when:

- its artist is one of the track's artists;
- its title is the same after normalization (case, punctuation, feat parts,
  promo noise and remaster notes do not count) **and** it has the same
  version markers - a sped-up, slowed, remixed or live upload never gets the
  original's lyrics;
- for synced lyrics, its length is within **2 s** of the file - otherwise the
  timestamps belong to a different cut;
- Genius has no length, so its (plain) text needs an exact artist + title
  match.

Loose (`--lyrics-loose`): fuzzy artist and title similarity, up to 10 s
length difference; versions may differ, but then - or whenever the length
differs by more than 2 s - only plain text is saved, never misaligned
timestamps.

Instrumentals, DJ sets, mixes and beats are skipped. Synced lyrics are saved
as `<track>.lrc` and their text is embedded (USLT); plain lyrics are
embedded only. A track whose search found nothing is retried after 7 days.
A provider that keeps failing - or Musixmatch asking for a captcha - is
switched off for the rest of the run instead of stalling it.

spotDL's own lyrics (Genius at 55 % similarity plus an unverified `.lrc`
search) are disabled; Spotify tracks go through the same verified lookup.

## Checks and `--recheck`

Every new file is checked before it counts as downloaded:

- SoundCloud: the converted file's length must match SoundCloud's (±3 s /
  3 %). A 30-second Go+ preview or a cut-off download fails the track instead
  of being filed as complete.
- SoundCloud: a partial file left by an interrupted run is never reused.
- Spotify: spotDL converts straight into the final file and tags it last; a
  file without spotDL's URL tag is an interrupted download and is downloaded
  again instead of being skipped. Interrupting a run removes such files
  right away.
- Spotify: every song is checked on disk after the run, so the counters show
  what really happened (spotdl exits with 0 even when tracks failed).

`--recheck` applies the current rules to tracks that are already in the
library - useful after updating music-loader:

- SoundCloud: metadata is fetched again; tags, folder and file name are
  rewritten (files from older versions move into artist/album folders, with their
  `.lrc`; entries in the `.m3u8` playlists follow the moved files); a file whose length does not match is downloaded again.
- Spotify: tags are refreshed from Spotify (`--overwrite metadata`), files
  are renamed to the current naming scheme (with their `.lrc`), the
  artist frames are rewritten as `A, B` + `ARTISTS` (ID3v2.4) and
  duplicate copies of a track under other paths are removed.
- Lyrics are searched again with the current mode (existing ones are
  replaced; the 7-day cooldown is ignored).

Without `--recheck`, a Spotify track that exists under an older folder layout
is moved to its current path (with its `.lrc`) instead of being downloaded a
second time.

## SoundCloud links

| Link | Downloads |
|---|---|
| track | that track |
| set (`/sets/...`) | its tracks; album/EP/single sets also give album, album artist, track numbers |
| profile (`soundcloud.com/<user>`) | the artist's own albums and tracks; reposts with `--soundcloud-reposts`, likes with `--soundcloud-likes` |
| profile page (`/tracks`, `/albums`, `/sets`, `/likes`, `/reposts`) | exactly that page |

Sets inside a listing (an album in the likes) are expanded into all their
tracks.

SoundCloud allows roughly 600 API requests per 10 minutes. When it answers
"429 Too Many Requests", all downloads pause (30 s, doubling up to 5 min) and
the track is retried. From then on the run downloads one track at a time
instead of `--soundcloud-download-workers`, so the parallel workers do not hit
the limit again the moment the pause ends (a new server job starts at full
speed again). A track that still fails after its retries is a `rate_limited`
failure and can be retried later, see [Retrying](#retrying-failed-tracks).

Profile, likes and reposts listings need yt-dlp's browser impersonation
(`curl-cffi`), otherwise SoundCloud answers with HTTP 403.

## Spotify

Each link is resolved by its own `spotdl save` run (an album, a playlist, a
whole discography), which reports where every song goes. Once every link of
the job is resolved, one `spotdl download` downloads the saved songs of all
Spotify links together - each song once, without a second round of Spotify
API calls. Before the download the library is checked (unfinished files,
files under an older layout), after it every song is verified; a link counts
as failed when any of its songs failed.

spotDL looks a song up on YouTube Music first and on YouTube when YouTube
Music returns nothing usable (`--audio youtube-music youtube`): its answers
differ by region and network, and a song that plays there can come back empty.
Both lookups go through spotDL's matching (name, artists, duration).

Before the first spotdl run, Spotify is asked for a sign of life
(`open.spotify.com`, or `accounts.spotify.com` and `api.spotify.com` with own
credentials; any HTTP answer counts, two tries of 10 s). If it does not
answer, the job's Spotify links fail at once with the reason and a hint (internet, VPN,
proxy) instead of spotdl sitting silent for minutes: its client retries every
stalled request. While `spotdl save` is quiet for a minute, the log says that
large artists take a while and that Spotify may be unreachable.

spotDL's built-in client finds the query hashes of Spotify's web player by
downloading the player's script and every chunk of it - for every track,
album and release of an artist it looks at (about a minute per object on a
phone). The hashes only change with the player's build, so
[`spotify_cache.py`](../../cli/music_loader/spotify_cache.py) keeps them per
build in `<cache dir>/music-loader/spotify-hashes.json` (the last three
builds; the cache dir is `$XDG_CACHE_HOME` or `~/.cache`). The first run after
a new build of the player is still slow; the rest only ask the start page for
the current build. It patches spotapi's `BaseClient.part_hash` (a spotapi that
looks different is left alone), in the spotdl child process through
`spotify.py`'s bootstrap and in the app through `inprocess.py`.

Credentials are optional. spotDL 4.5+ uses its built-in client and needs
none. Own application credentials
(<https://developer.spotify.com/dashboard>) switch spotdl to the official
Web API:

```bash
export SPOTIFY_CLIENT_ID=...
export SPOTIFY_CLIENT_SECRET=...
music-loader "https://open.spotify.com/artist/..." -o /path/to/Music
```

`--spotify-client-id` / `--spotify-client-secret` work as well, but
command-line arguments are visible to every user of the machine in the
process list; the environment variables are the safer channel. The secret
is passed to spotdl through its environment, never on its command line.

When credentials are in use the activity log says "Using the official Spotify
API" (never the secret). In the Android app they are set under Settings →
Phone and kept encrypted with the Android Keystore; they are handed to the
embedded engine in memory and are not part of any job's options.

## Parallel work

A job runs in two phases:

1. **Resolve.** Every link is resolved before anything is downloaded, up to
   four at a time (at most two Spotify links: each is a whole `spotdl save`
   run; on Android spotDL runs one at a time anyway). SoundCloud listings
   share the rate-limit pause described above. A link that cannot be resolved
   fails on its own and never holds up the others. The link queue counts
   resolved links meanwhile ("Resolve" in the terminal, "Resolving links" in
   the app).
2. **Download.** The track total is now final: every track once, however many
   links of the job contain it (an album that is also in a playlist). The
   tracks of all links of a service go through one download run, so the
   parallel workers below work across links instead of one link at a time - a
   retry of twenty single failed tracks is one run with twenty tracks, not
   twenty runs. Downloaded, already had, failed and unavailable add up to the
   total. The services run one after the other, in the order their first
   link appears in the job; the link queue counts finished links.

- SoundCloud: `--soundcloud-download-workers` downloads run in parallel; a
  pool of `--soundcloud-workers` converts, tags and validates; lyrics run in
  their own pool. The hand-over is bounded, so only a few unconverted
  downloads wait on disk. File names are reserved while a worker writes, so
  two tracks with the same name never overwrite each other.
- Spotify: spotdl downloads `--spotify-threads` songs at a time; lyrics run
  afterwards in `--lyrics-workers` threads.
- Ctrl+C stops every child process (yt-dlp, spotdl) and worker, resolves in
  flight included, removes partial files and keeps the index consistent.

## Failure log

Every failure is written to `.music-loader-logs/failures-<timestamp>.log` as
it happens, so a long batch can be reviewed afterwards.

The optional debug log (`--debug-log`, server job option `debug_log`, a switch
in the Android app) goes further: it records how every link was resolved, the
raw SoundCloud metadata, how each title was parsed into credits, the final
tags, folder and file name, what spotDL matched, and every lyrics query and
candidate with the verdict and the reason it was rejected. Format and
contents: [CLI reference](../reference/cli.md#debug-log).

## Unavailable tracks (DRM, previews)

A track ends as one of: downloaded, already had, **failed**, or
**unavailable**. They are different things (`availability.py`):

- *unavailable* - the track cannot be downloaded and trying again will not
  help: it is only served **DRM-protected** (label and distributor uploads;
  yt-dlp's text "This video is DRM protected" says "video" for every kind of
  media), only as a 30-second **Go+ preview**, **blocked** (region, rights
  holder) or **removed**. It is skipped, counted separately and does **not**
  make the job fail: a run where only such tracks were skipped completes.
- *failed* - anything else that went wrong; it counts as a failure.
- *rate_limited* and *network* - transient causes. They count as failures
  like *failed* and are named apart so the cause is visible.

Every failure, whatever its category, is *retryable*
(`FailureCategory.retryable`): a retry picks all of them and never an
unavailable track.

Music Loader does not decrypt DRM and does not try to get around it. SoundCloud
serves such tracks only as `ctr-encrypted-hls` / `cbc-encrypted-hls` streams;
yt-dlp ignores those and the track is left out.

Detection costs no download: SoundCloud's track JSON (`media.transcodings`,
`policy`) is asked for 50 tracks per request after the link is resolved
(tracks already in the library are left out), so a 229-track album needs
about five requests of SoundCloud's budget of ~600 per 10 minutes. A track is
unavailable when `policy` is `BLOCK` or `SNIP`, when every stream is encrypted,
or when the only plain streams are previews; encrypted and plain streams
together are fine (the plain one is downloaded), and so is a track with an
offered original download. If that request fails, the same verdict is made
from yt-dlp's error after the attempt.

Each skipped track is explained in the activity list (`'<title>' by <artist>:
DRM-protected on SoundCloud, can't be downloaded, skipped`), counted in the
summary / the app's job screen and written - artist, title, reason, link - to
`.music-loader-logs/unavailable-<timestamp>.log`, so the tracks can be found
elsewhere. This file is separate from the failure log.

### `--soundcloud-fallback` (off by default)

With `--soundcloud-fallback` (a switch in the app) an unavailable track is
searched on **YouTube Music** instead of only being skipped. It is another
recording or master from another source, so a result is used only when all of
this agrees: the title (without "feat." and promo noise), the version
("Sped Up", "Remix", "Live" ... on both sides or neither), at least one main
artist, and the length within 3 s of SoundCloud's. The first five search results
are checked, one by one. Nothing matches - the track stays unavailable. The
finished file is checked against SoundCloud's length like any other, is tagged
and filed from SoundCloud's metadata, and carries `MUSIC_LOADER_SOURCE` and
`MUSIC_LOADER_SOURCE_URL` tags saying where the audio came from.

## Retrying failed tracks

Every failed track (or a whole link that could not be resolved) is reported
to the server with its category, URL and title (a `failure` event, see
`events.py`). The server keeps them on the job (at most 2000, one entry per
URL) and shows them grouped by category:

| Category | Retried? | Typical cause |
|---|---|---|
| `rate_limited` | yes | HTTP 429 / 403, "too many requests" |
| `network` | yes | timeout, connection reset, HTTP 5xx |
| `failed` | yes | conversion, tagging or an error nobody recognized |
| `unavailable` | never | DRM, preview, blocked, removed - listed apart, not a failure |

Re-running a link is cheap because tracks already in the library are skipped,
so a retry is simply the same link queued again. `POST /api/v1/jobs/<id>/retry`
creates a new job with the same options: scope `all` queues the original links,
scope `failed` queues the URLs of the failed tracks only (every category).

**Automatic retry** (job option `auto_retry`, off by default; a switch in the
app): when a job *completes* with failed tracks, the server queues them
again by itself after 15 minutes, then 30, then 60 - at most three attempts.
The job shows when the next attempt is due, and cancelling it stops the wait.
Cancelled and crashed jobs are not retried. The timer is kept with the job
history (`server-jobs.json` in the server's state folder, the app's private
storage on Android), so it survives a restart of the server or the app; the
retry runs once the server runs again (right away when its time has passed).
Jobs that were queued or running when the process died come back as
interrupted (`cancelled` / `failed`) and are never re-run by themselves.
Android has no wake-up of its own for the timer (no WorkManager): a killed
app retries the next time it starts.

## Code map

```
cli/music_loader/
├── __main__.py           # python -m music_loader
├── cli.py                # command-line arguments, job phases (resolve, then download)
├── config.py             # paths and constants
├── deps.py               # ffmpeg / spotdl / yt-dlp checks
├── links.py              # link validation and classification
├── process.py            # subprocess execution with streamed output parsing
├── spotify.py            # Spotify: resolve, check, download, verify
├── spotify_index.py      # Spotify URL -> files (finds moved/duplicate files)
├── spotify_cache.py      # remembers the web player's query hashes for spotDL's client
├── soundcloud.py         # SoundCloud: discovery, parallel pipeline, recheck
├── soundcloud_meta.py    # SoundCloud tags, album context, folders, covers
├── soundcloud_index.py   # SoundCloud id -> file index, archive
├── soundcloud_layout.py  # moves old "Artist - Album" SoundCloud folders to "Artist/Album"
├── text_utils.py         # title parsing, normalization, version markers
├── artists.py            # canonical artist spelling across the library
├── lyrics.py             # verified lyrics (LRCLIB, Musixmatch, Genius)
├── tags.py               # ID3 helpers (mutagen)
├── naming.py             # one naming scheme for folders and files (both sources)
├── paths.py              # moving files with their .lrc
├── net.py                # small HTTP helper
├── playlist.py           # .m3u8 playlists for SoundCloud
├── runlog.py             # persistent per-run failure log and unavailable-tracks list
├── debuglog.py           # optional debug log of a whole run (JSON lines, secrets masked)
├── availability.py       # failure categories, DRM / preview / blocked detection
├── fallback.py           # opt-in: verified YouTube Music match for unavailable tracks
├── ui.py                 # live dashboard
├── server.py             # server mode: HTTP API, job queue, event stream
├── worker.py             # runs one server job in its own process
├── events.py             # progress as JSON events (server mode), incl. failed tracks
├── inprocess.py          # spotdl / yt-dlp inside the interpreter (Android)
└── android.py            # entry points of the Android app's phone mode
```
