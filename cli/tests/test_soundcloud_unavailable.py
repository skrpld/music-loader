"""Tracks SoundCloud does not give out: counted as unavailable and listed,
never a failure; the opt-in fallback only takes a verified match."""
import io
import json
import threading
from pathlib import Path

import pytest

from music_loader import soundcloud as sc
from music_loader.artists import ArtistRegistry
from music_loader.availability import Reason, UnavailableTrack
from music_loader.config import AppConfig
from music_loader.events import EventDashboard
from music_loader.fallback import Candidate
from music_loader.runlog import RunLog
from music_loader.soundcloud_index import SoundCloudArchive, get_index
from music_loader.tags import SOURCE_DESC, SOURCE_URL_DESC, TrackTags, read_txxx, write_tags

DRM_JSON = {
    "id": 675214841, "title": "Locked Song", "duration": 201000, "full_duration": 201000,
    "permalink_url": "https://soundcloud.com/label/locked-song", "policy": "ALLOW",
    "user": {"username": "Label"}, "publisher_metadata": {"artist": "Artist"},
    "media": {"transcodings": [
        {"url": "https://x/ctr", "snipped": False, "format": {"protocol": "ctr-encrypted-hls"}},
        {"url": "https://x/cbc", "snipped": False, "format": {"protocol": "cbc-encrypted-hls"}},
    ]},
}
PLAIN_JSON = {
    "id": 111, "title": "Open Song", "duration": 120000, "policy": "ALLOW",
    "media": {"transcodings": [
        {"url": "https://x/hls", "snipped": False, "format": {"protocol": "hls"}},
    ]},
}


class FakeApi:
    def __init__(self, tracks=None, error=None):
        self.disabled = False
        self.tracks_data = tracks or {}
        self.error = error
        self.calls: list[list[str]] = []

    def tracks(self, ids):
        self.calls.append(list(ids))
        if self.error:
            raise self.error
        return {i: self.tracks_data[i] for i in ids if i in self.tracks_data}

    def album_for(self, *_args):
        return None


def make_ctx(tmp_path: Path, api=None, fallback=False):
    config = AppConfig.from_output_dir(tmp_path / "Music")
    config.ensure_dirs()
    config.lyrics_enabled = False
    config.soundcloud_fallback = fallback
    staging = config.soundcloud_dir / ".sc_downloads"
    staging.mkdir()
    runlog = RunLog(tmp_path / "logs")
    stream = io.StringIO()
    dashboard = EventDashboard(stream, runlog)
    ctx = sc._Context(
        config=config, soundcloud_dir=config.soundcloud_dir, staging_dir=staging,
        dashboard=dashboard, lyrics=None, abort=threading.Event(), yt=["yt-dlp"],
        api=api or FakeApi(), gate=sc._RateGate(),
        index=get_index(config.soundcloud_dir),
        archive=SoundCloudArchive(config.soundcloud_dir / ".sc_archive.txt"),
        registry=ArtistRegistry(tmp_path / "artists.json"),
    )
    return ctx, dashboard, runlog, stream


def job(track_id="675214841", title="Locked Song"):
    return sc.TrackJob(info={
        "id": track_id, "title": title, "uploader": "Label", "duration": 201.0,
        "webpage_url": f"https://soundcloud.com/label/{track_id}",
    }, lookup_album=False)


def events(stream):
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def unavailable_lines(runlog):
    return runlog.unavailable_path.read_text(encoding="utf-8").splitlines()


# -- up-front check ------------------------------------------------------------------------------
def test_precheck_flags_only_tracks_that_cannot_be_downloaded(tmp_path):
    api = FakeApi({"675214841": DRM_JSON, "111": PLAIN_JSON})
    ctx, *_ = make_ctx(tmp_path, api)
    sc._precheck([job("675214841"), job("111", "Open Song")], ctx)
    assert list(ctx.unavailable) == ["675214841"]
    flagged = ctx.unavailable["675214841"]
    assert flagged.reason is Reason.DRM
    assert flagged.artist == "Artist"
    assert api.calls == [["675214841", "111"]]


