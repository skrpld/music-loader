"""Spotify URL -> files in the library, read from the URL spotDL embeds in
every file it finishes (the WOAS tag).

Used to find a track that already exists under another path - a folder
layout of an older version, a renamed album - and move it to the current
path instead of downloading it again, and to spot duplicate copies.

A file without a Spotify URL (another tool, a copy without its tags) is
recognised by its artist, title and length (`find_match`: the same main
artist and title, length within 2 s of a song of the link); the caller then
binds the URL and id into it (tags.bind_ids).

Reading tags of a large library is slow, so results are cached in
`.spotify_index.json` per file (size + modification time, plus title,
artists and length for a file without a URL) and the scan only happens when
it is actually needed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .config import AUDIO_EXTENSIONS, SPOTIFY_INDEX_FILENAME
from .tags import audio_duration, read_tags, read_woas
from .text_utils import normalize_name

_SPOTIFY_PREFIX = "https://open.spotify.com/"
# A file without a URL matches a song when the lengths differ by at most this.
MATCH_TOLERANCE_SECONDS = 2.0


class SpotifyLibrary:
    def __init__(self, music_dir: Path, exclude: list[Path]):
        self.music_dir = music_dir
        self.exclude = [path.resolve() for path in exclude]
        self.cache_path = music_dir / SPOTIFY_INDEX_FILENAME
        self._by_url: dict[str, list[Path]] | None = None
        # Files without a Spotify URL: (path, title key, artist keys, length).
        self._prints: list[tuple[Path, str, set[str], float | None]] = []
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
        prints: list[tuple[Path, str, set[str], float | None]] = []
        for path in self._walk():
            try:
                stat = path.stat()
                relative = path.relative_to(self.music_dir).as_posix()
            except (OSError, ValueError):
                continue
            entry = old.get(relative)
            fresh = isinstance(entry, list) and len(entry) >= 3 \
                and entry[0] == stat.st_mtime_ns and entry[1] == stat.st_size
            url = str(entry[2]) if fresh else read_woas(path)
            if url.startswith(_SPOTIFY_PREFIX):
                cache[relative] = [stat.st_mtime_ns, stat.st_size, url]
                by_url.setdefault(url, []).append(path)
                continue
            if fresh and len(entry) == 6:
                title, artists, duration = entry[3], entry[4], entry[5]
            else:
                tags = read_tags(path)
                title, artists, duration = tags["title"], tags["artists"], audio_duration(path)
            cache[relative] = [stat.st_mtime_ns, stat.st_size, url, title, artists, duration]
            if title and artists:
                prints.append((path, normalize_name(str(title)),
                               {normalize_name(str(name)) for name in artists}, _seconds(duration)))
        self._cache = cache
        self._by_url = by_url
        self._prints = prints
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

    def find_match(self, artist: str, titles: list[str], duration) -> Path | None:
        """A file without a Spotify URL that is this song: `artist` among its
        artists, one of `titles` as its title, the length within 2 s (both
        lengths known)."""
        if self._by_url is None:
            self._build()
        artist_key = normalize_name(artist)
        title_keys = {normalize_name(title) for title in titles if title}
        length = _seconds(duration)
        if not artist_key or not title_keys or length is None:
            return None
        for path, title, artists, other in self._prints:
            if title in title_keys and artist_key in artists and other is not None \
                    and abs(other - length) <= MATCH_TOLERANCE_SECONDS and path.exists():
                return path
        return None

    def bound(self, path: Path, url: str) -> None:
        """`path` got the URL of a song written into it."""
        self._prints = [item for item in self._prints if item[0] != path]
        if self._by_url is not None:
            self._by_url.setdefault(url, []).append(path)

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


def _seconds(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


_LIBRARIES: dict[Path, SpotifyLibrary] = {}


def get_library(music_dir: Path, exclude: list[Path]) -> SpotifyLibrary:
    key = music_dir.resolve()
    library = _LIBRARIES.get(key)
    if library is None:
        library = SpotifyLibrary(key, exclude)
        _LIBRARIES[key] = library
    return library
