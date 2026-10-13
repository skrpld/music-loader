# Music Loader for Android

Downloads Spotify and SoundCloud links straight to the phone, or queues them on a Music Loader server.

The [`music_loader`](../cli/music_loader) package runs inside the app (phone mode, the default), or
the app drives a computer running [`music-loader serve`](../docs/how-to/run-a-server.md) and follows
the downloads there (server mode). Kotlin and Jetpack Compose with Material 3 Expressive: dynamic
colors from the wallpaper (Android 12+), light / dark / system theme, adaptive layout (bottom bar
on phones, navigation rail and list + details on tablets and foldables). English and Russian.

## Table of Contents

- [Install](#install)
- [Usage](#usage)
- [Development](#development)
- [Contributing](#contributing)
- [License](#license)

## Install

Requires Android 8.0 (API 26) or newer on an `arm64-v8a` device.

Download `music-loader-<VERSION>.apk` from the latest
[`android-v*` release](https://github.com/skrpld/music-loader/releases) and open it on the phone
(allow installing from the browser / file manager when asked). `<VERSION>` is the app version, for
example `1.3.0`. Each release also has a `.sha256` checksum file.

APKs named `*-debug.apk` are signed with a throwaway key; uninstall the previous build before
installing one.

### Build from source

Requires JDK 17+, the Android SDK (or Android Studio) with platform 37, and Python 3.13 in `PATH`
(Chaquopy installs the app's Python packages with it). The Gradle wrapper downloads Gradle 9.5.0.

```sh
git clone https://github.com/skrpld/music-loader
cd music-loader/android
./gradlew assembleDebug
adb install app/build/outputs/apk/debug/app-debug.apk
```

## Usage

1. On the first start, allow **All files access** (the storage permission on Android 10 and older)
   and notifications when asked. The first start after installing or updating unpacks ffmpeg and
   the JavaScript runtime (up to a minute).
2. On **Download**, paste links, or share them from the Spotify / SoundCloud app into Music Loader.
   Choose the lyrics mode and options, then add them to the queue.
3. Follow progress on **Jobs**: links and tracks progress, active downloads with speed and ETA,
   statistics and a filterable activity log. A finished job offers **Retry failed (N)** and
   **Run again**; the *Retry automatically* option queues failed tracks again after 15, 30 and 60
   minutes.

The music lands in the phone's `Music` folder; pick another folder in **Settings**.

To download on a computer instead, run `music-loader serve -o ~/Music` there, then enter the
printed address and token in **Settings → Where to download → Server** and tap **Test → Save**.
Retry actions need `music-loader` 2.2 or later on the server.

**Settings** also holds optional Spotify Client ID / Secret for the phone mode (official API, fewer
403/429 refusals; stored encrypted with the Android Keystore), theme and dynamic colors.

How the phone mode works, its permissions and limits:
[About the Android phone mode](../docs/explanation/android-phone-mode.md).

## Development

| Task | Command (from `android/`) |
|---|---|
| Debug APK | `./gradlew assembleDebug` (`app/build/outputs/apk/debug/app-debug.apk`) |
| Release APK | `./gradlew assembleRelease` |

`assembleRelease` signs the APK when `ANDROID_KEYSTORE_PATH`, `ANDROID_KEYSTORE_PASSWORD`,
`ANDROID_KEY_ALIAS` and `ANDROID_KEY_PASSWORD` are set in the environment; CI sets them from
repository secrets. Versioning and signing setup: [Publish a release](../docs/how-to/publish-a-release.md).

The app's Python packages are pinned in [`app/requirements-android.txt`](app/requirements-android.txt);
its comments have the build commands for the prebuilt wheels in [`app/wheels/`](app/wheels). A new
dependency of `music_loader` must be added there too, not only to `cli/pyproject.toml`.

```text
app/src/main/
├── java/dev/skrpld/musicloader/
│   ├── MainActivity.kt, MusicLoaderApplication.kt
│   ├── data/      # API client (OkHttp + SSE), models, settings (DataStore), link parsing
│   ├── engine/    # phone mode: embedded downloader, storage access, download service
│   └── ui/        # Compose screens: download, jobs, settings; theme; view models
└── python/        # Android-only stand-ins (curl_cffi, pymongo)
```

UI strings are localized in English and Russian (`res/values/strings.xml`,
`res/values-ru/strings.xml`); add new strings to both.

## Contributing

Questions and bug reports: [issues](https://github.com/skrpld/music-loader/issues). Pull requests
are welcome; commits follow [Conventional Commits](https://www.conventionalcommits.org/) with a
scope such as `android`.

## License

[Apache-2.0](../LICENSE) © skrpld
