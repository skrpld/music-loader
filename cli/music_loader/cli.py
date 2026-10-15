"""Command-line entry point: argument parsing and the main link-processing loop."""
import argparse
import os
import shlex
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue
from typing import Any

from rich.console import Console
from rich.markup import escape

from . import __version__, debuglog
from .config import (
    DEFAULT_FILENAME_TEMPLATE,
    FEAT_BOTH,
    FEAT_MODES,
    LOGS_DIRNAME,
    LYRICS_MODE_LOOSE,
    LYRICS_MODE_STRICT,
    SPOTIFY_CLIENT_ID_ENV,
    SPOTIFY_CLIENT_SECRET_ENV,
    AppConfig,
    SoundCloudFormat,
    validate_filename_template,
)
from .deps import check_dependencies
from .links import Link, parse_link, redact_url
from .lyrics import LyricsService
from .process import wait_future
from .runlog import RunLog
from .soundcloud import DownloadAborted, SoundCloudBatch
from .spotify import SpotifyBatch
from .ui import PHASE_DOWNLOADING, PHASE_RESOLVING, Dashboard

# Links resolved at the same time, in all and per service. Each Spotify
# resolve is a whole spotdl run (on Android they take turns anyway, spotdl
# runs one at a time there); SoundCloud listings are cheap and share the
# rate-limit pause of soundcloud.py.
RESOLVE_WORKERS = 4
_RESOLVE_LIMITS = {"spotify": 2, "soundcloud": RESOLVE_WORKERS}


