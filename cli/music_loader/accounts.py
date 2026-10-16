"""Who is behind a SoundCloud account: an artist under another name, or a
reupload account that posts other people's tracks.

The SoundCloud account "@aquagwap" is the artist AQUAKEY. Its uploads are
titled "AQUAKEY - Song", but SoundCloud fills the artist field with the
account name, so the tracks used to be tagged "@aquagwap" and filed under
"@aquagwap/". A reupload account ("slmndk") posts tracks of many artists,
often without the artist in the title, and used to become their artist.

One decision per account, keyed by the uploader id (stable across renames),
kept in `.music-loader-artists.json` (artists.py):

    {"handle": "@aquagwap", "permalink": "ak2angel", "kind": "own|reuploader|unknown",
     "alias": "AQUAKEY" | null, "source": "library|spotify|deezer|manual|title",
     "suggestion": "AQUAKEY" | null, "rejected": [...], "locked": false,
     "checked_at": "2026-10-15" | null, "pending": false}

How it is decided, in the resolve phase of a job (before anything is
downloaded, so files get the right name and folder at once):

1. Local evidence, free. A candidate is a name X other than the account name
   that is the first main artist in the titles of at least 70 % of the
   account's tracks in the job, with at least 3 tracks. A featured partner
   never gets there. No candidate: nothing changes and nothing is looked up
   (most accounts). Hashtags and the description only add to the evidence.
2. The user's own library: a Spotify track with the same title, length
   ±3 s and X as its main artist.
3. Only for an account with a candidate: "X <title>" for 2-3 of its tracks
   on Spotify (spotDL), then Deezer, until two match.
4. Two matches with X: X becomes the account's name ("own"). Matches with
   other artists: a reupload account. Fewer: X is only a suggestion, shown in
   Artists, never applied. A service that does not answer decides nothing:
   the account is "not checked" and tried again on a later run.
5. A decision is kept for 30 days; a locked one (every choice the user makes)
   is never changed by a run.

A name in the title always wins. The confirmed name replaces the account
name where it would be used: a title without an artist part, the album
artist, the artist folder. A new track of a confirmed account without an
artist in its title that is not found under that name puts the account back
to evaluation; with tracks of many artists it becomes a reupload account.

Tracks of a reupload account (and, for an account without a decision whose
titles name many artists, its tracks without one) get the artist of the
original upload: the library first, then SoundCloud's search (same title,
length ±2 s, another account, uploaded earlier), then Spotify and Deezer.
Not found: the track keeps the account name and is reported. The result is
kept per track (`.sc_index.json`), so the search runs once.
"""
from __future__ import annotations

import threading
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable
from urllib.parse import urlsplit

from . import debuglog
from .artist_lookup import (
    SOURCE_DEEZER,
    SOURCE_LABELS,
    SOURCE_LIBRARY,
    SOURCE_SOUNDCLOUD,
    SOURCE_SPOTIFY,
    Hit,
    ServiceUnavailable,
    match_key,
    same_song,
    search_title,
)
from .artists import PRIORITY_SPOTIFY, PRIORITY_UPLOADER, ArtistRegistry
from .text_utils import normalize_name, parse_soundcloud_title, strip_decorations, unique

KIND_OWN = "own"
KIND_REUPLOADER = "reuploader"
KIND_UNKNOWN = "unknown"
KINDS = (KIND_OWN, KIND_REUPLOADER, KIND_UNKNOWN)

SOURCE_MANUAL = "manual"
SOURCE_TITLE = "title"
SOURCES = (SOURCE_LIBRARY, SOURCE_SPOTIFY, SOURCE_DEEZER, SOURCE_MANUAL, SOURCE_TITLE)

# What the user sees (CLI list, server, app).
STATUS_CONFIRMED = "confirmed"
STATUS_SUGGESTION = "suggestion"
STATUS_REUPLOADER = "reuploader"
STATUS_NOT_CHECKED = "not_checked"
STATUS_NICK = "nick"

