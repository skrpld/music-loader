"""Lyrics lookup with verified matches.

The previous lookup went through `syncedlyrics`, which accepts the first
Genius search hit, the best Lrclib hit without any threshold and fuzzy
"token set" matches elsewhere - and was fed queries down to the bare song
name. A search for "Intro" happily returned somebody else's "Intro".

Every candidate is now checked against the track itself:

strict (default)
    * artist: the provider's artist must be one of the track's artists;
    * title: equal after normalization (case, punctuation, "feat." parts,
      promotional noise and remaster notes do not count) and with the same
      version markers - a sped-up, slowed, remixed or live upload never gets
      the lyrics of the original;
    * synced lyrics: the provider's track length must be within 2 s of the
      file, otherwise the timestamps belong to a different cut;
    * plain lyrics: same checks; Genius has no track length, so its text is
      accepted on an exact artist + title match only.
loose (`--lyrics-loose`)
    * fuzzy artist and title similarity, version markers ignored;
    * the length may differ by up to 10 s; when it differs by more than 2 s
      (or the versions differ) only plain text is saved, never misaligned
      timestamps.

Uploads that cannot have lyrics (instrumentals, DJ sets, beats) are skipped.

Synced lyrics are written next to the audio file as `.lrc` and their text is
also embedded (USLT); plain lyrics are embedded only.

Provider failures never stall a run: a provider that keeps failing - or
Musixmatch asking for a captcha, which used to make syncedlyrics retry every
10 seconds forever - is switched off for the rest of the run.
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

from .config import (
    LYRICS_LOOSE_DURATION_TOLERANCE,
    LYRICS_MODE_LOOSE,
    LYRICS_MODE_STRICT,
    LYRICS_RETRY_COOLDOWN_SECONDS,
    LYRICS_STRICT_DURATION_TOLERANCE,
)
from .net import BROWSER_USER_AGENT, http_get
from .tags import embed_lyrics, has_embedded_lyrics, remove_lyrics
from .text_utils import (
    collapse,
    has_no_lyrics_hint,
    normalize_name,
    split_artist_names,
    strip_feat,
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
    force: bool = False               # --recheck: replace existing lyrics

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


# -- matching ---------------------------------------------------------------------------
@dataclass
class Verdict:
    synced: bool = False
    plain: bool = False
    instrumental: bool = False


def evaluate(request: LyricsRequest, candidate: Candidate, mode: str) -> Verdict:
    """Decides what (if anything) may be taken from a candidate."""
    ours = [normalize_name(name) for name in request.artists if normalize_name(name)]
    if not ours:
        return Verdict()
    primary = normalize_name(request.main_artists[0]) if request.main_artists else ours[0]
    theirs_names = split_artist_names(candidate.artist or "")
    theirs = [normalize_name(name) for name in theirs_names if normalize_name(name)]
    whole = normalize_name(candidate.artist)
    if whole and whole not in theirs:
        theirs.append(whole)
    if not theirs:
        return Verdict()

    strict_artist = primary in theirs or theirs[0] in set(ours)
    our_title, their_title = request.title, candidate.title
    same_versions = variant_markers(our_title) == variant_markers(their_title)
    strict_title = bool(title_key(our_title)) and title_key(our_title) == title_key(their_title)

    diff = None
    if request.duration and candidate.duration:
        diff = abs(float(request.duration) - float(candidate.duration))

    if mode == LYRICS_MODE_STRICT:
        if not (strict_artist and strict_title and same_versions):
            return Verdict()
        if candidate.instrumental:
            return Verdict(instrumental=diff is None or diff <= LYRICS_STRICT_DURATION_TOLERANCE)
        timing_ok = diff is not None and diff <= LYRICS_STRICT_DURATION_TOLERANCE
        return Verdict(
            synced=_is_synced(candidate.synced) and timing_ok,
            plain=bool((candidate.plain or candidate.synced)) and (diff is None or timing_ok),
        )

    # loose
    artist_ok = strict_artist or any(_similar(a, b) >= 0.85 for a in ours for b in theirs)
    loose_title = _similar(
        title_key(strip_variants(our_title)), title_key(strip_variants(their_title))
    ) >= 0.85
    if not (artist_ok and (strict_title or loose_title)):
        return Verdict()
    if not same_versions:
        # A sped-up/slowed/remixed upload of a known song: the words are the
        # same, the length is not - plain text only.
        return Verdict(plain=bool(candidate.plain or candidate.synced) and not candidate.instrumental)
    if diff is not None and diff > LYRICS_LOOSE_DURATION_TOLERANCE:
        return Verdict()
    if candidate.instrumental:
        return Verdict(instrumental=same_versions)
    exact_timing = diff is not None and diff <= LYRICS_STRICT_DURATION_TOLERANCE
    return Verdict(
        synced=_is_synced(candidate.synced) and exact_timing and same_versions,
        plain=bool(candidate.plain or candidate.synced),
    )


# -- providers ----------------------------------------------------------------------------
class ProviderError(Exception):
    """A request failed; the provider may have been switched off as well."""


class _Provider:
    name = "provider"
    min_interval = 0.3
    max_failures = 3

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_call = 0.0
        self._failures = 0
        self.disabled_reason = ""

    @property
    def disabled(self) -> bool:
        return bool(self.disabled_reason)

    def disable(self, reason: str) -> None:
        with self._lock:
            if not self.disabled_reason:
                self.disabled_reason = reason

    def _pace(self) -> None:
        with self._lock:
            wait = self.min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()

    def _get(self, url: str, **kwargs):
        if self.disabled:
            raise ProviderError(self.disabled_reason)
        self._pace()
        try:
            response = http_get(url, **kwargs)
        except OSError as exc:
            self._failed(f"network error: {exc}")
            raise ProviderError(str(exc)) from exc
        if response.status == 429 or response.status >= 500:
            self._failed(f"HTTP {response.status}")
            raise ProviderError(f"HTTP {response.status}")
        with self._lock:
            self._failures = 0
        return response

    def _failed(self, reason: str) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.max_failures and not self.disabled_reason:
                self.disabled_reason = f"{reason} ({self._failures} times in a row)"

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
        artist = request.main_artists[0] if request.main_artists else ""
        title = strip_feat(request.title) or request.title
        seen: set = set()
        if artist and request.duration and request.album:
            response = self._get(f"{self.base}/get", params={
                "artist_name": artist, "track_name": title,
                "album_name": request.album, "duration": int(round(request.duration)),
            })
            if response.ok:
                item = response.json()
                if isinstance(item, dict) and item.get("id") is not None:
                    seen.add(item.get("id"))
                    yield self._candidate(item)
        queries = [{"track_name": title, "artist_name": artist}] if artist else []
        if mode == LYRICS_MODE_LOOSE:
            queries.append({"q": f"{' '.join(request.main_artists)} {strip_variants(title)}"})
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
            self.disable(f"access refused ({header.get('hint') or 'captcha'})")
            raise ProviderError(self.disabled_reason)
        return data["message"]

    def _ensure_token(self) -> None:
        with self._token_lock:
            if self._token:
                return
            message = self._call("token.get", {"user_language": "en"})
            token = (message.get("body") or {}).get("user_token") if isinstance(message.get("body"), dict) else None
            if not token or "UpgradeOnly" in token:
                self.disable("no usable token")
                raise ProviderError(self.disabled_reason)
            self._token = token

    def candidates(self, request: LyricsRequest, mode: str) -> Iterable[Candidate]:
        if not request.main_artists:
            return
        self._ensure_token()
        title = strip_feat(request.title) or request.title
        message = self._call("track.search", {
            "q_track": title, "q_artist": request.main_artists[0],
            "page_size": "10", "page": "1", "f_has_lyrics": "1",
        })
        body = message.get("body")
        tracks = body.get("track_list") if isinstance(body, dict) else None
        for entry in tracks or []:
            track = entry.get("track") if isinstance(entry, dict) else None
            if not isinstance(track, dict):
                continue
            candidate = Candidate(
                provider="Musixmatch",
                artist=str(track.get("artist_name") or ""),
                title=str(track.get("track_name") or ""),
                duration=float(track["track_length"]) if track.get("track_length") else None,
                instrumental=bool(track.get("instrumental")),
            )
            # Only fetch the subtitles for a candidate that can be accepted.
            probe = Candidate(**{**candidate.__dict__, "synced": "[00:00.00]a\n[00:01.00]b\n[00:02.00]c"})
            if not evaluate(request, probe, mode).synced and not candidate.instrumental:
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

    def candidates(self, request: LyricsRequest, mode: str) -> Iterable[Candidate]:
        if BeautifulSoup is None or not request.main_artists:
            return
        title = strip_feat(request.title) or request.title
        response = self._get(self.search_url, params={
            "per_page": "5", "q": f"{request.main_artists[0]} {title}",
        }, headers={"User-Agent": BROWSER_USER_AGENT})
        if not response.ok:
            return
        try:
            sections = response.json()["response"]["sections"]
        except (ValueError, KeyError, TypeError):
            return
        seen: set = set()
        for section in sections if isinstance(sections, list) else []:
            if not isinstance(section, dict) or section.get("type") not in {"song", "top_hit"}:
                continue
            for hit in section.get("hits") or []:
                result = hit.get("result") if isinstance(hit, dict) else None
                if not isinstance(result, dict) or hit.get("type") not in {None, "song"}:
                    continue
                if result.get("id") in seen or not result.get("url"):
                    continue
                seen.add(result.get("id"))
                artist = str((result.get("primary_artist") or {}).get("name") or result.get("artist_names") or "")
                candidate = Candidate(
                    provider="Genius",
                    artist=artist,
                    title=str(result.get("title") or ""),
                    instrumental=bool(result.get("instrumental")),
                )
                if result.get("lyrics_state") not in {None, "complete"} and not candidate.instrumental:
                    continue
                probe = Candidate(**{**candidate.__dict__, "plain": "x"})
                verdict = evaluate(request, probe, mode)
                if not (verdict.plain or verdict.instrumental):
                    continue
                if not candidate.instrumental:
                    candidate.plain = self._page_lyrics(str(result["url"]))
                yield candidate

    def _page_lyrics(self, url: str) -> str | None:
        if not url.startswith("https://genius.com/"):
            return None
        response = self._get(url, headers={"User-Agent": BROWSER_USER_AGENT})
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

    def should_skip(self, key: str, mode: str) -> bool:
        if not key:
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
        return (datetime.now() - last).total_seconds() < LYRICS_RETRY_COOLDOWN_SECONDS

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
class LyricsService:
    """Shared by every link of a run: providers keep their state (tokens,
    switched-off providers) and a file is never processed twice at once."""

    def __init__(self, mode: str = LYRICS_MODE_STRICT, providers: list[_Provider] | None = None):
        self.mode = mode if mode in {LYRICS_MODE_STRICT, LYRICS_MODE_LOOSE} else LYRICS_MODE_STRICT
        self.providers = providers if providers is not None else [Lrclib(), Musixmatch(), Genius()]
        self._busy: set[Path] = set()
        self._busy_lock = threading.Lock()
        self._reported: set[str] = set()

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
        try:
            outcome = self._process(request, attempts, dashboard, abort)
        finally:
            self._release(path)
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

        if has_no_lyrics_hint(request.title) or "instrumental" in variant_markers(request.title):
            return LyricsOutcome("skipped", "instrumental / DJ set")
        if not request.main_artists or not collapse(request.title):
            return LyricsOutcome("skipped", "no artist/title")
        if attempts is not None and not request.force and attempts.should_skip(request.key, self.mode):
            return LyricsOutcome("skipped", "searched recently")

        best_plain: Candidate | None = None
        for provider in self.providers:
            if abort is not None and abort.is_set():
                return LyricsOutcome("skipped", "run interrupted")
            if provider.disabled:
                self._report_disabled(provider, dashboard)
                continue
            if best_plain is not None and isinstance(provider, Genius):
                continue
            try:
                for candidate in provider.candidates(request, self.mode):
                    verdict = evaluate(request, candidate, self.mode)
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
            except ProviderError:
                self._report_disabled(provider, dashboard)
                continue
            except Exception as exc:  # a provider bug must not cost the track
                dashboard.log(f"[Lyrics] {provider.name} failed for '{path.name}': {exc}")
                continue

        if best_plain is not None:
            text = best_plain.plain or synced_to_plain(best_plain.synced or "")
            if text:
                embed_lyrics(path, text)
                dashboard.log(f"[Lyrics] Plain lyrics from {best_plain.provider}: {path.name}")
                return LyricsOutcome("plain", provider=best_plain.provider)

        if attempts is not None:
            attempts.record(request.key, self.mode)
        dashboard.log(f"[Lyrics] No verified lyrics: {', '.join(request.main_artists)} - {request.title}")
        return LyricsOutcome("not_found")

    def _report_disabled(self, provider: _Provider, dashboard) -> None:
        if provider.disabled and provider.name not in self._reported:
            self._reported.add(provider.name)
            dashboard.log_error("Lyrics", f"{provider.name} switched off for this run: {provider.disabled_reason}")

    @staticmethod
    def _save_synced(path: Path, synced: str) -> None:
        lrc_path = path.with_suffix(".lrc")
        tmp = lrc_path.with_name(lrc_path.name + ".tmp")
        tmp.write_text(synced.strip() + "\n", encoding="utf-8")
        tmp.replace(lrc_path)
        plain = synced_to_plain(synced)
        if plain:
            embed_lyrics(path, plain)
