"""The cache of the web player's query hashes (spotify_cache), run on spotapi's real
BaseClient with a stand-in HTTP client that counts the requests."""
import base64
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from music_loader import spotify, spotify_cache

spotapi_client = pytest.importorskip("spotapi.client")

HASH = "ab" * 32
ALBUM_HASH = "cd" * 32
PACK = "https://open.spotifycdn.com/cdn/build/web-player/web-player.1234abcd.js"
CHUNKS = [f"chunk{i}.h{i}.js" for i in range(1, 6)]


def _page(pack: str) -> str:
    config = base64.b64encode(json.dumps({
        "recaptchaWebPlayerFraudSiteKey": "", "clientVersion": "1.2.3",
    }).encode()).decode()
    return (f'<html><script src="{pack}"></script>'
            f'<script id="appServerConfig" type="text/plain">{config}</script></html>')


def _pack_source() -> str:
    # extract_mappings takes the 4th and 5th "{1:"a",2:"b"}" literal: chunk names, chunk hashes.
    names = ",".join(f'{i}:"chunk{i}"' for i in range(1, 6))
    hashes = ",".join(f'{i}:"h{i}"' for i in range(1, 6))
    filler = '{0:"x",9:"y"}'
    return " ".join([filler] * 3 + ["{" + names + "}", "{" + hashes + "}"])


class FakeHttp:
    """What spotapi's TLSClient offers BaseClient, answering like the web player."""

    def __init__(self, pack: str = PACK):
        self.pack = pack
        self.requests: list[str] = []
        self.impersonate = "chrome120"
        self.headers: dict = {}
        self.cookies = {"sp_t": "device"}
        self.authenticate = None
        self.on_auth_failure = None

    def close(self):
        pass

    def post(self, url, **kwargs):
        self.requests.append(url)
        return SimpleNamespace(fail=False, response={
            "response_type": "RESPONSE_GRANTED_TOKEN_RESPONSE", "granted_token": {"token": "t"}})

    def get(self, url, **kwargs):
        self.requests.append(url)
        if url == "https://open.spotify.com":
            body = _page(self.pack)
        elif url.endswith("/api/token"):
            body = {"accessToken": "a", "clientId": "c", "accessTokenExpirationTimestampMs": 0}
        elif url == self.pack:
            body = _pack_source()
        else:  # a chunk of the player: this is where the hashes are
            body = f'... "getTrack","query","{HASH}" ... "getAlbum","query","{ALBUM_HASH}" ...'
        return SimpleNamespace(fail=False, response=body)

    def chunk_requests(self) -> int:
        return sum(1 for url in self.requests if url.endswith(".js") and url != self.pack)


