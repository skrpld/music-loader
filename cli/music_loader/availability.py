"""Why a track did not come down, and what that means for the run.

Every track that is not saved ends in exactly one category:

* ``unavailable`` - the track cannot be downloaded and trying again will not
  help: it is DRM-protected, only a preview is offered (SoundCloud Go+),
  blocked or removed. It is skipped, listed for the user and does *not* make
  the job fail.
* ``failed`` - something went wrong that is worth looking at. The job is
  marked as having failures.
* ``rate_limited`` and ``network`` - transient: the same download may work a
  little later. They are reported like ``failed`` today; they exist as their
  own categories so that a retry can pick exactly these (see
  `FailureCategory.retryable`) and never an unavailable track.

SoundCloud serves some tracks (mostly label uploads) only as encrypted
streams (``ctr-encrypted-hls`` / ``cbc-encrypted-hls``). yt-dlp does not
decrypt DRM and neither does this project: such a track is detected, skipped
and listed, nothing more.

Two detectors feed the same verdict:

* `classify_track` reads SoundCloud's track JSON (``media.transcodings``,
  ``policy``) *before* anything is downloaded;
* `classify_error` reads yt-dlp's error lines when a download failed anyway.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

SOURCE_SOUNDCLOUD = "SoundCloud"

# yt-dlp names every kind of media a "video"; "DRM protected" is its generic text.
DRM_RE = re.compile(r"\bDRM[ -]protected\b|\bDRM\b.*\bprotected\b", re.IGNORECASE)
NO_FORMAT_RE = re.compile(r"requested format is not available", re.IGNORECASE)
RATE_LIMIT_RE = re.compile(r"\b429\b|too many requests|rate limit", re.IGNORECASE)
_GEO_RE = re.compile(
    r"not available in your country|geo[- ]?restrict|blocked (?:it )?in your country|"
    r"not available in your region",
    re.IGNORECASE,
)
# Only the answer to the track's own metadata request: a 404 elsewhere (a
# stream URL, a cover) is not proof that the track is gone.
_REMOVED_RE = re.compile(r"JSON metadata: HTTP Error 404", re.IGNORECASE)
_NETWORK_RE = re.compile(
    r"timed out|time out|connection (?:reset|refused|aborted|error)|network is unreachable|"
    r"temporary failure in name resolution|name or service not known|unable to connect|"
    r"remote end closed|unexpected eof|ssl.*(?:error|eof)|incompleteread|HTTP Error 5\d\d",
    re.IGNORECASE,
)

_ENCRYPTED_PREFIXES = ("ctr-", "cbc-")


class FailureCategory(str, Enum):
    """Outcome category of a track that was not saved. The values are the
    names used in stats, events and logs."""
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    RATE_LIMITED = "rate_limited"
    NETWORK = "network"

    @property
    def retryable(self) -> bool:
        """True for failures that may disappear on their own. An unavailable
        track is never retryable; a plain failure is left to the user."""
        return self in {FailureCategory.RATE_LIMITED, FailureCategory.NETWORK}


class Reason(str, Enum):
    """Why an unavailable track is unavailable."""
    DRM = "drm"
    PREVIEW = "preview"
    BLOCKED = "blocked"
    REMOVED = "removed"


_REASON_TEXT = {
    Reason.DRM: "DRM-protected on SoundCloud, can't be downloaded",
    Reason.PREVIEW: "only a 30-second preview is available on SoundCloud (Go+)",
    Reason.BLOCKED: "blocked on SoundCloud (region or rights holder)",
    Reason.REMOVED: "no longer available on SoundCloud",
}


@dataclass(frozen=True)
class Verdict:
    category: FailureCategory
    reason: Reason | None = None


FAILED = Verdict(FailureCategory.FAILED)


def unavailable(reason: Reason) -> Verdict:
    return Verdict(FailureCategory.UNAVAILABLE, reason)


@dataclass(frozen=True)
class UnavailableTrack:
    """A track that is skipped because it cannot be downloaded."""
    track_id: str
    title: str
    artist: str
    url: str
    reason: Reason
    duration: float | None = None      # seconds, as SoundCloud lists it
    source: str = SOURCE_SOUNDCLOUD

    def message(self, note: str = "") -> str:
        """``'<title>' by <artist>: DRM-protected on SoundCloud, can't be
        downloaded, skipped`` (`note` is appended when given)."""
        who = f" by {self.artist}" if self.artist else ""
        text = f"'{self.title}'{who}: {_REASON_TEXT[self.reason]}, skipped"
        return f"{text} ({note})" if note else text

    @classmethod
    def from_api(cls, data: dict[str, Any], reason: Reason) -> "UnavailableTrack":
        """From SoundCloud's own track JSON."""
        publisher = data.get("publisher_metadata") if isinstance(data.get("publisher_metadata"), dict) else {}
        user = data.get("user") if isinstance(data.get("user"), dict) else {}
        artist = str(publisher.get("artist") or user.get("username") or "")
        return cls(
            track_id=str(data.get("id") or ""),
            title=str(data.get("title") or data.get("id") or ""),
            artist=artist,
            url=str(data.get("permalink_url") or ""),
            reason=reason,
            duration=_seconds(data.get("full_duration") or data.get("duration"), scale=1000.0),
        )

    @classmethod
    def from_info(cls, info: dict[str, Any], reason: Reason) -> "UnavailableTrack":
        """From a yt-dlp info dict (a flat listing entry or a full extract)."""
        artists = info.get("artists")
        artist = (
            str(artists[0]) if isinstance(artists, list) and artists
            else str(info.get("artist") or info.get("uploader") or "")
        )
        return cls(
            track_id=str(info.get("id") or ""),
            title=str(info.get("title") or info.get("id") or ""),
            artist=artist,
            url=str(info.get("webpage_url") or info.get("url") or ""),
            reason=reason,
            duration=_seconds(info.get("duration")),
        )


