import os
import re
import sys
import subprocess
from pathlib import Path

try:
    import syncedlyrics
except ImportError:
    syncedlyrics = None

BASE_DIR = Path("./Music").resolve()
SOUNDCLOUD_DIR = BASE_DIR / "SoundCloud"
AUDIO_EXTENSIONS = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".webm"}


def setup_directories():
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    SOUNDCLOUD_DIR.mkdir(parents=True, exist_ok=True)


def clean_track_title(title: str) -> str:
    patterns = [
        r"[\(\[\{].*?(official|audio|video|lyric|prod|remix|out now|free download|premiere|hq|hd|visualizer|feat\.?|ft\.?).*?[\)\]\}]",
        r"[\(\[\{]\s*[\)\]\}]",
    ]
    cleaned = title
    for pattern in patterns:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(r"\.(mp3|flac|m4a|wav|opus|webm)$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def has_ffmpeg() -> bool:
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


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
        print("  [!] 'syncedlyrics' not installed. Skipping lyrics.")
        return

    search_query = clean_track_title(audio_path.stem)
    print(f"  [Lyrics] Searching for: '{search_query}'")

    try:
        lrc_content = syncedlyrics.search(
            search_query, providers=["Musixmatch", "NetEase", "Lrclib"]
        )

        if lrc_content:
            lrc_path = audio_path.with_suffix(".lrc")
            with open(lrc_path, "w", encoding="utf-8") as f:
                f.write(lrc_content)
            print(f"  [+] Lyrics saved: {lrc_path.name}")
        else:
            print(f"  [-] No lyrics found")
    except Exception as e:
        print(f"  [!] Lyrics error: {e}")


def download_spotify(url: str):
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
    ]

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"[!] spotDL error: {e}")
    except FileNotFoundError:
        print("[!] spotdl not found. Run: pip install spotdl")


def download_soundcloud(url: str):
    print("\n" + "=" * 50)
    print(f"[SoundCloud] Downloading: {url}")
    print("=" * 50)

    if not has_ffmpeg():
        print("[!] ffmpeg not found. Install it to ensure MP3 conversion.")

    archive_file = SOUNDCLOUD_DIR / ".sc_archive.txt"
    output_template = str(SOUNDCLOUD_DIR / "%(title)s.%(ext)s")

    cmd = [
        "yt-dlp",
        "-x",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        "--add-metadata",
        "--embed-thumbnail",
        "--download-archive", str(archive_file),
        "-o", output_template,
        url,
    ]

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"[!] yt-dlp error: {e}")
    except FileNotFoundError:
        print("[!] yt-dlp not found. Run: pip install yt-dlp")
        return

    for audio_file in SOUNDCLOUD_DIR.iterdir():
        if audio_file.is_file() and audio_file.suffix.lower() in AUDIO_EXTENSIONS:
            lrc_file = audio_file.with_suffix(".lrc")
            if not lrc_file.exists():
                fetch_lyrics_sc(audio_file)

    update_sc_playlist()


def process_links(links: list[str]):
    setup_directories()
    for link in links:
        link = link.strip()
        if not link or link.startswith("#"):
            continue

        if "spotify.com" in link:
            download_spotify(link)
        elif "soundcloud.com" in link:
            download_soundcloud(link)
        else:
            print(f"[!] Unsupported link: {link}")


def main():
    if len(sys.argv) > 1:
        process_links(sys.argv[1:])
    elif Path("links.txt").exists():
        print("[i] Reading links from links.txt...")
        with open("links.txt", "r", encoding="utf-8") as f:
            process_links(f.readlines())
    else:
        print("=== Music Loader (Spotify & SoundCloud) ===")
        print("1. Enter link")
        print("2. Exit")
        choice = input("Choose (1-2): ").strip()
        if choice == "1":
            url = input("Enter Spotify or SoundCloud URL: ").strip()
            if url:
                process_links([url])
        else:
            print("Exiting.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Interrupted by user.")
        sys.exit(0)
