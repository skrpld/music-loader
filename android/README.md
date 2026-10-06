# Music Loader for Android

Downloads Spotify and SoundCloud links straight to the phone - the
[`music_loader`](../cli/music_loader) package runs inside the app - or queues
them on a computer running [`music-loader serve`](../docs/server.md) and
follows the downloads there.

Kotlin and Jetpack Compose with Material 3 Expressive: dynamic colors from the
wallpaper (Android 12+), light / dark / system theme, expressive motion, wavy
progress indicators, and an adaptive layout - bottom bar on phones, navigation
rail and list + details side by side on tablets and foldables. English and
Russian. Android 8.0 (API 26) or later, `arm64-v8a`.

## Screens

- **Download**: paste links (or share them from the Spotify / SoundCloud app
  into Music Loader), choose the lyrics mode and options, add to the queue.
- **Jobs**: the running job live - links and tracks progress, active
  downloads with speed and ETA, counters - plus the queue and finished jobs
  with their errors and activity log; cancel and remove.
- **Settings**: where to download (this phone or a server); the phone's music
  folder and storage access, or the server address and token with a
  connection test; theme, dynamic colors.

## Download modes

### This phone (default)

The app downloads, converts, tags and finds lyrics itself and saves to a
folder on the phone (default: the shared `Music` folder; pick another one in
Settings). The library layout is the same as on a computer.

How it works:

- The `music_loader` package is embedded with
  [Chaquopy](https://chaquo.com/chaquopy/). The app starts the same server as
  `music-loader serve` inside itself, bound to `127.0.0.1` with a fresh token,
  and talks to it like to a remote one.
- Android has no Python executable, so spotDL and yt-dlp run in threads of the
  app process instead of child processes
  ([`inprocess.py`](../cli/music_loader/inprocess.py)); cancelling stops them
  the same way Ctrl+C does.
- ffmpeg/ffprobe and QuickJS (the JavaScript runtime yt-dlp needs for
  YouTube, spotDL's audio source) come from the
  [youtubedl-android](https://github.com/yausername/youtubedl-android)
  packages and are shipped as native libraries; the first start after
  installing or updating unpacks them (up to a minute).
- While the queue runs, a foreground service with a progress notification
  keeps the app alive with the screen off. Finished tracks are handed to the
  media scanner so players see them.

Permissions:

- **All files access** (Android 11+; the storage permission on older
  versions): tracks, `.lrc` lyrics, playlists and the index files are written
  as plain files.
- **Notifications**: the download progress.

Limits:

- `arm64-v8a` devices only (practically every phone of the last years).
- There is no `curl_cffi` for Android, so yt-dlp cannot impersonate a
  browser: SoundCloud may refuse profile, likes and reposts listings
  (HTTP 403); tracks and sets work. spotDL's built-in Spotify client also runs
  without browser impersonation; if Spotify refuses it, use the server mode.
- Jobs live in the app's memory: the list starts empty after Android ends the
  app. The library and its indexes are on disk, so running a link again only
  fetches what is missing.
- The APK is large (~100 MB): Python, the packages and ffmpeg are inside.

### Server

A computer running `music-loader serve` downloads into its own library; the
app queues links and follows the progress:

```bash
music-loader serve -o ~/Music
```

Enter the printed address and token in **Settings → Where to download →
Server**, then **Test → Save**.

## Install

Download `music-loader-<version>.apk` from the latest
[`android-v*` release](https://github.com/skrpld/music-loader/releases) and
open it on the phone (allow installing from the browser / file manager when
asked).

APKs named `*-debug.apk` are signed with a throwaway key; uninstall the
previous build before installing one.

## Build

JDK 17+, the Android SDK (or Android Studio) and Python 3.13 in `PATH`
(Chaquopy installs the app's Python packages with it):

```bash
cd android
./gradlew assembleDebug     # app/build/outputs/apk/debug/app-debug.apk
./gradlew assembleRelease   # unsigned unless ANDROID_KEYSTORE_PATH etc. are set
```

The app's Python packages are pinned in
[`app/requirements-android.txt`](app/requirements-android.txt): pure-Python
wheels only, installed without dependency resolution; two of them are
prebuilt in [`app/wheels/`](app/wheels). Stand-ins for packages without an
Android build are in [`app/src/main/python/`](app/src/main/python).

Release signing reads `ANDROID_KEYSTORE_PATH`, `ANDROID_KEYSTORE_PASSWORD`,
`ANDROID_KEY_ALIAS` and `ANDROID_KEY_PASSWORD` from the environment; CI sets
them from repository secrets. Versioning, signing setup and the release
process: [docs/releasing.md](../docs/releasing.md).

## Code map

```
app/src/main/
├── java/dev/skrpld/musicloader/
│   ├── MainActivity.kt, MusicLoaderApplication.kt
│   ├── data/      # API client (OkHttp + SSE), models, settings (DataStore), link parsing
│   ├── engine/    # phone mode: embedded downloader, storage access, download service
│   └── ui/        # Compose screens: download, jobs, settings; theme; view models
└── python/        # Android-only stand-ins (curl_cffi, pymongo)
```
