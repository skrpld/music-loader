"""Runs one server job in its own process: `python -m music_loader.worker`.

The server writes the job as JSON to stdin and reads progress events (see
events.py), one JSON object per line, from stdout. A separate process per job
lets the server cancel a run exactly the way Ctrl+C stops the command-line
tool: SIGINT (CTRL_BREAK on Windows) raises KeyboardInterrupt in the main
thread, and the existing cleanup stops yt-dlp/spotdl and their workers,
removes partial files and keeps the indexes consistent.

Job spec:

    {"links": ["https://open.spotify.com/album/..."], "output": "/srv/Music",
     "lyrics": "strict" | "loose" | "off", "recheck": false,
     "soundcloud_reposts": false, "soundcloud_likes": false,
     "spotify_threads": 4, "soundcloud_download_workers": 2,
     "soundcloud_workers": 4, "lyrics_workers": 2}

Spotify credentials are read from the environment, as in the CLI.
"""
from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path
from typing import Any

from .config import (
    LOGS_DIRNAME,
    LYRICS_MODE_LOOSE,
    LYRICS_MODE_STRICT,
    SPOTIFY_CLIENT_ID_ENV,
    SPOTIFY_CLIENT_SECRET_ENV,
    AppConfig,
)
from .events import EventDashboard
from .links import Link, parse_link
from .runlog import RunLog


def _interrupt(signum, frame) -> None:
    raise KeyboardInterrupt


def _install_signal_handlers() -> None:
    # A server started in the background may hand down an ignored SIGINT;
    # the cancel path relies on it raising KeyboardInterrupt. SIGTERM (a
    # service manager stopping everything) gets the same cleanup.
    signal.signal(signal.SIGINT, signal.default_int_handler)
    signal.signal(signal.SIGTERM, _interrupt)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _interrupt)


def _positive(spec: dict[str, Any], key: str, default: int) -> int:
    value = spec.get(key, default)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else default


def build_config(spec: dict[str, Any]) -> AppConfig:
    config = AppConfig.from_output_dir(Path(spec["output"]))
    config.spotify_threads = _positive(spec, "spotify_threads", config.spotify_threads)
    config.soundcloud_download_workers = _positive(
        spec, "soundcloud_download_workers", config.soundcloud_download_workers)
    config.soundcloud_postprocess_workers = _positive(
        spec, "soundcloud_workers", config.soundcloud_postprocess_workers)
    config.lyrics_workers = _positive(spec, "lyrics_workers", config.lyrics_workers)
    lyrics = spec.get("lyrics", LYRICS_MODE_STRICT)
    config.lyrics_enabled = lyrics != "off"
    config.lyrics_mode = LYRICS_MODE_LOOSE if lyrics == LYRICS_MODE_LOOSE else LYRICS_MODE_STRICT
    config.recheck = spec.get("recheck") is True
    config.soundcloud_reposts = spec.get("soundcloud_reposts") is True
    config.soundcloud_likes = spec.get("soundcloud_likes") is True
    client_id = os.environ.get(SPOTIFY_CLIENT_ID_ENV) or None
    client_secret = os.environ.get(SPOTIFY_CLIENT_SECRET_ENV) or None
    if client_id and client_secret:
        config.spotify_client_id, config.spotify_client_secret = client_id, client_secret
    return config


def run(spec: dict[str, Any], events: EventDashboard) -> int:
    # Imported here: cli pulls in every pipeline module, and a broken
    # install should still be reported as an event, not a bare traceback.
    from .cli import process_links

    links: list[Link] = []
    for entry in spec.get("links") or []:
        link = parse_link(entry) if isinstance(entry, str) else None
        if link is not None and link not in links:
            links.append(link)
    if not links:
        events.emit({"type": "finished", "status": "failed", "message": "No valid links"})
        return 1

    config = build_config(spec)
    try:
        config.ensure_dirs()
    except OSError as exc:
        events.emit({"type": "finished", "status": "failed",
                     "message": f"Could not use the target folder '{config.music_dir}': {exc}"})
        return 1
    try:
        events.runlog = RunLog(config.music_dir / LOGS_DIRNAME)
        events.emit({"type": "runlog", "path": str(events.runlog.path)})
    except OSError as exc:
        events.log(f"[Server] Could not create the failure log: {exc}")

    try:
        process_links(links, config, events)
    except KeyboardInterrupt:
        events.finish_file(None)
        events.emit({"type": "finished", "status": "cancelled", "message": None})
        return 130
    events.emit({"type": "finished", "status": "completed", "message": None})
    return 0


def main() -> int:
    _install_signal_handlers()
    # Events get their own copy of stdout; anything else a library prints
    # goes to stderr instead of corrupting the event stream.
    stream = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", newline="\n")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    events = EventDashboard(stream)
    try:
        spec = json.load(sys.stdin)
        if not isinstance(spec, dict) or not isinstance(spec.get("output"), str):
            raise ValueError("job spec must be an object with an 'output' folder")
        return run(spec, events)
    except KeyboardInterrupt:
        events.emit({"type": "finished", "status": "cancelled", "message": None})
        return 130
    except Exception as exc:
        events.emit({"type": "finished", "status": "failed", "message": f"{type(exc).__name__}: {exc}"})
        return 1
    finally:
        try:
            stream.close()
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