def _filename_template(value: str) -> str:
    try:
        return validate_filename_template(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a number: {value}") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return number


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="music-loader",
        description="Downloads music with metadata from Spotify and SoundCloud, "
                     "fetches verified lyrics, and prepares the library for Symfonium.",
        epilog="Server mode for the Android app: music-loader serve -o <Music folder> "
               "(see music-loader serve --help).",
    )
    parser.add_argument("--version", action="version", version=f"music-loader {__version__}")
    parser.add_argument(
        "source",
        nargs="*",
        help="Spotify/SoundCloud link(s), or a path to a .txt file with one link per line. "
             "If omitted, you will be prompted interactively.",
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="Target Music folder. If omitted, you will be prompted interactively.",
    )

    lyrics = parser.add_argument_group("lyrics")
    lyrics.add_argument(
        "--no-lyrics",
        action="store_true",
        help="Do not look up lyrics at all.",
    )
    lyrics.add_argument(
        "--lyrics-loose",
        action="store_true",
        help="Loose lyrics matching: fuzzy artist/title match and up to 10 s length "
             "difference (plain text only when the timing may not fit). Default is strict: "
             "exact artist, title, version and length (±2 s).",
    )
    lyrics.add_argument(
        "--lyrics-workers",
        type=_positive_int,
        default=2,
        help="Number of parallel lyrics workers (default: 2).",
    )

    library = parser.add_argument_group("library")
    library.add_argument(
        "--recheck",
        action="store_true",
        help="Check tracks that are already downloaded against the current rules again: "
             "tags, folder and file name, broken/preview files, lyrics.",
    )

    soundcloud = parser.add_argument_group("SoundCloud")
    soundcloud.add_argument(
        "--soundcloud-reposts",
        action="store_true",
        help="For a profile link, also download the profile's reposts.",
    )
    soundcloud.add_argument(
        "--soundcloud-likes",
        action="store_true",
        help="For a profile link, also download the profile's likes.",
    )
    soundcloud.add_argument(
        "--soundcloud-fallback",
        action="store_true",
        help="For a track SoundCloud does not give out (DRM-protected, preview only, blocked), "
             "look for the same track on YouTube Music and take it from there when artist, "
             "title, version and length all match. It is another recording or master, so it is "
             "off by default; the file's tags say where it came from.",
    )
    soundcloud.add_argument(
        "--soundcloud-download-workers",
        type=_positive_int,
        default=2,
        help="Number of parallel SoundCloud downloads (default: 2). SoundCloud allows "
             "about 600 requests per 10 minutes; more workers hit that limit sooner.",
    )
    soundcloud.add_argument(
        "--soundcloud-workers",
        type=_positive_int,
        default=4,
        help="Number of parallel SoundCloud conversion/tagging workers (default: 4).",
    )

    formatting = parser.add_argument_group(
        "SoundCloud formatting",
        "How SoundCloud titles are written. --recheck applies a change to tracks already there.",
    )
    formatting.add_argument(
        "--soundcloud-feat",
        choices=FEAT_MODES,
        default=FEAT_BOTH,
        help="Where featured artists go: 'both' (default) = \"(feat. X)\" in the title and X in the "
             "artist tag; 'artist' = artist tag only; 'title' = title only.",
    )
    formatting.add_argument(
        "--soundcloud-keep-producers",
        action="store_true",
        help="Keep producer credits in the title as \"(prod. X)\" (dropped by default).",
    )
    formatting.add_argument(
        "--soundcloud-drop-versions",
        action="store_true",
        help="Remove version markers (Remix, Sped Up, Slowed, Live...) from the title. Lyrics "
             "matching still compares the real version.",
    )
    formatting.add_argument(
        "--soundcloud-keep-promo",
        action="store_true",
        help="Keep promotional noise ([Free DL], (Official Audio), hashtags, emoji) in the title.",
    )
    formatting.add_argument(
        "--soundcloud-filename",
        type=_filename_template,
        default=DEFAULT_FILENAME_TEMPLATE,
        metavar="TEMPLATE",
        help="File name template with {track}, {title}, {artist}, {album}, {album_artist} "
             "(default: '{track} - {title}'; '{track:02}' sets the padding). The usual "
             "file name rules and length limit apply.",
    )

    diagnostics = parser.add_argument_group("diagnostics")
    diagnostics.add_argument(
        "--debug-log",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help="Write a detailed debug log of the run: how every link was resolved, raw "
             "SoundCloud metadata, title parsing, final tags/folder/file name, what spotDL "
             "matched, every lyrics query and candidate with its verdict. Secrets are masked. "
             f"Without PATH (or with a folder) the file goes to {LOGS_DIRNAME}/debug-<time>.jsonl "
             "in the Music folder. Off by default.",
    )

    spotify = parser.add_argument_group("Spotify")
    spotify.add_argument(
        "--spotify-threads",
        type=_positive_int,
        default=4,
        help="Number of parallel spotdl downloads (default: 4).",
    )
    spotify.add_argument(
        "--spotify-client-id",
        default=None,
        help=f"Own Spotify application client id. Not needed normally; with credentials "
             f"spotdl uses the official Spotify Web API. Falls back to "
             f"{SPOTIFY_CLIENT_ID_ENV}.",
    )
    spotify.add_argument(
        "--spotify-client-secret",
        default=None,
        help=f"Own Spotify application client secret. Prefer the {SPOTIFY_CLIENT_SECRET_ENV} "
             f"environment variable: command-line arguments are visible to other users "
             f"of the machine.",
    )
    return parser.parse_args(argv)


def _reclaim_debug_log_link(args: argparse.Namespace) -> None:
    """`--debug-log <link>`: the optional PATH took a link; it is a link to download."""
    if args.debug_log and parse_link(args.debug_log) is not None:
        args.source.append(args.debug_log)
        args.debug_log = ""


