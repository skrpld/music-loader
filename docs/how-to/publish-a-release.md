# Publish a release

The CLI and the Android app are versioned and released independently. A
release is cut either by pushing a tag or by running the component's workflow
manually on `main` (see [Release from a manual run](#release-from-a-manual-run));
GitHub Actions builds, checks and publishes it as a GitHub release. Both ways
give the same tag, checks and assets.

| Component | Tag | Workflow | Release assets |
|---|---|---|---|
| CLI / server | `cli-vX.Y.Z` | [`cli.yml`](../../.github/workflows/cli.yml) | wheel, sdist, `SHA256SUMS` |
| Android app | `android-vX.Y.Z` | [`android.yml`](../../.github/workflows/android.yml) | `music-loader-X.Y.Z.apk`, `.sha256` |

A suffix (`cli-v2.1.0-rc.1`, `android-v1.1.0-beta.2`) publishes a
pre-release. Release notes are generated from the pull requests and commits
since the previous tag of the same component.

## CLI

1. Bump `__version__` in [`cli/music_loader/__init__.py`](../../cli/music_loader/__init__.py)
   (the only place; `pyproject.toml` reads it).
2. Commit and merge to `main`.
3. Tag and push:

   ```bash
   git tag cli-v2.1.0
   git push origin cli-v2.1.0
   ```

The workflow runs lint and tests on Python 3.10 and 3.13, refuses a tag that
does not match `__version__`, builds the wheel and sdist, installs the wheel
into a clean environment and runs it, then publishes the release.

## Android

1. Optionally bump `appVersionName` in
   [`android/gradle.properties`](../../android/gradle.properties) (used by
   branch builds; a tag build takes its version from the tag).
2. Commit and merge to `main`.
3. Tag and push:

   ```bash
   git tag android-v1.1.0
   git push origin android-v1.1.0
   ```

`versionName` comes from the tag; `versionCode` is the workflow run number,
so every build is newer than the previous one.

### Signing

Android installs an update only when it is signed with the same key as the
installed app. Without signing secrets the workflow falls back to the debug
build, whose key is generated on every run - such APKs (`*-debug.apk`) cannot
be installed over each other. Set up a release key once:

```bash
keytool -genkeypair -v -keystore music-loader.jks -alias music-loader \
  -keyalg RSA -keysize 4096 -validity 10000
base64 -w0 music-loader.jks   # value of ANDROID_KEYSTORE_BASE64
```

Repository secrets (**Settings → Secrets and variables → Actions**):

| Secret | Value |
|---|---|
| `ANDROID_KEYSTORE_BASE64` | the keystore, base64-encoded |
| `ANDROID_KEYSTORE_PASSWORD` | keystore password |
| `ANDROID_KEY_ALIAS` | key alias (`music-loader` above) |
| `ANDROID_KEY_PASSWORD` | key password |

Keep the keystore and its passwords outside the repository (`*.jks`,
`*.keystore` and `keystore.properties` are git-ignored) and back them up: a
lost key means users have to uninstall the app before the next update.

## Release from a manual run

Instead of pushing a tag, run the workflow on `main` with `release` enabled:
**Actions → CLI / Android → Run workflow**, branch `main`. The run builds and
checks the commit exactly as for a tag push, then creates the tag on that
commit and publishes the release. Claude Code cloud sessions, which cannot
push tags, release this way after their pull request is merged.

```bash
# CLI: the version is __version__ from cli/music_loader/__init__.py
gh workflow run cli.yml --ref main -f release=true

# Android: the version becomes versionName and the tag android-v1.11.0
gh workflow run android.yml --ref main -f release=true -f version=1.11.0
```

The run refuses to release (and publishes nothing) when:

- it is not on `main`;
- the version is not `MAJOR.MINOR.PATCH[-suffix]` (Android: the `version`
  input is required);
- the tag `cli-v<version>` / `android-v<version>` already exists;
- the version is not newer than the latest tag of the same component.

A suffix (`-rc.1`) publishes a pre-release, as with tags. The tag is created
with the workflow token, which does not start another run: everything happens
in the one manual run. Without `release` a manual run is an ordinary build on
any branch and only uploads its artifact.

## Branch and pull request builds

The same workflows run on every push and pull request that touches their
component (`cli/**`; `android/**` or `cli/music_loader/**`, which the app
embeds). Their output is attached to the run as
an artifact (**Actions → run → Artifacts**) and is not published. A manual run
without `release` (**Run workflow** on any branch) does the same.

## Re-running a release

Re-running a failed release workflow is safe: an existing release keeps its
notes and only its assets are replaced. To rebuild from a different commit,
delete the release and the tag on GitHub, then tag again or start a new
manual run.
