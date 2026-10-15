"""Lyrics lookup (lyrics.py) against provider responses: the failures of a
real run recorded in a debug log (tests/data/lyrics_debug_log.json) and
provider answers in their own JSON format. Strict mode must keep rejecting
other songs and other versions with timestamps."""
import json
import threading
from pathlib import Path

import pytest

from music_loader import lyrics as ly
from music_loader.cli import parse_args
from music_loader.config import (
    LYRICS_MODE_LOOSE,
    LYRICS_MODE_STRICT,
    LyricsSettings,
)
from music_loader.lyrics import Candidate, LyricsRequest, LyricsService, evaluate
from music_loader.net import Response
from music_loader.text_utils import looks_instrumental

RECORDED = json.loads((Path(__file__).parent / "data" / "lyrics_debug_log.json").read_text(encoding="utf-8"))
SYNCED = "[00:01.00]one\n[00:02.00]two\n[00:03.00]three"


class Dashboard:
    def __init__(self):
        self.lines: list[str] = []
        self.errors: list[str] = []
        self.found = self.missed = self.skipped = 0

    def log(self, text):
        self.lines.append(text)

    def log_error(self, source, text):
        self.errors.append(text)

    def record_lyrics(self, found):
        if found:
            self.found += 1
        else:
            self.missed += 1

    def record_lyrics_skipped(self):
        self.skipped += 1


class FakeHttp:
    """Routes provider requests to canned answers; records every call."""

    def __init__(self, routes):
        self.routes = routes          # [(predicate(url, params, headers), answer or [answers])]
        self.calls: list[tuple[str, dict, dict]] = []

    def __call__(self, url, params=None, headers=None, **_):
        params = dict(params or {})
        self.calls.append((url, params, dict(headers or {})))
        for match, answer in self.routes:
            if match(url, params, headers or {}):
                if isinstance(answer, list):
                    answer = answer.pop(0) if len(answer) > 1 else answer[0]
                status, body, extra = answer if len(answer) == 3 else (*answer, {})
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                return Response(status, raw, url, extra)
        return Response(404, b"{}", url)

    def count(self, part):
        return sum(1 for url, _, _ in self.calls if part in url)


@pytest.fixture
def http(monkeypatch):
    def install(routes):
        fake = FakeHttp(routes)
        monkeypatch.setattr(ly, "http_get", fake)
        return fake
    return install


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr(ly._Provider, "_pace", lambda self: None)
    monkeypatch.setattr(ly._Provider, "_wait", lambda self, seconds: waits.append(seconds) or True)
    return waits


def recorded_request(tmp_path, title, **overrides) -> LyricsRequest:
    data = RECORDED["requests"][title]
    path = tmp_path / "track.mp3"
    path.write_bytes(b"")
    fields = dict(key=title, audio_path=path, main_artists=data["artists"], featured=data["featured"],
                  title=data["title"], album=data["album"], duration=data["duration"])
    fields.update(overrides)
    return LyricsRequest(**fields)


def request(title="Song", artists=("Artist",), duration=200.0, tmp_path=None):
    return LyricsRequest(key=title, audio_path=(tmp_path or Path(".")) / "a.mp3", main_artists=list(artists),
                         title=title, duration=duration)


def lrclib_item(artist, title, duration, synced=SYNCED, plain="one\ntwo\nthree", item_id=1):
    return {"id": item_id, "artistName": artist, "trackName": title, "albumName": "", "duration": duration,
            "instrumental": False, "plainLyrics": plain, "syncedLyrics": synced}


def lrclib(path):
    return lambda url, params, headers: url.startswith("https://lrclib.net/api/" + path)


@pytest.fixture
def no_tags(monkeypatch):
    saved: dict[str, str] = {}
    monkeypatch.setattr(ly, "embed_lyrics", lambda path, text: saved.__setitem__("plain", text))
    monkeypatch.setattr(ly, "has_embedded_lyrics", lambda path: False)
    monkeypatch.setattr(ly, "remove_lyrics", lambda path: None)
    return saved