MIN_TRACKS = 3
MIN_SHARE = 0.7
# An account without a decision has "mixed" titles when at least this share
# of its tracks name someone else first, and at least two different names.
MIXED_SHARE = 0.5
CACHE_DAYS = 30
CHECK_TRACKS = 3
NEEDED_MATCHES = 2
MATCH_TOLERANCE = 3.0
ORIGINAL_TOLERANCE = 2.0
_MAX_NAME = 200


class AccountError(ValueError):
    """A user action that cannot be applied; the message says why."""


# -- the stored decision ----------------------------------------------------------------
@dataclass
class Account:
    id: str
    handle: str = ""
    permalink: str = ""
    kind: str = KIND_UNKNOWN
    alias: str | None = None
    source: str | None = None
    suggestion: str | None = None
    rejected: list[str] = field(default_factory=list)
    locked: bool = False
    checked_at: str | None = None
    pending: bool = False
    evidence: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, account_id: str, data: dict[str, Any]) -> "Account":
        def text(key: str) -> str | None:
            value = data.get(key)
            return value.strip()[:_MAX_NAME] or None if isinstance(value, str) else None

        kind = data.get("kind") if data.get("kind") in KINDS else KIND_UNKNOWN
        source = data.get("source") if data.get("source") in SOURCES else None
        rejected = [item for item in data.get("rejected") or [] if isinstance(item, str)] \
            if isinstance(data.get("rejected"), list) else []
        evidence = data.get("evidence") if isinstance(data.get("evidence"), dict) else {}
        return cls(
            id=account_id, handle=text("handle") or "", permalink=text("permalink") or "",
            kind=kind, alias=text("alias") if kind == KIND_OWN else None, source=source,
            suggestion=text("suggestion"), rejected=rejected, locked=data.get("locked") is True,
            checked_at=text("checked_at"), pending=data.get("pending") is True, evidence=evidence,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "handle": self.handle, "permalink": self.permalink, "kind": self.kind,
            "alias": self.alias, "source": self.source, "suggestion": self.suggestion,
            "rejected": self.rejected, "locked": self.locked, "checked_at": self.checked_at,
            "pending": self.pending, "evidence": self.evidence,
        }

    @property
    def status(self) -> str:
        if self.kind == KIND_REUPLOADER:
            return STATUS_REUPLOADER
        if self.kind == KIND_OWN and self.alias:
            return STATUS_CONFIRMED
        if self.pending:
            return STATUS_NOT_CHECKED
        if self.suggestion:
            return STATUS_SUGGESTION
        return STATUS_NICK

    @property
    def url(self) -> str:
        return f"https://soundcloud.com/{self.permalink}" if self.permalink else ""

    @property
    def name(self) -> str:
        return self.handle or self.permalink or self.id

    def public(self) -> dict[str, Any]:
        """The account as the CLI list, the server and the app show it."""
        return {
            "id": self.id, "handle": self.handle, "permalink": self.permalink, "url": self.url,
            "status": self.status, "kind": self.kind, "alias": self.alias, "source": self.source,
            "suggestion": self.suggestion, "locked": self.locked, "checked_at": self.checked_at,
            "tracks": int(self.evidence.get("tracks") or 0) if isinstance(self.evidence, dict) else 0,
        }

    def is_fresh(self, today: date) -> bool:
        if not self.checked_at or self.pending:
            return False
        try:
            checked = date.fromisoformat(self.checked_at)
        except ValueError:
            return False
        return today - checked < timedelta(days=CACHE_DAYS)


