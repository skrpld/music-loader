"""SoundCloud downloads with a decoupled download/post-processing pipeline.

The producer only downloads the source audio.  Finished downloads are put into
an internal queue and a small worker pool performs the expensive ffmpeg /
thumbnail / metadata work.  Lyrics use a separate, independently limited pool
so network lookups cannot stall the download producer.
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from queue import Queue
from typing import Any, Optional

try:
    from mutagen import File as mutagen_file
except ImportError:  # pragma: no cover - dependency is declared in pyproject
    mutagen_file = None

try:
    from yt_dlp.utils import sanitize_filename
except ImportError:  # pragma: no cover - dependency is declared in pyproject
    sanitize_filename = None

from .config import ARCHIVE_FILENAME, AUDIO_EXTENSIONS, SUBPROCESS_TIMEOUT_SECONDS
from .lyrics import fetch_lyrics
from .playlist import update_soundcloud_playlist
from .process import run_captured, run_streamed

# yt-dlp's default (--newline) progress line, e.g.:
# [download]  45.2% of ~3.45MiB at 1.23MiB/s ETA 00:02
# Percent, speed and ETA are matched separately: yt-dlp omits or changes the
# size/speed/ETA parts in several situations (live streams, unknown size),
# and a single strict pattern silently stopped updating the progress bar.
_PERCENT_RE = re.compile(r"\[download\]\s+(\d{1,3}(?:\.\d+)?)%")
_SPEED_RE = re.compile(r"\bat\s+([\d.]+\s*[KMG]?i?B/s|Unknown speed)", re.IGNORECASE)
_ETA_RE = re.compile(r"\bETA\s+(\S+)")
_ERROR_RE = re.compile(r"^ERROR:", re.IGNORECASE)

_INDEX_FILENAME = ".sc_index.json"
_STAGING_DIRNAME = ".sc_downloads"
_DEFAULT_LYRICS_WORKERS = 2

# Files yt-dlp may leave next to the audio in the staging directory.
_NON_AUDIO_SUFFIXES = {
    ".part", ".tmp", ".ytdl", ".json", ".txt", ".lrc", ".vtt", ".srt",
    ".jpg", ".jpeg", ".png", ".webp", ".description",
}


def _remove_quietly(path: Optional[Path]) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


class _SoundCloudIndex:
    """Persistent id -> local-file mapping plus a legacy metadata index.

    The legacy scan reads tags from every file in the library, so it is done
    lazily (only when an id lookup misses) and the result is reused for the
    whole run instead of being rebuilt for every link.
    """

    def __init__(self, soundcloud_dir: Path):
        self.dir = soundcloud_dir
        self.path = soundcloud_dir / _INDEX_FILENAME
        self._lock = threading.RLock()
        self._data: dict[str, dict[str, Any]] = self._load()
        self._legacy: list[tuple[Path, str, str, float | None]] = []
        self._legacy_scanned = False

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    @staticmethod
    def _norm(value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip().casefold()

    def _scan_legacy_files_locked(self) -> None:
        if self._legacy_scanned:
            return
        self._legacy_scanned = True
        if mutagen_file is None:
            return
        known = {entry.get("path", "") for entry in self._data.values()}
        try:
            candidates = list(self.dir.rglob("*"))
        except OSError:
            return
        for path in candidates:
            # Never look inside the staging directory: those are raw,
            # in-progress downloads and matching against them would make the
            # downloader "recognize" a file it is still writing.
            if _STAGING_DIRNAME in path.parts:
                continue
            if str(path) in known:
                continue
            if not path.is_file() or path.suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            try:
                audio = mutagen_file(path, easy=True)
                if audio is None:
                    continue
                tags = audio.tags or {}
                title = (tags.get("title") or [""])[0]
                artist = (tags.get("artist") or [""])[0]
                duration = getattr(audio.info, "length", None)
                if title and artist:
                    self._legacy.append((path, self._norm(title), self._norm(artist), duration))
            except Exception:
                continue

    def find(self, info: dict[str, Any]) -> Path | None:
        track_id = str(info.get("id") or "")
        title = self._norm(info.get("track", info.get("title", "")))
        artist = self._norm(info.get("uploader") or info.get("artist") or info.get("creator"))
        duration = info.get("duration")
        with self._lock:
            entry = self._data.get(track_id) if track_id else None
            if entry:
                candidate = Path(entry.get("path", ""))
                if candidate.exists():
                    return candidate
                self._data.pop(track_id, None)

            # New files are embedded with the SoundCloud URL.  The persistent
            # index above is the primary mapping; metadata matching is only a
            # compatibility path for files created by older versions.
            if not title or not artist:
                return None
            self._scan_legacy_files_locked()
            for candidate, old_title, old_artist, old_duration in self._legacy:
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
            if not self._legacy_scanned:
                return
            try:
                length = float(info["duration"]) if info.get("duration") is not None else None
            except (TypeError, ValueError):
                length = None
            fingerprint = (
                path,
                self._norm(info.get("title")),
                self._norm(info.get("uploader") or info.get("artist") or info.get("creator")),
                length,
            )
            self._legacy = [item for item in self._legacy if item[0] != path]
            self._legacy.append(fingerprint)

    def _save_locked(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            # Losing the index only costs a re-scan next run; it must never
            # abort an otherwise successful download.
            _remove_quietly(tmp)


# One index per output directory per run: the legacy tag scan is expensive and
# used to be repeated for every single link.
_INDEX_CACHE: dict[Path, _SoundCloudIndex] = {}
_INDEX_CACHE_LOCK = threading.Lock()


def _get_index(soundcloud_dir: Path) -> _SoundCloudIndex:
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
        if not track_id or track_id == "None":
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


def _clean_metadata(value: Any, fallback: str = "") -> str:
    text = str(value or fallback).replace("\x00", "").strip()
    return text[:1000]


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
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-frames:v", "1", "-q:v", "2", str(jpg)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if convert.returncode == 0 and jpg.exists():
            return jpg
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return None


def _cleanup_dir(work_dir: Path) -> None:
    try:
        for child in work_dir.iterdir():
            if child.is_file():
                child.unlink(missing_ok=True)
        work_dir.rmdir()
    except OSError:
        pass


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
        # Another job for the same track finished first: drop the raw file
        # instead of leaving it behind in the staging directory.
        _remove_quietly(raw_path)
        index.add(info, existing)
        archive.add(str(info.get("id") or ""))
        return True, existing, True

    output_path = _allocate_output(soundcloud_dir, info)
    work_dir = soundcloud_dir / _STAGING_DIRNAME / str(info.get("id") or "work")
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        dashboard.log_error("SoundCloud", f"Could not create a work directory: {exc}")
        return False, None, False
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
        cmd += ["-i", str(cover), "-map", "0:a:0", "-map", "1:v:0", "-c:v", "mjpeg", "-disposition:v:0", "attached_pic"]
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
            dashboard.log_error("SoundCloud", f"Post-processing failed for '{title}': {message[:500]}")
            _remove_quietly(tmp_output)
            return False, None, False
        tmp_output.replace(output_path)
    except (OSError, subprocess.SubprocessError) as exc:
        dashboard.log_error("SoundCloud", f"Post-processing failed for '{title}': {exc}")
        _remove_quietly(tmp_output)
        return False, None, False
    finally:
        _remove_quietly(raw_path)
        _cleanup_dir(work_dir)

    index.add(info, output_path)
    archive.add(str(info.get("id") or ""))
    return True, output_path, False


def _discover_tracks(url: str) -> list[dict[str, Any]]:
    # Full metadata (without download) gives worker threads the same identity,
    # title/artist and thumbnail information that the old yt-dlp postprocessors
    # used, while keeping the actual audio download free of post-processing.
    code, stdout, stderr = run_captured(
        [
            "yt-dlp", "--dump-single-json", "--skip-download", "--no-warnings", url,
        ],
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )
    if code != 0:
        raise RuntimeError((stderr or stdout or f"yt-dlp exited with code {code}").strip())
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not parse yt-dlp metadata: {exc}") from exc

    tracks: list[dict[str, Any]] = []
    seen: set[str] = set()

    def collect(node: Any) -> None:
        if not isinstance(node, dict):
            return
        entries = node.get("entries")
        if isinstance(entries, list):
            # Playlists of playlists (e.g. a user profile) nest one more level.
            for child in entries:
                collect(child)
            return
        track_id = str(node.get("id") or "")
        track_url = node.get("webpage_url") or node.get("url")
        if not track_id or not track_url or track_id in seen:
            return
        item = dict(node)
        item["webpage_url"] = track_url
        seen.add(track_id)
        tracks.append(item)

    collect(data)
    return tracks


def _download_one(
    info: dict[str, Any],
    staging_dir: Path,
    dashboard,
) -> tuple[bool, Path | None]:
    track_id = str(info["id"])
    title = _clean_metadata(info.get("title"), track_id)
    output_template = str(staging_dir / f"{track_id}.%(ext)s")
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--newline",
        "-f", "bestaudio/best",
        "--no-part",
        "-o", output_template,
        info["webpage_url"],
    ]

    dashboard.update_file(label=f"SoundCloud: {title[:60]}", percent=0, speed="", eta="")
    had_error = False

    def on_line(line: str) -> None:
        nonlocal had_error
        percent_match = _PERCENT_RE.search(line)
        if percent_match:
            speed_match = _SPEED_RE.search(line)
            eta_match = _ETA_RE.search(line)
            dashboard.update_file(
                percent=float(percent_match.group(1)),
                speed=speed_match.group(1) if speed_match else "",
                eta=f"ETA {eta_match.group(1)}" if eta_match else "",
            )
        if _ERROR_RE.match(line):
            had_error = True
            dashboard.log_error("SoundCloud", line)

    code = run_streamed(cmd, on_line)

    candidates = sorted(
        p for p in staging_dir.glob(f"{track_id}.*")
        if p.is_file() and p.suffix.lower() not in _NON_AUDIO_SUFFIXES
    )
    if had_error or code != 0:
        for leftover in candidates:
            _remove_quietly(leftover)
        return False, None

    for path in candidates:
        if path.stat().st_size > 0:
            return True, path
    return False, None


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

    try:
        soundcloud_dir.mkdir(parents=True, exist_ok=True)
        staging_dir = soundcloud_dir / _STAGING_DIRNAME
        staging_dir.mkdir(exist_ok=True)
    except OSError as exc:
        dashboard.log_error("SoundCloud", f"Could not prepare '{soundcloud_dir}': {exc}")
        dashboard.finish_file()
        return False

    archive = _SoundCloudArchive(soundcloud_dir / ARCHIVE_FILENAME)
    index = _get_index(soundcloud_dir)

    try:
        tracks = _discover_tracks(url)
    except Exception as exc:
        dashboard.log_error("SoundCloud", f"Could not resolve '{url}': {exc}")
        dashboard.finish_file()
        return False

    dashboard.add_tracks_total("soundcloud", len(tracks))
    if not tracks:
        dashboard.log("[SoundCloud] No tracks found")
        dashboard.finish_file()
        update_soundcloud_playlist(soundcloud_dir, dashboard)
        return True

    worker_count = max(1, int(postprocess_workers))
    lyrics_count = max(1, int(lyrics_workers))
    queue: "Queue[tuple[dict[str, Any], Path] | None]" = Queue(maxsize=worker_count * 2)
    futures: list[Future[None]] = []
    lyrics_futures: list[Future[bool]] = []
    lyrics_lock = threading.Lock()
    had_failure = False
    state_lock = threading.Lock()

    def mark_failure() -> None:
        nonlocal had_failure
        with state_lock:
            had_failure = True

    def submit_lyrics(pool: ThreadPoolExecutor, audio_path: Path) -> None:
        future = pool.submit(_lyrics_task, audio_path)
        with lyrics_lock:
            lyrics_futures.append(future)

    def _lyrics_task(audio_path: Path) -> bool:
        if audio_path.with_suffix(".lrc").exists():
            return True
        found = fetch_lyrics(audio_path, dashboard)
        dashboard.record_lyrics(found)
        return found

    def postprocess_worker() -> None:
        while True:
            job = queue.get()
            try:
                if job is None:
                    return
                info, raw_path = job
                title = _clean_metadata(info.get("title"), str(info.get("id")))
                try:
                    ok, final_path, existed = _postprocess_track(
                        info, raw_path, soundcloud_dir, index, archive, dashboard
                    )
                    if not ok or final_path is None:
                        mark_failure()
                        dashboard.record_track("soundcloud", "failed")
                        continue

                    dashboard.record_track("soundcloud", "skipped" if existed else "done")
                    if not existed:
                        dashboard.log(f"[SoundCloud] Ready: {title}")
                    submit_lyrics(lyrics_pool, final_path)
                except Exception as exc:
                    mark_failure()
                    dashboard.record_track("soundcloud", "failed")
                    dashboard.log_error("SoundCloud", f"Worker failed for '{title}': {exc}")
            finally:
                queue.task_done()

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
                    # id -> file mapping so future runs no longer need the fallback.
                    index.add(info, existing)
                    archive.add(str(info.get("id") or ""))
                    dashboard.record_track("soundcloud", "skipped")
                    dashboard.log(f"[SoundCloud] Already exists: {existing.name}")
                    submit_lyrics(lyrics_pool, existing)
                    continue

                try:
                    ok, raw_path = _download_one(info, staging_dir, dashboard)
                except Exception as exc:
                    ok, raw_path = False, None
                    dashboard.log_error(
                        "SoundCloud",
                        f"Download failed for '{info.get('title', info.get('id'))}': {exc}",
                    )
                if not ok or raw_path is None:
                    mark_failure()
                    dashboard.record_track("soundcloud", "failed")
                    continue

                # The producer never waits for ffmpeg/metadata/thumbnail. It
                # only blocks when the bounded queue is full.
                queue.put((info, raw_path))
        finally:
            # Sentinels are sent even if the producer loop raised (e.g.
            # Ctrl-C), otherwise the workers would block on an empty queue
            # forever and the whole program would hang on shutdown.
            for _ in range(worker_count):
                queue.put(None)
            for future in futures:
                try:
                    future.result()
                except Exception as exc:
                    mark_failure()
                    dashboard.log_error("SoundCloud", f"Post-processing worker crashed: {exc}")

            # All post-processing is done, so no further lyrics jobs can be
            # submitted and the list can safely be drained.
            for future in list(lyrics_futures):
                try:
                    future.result()
                except Exception as exc:
                    dashboard.log_error("Lyrics", f"Lyrics worker failed: {exc}")
            dashboard.finish_file()

    _cleanup_staging(staging_dir)
    update_soundcloud_playlist(soundcloud_dir, dashboard)
    return not had_failure


def _cleanup_staging(staging_dir: Path) -> None:
    """Removes the staging directory when nothing is left in it."""
    try:
        for child in staging_dir.iterdir():
            if child.is_dir():
                _cleanup_dir(child)
        staging_dir.rmdir()
    except OSError:
        pass
