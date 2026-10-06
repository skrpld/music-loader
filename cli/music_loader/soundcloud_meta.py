"""Turns SoundCloud metadata into tags, folders and file names.

Rules
-----
* Artist: SoundCloud's own artist field (set for label releases) ->
  "Artist - Song" from the title -> the uploader. Featured artists from
  "(feat. X)", "ft. X", "w/ X" are added after the main artists (Spotify
  does the same), and every name is written in one canonical spelling.
* Title: promotional noise ("[Free DL]", "*music video in description*"),
  producer credits and the leading "09." are removed; "(feat. ...)" is
  normalized; version markers ("(Sped Up)", "hexd", "(Remix)") stay.
* Album: the album/EP/single set the track belongs to (title, owner as album
  artist, position as track number); everything else is its own single,
  "<song> - Single", by the main artist.
* Folder: "<album artist> - <album>/<NN> - <title>.mp3", a single included.
* Cover: the track's artwork, else the album's; never the uploader's avatar
  (yt-dlp offers the avatar when a track has no artwork of its own).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .artists import PRIORITY_TITLE, PRIORITY_UPLOADER, ArtistRegistry
from .config import SINGLE_ALBUM_SUFFIX
from .links import redact_url
from .paths import safe_component
from .process import module_available
from .tags import TrackTags
from .text_utils import clean_promo, normalize_name, parse_soundcloud_title, split_artist_names, strip_decorations

ALBUM_SET_TYPES = {"album", "ep", "single", "compilation"}
_SET_TYPE_RANK = {"album": 0, "ep": 1, "compilation": 2, "single": 3}
_AVATAR_MARKER = "avatars-"


@dataclass
class AlbumContext:
    title: str
    artist: str
    set_type: str
    position: int = 1
    total: int = 1
    release_timestamp: Any = None      # unix time or an ISO date string
    genre: str = ""
    artwork_url: str = ""
    set_id: str = ""


@dataclass
class TrackMeta:
    tags: TrackTags
    folder: str
    filename: str
    main_artists: list[str]
    featured: list[str]
    title: str
    album: str
    artwork_urls: list[str]


# -- artwork -------------------------------------------------------------------------
def is_avatar(url: str) -> bool:
    return _AVATAR_MARKER in (url or "").lower()


def pick_artwork_url(info: dict[str, Any]) -> str:
    """Best real artwork of a track or set; never the uploader's avatar."""
    best_url, best_score = "", (-1, -1)
    for thumb in info.get("thumbnails") or []:
        if not isinstance(thumb, dict):
            continue
        url = str(thumb.get("url") or "")
        if not url.startswith("https://") or is_avatar(url):
            continue
        try:
            preference = int(thumb.get("preference") or 0)
        except (TypeError, ValueError):
            preference = 0
        try:
            width = int(thumb.get("width") or 0)
        except (TypeError, ValueError):
            width = 0
        if (preference, width) > best_score:
            best_score, best_url = (preference, width), url
    if best_url:
        return best_url
    url = str(info.get("thumbnail") or "")
    return url if url.startswith("https://") and not is_avatar(url) else ""


def _artwork_from_api(url: str | None) -> str:
    """API artwork URLs point at a 100px variant; ask for 500px instead."""
    url = str(url or "")
    if not url.startswith("https://") or is_avatar(url):
        return ""
    return url.replace("-large.", "-t500x500.")


# -- dates / genre ---------------------------------------------------------------------
def _date(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc).strftime("%Y-%m-%d")
        text = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(text).strftime("%Y-%m-%d")
    except (ValueError, OverflowError, OSError):
        return ""


def _genre(info: dict[str, Any]) -> str:
    genres = info.get("genres") or ([info["genre"]] if info.get("genre") else [])
    for genre in genres:
        value = strip_decorations(str(genre).lstrip("#"))
        if value:
            return value
    return ""


# -- album lookup ------------------------------------------------------------------------
class _QuietLogger:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