class AccountBook:
    """The stored decisions, and the user's actions on them."""

    def __init__(self, registry: ArtistRegistry):
        self.registry = registry

    def get(self, account_id: str) -> Account | None:
        data = self.registry.account(account_id)
        return Account.from_dict(account_id, data) if data is not None else None

    def all(self) -> list[Account]:
        return [Account.from_dict(key, value) for key, value in self.registry.accounts().items()]

    def save(self, account: Account) -> None:
        self.registry.put_account(account.id, account.to_dict())

    def find(self, ref: str) -> Account | None:
        """An account by uploader id, profile URL, handle or profile name."""
        ref = (ref or "").strip()
        if not ref:
            return None
        accounts = self.all()
        if ref.isdigit():
            for account in accounts:
                if account.id == ref:
                    return account
        parts = urlsplit(ref if "://" in ref else f"https://{ref}") if "soundcloud.com" in ref.lower() else None
        if parts is not None:
            permalink = parts.path.strip("/").split("/")[0].casefold()
            return next((item for item in accounts if item.permalink.casefold() == permalink), None)
        key = normalize_name(ref)
        for account in accounts:
            if account.permalink.casefold() == ref.casefold():
                return account
        matches = [item for item in accounts if key and normalize_name(item.handle) == key]
        return matches[0] if len(matches) == 1 else None

    def _require(self, ref: str) -> Account:
        account = self.find(ref)
        if account is None:
            raise AccountError(f"unknown account '{ref}': it is listed once a job has seen one of its "
                               "tracks (name it by its id, profile link or handle)")
        return account

    def _register(self, name: str, priority: int) -> None:
        self.registry.register(name, priority)

    # -- user actions (always locked: a run never changes the user's choice) ------------
    def set_alias(self, ref: str, name: str) -> Account:
        name = strip_decorations(name)
        if not normalize_name(name) or len(name) > _MAX_NAME:
            raise AccountError("the artist name is empty or too long")
        account = self._require(ref)
        account.kind, account.alias, account.source = KIND_OWN, name, SOURCE_MANUAL
        account.suggestion, account.locked, account.pending = None, True, False
        self._register(name, PRIORITY_UPLOADER)
        self.save(account)
        return account

    def keep(self, ref: str) -> Account:
        account = self._require(ref)
        account.kind, account.alias, account.source = KIND_OWN, None, SOURCE_MANUAL
        if account.suggestion:
            account.rejected = unique(account.rejected + [normalize_name(account.suggestion)])
        account.suggestion, account.locked, account.pending = None, True, False
        self.save(account)
        return account

    def mark_reuploader(self, ref: str) -> Account:
        account = self._require(ref)
        account.kind, account.alias, account.source = KIND_REUPLOADER, None, SOURCE_MANUAL
        account.suggestion, account.locked, account.pending = None, True, False
        self.save(account)
        return account

    def accept(self, ref: str) -> Account:
        account = self._require(ref)
        if not account.suggestion:
            raise AccountError(f"'{account.name}' has no suggestion to accept")
        name = account.suggestion
        account.kind, account.alias, account.source = KIND_OWN, name, SOURCE_MANUAL
        account.suggestion, account.locked, account.pending = None, True, False
        self._register(name, PRIORITY_UPLOADER)
        self.save(account)
        return account

    def reject(self, ref: str) -> Account:
        account = self._require(ref)
        if not account.suggestion:
            raise AccountError(f"'{account.name}' has no suggestion to reject")
        account.rejected = unique(account.rejected + [normalize_name(account.suggestion)])
        account.suggestion, account.pending = None, False
        self.save(account)
        return account

    def unlock(self, ref: str) -> Account:
        account = self._require(ref)
        account.locked = False
        # The next job that sees the account decides again.
        account.checked_at = None
        self.save(account)
        return account


ACTIONS = ("set", "keep", "reuploader", "accept", "reject", "unlock")


def apply_action(book: AccountBook, action: str, ref: str, name: str | None = None) -> Account:
    """One user action by name (the CLI and the server use the same ones)."""
    if action == "set":
        if not name:
            raise AccountError("'set' needs the artist name")
        return book.set_alias(ref, name)
    handlers = {
        "keep": book.keep, "reuploader": book.mark_reuploader, "accept": book.accept,
        "reject": book.reject, "unlock": book.unlock,
    }
    handler = handlers.get(action)
    if handler is None:
        raise AccountError(f"unknown action '{action}'; use one of {', '.join(ACTIONS)}")
    return handler(ref)