# -- recorded failures ----------------------------------------------------------------------------
def test_recorded_run_lrclib_rate_limit_is_retried_instead_of_switching_lrclib_off(tmp_path, http, no_waiting,
                                                                                   no_tags):
    # The run got HTTP 429 on its first requests and switched LRCLIB off
    # after three of them: 323 of 329 tracks never reached it.
    assert RECORDED["lrclib_statuses"][:2] == [429, 429]
    req = recorded_request(tmp_path, "yung millionaire")
    fake = http([
        (lrclib("get"), [(429, {}, {"retry-after": "3"}), (429, {}), (200, lrclib_item("WHITENER", "yung millionaire",
                                                                                      103.0))]),
    ])
    service = LyricsService(LYRICS_MODE_LOOSE, [ly.Lrclib()])
    outcome = service.process(req, None, Dashboard())
    assert outcome.status == "synced" and fake.count("/get") == 3
    assert no_waiting == [3.0, 5.0]          # Retry-After first, then the own backoff
    assert not service.providers[0].unavailable_reason


def test_a_provider_that_keeps_failing_pauses_and_comes_back(tmp_path, http, no_tags):
    fake = http([(lrclib(""), (429, {}))])
    provider = ly.Lrclib()
    service = LyricsService(LYRICS_MODE_STRICT, [provider])
    attempts = ly.LyricsAttempts(tmp_path / "attempts.json")
    dashboard = Dashboard()
    for index in range(4):
        outcome = service.process(request(f"Song {index}", tmp_path=tmp_path), attempts, dashboard)
        assert outcome.status == "not_found" and outcome.detail.startswith("search incomplete")
    assert provider.unavailable_reason.startswith("paused: HTTP 429")
    assert not provider.disabled
    assert any("paused" in line for line in dashboard.lines) and not dashboard.errors
    # Nothing was marked as searched: the next run asks again.
    assert not (tmp_path / "attempts.json").exists()
    calls = len(fake.calls)
    provider._paused_until = 0.0              # the pause is over
    service.process(request("Song 9", tmp_path=tmp_path), attempts, dashboard)
    assert len(fake.calls) > calls


def test_recorded_genius_403_switches_genius_off_once_and_tries_the_plain_client_name(tmp_path, http, no_tags):
    assert set(RECORDED["genius_statuses_first_10"]) == {403}
    fake = http([(lambda url, params, headers: "genius.com" in url, (403, b"Forbidden"))])
    service = LyricsService(LYRICS_MODE_STRICT, [ly.Genius()])
    attempts = ly.LyricsAttempts(tmp_path / "attempts.json")
    dashboard = Dashboard()
    first = service.process(request("Song", tmp_path=tmp_path), attempts, dashboard)
    service.process(request("Other", tmp_path=tmp_path), attempts, dashboard)
    agents = [headers["User-Agent"] for _, _, headers in fake.calls]
    assert len(agents) == 2 and "Mozilla" in agents[0] and agents[1].startswith("music-loader/")
    assert dashboard.errors == ["Genius switched off for this run: access refused (HTTP 403)"]
    # A refusal is not a rate limit: the search counts as done.
    assert first.status == "not_found" and first.detail == ""
    assert attempts.should_skip("Song", LYRICS_MODE_STRICT)


def test_genius_keeps_the_client_name_that_works(tmp_path, http, no_tags, monkeypatch):
    monkeypatch.setattr(ly.Genius, "_page_lyrics", lambda self, url: "the words")
    search = {"response": {"sections": [{"type": "song", "hits": [{"type": "song", "result": {
        "id": 7, "url": "https://genius.com/x", "title": "Song", "primary_artist": {"name": "Artist"},
        "lyrics_state": "complete"}}]}]}}
    browser = ly.BROWSER_USER_AGENT
    fake = http([
        (lambda url, params, headers: headers.get("User-Agent") == browser, (403, b"")),
        (lambda url, params, headers: "genius.com/api" in url, (200, search)),
    ])
    service = LyricsService(LYRICS_MODE_STRICT, [ly.Genius()])
    assert service.process(request("Song", tmp_path=tmp_path), None, Dashboard()).status == "plain"
    service.process(request("Song", tmp_path=tmp_path / "second"), None, Dashboard())
    assert [h["User-Agent"] == browser for _, _, h in fake.calls] == [True, False, False]