def test_precheck_asks_in_batches_and_skips_tracks_already_in_the_library(tmp_path):
    api = FakeApi()
    ctx, *_ = make_ctx(tmp_path, api)
    jobs = [job(str(1000 + n)) for n in range(120)]
    have = tmp_path / "have.mp3"
    have.write_bytes(b"x")
    ctx.index.add(jobs[0].info, have)
    sc._precheck(jobs, ctx)
    assert [len(call) for call in api.calls] == [50, 50, 19]
    assert "1000" not in {i for call in api.calls for i in call}


def test_precheck_failure_is_silent_for_the_run_and_a_429_pauses_downloads(tmp_path):
    api = FakeApi(error=RuntimeError("HTTP Error 429: Too Many Requests"))
    ctx, _dash, _log, stream = make_ctx(tmp_path, api)
    sc._precheck([job()], ctx)
    assert ctx.unavailable == {}
    assert ctx.gate.tripped
    assert len(api.calls) == 1                      # stops at the first failure
    assert any("checked while downloading" in e.get("text", "") for e in events(stream))
    assert not ctx.had_failure


# -- pipeline ----------------------------------------------------------------------------------
def test_flagged_track_is_skipped_without_a_download_and_is_not_a_failure(tmp_path, monkeypatch):
    ctx, dash, runlog, stream = make_ctx(tmp_path)
    ctx.unavailable["675214841"] = UnavailableTrack.from_api(DRM_JSON, Reason.DRM)
    monkeypatch.setattr(sc, "_download_one", lambda *a, **k: pytest.fail("must not download"))
    sc._run_pipeline([job()], ctx)

    assert dash.stats.soundcloud_tracks_unavailable == 1
    assert dash.stats.soundcloud_tracks_failed == 0
    assert not ctx.had_failure
    lines = unavailable_lines(runlog)
    assert len(lines) == 1 and "drm | Artist - Locked Song | https://soundcloud.com/label/locked-song" in lines[0]
    assert runlog.count == 0 and not runlog.path.exists()          # nothing in the failure log
    sent = events(stream)
    assert {"type": "unavailable_log", "path": str(runlog.unavailable_path)} in sent
    texts = [e["text"] for e in sent if e["type"] == "log"]
    assert "[SoundCloud] 'Locked Song' by Artist: DRM-protected on SoundCloud, can't be downloaded, skipped" in texts
    assert not any(e.get("level") == "error" for e in sent if e["type"] == "log")


def test_drm_found_by_yt_dlp_after_the_attempt_is_unavailable_too(tmp_path, monkeypatch):
    ctx, dash, runlog, _ = make_ctx(tmp_path)
    monkeypatch.setattr(
        sc, "_download_one",
        lambda j, c, slot, source_url=None: sc._Download(False, None, dict(j.info), unavailable=Reason.DRM),
    )
    sc._run_pipeline([job()], ctx)
    assert dash.stats.soundcloud_tracks_unavailable == 1
    assert not ctx.had_failure
    assert "drm | Label - Locked Song" in unavailable_lines(runlog)[0]


def test_a_real_failure_still_fails(tmp_path, monkeypatch):
    ctx, dash, runlog, _ = make_ctx(tmp_path)
    monkeypatch.setattr(sc, "_download_one", lambda j, c, slot, source_url=None: sc._Download(False, None, dict(j.info)))
    sc._run_pipeline([job()], ctx)
    assert dash.stats.soundcloud_tracks_failed == 1
    assert dash.stats.soundcloud_tracks_unavailable == 0
    assert ctx.had_failure
    assert not runlog.unavailable_path.exists()


# -- fallback ----------------------------------------------------------------------------------
def test_fallback_is_not_tried_unless_asked_for(tmp_path, monkeypatch):
    ctx, dash, *_ = make_ctx(tmp_path, fallback=False)
    ctx.unavailable["675214841"] = UnavailableTrack.from_api(DRM_JSON, Reason.DRM)
    monkeypatch.setattr(sc, "_fallback_download", lambda *a: pytest.fail("fallback is opt-in"))
    sc._run_pipeline([job()], ctx)
    assert dash.stats.soundcloud_tracks_unavailable == 1


