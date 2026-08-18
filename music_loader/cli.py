"""Command-line entry point: argument parsing and the main link-processing loop."""
import argparse
import sys
from pathlib import Path

from rich.console import Console

from .config import AppConfig, LOGS_DIRNAME
from .deps import check_dependencies
from .runlog import RunLog
from .soundcloud import download_soundcloud
from .spotify import download_spotify
from .ui import Dashboard


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="music-loader",
        description="Downloads music with metadata from Spotify and SoundCloud, "
                     "fetches synced lyrics, and prepares the library for Symfonium.",
    )
    parser.add_argument(
        "source",
        nargs="*",
        help="Spotify/SoundCloud link(s), or a path to a .txt file with one link per line. "
             "If omitted, you will be prompted interactively.",
    )
    parser.add_argument(
        "--soundcloud-workers",
        type=int,
        default=4,
        help="Number of parallel SoundCloud post-processing workers (default: 4).",
    )
    parser.add_argument(
        "--lyrics-workers",
        type=int,
        default=2,
        help="Number of parallel lyrics workers (default: 2).",
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="Target Music folder. If omitted, you will be prompted interactively.",
    )
    return parser.parse_args(argv)


def collect_links(source_args: list[str], console: Console) -> list[str]:
    if not source_args:
        try:
            console.print("[bold]Enter a link, or a path to a links.txt file:[/bold]")
            entry = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            return []
        source_args = [entry] if entry else []

    links: list[str] = []
    seen: set[str] = set()
    for entry in source_args:
        try:
            path = Path(entry).expanduser()
            is_file = path.is_file()
        except OSError:
            is_file = False
        if is_file:
            try:
                with open(path, "r", encoding="utf-8-sig") as f:
                    raw_lines = [line.strip() for line in f]
            except OSError as exc:
                console.print(f"[red]Could not read '{entry}': {exc}[/red]")
                continue
            candidates = [line for line in raw_lines if line and not line.startswith("#")]
        else:
            candidates = [entry]

        for link in candidates:
            if link not in seen:
                seen.add(link)
                links.append(link)
    return links


def resolve_output(output_arg: str | None, console: Console) -> str:
    if output_arg:
        return output_arg
    console.print("[bold]Target Music folder:[/bold] (default: ./Music)")
    try:
        entry = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        entry = ""
    return entry or "./Music"


def process_links(links: list[str], config: AppConfig, dashboard: Dashboard) -> None:
    total = len(links)
    dashboard.set_queue(0, total)

    for index, link in enumerate(links, start=1):
        lowered = link.lower()
        if "spotify.com" in lowered or lowered.startswith("spotify:"):
            ok = download_spotify(link, config.music_dir, dashboard)
            dashboard.record("spotify", ok)
        elif "soundcloud.com" in lowered or "snd.sc" in lowered:
            ok = download_soundcloud(
                link,
                config.soundcloud_dir,
                dashboard,
                postprocess_workers=config.soundcloud_postprocess_workers,
                lyrics_workers=config.lyrics_workers,
            )
            dashboard.record("soundcloud", ok)
        else:
            dashboard.log_error("Links", f"Unsupported link (not Spotify/SoundCloud): {link}")

        dashboard.set_queue(index, total)


def print_summary(console: Console, dashboard: Dashboard) -> None:
    stats = dashboard.stats
    total_ok = stats.spotify_ok + stats.soundcloud_ok
    total_fail = stats.spotify_fail + stats.soundcloud_fail
    failures = dashboard.runlog.count if dashboard.runlog is not None else 0
    # A run where every link was unsupported produced no link stats at all and
    # used to end without printing anything, hiding the errors.
    if total_ok + total_fail == 0 and failures == 0:
        return

    console.print()
    console.rule("Summary")
    console.print(
        f"Spotify:    [green]{stats.spotify_ok} link(s) ok[/green] / [red]{stats.spotify_fail} failed[/red]"
        f"  -  tracks: [green]{stats.spotify_tracks_done} downloaded[/green], "
        f"[cyan]{stats.spotify_tracks_skipped} already had[/cyan], "
        f"[red]{stats.spotify_tracks_failed} failed[/red]"
    )
    console.print(
        f"SoundCloud: [green]{stats.soundcloud_ok} link(s) ok[/green] / [red]{stats.soundcloud_fail} failed[/red]"
        f"  -  tracks: [green]{stats.soundcloud_tracks_done} downloaded[/green], "
        f"[cyan]{stats.soundcloud_tracks_skipped} already had[/cyan], "
        f"[red]{stats.soundcloud_tracks_failed} failed[/red]"
    )
    console.print(
        f"Lyrics:     [green]{stats.lyrics_ok} found[/green] / [yellow]{stats.lyrics_fail} missing[/yellow]"
    )

    if failures:
        console.print(
            f"\n[yellow]{failures} failed operation(s) logged to:[/yellow] "
            f"{dashboard.runlog.path}"
        )


def main(argv: list[str] | None = None) -> int:
    console = Console()
    args = parse_args(argv if argv is not None else sys.argv[1:])

    if not check_dependencies(console):
        return 1

    links = collect_links(args.source, console)
    if not links:
        console.print("[red]No links provided.[/red]")
        return 1

    output = resolve_output(args.output, console)
    try:
        config = AppConfig.from_output_dir(Path(output))
        config.soundcloud_postprocess_workers = max(1, args.soundcloud_workers)
        config.lyrics_workers = max(1, args.lyrics_workers)
        config.ensure_dirs()
    except OSError as exc:
        console.print(f"[red]Could not use '{output}' as the target folder: {exc}[/red]")
        return 1

    try:
        runlog = RunLog(config.music_dir / LOGS_DIRNAME)
    except OSError as exc:
        console.print(f"[yellow]Failure log is disabled ({exc})[/yellow]")
        runlog = None

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
            console.print(f"[yellow]{runlog.count} failed operation(s) logged to:[/yellow] {runlog.path}")
        return 130

    print_summary(console, dashboard)
    if dashboard.stats.spotify_fail or dashboard.stats.soundcloud_fail:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
