# About the Android phone mode

In the phone mode (the app's default) the Android app downloads, converts, tags and finds lyrics
itself, using the same Python code as the CLI. The library layout is the same as on a computer.
This page explains how that works and what it costs.

## How it works

- The `music_loader` package is embedded with [Chaquopy](https://chaquo.com/chaquopy/). The app
  starts the same server as `music-loader serve` inside itself, bound to `127.0.0.1` with a fresh
  token, and talks to it like to a remote one.
- Android has no Python executable, so spotDL and yt-dlp run in threads of the app process instead
  of child processes ([`inprocess.py`](../../cli/music_loader/inprocess.py)); cancelling stops
  them the same way Ctrl+C does.
- ffmpeg/ffprobe and QuickJS (the JavaScript runtime yt-dlp needs for YouTube, spotDL's audio
  source) come from the [youtubedl-android](https://github.com/yausername/youtubedl-android)
  packages and are shipped as native libraries; the first start after installing or updating
  unpacks them (up to a minute).
- While the queue runs, a foreground service with a progress notification keeps the app alive with
  the screen off. Finished tracks are handed to the media scanner so players see them.
- The app's Python packages are pinned in
  [`android/app/requirements-android.txt`](../../android/app/requirements-android.txt): pure-Python
  wheels only, installed without dependency resolution. Three are prebuilt in
  [`android/app/wheels/`](../../android/app/wheels): rapidfuzz and websockets without their C
  extensions, and pykakasi patched to find its dictionaries through `__file__` (Chaquopy's
  `importlib.resources.files()` returns no file system path for a folder). Stand-ins for packages
  without an Android build (`curl_cffi`, `pymongo`) are in
  [`android/app/src/main/python/`](../../android/app/src/main/python).
- If the bundled packages are broken, the phone mode does not start and shows the error
  ("spotDL does not load: ...") instead of failing every Spotify job.

## Permissions

- **All files access** (Android 11+; the storage permission on older versions): tracks, `.lrc`
  lyrics, playlists and the index files are written as plain files. The downloader only writes
  below the folder chosen in Settings.
- **Notifications**: the download progress.

## Limits

- `arm64-v8a` devices only.
- There is no `curl_cffi` for Android, so yt-dlp cannot impersonate a browser: SoundCloud may
  refuse profile, likes and reposts listings (HTTP 403); tracks and sets work. spotDL's built-in
  Spotify client also runs without browser impersonation; if Spotify refuses it, use the server
  mode.
- Spotify must be reachable from the app itself. With a VPN, the VPN app has to include Music
  Loader (split tunneling often leaves new apps out); otherwise a link fails after a few seconds
  with "Cannot reach open.spotify.com".
- Tracks SoundCloud only serves DRM-protected (or as a 30-second preview) are skipped and shown as
  "Unavailable"; the option "Look for blocked tracks elsewhere" takes the same track from YouTube
  Music when artist, title, version and length match (off by default).
- Jobs live in the app's memory: the list starts empty after Android ends the app. The library and
  its indexes are on disk, so running a link again only fetches what is missing.
- The automatic retry timer lives in the app process: it works while the download service runs and
  is lost when the app is killed.
- The APK is large (~100 MB): Python, the packages and ffmpeg are inside.

## Alternative: server mode

When a limit gets in the way, a computer running `music-loader serve` can do the downloads and the
app only queues links and follows progress: [Run a server for the Android app](../how-to/run-a-server.md).
Security of both modes: [Security](security.md).
