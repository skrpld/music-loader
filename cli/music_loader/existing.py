"""Tracks that are already in the library: are they outdated?

The "Existing tracks" setting (config.EXISTING_MODES) decides what happens
to a track a run finds in the library: kept as it is (only its id is bound
to the file), rewritten when outdated, or always rewritten.

A file is outdated when

* it was written under older rules: its `TXXX:MUSIC_LOADER_FORMAT` is lower
  than `config.FORMAT_VERSION` (a file without the frame is version 0), or
* one of its artists changed since it was written: the artist registry now
  spells a name on the file differently, or (SoundCloud) the decision for
  the uploader's account (accounts.py: an alias, a reupload account) is not
  the one the file was written with. The index entry of a SoundCloud track
  keeps that decision as `account`; rewriting the file updates it, which is
  what clears the mark.
"""
from __future__ import annotations

from typing import Any

from .config import FORMAT_VERSION

REASON_FORMAT = "format"
REASON_ARTISTS = "artists"
REASON_ACCOUNT = "account"


def account_state(accounts: Any, account_id: str) -> str:
    """What an account decision changes in the files of its tracks:
    "own:<alias>", "reuploader", or "" for no decision."""
    if accounts is None or not account_id:
        return ""
    alias = accounts.alias(account_id)
    if alias:
        return f"own:{alias}"
    return "reuploader" if accounts.is_reuploader(account_id) else ""


def artists_changed(tags: dict[str, Any], registry: Any) -> bool:
    """True when the registry knows a name on the file under another
    spelling than the one written."""
    if registry is None:
        return False
    names = list(tags.get("artists") or [])
    if tags.get("album_artist"):
        names.append(tags["album_artist"])
    return any(registry.is_known(name) and registry.canonical(name) != name for name in names)


def outdated_reason(tags: dict[str, Any], registry: Any = None,
                    written_account: str | None = None, current_account: str | None = None) -> str | None:
    """Why a file (its `tags.read_tags`) is outdated; None when it is current.
    `written_account` is the account decision the file was written with
    (None: unknown, not compared)."""
    if int(tags.get("format") or 0) < FORMAT_VERSION:
        return REASON_FORMAT
    if artists_changed(tags, registry):
        return REASON_ARTISTS
    if written_account is not None and current_account is not None and written_account != current_account:
        return REASON_ACCOUNT
    return None