def test_recorded_musixmatch_search_that_ignores_the_query_is_switched_off(tmp_path, http):
    body = RECORDED["musixmatch_track_search_body"]
    assert {entry["track"]["artist_name"] for entry in body["track_list"]} == {"Doja Cat", "Calming Cat Music",
                                                                               "Cat Music"}

    def musixmatch(action):
        return lambda url, params, headers: url.endswith(action)
    ok = {"status_code": 200}
    fake = http([
        (musixmatch("token.get"), (200, {"message": {"header": ok, "body": {"user_token": "tok"}}})),
        (musixmatch("matcher.track.get"), (200, {"message": {"header": {"status_code": 404}, "body": []}})),
        (musixmatch("track.search"), (200, {"message": {"header": ok, "body": body}})),
    ])
    provider = ly.Musixmatch()
    service = LyricsService(LYRICS_MODE_LOOSE, [provider])
    titles = ["yung millionaire", "follow me", "check da tag"]
    for title in titles:
        outcome = service.process(recorded_request(tmp_path, title), None, Dashboard())
        assert outcome.status == "not_found"
    assert provider.disabled_reason.startswith("search ignores the query")
    searches = [params for url, params, _ in fake.calls if url.endswith("track.search")]
    assert [params["q"] for params in searches] == [f"WHITENER {title}" for title in titles]
    assert "q_track" not in searches[0]
    service.process(recorded_request(tmp_path, "anyday *prod playasin*"), None, Dashboard())
    assert len([url for url, _, _ in fake.calls if url.endswith("track.search")]) == 3


def test_musixmatch_matcher_finds_synced_lyrics(tmp_path, http, no_tags):
    ok = {"status_code": 200}
    track = {"track_id": 42, "artist_name": "WHITENER", "track_name": "anyday", "track_length": 90,
             "has_subtitles": 1, "instrumental": 0}
    fake = http([
        (lambda url, p, h: url.endswith("token.get"), (200, {"message": {"header": ok, "body": {"user_token": "t"}}})),
        (lambda url, p, h: url.endswith("matcher.track.get"), (200, {"message": {"header": ok, "body": {
            "track": track}}})),
        (lambda url, p, h: url.endswith("track.subtitle.get"), (200, {"message": {"header": ok, "body": {
            "subtitle": {"subtitle_body": SYNCED}}}})),
    ])
    service = LyricsService(LYRICS_MODE_STRICT, [ly.Musixmatch()])
    outcome = service.process(recorded_request(tmp_path, "anyday *prod playasin*"), None, Dashboard())
    assert outcome.status == "synced"
    matcher = next(params for url, params, _ in fake.calls if url.endswith("matcher.track.get"))
    assert (matcher["q_track"], matcher["q_artist"], matcher["q_duration"]) == ("anyday", "WHITENER", "90")


# -- queries and titles from the recorded run ----------------------------------------------------
@pytest.mark.parametrize("title, provider_title, query", [
    ("anyday *prod playasin*", "anyday", "anyday"),
    ("VAMPFLOW ***#UNRELEASED!***", "VAMPFLOW", "VAMPFLOW"),
    ("5 SE「prod. AQUAKEY」", "5 SE", "5 SE"),
    ("bloodrain ###", "bloodrain", "bloodrain"),
])
def test_recorded_noisy_titles_are_searched_clean_and_match_in_strict_mode(tmp_path, http, no_tags, title,
                                                                          provider_title, query):
    req = recorded_request(tmp_path, title)
    item = lrclib_item(req.main_artists[0], provider_title, round(req.duration))
    fake = http([(lrclib("get"), (404, {})), (lrclib("search"), (200, [item]))])
    outcome = LyricsService(LYRICS_MODE_STRICT, [ly.Lrclib()]).process(req, None, Dashboard())
    assert outcome.status == "synced"
    get = fake.calls[0][1]
    assert get["track_name"] == query and "Single" not in get["album_name"] and "prod" not in get["album_name"]


