# Server mode and API

`music-loader serve` runs Music Loader on the machine that holds the library;
the [Android app](../android/README.md) (or any HTTP client) queues links and
follows the downloads from another device.

```bash
music-loader serve -o /path/to/Music
```

At startup the server prints its address and the access token:

```
Server address: http://192.168.1.10:8765
Token: <generated token> (~/.config/music-loader/server-token)
```

Enter both in the app: **Settings → Where to download → Server**, then **Test → Save**.

## Options

| Option | Default | Meaning |
|---|---|---|
| `-o, --output` | required | Target Music folder |
| `--host` | `0.0.0.0` | Address to listen on; `127.0.0.1` behind a reverse proxy |
| `--port` | 8765 | Port |
| `--token` | stored token | Access token; prefer `MUSIC_LOADER_TOKEN` |
| `--new-token` | off | Generate a new stored token (the app has to be paired again) |
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
  `auto_retry` (queue retryable failures again after a cooldown, off by
  default).
- Cancelling a job goes through the same cleanup as Ctrl+C: child processes
  stop and unfinished files are removed. A worker that does not finish its
  cleanup within 60 s is killed.
- Stopping the server (Ctrl+C, SIGTERM) cancels the running job the same way.
- Limits: 100 queued jobs, 2000 links per job, 50 finished jobs kept,
  256 KiB request body.

## Deployment

The server speaks plain HTTP. Use it:

- in the home network, or
- over a VPN (Tailscale, WireGuard), or
- behind an HTTPS reverse proxy with `--host 127.0.0.1`. The event stream
  needs response buffering off; the server sends `X-Accel-Buffering: no` for
  nginx.

Example systemd unit (`~/.config/systemd/user/music-loader.service`):

```ini
[Unit]
Description=Music Loader server
After=network-online.target

[Service]
ExecStart=%h/.local/bin/music-loader serve -o %h/Music
Restart=on-failure
# Optional: own Spotify credentials, a fixed token
# EnvironmentFile=%h/.config/music-loader/env

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now music-loader
journalctl --user -u music-loader -f     # shows the address and token
```

Keep an `EnvironmentFile` with secrets readable only by its owner
(`chmod 600`).

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
| `DELETE /api/v1/jobs/<id>` | remove a finished job (`409` while it runs) |
| `GET /api/v1/events` | Server-Sent Events: a `state` event after every change, keep-alive comments every 15 s |

Queue request:

```json
{
  "links": ["https://open.spotify.com/album/...", "https://soundcloud.com/..."],
  "options": {"lyrics": "strict", "recheck": false, "soundcloud_reposts": false, "soundcloud_likes": false,
              "soundcloud_fallback": false, "auto_retry": false}
}
```

In a job's `stats`, `soundcloud_tracks_unavailable` counts tracks SoundCloud does
not give out (DRM, preview, blocked). They are skipped, not failures: a job
with only such tracks still ends `completed`. The job detail carries
`unavailable_log`, the path of the list of these tracks on the server.

### Failures and retrying

A track (or link) that failed is listed in the job detail as `failed_items`
(`url`, `title`, `service`, `category`, `retryable`); the summary carries
`failed_counts` (per category) and `retryable_count`. Categories:
`rate_limited` and `network` are retryable, `failed` is not. Unavailable
tracks (DRM, preview, blocked) are never failures and never retried.

```bash
# only the retryable failures, or every link of the job again
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
*completes* with retryable failures is queued again after 15, 30 and 60
minutes (three attempts at most; `attempt` / `max_attempts` count them). While
it waits, the job has `retry_at` (epoch milliseconds); `POST .../cancel` on it
stops the wait. The timer is kept in memory: it is lost when the server
restarts.

`GET /api/v1/info` and every `state` event carry `features`
(`["retry", "auto_retry"]`). An older server has no such list; the Android app
then hides the retry actions.

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
