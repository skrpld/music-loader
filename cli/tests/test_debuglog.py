"""Optional debug log of a run (debuglog.py): what it records, what it masks,
and that nothing is written while it is off."""
import io
import json
import threading
from pathlib import Path

import pytest
from rich.console import Console

from music_loader import debuglog, server, worker
from music_loader import soundcloud as sc
from music_loader.artists import ArtistRegistry
from music_loader.cli import _reclaim_debug_log_link, parse_args
from music_loader.config import LOGS_DIRNAME, LYRICS_MODE_LOOSE, LYRICS_MODE_STRICT, AppConfig
from music_loader.events import EventDashboard
from music_loader.lyrics import Candidate, LyricsRequest, LyricsService, _Provider, evaluate
from music_loader.soundcloud_index import SoundCloudArchive, get_index

SOUNDCLOUD = "https://soundcloud.com/label/song"


@pytest.fixture(autouse=True)
def no_active_log():
    debuglog.deactivate()
    yield
    debuglog.deactivate()


def entries(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def find(items: list[dict], section: str, event: str) -> list[dict]:
    return [item for item in items if item["section"] == section and item["event"] == event]


# -- a SoundCloud track run --------------------------------------------------------------------
class FakeLyrics(_Provider):
    name = "FakeLyrics"

    def candidates(self, request, mode):
        yield Candidate("FakeLyrics", artist="Somebody Else", title="Song", plain="wrong words")
        yield Candidate("FakeLyrics", artist="Main Artist", title="Song (Sped Up)", plain="sped up words")
        yield Candidate("FakeLyrics", artist="Main Artist", title="Song", plain="the words")


def run_soundcloud_track(tmp_path: Path) -> sc._Context:
    config = AppConfig.from_output_dir(tmp_path / "Music")
    config.ensure_dirs()
    staging = config.soundcloud_dir / ".sc_downloads"
    staging.mkdir()
    ctx = sc._Context(
        config=config, soundcloud_dir=config.soundcloud_dir, staging_dir=staging,
        dashboard=EventDashboard(io.StringIO()), lyrics=LyricsService(LYRICS_MODE_STRICT, [FakeLyrics()]),
        abort=threading.Event(), yt=["yt-dlp"], api=None, gate=sc._RateGate(),
        index=get_index(config.soundcloud_dir),
        archive=SoundCloudArchive(config.soundcloud_dir / ".sc_archive.txt"),
        registry=ArtistRegistry(tmp_path / "artists.json"),
    )
    info = {
        "id": "123456", "title": "Main Artist - Song (feat. Guest) [FREE DL]", "uploader": "Label",
        "duration": 200.0, "description": "Out now on Label",
        "webpage_url": "https://soundcloud.com/label/song/s-SeCrEt42",
    }

    def fake_download(job, c, slot, source_url=None):
        raw = c.staging_dir / f"{job.track_id}.mp3"
        raw.write_bytes(b"")
        return sc._Download(True, raw, dict(job.info))

    def fake_postprocess(job, raw, info, c):
        # The real one converts with ffmpeg; the naming and tagging are what matters here.
        meta = sc.build_meta(info, None, c.registry)
        output = sc._target_path(meta, c)
        output.parent.mkdir(parents=True, exist_ok=True)
        raw.replace(output)
        return sc._Processed(True, output, False, meta)

    pipeline = pytest.MonkeyPatch()
    pipeline.setattr(sc, "_download_one", fake_download)
    pipeline.setattr(sc, "_postprocess", fake_postprocess)
    try:
        sc._run_pipeline([sc.TrackJob(info=info, lookup_album=False)], ctx)
    finally:
        pipeline.undo()
    return ctx


def test_soundcloud_track_run_logs_title_parsing_naming_and_lyrics_verdicts(tmp_path):
    log = debuglog.DebugLog.in_directory(tmp_path / "logs")
    debuglog.activate(log)
    ctx = run_soundcloud_track(tmp_path)
    debuglog.deactivate()
    items = entries(log.path)

    (metadata,) = find(items, "soundcloud", "metadata")
    assert metadata["raw"]["title"] == "Main Artist - Song (feat. Guest) [FREE DL]"
    assert metadata["raw"]["description"] == "Out now on Label"

    (parsed,) = find(items, "soundcloud", "title_parsed")
    assert parsed["raw_title"] == "Main Artist - Song (feat. Guest) [FREE DL]"
    assert parsed["artists"] == ["Main Artist"] and parsed["featured"] == ["Guest"]

    (final,) = find(items, "soundcloud", "final_meta")
    assert final["tags"]["artists"] == ["Main Artist", "Guest"]
    filename = final["filename"]
    assert filename.endswith(".mp3")
    assert final["folder"] == "Main Artist/Song - Single"
    assert final["path"] == f"Main Artist/Song - Single/{filename}"
    (result,) = find(items, "soundcloud", "track_result")
    assert result["status"] == "downloaded" and result["path"].endswith(filename)
    assert ctx.results["123456"].name == filename

    candidates = find(items, "lyrics", "candidate")
    verdicts = [(c["artist"], c["title"], c["verdict"], c["reason"]) for c in candidates]
    assert verdicts[0] == ("Somebody Else", "Song", "rejected", "artist mismatch")
    assert verdicts[1][2] == "rejected" and verdicts[1][3].startswith("version mismatch")
    assert verdicts[2][:3] == ("Main Artist", "Song", "plain")
    (request,) = find(items, "lyrics", "request")
    assert request["artists"] == ["Main Artist"] and request["title"].startswith("Song")
    (outcome,) = find(items, "lyrics", "result")
    assert outcome["status"] == "plain" and outcome["provider"] == "FakeLyrics"

    text = log.path.read_text(encoding="utf-8")
    assert "SeCrEt42" not in text


def test_nothing_is_written_when_the_debug_log_is_off(tmp_path):
    run_soundcloud_track(tmp_path)
    assert not debuglog.enabled()
    logs = tmp_path / "Music" / LOGS_DIRNAME
    assert not logs.exists() or not list(logs.glob("debug-*"))
    assert not list(tmp_path.rglob("debug-*"))


# -- masking -----------------------------------------------------------------------------------
def test_secrets_and_credentials_are_masked(tmp_path):
    log = debuglog.DebugLog(tmp_path / "debug.jsonl")
    debuglog.activate(log)
    debuglog.event(
        "lyrics", "query",
        url="https://api-v2.soundcloud.com/tracks?client_id=abcDEF123&ids=1",
        link="https://soundcloud.com/a/b/s-PrivTok",
        params={"q_track": "Song", "usertoken": "user-token-value", "t": "1"},
        text="Authorization: Bearer abcdefghijklmnop secret_token=s-Other",
        nested={"password": "hunter2", "list": ["access_token=tok999"]},
    )
    text = log.path.read_text(encoding="utf-8")
    for secret in ("abcDEF123", "PrivTok", "user-token-value", "abcdefghijklmnop", "s-Other",
                   "hunter2", "tok999"):
        assert secret not in text
    (item,) = entries(log.path)
    assert item["params"]["q_track"] == "Song" and item["params"]["usertoken"] == "***"
    assert item["url"].endswith("client_id=***&ids=1")


def test_long_texts_are_cut(tmp_path):
    log = debuglog.DebugLog(tmp_path / "debug.jsonl")
    log.write("run", "log", {"text": "x" * 10000})
    (item,) = entries(log.path)
    assert len(item["text"]) < 2100 and item["text"].endswith("[10000 chars]")


def test_events_without_an_active_log_do_nothing(tmp_path):
    debuglog.event("run", "log", text="nowhere")
    assert debuglog.active() is None


def test_a_folder_path_gets_a_file_name(tmp_path):
    log = debuglog.DebugLog(tmp_path)
    assert log.path.parent == tmp_path and log.path.name.startswith("debug-")


# -- lyrics verdicts ---------------------------------------------------------------------------
def request(title="Song", duration=200.0):
    return LyricsRequest(key="k", audio_path=Path("a.mp3"), main_artists=["Artist"], title=title,
                         duration=duration)


@pytest.mark.parametrize("candidate, mode, label, reason", [
    (Candidate("P", "Other", "Song", 200.0, plain="x"), LYRICS_MODE_STRICT, "rejected", "artist mismatch"),
    (Candidate("P", "Artist", "Other Song", 200.0, plain="x"), LYRICS_MODE_STRICT, "rejected", "title mismatch"),
    (Candidate("P", "Artist", "Song", 230.0, plain="x"), LYRICS_MODE_STRICT, "rejected", "length differs by 30.0s"),
    (Candidate("P", "Artist", "Song", 200.0), LYRICS_MODE_STRICT, "rejected", "no lyrics text"),
    (Candidate("P", "Artist", "Song", 200.0, plain="x"), LYRICS_MODE_STRICT, "plain", "no synced lyrics"),
    (Candidate("P", "Artist", "Song", 230.0, plain="x"), LYRICS_MODE_LOOSE, "rejected",
     "length differs by 30.0s (more than 10s)"),
])
def test_every_verdict_says_why(candidate, mode, label, reason):
    verdict = evaluate(request(), candidate, mode)
    assert (verdict.label, verdict.reason) == (label, reason)


def test_a_full_match_has_no_reason():
    synced = "[00:01.00]a\n[00:02.00]b\n[00:03.00]c"
    verdict = evaluate(request(), Candidate("P", "Artist", "Song", 201.0, synced=synced), LYRICS_MODE_STRICT)
    assert verdict.synced and verdict.label == "synced" and verdict.reason == ""


def test_version_mismatch_names_the_versions():
    verdict = evaluate(request("Song (Sped Up)"), Candidate("P", "Artist", "Song", 200.0, plain="x"),
                       LYRICS_MODE_STRICT)
    assert verdict.label == "rejected" and verdict.reason.startswith("version mismatch (")
    assert "original" in verdict.reason


# -- command line ------------------------------------------------------------------------------
def test_debug_log_flag_is_off_by_default_and_takes_an_optional_path():
    assert parse_args([SOUNDCLOUD]).debug_log is None
    assert parse_args([SOUNDCLOUD, "--debug-log"]).debug_log == ""
    assert parse_args(["--debug-log=run.jsonl", SOUNDCLOUD]).debug_log == "run.jsonl"


def test_a_link_right_after_the_flag_is_still_downloaded():
    args = parse_args(["--debug-log", SOUNDCLOUD])
    _reclaim_debug_log_link(args)
    assert args.debug_log == "" and args.source == [SOUNDCLOUD]


# -- server ------------------------------------------------------------------------------------
def test_debug_log_job_option_is_off_by_default_and_must_be_a_boolean():
    _, options, _ = server._parse_job_request({"links": [SOUNDCLOUD]})
    assert options["debug_log"] is False
    _, options, _ = server._parse_job_request({"links": [SOUNDCLOUD], "options": {"debug_log": True}})
    assert options["debug_log"] is True
    with pytest.raises(server._RequestError):
        server._parse_job_request({"links": [SOUNDCLOUD], "options": {"debug_log": "yes"}})


def run_worker(tmp_path, monkeypatch, **spec):
    from music_loader import cli

    seen = {}
    monkeypatch.setattr(cli, "process_links", lambda links, config, events: seen.update(
        active=debuglog.active(), links=links))
    stream = io.StringIO()
    code = worker.run({"links": [SOUNDCLOUD], "output": str(tmp_path / "Music"), "lyrics": "off", **spec},
                      EventDashboard(stream))
    sent = [json.loads(line) for line in stream.getvalue().splitlines()]
    return code, seen, sent


def test_worker_writes_the_debug_log_and_announces_it(tmp_path, monkeypatch):
    code, seen, sent = run_worker(tmp_path, monkeypatch, debug_log=True)
    assert code == 0
    (announced,) = [event for event in sent if event["type"] == "debug_log"]
    path = Path(announced["path"])
    assert path.parent == (tmp_path / "Music" / LOGS_DIRNAME).resolve()
    assert seen["active"] is not None and seen["active"].path == path
    assert not debuglog.enabled()                       # dropped when the job ends
    items = entries(path)
    (start,) = find(items, "run", "start")
    assert start["front_end"] == "server" and "spotify_client_secret" not in start["options"]
    assert find(items, "links", "link")[0]["url"] == SOUNDCLOUD
    assert find(items, "run", "finished")[0]["status"] == "completed"


def test_worker_without_the_option_writes_no_debug_log(tmp_path, monkeypatch):
    code, seen, sent = run_worker(tmp_path, monkeypatch)
    assert code == 0 and seen["active"] is None
    assert not any(event["type"] == "debug_log" for event in sent)
    assert not list((tmp_path / "Music" / LOGS_DIRNAME).glob("debug-*"))


@pytest.fixture
def manager(tmp_path):
    return server.JobManager(tmp_path / "Music", {}, Console(file=io.StringIO()))


def test_job_shows_and_keeps_its_debug_log(manager, tmp_path):
    job = manager.submit([server.parse_link(SOUNDCLOUD)], {"debug_log": True}, [])
    path = str(tmp_path / "Music" / LOGS_DIRNAME / "debug-1.jsonl")
    with manager._cond:
        manager._apply(job, {"type": "debug_log", "path": path})
    assert manager.job(job.id)["debug_log"] == path
    restored = server._job_from_state(job.state(), None)
    assert restored.debug_log == path


def test_debug_log_is_served_only_from_the_log_folder(manager, tmp_path):
    logs = tmp_path / "Music" / LOGS_DIRNAME
    logs.mkdir(parents=True)
    good = logs / "debug-1.jsonl"
    good.write_text('{"event": "start"}\n', encoding="utf-8")
    elsewhere = tmp_path / "debug-2.jsonl"
    elsewhere.write_text("{}\n", encoding="utf-8")
    other = logs / "failures-1.log"
    other.write_text("x\n", encoding="utf-8")

    job = manager.submit([server.parse_link(SOUNDCLOUD)], {}, [])
    assert manager.debug_log(job.id) is None
    for path, expected in ((good, good.resolve()), (elsewhere, None), (other, None)):
        job.debug_log = str(path)
        assert manager.debug_log(job.id) == expected
    with pytest.raises(server.JobNotFound):
        manager.debug_log("ffff")


def test_debug_log_endpoint(tmp_path):
    import urllib.error
    import urllib.request

    manager = server.JobManager(tmp_path / "Music", {}, Console(file=io.StringIO()))
    httpd = server.make_server("127.0.0.1", 0, manager, "t" * 32)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}/api/v1"

    def get(path, token="t" * 32):
        req = urllib.request.Request(base + path)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, response.headers.get("Content-Type"), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, None, exc.read()

    try:
        logs = tmp_path / "Music" / LOGS_DIRNAME
        logs.mkdir(parents=True)
        log_file = logs / "debug-1.jsonl"
        log_file.write_text('{"event": "start"}\n', encoding="utf-8")
        job = manager.submit([server.parse_link(SOUNDCLOUD)], {"debug_log": True}, [])
        assert get(f"/jobs/{job.id}/debug-log")[0] == 404          # nothing announced yet
        job.debug_log = str(log_file)
        status, content_type, body = get(f"/jobs/{job.id}/debug-log")
        assert status == 200 and content_type.startswith("application/x-ndjson")
        assert body == b'{"event": "start"}\n'
        assert get(f"/jobs/{job.id}/debug-log", token=None)[0] == 401
        assert get("/jobs/ffff/debug-log")[0] == 404
        assert "debug_log" in server.FEATURES
    finally:
        httpd.shutdown()
        httpd.server_close()
