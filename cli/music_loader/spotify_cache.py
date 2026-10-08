"""Remembers the query hashes of Spotify's web player for spotDL's built-in client.

spotDL's built-in client (SpotipyFree on top of spotapi) talks to the private
API of the web player. Every query needs a hash that only the player's
JavaScript contains, and spotapi finds it by downloading the player's main
script and then every chunk of it - hundreds of requests, about 70 seconds for
one track on a phone. It does that again for every client object it creates:
one per track, one per album, one per release of an artist. A discography took
many minutes with no output, and a single track more than a minute.

The hashes only change with the build of the player (its script URL carries a
content hash), so they are kept per build, in memory and in a small cache file.
A client still asks the start page for the current build (two small requests),
then finds the hash it needs without the chunk download. A build that is not in
the cache is handled exactly as before, and the result is remembered.

`install()` patches `spotapi.client.BaseClient.part_hash`. It does nothing when
spotapi is missing or looks different (another version), and every problem of
the cache itself falls back to spotapi's own way.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Optional

CACHE_FILENAME = "spotify-hashes.json"
_MAX_BUILDS = 3  # the player is rebuilt often; older builds are of no use
_HASH_RE = re.compile(r"[0-9a-f]{64}")
# How spotapi finds them in the player: "getTrack","query","<sha256>"
_OPERATION_RE = re.compile(r'"([A-Za-z0-9_]+)","(?:query|mutation)","([0-9a-f]{64})"')

_lock = threading.Lock()
_memory: Optional[dict[str, dict[str, str]]] = None
_MARK = "_music_loader_original_part_hash"


def cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    if not base:
        try:
            base = str(Path.home() / ".cache")
        except RuntimeError:  # no home directory
            base = tempfile.gettempdir()
    return Path(base) / "music-loader" / CACHE_FILENAME


def _read() -> dict[str, dict[str, str]]:
    loaded: dict[str, dict[str, str]] = {}
    try:
        raw = json.loads(cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return loaded
    if isinstance(raw, dict):
        for build, hashes in raw.items():
            if isinstance(build, str) and isinstance(hashes, dict):
                loaded[build] = {
                    name: value for name, value in hashes.items()
                    if isinstance(name, str) and isinstance(value, str) and _HASH_RE.fullmatch(value)
                }
    return loaded


def _load() -> dict[str, dict[str, str]]:
    global _memory
    if _memory is None:
        _memory = _read()
    return _memory


def _save(data: dict[str, dict[str, str]]) -> None:
    path = cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, tmp = tempfile.mkstemp(dir=path.parent, prefix=CACHE_FILENAME, suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(data, stream)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError:
        pass  # a cache that cannot be written is only a slower start next time


def lookup(build: str, name: str) -> Optional[str]:
    with _lock:
        return _load().get(build, {}).get(name)


def remember(build: str, hashes: dict[str, str]) -> None:
    """Stores the hashes (operation name -> hash) of one build of the player."""
    hashes = {name: value for name, value in hashes.items() if _HASH_RE.fullmatch(value or "")}
    if not (build and hashes):
        return
    with _lock:
        data = _load()
        known = data.get(build)
        if known is not None and all(known.get(name) == value for name, value in hashes.items()):
            return
        # Another process may have written meanwhile: merge, do not overwrite.
        for other_build, other_hashes in _read().items():
            data[other_build] = {**other_hashes, **data.get(other_build, {})}
        data[build] = {**data.get(build, {}), **hashes}
        data[build] = data.pop(build)  # last: the build written last stays
        while len(data) > _MAX_BUILDS:
            data.pop(next(iter(data)))
        _save(data)


def forget_memory() -> None:
    """Drops the in-memory copy (tests; a new process starts like this)."""
    global _memory
    with _lock:
        _memory = None


def install() -> bool:
    """Patches spotapi's hash lookup; True when the cache is in place."""
    try:
        from spotapi import client as spotapi_client
    except Exception:
        return False
    base = getattr(spotapi_client, "BaseClient", None)
    with _lock:
        if base is None or not hasattr(base, "get_session"):
            return False
        if getattr(base, _MARK, None) is not None:
            return True
        original = getattr(base, "part_hash", None)
        if original is None:
            return False

        def part_hash(self, name):
            js_pack = getattr(self, "js_pack", None)
            if not isinstance(js_pack, str):
                # The start page tells the current build of the player: one page and
                # one token request instead of the whole script. Errors are spotapi's.
                self.get_session()
                js_pack = getattr(self, "js_pack", None)
            build = js_pack if isinstance(js_pack, str) and js_pack else None
            if build:
                try:
                    cached = lookup(build, name)
                except Exception:  # the cache must never break spotDL
                    cached = None
                if cached:
                    return cached
            value = original(self, name)
            if build:
                try:
                    # The download holds the hashes of every operation: keep them all.
                    found = dict(_OPERATION_RE.findall(str(getattr(self, "raw_hashes", ""))))
                    if isinstance(value, str):
                        found.setdefault(name, value)
                    remember(build, found)
                except Exception:
                    pass
            return value

        base.part_hash = part_hash
        setattr(base, _MARK, original)
        return True
