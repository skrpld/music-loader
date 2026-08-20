"""SoundCloud downloads with a decoupled download/post-processing pipeline.

Track discovery uses a *flat* playlist listing (ids and urls only), so a large
playlist or a whole user profile resolves in seconds instead of minutes.  The
full metadata needed for tagging (title, uploader, thumbnail, description) is
printed by yt-dlp while the track is being downloaded, so nothing is resolved
twice.

The producer only downloads the source audio.  Finished downloads are put into
an internal queue and a small worker pool performs the expensive ffmpeg /
thumbnail / metadata work.  Lyrics use a separate, independently limited pool
so network lookups cannot stall the download producer. A lyrics search query
is built from the track's actual artist/title tags (read back from the file,
falling back to platform metadata), not the filename, and a track whose
lyrics were not found is not re-searched again until a cooldown period has
passed, so already-downloaded tracks don't cost time on every run.

Shutdown is explicit: whatever happens in the producer (an unexpected error or
Ctrl+C), the workers always receive their stop signals, so the run can never
hang waiting for threads that are blocked on an empty queue.
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Any

try:
    from mutagen import File as mutagen_file
except ImportError:  # pragma: no cover - dependency is declared in pyproject
    mutagen_file = None

try:
    from yt_dlp.utils import sanitize_filename
except ImportError:  # pragma: no cover - dependency is declared in pyproject
    sanitize_filename = None

from .config import (
    ARCHIVE_FILENAME,
    AUDIO_EXTENSIONS,
    INDEX_FILENAME,
    LYRICS_ATTEMPTS_FILENAME,
    LYRICS_RETRY_COOLDOWN_SECONDS,
    RAW_EXTENSIONS,
    STAGING_DIRNAME,
    STALE_STAGING_SECONDS,
    SUBPROCESS_TIMEOUT_SECONDS,
)
from .lyrics import fetch_lyrics
from .playlist import update_soundcloud_playlist
from .process import run_captured, run_streamed

# Matches yt-dlp's default (--newline) progress line, e.g.:
# [download] 45.2% of ~3.45MiB at 1.23MiB/s ETA 00:02
_PROGRESS_RE = re.compile(
    r"\[download\]\s+(\d{1,3}(?:\.\d)?)%\s+of\s+~?\s*[\d.]+\w+\s+at\s+"
    r"([\d.]+\w+/s|Unknown speed)\s+ETA\s+(\S+)"
)
_ERROR_RE = re.compile(r"^ERROR:", re.IGNORECASE)
_DEFAULT_LYRICS_WORKERS = 2


class _SoundCloudIndex:
    """Persistent id -> local-file mapping plus a legacy metadata index.

    The legacy metadata scan reads tags from every audio file in the library,
    which is expensive.  It is therefore performed lazily: only when an id
    lookup misses, and only once per run (the instance is reused across all
    links of a run, see `get_index`).
    """

    def __init__(self, soundcloud_dir: Path):
        self.dir = soundcloud_dir
        self.path = soundcloud_dir / INDEX_FILENAME
        self._lock = threading.RLock()
        self._data: dict[str, dict[str, Any]] = self._load()
        self._legacy: list[tuple[Path, str, str, float | None]] | None = None

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    @staticmethod
    def _norm(value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip().casefold()

    def _ensure_legacy_locked(self) -> None:
        if self._legacy is not None:
            return
        self._legacy = []
        if mutagen_file is None:
            return
        known = {entry.get("path") for entry in self._data.values()}
        try:
            candidates = list(self.dir.rglob("*"))
        except OSError:
            return
        for path in candidates:
            try:
                relative_parts = path.relative_to(self.dir).parts
            except ValueError:
                continue
            # Skip hidden folders such as the staging directory: they hold
            # partial downloads, not library files.
            if any(part.startswith(".") for part in relative_parts):
                continue
            try:
                if not path.is_file() or path.suffix.lower() not in AUDIO_EXTENSIONS:
                    continue
            except OSError:
                continue
            if str(path) in known:
                continue
            try:
                audio = mutagen_file(path, easy=True)
                if audio is None:
                    continue
                tags = audio.tags or {}
                title = tags.get("title", [""])[0]
                artist = tags.get("artist", [""])[0]
                duration = getattr(audio.info, "length", None)
                if title and artist:
                    self._legacy.append((path, self._norm(title), self._norm(artist), duration))
            except Exception:
                continue

    def find(self, info: dict[str, Any]) -> Path | None:
        track_id = str(info.get("id") or "")
        title = self._norm(info.get("track") or info.get("title"))
        artist = self._norm(info.get("uploader") or info.get("artist") or info.get("creator"))
        duration = info.get("duration")
        with self._lock:
            entry = self._data.get(track_id) if track_id else None
            if entry:
                candidate = Path(entry.get("path", ""))
                if candidate.exists():
                    return candidate
                self._data.pop(track_id, None)

            # A flat listing has neither a reliable title nor an uploader, so
            # metadata matching would compare empty strings against each other.
            if not title or not artist:
                return None

            # New files carry the SoundCloud URL in their tags.  The persistent
            # index above is the primary mapping; metadata matching is only a
            # compatibility path for files created by older versions.
            self._ensure_legacy_locked()
            for candidate, old_title, old_artist, old_duration in self._legacy or []:
                if old_title != title or old_artist != artist:
                    continue
                if duration is not None and old_duration is not None:
                    try:
                        if abs(float(duration) - float(old_duration)) > 2.0:
                            continue
                    except (TypeError, ValueError):
                        pass
                if not candidate.exists():
                    continue
                return candidate
        return None

    def add(self, info: dict[str, Any], path: Path) -> None:
        track_id = str(info.get("id") or "")
        if not track_id:
            return
        with self._lock:
            self._data[track_id] = {
                "path": str(path),
                "title": info.get("title"),
                "artist": info.get("uploader") or info.get("artist") or info.get("creator"),
                "duration": info.get("duration"),
                "webpage_url": info.get("webpage_url"),
            }
            self._save_locked()
            if self._legacy is None:
                return
            try:
                duration = float(info["duration"]) if info.get("duration") is not None else None
            except (TypeError, ValueError):
                duration = None
            fingerprint = (
                path,
                self._norm(info.get("title")),
                self._norm(info.get("uploader") or info.get("artist") or info.get("creator")),
                duration,
            )
            self._legacy = [item for item in self._legacy if item[0] != path]
            self._legacy.append(fingerprint)

    def _save_locked(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            # A failing index write must never abort a finished download.
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


# Scanning the whole library with mutagen is expensive, and every link used to
# repeat it from scratch.  One index per directory is created per run and then
# reused (it is kept up to date through `add`).
_INDEX_CACHE: dict[Path, _SoundCloudIndex] = {}
_INDEX_CACHE_LOCK = threading.Lock()


def get_index(soundcloud_dir: Path) -> _SoundCloudIndex:
    """Returns one shared index per directory, so the expensive library scan
    happens at most once even when many links are processed in a row."""
    key = soundcloud_dir.resolve()
    with _INDEX_CACHE_LOCK:
        index = _INDEX_CACHE.get(key)
        if index is None:
            index = _SoundCloudIndex(key)
            _INDEX_CACHE[key] = index
        return index


class _SoundCloudArchive:
    """Keeps yt-dlp-compatible archive entries, but commits only after success."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        try:
            self._lines = set(path.read_text(encoding="utf-8").splitlines())
        except (FileNotFoundError, OSError):
            self._lines = set()

    def add(self, track_id: str) -> None:
        if not track_id:
            return
        line = f"soundcloud {track_id}"
        with self._lock:
            if line in self._lines:
                return
            try:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                return
            self._lines.add(line)


