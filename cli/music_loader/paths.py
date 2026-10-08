"""File-system helpers: moving files together with their sidecar files (the
`.lrc` lyrics). File and folder names come from naming.py."""
from __future__ import annotations

import os
from pathlib import Path

SIDECAR_SUFFIXES = (".lrc",)


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
