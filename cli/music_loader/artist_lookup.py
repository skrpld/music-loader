"""Where accounts.py looks a song up: the user's own library, Spotify (through
spotDL), Deezer's public search and SoundCloud's own search.

Every lookup returns `Hit`s (a song with its artists and length) and raises
`ServiceUnavailable` when the service did not answer (no network, blocked,
rate-limited): the caller then leaves the decision for a later run instead
of reading "nothing found" into it. A service that failed once is not asked
again in the same run.

Only accounts with a candidate name (and the tracks of reupload accounts)
are ever looked up; see accounts.py for when.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import debuglog
from .config import AUDIO_EXTENSIONS, STAGING_DIRNAME, AppConfig
from .net import http_get
from .process import run_captured, tool_command
from .tags import audio_duration, read_tags
from .naming import spotify_title
from .text_utils import collapse, latin_key, lyrics_clean_title

SOURCE_LIBRARY = "library"
SOURCE_SPOTIFY = "spotify"
SOURCE_DEEZER = "deezer"
SOURCE_SOUNDCLOUD = "soundcloud"

SOURCE_LABELS = {
    SOURCE_LIBRARY: "your library",
    SOURCE_SPOTIFY: "Spotify",
    SOURCE_DEEZER: "Deezer",
    SOURCE_SOUNDCLOUD: "SoundCloud",
}

DEEZER_SEARCH_URL = "https://api.deezer.com/search"
_SPOTIFY_TIMEOUT = 180
# A file name is "<NN> - <title>" or "<D>-<NN> - <title>" (naming.py).
_FILE_NUMBER_RE = re.compile(r"^\s*(?:\d+-)?\d+\s*-\s+")
# Text that would make spotDL read a search as something else: a list
# ("album: ..."), a saved file, a link.
_QUERY_UNSAFE_RE = re.compile(r"[:|/\\]|\.spotdl\b|https?", re.IGNORECASE)


class ServiceUnavailable(Exception):
    """The service did not answer; nothing can be concluded from it."""


@dataclass
class Hit:
    title: str
    artists: list[str]
    duration: float | None
    source: str
    # SoundCloud only: who uploaded it and when (epoch seconds).
    account_id: str = ""
    account_name: str = ""
    timestamp: float | None = None
    soundcloud_id: str = ""
    # The library only: whether the file came from Spotify.
    spotify: bool = False
    info: dict[str, Any] = field(default_factory=dict)


# Any bracket or "*...*" part: credits, promo, versions. The length check
# keeps a different version of the same song apart.
_GROUP_RE = re.compile(r"\([^()]*\)|\[[^\[\]]*\]|\{[^{}]*\}|\*[^*]*\*")


def search_title(title: str | None) -> str:
    """The bare song name to search for and compare: no feat or producer
    credits, no promo, no bracketed or " - Remastered" style version parts
    ("ALONE (glorykeyz & aquagwap)" -> "ALONE")."""
    text = lyrics_clean_title(spotify_title(title))
    previous = None
    while previous != text:
        previous, text = text, _GROUP_RE.sub(" ", text)
    return collapse(text).strip(" -") or collapse(title)


def match_key(title: str | None) -> str:
    """Comparison key of a song title (`search_title`), Cyrillic
    transliterated ("Шишки" == "Shishki")."""
    return latin_key(search_title(title))


def same_song(hit: Hit, key: str, duration: float | None, tolerance: float) -> bool:
    if not key or match_key(hit.title) != key:
        return False
    if duration is None or hit.duration is None:
        return False
    return abs(float(hit.duration) - float(duration)) <= tolerance


def _safe_query(text: str) -> str:
    # A leading "-" would make spotDL read the query as an option.
    return re.sub(r"\s+", " ", _QUERY_UNSAFE_RE.sub(" ", text)).strip().lstrip("-").strip()


# -- the user's library -----------------------------------------------------------------
class LibraryCatalog:
    """Song titles in the Music folder, from the file names (no tag is read
    until a title matches), built once per run when first needed."""

    def __init__(self, music_dir: Path, soundcloud_dir: Path | None = None):
        self.music_dir = music_dir
        self.soundcloud_dir = soundcloud_dir
        self._lock = threading.Lock()
        self._by_key: dict[str, list[Path]] | None = None

    def _build(self) -> dict[str, list[Path]]:
        by_key: dict[str, list[Path]] = {}
        for root, dirs, files in os.walk(self.music_dir):
            dirs[:] = [name for name in dirs if not name.startswith(".") and name != STAGING_DIRNAME]
            for name in files:
                path = Path(root) / name
                if name.startswith(".") or path.suffix.lower() not in AUDIO_EXTENSIONS:
                    continue
                key = match_key(_FILE_NUMBER_RE.sub("", path.stem))
                if key:
                    by_key.setdefault(key, []).append(path)
        return by_key

    def find(self, title: str) -> list[Hit]:
        key = match_key(title)
        if not key:
            return []
        with self._lock:
            if self._by_key is None:
                self._by_key = self._build()
            paths = list(self._by_key.get(key, []))
        hits: list[Hit] = []
        for path in paths:
            try:
                tags = read_tags(path)
            except Exception:
                continue
            if not tags.get("title") or not tags.get("artists"):
                continue
            hits.append(Hit(
                title=tags["title"], artists=list(tags["artists"]), duration=audio_duration(path),
                source=SOURCE_LIBRARY, soundcloud_id=str(tags.get("soundcloud_id") or ""),
                spotify=str(tags.get("url") or "").startswith("https://open.spotify.com/")
                and not self._in_soundcloud(path),
            ))
        return hits

    def _in_soundcloud(self, path: Path) -> bool:
        if self.soundcloud_dir is None:
            return False
        try:
            return path.resolve().is_relative_to(self.soundcloud_dir.resolve())
        except OSError:
            return False


# -- online services --------------------------------------------------------------------
class OnlineServices:
    """Spotify, Deezer and SoundCloud searches of one run, each answer cached."""

    def __init__(self, config: AppConfig, soundcloud_api: Any = None,
                 soundcloud_gate: Callable[[], None] | None = None,
                 on_rate_limit: Callable[[], None] | None = None):
        self.config = config
        self.soundcloud_api = soundcloud_api
        self.soundcloud_gate = soundcloud_gate
        self.on_rate_limit = on_rate_limit
        self._lock = threading.Lock()
        self._down: set[str] = set()
        self._cache: dict[tuple[str, str], list[Hit]] = {}
        self._spotdl: list[str] | None = None
        self._spotdl_ready = False

    def _cached(self, source: str, query: str, lookup: Callable[[], list[Hit]]) -> list[Hit]:
        with self._lock:
            if source in self._down:
                raise ServiceUnavailable(f"{source} did not answer earlier in this run")
            if (source, query) in self._cache:
                return self._cache[(source, query)]
        try:
            hits = lookup()
        except ServiceUnavailable as exc:
            with self._lock:
                self._down.add(source)
            debuglog.event("accounts", "service_unavailable", service=source, query=query, error=str(exc))
            raise
        with self._lock:
            self._cache[(source, query)] = hits
        debuglog.event("accounts", "search", service=source, query=query, results=[
            {"title": hit.title, "artists": hit.artists, "duration": hit.duration} for hit in hits[:10]
        ])
        return hits

    # Spotify -----------------------------------------------------------------------
    def spotify(self, query: str) -> list[Hit]:
        query = _safe_query(query)
        if not query:
            return []
        return self._cached(SOURCE_SPOTIFY, query, lambda: self._spotify(query))

    def _spotify_command(self) -> tuple[list[str], list[str], dict[str, str]]:
        from .spotify import _BUILTIN_CLIENT_URLS, _OFFICIAL_API_URLS, _command, _credential_args, \
            _unreachable, spotdl_version

        with self._lock:
            if not self._spotdl_ready:
                self._spotdl_ready = True
                spotdl = tool_command("spotdl")
                if spotdl is not None:
                    args, env = _credential_args(self.config, spotdl_version(spotdl), spotdl)
                    if _unreachable(_OFFICIAL_API_URLS if args else _BUILTIN_CLIENT_URLS) is None:
                        self._spotdl = spotdl
                        self._spotdl_args, self._spotdl_env = args, env
            if self._spotdl is None:
                raise ServiceUnavailable("spotdl is missing or Spotify does not answer")
            return _command(self._spotdl), list(self._spotdl_args), dict(self._spotdl_env)

    def _spotify(self, query: str) -> list[Hit]:
        command, args, env = self._spotify_command()
        work = Path(tempfile.mkdtemp(prefix="music-loader-search-"))
        try:
            save_file = work / "search.spotdl"
            code, stdout, stderr = run_captured(
                command + ["save", query, "--save-file", str(save_file)] + args,
                timeout=_SPOTIFY_TIMEOUT, env=env,
            )
            output = f"{stdout}\n{stderr}"
            if code != 0 or not save_file.exists():
                if re.search(r"no results found", output, re.IGNORECASE):
                    return []
                raise ServiceUnavailable(f"spotdl exited with code {code}")
            try:
                songs = json.loads(save_file.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ServiceUnavailable(f"unreadable spotdl answer: {exc}") from exc
        finally:
            shutil.rmtree(work, ignore_errors=True)
        hits: list[Hit] = []
        for song in songs if isinstance(songs, list) else []:
            if not isinstance(song, dict):
                continue
            artists = [str(name) for name in song.get("artists") or [] if str(name).strip()]
            if song.get("name") and artists:
                hits.append(Hit(title=str(song["name"]), artists=artists,
                                duration=_number(song.get("duration")), source=SOURCE_SPOTIFY))
        return hits

    # Deezer ------------------------------------------------------------------------
    def deezer(self, artist: str | None, title: str) -> list[Hit]:
        title = title.replace('"', " ").strip()
        if not title:
            return []
        if artist:
            query = f'artist:"{artist.replace(chr(34), " ").strip()}" track:"{title}"'
        else:
            query = f'track:"{title}"'
        return self._cached(SOURCE_DEEZER, query, lambda: self._deezer(query))

    @staticmethod
    def _deezer(query: str) -> list[Hit]:
        try:
            response = http_get(DEEZER_SEARCH_URL, params={"q": query, "limit": 10}, timeout=15,
                                max_bytes=2 * 1024 * 1024)
        except OSError as exc:
            raise ServiceUnavailable(str(exc)) from exc
        if not response.ok:
            raise ServiceUnavailable(f"HTTP {response.status}")
        try:
            data = response.json()
        except ValueError as exc:
            raise ServiceUnavailable(f"unreadable answer: {exc}") from exc
        if not isinstance(data, dict):
            raise ServiceUnavailable("unexpected answer")
        if data.get("error"):
            # Code 4: quota exceeded; anything else: the search could not run.
            raise ServiceUnavailable(f"Deezer error: {str(data['error'])[:200]}")
        hits: list[Hit] = []
        for item in data.get("data") or []:
            if not isinstance(item, dict):
                continue
            artist = item.get("artist") if isinstance(item.get("artist"), dict) else {}
            name = str(artist.get("name") or "").strip()
            if item.get("title") and name:
                hits.append(Hit(title=str(item["title"]), artists=[name],
                                duration=_number(item.get("duration")), source=SOURCE_DEEZER))
        return hits

    # SoundCloud ----------------------------------------------------------------------
    def soundcloud(self, title: str) -> list[Hit]:
        title = title.strip()
        if not title:
            return []
        return self._cached(SOURCE_SOUNDCLOUD, title, lambda: self._soundcloud(title))

    def _soundcloud(self, title: str) -> list[Hit]:
        from .availability import RATE_LIMIT_RE
        from .soundcloud_meta import info_from_api

        api = self.soundcloud_api
        if api is None or getattr(api, "disabled", False):
            raise ServiceUnavailable("SoundCloud search is not available")
        if self.soundcloud_gate is not None:
            self.soundcloud_gate()
        try:
            found = api.search_tracks(title)
        except Exception as exc:
            if RATE_LIMIT_RE.search(str(exc)) and self.on_rate_limit is not None:
                self.on_rate_limit()
            raise ServiceUnavailable(str(exc)) from exc
        hits: list[Hit] = []
        for item in found:
            info = info_from_api(item)
            if not info["id"] or not info["title"]:
                continue
            hits.append(Hit(
                title=info["title"], artists=[], duration=info["duration"], source=SOURCE_SOUNDCLOUD,
                account_id=info["uploader_id"], account_name=info["uploader"],
                timestamp=info["timestamp"], soundcloud_id=info["id"], info=info,
            ))
        return hits


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