class _LyricsAttempts:
    """Remembers when a lyrics search last found nothing for a track.

    Without this, a track whose lyrics genuinely aren't available anywhere
    gets searched again on every single run, which adds up across a large
    already-downloaded library. A failed lookup is skipped until
    `LYRICS_RETRY_COOLDOWN_SECONDS` has passed, after which it's tried again
    in case the lyrics provider has since added it.
    """

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            self._data: dict[str, str] = raw if isinstance(raw, dict) else {}
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            self._data = {}

    def should_skip(self, track_id: str) -> bool:
        if not track_id:
            return False
        with self._lock:
            last = self._data.get(track_id)
        if not last:
            return False
        try:
            last_attempt = datetime.fromisoformat(last)
        except ValueError:
            return False
        return (datetime.now() - last_attempt).total_seconds() < LYRICS_RETRY_COOLDOWN_SECONDS

    def record(self, track_id: str) -> None:
        if not track_id:
            return
        with self._lock:
            self._data[track_id] = datetime.now().isoformat()
            self._save_locked()

    def _save_locked(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            # A failing write must never abort a finished download.
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def _clean_metadata(value: Any, fallback: str = "") -> str:
    text = str(value or fallback).replace("\x00", "").strip()
    return text[:1000]


def _read_audio_tags(audio_path: Path) -> tuple[str, str]:
    """Returns (artist, title) read back from the file's own embedded tags.

    These are the most reliable source for a lyrics search query: they were
    written from the track's full metadata during post-processing, unlike
    the flat playlist listing used for already-downloaded tracks, which may
    have an empty or unreliable title/uploader.
    """
    if mutagen_file is None:
        return "", ""
    try:
        audio = mutagen_file(audio_path, easy=True)
        if audio is None:
            return "", ""
        tags = audio.tags or {}
        title = tags.get("title", [""])[0]
        artist = tags.get("artist", [""])[0]
        return artist, title
    except Exception:
        return "", ""


def _final_name(info: dict[str, Any]) -> str:
    title = _clean_metadata(info.get("title"), "SoundCloud track")
    if sanitize_filename is not None:
        return sanitize_filename(title, restricted=False) or "SoundCloud track"
    return re.sub(r"[\\/:*?\"<>|]", "_", title).strip() or "SoundCloud track"


def _allocate_output(soundcloud_dir: Path, info: dict[str, Any]) -> Path:
    base = _final_name(info)
    candidate = soundcloud_dir / f"{base}.mp3"
    track_id = str(info.get("id") or "")
    if not candidate.exists():
        return candidate
    # If a title collision belongs to another SoundCloud track, don't overwrite
    # it; make the identity explicit in the filename while the index remains
    # the authoritative mapping.
    suffix = f" [{track_id}]" if track_id else " (2)"
    candidate = soundcloud_dir / f"{base}{suffix}.mp3"
    n = 2
    while candidate.exists():
        candidate = soundcloud_dir / f"{base}{suffix} ({n}).mp3"
        n += 1
    return candidate


def _remove_quietly(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _cleanup_stale_staging(staging_dir: Path) -> None:
    """Removes leftovers from runs that were interrupted long ago."""
    cutoff = time.time() - STALE_STAGING_SECONDS
    try:
        entries = list(staging_dir.iterdir())
    except OSError:
        return
    for entry in entries:
        try:
            if entry.is_file():
                if entry.stat().st_mtime < cutoff:
                    entry.unlink(missing_ok=True)
            elif entry.is_dir():
                for child in entry.iterdir():
                    if child.is_file() and child.stat().st_mtime < cutoff:
                        child.unlink(missing_ok=True)
                entry.rmdir()
        except OSError:
            continue


def _download_thumbnail(info: dict[str, Any], work_dir: Path) -> Path | None:
    url = info.get("thumbnail")
    if not url:
        return None
    raw = work_dir / "cover.original"
    jpg = work_dir / "cover.jpg"
    try:
        with urllib.request.urlopen(str(url), timeout=30) as response, raw.open("wb") as out:
            out.write(response.read())
        convert = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw),
             "-frames:v", "1", "-q:v", "2", str(jpg)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if convert.returncode == 0 and jpg.exists():
            return jpg
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _postprocess_track(
    info: dict[str, Any],
    raw_path: Path,
    soundcloud_dir: Path,
    index: _SoundCloudIndex,
    archive: _SoundCloudArchive,
    dashboard,
) -> tuple[bool, Path | None, bool]:
    """Returns ``(ok, final_path, already_existed)``."""
    existing = index.find(info)
    if existing is not None:
        # Recognized only after the download (e.g. a legacy file matched by
        # full metadata). Register it and drop the redundant source file.
        index.add(info, existing)
        archive.add(str(info.get("id") or ""))
        _remove_quietly(raw_path)
        return True, existing, True

    output_path = _allocate_output(soundcloud_dir, info)
    work_dir = soundcloud_dir / STAGING_DIRNAME / str(info["id"])
    work_dir.mkdir(parents=True, exist_ok=True)
    cover = _download_thumbnail(info, work_dir)
    tmp_output = output_path.with_suffix(".mp3.part")

    artist = _clean_metadata(
        info.get("uploader") or info.get("artist") or info.get("creator"),
        "Unknown Artist",
    )
    title = _clean_metadata(info.get("title"), "Unknown Title")
    album = "SoundCloud"
    webpage_url = _clean_metadata(info.get("webpage_url"))
    description = _clean_metadata(info.get("description"))

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(raw_path),
    ]
    if cover is not None:
        cmd += ["-i", str(cover), "-map", "0:a:0", "-map", "1:v:0",
                "-c:v", "mjpeg", "-disposition:v:0", "attached_pic"]
    else:
        cmd += ["-map", "0:a:0"]
    cmd += [
        "-c:a", "libmp3lame", "-q:a", "0", "-id3v2_version", "3",
        "-metadata", f"title={title}",
        "-metadata", f"artist={artist}",
        "-metadata", f"album={album}",
        "-metadata", f"album_artist={artist}",
        "-metadata", f"comment={webpage_url}",
    ]
    if description:
        cmd += ["-metadata", f"description={description}"]
    cmd.append(str(tmp_output))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
        if result.returncode != 0 or not tmp_output.exists():
            message = (result.stderr or result.stdout or "ffmpeg failed").strip()
            dashboard.log_error(
                "SoundCloud", f"Post-processing failed for '{title}': {message[:500]}"
            )
            _remove_quietly(tmp_output)
            return False, None, False
        tmp_output.replace(output_path)
    except (OSError, subprocess.SubprocessError) as exc:
        dashboard.log_error("SoundCloud", f"Post-processing failed for '{title}': {exc}")
        _remove_quietly(tmp_output)
        return False, None, False
    finally:
        _remove_quietly(raw_path)
        try:
            for child in work_dir.iterdir():
                child.unlink(missing_ok=True)
            work_dir.rmdir()
        except OSError:
            pass

    index.add(info, output_path)
    archive.add(str(info.get("id") or ""))
    return True, output_path, False


