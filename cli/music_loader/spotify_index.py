"""Spotify URL -> files in the library, read from the URL spotDL embeds in
every file it finishes (the WOAS tag).

Used to find a track that already exists under another path - a folder
layout of an older version, a renamed album - and move it to the current
path instead of downloading it again, and to spot duplicate copies.

Reading tags of a large library is slow, so results are cached in
`.spotify_index.json` per file (size + modification time) and the scan only
happens when it is actually needed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .config import AUDIO_EXTENSIONS, SPOTIFY_INDEX_FILENAME
from .tags import read_woas


class SpotifyLibrary:
    def __init__(self, music_dir: Path, exclude: list[Path]):
        self.music_dir = music_dir
        self.exclude = [path.resolve() for path in exclude]
        self.cache_path = music_dir / SPOTIFY_INDEX_FILENAME
        self._by_url: dict[str, list[Path]] | None = None
        self._cache: dict[str, list] = {}

    def _load_cache(self) -> dict[str, list]:
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _walk(self):
        for root, dirs, files in os.walk(self.music_dir):
            root_path = Path(root)
            dirs[:] = [
                name for name in dirs
                if not name.startswith(".") and (root_path / name).resolve() not in self.exclude
            ]
            for name in files:
                if Path(name).suffix.lower() in AUDIO_EXTENSIONS and not name.startswith("."):
                    yield root_path / name

    def _build(self) -> None:
        old = self._load_cache()
        cache: dict[str, list] = {}
        by_url: dict[str, list[Path]] = {}
        for path in self._walk():
            try:
                stat = path.stat()
                relative = path.relative_to(self.music_dir).as_posix()
            except (OSError, ValueError):
                continue
            entry = old.get(relative)
            if isinstance(entry, list) and len(entry) == 3 and entry[0] == stat.st_mtime_ns and entry[1] == stat.st_size:
                url = str(entry[2])
            else:
                url = read_woas(path)
            cache[relative] = [stat.st_mtime_ns, stat.st_size, url]
            if url.startswith("https://open.spotify.com/"):
                by_url.setdefault(url, []).append(path)
        self._cache = cache
        self._by_url = by_url
        self._save()

    def _save(self) -> None:
        tmp = self.cache_path.with_name(self.cache_path.name + ".tmp")
        try:
            tmp.write_text(json.dumps(self._cache, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.cache_path)
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def find(self, url: str) -> list[Path]:
        if self._by_url is None:
            self._build()
        return [path for path in (self._by_url or {}).get(url, []) if path.exists()]

    def moved(self, source: Path, target: Path, url: str) -> None:
        if self._by_url is None:
            return
        paths = [path for path in self._by_url.get(url, []) if path != source]
        paths.append(target)
        self._by_url[url] = paths

    def removed(self, path: Path, url: str) -> None:
        if self._by_url is None:
            return
        self._by_url[url] = [item for item in self._by_url.get(url, []) if item != path]


_LIBRARIES: dict[Path, SpotifyLibrary] = {}


def get_library(music_dir: Path, exclude: list[Path]) -> SpotifyLibrary:
    key = music_dir.resolve()
    library = _LIBRARIES.get(key)
    if library is None:
        library = SpotifyLibrary(key, exclude)
        _LIBRARIES[key] = library
    return library