def sorted_accounts(accounts: list[Account]) -> list[Account]:
    """Suggestions first (they wait for the user), then the rest by status and name."""
    order = {STATUS_SUGGESTION: 0, STATUS_NOT_CHECKED: 1, STATUS_CONFIRMED: 2, STATUS_REUPLOADER: 3,
             STATUS_NICK: 4}
    return sorted(accounts, key=lambda item: (order.get(item.status, 9), item.name.casefold()))


# -- evidence ---------------------------------------------------------------------------
@dataclass
class TrackFacts:
    """One track of an account, as far as the decision needs it."""
    id: str
    title: str
    base_title: str
    account_id: str
    account_name: str
    permalink: str
    duration: float | None
    timestamp: float | None
    main: list[str]
    featured: list[str]
    # The title (or SoundCloud's credit) names someone, not just the account.
    titled: bool
    tags: list[str]
    description: str

    @property
    def first_key(self) -> str:
        return normalize_name(self.main[0]) if self.main else ""


def track_facts(info: dict[str, Any], registry: ArtistRegistry | None = None) -> TrackFacts | None:
    """From a yt-dlp info dict or `soundcloud_meta.info_from_api`."""
    from .soundcloud_meta import credited_artists

    track_id = str(info.get("id") or "")
    account_id = str(info.get("uploader_id") or "")
    uploader = strip_decorations(info.get("uploader") or "")
    title = str(info.get("title") or "")
    if not track_id or not account_id or not uploader or not title:
        return None
    official = credited_artists(info, uploader)
    parsed = parse_soundcloud_title(
        title, uploader, official,
        registry.is_known if registry is not None else None,
        registry.canonical if registry is not None else None,
    )
    key = normalize_name(uploader)
    titled = bool(official) or {normalize_name(name) for name in parsed.artists} != {key}
    url = str(info.get("uploader_url") or "")
    permalink = urlsplit(url).path.strip("/").split("/")[0] if "soundcloud.com" in url else ""
    try:
        duration = float(info.get("duration")) if info.get("duration") else None
    except (TypeError, ValueError):
        duration = None
    try:
        timestamp = float(info.get("timestamp")) if info.get("timestamp") else None
    except (TypeError, ValueError):
        timestamp = None
    tags = [str(tag) for tag in info.get("tags") or [] if isinstance(tag, str)]
    return TrackFacts(
        id=track_id, title=title, base_title=parsed.base_title or title, account_id=account_id,
        account_name=uploader, permalink=permalink, duration=duration, timestamp=timestamp,
        main=list(parsed.artists), featured=list(parsed.featured), titled=titled, tags=tags,
        description=str(info.get("description") or ""),
    )


@dataclass
class Evidence:
    tracks: int = 0
    candidate: str | None = None
    share: float = 0.0
    hashtags: int = 0
    description: int = 0
    mixed: bool = False
    names: dict[str, int] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {"tracks": self.tracks, "candidate": self.candidate, "share": round(self.share, 2),
                "hashtags": self.hashtags, "description": self.description, "mixed": self.mixed}