class SoundCloudApi:
    """Best-effort "which album is this track on" lookup through yt-dlp's
    SoundCloud client (same client id handling and cache as the yt-dlp
    command line). Only used for tracks whose link did not say it: a single
    track link, a playlist, likes, reposts.

    Every failure simply means "no album known"; repeated failures switch
    the lookup off for the rest of the run so it never costs more than a few
    requests of SoundCloud's rate limit (~600 requests per 10 minutes).
    """

    def __init__(self) -> None:
        self.disabled = not module_available("yt_dlp")
        self._local = threading.local()
        self._lock = threading.Lock()
        self._albums: dict[str, AlbumContext | None] = {}
        self._playlists: dict[str, dict | None] = {}
        self._failures = 0
        self._successes = 0

    def _extractor(self):
        ie = getattr(self._local, "ie", None)
        if ie is None:
            from yt_dlp import YoutubeDL
            ydl = YoutubeDL({
                "quiet": True, "no_warnings": True, "noprogress": True,
                "logger": _QuietLogger(), "extractor_retries": 1,
            })
            ie = ydl.get_info_extractor("Soundcloud")
            ie.initialize()
            self._local.ydl, self._local.ie = ydl, ie
        return ie

    def _call(self, path: str, item_id: str, query: dict) -> Any:
        ie = self._extractor()
        return ie._call_api(
            ie._API_V2_BASE + path, item_id, note=False, errnote=False,
            query=query, headers=ie._HEADERS,
        )

    def _record(self, ok: bool) -> None:
        with self._lock:
            if ok:
                self._successes += 1
                self._failures = 0
            else:
                self._failures += 1
                if self._failures >= 3:
                    self.disabled = True

    def _playlist(self, playlist_id: str) -> dict | None:
        with self._lock:
            if playlist_id in self._playlists:
                return self._playlists[playlist_id]
        try:
            data = self._call(f"playlists/{playlist_id}", playlist_id, {"representation": "full"})
            self._record(True)
        except Exception:
            self._record(False)
            data = None
        data = data if isinstance(data, dict) else None
        with self._lock:
            self._playlists[playlist_id] = data
        return data

    def album_for(self, track_id: str, uploader: str) -> AlbumContext | None:
        if self.disabled or not track_id:
            return None
        with self._lock:
            if track_id in self._albums:
                return self._albums[track_id]
        try:
            data = self._call(f"tracks/{track_id}/albums", track_id,
                              {"limit": "10", "linked_partitioning": "1"})
            self._record(True)
        except Exception:
            self._record(False)
            return None
        collection = data.get("collection") if isinstance(data, dict) else None
        albums = [
            item for item in collection or []
            if isinstance(item, dict) and str(item.get("set_type") or "").lower() in ALBUM_SET_TYPES
        ]
        context = None
        if albums:
            owner = normalize_name(uploader)

            def rank(item: dict) -> tuple:
                user = item.get("user") or {}
                owned = normalize_name(str(user.get("username") or "")) == owner
                return (0 if owned else 1, _SET_TYPE_RANK.get(str(item.get("set_type")).lower(), 9))

            chosen = sorted(albums, key=rank)[0]
            context = self._context(chosen, track_id)
        with self._lock:
            self._albums[track_id] = context
        return context

    def _context(self, album: dict, track_id: str) -> AlbumContext | None:
        playlist_id = str(album.get("id") or "")
        full = self._playlist(playlist_id) if playlist_id else None
        source = full or album
        track_ids = [str(item.get("id")) for item in source.get("tracks") or [] if isinstance(item, dict)]
        total = len(track_ids) or int(source.get("track_count") or 1)
        position = track_ids.index(track_id) + 1 if track_id in track_ids else 1
        title = str(source.get("title") or "")
        if not title:
            return None
        return AlbumContext(
            title=title,
            artist=str((source.get("user") or {}).get("username") or ""),
            set_type=str(source.get("set_type") or "album").lower(),
            position=position,
            total=max(total, position),
            release_timestamp=(
                source.get("release_date") or source.get("published_at") or source.get("display_date")
            ),
            genre=str(source.get("genre") or ""),
            artwork_url=_artwork_from_api(source.get("artwork_url")),
            set_id=playlist_id,
        )