def collect_links(source_args: list[str], console: Console) -> tuple[list[Link], list[str]]:
    """Returns (valid links, rejected entries)."""
    if not source_args:
        console.print("[bold]Enter a link (or several, separated by spaces), "
                      "or a path to a links.txt file:[/bold]")
        entry = input("> ").strip()
        # A pasted line may hold more than one link; a quoted path with
        # spaces still stays in one piece. Windows paths keep their "\".
        if entry:
            try:
                source_args = shlex.split(entry, posix=os.name != "nt")
                source_args = [item.strip('"') for item in source_args]
            except ValueError:
                source_args = [entry]
        else:
            source_args = []

    links: list[Link] = []
    rejected: list[str] = []
    seen: set[str] = set()

    def add(text: str) -> None:
        link = parse_link(text)
        if link is None:
            rejected.append(text)
            return
        if link.url not in seen:
            seen.add(link.url)
            links.append(link)

    for entry in source_args:
        if parse_link(entry) is None:
            path = Path(entry).expanduser()
            try:
                is_file = path.is_file()
            except OSError:
                is_file = False
            if is_file:
                try:
                    with open(path, "r", encoding="utf-8-sig") as f:
                        for raw_line in f:
                            line = raw_line.strip()
                            if line and not line.startswith("#"):
                                add(line)
                except OSError as exc:
                    console.print(f"[red]Could not read '{escape(str(path))}': {escape(str(exc))}[/red]")
                continue
        add(entry)
    return links, rejected


def resolve_output(output_arg: str | None, console: Console) -> str:
    if output_arg:
        return output_arg
    console.print("[bold]Target Music folder:[/bold] (default: ./Music)")
    entry = input("> ").strip()
    return entry or "./Music"


def resolve_spotify_credentials(args: argparse.Namespace) -> tuple[str | None, str | None]:
    """Command-line values win, the environment is the fallback.

    Both halves are required: a half-filled pair is treated as no
    credentials at all.
    """
    client_id = args.spotify_client_id or os.environ.get(SPOTIFY_CLIENT_ID_ENV) or None
    client_secret = (
        args.spotify_client_secret or os.environ.get(SPOTIFY_CLIENT_SECRET_ENV) or None
    )
    if not client_id or not client_secret:
        return None, None
    return client_id, client_secret


def process_links(links: list[Link], config: AppConfig, dashboard: Dashboard) -> None:
    """Runs a job in two phases: every link is resolved first (in parallel),
    so the real number of tracks is known before the first download; then the
    tracks of all links of a service are downloaded together, each once."""
    lyrics = None
    if config.lyrics_enabled:
        lyrics = LyricsService(config.lyrics_mode)
    abort = threading.Event()
    batches: dict[str, Any] = {
        "spotify": SpotifyBatch(config, dashboard, abort),
        "soundcloud": SoundCloudBatch(config, dashboard, lyrics, abort),
    }

    plans = _resolve_links(links, batches, dashboard, abort)

    services = list(dict.fromkeys(link.service for link in links))
    for service in services:
        resolved = [plan for plan in plans if plan is not None and plan.link.service == service]
        dashboard.add_tracks_total(service, batches[service].track_count(resolved))

    total = len(links)
    completed = 0
    for link, plan in zip(links, plans):
        if plan is None or not plan.resolved:
            dashboard.record(link.service, False)
            completed += 1
    dashboard.set_queue(completed, total, PHASE_DOWNLOADING)
    for service in services:
        group = [plan for plan in plans if plan is not None and plan.resolved and plan.link.service == service]
        if not group:
            continue
        try:
            if service == "spotify":
                results = batches[service].download(group, lyrics)
            else:
                results = batches[service].download(group)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            # One broken service must not abort the whole job.
            results = [False for _ in group]
            dashboard.log_error("Links", f"Unexpected error while downloading from {service}: {exc}")
        for ok in results:
            dashboard.record(service, ok)
        completed += len(group)
        dashboard.set_queue(completed, total, PHASE_DOWNLOADING)