def _seconds(value: Any, scale: float = 1.0) -> float | None:
    try:
        number = float(value) / scale
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


# -- SoundCloud track JSON ------------------------------------------------------------------
def _is_encrypted(transcoding: dict[str, Any]) -> bool:
    fmt = transcoding.get("format")
    protocol = str(fmt.get("protocol") or "") if isinstance(fmt, dict) else ""
    return protocol.startswith(_ENCRYPTED_PREFIXES)


def _is_preview(transcoding: dict[str, Any]) -> bool:
    # The same test yt-dlp uses to tag a format as a preview.
    return bool(transcoding.get("snipped")) or "/preview/" in str(transcoding.get("url") or "")


def classify_track(data: Any) -> Reason | None:
    """Reason a SoundCloud track (API JSON) cannot be downloaded, or None
    when it can - or when the JSON does not say, in which case yt-dlp and the
    error classification after the download decide.

    * an offered original download wins over everything else;
    * ``policy: BLOCK`` - blocked;
    * ``policy: SNIP`` - a preview only (DRM when all that is left is
      encrypted);
    * only encrypted transcodings - DRM; encrypted and plain together are
      fine, the plain one is downloaded;
    * plain transcodings that are all previews - a preview only.
    """
    if not isinstance(data, dict):
        return None
    if data.get("downloadable") and data.get("has_downloads_left"):
        return None
    policy = str(data.get("policy") or "").upper()
    if policy == "BLOCK":
        return Reason.BLOCKED

    media = data.get("media")
    raw = media.get("transcodings") if isinstance(media, dict) else None
    transcodings = [item for item in raw or [] if isinstance(item, dict)]
    encrypted = [item for item in transcodings if _is_encrypted(item)]
    plain = [item for item in transcodings if not _is_encrypted(item)]
    full = [item for item in plain if not _is_preview(item)]

    if policy == "SNIP":
        return Reason.DRM if encrypted and not full else Reason.PREVIEW
    if full:
        return None
    if encrypted:
        return Reason.DRM
    if plain:
        return Reason.PREVIEW
    return None


# -- yt-dlp output ---------------------------------------------------------------------------
def classify_error(lines: Iterable[str]) -> Verdict:
    """Verdict for the error lines of a failed yt-dlp run.

    A rate limit comes first: it may be what broke the request that would
    have told more, and a wrong "unavailable" is final while a wrong
    "rate_limited" only costs a retry. Anything unrecognized is a plain
    failure."""
    text = "\n".join(lines)
    if RATE_LIMIT_RE.search(text):
        return Verdict(FailureCategory.RATE_LIMITED)
    if DRM_RE.search(text):
        return unavailable(Reason.DRM)
    if NO_FORMAT_RE.search(text):
        return unavailable(Reason.PREVIEW)
    if _GEO_RE.search(text):
        return unavailable(Reason.BLOCKED)
    if _REMOVED_RE.search(text):
        return unavailable(Reason.REMOVED)
    if _NETWORK_RE.search(text):
        return Verdict(FailureCategory.NETWORK)
    return FAILED