# -- tags ------------------------------------------------------------------------------------
def official_artists(info: dict[str, Any]) -> list[str]:
    """SoundCloud's structured artist credit (publisher metadata), if any."""
    raw = info.get("artists")
    if not raw and isinstance(info.get("artist"), str):
        raw = [info["artist"]]
    names: list[str] = []
    for item in raw or []:
        names.extend(split_artist_names(str(item)))
    return names


def build_meta(
    info: dict[str, Any],
    album: AlbumContext | None,
    registry: ArtistRegistry,
) -> TrackMeta:
    track_id = str(info.get("id") or "")
    uploader = strip_decorations(info.get("uploader") or "")
    if uploader:
        registry.register(uploader, PRIORITY_UPLOADER)
    official = official_artists(info)
    for name in official:
        registry.register(name, PRIORITY_UPLOADER)

    parsed = parse_soundcloud_title(
        str(info.get("title") or ""), uploader, official, registry.is_known, registry.canonical,
    )
    for name in parsed.artists + parsed.featured:
        registry.register(name, PRIORITY_TITLE)
    main = parsed.artists or ["Unknown Artist"]
    featured = parsed.featured
    title = parsed.title or str(info.get("title") or track_id or "Unknown Title")

    artwork_urls = [url for url in [pick_artwork_url(info)] if url]
    if album is not None:
        album_title = clean_promo(album.title) or album.title
        if album.set_type == "single" and not album_title.casefold().endswith("single"):
            album_title = f"{album_title}{SINGLE_ALBUM_SUFFIX}"
        owner = strip_decorations(album.artist)
        if owner:
            registry.register(owner, PRIORITY_UPLOADER)
        album_artist = registry.canonical(owner) if owner else main[0]
        number, total = max(album.position, 1), max(album.total, album.position, 1)
        release = info.get("release_timestamp") or album.release_timestamp or info.get("timestamp")
        genre = _genre(info) or strip_decorations(album.genre.lstrip("#"))
        if album.artwork_url and album.artwork_url not in artwork_urls:
            artwork_urls.append(album.artwork_url)
    else:
        album_title = f"{parsed.base_title or title}{SINGLE_ALBUM_SUFFIX}"
        album_artist = main[0]
        number, total = 1, 1
        release = info.get("release_timestamp") or info.get("timestamp")
        genre = _genre(info)

    tags = TrackTags(
        title=title,
        artists=main + featured,
        album=album_title,
        album_artist=album_artist,
        track_number=number,
        track_total=total,
        date=_date(release),
        genre=genre,
        url=redact_url(str(info.get("webpage_url") or "")),
        soundcloud_id=track_id,
    )
    return TrackMeta(
        tags=tags,
        folder=safe_component(f"{album_artist} - {album_title}"),
        filename=safe_component(f"{number:02d} - {title}") + ".mp3",
        main_artists=main,
        featured=featured,
        title=title,
        album=album_title,
        artwork_urls=artwork_urls,
    )


def context_from_set(data: dict[str, Any], position: int, total: int) -> AlbumContext | None:
    """Album context for a track listed in an album-type set."""
    entries = data.get("entries") or []
    first = entries[0] if entries and isinstance(entries[0], dict) else {}
    set_type = str(data.get("album_type") or first.get("album_type") or "").lower()
    if set_type not in ALBUM_SET_TYPES:
        return None
    title = str(data.get("album") or first.get("album") or data.get("title") or "")
    if not title:
        return None
    return AlbumContext(
        title=title,
        artist=str(data.get("album_artist") or first.get("album_artist") or data.get("uploader") or ""),
        set_type=set_type,
        position=position,
        total=total,
        release_timestamp=data.get("release_timestamp") or data.get("timestamp"),
        genre=_genre(data),
        artwork_url=pick_artwork_url(data),
        set_id=str(data.get("id") or ""),
    )