def local_evidence(account_name: str, facts: list[TrackFacts], rejected: list[str] | None = None) -> Evidence:
    """Step 1: the candidate name from the account's own titles."""
    own = normalize_name(account_name)
    evidence = Evidence(tracks=len(facts))
    if not facts:
        return evidence
    counts: Counter[str] = Counter()
    display: dict[str, str] = {}
    for item in facts:
        if item.main:
            counts[item.first_key] += 1
            display.setdefault(item.first_key, item.main[0])
    evidence.names = {display[key]: count for key, count in counts.most_common(10)}
    others = {key: count for key, count in counts.items() if key and key != own}
    other_tracks = sum(others.values())
    top = max(others.values()) if others else 0
    # Many names, none of them dominant: the titles of a reupload account.
    evidence.mixed = (len(others) >= 2 and other_tracks / len(facts) >= MIXED_SHARE
                      and top / len(facts) < MIN_SHARE)
    blocked = set(rejected or [])
    ranked = [(count, key) for key, count in others.items() if key not in blocked]
    if not ranked:
        return evidence
    count, key = max(ranked)
    evidence.share = count / len(facts)
    if len(facts) < MIN_TRACKS or evidence.share < MIN_SHARE:
        return evidence
    evidence.candidate = display[key]
    compact = key.replace(" ", "")
    evidence.hashtags = sum(
        1 for item in facts
        if any(normalize_name(tag.lstrip("#")).replace(" ", "") == compact for tag in item.tags)
    )
    evidence.description = sum(1 for item in facts if key in normalize_name(item.description))
    return evidence


# -- checks against the library and the services -----------------------------------------
@dataclass
class Check:
    name: str
    matches: list[dict[str, Any]] = field(default_factory=list)
    others: list[dict[str, Any]] = field(default_factory=list)
    unreachable: list[str] = field(default_factory=list)
    answered: bool = False

    @property
    def source(self) -> str | None:
        if not self.matches:
            return None
        counts = Counter(item["source"] for item in self.matches)
        best = max(counts.values())
        return next(item["source"] for item in self.matches if counts[item["source"]] == best)

    def summary(self) -> dict[str, Any]:
        return {"name": self.name, "matches": self.matches, "others": self.others,
                "unreachable": self.unreachable, "answered": self.answered}


def _same_artist(hit: Hit, key: str) -> bool:
    return bool(hit.artists) and normalize_name(hit.artists[0]) == key


class Checker:
    """Looks songs up in the library and on the services (artist_lookup.py)."""

    def __init__(self, library: Any = None, services: Any = None):
        self.library = library
        self.services = services

    def _library(self, title: str) -> list[Hit]:
        if self.library is None:
            return []
        try:
            return self.library.find(title)
        except OSError:
            return []

    def check(self, name: str, sample: list[TrackFacts], needed: int = NEEDED_MATCHES) -> Check:
        """Steps 2 and 3: are these tracks by `name` elsewhere?"""
        result = Check(name)
        key = normalize_name(name)
        sample = [item for item in sample if item.duration][:CHECK_TRACKS]
        done: set[str] = set()
        for item in sample:
            if len(result.matches) >= needed:
                return result
            title_key = match_key(item.base_title)
            for hit in self._library(item.base_title):
                if hit.spotify and same_song(hit, title_key, item.duration, MATCH_TOLERANCE):
                    if _same_artist(hit, key):
                        result.matches.append({"source": SOURCE_LIBRARY, "title": item.base_title})
                        done.add(item.id)
                        break
        for item in sample:
            if len(result.matches) >= needed:
                break
            if item.id in done or self.services is None:
                continue
            title_key = match_key(item.base_title)
            song = search_title(item.base_title)
            for source, search in ((SOURCE_SPOTIFY, lambda: self.services.spotify(f"{name} {song}")),
                                   (SOURCE_DEEZER, lambda: self.services.deezer(name, song))):
                try:
                    hits = search()
                except ServiceUnavailable:
                    if source not in result.unreachable:
                        result.unreachable.append(source)
                    continue
                result.answered = True
                same = [hit for hit in hits if same_song(hit, title_key, item.duration, MATCH_TOLERANCE)]
                if any(_same_artist(hit, key) for hit in same):
                    result.matches.append({"source": source, "title": item.base_title})
                    break
                if same:
                    result.others.append({"source": source, "title": item.base_title,
                                          "artist": same[0].artists[0] if same[0].artists else ""})
                    break
        return result


@dataclass
class Original:
    artists: list[str]
    featured: list[str]
    source: str
    by: str = ""      # the account the original was uploaded by (SoundCloud)

    def to_dict(self) -> dict[str, Any]:
        return {"artists": self.artists, "featured": self.featured, "source": self.source, "by": self.by}


