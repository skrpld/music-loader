# Music Loader for Android

A client for [`music-loader serve`](../docs/server.md): queue Spotify and
SoundCloud links from the phone and follow the downloads on the machine that
holds the library.

Kotlin and Jetpack Compose with Material 3 Expressive: dynamic colors from the
wallpaper (Android 12+), light / dark / system theme, expressive motion, wavy
progress indicators, and an adaptive layout - bottom bar on phones, navigation
rail and list + details side by side on tablets and foldables. English and
Russian. Android 8.0 (API 26) or later.

## Screens

- **Download**: paste links (or share them from the Spotify / SoundCloud app
  into Music Loader), choose the lyrics mode and options, add to the queue.
- **Jobs**: the running job live - links and tracks progress, active
  downloads with speed and ETA, counters - plus the queue and finished jobs
  with their errors and activity log; cancel and remove.
- **Settings**: server address and token with a connection test, theme,
  dynamic colors.

## Install

Download `music-loader-<version>.apk` from the latest
[`android-v*` release](https://github.com/skrpld/music-loader/releases) and
open it on the phone (allow installing from the browser / file manager when
asked). Then start the server on the computer:

```bash
music-loader serve -o ~/Music
```

and enter the printed address and token in **Settings → Server → Test → Save**.

APKs named `*-debug.apk` are signed with a throwaway key; uninstall the
previous build before installing one.

## Build

JDK 17+ and the Android SDK (or Android Studio):

```bash
cd android
./gradlew assembleDebug     # app/build/outputs/apk/debug/app-debug.apk
./gradlew assembleRelease   # unsigned unless ANDROID_KEYSTORE_PATH etc. are set
```

Release signing reads `ANDROID_KEYSTORE_PATH`, `ANDROID_KEYSTORE_PASSWORD`,
`ANDROID_KEY_ALIAS` and `ANDROID_KEY_PASSWORD` from the environment; CI sets
them from repository secrets. Versioning, signing setup and the release
process: [docs/releasing.md](../docs/releasing.md).

## Code map

```
app/src/main/java/dev/skrpld/musicloader/
├── MainActivity.kt, MusicLoaderApplication.kt
├── data/      # API client (OkHttp + SSE), models, settings (DataStore), link parsing
└── ui/        # Compose screens: download, jobs, settings; theme; view models
```
