"""Lyrics lookup with verified matches.

The previous lookup went through `syncedlyrics`, which accepts the first
Genius search hit, the best Lrclib hit without any threshold and fuzzy
"token set" matches elsewhere - and was fed queries down to the bare song
name. A search for "Intro" happily returned somebody else's "Intro".

Every candidate is now checked against the track itself:

strict (default)
    * artist: the provider's artist must be one of the track's artists
      (spaces do not count: "HARUMATSUI" is "haru matsui");
    * title: equal after normalization (case, punctuation, "feat." parts,
      producer and "hosted by" credits in any bracket style, promotional
      noise and remaster notes do not count) and with the same version
      markers - a sped-up, slowed, remixed or live upload never gets the
      lyrics of the original (`original_for_versions` lets a sped-up or
      slowed upload take the original's plain text);
    * synced lyrics: the provider's track length must be within 2 s of the
      file, otherwise the timestamps belong to a different cut;
    * plain lyrics: same checks; Genius has no track length, so its text is
      accepted on an exact artist + title match only.
loose (`--lyrics-loose`)
    * fuzzy artist and title similarity (Cyrillic and Latin spellings
      compared too), version markers and brackets ignored;
    * the length may differ by up to 10 s (`length_tolerance`); when it
      differs by more than 2 s (or the versions differ) only plain text is
      saved, never misaligned timestamps.

A title such as "AQUAKEY - ALONE" uploaded by another account is also read
as artist "AQUAKEY", song "ALONE".

Uploads that cannot have lyrics (instrumentals, DJ sets, beats) are skipped.

Synced lyrics are written next to the audio file as `.lrc` and their text is
also embedded (USLT); plain lyrics are embedded only.

Provider failures never stall a run. A rate limit (HTTP 429) or a server
error is retried after a pause (`Retry-After` when the provider sends one);
a provider that keeps failing pauses for a while and is tried again later
instead of being lost for the whole run. A provider that refuses access
(HTTP 401/403, Musixmatch asking for a captcha) or whose search answers
every query with the same results is switched off for the rest of the run.
A track whose search could not ask every provider is not marked as "not
found", so the next run searches it again.
"""
from __future__ import annotations

import difflib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

from . import debuglog
from .config import (
    LYRICS_MODE_LOOSE,
    LYRICS_MODE_STRICT,
    LYRICS_RETRY_COOLDOWN_SECONDS,
    LYRICS_STRICT_DURATION_TOLERANCE,
    SINGLE_ALBUM_SUFFIX,
    LyricsSettings,
)
from .net import BROWSER_USER_AGENT, USER_AGENT, http_get
from .tags import embed_lyrics, has_embedded_lyrics, remove_lyrics
from .text_utils import (
    collapse,
    latin_key,
    looks_instrumental,
    lyrics_bare_title,
    lyrics_clean_title,
    normalize_name,
    split_artist_names,
    split_title_artist,
    strip_variants,
    title_key,
    variant_markers,
)

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover - dependency is declared in pyproject
    BeautifulSoup = None

_LRC_TIMESTAMP_RE = re.compile(r"\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]")
_LRC_TAG_LINE_RE = re.compile(r"^\[[a-zA-Z#]+:.*\]$")

# Versions with the same words as the original, only played differently:
# with `original_for_versions` strict mode lets them take its plain text.
SAME_WORDS_VERSIONS = frozenset({"sped up", "slowed", "8d", "bass boosted"})


# -- data -----------------------------------------------------------------------
@dataclass
class LyricsRequest:
    key: str                          # stable id for the retry cooldown
    audio_path: Path
    main_artists: list[str]
    title: str
    album: str = ""
    duration: float | None = None     # seconds, from the file itself
    featured: list[str] = field(default_factory=list)
    force: bool = False               # a rewrite (--existing): replace existing lyrics

    @property
    def artists(self) -> list[str]:
        return list(self.main_artists) + list(self.featured)


@dataclass
class Candidate:
    provider: str
    artist: str
    title: str
    duration: float | None = None
    synced: str | None = None
    plain: str | None = None
    instrumental: bool = False


@dataclass
class LyricsOutcome:
    status: str          # synced | plain | instrumental | not_found | skipped | exists | error
    detail: str = ""
    provider: str = ""


# -- text helpers -------------------------------------------------------------------
def synced_to_plain(text: str) -> str:
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if _LRC_TAG_LINE_RE.match(stripped) and not _LRC_TIMESTAMP_RE.match(stripped):
            continue
        lines.append(_LRC_TIMESTAMP_RE.sub("", line).strip())
    return "\n".join(lines).strip()


def _is_synced(text: str | None) -> bool:
    if not text:
        return False
    timed = sum(1 for line in text.splitlines() if _LRC_TIMESTAMP_RE.match(line.strip()))
    return timed >= 3


