# Server

`music-loader serve` runs the download engine as an HTTP server for the
[Android app](../../android/README.md) or any HTTP client. Setup steps:
[Run a server for the Android app](../how-to/run-a-server.md).

```text
music-loader serve -o <MUSIC_FOLDER> [--host HOST] [--port PORT] [--token TOKEN] [--new-token]
                   [--state-dir DIR]
```

`<MUSIC_FOLDER>` is the library folder jobs download into.

## Options

| Option | Default | Meaning |
|---|---|---|
| `-o, --output` | required | Target Music folder |
| `--host` | `0.0.0.0` | Address to listen on; `127.0.0.1` behind a reverse proxy |
| `--port` | 8765 | Port |
| `--token` | stored token | Access token; prefer `MUSIC_LOADER_TOKEN` |
| `--new-token` | off | Generate a new stored token (the app has to be paired again) |
| `--state-dir` | token folder | Folder for the job history (`server-jobs.json`); give each server its own when several run on one machine |
| `--spotify-threads`, `--soundcloud-download-workers`, `--soundcloud-workers`, `--lyrics-workers` | as in the CLI | Parallel work for every job |

Spotify credentials are read from `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET`
as in the CLI. They are never part of a job's options or of an API answer.

## Token

Every request needs `Authorization: Bearer <token>`; a missing or wrong token
is answered with 401. The token comes from, in this order:

1. `--token` (visible in the process list - avoid on shared machines);
2. the `MUSIC_LOADER_TOKEN` environment variable;
3. a token generated on the first start and stored with mode `0600` in
   `~/.config/music-loader/server-token` (`$XDG_CONFIG_HOME` is honoured;
   `%APPDATA%\music-loader` on Windows).

An explicit token must be at least 16 characters of letters, digits and
`._~+/=-`. `--new-token` replaces the stored token.

## Jobs

- Jobs run one after another, each in its own worker process
  (`python -m music_loader.worker`).
