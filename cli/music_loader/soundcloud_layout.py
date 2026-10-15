"""Moves a SoundCloud library from the old layout to the current one.

    old:  SoundCloud/<album artist> - <album>/<NN> - <title>.mp3
    new:  SoundCloud/<album artist>/<album>/<NN> - <title>.mp3

The current layout never has audio files directly in a first-level folder,
so a track there is in the old layout. It is moved when music-loader wrote
it (its SoundCloud id tag, or an entry in `.sc_index.json`) and it has the
album and album artist tags the new folder is built from; anything else is
left alone. The file keeps its name; its `.lrc` moves with it, index entries
and `.m3u8` playlist entries follow it, and old folders left empty are
removed.

Every move is written to a journal (`.sc_layout_migration.json`) before the
first file is touched and the journal is deleted last, so an interrupted
migration finishes on the next run without losing a file, and a library in
the current layout costs one listing of its first-level folders.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import debuglog
from .config import AUDIO_EXTENSIONS
from .naming import album_path
from .paths import prune_empty_dirs, sidecars
from .playlist import apply_moves
from .soundcloud_index import SoundCloudIndex
from .tags import MUTAGEN_AVAILABLE, read_tags

JOURNAL_FILENAME = ".sc_layout_migration.json"


def _old_layout_files(root: Path) -> list[Path]:
    """Audio files directly inside a first-level folder (hidden ones hold
    partial downloads)."""
    found: list[Path] = []
    try:
        folders = sorted(path for path in root.iterdir() if path.is_dir() and not path.name.startswith("."))
    except OSError:
        return found
    for folder in folders:
        try:
            entries = sorted(folder.iterdir())
        except OSError:
            continue
        for path in entries:
            try:
                if path.suffix.lower() in AUDIO_EXTENSIONS and path.is_file():
                    found.append(path)
            except OSError:
                continue
    return found


def _free_name(target: Path, track_id: str, taken: set[Path]) -> Path:
    def busy(path: Path) -> bool:
        return path in taken or path.exists()

    if not busy(target):
        return target
    base = f"{target.stem} [{track_id}]" if track_id else target.stem
    candidate = target.with_name(f"{base}{target.suffix}")
    n = 2
    while busy(candidate):
        candidate = target.with_name(f"{base} ({n}){target.suffix}")
        n += 1
    return candidate


def plan_moves(root: Path, indexed: set[Path]) -> list[tuple[Path, Path]]:
    """(old, new) for every track still in the old layout."""
    if not MUTAGEN_AVAILABLE:
        return []
    moves: list[tuple[Path, Path]] = []
    taken: set[Path] = set()
    for path in _old_layout_files(root):
        tags = read_tags(path)
        if not tags["soundcloud_id"] and path not in indexed:
            continue
        album_artist = tags["album_artist"] or (tags["artists"][0] if tags["artists"] else "")
        if not album_artist or not tags["album"]:
            continue
        target = _free_name(root / album_path(album_artist, tags["album"]) / path.name,
                            tags["soundcloud_id"], taken)
        taken.add(target)
        moves.append((path, target))
    return moves


# -- journal ---------------------------------------------------------------------------------------
def _read_journal(path: Path, root: Path) -> list[tuple[Path, Path]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    moves = []
    for item in raw.get("moves", []) if isinstance(raw, dict) else []:
        if isinstance(item, list) and len(item) == 2 and all(isinstance(part, str) for part in item):
            moves.append((root / item[0], root / item[1]))
    return moves


def _write_journal(path: Path, root: Path, moves: list[tuple[Path, Path]]) -> None:
    data: dict[str, Any] = {
        "moves": [[old.relative_to(root).as_posix(), new.relative_to(root).as_posix()] for old, new in moves],
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


# -- moving ----------------------------------------------------------------------------------------
def _move(old: Path, new: Path) -> bool:
    """Moves the audio file and its sidecars that are not moved yet. True
    when the audio file is at its new place afterwards. Never overwrites."""
    if old.exists() and not new.exists():
        new.parent.mkdir(parents=True, exist_ok=True)
        os.replace(old, new)
    if not new.exists():
        return False
    for old_side, new_side in zip(sidecars(old), sidecars(new)):
        if old_side.exists() and not new_side.exists():
            try:
                os.replace(old_side, new_side)
            except OSError:
                pass
    return True


def _apply(index: SoundCloudIndex, moves: list[tuple[Path, Path]], dashboard) -> list[tuple[Path, Path]]:
    root = index.dir
    done: list[tuple[Path, Path]] = []
    for old, new in moves:
        try:
            if _move(old, new):
                done.append((old, new))
            elif old.exists():
                dashboard.log_error("SoundCloud", f"Could not move '{old.relative_to(root)}' "
                                                  f"to '{new.relative_to(root)}': the target exists")
        except OSError as exc:
            dashboard.log_error("SoundCloud", f"Could not move '{old.relative_to(root)}': {exc}")
    if done:
        index.relocate(dict(done))
        index.flush()
        apply_moves(root, done, dashboard)
    for folder in sorted({old.parent for old, _ in moves}):
        prune_empty_dirs(folder, root)
    return done


def migrate_layout(index: SoundCloudIndex, dashboard) -> int:
    """Brings the library under `index.dir` into the current layout.
    Returns the number of tracks moved; a second run moves nothing."""
    root = index.dir
    journal = root / JOURNAL_FILENAME
    moved: list[tuple[Path, Path]] = []
    pending = _read_journal(journal, root)
    if pending:
        moved += _apply(index, pending, dashboard)
    planned = plan_moves(root, index.known_paths())
    if planned:
        try:
            _write_journal(journal, root, planned)
        except OSError as exc:
            # Without a journal an interruption could leave the index behind.
            dashboard.log_error("SoundCloud", f"Library layout not updated, could not write '{journal.name}': {exc}")
            return len(moved)
        moved += _apply(index, planned, dashboard)
    try:
        journal.unlink(missing_ok=True)
    except OSError:
        pass
    if moved:
        dashboard.log(f"[SoundCloud] Library layout: moved {len(moved)} track(s) "
                      "to '<album artist>/<album>' folders")
        debuglog.event("soundcloud", "layout_migration", moves=[
            {"from": old.relative_to(root).as_posix(), "to": new.relative_to(root).as_posix()}
            for old, new in moved
        ])
    return len(moved)