def test_recorded_artist_inside_the_title_is_searched_and_matched(tmp_path, http, no_tags):
    # "AQUAKEY - OLD NOKIA" uploaded by @aquagwap.
    req = recorded_request(tmp_path, "AQUAKEY - OLD NOKIA")
    fake = http([(lrclib("search"), [(200, []), (200, [lrclib_item("AQUAKEY", "OLD NOKIA", 152.0)])])])
    outcome = LyricsService(LYRICS_MODE_STRICT, [ly.Lrclib()]).process(req, None, Dashboard())
    assert outcome.status == "synced"
    assert {"track_name": "OLD NOKIA", "artist_name": "AQUAKEY"} in [params for _, params, _ in fake.calls]


def test_recorded_producer_in_bare_brackets_matches_in_loose_mode_only(tmp_path):
    req = recorded_request(tmp_path, "Deathtones (mxstrakh)")
    candidate = Candidate("LRCLIB", "platov", "Deathtones", 124.0, synced=SYNCED)
    assert evaluate(req, candidate, LYRICS_MODE_LOOSE).synced
    assert evaluate(req, candidate, LYRICS_MODE_STRICT).reason == "title mismatch"


def test_spelling_differences_of_an_artist():
    req = request("WINDRIDER", artists=("HARUMATSUI",))
    assert evaluate(req, Candidate("P", "Haru Matsui", "Windrider", 200.0, synced=SYNCED), LYRICS_MODE_STRICT).synced
    req = request("Сейчас", artists=("хестон",))
    loose = evaluate(req, Candidate("P", "Heston", "Seychas", 200.0, synced=SYNCED), LYRICS_MODE_LOOSE)
    assert loose.synced
    assert evaluate(req, Candidate("P", "Heston", "Seychas", 200.0, synced=SYNCED), LYRICS_MODE_STRICT).label == \
        "rejected"


# -- strict stays strict ----------------------------------------------------------------------------
@pytest.mark.parametrize("ours, candidate, reason", [
    (request("yung millionaire", artists=("WHITENER",), duration=103.5),
     Candidate("Musixmatch", "Doja Cat", "Casual", 240.0, synced=SYNCED), "artist mismatch"),
    (request("Intro", artists=("WHITENER",)), Candidate("P", "Drake", "Intro", 200.0, synced=SYNCED),
     "artist mismatch"),
    (request("Song (Part 2)"), Candidate("P", "Artist", "Song", 200.0, synced=SYNCED), "title mismatch"),
    (request("Song (Sped Up)", duration=160.0), Candidate("P", "Artist", "Song", 200.0, synced=SYNCED),
     "version mismatch (sped up vs original)"),
    (request("Song"), Candidate("P", "Artist", "Song (Remix)", 200.0, synced=SYNCED),
     "version mismatch (original vs remix)"),
    (request("Song"), Candidate("P", "Artist", "Song", 205.0, synced=SYNCED), "length differs by 5.0s"),
])
def test_strict_mode_still_rejects_other_songs_and_versions(ours, candidate, reason):
    verdict = evaluate(ours, candidate, LYRICS_MODE_STRICT)
    assert (verdict.label, verdict.reason) == ("rejected", reason)


def test_original_for_versions_gives_plain_text_only_and_only_for_same_words_versions():
    settings = LyricsSettings(original_for_versions=True)
    original = Candidate("P", "Artist", "Song", 200.0, synced=SYNCED)
    verdict = evaluate(request("Song (Sped Up)", duration=160.0), original, LYRICS_MODE_STRICT, settings)
    assert verdict.plain and not verdict.synced
    assert evaluate(request("Song (Remix)"), original, LYRICS_MODE_STRICT, settings).label == "rejected"
    assert evaluate(request("Song (Sped Up)"), Candidate("P", "Artist", "Song (Remix)", 200.0, synced=SYNCED),
                    LYRICS_MODE_STRICT, settings).label == "rejected"


# -- settings ---------------------------------------------------------------------------------------
def test_synced_only_never_takes_plain_text():
    settings = LyricsSettings(plain=False)
    verdict = evaluate(request(), Candidate("P", "Artist", "Song", 200.0, plain="words"), LYRICS_MODE_STRICT, settings)
    assert verdict.label == "rejected" and "plain text switched off" in verdict.reason
    assert evaluate(request(), Candidate("P", "Artist", "Song", 201.0, synced=SYNCED), LYRICS_MODE_STRICT,
                    settings).synced