def _similar(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _compact(name: str) -> str:
    return name.replace(" ", "")


# -- readings of a track -------------------------------------------------------------
@dataclass(frozen=True)
class Reading:
    """One way to read a track's artist and title: as tagged, or "AQUAKEY -
    ALONE" split into artist and song. Loose mode adds the title without
    its brackets ("Deathtones (mxstrakh)" -> "Deathtones")."""
    artists: tuple[str, ...]      # primary first
    title: str


def readings(request: LyricsRequest, mode: str) -> list[Reading]:
    artists = tuple(name for name in request.artists if normalize_name(name))
    found: list[Reading] = []
    if artists:
        found.append(Reading(artists, lyrics_clean_title(request.title)))
    split = split_title_artist(request.title)
    if split is not None:
        named = tuple(name for name in split_artist_names(split[0]) if normalize_name(name))
        if named:
            reading = Reading(named + tuple(a for a in artists if a not in named), lyrics_clean_title(split[1]))
            # "AQUAKEY - ALONE" from another account: most likely the real
            # artist, so it is searched first.
            known = {normalize_name(a) for a in artists}
            found.insert(0 if not known & {normalize_name(a) for a in named} else len(found), reading)
    if mode == LYRICS_MODE_LOOSE:
        found = [variant for reading in found
                 for variant in (reading, Reading(reading.artists, lyrics_bare_title(reading.title)))]
    unique: list[Reading] = []
    keys: set = set()
    for reading in found:
        key = (normalize_name(reading.artists[0]), title_key(reading.title))
        if key[1] and key not in keys:
            keys.add(key)
            unique.append(reading)
    return unique


# Searches per provider and track: more would only run into rate limits.
MAX_SEARCH_TERMS = 2


def search_terms(request: LyricsRequest, mode: str) -> list[tuple[str, str]]:
    """(artist, title) pairs to search for, most likely first."""
    return [(reading.artists[0], reading.title) for reading in readings(request, mode)][:MAX_SEARCH_TERMS]


def _search_album(album: str) -> str:
    """"1million (prod. cryst) - Single" -> "1million"."""
    text = collapse(album)
    if text.endswith(SINGLE_ALBUM_SUFFIX.strip()) and text != SINGLE_ALBUM_SUFFIX.strip():
        text = text[: -len(SINGLE_ALBUM_SUFFIX.strip())].rstrip(" -")
    return lyrics_clean_title(text) if text else ""


# -- matching ---------------------------------------------------------------------------
@dataclass
class Verdict:
    synced: bool = False
    plain: bool = False
    instrumental: bool = False
    # Why the candidate was rejected, or why only part of it is usable
    # (for the debug log); empty for a full match.
    reason: str = ""

    @property
    def label(self) -> str:
        if self.instrumental:
            return "instrumental"
        if self.synced:
            return "synced"
        return "plain" if self.plain else "rejected"

    @property
    def rank(self) -> int:
        return 3 if self.instrumental or self.synced else (2 if self.plain else 0)


def _diff_text(diff: float | None) -> str:
    return "unknown length" if diff is None else f"length differs by {diff:.1f}s"


def evaluate(request: LyricsRequest, candidate: Candidate, mode: str,
             settings: LyricsSettings | None = None) -> Verdict:
    """Decides what (if anything) may be taken from a candidate: the best
    verdict over every reading of the track."""
    settings = settings or LyricsSettings()
    if not any(normalize_name(name) for name in request.artists):
        return Verdict(reason="track has no artist")
    theirs_names = split_artist_names(candidate.artist or "")
    theirs = [normalize_name(name) for name in theirs_names if normalize_name(name)]
    whole = normalize_name(candidate.artist)
    if whole and whole not in theirs:
        theirs.append(whole)
    if not theirs:
        return Verdict(reason="candidate has no artist")
    first: Verdict | None = None
    best: Verdict | None = None
    for reading in readings(request, mode) or [Reading(tuple(request.artists), request.title)]:
        verdict = _evaluate_reading(reading, request.duration, theirs, candidate, mode, settings)
        if first is None:
            first = verdict
        if verdict.rank and (best is None or verdict.rank > best.rank):
            best = verdict
    return best or first or Verdict(reason="title mismatch")


def _evaluate_reading(reading: Reading, duration: float | None, theirs: list[str], candidate: Candidate,
                      mode: str, settings: LyricsSettings) -> Verdict:
    ours = [normalize_name(name) for name in reading.artists if normalize_name(name)]
    primary = ours[0]
    compact_ours = {_compact(name) for name in ours}
    strict_artist = (_compact(primary) in {_compact(name) for name in theirs}
                     or _compact(theirs[0]) in compact_ours)
    our_title, their_title = reading.title, lyrics_clean_title(candidate.title)
    our_versions, their_versions = variant_markers(our_title), variant_markers(their_title)
    same_versions = our_versions == their_versions
    strict_title = bool(title_key(our_title)) and title_key(our_title) == title_key(their_title)

    diff = None
    if duration and candidate.duration:
        diff = abs(float(duration) - float(candidate.duration))

    has_text = bool(candidate.plain or candidate.synced)
    if mode == LYRICS_MODE_STRICT:
        if not strict_artist:
            return Verdict(reason="artist mismatch")
        if not same_versions and _same_song(our_title, their_title):
            if (settings.original_for_versions and not their_versions
                    and our_versions <= SAME_WORDS_VERSIONS and not candidate.instrumental):
                # A sped-up/slowed upload of a known song: the words are the
                # same, the timing is not - plain text only.
                return _plain_only(Verdict(plain=has_text,
                                           reason=f"{_version_text(our_title, their_title)}, plain text only"),
                                   settings)
            return Verdict(reason=_version_text(our_title, their_title))
        if not strict_title:
            return Verdict(reason="title mismatch")
        if not same_versions:
            return Verdict(reason=_version_text(our_title, their_title))
        if candidate.instrumental:
            ok = diff is None or diff <= LYRICS_STRICT_DURATION_TOLERANCE
            return Verdict(instrumental=ok, reason="" if ok else f"instrumental, {_diff_text(diff)}")
        timing_ok = diff is not None and diff <= LYRICS_STRICT_DURATION_TOLERANCE
        verdict = Verdict(
            synced=_is_synced(candidate.synced) and timing_ok,
            plain=has_text and (diff is None or timing_ok),
        )
        if not has_text:
            verdict.reason = "no lyrics text"
        elif diff is not None and not timing_ok:
            verdict.reason = _diff_text(diff)
        elif not verdict.synced:
            verdict.reason = ("no synced lyrics" if not _is_synced(candidate.synced)
                              else f"synced needs a known length ({_diff_text(diff)})")
        return _plain_only(verdict, settings)

    # loose
    artist_ok = strict_artist or any(
        _similar(a, b) >= 0.85 or _similar(latin_key(a), latin_key(b)) >= 0.85 for a in ours for b in theirs
    )
    ours_key = title_key(strip_variants(our_title))
    theirs_key = title_key(lyrics_bare_title(strip_variants(their_title)))
    loose_title = (_similar(ours_key, theirs_key) >= 0.85
                   or _similar(latin_key(ours_key), latin_key(theirs_key)) >= 0.85)
    if not artist_ok:
        return Verdict(reason="artist mismatch")
    if not (strict_title or loose_title):
        return Verdict(reason="title mismatch")
    if not same_versions:
        # A sped-up/slowed/remixed upload of a known song: the words are the
        # same, the length is not - plain text only.
        return _plain_only(Verdict(plain=has_text and not candidate.instrumental,
                                   reason=f"{_version_text(our_title, their_title)}, plain text only"),
                           settings)
    tolerance = settings.length_tolerance
    if diff is not None and diff > tolerance:
        return Verdict(reason=f"{_diff_text(diff)} (more than {tolerance:g}s)")
    if candidate.instrumental:
        return Verdict(instrumental=same_versions)
    exact_timing = diff is not None and diff <= LYRICS_STRICT_DURATION_TOLERANCE
    verdict = Verdict(
        synced=_is_synced(candidate.synced) and exact_timing and same_versions,
        plain=has_text,
    )
    if not has_text:
        verdict.reason = "no lyrics text"
    elif not verdict.synced:
        verdict.reason = ("no synced lyrics" if not _is_synced(candidate.synced)
                          else f"synced needs ±{LYRICS_STRICT_DURATION_TOLERANCE:.0f}s ({_diff_text(diff)})")
    return _plain_only(verdict, settings)


def _plain_only(verdict: Verdict, settings: LyricsSettings) -> Verdict:
    """`plain = False` (synced lyrics only): plain text is never taken."""
    if verdict.plain and not verdict.synced and not settings.plain:
        verdict.plain = False
        verdict.reason = f"{verdict.reason}; plain text switched off" if verdict.reason else "plain text switched off"
    return verdict


def _same_song(ours: str, theirs: str) -> bool:
    """Same title once the version markers are left out."""
    key = title_key(strip_variants(ours))
    return bool(key) and key == title_key(strip_variants(theirs))


def _version_text(ours: str, theirs: str) -> str:
    def markers(title: str) -> str:
        return ", ".join(sorted(variant_markers(title))) or "original"
    return f"version mismatch ({markers(ours)} vs {markers(theirs)})"


def log_candidate(request: LyricsRequest, candidate: Candidate, verdict: Verdict,
                  stage: str = "checked") -> None:
    """One candidate and its verdict in the debug log (see debuglog.py)."""
    if not debuglog.enabled():
        return
    debuglog.event(
        "lyrics", "candidate",
        track=request.audio_path.name, provider=candidate.provider, stage=stage,
        artist=candidate.artist, title=candidate.title, duration=candidate.duration,
        has_synced=_is_synced(candidate.synced), has_plain=bool(candidate.plain),
        instrumental=candidate.instrumental, verdict=verdict.label, reason=verdict.reason,
    )


# -- providers ----------------------------------------------------------------------------
class ProviderError(Exception):
    """A request failed; the provider may have been paused or switched off.
    `temporary`: asking again later may work (rate limit, server error,
    network), so the track is not marked as "not found"."""

    def __init__(self, message: str, temporary: bool = True):
        super().__init__(message)
        self.temporary = temporary


class _Provider:
    name = "provider"
    min_interval = 0.3
    max_interval = 3.0             # slowest pacing after rate limits
    max_failures = 3               # failed requests in a row before a pause
    max_refusals = 2               # 401/403 in a row before switching off
    retry_delays = (2.0, 5.0)      # waits before retrying a 429/5xx request
    max_retry_wait = 30.0          # a longer Retry-After fails the request instead
    first_pause = 60.0             # the pause doubles every time, up to max_pause
    max_pause = 600.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_call = 0.0
        self._interval = self.min_interval
        self._failures = 0
        self._refusals = 0
        self._pause = self.first_pause
        self._paused_until = 0.0
        self._pause_reason = ""
        self._last_results: tuple = ()
        self._last_query: object = None
        self._echoes = 0
        self.disabled_reason = ""
        self.disabled_temporary = False
        self.settings = LyricsSettings()
        self.abort: threading.Event | None = None

    @property
    def disabled(self) -> bool:
        return bool(self.disabled_reason)

    @property
    def unavailable_reason(self) -> str:
        """Why the provider cannot be asked right now ("" when it can)."""
        if self.disabled_reason:
            return f"switched off: {self.disabled_reason}"
        with self._lock:
            if time.monotonic() < self._paused_until:
                return f"paused: {self._pause_reason}"
        return ""

    @property
    def unavailable_temporary(self) -> bool:
        return not self.disabled_reason or self.disabled_temporary

    def disable(self, reason: str, temporary: bool = False) -> None:
        with self._lock:
            if not self.disabled_reason:
                self.disabled_reason = reason
                self.disabled_temporary = temporary

    def _wait(self, seconds: float) -> bool:
        """Sleeps unless the run is interrupted; False when it was."""
        if seconds <= 0:
            return True
        if self.abort is not None:
            return not self.abort.wait(seconds)
        time.sleep(seconds)
        return True

    def _pace(self) -> None:
        with self._lock:
            wait = self._interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()

    def _get(self, url: str, **kwargs):
        reason = self.unavailable_reason
        if reason:
            raise ProviderError(reason, temporary=self.unavailable_temporary)
        status = 0
        for attempt in range(len(self.retry_delays) + 1):
            self._pace()
            try:
                response = http_get(url, **kwargs)
            except OSError as exc:
                debuglog.event("lyrics", "query", provider=self.name, url=url,
                               params=kwargs.get("params"), error=str(exc))
                self._failed(f"network error: {exc}")
                raise ProviderError(str(exc)) from exc
            status = response.status
            debuglog.event("lyrics", "query", provider=self.name, url=url,
                           params=kwargs.get("params"), status=status)
            if status in (401, 403):
                self._refused(status)
                raise ProviderError(f"HTTP {status}", temporary=False)
            if status != 429 and status < 500:
                with self._lock:
                    self._failures = 0
                    self._refusals = 0
                    self._pause = self.first_pause
                return response
            if attempt >= len(self.retry_delays):
                break
            delay = response.retry_after()
            delay = self.retry_delays[attempt] if delay is None else delay
            if delay > self.max_retry_wait:
                break
            with self._lock:
                # Rate limited: every later request is spaced out more.
                self._interval = min(self.max_interval, max(self._interval * 2, 1.0))
            if not self._wait(delay):
                raise ProviderError("run interrupted")
        self._failed(f"HTTP {status}")
        raise ProviderError(f"HTTP {status}")

    def _failed(self, reason: str) -> None:
        """A request failed for good (after its retries). Several in a row
        pause the provider; it is asked again once the pause is over."""
        with self._lock:
            self._failures += 1
            if self._failures >= self.max_failures:
                self._pause_reason = f"{reason} ({self._failures} times in a row), for {self._pause:.0f} s"
                self._paused_until = time.monotonic() + self._pause
                self._pause = min(self.max_pause, self._pause * 2)
                self._failures = 0

    def _refused(self, status: int) -> None:
        with self._lock:
            self._refusals += 1
            refused = self._refusals >= self.max_refusals
        if refused:
            self.disable(f"access refused (HTTP {status})")

    def _check_echo(self, query: object, results: tuple) -> None:
        """A search that answers different queries with the very same
        results ignores the query: switched off instead of asked for every
        track."""
        with self._lock:
            if results and results == self._last_results and query != self._last_query:
                self._echoes += 1
            else:
                self._echoes = 0
            self._last_results, self._last_query = results, query
            echo = self._echoes >= 2
        if echo:
            self.disable("search ignores the query (same results for every track)")

    def _evaluate(self, request: LyricsRequest, candidate: Candidate, mode: str) -> Verdict:
        return evaluate(request, candidate, mode, self.settings)

    def candidates(self, request: LyricsRequest, mode: str) -> Iterable[Candidate]:
        raise NotImplementedError


class Lrclib(_Provider):
    name = "LRCLIB"
    base = "https://lrclib.net/api"

    @staticmethod
    def _candidate(item: dict) -> Candidate:
        return Candidate(
            provider="LRCLIB",
            artist=str(item.get("artistName") or ""),
            title=str(item.get("trackName") or ""),
            duration=float(item["duration"]) if item.get("duration") else None,
            synced=item.get("syncedLyrics") or None,
            plain=item.get("plainLyrics") or None,
            instrumental=bool(item.get("instrumental")),
        )

    def candidates(self, request: LyricsRequest, mode: str) -> Iterable[Candidate]:
        terms = search_terms(request, mode)
        if not terms:
            return
        seen: set = set()
        artist, title = terms[0]
        album = _search_album(request.album)
        if request.duration and album:
            response = self._get(f"{self.base}/get", params={
                "artist_name": artist, "track_name": title,
                "album_name": album, "duration": int(round(request.duration)),
            })
            if response.ok:
                item = response.json()
                if isinstance(item, dict) and item.get("id") is not None:
                    seen.add(item.get("id"))
                    yield self._candidate(item)
        queries: list[dict] = [{"track_name": t, "artist_name": a} for a, t in terms]
        if mode == LYRICS_MODE_LOOSE:
            queries.append({"q": f"{artist} {strip_variants(title) or title}"})
        for params in queries:
            response = self._get(f"{self.base}/search", params=params)
            if not response.ok:
                continue
            items = response.json()
            for item in items if isinstance(items, list) else []:
                if isinstance(item, dict) and item.get("id") not in seen:
                    seen.add(item.get("id"))
                    yield self._candidate(item)


class Musixmatch(_Provider):
    name = "Musixmatch"
    base = "https://apic-desktop.musixmatch.com/ws/1.1/"
    max_failures = 2

    def __init__(self) -> None:
        super().__init__()
        self._token: str | None = None
        self._token_lock = threading.Lock()

    def _call(self, action: str, params: dict) -> dict:
        query = dict(params)
        query["app_id"] = "web-desktop-app-v1.0"
        if self._token:
            query["usertoken"] = self._token
        query["t"] = str(int(time.time() * 1000))
        response = self._get(self.base + action, params=query,
                             headers={"Cookie": "AWSELBCORS=0; AWSELB=0"})
        try:
            data = response.json()
            header = data["message"]["header"]
        except (ValueError, KeyError, TypeError):
            self._failed("unexpected response")
            raise ProviderError("unexpected response")
        status = header.get("status_code")
        if status == 401:
            # A captcha/rate limit. syncedlyrics waited 10 s and retried
            # forever here; the provider is simply skipped for this run.
            self.disable(f"access refused ({header.get('hint') or 'captcha'})", temporary=True)
            raise ProviderError(self.disabled_reason)
        return data["message"]

    def _ensure_token(self) -> None:
        with self._token_lock:
            if self._token:
                return
            message = self._call("token.get", {"user_language": "en"})
            token = (message.get("body") or {}).get("user_token") if isinstance(message.get("body"), dict) else None
            if not token or "UpgradeOnly" in token:
                self.disable("no usable token", temporary=True)
                raise ProviderError(self.disabled_reason)
            self._token = token

    @staticmethod
    def _candidate(track: dict) -> Candidate:
        return Candidate(
            provider="Musixmatch",
            artist=str(track.get("artist_name") or ""),
            title=str(track.get("track_name") or ""),
            duration=float(track["track_length"]) if track.get("track_length") else None,
            instrumental=bool(track.get("instrumental")),
        )

    def _tracks(self, request: LyricsRequest, mode: str) -> Iterable[tuple[dict, str]]:
        """The matcher's best guess for every search term first, then the
        free-text search. `track.search` with q_track/q_artist answered
        every query with the same unrelated tracks."""
        terms = search_terms(request, mode)
        for artist, title in terms:
            params = {"q_track": title, "q_artist": artist}
            if request.duration:
                params["q_duration"] = str(int(round(request.duration)))
            body = self._call("matcher.track.get", params).get("body")
            track = body.get("track") if isinstance(body, dict) else None
            if isinstance(track, dict):
                yield track, "matcher"
        for artist, title in terms[:1]:
            query = f"{artist} {title}"
            body = self._call("track.search", {
                "q": query, "page_size": "10", "page": "1", "s_track_rating": "desc",
            }).get("body")
            tracks = body.get("track_list") if isinstance(body, dict) else None
            found = [entry.get("track") for entry in tracks or []
                     if isinstance(entry, dict) and isinstance(entry.get("track"), dict)]
            self._check_echo(query, tuple(str(track.get("track_id")) for track in found))
            for track in found:
                yield track, "search result"

    def candidates(self, request: LyricsRequest, mode: str) -> Iterable[Candidate]:
        if not request.main_artists:
            return
        self._ensure_token()
        seen: set = set()
        for track, stage in self._tracks(request, mode):
            if track.get("track_id") in seen:
                continue
            seen.add(track.get("track_id"))
            candidate = self._candidate(track)
            # Only fetch the subtitles for a candidate that can be accepted.
            probe = Candidate(**{**candidate.__dict__, "synced": "[00:00.00]a\n[00:01.00]b\n[00:02.00]c"})
            probe_verdict = self._evaluate(request, probe, mode)
            if not probe_verdict.synced and not candidate.instrumental:
                log_candidate(request, candidate, probe_verdict, stage=stage)
                continue
            if track.get("has_subtitles") and not candidate.instrumental:
                sub = self._call("track.subtitle.get", {
                    "track_id": str(track.get("track_id")), "subtitle_format": "lrc",
                })
                sub_body = sub.get("body")
                subtitle = sub_body.get("subtitle") if isinstance(sub_body, dict) else None
                if isinstance(subtitle, dict):
                    candidate.synced = subtitle.get("subtitle_body") or None
            yield candidate


class Genius(_Provider):
    name = "Genius"
    search_url = "https://genius.com/api/search/multi"
    min_interval = 0.6
    # Every search was answered with 403 when the request claimed to be a
    # browser; the honest client name is tried next, and the one that works
    # is kept.
    header_sets = (
        {"User-Agent": BROWSER_USER_AGENT, "Accept": "application/json"},
        {"User-Agent": USER_AGENT, "Accept": "application/json"},
    )

    def __init__(self) -> None:
        super().__init__()
        self._headers = 0

    def _search(self, query: str):
        for index in range(self._headers, len(self.header_sets)):
            try:
                response = self._get(self.search_url, params={"per_page": "5", "q": query},
                                     headers=self.header_sets[index])
            except ProviderError as exc:
                if exc.temporary or index + 1 >= len(self.header_sets) or self.disabled:
                    raise
                continue
            self._headers = index
            return response
        return None

    def candidates(self, request: LyricsRequest, mode: str) -> Iterable[Candidate]:
        if BeautifulSoup is None or not request.main_artists:
            return
        seen: set = set()
        for number, (artist, title) in enumerate(search_terms(request, mode)):
            query = f"{artist} {title}"
            response = self._search(query)
            if response is None or not response.ok:
                continue
            try:
                sections = response.json()["response"]["sections"]
            except (ValueError, KeyError, TypeError):
                continue
            hits = []
            for section in sections if isinstance(sections, list) else []:
                if not isinstance(section, dict) or section.get("type") not in {"song", "top_hit"}:
                    continue
                for hit in section.get("hits") or []:
                    result = hit.get("result") if isinstance(hit, dict) else None
                    if isinstance(result, dict) and hit.get("type") in {None, "song"} and result.get("url"):
                        hits.append(result)
            if number == 0:
                self._check_echo(query, tuple(str(result.get("id")) for result in hits))
            for result in hits:
                if result.get("id") in seen:
                    continue
                seen.add(result.get("id"))
                candidate = Candidate(
                    provider="Genius",
                    artist=str((result.get("primary_artist") or {}).get("name") or result.get("artist_names") or ""),
                    title=str(result.get("title") or ""),
                    instrumental=bool(result.get("instrumental")),
                )
                if result.get("lyrics_state") not in {None, "complete"} and not candidate.instrumental:
                    log_candidate(request, candidate, Verdict(
                        reason=f"lyrics state '{result.get('lyrics_state')}'"), stage="search result")
                    continue
                probe = Candidate(**{**candidate.__dict__, "plain": "x"})
                verdict = self._evaluate(request, probe, mode)
                if not (verdict.plain or verdict.instrumental):
                    log_candidate(request, candidate, verdict, stage="search result")
                    continue
                if not candidate.instrumental:
                    candidate.plain = self._page_lyrics(str(result["url"]))
                yield candidate

    def _page_lyrics(self, url: str) -> str | None:
        if not url.startswith("https://genius.com/"):
            return None
        headers = dict(self.header_sets[self._headers], Accept="text/html")
        response = self._get(url, headers=headers)
        if not response.ok:
            return None
        soup = BeautifulSoup(response.text(), "html.parser")
        containers = soup.select('div[data-lyrics-container="true"]')
        if not containers:
            return None
        parts: list[str] = []
        for container in containers:
            for excluded in container.select('[data-exclude-from-selection="true"]'):
                excluded.decompose()
            for br in container.find_all("br"):
                br.replace_with("\n")
            parts.append(container.get_text())
        text = "\n".join(part.strip("\n") for part in parts)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text or None


PROVIDER_CLASSES: dict[str, type[_Provider]] = {"lrclib": Lrclib, "musixmatch": Musixmatch, "genius": Genius}


# -- retry cooldown ------------------------------------------------------------------------
class LyricsAttempts:
    """Remembers when a lyrics search last found nothing for a track (per
    mode), so a track without lyrics anywhere isn't searched on every run."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            self._data: dict = raw if isinstance(raw, dict) else {}
        except (OSError, ValueError):
            self._data = {}

    def should_skip(self, key: str, mode: str, cooldown: float = LYRICS_RETRY_COOLDOWN_SECONDS) -> bool:
        if not key or cooldown <= 0:
            return False
        with self._lock:
            entry = self._data.get(key)
        if isinstance(entry, str):          # written by older versions (strict-equivalent)
            entry = {"at": entry, "mode": LYRICS_MODE_STRICT}
        if not isinstance(entry, dict) or entry.get("mode") != mode:
            return False
        try:
            last = datetime.fromisoformat(str(entry.get("at")))
        except ValueError:
            return False
        return (datetime.now() - last).total_seconds() < cooldown

    def has(self, key: str) -> bool:
        """Whether a search was recorded for the track at all (any mode, any age)."""
        with self._lock:
            return bool(key) and key in self._data

    def record(self, key: str, mode: str) -> None:
        if not key:
            return
        with self._lock:
            self._data[key] = {"at": datetime.now().isoformat(timespec="seconds"), "mode": mode}
            self._save_locked()

    def clear(self, key: str) -> None:
        with self._lock:
            if self._data.pop(key, None) is not None:
                self._save_locked()

    def _save_locked(self) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


_ATTEMPTS: dict[Path, LyricsAttempts] = {}
_ATTEMPTS_LOCK = threading.Lock()


def get_attempts(path: Path) -> LyricsAttempts:
    key = path.resolve()
    with _ATTEMPTS_LOCK:
        attempts = _ATTEMPTS.get(key)
        if attempts is None:
            attempts = LyricsAttempts(key)
            _ATTEMPTS[key] = attempts
        return attempts


# -- service ---------------------------------------------------------------------------------
def build_providers(names: Iterable[str]) -> list[_Provider]:
    return [PROVIDER_CLASSES[name]() for name in names if name in PROVIDER_CLASSES]


class LyricsService:
    """Shared by every link of a run: providers keep their state (tokens,
    paused or switched-off providers) and a file is never processed twice
    at once."""

    def __init__(self, mode: str = LYRICS_MODE_STRICT, providers: list[_Provider] | None = None,
                 settings: LyricsSettings | None = None):
        self.mode = mode if mode in {LYRICS_MODE_STRICT, LYRICS_MODE_LOOSE} else LYRICS_MODE_STRICT
        self.settings = settings or LyricsSettings()
        self.providers = providers if providers is not None else build_providers(self.settings.providers)
        for provider in self.providers:
            provider.settings = self.settings
        self._busy: set[Path] = set()
        self._busy_lock = threading.Lock()
        self._reported: set[str] = set()
        self._reported_lock = threading.Lock()

    def _claim(self, path: Path) -> bool:
        with self._busy_lock:
            if path in self._busy:
                return False
            self._busy.add(path)
            return True

    def _release(self, path: Path) -> None:
        with self._busy_lock:
            self._busy.discard(path)

    def process(
        self,
        request: LyricsRequest,
        attempts: LyricsAttempts | None,
        dashboard,
        abort: threading.Event | None = None,
    ) -> LyricsOutcome:
        path = request.audio_path
        if abort is not None and abort.is_set():
            return LyricsOutcome("skipped", "run interrupted")
        if not self._claim(path):
            return LyricsOutcome("skipped", "already being processed")
        debuglog.event(
            "lyrics", "request", track=path.name, mode=self.mode, artists=request.main_artists,
            featured=request.featured, title=request.title, album=request.album,
            duration=request.duration, force=request.force,
            searches=[list(term) for term in search_terms(request, self.mode)],
        )
        try:
            outcome = self._process(request, attempts, dashboard, abort)
        finally:
            self._release(path)
        debuglog.event("lyrics", "result", track=path.name, status=outcome.status,
                       provider=outcome.provider, detail=outcome.detail)
        if outcome.status in {"synced", "plain"}:
            dashboard.record_lyrics(True)
        elif outcome.status == "not_found":
            dashboard.record_lyrics(False)
        elif outcome.status in {"skipped", "instrumental"}:
            dashboard.record_lyrics_skipped()
        return outcome

    def _process(
        self,
        request: LyricsRequest,
        attempts: LyricsAttempts | None,
        dashboard,
        abort: threading.Event | None,
    ) -> LyricsOutcome:
        path = request.audio_path
        lrc_path = path.with_suffix(".lrc")
        if request.force:
            try:
                lrc_path.unlink(missing_ok=True)
            except OSError:
                pass
            remove_lyrics(path)
            if attempts is not None:
                attempts.clear(request.key)
        elif lrc_path.exists() or has_embedded_lyrics(path):
            return LyricsOutcome("exists")

        if looks_instrumental(request.title):
            return LyricsOutcome("skipped", "instrumental / DJ set")
        if not request.main_artists or not collapse(request.title):
            return LyricsOutcome("skipped", "no artist/title")
        if (attempts is not None and not request.force
                and attempts.should_skip(request.key, self.mode, self.settings.retry_seconds)):
            return LyricsOutcome("skipped", "searched recently")

        best_plain: Candidate | None = None
        # Providers that could not be asked for a reason that may pass (rate
        # limit, server error, network): the search is incomplete.
        missed: list[str] = []
        for provider in self.providers:
            if abort is not None and abort.is_set():
                return LyricsOutcome("skipped", "run interrupted")
            provider.abort = abort
            unavailable = provider.unavailable_reason
            if unavailable:
                self._report_unavailable(provider, dashboard)
                debuglog.event("lyrics", "provider_skipped", track=path.name, provider=provider.name,
                               reason=unavailable)
                if provider.unavailable_temporary:
                    missed.append(provider.name)
                continue
            if best_plain is not None and isinstance(provider, Genius):
                debuglog.event("lyrics", "provider_skipped", track=path.name, provider=provider.name,
                               reason="plain lyrics already found")
                continue
            try:
                for candidate in provider.candidates(request, self.mode):
                    verdict = evaluate(request, candidate, self.mode, self.settings)
                    log_candidate(request, candidate, verdict)
                    if verdict.instrumental:
                        if attempts is not None:
                            attempts.record(request.key, self.mode)
                        dashboard.log(f"[Lyrics] Instrumental per {candidate.provider}: {path.name}")
                        return LyricsOutcome("instrumental", provider=candidate.provider)
                    if verdict.synced:
                        self._save_synced(path, candidate.synced or "")
                        dashboard.log(f"[Lyrics] Synced lyrics from {candidate.provider}: {lrc_path.name}")
                        return LyricsOutcome("synced", provider=candidate.provider)
                    if verdict.plain and best_plain is None:
                        best_plain = candidate
            except ProviderError as exc:
                self._report_unavailable(provider, dashboard)
                debuglog.event("lyrics", "provider_error", track=path.name, provider=provider.name,
                               error=str(exc))
                if exc.temporary:
                    missed.append(provider.name)
                continue
            except Exception as exc:  # a provider bug must not cost the track
                dashboard.log(f"[Lyrics] {provider.name} failed for '{path.name}': {exc}")
                debuglog.event("lyrics", "provider_error", track=path.name, provider=provider.name,
                               error=f"{type(exc).__name__}: {exc}")
                continue

        if best_plain is not None:
            text = best_plain.plain or synced_to_plain(best_plain.synced or "")
            if text:
                embed_lyrics(path, text)
                dashboard.log(f"[Lyrics] Plain lyrics from {best_plain.provider}: {path.name}")
                return LyricsOutcome("plain", provider=best_plain.provider)

        if missed:
            # Not marked as searched: the next run asks the missing providers.
            detail = f"search incomplete: {', '.join(missed)} not reached"
            dashboard.log(f"[Lyrics] No verified lyrics yet ({detail}): "
                          f"{', '.join(request.main_artists)} - {request.title}")
            return LyricsOutcome("not_found", detail)
        if attempts is not None:
            attempts.record(request.key, self.mode)
        dashboard.log(f"[Lyrics] No verified lyrics: {', '.join(request.main_artists)} - {request.title}")
        return LyricsOutcome("not_found")

    def _report_unavailable(self, provider: _Provider, dashboard) -> None:
        """Once per provider and reason in Activity: switched off, or paused."""
        reason = provider.unavailable_reason
        if not reason:
            return
        key = f"{provider.name}\0{provider.disabled_reason or provider._pause_reason}"
        with self._reported_lock:
            if key in self._reported:
                return
            self._reported.add(key)
        if provider.disabled:
            dashboard.log_error("Lyrics", f"{provider.name} switched off for this run: {provider.disabled_reason}")
        else:
            dashboard.log(f"[Lyrics] {provider.name} {reason}; it is asked again afterwards")

    @staticmethod
    def _save_synced(path: Path, synced: str) -> None:
        lrc_path = path.with_suffix(".lrc")
        tmp = lrc_path.with_name(lrc_path.name + ".tmp")
        tmp.write_text(synced.strip() + "\n", encoding="utf-8")
        tmp.replace(lrc_path)
        plain = synced_to_plain(synced)
        if plain:
            embed_lyrics(path, plain)
