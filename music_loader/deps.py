"""Checks that required external tools/packages are available before starting,
instead of failing midway through a download with an unclear error."""
import shutil

from rich.console import Console


def check_dependencies(console: Console) -> bool:
    missing = []
    if shutil.which("ffmpeg") is None:
        missing.append("ffmpeg (required for MP3 conversion and embedding covers)")
    if shutil.which("spotdl") is None:
        missing.append("spotdl (pip install spotdl)")
    if shutil.which("yt-dlp") is None:
        missing.append("yt-dlp (pip install yt-dlp)")

    if missing:
        console.print("[bold red]Required components are missing:[/bold red]")
        for item in missing:
            console.print(f"  - {item}")
        console.print("[red]Install the missing components and run the script again.[/red]")
        return False

    try:
        import syncedlyrics  # noqa: F401
    except ImportError:
        console.print(
            "[yellow]'syncedlyrics' is not installed — lyrics for SoundCloud tracks "
            "will not be downloaded. Install with: pip install syncedlyrics[/yellow]"
        )

    # Deno/Node is optional: yt-dlp (used directly for SoundCloud and internally
    # by spotdl for YouTube sources) can use it to solve YouTube's signature
    # challenges. Without it, downloads still work but are more prone to
    # throttling or extraction failures.
    if shutil.which("deno") is None and shutil.which("node") is None:
        console.print(
            "[yellow]Neither 'deno' nor 'node' was found — yt-dlp will fall back to its "
            "built-in JS interpreter for YouTube sources, which is slower and less "
            "reliable. Recommended: install Deno (https://deno.com)[/yellow]"
        )

    return True