def test_length_tolerance_widens_loose_plain_text_but_never_synced():
    candidate = Candidate("P", "Artist", "Song", 215.0, synced=SYNCED)
    assert evaluate(request(), candidate, LYRICS_MODE_LOOSE).reason == "length differs by 15.0s (more than 10s)"
    verdict = evaluate(request(), candidate, LYRICS_MODE_LOOSE, LyricsSettings(length_tolerance=20))
    assert verdict.plain and not verdict.synced


def test_retry_days(tmp_path):
    attempts = ly.LyricsAttempts(tmp_path / "attempts.json")
    attempts.record("k", LYRICS_MODE_STRICT)
    assert attempts.should_skip("k", LYRICS_MODE_STRICT, LyricsSettings().retry_seconds)
    assert not attempts.should_skip("k", LYRICS_MODE_STRICT, LyricsSettings(retry_days=0).retry_seconds)


def test_provider_list_and_order():
    service = LyricsService(LYRICS_MODE_STRICT, settings=LyricsSettings(providers=("genius", "lrclib")))
    assert [provider.name for provider in service.providers] == ["Genius", "LRCLIB"]
    assert all(provider.settings is service.settings for provider in service.providers)


def test_command_line_lyrics_settings():
    args = parse_args(["https://soundcloud.com/a/b", "--lyrics-providers", "musixmatch,LRCLIB",
                       "--lyrics-synced-only", "--lyrics-length-tolerance", "15",
                       "--lyrics-original-for-versions", "--lyrics-retry-days", "0"])
    assert LyricsSettings.from_args(args) == LyricsSettings(
        providers=("musixmatch", "lrclib"), plain=False, length_tolerance=15.0,
        original_for_versions=True, retry_days=0)
    assert LyricsSettings.from_args(parse_args(["https://soundcloud.com/a/b"])) == LyricsSettings()
    for bad in (["--lyrics-providers", "azlyrics"], ["--lyrics-providers", "genius,genius"],
                ["--lyrics-length-tolerance", "1"], ["--lyrics-retry-days", "-1"]):
        with pytest.raises(SystemExit):
            parse_args(["https://soundcloud.com/a/b", *bad])


def test_server_options_round_trip_and_validation():
    from music_loader import server

    link = "https://soundcloud.com/a/b"
    custom = LyricsSettings(providers=("genius",), plain=False, length_tolerance=30,
                            original_for_versions=True, retry_days=30)
    _, options, _ = server._parse_job_request({"links": [link], "options": custom.options()})
    assert LyricsSettings.from_options(options) == custom
    for bad in ({"lyrics_providers": []}, {"lyrics_providers": "lrclib"}, {"lyrics_length_tolerance": 100},
                {"lyrics_retry_days": 1.5}, {"lyrics_plain": "yes"}):
        with pytest.raises(server._RequestError):
            server._parse_job_request({"links": [link], "options": bad})
    assert LyricsSettings.from_options({"lyrics_providers": ["nope"]}) == LyricsSettings()


# -- cannot have lyrics -----------------------------------------------------------------------------
@pytest.mark.parametrize("title, skipped", [
    ("Beat It", False), ("Minus", False), ("Heartbeat", False),
    ("Song (Instrumental)", True), ("Song - минус", True), ("Song (Beat)", True), ("Live DJ Set", True),
])
def test_only_real_instrumentals_are_skipped(title, skipped):
    assert looks_instrumental(title) is skipped


def test_an_interrupted_run_stops_waiting(tmp_path, http, monkeypatch):
    monkeypatch.undo()                        # the real _wait
    http([(lrclib(""), (429, {}, {"retry-after": "20"}))])
    provider = ly.Lrclib()
    provider._interval = 0.0
    abort = threading.Event()
    abort.set()
    provider.abort = abort
    with pytest.raises(ly.ProviderError):
        list(provider.candidates(request(tmp_path=tmp_path), LYRICS_MODE_STRICT))
