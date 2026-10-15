"""Optional debug log of a whole run (`--debug-log`, job option `debug_log`).

The failure log (runlog.py) only says *that* something failed. The debug log
says *why* a track ended up the way it did: how every link was resolved, the
raw SoundCloud metadata, how the title was parsed into credits, the final
tags, folder and file name, what spotDL matched, every lyrics query and every
candidate with its verdict, and each skip, retry and failure.

It is off by default; nothing is written unless a run turns it on. One file
per run or server job, `debug-<timestamp>.jsonl` in the library's log folder
(or a path given to `--debug-log`), one JSON object per line:

    {"time": "2026-10-15 08:00:00.123", "section": "lyrics", "event": "candidate",
     "provider": "LRCLIB", "artist": "...", "verdict": "rejected", "reason": "artist mismatch"}

Every text is passed through `redact`: private SoundCloud tokens, client ids,
user tokens and passwords never reach the file, and fields named like a
credential are masked whatever their value. Long texts are cut.

The pipelines call `event(...)` wherever they are; it does nothing while no
log is active. A run (the CLI, or one server job; jobs run one at a time)
activates its log with `activate` and drops it with `deactivate`.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .runlog import redact_secrets

# Query parameters and "key: value" pairs that carry credentials.
_CREDENTIAL_RE = re.compile(
    r"(?i)\b(client_id|client_secret|usertoken|user_token|oauth_token|access_token|"
    r"refresh_token|api_key|apikey|token|password|secret|signature)"
    r"(\s*[=:]\s*\"?)([^&\s\"',;]+)"
)
_AUTH_HEADER_RE = re.compile(r"(?i)\b(bearer|oauth)\s+[A-Za-z0-9._~+/=-]{8,}")
# Fields whose value is masked whatever it looks like.
_SECRET_KEYS = {
    "client_id", "client_secret", "usertoken", "user_token", "oauth_token", "access_token",
    "refresh_token", "api_key", "apikey", "token", "password", "secret", "authorization",
    "cookie", "signature",
}
_MAX_TEXT = 2000
_MAX_ITEMS = 200


def redact(text: str) -> str:
    """Masks secret SoundCloud links and credentials in a free-form text."""
    text = redact_secrets(text)
    text = _CREDENTIAL_RE.sub(r"\1\2***", text)
    return _AUTH_HEADER_RE.sub(r"\1 ***", text)


def _clean(value: Any, depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Path):
        value = str(value)
    if isinstance(value, str):
        text = redact(value)
        return text if len(text) <= _MAX_TEXT else text[:_MAX_TEXT] + f"... [{len(text)} chars]"
    if depth >= 4:
        return redact(str(value))[:_MAX_TEXT]
    if isinstance(value, dict):
        return {
            str(key): "***" if str(key).lower() in _SECRET_KEYS else _clean(item, depth + 1)
            for key, item in list(value.items())[:_MAX_ITEMS]
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        items = sorted(value, key=str) if isinstance(value, (set, frozenset)) else list(value)
        return [_clean(item, depth + 1) for item in items[:_MAX_ITEMS]]
    return _clean(str(value), depth)


class DebugLog:
    """Append-only JSON-lines file. Written from many worker threads; every
    line is opened, written and closed under a lock, so the file is always
    readable mid-run and a crash loses nothing."""

    def __init__(self, path: Path):
        path = Path(path).expanduser()
        if path.is_dir():
            path = path / self.default_name()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Fails here, not mid-run, when the file cannot be written.
        with open(path, "a", encoding="utf-8"):
            pass
        self.path = path
        self.count = 0
        self._lock = threading.Lock()

    @staticmethod
    def default_name() -> str:
        return f"debug-{datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl"

    @classmethod
    def in_directory(cls, directory: Path) -> "DebugLog":
        return cls(Path(directory) / cls.default_name())

    def write(self, section: str, name: str, fields: dict[str, Any]) -> None:
        entry = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            "section": section,
            "event": name,
            **_clean(fields),
        }
        try:
            line = json.dumps(entry, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            line = json.dumps({"time": entry["time"], "section": section, "event": name,
                               "error": "unserializable fields"})
        with self._lock:
            self.count += 1
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                # Losing a debug line must never take the run down.
                pass


_active: Optional[DebugLog] = None


def activate(log: Optional[DebugLog]) -> None:
    global _active
    _active = log


def deactivate() -> None:
    activate(None)


def active() -> Optional[DebugLog]:
    return _active


def enabled() -> bool:
    return _active is not None


def event(section: str, name: str, /, **fields: Any) -> None:
    """Records one event in the active debug log; does nothing when it is off."""
    log = _active
    if log is not None:
        log.write(section, name, fields)


def log_unavailable(kind: str, track: Any, note: str = "") -> None:
    """A track skipped because it cannot be downloaded (availability.py)."""
    if _active is not None:
        event("run", "unavailable", service=kind, source=track.source, id=track.track_id,
              artist=track.artist, title=track.title, url=track.url,
              reason=track.reason.value, duration=track.duration, note=note)


def log_start(front_end: str, links: list[Any], config: Any) -> None:
    """The first lines of a run: version, options (never the Spotify
    credentials themselves) and the links as given."""
    if _active is None:
        return
    from . import __version__

    options = {
        key: value for key, value in vars(config).items()
        if key not in {"spotify_client_id", "spotify_client_secret"}
    }
    options["spotify_own_credentials"] = bool(config.spotify_client_id and config.spotify_client_secret)
    event("run", "start", version=__version__, front_end=front_end, options=options)
    for link in links:
        event("links", "link", url=link.url, service=link.service, kind=link.kind,
              link_section=getattr(link, "section", None))