def test_fallback_without_a_verified_match_leaves_the_track_unavailable(tmp_path, monkeypatch):
    ctx, dash, _runlog, stream = make_ctx(tmp_path, fallback=True)
    ctx.unavailable["675214841"] = UnavailableTrack.from_api(DRM_JSON, Reason.DRM)
    monkeypatch.setattr(sc, "find_match", lambda wanted, yt, abort: (None, 3))
    sc._run_pipeline([job()], ctx)
    assert dash.stats.soundcloud_tracks_unavailable == 1
    assert dash.stats.soundcloud_tracks_done == 0
    assert not ctx.had_failure
    texts = [e["text"] for e in events(stream) if e["type"] == "log"]
    assert any(t.endswith("skipped (no matching track on YouTube Music)") for t in texts)


def test_fallback_with_a_verified_match_downloads_it_and_marks_the_source(tmp_path, monkeypatch):
    ctx, dash, runlog, _ = make_ctx(tmp_path, fallback=True)
    ctx.unavailable["675214841"] = UnavailableTrack.from_api(DRM_JSON, Reason.DRM)
    match = Candidate("dQw4w9WgXcQ", "Locked Song", ("Artist",), 201.0)
    monkeypatch.setattr(sc, "find_match", lambda wanted, yt, abort: (match, 1))
    seen = {}

    def fake_download(j, c, slot, source_url=None):
        seen["url"], seen["duration"] = source_url, j.info["duration"]
        raw = c.staging_dir / "675214841.webm"
        raw.write_bytes(b"audio")
        return sc._Download(True, raw, dict(j.info))

    def fake_postprocess(j, raw, info, c):
        seen["source"] = info.get(sc._SOURCE_KEY)
        raw.unlink()
        return sc._Processed(True, c.soundcloud_dir / "a.mp3", False, None)

    monkeypatch.setattr(sc, "_download_one", fake_download)
    monkeypatch.setattr(sc, "_postprocess", fake_postprocess)
    sc._run_pipeline([job()], ctx)

    assert seen == {"url": "https://music.youtube.com/watch?v=dQw4w9WgXcQ", "duration": 201.0,
                    "source": "https://music.youtube.com/watch?v=dQw4w9WgXcQ"}
    assert dash.stats.soundcloud_tracks_done == 1
    assert dash.stats.soundcloud_tracks_unavailable == 0
    assert not runlog.unavailable_path.exists()


def test_fallback_needs_a_length_to_verify_against(tmp_path, monkeypatch):
    ctx, dash, *_ = make_ctx(tmp_path, fallback=True)
    data = dict(DRM_JSON, duration=0, full_duration=0)
    ctx.unavailable["675214841"] = UnavailableTrack.from_api(data, Reason.DRM)
    monkeypatch.setattr(sc, "find_match", lambda *a: pytest.fail("no search without a length"))
    nolength = job()
    nolength.info.pop("duration")
    sc._run_pipeline([nolength], ctx)
    assert dash.stats.soundcloud_tracks_unavailable == 1


# -- source note in the tags -------------------------------------------------------------------------
def test_source_note_is_written_and_readable(tmp_path):
    path = tmp_path / "a.mp3"
    path.write_bytes(b"")
    tags = TrackTags(title="T", artists=["A"], album="T - Single", album_artist="A",
                     extra={SOURCE_DESC: "YouTube Music", SOURCE_URL_DESC: "https://music.youtube.com/watch?v=x"})
    write_tags(path, tags)
    assert read_txxx(path, SOURCE_DESC) == "YouTube Music"
    assert read_txxx(path, SOURCE_URL_DESC) == "https://music.youtube.com/watch?v=x"
    assert read_txxx(path, "MISSING") == ""