def _parse_entries(data: Any) -> list[dict[str, Any]]:
    entries = data.get("entries") if isinstance(data, dict) else None
    if entries is None:
        entries = [data]
    tracks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in entries:
        if not isinstance(item, dict):
            continue
        # Nested collections (e.g. a user profile with several playlists).
        if item.get("entries"):
            for nested in _parse_entries(item):
                if nested["id"] not in seen:
                    seen.add(nested["id"])
                    tracks.append(nested)
            continue
        track_id = str(item.get("id") or "")
        track_url = item.get("webpage_url") or item.get("url")
        if not track_id or not track_url or track_id in seen:
            continue
        item = dict(item)
        item["id"] = track_id
        item["webpage_url"] = track_url
        seen.add(track_id)
        tracks.append(item)
    return tracks


def _discover_tracks(url: str, dashboard) -> list[dict[str, Any]]:
    """Lists the tracks of a link without resolving each one of them.

    `--flat-playlist` keeps this to a single cheap request even for large
    playlists; the per-track metadata that post-processing needs is collected
    later, while the track is being downloaded.
    """
    json_lines: list[str] = []
    other_lines: list[str] = []
    started = time.monotonic()

    def on_line(line: str) -> None:
        if line.startswith("{"):
            json_lines.append(line)
        else:
            other_lines.append(line)

    def on_idle(_idle_seconds: float) -> None:
        elapsed = int(time.monotonic() - started)
        dashboard.update_file(label=f"SoundCloud: resolving tracks... ({elapsed}s)")

    code = run_streamed(
        ["yt-dlp", "--flat-playlist", "--dump-single-json", "--no-warnings", url],
        on_line,
        on_idle=on_idle,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )
    if code != 0 or not json_lines:
        detail = "\n".join(other_lines[-5:]) or f"yt-dlp exited with code {code}"
        raise RuntimeError(detail.strip())

    try:
        data = json.loads(json_lines[-1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not parse yt-dlp metadata: {exc}") from exc

    return _parse_entries(data)


def _merge_info(base: dict[str, Any], full: dict[str, Any] | None) -> dict[str, Any]:
    if not full:
        return base
    merged = dict(base)
    for key, value in full.items():
        if value is not None:
            merged[key] = value
    if not merged.get("webpage_url"):
        merged["webpage_url"] = base.get("webpage_url")
    merged["id"] = str(merged.get("id") or base.get("id") or "")
    return merged


def _fetch_full_info(url: str) -> dict[str, Any] | None:
    """Fallback used only when the download did not print metadata."""
    code, stdout, _stderr = run_captured(
        ["yt-dlp", "--no-playlist", "--dump-single-json", "--skip-download", "--no-warnings", url],
        timeout=300,
    )
    if code != 0:
        return None
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _download_one(
    info: dict[str, Any],
    staging_dir: Path,
    dashboard,
) -> tuple[bool, Path | None, dict[str, Any]]:
    track_id = str(info["id"])
    title = _clean_metadata(info.get("title"), track_id)
    output_template = str(staging_dir / f"{track_id}.%(ext)s")
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--newline",
        "--progress",
        "--no-simulate",
        "--dump-json",
        "-f", "bestaudio/best",
        "--no-part",
        "-o", output_template,
        info["webpage_url"],
    ]

    dashboard.update_file(label=f"SoundCloud: {title[:60]}", percent=0, speed="", eta="")
    had_error = False
    full_info: dict[str, Any] | None = None

    def on_line(line: str) -> None:
        nonlocal had_error, full_info
        if full_info is None and line.startswith("{"):
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict) and parsed.get("id"):
                full_info = parsed
                return
        match = _PROGRESS_RE.search(line)
        if match:
            percent, speed, eta = match.groups()
            dashboard.update_file(percent=float(percent), speed=speed, eta=f"ETA {eta}")
        if _ERROR_RE.match(line):
            had_error = True
            dashboard.log_error("SoundCloud", line)

    code = run_streamed(cmd, on_line)

    candidates = sorted(
        p for p in staging_dir.glob(f"{track_id}.*")
        if p.is_file() and p.suffix.lower() not in {".part", ".tmp"}
    )
    if had_error or code != 0:
        # A partially downloaded source file is useless and must not be left
        # behind for the next run to trip over.
        for path in candidates:
            _remove_quietly(path)
        return False, None, _merge_info(info, full_info)

    if full_info is None:
        # Rare: the metadata line was not printed. One extra cheap request
        # keeps the tags correct instead of writing "Unknown Artist".
        full_info = _fetch_full_info(str(info["webpage_url"]))
    merged = _merge_info(info, full_info)

    for path in candidates:
        if path.suffix.lower() in RAW_EXTENSIONS:
            return True, path, merged
    return False, None, merged


