"""Command-line entry point: argument parsing and the main link-processing loop."""
import argparse
import os
import shlex
import sys
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from . import __version__
from .config import (
    LOGS_DIRNAME,
    LYRICS_MODE_LOOSE,
    LYRICS_MODE_STRICT,
    SPOTIFY_CLIENT_ID_ENV,
    SPOTIFY_CLIENT_SECRET_ENV,
    AppConfig,
)
from .deps import check_dependencies
from .links import Link, parse_link, redact_url
from .lyrics import LyricsService
from .runlog import RunLog
from .soundcloud import download_soundcloud
from .spotify import download_spotify
from .ui import Dashboard


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
    total = len(links)
    dashboard.set_queue(0, total)
    lyrics = None
    if config.lyrics_enabled:
        lyrics = LyricsService(config.lyrics_mode)

    for index, link in enumerate(links, start=1):
        try:
            if link.service == "spotify":
                ok = download_spotify(link, config, dashboard, lyrics)
            else:
                ok = download_soundcloud(link, config, dashboard, lyrics)
            dashboard.record(link.service, ok)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            # One broken link must not abort the whole batch.
            dashboard.record(link.service, False)
            dashboard.log_error("Links", f"Unexpected error while processing '{redact_url(link.url)}': {exc}")

        dashboard.set_queue(index, total)


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


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if argv[:1] == ["serve"]:
        from .server import main as serve_main
        return serve_main(argv[1:])

    console = Console()
    args = parse_args(argv)

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

    dashboard = Dashboard(
        console,
        source_label=f"{len(links)} link(s)",
        output_dir=str(config.music_dir),
        runlog=runlog,
    )
    try:
        with dashboard:
            process_links(links, config, dashboard)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")
        if runlog is not None and runlog.count:
            console.print(
                f"[yellow]{runlog.count} failed operation(s) logged to:[/yellow] {escape(str(runlog.path))}"
            )
        return 130

    print_summary(console, dashboard)
    return 0


if __name__ == "__main__":
    sys.exit(main())
