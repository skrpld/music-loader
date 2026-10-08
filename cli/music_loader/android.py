"""Entry points for the Android app, which embeds this package (Chaquopy).

The app runs the same server as `music-loader serve`, bound to 127.0.0.1 and
with a fresh token, and talks to it over HTTP like it talks to a remote one.
Jobs run inside the app process (see inprocess.py); the downloads land in the
phone's music folder.

Native tools come with the app as "native libraries", the only files Android
lets an app execute:

    libffmpeg.so, libffprobe.so   ffmpeg and ffprobe executables
    libffmpeg.zip.so              their shared libraries (zip)
    libffmpegdeps.zip.so          libraries those need on top (zip)
    libqjs.so                     QuickJS, the JavaScript runtime yt-dlp needs
                                  for YouTube (spotDL's audio source)

The zips are unpacked once per app version into the app's own storage.

Called from Kotlin (dev.skrpld.musicloader.engine.LocalEngine):

    start(native_dir, files_dir, cache_dir, music_dir) -> JSON {"url", "token"}
    set_music_dir(path)
    stop()
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import Optional

_TOOLS_DIRNAME = "tools"
_STAMP_FILENAME = ".stamp"

_lock = threading.Lock()
_server = None
_manager = None
_endpoint: Optional[dict[str, str]] = None


def _prepare_environment(native_dir: Path, files_dir: Path, cache_dir: Path) -> None:
    os.environ["MUSIC_LOADER_IN_PROCESS"] = "1"
    # spotdl keeps its config in ~/.spotdl; yt-dlp its cache in ~/.cache.
    os.environ["HOME"] = str(files_dir)
    os.environ["XDG_CACHE_HOME"] = str(cache_dir)
    os.environ["TMPDIR"] = str(cache_dir)
    tempfile.tempdir = None
    # rich (spotdl's output) wraps lines at the terminal width; progress
    # parsing relies on one event per line.
    os.environ["COLUMNS"] = "4000"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    try:
        import certifi

        os.environ["SSL_CERT_FILE"] = certifi.where()
    except ImportError:
        pass
    from .process import JS_RUNTIME_ENV

    qjs = native_dir / "libqjs.so"
    if qjs.exists():
        os.environ[JS_RUNTIME_ENV] = f"quickjs:{qjs}"


def _unpack(archive: Path, target: Path) -> None:
    """Unpacks a zip with its symbolic links (the libraries' version names)."""
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            name = Path(info.filename)
            if name.is_absolute() or ".." in name.parts:
                continue
            path = target / name
            if info.is_dir():
                path.mkdir(parents=True, exist_ok=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink() or path.exists():
                path.unlink()
            if stat.S_ISLNK(info.external_attr >> 16):
                os.symlink(bundle.read(info).decode("utf-8"), path)
            else:
                with bundle.open(info) as source, open(path, "wb") as sink:
                    shutil.copyfileobj(source, sink)


def _install_tools(native_dir: Path, files_dir: Path) -> Path:
    """Unpacks ffmpeg's libraries (once per app version) and links the
    executables as `ffmpeg`/`ffprobe` into a folder for PATH."""
    root = files_dir / _TOOLS_DIRNAME
    archives = [native_dir / "libffmpeg.zip.so", native_dir / "libffmpegdeps.zip.so"]
    missing = [path.name for path in archives if not path.exists()]
    if missing:
        raise RuntimeError(f"ffmpeg is missing from the app: {', '.join(missing)}")
    stamp = json.dumps([[str(path), path.stat().st_size] for path in archives])
    stamp_file = root / _STAMP_FILENAME
    try:
        current = stamp_file.read_text(encoding="utf-8")
    except OSError:
        current = None
    lib_dir = root / "ffmpeg" / "usr" / "lib"
    if current != stamp:
        shutil.rmtree(root, ignore_errors=True)
        _unpack(archives[0], root / "ffmpeg")
        _unpack(archives[1], lib_dir)
        stamp_file.write_text(stamp, encoding="utf-8")

    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in ("ffmpeg", "ffprobe"):
        link = bin_dir / name
        executable = native_dir / f"lib{name}.so"
        if link.is_symlink() and os.readlink(link) == str(executable):
            continue
        if link.is_symlink() or link.exists():
            link.unlink()
        os.symlink(executable, link)

    os.environ["PATH"] = os.pathsep.join([str(bin_dir), os.environ.get("PATH") or "/system/bin"])
    os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(
        filter(None, [str(lib_dir), os.environ.get("LD_LIBRARY_PATH")]))
    return bin_dir


def _check_ffmpeg() -> None:
    try:
        result = subprocess.run(["ffmpeg", "-hide_banner", "-version"], capture_output=True,
                                text=True, errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"ffmpeg does not start: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise RuntimeError(f"ffmpeg does not start: {detail[-1] if detail else result.returncode}")


def start(native_dir: str, files_dir: str, cache_dir: str, music_dir: str) -> str:
    """Starts the local server (once) and returns its address and token as JSON."""
    global _server, _manager, _endpoint
    with _lock:
        if _endpoint is not None:
            set_music_dir(music_dir)
            return json.dumps(_endpoint)

        native, files, cache = Path(native_dir), Path(files_dir), Path(cache_dir)
        _prepare_environment(native, files, cache)
        _install_tools(native, files)
        _check_ffmpeg()

        from rich.console import Console

        from . import inprocess
        from .server import JobManager, make_server

        inprocess.install()
        inprocess.check_spotdl()
        # The server's own messages end up in the app log (logcat).
        console = Console(file=sys.stderr, force_terminal=False, width=200)
        manager = JobManager(Path(music_dir), {}, console, in_process=True)
        token = secrets.token_urlsafe(32)
        server = make_server("127.0.0.1", 0, manager, token)
        manager.start()
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.5},
                         name="http-server", daemon=True).start()
        _server, _manager = server, manager
        _endpoint = {"url": f"http://127.0.0.1:{server.server_address[1]}", "token": token}
        return json.dumps(_endpoint)


def set_music_dir(path: str) -> None:
    """Folder for the next job; a running job keeps its folder."""
    manager = _manager
    if manager is None:
        return
    with manager._cond:
        manager.music_dir = Path(path)
        manager._changed()


def stop() -> None:
    global _server, _manager, _endpoint
    with _lock:
        server, manager = _server, _manager
        _server = _manager = _endpoint = None
    if manager is not None:
        manager.shutdown(timeout=30)
    if server is not None:
        server.shutdown()
        server.server_close()
