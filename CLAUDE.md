# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Layout

Monorepo with two independently released components that share one Python package:

- `cli/` — Python package `music-loader` (`cli/music_loader/`): command-line tool and HTTP server (`music-loader serve`). Python 3.10+, needs `ffmpeg` in `PATH` at runtime. Tests in `cli/tests/`.
- `android/` — Android app (Kotlin, Jetpack Compose, Material 3 Expressive). It embeds `cli/music_loader` via Chaquopy (`android/app/build.gradle.kts` adds `../../cli` as a Python source dir), so **every change under `cli/music_loader/` also ships in the app** and triggers the Android workflow.
- `docs/` — behaviour in depth (`how-it-works.md`), server API (`server.md`), releasing, security.
- `.github/workflows/cli.yml`, `android.yml` — CI and tag-based releases; `.github/scripts/publish-release.sh` publishes GitHub releases.

## Commands

CLI (run from `cli/`):

```bash
pip install -e ".[dev]"          # package + pytest + ruff
ruff check .                     # lint: real errors only (F, E9), style is not enforced
pytest -q                        # all tests (testpaths = tests)
pytest tests/test_links.py::test_name   # single test
python -m music_loader --version
```

From the repository root: `pytest cli/tests`. In Claude Code cloud sessions the SessionStart hook (`.claude/hooks/session-start.sh`) installs the package with `[dev]` into `cli/.venv` and puts it first in `PATH`. CI also requires `cli/LICENSE` to be an exact copy of the root `LICENSE` (`cmp LICENSE ../LICENSE`), and runs tests on Python 3.10 and 3.13 — keep code 3.10-compatible (ruff `target-version = "py310"`).

Android (from `android/`; JDK 17+, Android SDK, Python 3.13 in `PATH` for Chaquopy):

```bash
./gradlew assembleDebug          # app/build/outputs/apk/debug/app-debug.apk
./gradlew assembleRelease        # signed only if ANDROID_KEYSTORE_PATH etc. are set
```

## Architecture

- **One engine, three front-ends.** `cli.py` runs links through the Spotify (`spotify.py`, two spotDL runs: `save` then `download`) and SoundCloud (`soundcloud.py`, yt-dlp) pipelines with the `rich` dashboard (`ui.py`). Server mode (`server.py`) runs each job in a separate process (`worker.py`, job JSON on stdin) and the pipelines report through `events.py`'s `EventDashboard`, which mirrors `Dashboard`'s public methods and emits JSON lines. Keep the two dashboards' interfaces in sync.
- **Cancellation = Ctrl+C.** Server jobs are cancelled with SIGINT so the CLI's `KeyboardInterrupt` cleanup (stop tools, remove partial files, keep indexes consistent) is the single cleanup path.
- **Android phone mode.** `android.py` is the app's entry point: it starts the same server bound to `127.0.0.1` with a fresh token. Android has no Python executable, so `process.py` hands commands starting with `inprocess.MARK` to `inprocess.py`, which runs spotDL/yt-dlp in threads with per-run stdout/stderr routing. `inprocess.py` drives spotDL internals — re-check it when bumping spotDL.
- **Android Python deps** are pinned in `android/app/requirements-android.txt` (pure-Python wheels, installed with `--no-deps`; three prebuilt in `android/app/wheels/`, pykakasi patched for Chaquopy). Stand-ins for packages without an Android build (`curl_cffi`, `pymongo`) live in `android/app/src/main/python/`. A new dependency of `music_loader` must be added there too, not only to `cli/pyproject.toml`.
- **Library state** lives in hidden index files in the output folder (`.sc_index.json`, `.spotify_index.json`, `.music-loader-artists.json`, lyrics attempt files); paths are relative so libraries can move. `artists.py` keeps one canonical spelling per artist; `text_utils.py`/`soundcloud_meta.py` parse SoundCloud titles into credits; `lyrics.py` does strict verified matching. Rules are documented in `docs/how-it-works.md` — update it when behaviour changes.
- Tags are ID3v2.3 with `/` between artists (same convention as spotDL).

## Releasing (docs/releasing.md)

CLI and Android are versioned and released independently by pushing a tag; GitHub Actions builds and publishes the release. A suffix (`-rc.1`, `-beta.2`) makes a pre-release.

- **CLI**: bump `__version__` in `cli/music_loader/__init__.py` (the only place; `pyproject.toml` reads it), merge to `main`, push tag `cli-vX.Y.Z`. CI refuses a tag that does not match `__version__`. Assets: wheel, sdist, `SHA256SUMS`.
- **Android**: optionally bump `appVersionName` in `android/gradle.properties` (branch builds only), merge to `main`, push tag `android-vX.Y.Z`. `versionName` comes from the tag, `versionCode` is the workflow run number. Release signing uses the `ANDROID_KEYSTORE_*` / `ANDROID_KEY_*` repository secrets; without them CI builds a debug APK.
- Branch and PR builds run the same workflows (path-filtered) and only upload artifacts.

## Conventions

- Conventional Commits (`feat(android): ...`, `fix(soundcloud): ...`, `refactor!: ...`); scopes in use include `cli`, `server`, `android`, `spotify`, `soundcloud`.
- English for code, comments, docs and commit messages. The Android UI is localized in English and Russian (`res/values/strings.xml`, `res/values-ru/strings.xml`) — add new strings to both.
