"""Checks that required external tools/packages are available before starting,
instead of failing midway through a download with an unclear error."""
import shutil

from rich.console import Console

from .process import module_available, tool_command


def check_dependencies(console: Console) -> bool:
    missing = []
    if shutil.which("ffmpeg") is None:
        missing.append("ffmpeg (required for MP3 conversion and covers)")
    if tool_command("spotdl") is None:
        missing.append("spotdl (pip install spotdl)")
    if tool_command("yt-dlp") is None:
        missing.append("yt-dlp (pip install yt-dlp)")
    if not module_available("mutagen"):
        missing.append("mutagen (pip install mutagen) - needed for tags and library checks")

    if missing:
        console.print("[bold red]Required components are missing:[/bold red]")
        for item in missing:
            console.print(f"  - {item}")
        console.print("[red]Install the missing components and run the script again.[/red]")
        return False

    for name in ("yt-dlp", "spotdl"):
        command = tool_command(name)
        if command and len(command) == 1:
            console.print(
                f"[yellow]'{name}' is used from {command[0]}, not from this Python environment - "
                f"an outdated copy there breaks downloads. Recommended: "
                f"pip install -U {name} in the environment music-loader runs in.[/yellow]"
            )

    if not module_available("bs4"):
        console.print(
            "[yellow]'beautifulsoup4' is not installed - Genius lyrics are disabled. "
            "Install with: pip install beautifulsoup4[/yellow]"
        )

    # SoundCloud answers profile/likes/reposts listings with HTTP 403 unless
    # yt-dlp can impersonate a browser, which needs curl_cffi.
    if module_available("yt_dlp") and not module_available("curl_cffi"):
        console.print(
            "[yellow]'curl_cffi' is not installed - SoundCloud profile, likes and reposts "
            "links may fail with HTTP 403. Install with: pip install \"yt-dlp[curl-cffi]\"[/yellow]"
        )

    # Deno/Node is optional: yt-dlp (used internally by spotdl for YouTube
    # sources) can use it to solve YouTube's signature challenges.
    if shutil.which("deno") is None and shutil.which("node") is None:
        console.print(
            "[yellow]Neither 'deno' nor 'node' was found - yt-dlp will fall back to its "
            "built-in JS interpreter for YouTube sources, which is slower and less "
            "reliable. Recommended: install Deno (https://deno.com)[/yellow]"
        )

    return True
