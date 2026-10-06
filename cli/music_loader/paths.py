"""File-system helpers: safe path components and moving files together with
their sidecar files (the `.lrc` lyrics)."""
from __future__ import annotations

import os
import re
from pathlib import Path

try:
    from yt_dlp.utils import sanitize_filename
except ImportError:  # pragma: no cover - dependency is declared in pyproject
    sanitize_filename = None

_UNSAFE_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10)),
}
SIDECAR_SUFFIXES = (".lrc",)


def safe_component(text: str, fallback: str = "Unknown", max_bytes: int = 180) -> str:
    """One file or folder name that is valid on Linux, Windows and the
    FAT/exFAT cards phones use, and short enough for every file system."""
    value = (text or "").replace("\x00", "").strip()
    if sanitize_filename is not None:
        value = sanitize_filename(value, restricted=False)
    value = _UNSAFE_RE.sub("_", value)
    value = re.sub(r"\s+", " ", value).strip().strip(".").strip()
    if not value:
        value = fallback
    if value.split(".")[0].casefold() in _WINDOWS_RESERVED:
        value = f"_{value}"
    encoded = value.encode("utf-8")
    if len(encoded) > max_bytes:
        value = encoded[:max_bytes].decode("utf-8", "ignore").rstrip(" .")
    return value or fallback


def sidecars(path: Path) -> list[Path]:
    return [path.with_suffix(suffix) for suffix in SIDECAR_SUFFIXES]


def move_with_sidecars(source: Path, target: Path) -> None:
    """Moves an audio file and its `.lrc` to a new place (same library)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, target)
    for old, new in zip(sidecars(source), sidecars(target)):
        if old.exists():
            try:
                os.replace(old, new)
            except OSError:
                pass


def remove_with_sidecars(path: Path) -> None:
    for item in [path, *sidecars(path)]:
        try:
            item.unlink(missing_ok=True)
        except OSError:
            pass


def prune_empty_dirs(start: Path, stop: Path) -> None:
    """Removes now-empty folders from `start` up to (not including) `stop`."""
    try:
        current = start.resolve()
        stop = stop.resolve()
    except OSError:
        return
    while current != stop and stop in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent
