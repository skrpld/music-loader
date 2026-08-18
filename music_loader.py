#!/usr/bin/env python3
"""
Music Loader — downloads music with metadata from Spotify (via spotDL)
and SoundCloud (via yt-dlp), automatically searches for synchronized
lyrics (.lrc), and prepares the library for Symfonium.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import syncedlyrics
except ImportError:
    syncedlyrics = None

BASE_DIR = Path("./Music").resolve()
SOUNDCLOUD_DIR = BASE_DIR / "SoundCloud"
AUDIO_EXTENSIONS = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".webm"}

# Counters for the final summary
STATS = {"spotify_ok": 0, "spotify_fail": 0, "soundcloud_ok": 0, "soundcloud_fail": 0}


def setup_directories():
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    SOUNDCLOUD_DIR.mkdir(parents=True, exist_ok=True)


def check_dependencies() -> bool:
    """Checks for all required external utilities BEFORE starting downloads,
    instead of failing midway with an unclear error."""
    missing = []

    if shutil.which("ffmpeg") is None:
        missing.append("ffmpeg (required for MP3 conversion and embedding covers)")
    if shutil.which("spotdl") is None:
        missing.append("spotdl (pip install spotdl)")
    if shutil.which("yt-dlp") is None:
        missing.append("yt-dlp (pip install yt-dlp)")

    if missing:
        print("[!] Required components are missing:")
        for m in missing:
            print(f"    - {m}")
        print("[!] Install the missing components and run the script again.")
        return False

    if syncedlyrics is None:
        print("[i] 'syncedlyrics' is not installed — lyrics for SoundCloud tracks "
              "will not be downloaded. Install with: pip install syncedlyrics")

    # Deno (or Node.js) is not strictly required, but yt-dlp (used both
    # directly for SoundCloud and internally by spotdl for YouTube sources)
    # can use it as an external JS runtime to solve YouTube's signature/
    # nsig challenges. Without it, downloads still work but are more prone
    # to throttling or extraction failures.
    if shutil.which("deno") is None and shutil.which("node") is None:
        print("[i] Neither 'deno' nor 'node' was found — yt-dlp will fall back to its "
              "built-in JS interpreter, which is slower and less reliable for YouTube "
              "sources. Recommended: install Deno (https://deno.com) for more stable "
              "downloads.")

    return True


def clean_track_title(title: str) -> str:
    """Removes noise from a track title before searching for lyrics
    (YouTube/SoundCloud titles often contain 'Official Video', 'HQ', etc.)."""
    junk_words = (
        r"official|audio|video|lyric|lyrics|prod\.?|remix|out now|"
        r"free download|premiere|hq|hd|visualizer|explicit|clean|"
        r"monstercat|nocopyrightsounds|ncs release"
    )
    patterns = [
        rf"[\(\[\{{][^\(\)\[\]\{{\}}]*(?:{junk_words})[^\(\)\[\]\{{\}}]*[\)\]\}}]",
        r"[\(\[\{]\s*[\)\]\}]",  # empty brackets left after cleaning
        r"\s[-|]\s*$",           # trailing dash/pipe
    ]
    cleaned = title
    for pattern in patterns:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\.(mp3|flac|m4a|wav|opus|webm)$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" -|")


def update_sc_playlist():
    playlist_path = SOUNDCLOUD_DIR / "SoundCloud_New.m3u8"
    audio_files = [
        f.name
        for f in SOUNDCLOUD_DIR.iterdir()
        if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS
    ]

    with open(playlist_path, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for audio in sorted(audio_files):
            f.write(f"{audio}\n")

    print(f"\n[+] Playlist updated: {playlist_path.name} ({len(audio_files)} tracks)")


def fetch_lyrics_sc(audio_path: Path):
    if syncedlyrics is None:
        return

    search_query = clean_track_title(audio_path.stem)
    if not search_query:
        print(f"    [Lyrics] Skipping: could not extract a title from '{audio_path.stem}'")
        return

    print(f"    [Lyrics] Searching: '{search_query}'")
    try:
        lrc_content = syncedlyrics.search(
            search_query, providers=["Musixmatch", "NetEase", "Lrclib", "Genius"]
        )
        if lrc_content:
            lrc_path = audio_path.with_suffix(".lrc")
            with open(lrc_path, "w", encoding="utf-8") as f:
                f.write(lrc_content)
            print(f"    [+] Lyrics saved: {lrc_path.name}")
        else:
            print(f"    [-] Lyrics not found")
    except Exception as e:
        print(f"    [!] Lyrics search error: {e}")


def download_spotify(url: str) -> bool:
    print("\n" + "=" * 50)
    print(f"[Spotify] Downloading: {url}")
    print("=" * 50)

    output_template = f"{BASE_DIR}/{{artist}} - {{album}}/{{track-number}} - {{title}}.{{ext}}"
    cmd = [
        "spotdl",
        "download",
        url,
        "--output", output_template,
        "--format", "mp3",
        "--bitrate", "320k",
        "--lyrics", "genius",
        "--generate-lrc",
        "--overwrite", "skip",
    ]

    try:
        result = subprocess.run(cmd, timeout=3600)
        if result.returncode != 0:
            print(f"[!] spotDL exited with code {result.returncode}")
            return False
        return True
    except subprocess.TimeoutExpired:
        print("[!] spotDL: timeout exceeded (1 hour), skipping link")
        return False
    except subprocess.CalledProcessError as e:
        print(f"[!] spotDL error: {e}")
        return False


def download_soundcloud(url: str) -> bool:
    print("\n" + "=" * 50)
    print(f"[SoundCloud] Downloading: {url}")
    print("=" * 50)

    archive_file = SOUNDCLOUD_DIR / ".sc_archive.txt"
    output_template = str(SOUNDCLOUD_DIR / "%(title)s.%(ext)s")

    cmd = [
        "yt-dlp",
        "-x",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        "--embed-metadata",
        "--embed-thumbnail",
        # SoundCloud tracks often lack album/artist metadata, so use
        # reasonable defaults to avoid displaying "Unknown".
        "--parse-metadata", "%(uploader,artist,creator|Unknown Artist)s:%(meta_artist)s",
        "--parse-metadata", ":(?P<meta_album>SoundCloud)",
        "--postprocessor-args", "ffmpeg:-id3v2_version 3",
        "--download-archive", str(archive_file),
        "--no-overwrites",
        "-o", output_template,
        url,
    ]

    try:
        result = subprocess.run(cmd, timeout=3600)
        if result.returncode != 0:
            print(f"[!] yt-dlp exited with code {result.returncode}")
            return False
    except subprocess.TimeoutExpired:
        print("[!] yt-dlp: timeout exceeded (1 hour), skipping link")
        return False
    except subprocess.CalledProcessError as e:
        print(f"[!] yt-dlp error: {e}")
        return False

    for audio_file in SOUNDCLOUD_DIR.iterdir():
        if audio_file.is_file() and audio_file.suffix.lower() in AUDIO_EXTENSIONS:
            lrc_file = audio_file.with_suffix(".lrc")
            if not lrc_file.exists():
                fetch_lyrics_sc(audio_file)

    update_sc_playlist()
    return True


def process_links(links: list[str]):
    setup_directories()

    for link in links:
        link = link.strip()
        if not link or link.startswith("#"):
            continue

        if "spotify.com" in link:
            ok = download_spotify(link)
            STATS["spotify_ok" if ok else "spotify_fail"] += 1
        elif "soundcloud.com" in link:
            ok = download_soundcloud(link)
            STATS["soundcloud_ok" if ok else "soundcloud_fail"] += 1
        else:
            print(f"[!] Unsupported link (not Spotify/SoundCloud): {link}")

    print_summary()


def print_summary():
    total_ok = STATS["spotify_ok"] + STATS["soundcloud_ok"]
    total_fail = STATS["spotify_fail"] + STATS["soundcloud_fail"]
    if total_ok + total_fail == 0:
        return
    print("\n" + "=" * 50)
    print("SUMMARY")
    print(f"  Spotify:    successful {STATS['spotify_ok']}, failed {STATS['spotify_fail']}")
    print(f"  SoundCloud: successful {STATS['soundcloud_ok']}, failed {STATS['soundcloud_fail']}")
    print("=" * 50)


def interactive_mode():
    print("=== Music Loader (Spotify & SoundCloud) ===")
    while True:
        print("\n1. Enter a link")
        print("2. Exit")
        choice = input("Choice (1-2): ").strip()

        if choice == "1":
            url = input("Spotify or SoundCloud link: ").strip()
            if url:
                process_links([url])
        elif choice == "2":
            print("Exit.")
            break
        else:
            print("[!] Invalid choice, enter 1 or 2.")


def main():
    if not check_dependencies():
        sys.exit(1)

    if len(sys.argv) > 1:
        process_links(sys.argv[1:])
    elif Path("links.txt").exists():
        print("[i] Reading links from links.txt...")
        with open("links.txt", "r", encoding="utf-8-sig") as f:
            process_links(f.readlines())
    else:
        interactive_mode()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Interrupted by user.")
        sys.exit(0)
