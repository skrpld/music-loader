# Music Loader

Downloads music with full metadata from Spotify (via spotDL) and SoundCloud
(via yt-dlp), looks up **verified** lyrics, and prepares the library for
Symfonium. Progress, speed and statistics are shown in a live terminal
interface (based on `rich`), or - in [server mode](#server-mode-and-android-app) -
in the Android app.

## Quick start

**Requirements:** Python 3.10+, ffmpeg. spotDL, yt-dlp (with `curl-cffi`),
mutagen and beautifulsoup4 are installed as dependencies.

```bash
pip install -e .
```

Install ffmpeg:

```bash
# Ubuntu/Debian
sudo apt install ffmpeg
# macOS
brew install ffmpeg
```

**Usage:**

```bash
# Single link
music-loader "https://open.spotify.com/album/..." -o /path/to/Music

# From file (one link per line, "#" starts a comment)
music-loader links.txt -o /path/to/Music

# Several sources at once
music-loader links.txt "https://soundcloud.com/..." -o /path/to/Music

# Interactive mode
music-loader
```

Without installing the package:

```bash
python3 -m music_loader links.txt -o ./Music
```

## Options

| Option | Default | Meaning |
|---|---|---|
| `-o, --output` | asked | Target Music folder |
| `--no-lyrics` | off | Do not look up lyrics |
| `--lyrics-loose` | off | Loose lyrics matching (see [Lyrics](#lyrics)); default is strict |
| `--lyrics-workers N` | 2 | Parallel lyrics lookups |
| `--recheck` | off | Check already downloaded tracks against the current rules again |
| `--soundcloud-reposts` | off | Profile link: also download the profile's reposts |
| `--soundcloud-likes` | off | Profile link: also download the profile's likes |
| `--soundcloud-download-workers N` | 2 | Parallel SoundCloud downloads |
| `--soundcloud-workers N` | 4 | Parallel SoundCloud conversion/tagging workers |
| `--spotify-threads N` | 4 | Parallel spotdl downloads |
| `--spotify-client-id`, `--spotify-client-secret` | none | Optional own Spotify app credentials |

Only real Spotify (`open.spotify.com/...`, `spotify:...`) and SoundCloud
(`soundcloud.com`, `m.`, `on.soundcloud.com`, `snd.sc`, API) links are
accepted; anything else is reported and skipped.

## Project layout

```
music-loader/
├── pyproject.toml            # dependencies and entry point
├── README.md
├── android/                  # Android client (Kotlin, Jetpack Compose, Material 3 Expressive)
├── .github/workflows/        # android.yml: builds the APK
└── music_loader/
    ├── __main__.py           # python -m music_loader
    ├── cli.py                # command-line arguments, main loop
    ├── config.py             # paths and constants
    ├── deps.py               # ffmpeg / spotdl / yt-dlp checks
    ├── links.py              # link validation and classification
    ├── process.py            # subprocess execution with streamed output parsing
    ├── spotify.py            # Spotify: resolve, check, download, verify
    ├── spotify_index.py      # Spotify URL -> files (finds moved/duplicate files)
    ├── soundcloud.py         # SoundCloud: discovery, parallel pipeline, recheck
    ├── soundcloud_meta.py    # SoundCloud tags, album context, folders, covers
    ├── soundcloud_index.py   # SoundCloud id -> file index, archive
    ├── text_utils.py         # title parsing, normalization, version markers
    ├── artists.py            # canonical artist spelling across the library
    ├── lyrics.py             # verified lyrics (LRCLIB, Musixmatch, Genius)
    ├── tags.py               # ID3 helpers (mutagen)
    ├── paths.py              # safe file names, moving files with their .lrc
    ├── net.py                # small HTTP helper
    ├── playlist.py           # .m3u8 playlists for SoundCloud
    ├── runlog.py             # persistent per-run failure log
    ├── ui.py                 # live dashboard
    ├── server.py             # server mode: HTTP API, job queue, event stream
    ├── worker.py             # runs one server job in its own process
    └── events.py             # progress as JSON events (server mode)
```

## Server mode and Android app

`music-loader serve` runs music-loader on the machine that holds the library;
the Android app in [`android/`](android) queues links and follows the
downloads from the phone.

```bash
music-loader serve -o /path/to/Music
```

At startup the server prints its address and an access token:

```
Server address: http://192.168.1.10:8765
Token: 8Qm3VYk1x5rW0bH2tN6pL9sA4cE7uJ0d
```

Enter both in the app (Settings → Server → Test → Save).

| Option | Default | Meaning |
|---|---|---|
| `-o, --output` | required | Target Music folder |
| `--host` | `0.0.0.0` | Address to listen on; `127.0.0.1` behind a reverse proxy |
| `--port` | 8765 | Port |
| `--token` | stored token | Access token; prefer `MUSIC_LOADER_TOKEN` |
| `--new-token` | off | Generate a new stored token |
| `--spotify-threads`, `--soundcloud-download-workers`, `--soundcloud-workers`, `--lyrics-workers` | as in the CLI | Parallel work for every job |

- **Token**: `--token`, the `MUSIC_LOADER_TOKEN` environment variable, or a
  token generated on the first start and kept in
  `~/.config/music-loader/server-token` (`%APPDATA%\music-loader` on
  Windows). Every request needs it; a wrong token is answered with 401.
- **Jobs** run one after another, each in its own worker process. Options per
  job: lyrics mode (strict / loose / off), `--recheck`, SoundCloud reposts and
  likes. Cancelling a job goes through the same cleanup as Ctrl+C: child
  processes stop and unfinished files are removed. Stopping the server
  (Ctrl+C, SIGTERM) cancels the running job the same way.
- **Transport**: plain HTTP. Use it in the home network or over a VPN
  (Tailscale, WireGuard), or put an HTTPS reverse proxy in front of it
  (`--host 127.0.0.1`; the event stream needs response buffering off - the
  server sends `X-Accel-Buffering: no` for nginx).
- Spotify credentials are read from `SPOTIFY_CLIENT_ID` /
  `SPOTIFY_CLIENT_SECRET` as in the CLI.

API (JSON, `Authorization: Bearer <token>`):

| Request | Meaning |
|---|---|
| `GET /api/v1/info` | version, library folder, queue state |
| `GET /api/v1/jobs` | all jobs, newest first |
| `POST /api/v1/jobs` | queue links: `{"links": [...], "options": {"lyrics": "strict", "recheck": false, "soundcloud_reposts": false, "soundcloud_likes": false}}` |
| `GET /api/v1/jobs/<id>` | one job with its log, errors and active downloads |
| `POST /api/v1/jobs/<id>/cancel` | cancel a queued or running job |
| `DELETE /api/v1/jobs/<id>` | remove a finished job from the list |
| `GET /api/v1/events` | Server-Sent Events: a `state` event after every change |

### Android app

Kotlin and Jetpack Compose with Material 3 Expressive: dynamic colors from the
wallpaper (Android 12+), light / dark / system theme, expressive motion,
wavy progress indicators, and an adaptive layout - bottom bar on phones,
navigation rail and list + details side by side on tablets and foldables.
English and Russian. Android 8.0 or later.

- **Download**: paste links (or share them from the Spotify / SoundCloud app
  into Music Loader), choose the lyrics mode and options, add to the queue.
- **Jobs**: the running job live - links and tracks progress, active
  downloads with speed and ETA, counters - plus the queue and finished jobs
  with their errors and activity log; cancel and remove.
- **Settings**: server address and token with a connection test, theme,
  dynamic colors.

#### Getting the APK

[`.github/workflows/android.yml`](.github/workflows/android.yml) builds the
app on every push that touches `android/`; the APK is attached to the run as
an artifact (Actions → run → Artifacts). Publishing a GitHub release also
builds the APK and attaches it to the release; the tag sets the app version
(`v1.2.0` or `android-v1.2.0` → 1.2.0, otherwise `appVersionName` in
`android/gradle.properties`).

Without signing secrets the workflow builds the debug APK. A debug key is
generated on every run, so each build has a different signature and Android
refuses to update over the previous one. For installable updates, sign the
release build with your own key:

```bash
keytool -genkeypair -v -keystore music-loader.jks -alias music-loader \
  -keyalg RSA -keysize 4096 -validity 10000
base64 -w0 music-loader.jks   # value of ANDROID_KEYSTORE_BASE64
```

Repository secrets (Settings → Secrets and variables → Actions):
`ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`,
`ANDROID_KEY_PASSWORD`. Keep the keystore safe: a new key means uninstalling
the app before the next update.

#### Building locally

JDK 17+ and the Android SDK (or Android Studio):

```bash
cd android
./gradlew assembleDebug    # app/build/outputs/apk/debug/app-debug.apk
```

## Library layout

Both services use the same layout: one folder per album, singles included.

```
Music/
├── <album artist> - <album>/<NN> - <title>.mp3          # Spotify
├── SoundCloud/
│   ├── <album artist> - <album>/<NN> - <title>.mp3      # SoundCloud album/EP track
│   ├── <artist> - <song> - Single/01 - <song>.mp3        # standalone SoundCloud upload
│   ├── SoundCloud_New.m3u8          (all SoundCloud tracks)
│   ├── <uploader> - <playlist>.m3u8 (one per playlist link, original order)
│   ├── .sc_index.json               (SoundCloud id -> file)
│   └── .sc_lyrics_attempts.json     (lyrics retry cooldown)
├── .music-loader-artists.json       (canonical artist spelling)
├── .spotify_index.json              (Spotify URL -> file cache)
├── .spotify_lyrics_attempts.json
└── .music-loader-logs/failures-YYYYMMDD-HHMMSS.log
```

The album artist decides the folder - the artist the album belongs to, not
the first artist of a track - so an album whose tracks start with different
artists stays together. Lyrics (`.lrc`) sit next to the audio file.

Artist photos are never downloaded: only album/track artwork is embedded.

## Metadata

### Spotify

Tags come from Spotify through spotDL: artists, album artist, album, track
and disc numbers, date, genre, ISRC, album cover. Several artists are stored
as `A/B` (ID3v2.3).

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

Rules:

- **Artist**: SoundCloud's own artist field (set for label releases) →
  `Artist - Song` from the title → the uploader. Featured artists follow the
  main artists, as on Spotify. A right-hand side that is only a suffix
  ("Outro", "Remix", "Pt. 2") is not split off. Ambiguous credits - a bare
  `W` ("Pitstop W Aquakey & Platov"), guests appended with `+`/`x` to a title
  without an artist part ("35 hp + хестон + platov") - count as featured
  artists only when every name is already known (from Spotify, an account
  name or another title); otherwise the title is kept as written.
- **Title**: promotional noise (`[Free DL]`, `(Official Video)`,
  `*music video in description*`), producer credits and a leading `09.` are
  removed; `(feat. …)` is normalized; version markers (`(Sped Up)`, `hexd`,
  `(Remix)`) stay.
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
  of being filed as complete. Go+-only tracks are reported as such.
- SoundCloud: a partial file left by an interrupted run is never reused.
- Spotify: spotDL converts straight into the final file and tags it last; a
  file without spotDL's URL tag is an interrupted download and is downloaded
  again (it used to be "skipped" forever). Interrupting a run removes such
  files right away.
- Spotify: every song is checked on disk after the run, so the counters show
  what really happened (spotdl exits with 0 even when tracks failed).

`--recheck` applies the current rules to tracks that are already in the
library - useful after updating music-loader:

- SoundCloud: metadata is fetched again; tags, folder and file name are
  rewritten (files from older versions move into album folders, with their
  `.lrc`); a file whose length does not match is downloaded again.
- Spotify: tags are refreshed from Spotify (`--overwrite metadata`) and
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

Sets inside a listing (an album in the likes) are expanded - previously only
their first track was kept.

SoundCloud allows roughly 600 API requests per 10 minutes. When it answers
"429 Too Many Requests", all downloads pause (30 s, doubling up to 5 min) and
the track is retried.

Profile, likes and reposts listings need yt-dlp's browser impersonation
(`curl-cffi`), otherwise SoundCloud answers with HTTP 403.

## Spotify

A link is processed in two spotdl runs: `spotdl save` resolves it (an album,
a playlist, a whole discography) and reports where every song goes;
`spotdl download` then downloads that saved list without a second round of
Spotify API calls. Before the download the library is checked (unfinished
files, files under an older layout), after it every song is verified.

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

## Parallel work

- SoundCloud: `--soundcloud-download-workers` downloads run in parallel; a
  pool of `--soundcloud-workers` converts, tags and validates; lyrics run in
  their own pool. The hand-over is bounded, so only a few unconverted
  downloads wait on disk. File names are reserved while a worker writes, so
  two tracks with the same name never overwrite each other.
- Spotify: spotdl downloads `--spotify-threads` songs at a time; lyrics run
  afterwards in `--lyrics-workers` threads.
- Ctrl+C stops every child process (yt-dlp, spotdl) and worker, removes
  partial files and keeps the index consistent.

## Security notes

- Links are validated; other sites and option-like lines are refused, and
  URLs reach yt-dlp after `--`.
- Track ids are validated before they are used in file names.
- Tools are run from the Python environment music-loader is installed in,
  not from whatever `yt-dlp` comes first in `PATH`.
- Covers are fetched over http(s) only, with a size limit.
- Private SoundCloud links: the secret token is not written into tags,
  playlists or the failure log.
- All child output is decoded as UTF-8 (a Cyrillic title no longer turns into
  garbage on Windows), and remote text is escaped before it reaches the
  terminal UI.
- Server mode: every request needs the token (compared in constant time);
  request bodies are size-limited, links are validated as on the command
  line, and private SoundCloud tokens stay on the server - API responses and
  the event stream carry redacted links only.

## Failure log

Every failure is written to `.music-loader-logs/failures-<timestamp>.log` as
it happens, so a long batch can be reviewed afterwards.
