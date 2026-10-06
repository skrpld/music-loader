# Security

## Secrets

Nothing secret belongs in the repository. Music Loader takes its secrets from
the environment or from files outside the checkout:

| Secret | Where it lives |
|---|---|
| Spotify client id / secret (optional) | `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET`; handed to spotdl through its environment, never on its command line |
| Server token | `MUSIC_LOADER_TOKEN`, or `~/.config/music-loader/server-token` (mode `0600`) |
| Server token in the app | app-private storage; backups and device transfer are disabled |
| Android signing key | GitHub Actions secrets (see [releasing.md](releasing.md)); decoded into the runner's temp folder and removed after the build |

`.gitignore` excludes `.env*`, keystores (`*.jks`, `*.keystore`,
`keystore.properties`), `local.properties`, link lists (`links.txt`), logs and
everything Music Loader writes into a library.

Command-line options `--spotify-client-secret` and `--token` exist for
convenience, but arguments are visible to every user of the machine in the
process list; prefer the environment variables.

## Input handling

- Links are validated; other sites and option-like lines are refused, and
  URLs reach yt-dlp after `--`.
- Track ids are validated before they are used in file names.
- Tools are run from the Python environment Music Loader is installed in, not
  from whatever `yt-dlp` comes first in `PATH`.
- Covers are fetched over http(s) only, with a size limit.
- Private SoundCloud links: the secret token is not written into tags,
  playlists or the failure log.
- All child output is decoded as UTF-8, and remote text is escaped before it
  reaches the terminal UI.

## Server mode

- Every request needs the token (compared in constant time); unauthenticated
  requests are delayed and their bodies are never read.
- Request bodies are size-limited, chunked bodies are refused, links are
  validated as on the command line, and the queue is bounded.
- Private SoundCloud tokens stay on the server: API responses and the event
  stream carry redacted links only.
- The server speaks plain HTTP. Expose it only in a trusted network, over a
  VPN, or behind an HTTPS reverse proxy - never directly to the internet.

## Android app

- Cleartext HTTP is allowed because the server address is only known at
  runtime and usually is a LAN or VPN address; use an `https://` address when
  the server sits behind a TLS proxy.
- `allowBackup="false"` and data extraction rules keep the token on the
  device.
- Phone mode: the app's own server listens on `127.0.0.1` only, with a random
  token per start that never leaves the app process.
- Phone mode needs "All files access" (Android 11+) to write tracks, `.lrc`
  lyrics, playlists and index files into the music folder; the downloader
  only writes below the folder chosen in Settings.