def _drain_queue(queue: "Queue[tuple[dict[str, Any], Path] | None]") -> None:
    """Empties the queue after an abort, deleting the temp files it holds."""
    while True:
        try:
            job = queue.get_nowait()
        except Empty:
            return
        try:
            if job is not None:
                _remove_quietly(job[1])
        finally:
            queue.task_done()


def download_soundcloud(
    url: str,
    soundcloud_dir: Path,
    dashboard,
    postprocess_workers: int = 4,
    lyrics_workers: int = _DEFAULT_LYRICS_WORKERS,
) -> bool:
    """Download a SoundCloud link using a producer + worker pipeline."""
    dashboard.log(f"[SoundCloud] Starting: {url}")
    dashboard.start_file(label="SoundCloud: resolving tracks...")

    soundcloud_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = soundcloud_dir / STAGING_DIRNAME
    staging_dir.mkdir(exist_ok=True)
    _cleanup_stale_staging(staging_dir)
    archive = _SoundCloudArchive(soundcloud_dir / ARCHIVE_FILENAME)
    index = get_index(soundcloud_dir)
    lyrics_attempts = _LyricsAttempts(soundcloud_dir / LYRICS_ATTEMPTS_FILENAME)

    try:
        tracks = _discover_tracks(url, dashboard)
    except Exception as exc:
        dashboard.log_error("SoundCloud", f"Could not resolve '{url}': {exc}")
        dashboard.finish_file()
        return False

    dashboard.add_tracks_total("soundcloud", len(tracks))
    dashboard.log(f"[SoundCloud] Found {len(tracks)} track(s)")
    if not tracks:
        dashboard.finish_file()
        update_soundcloud_playlist(soundcloud_dir, dashboard)
        return True

    worker_count = max(1, int(postprocess_workers))
    lyrics_count = max(1, int(lyrics_workers))
    queue: Queue[tuple[dict[str, Any], Path] | None] = Queue(maxsize=worker_count * 2)
    futures: list[Future[None]] = []
    lyrics_futures: list[Future[bool]] = []
    had_failure = False
    aborted = False
    state_lock = threading.Lock()

    def _lyrics_task(info: dict[str, Any], audio_path: Path) -> bool:
        if audio_path.with_suffix(".lrc").exists():
            dashboard.record_lyrics(True)
            return True

        track_id = str(info.get("id") or "")
        if track_id and lyrics_attempts.should_skip(track_id):
            # Already tried and failed recently for this exact track - don't
            # spend time searching again until the cooldown passes.
            dashboard.record_lyrics(False)
            return False

        # The file's own embedded tags (written during post-processing) are
        # the most reliable source; the flat-listing `info` is a fallback
        # for cases where tags can't be read.
        artist, title = _read_audio_tags(audio_path)
        if not title:
            title = _clean_metadata(info.get("title"))
        if not artist:
            artist = _clean_metadata(info.get("uploader") or info.get("artist") or info.get("creator"))

        found = fetch_lyrics(audio_path, artist, title, dashboard)
        if not found and track_id:
            lyrics_attempts.record(track_id)
        dashboard.record_lyrics(found)
        return found

    def postprocess_worker() -> None:
        nonlocal had_failure
        while True:
            job = queue.get()
            try:
                if job is None:
                    return
                info, raw_path = job
                title = _clean_metadata(info.get("title"), str(info.get("id")))
                try:
                    ok, final_path, already_existed = _postprocess_track(
                        info, raw_path, soundcloud_dir, index, archive, dashboard
                    )
                    if not ok or final_path is None:
                        with state_lock:
                            had_failure = True
                        dashboard.record_track("soundcloud", "failed")
                        continue

                    if already_existed:
                        dashboard.record_track("soundcloud", "skipped")
                        dashboard.log(f"[SoundCloud] Already exists: {final_path.name}")
                    else:
                        dashboard.record_track("soundcloud", "done")
                        dashboard.log(f"[SoundCloud] Ready: {title}")
                    with state_lock:
                        lyrics_futures.append(lyrics_pool.submit(_lyrics_task, info, final_path))
                except Exception as exc:
                    with state_lock:
                        had_failure = True
                    dashboard.record_track("soundcloud", "failed")
                    dashboard.log_error("SoundCloud", f"Worker failed for '{title}': {exc}")
            finally:
                queue.task_done()

    def put_job(job: tuple[dict[str, Any], Path]) -> bool:
        """Hands a job to the workers, but never blocks forever if every
        worker thread has died."""
        while True:
            try:
                queue.put(job, timeout=5)
                return True
            except Full:
                if all(future.done() for future in futures):
                    return False

    # One executor for conversion/metadata/thumbnail and a separate limited
    # executor for lyrics.  Lyrics futures are submitted as soon as a track is
    # ready, so they never hold up the next audio download.
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="sc-pp") as pp_pool, \
         ThreadPoolExecutor(max_workers=lyrics_count, thread_name_prefix="sc-lyrics") as lyrics_pool:
        for _ in range(worker_count):
            futures.append(pp_pool.submit(postprocess_worker))

        try:
            for info in tracks:
                existing = index.find(info)
                if existing is not None:
                    # Promote a legacy metadata match into the exact persistent
                    # id -> file mapping so future runs skip the fallback.
                    index.add(info, existing)
                    archive.add(str(info.get("id") or ""))
                    dashboard.record_track("soundcloud", "skipped")
                    dashboard.log(f"[SoundCloud] Already exists: {existing.name}")
                    with state_lock:
                        lyrics_futures.append(lyrics_pool.submit(_lyrics_task, info, existing))
                    continue

                try:
                    ok, raw_path, info = _download_one(info, staging_dir, dashboard)
                except Exception as exc:
                    ok, raw_path = False, None
                    dashboard.log_error(
                        "SoundCloud",
                        f"Download failed for '{info.get('title', info.get('id'))}': {exc}",
                    )
                if not ok or raw_path is None:
                    with state_lock:
                        had_failure = True
                    dashboard.record_track("soundcloud", "failed")
                    continue

                # The producer never waits for ffmpeg/metadata/thumbnail. It
                # only blocks when the bounded queue is full.
                if not put_job((info, raw_path)):
                    _remove_quietly(raw_path)
                    with state_lock:
                        had_failure = True
                    dashboard.log_error("SoundCloud", "All post-processing workers stopped")
                    break

            queue.join()
        except BaseException:
            # Ctrl+C or an unexpected error: the workers still have to be
            # released, which the `finally` block below takes care of.
            aborted = True
            had_failure = True
            raise
        finally:
            # Always reach the workers with a stop signal, otherwise the pool
            # shutdown would wait forever on threads blocked in `queue.get()`.
            if aborted:
                _drain_queue(queue)
            for _ in range(worker_count):
                try:
                    queue.put(None, timeout=30)
                except Full:
                    pass

            for future in futures:
                try:
                    future.result()
                except Exception as exc:
                    had_failure = True
                    dashboard.log_error("SoundCloud", f"Worker crashed: {exc}")

            # Workers may still have queued new lyrics jobs while the previous
            # batch was being awaited, so drain until nothing is left.
            while True:
                with state_lock:
                    pending = list(lyrics_futures)
                    lyrics_futures.clear()
                if not pending:
                    break
                for future in pending:
                    if aborted:
                        future.cancel()
                        continue
                    try:
                        future.result()
                    except Exception as exc:
                        had_failure = True
                        dashboard.log_error("Lyrics", f"Lyrics worker failed: {exc}")

            dashboard.finish_file()

    try:
        staging_dir.rmdir()
    except OSError:
        pass

    update_soundcloud_playlist(soundcloud_dir, dashboard)
    return not had_failure
