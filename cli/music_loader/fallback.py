"""Opt-in fallback for tracks SoundCloud does not give out.

With `--soundcloud-fallback` a DRM-protected, preview-only or blocked track is
looked up on YouTube Music instead of only being skipped. This is a different
recording or master from a different source, so it is off by default and a
result is only taken when it is verifiably the same song:

* the title is the same after removing "(feat. ...)", promo noise and
  remaster notes, and the version markers agree ("Sped Up", "Remix", "Live",
  "Instrumental" ... on both sides or on neither);
* every one of the track's main artists is among the result's artists;
* the length is within `FALLBACK_DURATION_TOLERANCE_SECONDS` of SoundCloud's.

Nothing here touches the protection of the original: the SoundCloud stream is
never downloaded, decrypted or worked around. A result that fails any check is
simply not used. After the download the usual length check against SoundCloud's
duration still applies, and the file's tags record where it came from.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlencode

from .config import FALLBACK_DURATION_TOLERANCE_SECONDS, FALLBACK_MAX_CANDIDATES
from .process import run_streamed, ytdlp_extra_args
from .text_utils import normalize_name, split_artist_names, strip_variants, title_key, variant_markers

SOURCE_LABEL = "YouTube Music"
_WATCH_URL = "https://music.youtube.com/watch?v={}"
_SEARCH_URL = "https://music.youtube.com/search?{}#songs"
_VIDEO_ID_RE = re.compile(r"^[0-9A-Za-z_-]{6,20}$")
_TOPIC_SUFFIX_RE = re.compile(r"\s+-\s+topic$", re.IGNORECASE)
_LOOKUP_TIMEOUT = 300


@dataclass(frozen=True)
class Wanted:
    """The track to find: the clean title and main artists as they are
    tagged, and SoundCloud's length in seconds."""
    title: str
    artists: tuple[str, ...]
    duration: float


@dataclass(frozen=True)
class Candidate:
    video_id: str
    title: str
    artists: tuple[str, ...]
    duration: float | None

    @property
    def url(self) -> str:
        return _WATCH_URL.format(self.video_id)


def candidate_from_info(info: Any) -> Candidate | None:
    """A yt-dlp info dict of a YouTube Music page as a candidate."""
    if not isinstance(info, dict):
        return None
    video_id = str(info.get("id") or "")
    if not _VIDEO_ID_RE.match(video_id):
        return None
    title = str(info.get("track") or info.get("title") or "")
    artists: list[str] = []
    raw = info.get("artists")
    if isinstance(raw, list):
        for item in raw:
            artists.extend(split_artist_names(str(item)))
    elif info.get("artist"):
        artists.extend(split_artist_names(str(info["artist"])))
    if not artists:
        # An auto-generated "<Artist> - Topic" channel is the artist; any
        # other uploader is not necessarily one.
        channel = str(info.get("channel") or info.get("uploader") or "")
        if _TOPIC_SUFFIX_RE.search(channel):
            artists.extend(split_artist_names(_TOPIC_SUFFIX_RE.sub("", channel)))
    try:
        duration = float(info["duration"]) if info.get("duration") else None
    except (TypeError, ValueError):
        duration = None
    return Candidate(video_id, title, tuple(artists), duration)


def check_candidate(wanted: Wanted, candidate: Candidate) -> str | None:
    """None when the candidate is the wanted song, else why it is not."""
    wanted_key = title_key(strip_variants(wanted.title))
    if not wanted_key:
        return "no title to compare"
    if title_key(strip_variants(candidate.title)) != wanted_key:
        return "different title"
    if variant_markers(candidate.title) != variant_markers(wanted.title):
        return "different version"
    if title_key(candidate.title) != title_key(wanted.title):
        return "different title"
    own = {normalize_name(name) for name in wanted.artists if normalize_name(name)}
    found = {normalize_name(name) for name in candidate.artists if normalize_name(name)}
    if not found:
        return "artist unknown"
    if not own or not own <= found:
        return "different artist"
    if candidate.duration is None:
        return "length unknown"
    if wanted.duration <= 0:
        return "SoundCloud length unknown"
    if abs(candidate.duration - wanted.duration) > FALLBACK_DURATION_TOLERANCE_SECONDS:
        return "different length"
    return None


def search_url(wanted: Wanted) -> str:
    query = " ".join([wanted.artists[0] if wanted.artists else "", wanted.title]).strip()
    return _SEARCH_URL.format(urlencode({"q": query}))


def _json_run(args: list[str], yt: list[str], abort: Callable[[], bool]) -> dict[str, Any] | None:
    lines: list[str] = []
    code = run_streamed(
        yt + ytdlp_extra_args() + args,
        lambda line: lines.append(line) if line.startswith("{") else None,
        timeout=_LOOKUP_TIMEOUT, should_abort=abort,
    )
    if code != 0 or not lines:
        return None
    try:
        data = json.loads(lines[-1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def find_match(
    wanted: Wanted,
    yt: list[str],
    abort: Callable[[], bool],
) -> tuple[Candidate | None, int]:
    """Searches YouTube Music for `wanted`. Returns the first verified
    candidate (or None) and how many results were looked at."""
    listing = _json_run(
        ["--flat-playlist", "--dump-single-json", "--no-warnings",
         "--playlist-end", str(FALLBACK_MAX_CANDIDATES), "--", search_url(wanted)],
        yt, abort,
    )
    entries = [item for item in (listing or {}).get("entries") or [] if isinstance(item, dict)]
    checked = 0
    for entry in entries[:FALLBACK_MAX_CANDIDATES]:
        if abort():
            return None, checked
        video_id = str(entry.get("id") or "")
        if not _VIDEO_ID_RE.match(video_id):
            continue
        checked += 1
        info = _json_run(
            ["--no-playlist", "--dump-single-json", "--skip-download", "--no-warnings",
             "--", _WATCH_URL.format(video_id)],
            yt, abort,
        )
        candidate = candidate_from_info(info)
        if candidate is not None and check_candidate(wanted, candidate) is None:
            return candidate, checked
    return None, checked