def _resolve_links(links: list[Link], batches: dict[str, Any], dashboard: Dashboard,
                   abort: threading.Event) -> list[Any]:
    """Resolves every link, several at a time; one plan per link, None for a
    link that broke unexpectedly. A link that fails never holds up the others.
    Ctrl+C stops the resolves in flight (`abort`) before it is passed on."""
    total = len(links)
    plans: list[Any] = [None] * total
    if not links:
        dashboard.set_queue(0, 0, PHASE_RESOLVING)
        return plans
    workers = min(RESOLVE_WORKERS, total)
    limits = {service: threading.BoundedSemaphore(count) for service, count in _RESOLVE_LIMITS.items()}
    slots: Queue[int] = Queue()
    for slot in range(workers):
        slots.put(slot)
    lock = threading.Lock()
    finished = [0]
    dashboard.set_queue(0, total, PHASE_RESOLVING)

    def resolve(index: int, link: Link) -> None:
        debuglog.event("links", "resolve_queued", url=link.url, service=link.service)
        limit = limits[link.service]
        while not limit.acquire(timeout=0.5):
            if abort.is_set():
                return
        slot = slots.get()
        try:
            if abort.is_set():
                return
            try:
                plans[index] = batches[link.service].resolve(link, slot)
            except DownloadAborted:
                raise KeyboardInterrupt
            except Exception as exc:
                dashboard.log_error("Links", f"Unexpected error while resolving '{redact_url(link.url)}': {exc}")
            with lock:
                finished[0] += 1
                dashboard.set_queue(finished[0], total, PHASE_RESOLVING)
        finally:
            dashboard.finish_file(slot)
            slots.put(slot)
            limit.release()

    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="resolve")
    aborted = False
    try:
        for future in [pool.submit(resolve, index, link) for index, link in enumerate(links)]:
            wait_future(future)
    except BaseException:
        # Ctrl+C: stop the spotdl/yt-dlp runs in flight, drop the queued links.
        aborted = True
        abort.set()
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=aborted)
    return plans