@pytest.fixture
def cache(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(spotapi_client, "generate_totp", lambda: ("123456", 1))
    original = spotapi_client.BaseClient.part_hash
    was_installed = hasattr(spotapi_client.BaseClient, spotify_cache._MARK)  # another test may have
    spotify_cache.forget_memory()
    assert spotify_cache.install()
    yield tmp_path
    spotapi_client.BaseClient.part_hash = original
    if not was_installed and hasattr(spotapi_client.BaseClient, spotify_cache._MARK):
        delattr(spotapi_client.BaseClient, spotify_cache._MARK)
    spotify_cache.forget_memory()


def _hash(http: FakeHttp, name: str = "getTrack") -> str:
    return spotapi_client.BaseClient(http).part_hash(name)


def test_first_client_downloads_the_player_the_next_does_not(cache):
    first, second = FakeHttp(), FakeHttp()

    assert _hash(first) == HASH
    assert first.chunk_requests() == len(CHUNKS)

    # Another operation of the same build: found in the same download.
    assert _hash(second, "getAlbum") == ALBUM_HASH
    assert second.chunk_requests() == 0
    # Only the start page and the token: what a client needs anyway.
    assert second.requests == ["https://open.spotify.com", "https://open.spotify.com/api/token"]


def test_hashes_survive_a_restart(cache):
    _hash(FakeHttp())
    assert (cache / "music-loader" / spotify_cache.CACHE_FILENAME).is_file()

    spotify_cache.forget_memory()  # a new process
    after_restart = FakeHttp()
    assert _hash(after_restart) == HASH
    assert after_restart.chunk_requests() == 0


def test_a_new_build_of_the_player_is_downloaded_again(cache):
    _hash(FakeHttp())
    rebuilt = FakeHttp(pack="https://open.spotifycdn.com/cdn/build/web-player/web-player.9999ffff.js")
    assert _hash(rebuilt) == HASH
    assert rebuilt.chunk_requests() == len(CHUNKS)


def test_only_the_newest_builds_are_kept(cache):
    for number in range(spotify_cache._MAX_BUILDS + 2):
        spotify_cache.remember(f"build{number}", {"getTrack": HASH})
    spotify_cache.forget_memory()
    kept = json.loads((cache / "music-loader" / spotify_cache.CACHE_FILENAME).read_text())
    assert list(kept) == [f"build{number}" for number in range(2, spotify_cache._MAX_BUILDS + 2)]


def test_something_that_is_no_hash_is_not_remembered(cache):
    spotify_cache.remember("build", {"getTrack": "not a hash"})
    spotify_cache.remember("build", {"getTrack": ""})
    assert spotify_cache.lookup("build", "getTrack") is None


def test_entries_of_another_process_are_merged_not_overwritten(cache):
    spotify_cache.remember("build-x", {"getTrack": HASH})  # this process has loaded the file
    path = cache / "music-loader" / spotify_cache.CACHE_FILENAME
    other = json.loads(path.read_text())
    other["build-y"] = {"getAlbum": ALBUM_HASH}  # written by another process meanwhile
    path.write_text(json.dumps(other))

    spotify_cache.remember("build-x", {"getAlbum": ALBUM_HASH})

    saved = json.loads(path.read_text())
    assert saved["build-y"] == {"getAlbum": ALBUM_HASH}
    assert saved["build-x"] == {"getTrack": HASH, "getAlbum": ALBUM_HASH}


def test_a_start_page_without_the_player_script_is_left_to_spotapi(cache):
    http = FakeHttp(pack="")
    http.get = lambda url, **kw: (http.requests.append(url) or SimpleNamespace(
        fail=False,
        response=_page("https://example.com/other.js") if url == "https://open.spotify.com"
        else {"accessToken": "a", "clientId": "c", "accessTokenExpirationTimestampMs": 0}))
    with pytest.raises(Exception):  # spotapi's own failure, reported as before
        _hash(http)
    assert http.requests.count("https://open.spotify.com") == 1  # not repeated by the cache


def test_a_damaged_cache_file_is_ignored(cache):
    path = cache / "music-loader" / spotify_cache.CACHE_FILENAME
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    spotify_cache.forget_memory()
    http = FakeHttp()
    assert _hash(http) == HASH
    assert http.chunk_requests() == len(CHUNKS)


def test_a_cache_that_cannot_be_written_only_costs_time(cache, monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError("read-only")

    monkeypatch.setattr(spotify_cache.tempfile, "mkstemp", refuse)
    assert _hash(FakeHttp()) == HASH


def test_install_is_idempotent_and_skips_unknown_spotapi(cache, monkeypatch):
    assert spotify_cache.install()
    patched = spotapi_client.BaseClient.part_hash
    assert spotify_cache.install()
    assert spotapi_client.BaseClient.part_hash is patched

    with monkeypatch.context() as unknown:  # a spotapi that looks different
        unknown.delattr(spotapi_client.BaseClient, spotify_cache._MARK)
        unknown.delattr(spotapi_client.BaseClient, "part_hash")
        assert spotify_cache.install() is False


def test_desktop_spotdl_starts_through_the_bootstrap_with_the_cache(tmp_path):
    command = spotify._command([sys.executable, "-m", "spotdl"])
    assert command[:2] == [sys.executable, "-c"]
    assert "spotify_cache" in command[2]
    # A spotdl of another environment cannot import this package: left as it is.
    assert spotify._command(["/usr/bin/spotdl"]) == ["/usr/bin/spotdl"]
    result = subprocess.run(command + ["--version"], capture_output=True, text=True, timeout=120,
                            cwd=tmp_path, env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
                                               "PYTHONPATH": str(Path(spotify_cache.__file__).resolve().parents[1])})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().split(".")[0].isdigit()
    assert "hash cache not active" not in result.stderr  # the package was importable in the child