- Options per job: lyrics mode (strict / loose / off), `--recheck`,
  SoundCloud reposts and likes, `soundcloud_fallback` (look for tracks
  SoundCloud does not give out on YouTube Music, off by default) and
  `auto_retry` (queue failed tracks again after a cooldown, off by
  default) and `debug_log` (write a detailed debug log of the job, off by
  default; see [Debug log](#debug-log)).
- SoundCloud formatting per job, the same settings as the CLI's
  (see [Formatting settings](../explanation/how-it-works.md#formatting-settings)):
  `soundcloud_feat` (`"both"` / `"artist"` / `"title"`, default `"both"`),
  `soundcloud_keep_producers` (default `false`), `soundcloud_keep_versions`
  (default `true`), `soundcloud_strip_promo` (default `true`) and
  `soundcloud_filename` (default `"{track} - {title}"`). A missing option
  means its default; a wrong value or a bad template is refused with 400 and
  the reason (`'soundcloud_filename': the file name template must contain
  {title} exactly once`).
- Cancelling a job goes through the same cleanup as Ctrl+C: child processes
  stop and unfinished files are removed. A worker that does not finish its
  cleanup within 60 s is killed.
- Stopping the server (Ctrl+C, SIGTERM) cancels the running job the same way.
- Limits: 100 queued jobs, 2000 links per job, 50 finished jobs kept,
  256 KiB request body.

### History across restarts

The job list is saved to `server-jobs.json` in the state folder
(`--state-dir`, by default the folder of the stored token:
`~/.config/music-loader` or `%APPDATA%\music-loader`) whenever a job is queued,
starts, ends, is cancelled or deleted, and its retry timer changes. The file
is replaced atomically and created with mode `0600`: it holds the full links,
private SoundCloud tokens included, because a restored job must still be
retryable. API responses stay redacted as before.

On start the server restores the saved jobs (at most the 50 newest finished
ones, plus any waiting for an automatic retry) with their status, counters,
log, errors, failures and options, so the job detail and `/retry` keep
working:

- Finished jobs (`completed`, `failed`, `cancelled`) come back as they were.
- A job that was `running` when the process died comes back as `failed`, one
  that was `queued` as `cancelled`, both with a message starting with
  `Interrupted:`. Neither is run again by itself; `/retry` queues it again. A
  clean stop (Ctrl+C, SIGTERM) still cancels the running job first.
- A pending automatic retry keeps its `retry_at`: it is queued when that time
  comes, or right after the start when it passed while the server was down.
- A file that cannot be read (corrupted, unknown version) is ignored with a
  warning and replaced on the next change; unusable entries are skipped.

## HTTP API

JSON over HTTP, `Authorization: Bearer <token>` on every request.

| Request | Meaning |
|---|---|
| `GET /api/v1/info` | version, API version, `features`, library folder, queue state |
| `GET /api/v1/jobs` | all jobs, newest first (summaries) |
| `POST /api/v1/jobs` | queue links (body below); `201` with the job and the rejected entries |
| `GET /api/v1/jobs/<id>` | one job with its log, errors and active downloads |
| `POST /api/v1/jobs/<id>/cancel` | cancel a queued or running job; on a finished job it cancels the pending automatic retry |
| `POST /api/v1/jobs/<id>/retry` | queue a finished job again (body below); `201` with the new job |
| `GET /api/v1/jobs/<id>/debug-log` | the job's debug log file (`application/x-ndjson`); `404` when the job has none |
| `DELETE /api/v1/jobs/<id>` | remove a finished job (`409` while it runs) |
| `GET /api/v1/events` | Server-Sent Events: a `state` event after every change, keep-alive comments every 15 s |

Queue request:

```json
{
  "links": ["https://open.spotify.com/album/...", "https://soundcloud.com/..."],
  "options": {"lyrics": "strict", "recheck": false, "soundcloud_reposts": false, "soundcloud_likes": false,
              "soundcloud_fallback": false, "auto_retry": false, "debug_log": false,
              "soundcloud_feat": "both", "soundcloud_keep_producers": false,
              "soundcloud_keep_versions": true, "soundcloud_strip_promo": true,
              "soundcloud_filename": "{track} - {title}"}
}
```

A job's `queue` is `{"completed", "total", "phase"}`: while `phase` is
`resolving`, `completed` counts the links resolved so far; then `phase` is
`downloading` and `completed` counts finished links. The track totals in
`stats` (`*_tracks_total`) are set once every link is resolved, before the
first download, and stay 0 until then.

In a job's `stats`, `soundcloud_tracks_unavailable` counts tracks SoundCloud does
not give out (DRM, preview, blocked). They are skipped, not failures: a job
with only such tracks still ends `completed`. The job detail carries
`unavailable_log`, the path of the list of these tracks on the server.

### Failures and retrying

A track (or link) that failed is listed in the job detail as `failed_items`
(`url`, `title`, `service`, `category`, `retryable`); the summary carries
`failed_counts` (per category) and `retryable_count`. Every failure is
retryable, whatever its category (`failed`, `rate_limited`, `network`):
many tracks that failed once come down on a second attempt. Unavailable
tracks (DRM, preview, blocked, removed) are never failures and never retried.

```bash
# only the failed tracks, or every link of the job again
curl -H "Authorization: Bearer $MUSIC_LOADER_TOKEN" -d '{"scope": "failed"}' \
     http://192.168.1.10:8765/api/v1/jobs/<id>/retry
curl -H "Authorization: Bearer $MUSIC_LOADER_TOKEN" -d '{"scope": "all"}' \
     http://192.168.1.10:8765/api/v1/jobs/<id>/retry
```

The new job has the same options and `retry_of` set to the original. Errors:
`400` for a missing or unknown `scope`, `404` for an unknown job, `409` while
the job is queued or running or when scope `failed` finds nothing to retry,
`503` when the queue is full.

With the job option `auto_retry` the server does this by itself: a job that
*completes* (or whose worker crashed after recording failures) with failed tracks of any category is queued again after 15, 30 and 60
minutes (three attempts at most; `attempt` / `max_attempts` count them). The
retry is queued once the running job is done, so the times are a lower bound. While
it waits, the job has `retry_at` (epoch milliseconds); `POST .../cancel` on it
stops the wait. The timer is saved with the job history, so it survives a
restart (see [History across restarts](#history-across-restarts)).

`GET /api/v1/info` and every `state` event carry `features`
(`["retry", "auto_retry", "debug_log", "soundcloud_format"]`). An older server has
no such list (or lacks an entry); the Android app then hides the retry actions
or the debug log switch. A server without `soundcloud_format` ignores the
formatting options and writes the default format.

### Debug log

With the job option `debug_log` the worker writes the same debug log as the
CLI's `--debug-log` (format and contents: [CLI reference](cli.md#debug-log))
to `.music-loader-logs/debug-YYYYMMDD-HHMMSS.jsonl` in the library. The job
detail carries its path as `debug_log` (kept in the job history), and
`GET /api/v1/jobs/<id>/debug-log` sends the file as it is at that moment, so a
client on another device can save or share it, also while the job runs. Only
a `debug-*` file in the library's log folder is ever sent. Secrets are masked
in the file; a retry of the job keeps the option and writes a new log.

```bash
curl -H "Authorization: Bearer $MUSIC_LOADER_TOKEN" -o debug.jsonl \
     http://192.168.1.10:8765/api/v1/jobs/<id>/debug-log
```

`links` entries may hold several whitespace-separated links. Entries that are
not Spotify/SoundCloud links are returned in `rejected`; a request without a
single valid link is answered with `400`.

Private SoundCloud links: the secret token stays on the server; API responses
and the event stream carry redacted links only.

```bash
curl -H "Authorization: Bearer $MUSIC_LOADER_TOKEN" http://192.168.1.10:8765/api/v1/info
curl -H "Authorization: Bearer $MUSIC_LOADER_TOKEN" -H "Content-Type: application/json" \
     -d '{"links": ["https://soundcloud.com/artist/song"]}' \
     http://192.168.1.10:8765/api/v1/jobs
```