def print_summary(console: Console, dashboard: Dashboard) -> None:
    stats = dashboard.stats
    total_ok = stats.spotify_ok + stats.soundcloud_ok
    total_fail = stats.spotify_fail + stats.soundcloud_fail
    if total_ok + total_fail == 0:
        return

    console.print()
    console.rule("Summary")
    console.print(
        f"Spotify:    [green]{stats.spotify_ok} link(s) ok[/green] / "
        f"[red]{stats.spotify_fail} failed[/red]"
        f"  -  tracks: [green]{stats.spotify_tracks_done} downloaded[/green], "
        f"[cyan]{stats.spotify_tracks_skipped} already had[/cyan], "
        f"[red]{stats.spotify_tracks_failed} failed[/red]"
    )
    console.print(
        f"SoundCloud: [green]{stats.soundcloud_ok} link(s) ok[/green] / "
        f"[red]{stats.soundcloud_fail} failed[/red]"
        f"  -  tracks: [green]{stats.soundcloud_tracks_done} downloaded[/green], "
        f"[cyan]{stats.soundcloud_tracks_skipped} already had[/cyan], "
        f"[red]{stats.soundcloud_tracks_failed} failed[/red]"
        + (f", [yellow]{stats.soundcloud_tracks_unavailable} unavailable[/yellow]"
           if stats.soundcloud_tracks_unavailable else "")
    )
    console.print(
        f"Lyrics:     [green]{stats.lyrics_ok} found[/green] / "
        f"[yellow]{stats.lyrics_fail} not found[/yellow] / "
        f"[dim]{stats.lyrics_skipped} skipped[/dim]"
    )

    if dashboard.runlog is not None and dashboard.runlog.count:
        console.print(
            f"\n[yellow]{dashboard.runlog.count} failed operation(s) logged to:[/yellow] "
            f"{escape(str(dashboard.runlog.path))}"
        )
    if dashboard.runlog is not None and dashboard.runlog.unavailable_count:
        console.print(
            f"\n[yellow]{dashboard.runlog.unavailable_count} track(s) SoundCloud does not give out "
            f"(DRM, preview, blocked) - not failures, listed in:[/yellow] "
            f"{escape(str(dashboard.runlog.unavailable_path))}"
        )


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if argv[:1] == ["serve"]:
        from .server import main as serve_main
        return serve_main(argv[1:])

    console = Console()
    args = parse_args(argv)
    _reclaim_debug_log_link(args)

    if not check_dependencies(console):
        return 1

    try:
        links, rejected = collect_links(args.source, console)
    except (KeyboardInterrupt, EOFError):
        console.print("\n[yellow]Interrupted by user.[/yellow]")
        return 130
    for entry in rejected:
        console.print(f"[yellow]Skipped (not a Spotify/SoundCloud link): {escape(entry[:200])}[/yellow]")
    if not links:
        console.print("[red]No links provided.[/red]")
        return 1

    try:
        output = resolve_output(args.output, console)
    except (KeyboardInterrupt, EOFError):
        console.print("\n[yellow]Interrupted by user.[/yellow]")
        return 130

    config = AppConfig.from_output_dir(Path(output))
    config.soundcloud_postprocess_workers = args.soundcloud_workers
    config.soundcloud_download_workers = args.soundcloud_download_workers
    config.lyrics_workers = args.lyrics_workers
    config.spotify_threads = args.spotify_threads
    config.spotify_client_id, config.spotify_client_secret = resolve_spotify_credentials(args)
    config.lyrics_enabled = not args.no_lyrics
    config.lyrics_mode = LYRICS_MODE_LOOSE if args.lyrics_loose else LYRICS_MODE_STRICT
    config.recheck = args.recheck
    config.soundcloud_reposts = args.soundcloud_reposts
    config.soundcloud_likes = args.soundcloud_likes
    config.soundcloud_fallback = args.soundcloud_fallback
    config.soundcloud_format = SoundCloudFormat.from_args(args)
    try:
        config.ensure_dirs()
    except OSError as exc:
        console.print(f"[red]Could not use the target folder '{escape(str(config.music_dir))}': "
                      f"{escape(str(exc))}[/red]")
        return 1

    try:
        runlog = RunLog(config.music_dir / LOGS_DIRNAME)
    except OSError as exc:
        console.print(f"[yellow]Could not create the failure log: {escape(str(exc))}[/yellow]")
        runlog = None
    for entry in rejected:
        if runlog is not None:
            runlog.record("Links", f"Skipped (not a Spotify/SoundCloud link): {entry[:200]}")

    debug = None
    if args.debug_log is not None:
        try:
            debug = debuglog.DebugLog(Path(args.debug_log) if args.debug_log
                                      else config.music_dir / LOGS_DIRNAME)
        except OSError as exc:
            console.print(f"[yellow]Could not create the debug log: {escape(str(exc))}[/yellow]")

    dashboard = Dashboard(
        console,
        source_label=f"{len(links)} link(s)",
        output_dir=str(config.music_dir),
        runlog=runlog,
    )
    debuglog.activate(debug)
    try:
        debuglog.log_start("cli", links, config)
        for entry in rejected:
            debuglog.event("links", "rejected", entry=entry[:200])
        with dashboard:
            process_links(links, config, dashboard)
        debuglog.event("run", "finished", status="completed", stats=vars(dashboard.stats))
    except KeyboardInterrupt:
        debuglog.event("run", "finished", status="cancelled")
        console.print("\n[yellow]Interrupted by user.[/yellow]")
        if runlog is not None and runlog.count:
            console.print(
                f"[yellow]{runlog.count} failed operation(s) logged to:[/yellow] {escape(str(runlog.path))}"
            )
        _print_debug_log(console, debug)
        return 130
    finally:
        debuglog.deactivate()

    print_summary(console, dashboard)
    _print_debug_log(console, debug)
    return 0


def _print_debug_log(console: Console, debug: "debuglog.DebugLog | None") -> None:
    if debug is not None:
        console.print(f"\n[dim]Debug log ({debug.count} entries):[/dim] {escape(str(debug.path))}")


if __name__ == "__main__":
    sys.exit(main())