# -- one job ------------------------------------------------------------------------------
class RunAccounts:
    """The account decisions of one job, and the rules `build_meta` asks.

    `evaluate` runs before the downloads; the other methods are safe to call
    from the worker threads afterwards."""

    def __init__(self, registry: ArtistRegistry, dashboard: Any = None, library: Any = None,
                 services: Any = None, index: Any = None, today: Callable[[], date] = date.today):
        self.registry = registry
        self.book = AccountBook(registry)
        self.dashboard = dashboard
        self.checker = Checker(library, services)
        self.index = index
        self.today = today
        self._lock = threading.Lock()
        self._originals: dict[str, dict[str, Any]] = {}

    # -- rules for build_meta ------------------------------------------------------------
    def alias(self, account_id: str) -> str | None:
        account = self.book.get(account_id) if account_id else None
        return account.alias if account is not None and account.kind == KIND_OWN else None

    def is_reuploader(self, account_id: str) -> bool:
        account = self.book.get(account_id) if account_id else None
        return account is not None and account.kind == KIND_REUPLOADER

    def alias_by_name(self, name: str) -> str | None:
        key = normalize_name(strip_decorations(name))
        if not key:
            return None
        for account in self.book.all():
            if account.kind == KIND_OWN and account.alias and normalize_name(account.handle) == key:
                return account.alias
        return None

    def original(self, track_id: str) -> dict[str, Any] | None:
        with self._lock:
            found = self._originals.get(track_id)
        if found is None and self.index is not None:
            found = self.index.original(track_id)
        return found if found and found.get("artists") else None

    # -- feedback ---------------------------------------------------------------------------
    def _log(self, text: str) -> None:
        if self.dashboard is not None:
            self.dashboard.log(f"[Artists] {text}")

    def _note(self, kind: str, text: str) -> None:
        if self.dashboard is not None and hasattr(self.dashboard, "record_artists"):
            self.dashboard.record_artists(kind, text)

    # -- the decision -------------------------------------------------------------------------
    def evaluate(self, infos: list[dict[str, Any]], new_ids: set[str],
                 abort: Callable[[], bool] = lambda: False) -> None:
        """Decides the accounts of the job's tracks. `infos` are the job's
        tracks with full metadata, `new_ids` the ones that will be written
        (new downloads, or rechecked files)."""
        by_account: dict[str, list[TrackFacts]] = {}
        for info in infos:
            facts = track_facts(info, self.registry)
            if facts is not None:
                by_account.setdefault(facts.account_id, []).append(facts)
        mixed: set[str] = set()
        for account_id, facts in by_account.items():
            if abort():
                return
            if not any(item.id in new_ids for item in facts):
                continue
            try:
                if self._decide(account_id, facts, new_ids):
                    mixed.add(account_id)
            except ServiceUnavailable:
                continue
        for account_id, facts in by_account.items():
            account = self.book.get(account_id)
            if account is None:
                continue
            if account.kind != KIND_REUPLOADER and not (account.kind == KIND_UNKNOWN and account_id in mixed):
                continue
            for item in facts:
                if abort():
                    return
                if item.id in new_ids and not item.titled:
                    self._find_original(item, account)
        self.registry.flush()

    def _decide(self, account_id: str, facts: list[TrackFacts], new_ids: set[str]) -> bool:
        """Returns True when the account has no decision and mixed titles."""
        latest = facts[-1]
        account = self.book.get(account_id) or Account(account_id)
        account.handle = latest.account_name or account.handle
        account.permalink = latest.permalink or account.permalink
        evidence = local_evidence(account.handle, facts, account.rejected)
        account.evidence = evidence.summary()
        debuglog.event("accounts", "evidence", account=account_id, handle=account.handle,
                       locked=account.locked, kind=account.kind, alias=account.alias, **evidence.summary(),
                       names=evidence.names)
        if account.locked:
            self.book.save(account)
            return False
        today = self.today()

        reevaluate = False
        if account.kind == KIND_OWN and account.alias and account.source != SOURCE_MANUAL:
            untitled = [item for item in facts if item.id in new_ids and not item.titled and item.duration]
            if untitled:
                check = self.checker.check(account.alias, untitled[:2], needed=1)
                debuglog.event("accounts", "recheck", account=account_id, **check.summary())
                if not check.matches and check.answered and not check.unreachable:
                    reevaluate = True
                    self._log(f"Account {account.name}: a new track was not found under "
                              f"'{account.alias}', deciding again")

        candidate = evidence.candidate
        if candidate is None:
            if reevaluate:
                account.alias, account.suggestion, account.pending = None, None, False
                account.checked_at = today.isoformat()
                if evidence.mixed:
                    account.kind, account.source = KIND_REUPLOADER, SOURCE_TITLE
                    self._log(f"Account {account.name} is a reupload account")
                else:
                    account.kind, account.source = KIND_UNKNOWN, None
                debuglog.event("accounts", "verdict", account=account_id, kind=account.kind, alias=None,
                               reason="re-evaluated without a candidate")
            self.book.save(account)
            return account.kind == KIND_UNKNOWN and evidence.mixed

        candidate_key = normalize_name(candidate)
        known = account.alias if account.kind == KIND_OWN and account.alias else account.suggestion
        if (not reevaluate and account.is_fresh(today)
                and (account.kind == KIND_REUPLOADER or normalize_name(known) == candidate_key)):
            self.book.save(account)
            return False

        sample = [item for item in facts if item.first_key == candidate_key]
        # Tracks of this job that will be written first: they are the ones the decision is for.
        sample.sort(key=lambda item: item.id not in new_ids)
        check = self.checker.check(candidate, sample)
        debuglog.event("accounts", "check", account=account_id, **check.summary())
        self._apply_verdict(account, candidate, check, evidence)
        self.book.save(account)
        return False

    def _apply_verdict(self, account: Account, candidate: str, check: Check, evidence: Evidence) -> None:
        today = self.today().isoformat()
        before = (account.kind, account.alias, account.suggestion)
        if len(check.matches) >= NEEDED_MATCHES:
            source = check.source or SOURCE_SPOTIFY
            name = self.registry.register(candidate, PRIORITY_SPOTIFY)
            account.kind, account.alias, account.source = KIND_OWN, name, source
            account.suggestion, account.pending, account.checked_at = None, False, today
            if before[:2] != (KIND_OWN, name):
                self._log(f"Account {account.name} is {name} (confirmed by {SOURCE_LABELS.get(source, source)})")
                self._note("alias", f"{account.name} → {name} ({SOURCE_LABELS.get(source, source)})")
        elif check.others and not check.matches:
            account.kind, account.alias, account.source = KIND_REUPLOADER, None, check.others[0]["source"]
            account.suggestion, account.pending, account.checked_at = None, False, today
            if before[0] != KIND_REUPLOADER:
                self._log(f"Account {account.name} is a reupload account")
        else:
            if check.unreachable:
                # Nothing to conclude: try again on a later run.
                account.pending, account.checked_at = True, None
                self._log(f"Account {account.name} may be {candidate}; "
                          f"{', '.join(SOURCE_LABELS.get(item, item) for item in check.unreachable)} "
                          f"did not answer, checking again next time")
            else:
                account.pending, account.checked_at = False, today
            if account.kind == KIND_OWN and account.source != SOURCE_MANUAL:
                account.kind, account.alias, account.source = KIND_UNKNOWN, None, None
            account.suggestion = candidate
            if before[2] is None or normalize_name(before[2]) != normalize_name(candidate):
                self._log(f"Account {account.name} may be {candidate}: check Artists")
                self._note("suggestion", f"{account.name} → {candidate}?")
        debuglog.event("accounts", "verdict", account=account.id, handle=account.handle, kind=account.kind,
                       alias=account.alias, source=account.source, suggestion=account.suggestion,
                       pending=account.pending, evidence=evidence.summary())

    # -- originals of reuploads ------------------------------------------------------------
    def _find_original(self, item: TrackFacts, account: Account) -> None:
        cached = self.index.original(item.id) if self.index is not None else None
        if cached is not None:
            with self._lock:
                self._originals[item.id] = cached
            if not cached.get("artists"):
                self._note("undetermined", item.base_title)
            return
        try:
            found = self._search_original(item, account)
        except ServiceUnavailable:
            self._log(f"'{item.base_title}': the original artist could not be looked up now")
            self._note("undetermined", item.base_title)
            return
        data = found.to_dict() if found is not None else {"artists": [], "featured": [], "source": None}
        with self._lock:
            self._originals[item.id] = data
        if self.index is not None:
            self.index.set_original(item.id, data)
        debuglog.event("accounts", "original", id=item.id, title=item.base_title, account=account.id,
                       **data)
        if found is None:
            self._log(f"'{item.base_title}': artist not determined (uploaded by {account.name})")
            self._note("undetermined", item.base_title)
        else:
            where = "the original upload" if found.source == SOURCE_SOUNDCLOUD \
                else SOURCE_LABELS.get(found.source, found.source)
            self._log(f"'{item.base_title}': artist from {where} by {', '.join(found.artists)}")

    def _search_original(self, item: TrackFacts, account: Account) -> Original | None:
        key = match_key(item.base_title)
        excluded = {normalize_name(account.handle), normalize_name(account.alias or "")} - {""}

        def other_artist(hit: Hit) -> bool:
            return bool(hit.artists) and normalize_name(hit.artists[0]) not in excluded

        for hit in self.checker._library(item.base_title):
            if hit.soundcloud_id != item.id and other_artist(hit) \
                    and same_song(hit, key, item.duration, ORIGINAL_TOLERANCE):
                return Original(hit.artists[:1], hit.artists[1:], SOURCE_LIBRARY)

        services = self.checker.services
        if services is None:
            return None
        unreachable = False
        song = search_title(item.base_title)
        try:
            hits = services.soundcloud(song)
        except ServiceUnavailable:
            hits, unreachable = [], True
        candidates = []
        for hit in hits:
            if hit.account_id == account.id or hit.soundcloud_id == item.id:
                continue
            if hit.timestamp is None or item.timestamp is None or hit.timestamp >= item.timestamp:
                continue
            owner = self.book.get(hit.account_id)
            if owner is not None and owner.kind == KIND_REUPLOADER:
                continue
            facts = track_facts(hit.info, self.registry)
            if facts is None or not facts.main:
                continue
            # Compared without its artist part: "AQUAKEY - Song" is the song "Song".
            song_hit = Hit(title=facts.base_title, artists=facts.main, duration=hit.duration, source=hit.source)
            if not same_song(song_hit, key, item.duration, ORIGINAL_TOLERANCE):
                continue
            main = [owner.alias if owner is not None and owner.alias
                    and normalize_name(name) == normalize_name(facts.account_name) else name
                    for name in facts.main]
            if {normalize_name(name) for name in main} & excluded:
                continue
            candidates.append((hit.timestamp, Original(main, facts.featured, SOURCE_SOUNDCLOUD, hit.account_name)))
        if candidates:
            return min(candidates, key=lambda pair: pair[0])[1]

        for source, search in ((SOURCE_SPOTIFY, lambda: services.spotify(song)),
                               (SOURCE_DEEZER, lambda: services.deezer(None, song))):
            try:
                found = search()
            except ServiceUnavailable:
                unreachable = True
                continue
            for hit in found:
                if other_artist(hit) and same_song(hit, key, item.duration, ORIGINAL_TOLERANCE):
                    return Original(hit.artists[:1], hit.artists[1:], source)
        if unreachable:
            raise ServiceUnavailable("a service did not answer")
        return None
