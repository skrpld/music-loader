"""Account aliases and reupload accounts (accounts.py).

The AQUAKEY titles come from a debug log of a real run: the account
"@aquagwap" (uploader id 235795299, soundcloud.com/ak2angel) is the artist
AQUAKEY, and SoundCloud's artist field said "@aquagwap" on most uploads.
"""
import io
from pathlib import Path
import json
from datetime import date

import pytest
from rich.console import Console

from music_loader import accounts as acc
from music_loader import soundcloud as sc
from music_loader.artist_lookup import Hit, ServiceUnavailable, match_key
from music_loader.artists import ACCOUNTS_KEY, PRIORITY_SPOTIFY, ArtistRegistry
from music_loader.cli import artists_main
from music_loader.soundcloud_index import ORIGINALS_KEY, SoundCloudIndex
from music_loader.soundcloud_meta import AlbumContext, build_meta, credited_artists, info_from_api

AQUA_ID = "235795299"
AQUA = "@aquagwap"
AQUA_URL = "https://soundcloud.com/ak2angel"
TODAY = date(2026, 10, 15)

AQUA_TITLES = [
    ("BLAGOIBLAGO & AQUAKEY - DIANA", None),
    ("AQUAKEY - NEVER BE HAPPY (GOBLIN GRAVE) *2023 DEAD DEMO*", AQUA),
    ("AQUAKEY - ALONE (glorykeyz & aquagwap)", AQUA),
    ("AQUAKEY - OLD NOKIA (SHINE VERSION)", AQUA),
    ("AQUAKEY - MADNESS", AQUA),
    ("AQUAKEY - FAR AWAY", AQUA),
    ("AQUAKEY - OLD NOKIA", AQUA),
    ("AQUAKEY- UP & DOWN (MV IN DESCRIPTION)", None),
    ("AQUAKEY - MOSCOW NEVER SLEEP", None),
    ("AQUAKEY & JONE$ GRIFA - THE ONLY WAY", None),
]


def info(track_id, title, uploader=AQUA, uploader_id=AQUA_ID, artist=None, duration=180.0,
         timestamp=1_700_000_000.0, url=AQUA_URL, tags=None):
    data = {
        "id": str(track_id), "title": title, "uploader": uploader, "uploader_id": uploader_id,
        "uploader_url": url, "duration": duration, "timestamp": timestamp,
        "webpage_url": f"{url}/{track_id}", "tags": tags or [],
    }
    if artist:
        data["artist"] = artist
        data["artists"] = [artist]
    return data


def aqua_infos():
    return [info(100 + n, title, artist=artist, duration=150.0 + n, tags=["#AQUAKEY", "ZLG"])
            for n, (title, artist) in enumerate(AQUA_TITLES)]


class FakeServices:
    """Answers per service from a function of the query; `down` services raise."""

    def __init__(self, spotify=None, deezer=None, soundcloud=None, down=()):
        self._spotify = spotify or (lambda query: [])
        self._deezer = deezer or (lambda artist, title: [])
        self._soundcloud = soundcloud or (lambda title: [])
        self.down = set(down)
        self.calls = []

    def spotify(self, query):
        self.calls.append(("spotify", query))
        if "spotify" in self.down:
            raise ServiceUnavailable("down")
        return self._spotify(query)

    def deezer(self, artist, title):
        self.calls.append(("deezer", artist, title))
        if "deezer" in self.down:
            raise ServiceUnavailable("down")
        return self._deezer(artist, title)

    def soundcloud(self, title):
        self.calls.append(("soundcloud", title))
        if "soundcloud" in self.down:
            raise ServiceUnavailable("down")
        return self._soundcloud(title)


class FakeLibrary:
    def __init__(self, hits=None):
        self.hits = hits or {}
        self.calls = []

    def find(self, title):
        self.calls.append(title)
        return self.hits.get(match_key(title), [])


class Notes:
    def __init__(self):
        self.lines = []
        self.notes = []

    def log(self, text):
        self.lines.append(text)

    def record_artists(self, kind, text):
        self.notes.append((kind, text))


def spotify_by(artist, durations):
    """Spotify answers "<artist> <title>" with the song by `artist`."""
    def answer(query):
        for title, duration in durations.items():
            if query.endswith(title):
                return [Hit(title=title, artists=[artist], duration=duration, source="spotify")]
        return []
    return answer


def run(tmp_path, infos, new_ids=None, services=None, library=None, registry=None, index=None):
    registry = registry or ArtistRegistry(tmp_path / "artists.json")
    notes = Notes()
    runner = acc.RunAccounts(registry, notes, library or FakeLibrary(), services or FakeServices(),
                             index, today=lambda: TODAY)
    runner.evaluate(infos, set(new_ids if new_ids is not None else [i["id"] for i in infos]))
    return runner, notes


# -- the official artist field --------------------------------------------------------------
def test_an_official_artist_that_repeats_the_uploader_is_no_credit():
    track = info(1, "AQUAKEY - OLD NOKIA", artist=AQUA)
    assert credited_artists(track) == []
    meta = build_meta(track, None, ArtistRegistry(None))
    assert meta.main_artists == ["AQUAKEY"]
    assert meta.tags.title == "OLD NOKIA"
    assert meta.folder.startswith("AQUAKEY/")


def test_a_label_release_keeps_its_official_artist():
    track = info(2, "Song", uploader="Some Label", artist="Real Artist")
    assert credited_artists(track) == ["Real Artist"]
    assert build_meta(track, None, ArtistRegistry(None)).main_artists == ["Real Artist"]


def test_an_official_artist_equal_to_the_uploader_no_longer_hides_the_title_partner():
    track = info(3, "WHITENER + AQUAKEY - psycho 2 *mv in description*", uploader="WHITENER",
                 uploader_id="230191381", artist="WHITENER")
    assert build_meta(track, None, ArtistRegistry(None)).main_artists == ["WHITENER", "AQUAKEY"]


# -- local evidence ------------------------------------------------------------------------------
def test_candidate_needs_three_tracks_and_seventy_percent():
    facts = [acc.track_facts(item) for item in aqua_infos()]
    evidence = acc.local_evidence(AQUA, facts)
    assert evidence.candidate == "AQUAKEY"
    assert evidence.share == pytest.approx(0.8)          # "AQUAKEY- UP & DOWN" is not split
    assert evidence.hashtags == len(facts)
    assert acc.local_evidence(AQUA, facts[1:3]).candidate is None          # two tracks only
    mixed = facts[1:7] + [acc.track_facts(info(9, f"OTHER{n} - x{n}", duration=100.0)) for n in range(4)]
    assert acc.local_evidence(AQUA, mixed).candidate is None                # 60 %


def test_an_artist_who_features_with_others_gets_no_candidate():
    titles = ["platov & haru matsui - diesel", "haru matsui & platov - godline", "platov - nocturne",
              "best summer", "platov, TARABANDZ - x", "WHITENER, platov - heroin chic"]
    facts = [acc.track_facts(info(n, title, uploader="platov", uploader_id="1")) for n, title in enumerate(titles)]
    evidence = acc.local_evidence("platov", facts)
    assert evidence.candidate is None
    assert not evidence.mixed


def test_most_accounts_need_no_lookup(tmp_path):
    services = FakeServices()
    infos = [info(n, f"song {n}", uploader="platov", uploader_id="1") for n in range(5)]
    runner, notes = run(tmp_path, infos, services=services)
    assert services.calls == []
    assert runner.alias("1") is None
    assert notes.lines == [] and notes.notes == []
    assert runner.book.get("1").status == acc.STATUS_NICK


# -- verdicts ----------------------------------------------------------------------------------
def test_two_spotify_matches_confirm_the_name(tmp_path):
    services = FakeServices(spotify=spotify_by("AQUAKEY", {"NEVER BE HAPPY": 151.0,
                                                           "ALONE": 152.5, "OLD NOKIA": 153.0}))
    runner, notes = run(tmp_path, aqua_infos(), services=services)
    account = runner.book.get(AQUA_ID)
    assert (account.kind, account.alias, account.source) == ("own", "AQUAKEY", "spotify")
    assert account.checked_at == "2026-10-15" and account.permalink == "ak2angel"
    assert len([call for call in services.calls if call[0] == "spotify"]) == 2      # stops at two
    assert "Account @aquagwap is AQUAKEY (confirmed by Spotify)" in " ".join(notes.lines)
    assert ("alias", "@aquagwap → AQUAKEY (Spotify)") in notes.notes
    assert runner.registry.priority("AQUAKEY") == PRIORITY_SPOTIFY

    # Applied: no artist in the title -> AQUAKEY; the album artist and folder too.
    untitled = info(200, "STARS", artist=AQUA)
    meta = build_meta(untitled, None, runner.registry, accounts=runner)
    assert meta.main_artists == ["AQUAKEY"] and meta.folder == "AQUAKEY/STARS - Single"
    album = AlbumContext("Духлесс3", AQUA, "album", 1, 5)
    meta = build_meta(info(201, "AQUAKEY - CARE", artist=AQUA), album, runner.registry, accounts=runner)
    assert meta.tags.album_artist == "AQUAKEY" and meta.folder == "AQUAKEY/Духлесс3"
    # A name in the title always wins.
    meta = build_meta(info(202, "BLAGOIBLAGO - DIANA 2"), None, runner.registry, accounts=runner)
    assert meta.main_artists == ["BLAGOIBLAGO"]


def test_deezer_is_asked_when_spotify_does_not_have_it(tmp_path):
    def deezer(artist, title):
        return [Hit(title=title.upper(), artists=["Aquakey"], duration=151.0 if "NEVER" in title else 152.0,
                    source="deezer")]
    services = FakeServices(deezer=deezer)
    runner, _ = run(tmp_path, aqua_infos(), services=services)
    account = runner.book.get(AQUA_ID)
    assert (account.alias, account.source) == ("AQUAKEY", "deezer")


def test_the_library_confirms_without_any_request(tmp_path):
    library = FakeLibrary({
        match_key("NEVER BE HAPPY"): [Hit("Never Be Happy (Goblin Grave)", ["AQUAKEY"], 152.0,
                                                         "library", spotify=True)],
        match_key("ALONE"): [Hit("ALONE", ["AQUAKEY", "glorykeyz"], 151.0, "library", spotify=True)],
    })
    services = FakeServices()
    runner, _ = run(tmp_path, aqua_infos(), services=services, library=library)
    assert runner.book.get(AQUA_ID).source == "library"
    assert services.calls == []


def test_one_match_is_only_a_suggestion_and_nothing_is_applied(tmp_path):
    services = FakeServices(spotify=spotify_by("AQUAKEY", {"NEVER BE HAPPY": 151.0}))
    runner, notes = run(tmp_path, aqua_infos(), services=services)
    account = runner.book.get(AQUA_ID)
    assert account.status == acc.STATUS_SUGGESTION and account.suggestion == "AQUAKEY"
    assert runner.alias(AQUA_ID) is None
    assert ("suggestion", "@aquagwap → AQUAKEY?") in notes.notes
    # The nick stays for the album artist and folder; a title naming AQUAKEY still says so.
    meta = build_meta(info(300, "STARS", artist=AQUA), None, runner.registry, accounts=runner)
    assert meta.main_artists == [AQUA]
    meta = build_meta(info(301, "AQUAKEY - CARE", artist=AQUA), None, runner.registry, accounts=runner)
    assert meta.main_artists == ["AQUAKEY"]


def test_matches_with_other_artists_make_a_reupload_account(tmp_path):
    services = FakeServices(spotify=spotify_by("Somebody Else", {"NEVER BE HAPPY": 151.0,
                                                                 "ALONE": 152.0}))
    runner, notes = run(tmp_path, aqua_infos(), services=services)
    assert runner.book.get(AQUA_ID).kind == "reuploader"
    assert runner.is_reuploader(AQUA_ID)
    assert "Account @aquagwap is a reupload account" in notes.lines[-1] or any(
        "reupload account" in line for line in notes.lines)


def test_unreachable_services_decide_nothing_and_are_asked_again(tmp_path):
    services = FakeServices(down={"spotify", "deezer"})
    registry = ArtistRegistry(tmp_path / "artists.json")
    runner, notes = run(tmp_path, aqua_infos(), services=services, registry=registry)
    account = runner.book.get(AQUA_ID)
    assert account.kind == "unknown" and account.alias is None
    assert account.pending and account.status == acc.STATUS_NOT_CHECKED
    assert account.suggestion == "AQUAKEY"
    assert any("did not answer" in line for line in notes.lines)

    later = FakeServices(spotify=spotify_by("AQUAKEY", {"NEVER BE HAPPY": 151.0, "ALONE": 152.0}))
    runner, _ = run(tmp_path, aqua_infos(), services=later, registry=registry)
    assert runner.alias(AQUA_ID) == "AQUAKEY"


def test_a_decision_is_kept_for_thirty_days(tmp_path):
    registry = ArtistRegistry(tmp_path / "artists.json")
    services = FakeServices(spotify=spotify_by("AQUAKEY", {"NEVER BE HAPPY": 151.0, "ALONE": 152.0}))
    run(tmp_path, aqua_infos(), services=services, registry=registry)
    again = FakeServices()
    runner, _ = run(tmp_path, aqua_infos(), new_ids=["105"], services=again, registry=registry)
    assert again.calls == [] and runner.alias(AQUA_ID) == "AQUAKEY"


# -- user control ---------------------------------------------------------------------------------
def test_locked_and_rejected_decisions_are_never_changed_by_a_run(tmp_path):
    registry = ArtistRegistry(tmp_path / "artists.json")
    services = FakeServices(spotify=spotify_by("AQUAKEY", {"NEVER BE HAPPY": 151.0}))
    run(tmp_path, aqua_infos(), services=services, registry=registry)
    book = acc.AccountBook(registry)
    assert book.reject(AQUA_URL).suggestion is None
    services = FakeServices(spotify=spotify_by("AQUAKEY", {"NEVER BE HAPPY": 151.0, "ALONE": 152.0}))
    runner, notes = run(tmp_path, aqua_infos(), services=services, registry=registry)
    assert runner.book.get(AQUA_ID).suggestion is None and runner.alias(AQUA_ID) is None
    assert services.calls == [] and notes.notes == []                         # hidden for good

    book.set_alias("@aquagwap", "AQUAKEY")
    services = FakeServices(spotify=spotify_by("Other", {"NEVER BE HAPPY": 151.0, "ALONE": 152.0}))
    runner, _ = run(tmp_path, aqua_infos(), services=services, registry=registry)
    account = runner.book.get(AQUA_ID)
    assert account.locked and account.alias == "AQUAKEY" and account.source == "manual"
    assert services.calls == []
    assert book.unlock(AQUA_ID).locked is False


def test_user_actions_name_accounts_by_id_link_or_handle(tmp_path):
    registry = ArtistRegistry(tmp_path / "artists.json")
    run(tmp_path, aqua_infos(), services=FakeServices(
        spotify=spotify_by("AQUAKEY", {"NEVER BE HAPPY": 151.0})), registry=registry)
    book = acc.AccountBook(registry)
    assert book.find(AQUA_ID).id == AQUA_ID
    assert book.find("soundcloud.com/ak2angel/tracks").id == AQUA_ID
    assert book.find("@aquagwap").id == AQUA_ID and book.find("ak2angel").id == AQUA_ID
    accepted = acc.apply_action(book, "accept", "ak2angel")
    assert (accepted.alias, accepted.locked, accepted.status) == ("AQUAKEY", True, acc.STATUS_CONFIRMED)
    with pytest.raises(acc.AccountError):
        acc.apply_action(book, "accept", AQUA_ID)                        # nothing left to accept
    with pytest.raises(acc.AccountError):
        acc.apply_action(book, "keep", "nobody")
    with pytest.raises(acc.AccountError):
        acc.apply_action(book, "set", AQUA_ID, "  ")
    assert acc.apply_action(book, "reuploader", AQUA_ID).kind == "reuploader"
    assert acc.apply_action(book, "keep", AQUA_ID).status == acc.STATUS_NICK


# -- re-evaluation ----------------------------------------------------------------------------------
def test_a_new_track_not_found_under_the_name_reopens_the_decision(tmp_path):
    registry = ArtistRegistry(tmp_path / "artists.json")
    reup = [info(500 + n, f"Artist{n} - song {n}", uploader="slmndk", uploader_id="9", duration=120.0 + n)
            for n in range(3)]
    # So far only AQUAKEY tracks: confirmed as AQUAKEY.
    first = [info(400 + n, f"AQUAKEY - track {n}", uploader="slmndk", uploader_id="9", duration=100.0 + n)
             for n in range(4)]
    services = FakeServices(spotify=spotify_by("AQUAKEY", {"track 0": 100.0, "track 1": 101.0}))
    runner, _ = run(tmp_path, first, services=services, registry=registry)
    assert runner.alias("9") == "AQUAKEY"
    # Then tracks of other artists and one without an artist that is not AQUAKEY's.
    later = reup + [info(600, "untitled thing", uploader="slmndk", uploader_id="9", duration=90.0)]
    services = FakeServices()
    runner, notes = run(tmp_path, later, services=services, registry=registry)
    assert runner.book.get("9").kind == "reuploader"
    assert any("deciding again" in line for line in notes.lines)


# -- originals of reuploads --------------------------------------------------------------------------
def reuploader_registry(tmp_path):
    registry = ArtistRegistry(tmp_path / "artists.json")
    registry.put_account("9", acc.Account("9", handle="slmndk", kind="reuploader", source="manual",
                                          locked=True).to_dict())
    return registry


def test_reupload_gets_the_artist_of_the_earlier_original_upload(tmp_path):
    registry = reuploader_registry(tmp_path)
    original = {"id": 777, "title": "AQUAKEY - шишки на грядке", "duration": 140000,
                "created_at": "2020-01-01T00:00:00Z",
                "user": {"id": AQUA_ID, "username": AQUA, "permalink_url": AQUA_URL},
                "permalink_url": f"{AQUA_URL}/shishki"}
    services = FakeServices(soundcloud=lambda title: [Hit(
        title=original["title"], artists=[], duration=140.0, source="soundcloud", account_id=AQUA_ID,
        account_name=AQUA, timestamp=1_577_836_800.0, soundcloud_id="777", info=info_from_api(original))])
    index = SoundCloudIndex(tmp_path / "sc")
    (tmp_path / "sc").mkdir()
    track = info(800, "slmndk - шишки на грядке", uploader="slmndk", uploader_id="9", duration=141.0,
                 url="https://soundcloud.com/slmndk")
    runner, notes = run(tmp_path, [track], services=services, registry=registry, index=index)
    meta = build_meta(track, None, registry, accounts=runner)
    assert meta.main_artists == ["AQUAKEY"] and meta.tags.title == "шишки на грядке"
    assert any("artist from the original upload by AQUAKEY" in line for line in notes.lines)
    # Cached per track: the search runs once.
    again = FakeServices()
    run(tmp_path, [track], services=again, registry=registry, index=index)
    assert again.calls == []
    index.flush()
    assert json.loads((tmp_path / "sc" / ".sc_index.json").read_text())[ORIGINALS_KEY]["800"]["artists"] == ["AQUAKEY"]


def test_original_search_order_library_soundcloud_then_spotify(tmp_path):
    registry = reuploader_registry(tmp_path)
    track = info(801, "midnight", uploader="slmndk", uploader_id="9", duration=200.0)
    library = FakeLibrary({match_key("midnight"): [Hit("Midnight", ["Real One"], 201.0, "library")]})
    services = FakeServices()
    runner, _ = run(tmp_path, [track], services=services, library=library, registry=registry)
    assert runner.original("801")["artists"] == ["Real One"] and services.calls == []

    services = FakeServices(spotify=lambda q: [Hit("Midnight", ["Spot Artist", "Guest"], 199.0, "spotify")])
    runner, _ = run(tmp_path, [info(802, "midnight", uploader="slmndk", uploader_id="9", duration=200.0)],
                    services=services, registry=registry)
    assert [call[0] for call in services.calls] == ["soundcloud", "spotify"]
    meta = build_meta(info(802, "midnight", uploader="slmndk", uploader_id="9"), None, registry, accounts=runner)
    assert meta.main_artists == ["Spot Artist"] and meta.featured == ["Guest"]
    assert meta.tags.title == "midnight (feat. Guest)"


def test_reupload_without_an_original_keeps_the_account_and_is_reported(tmp_path):
    registry = reuploader_registry(tmp_path)
    track = info(803, "lost song", uploader="slmndk", uploader_id="9")
    runner, notes = run(tmp_path, [track], services=FakeServices(), registry=registry)
    assert ("undetermined", "lost song") in notes.notes
    assert build_meta(track, None, registry, accounts=runner).main_artists == ["slmndk"]


def test_an_unreachable_service_is_not_cached_as_not_found(tmp_path):
    registry = reuploader_registry(tmp_path)
    index = SoundCloudIndex(tmp_path)
    track = info(804, "lost song", uploader="slmndk", uploader_id="9")
    run(tmp_path, [track], services=FakeServices(down={"soundcloud", "spotify", "deezer"}),
        registry=registry, index=index)
    assert index.original("804") is None


# -- the registry file ----------------------------------------------------------------------------------
def test_registry_file_stays_compatible_both_ways(tmp_path):
    path = tmp_path / "artists.json"
    path.write_text(json.dumps({"aquakey": {"name": "AQUAKEY", "priority": 1}}), encoding="utf-8")
    registry = ArtistRegistry(path)
    assert registry.canonical("aquakey") == "AQUAKEY"
    registry.put_account(AQUA_ID, acc.Account(AQUA_ID, handle=AQUA).to_dict())
    registry.flush()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["aquakey"] == {"name": "AQUAKEY", "priority": 1}
    # An older version keeps every entry with a "name": the accounts survive its rewrite.
    old_view = {key: value for key, value in raw.items() if isinstance(value, dict) and isinstance(value.get("name"), str)}
    assert ACCOUNTS_KEY in old_view
    assert ArtistRegistry(path).account(AQUA_ID)["handle"] == AQUA


def test_a_flush_keeps_what_another_process_changed(tmp_path):
    path = tmp_path / "artists.json"
    job = ArtistRegistry(path)
    job.put_account("1", {"handle": "a", "kind": "unknown"})
    job.flush()
    app = ArtistRegistry(path)
    acc.AccountBook(app).set_alias("1", "Real Name")
    app.flush()
    job.register("Somebody", PRIORITY_SPOTIFY)
    job.put_account("2", {"handle": "b"})
    job.flush()
    fresh = ArtistRegistry(path)
    assert fresh.account("1")["alias"] == "Real Name"
    assert fresh.account("2")["handle"] == "b" and fresh.canonical("somebody") == "Somebody"


# -- pipeline: the job's track JSON feeds the decision -------------------------------------------------------
class FakeApi:
    disabled = False

    def __init__(self, found):
        self.found = found
        self.calls = []

    def tracks(self, ids):
        self.calls.append(list(ids))
        return {i: self.found[i] for i in ids if i in self.found}


def test_precheck_hands_uploaders_and_library_tracks_to_the_decision(tmp_path):
    import threading

    from music_loader.config import AppConfig
    from music_loader.events import EventDashboard
    from music_loader.soundcloud_index import SoundCloudArchive, get_index

    config = AppConfig.from_output_dir(tmp_path / "Music")
    config.ensure_dirs()
    index = get_index(config.soundcloud_dir)
    jobs = [sc.TrackJob(info={"id": str(100 + n), "webpage_url": f"{AQUA_URL}/{n}"}) for n in range(len(AQUA_TITLES))]
    found = {}
    for n, (title, artist) in enumerate(AQUA_TITLES):
        found[str(100 + n)] = {
            "id": 100 + n, "title": title, "duration": (150 + n) * 1000, "created_at": "2024-01-01T00:00:00Z",
            "user": {"id": int(AQUA_ID), "username": AQUA, "permalink_url": AQUA_URL},
            "publisher_metadata": {"artist": artist} if artist else {}, "media": {"transcodings": []},
            "permalink_url": f"{AQUA_URL}/{n}", "policy": "ALLOW",
        }
    # Two tracks are in the library already (an older entry without an uploader id).
    have = tmp_path / "have.mp3"
    have.write_bytes(b"x")
    for track_id in ("100", "101"):
        index.add({"id": track_id, "title": found[track_id]["title"], "uploader": AQUA, "duration": 150}, have)
        del found[track_id]
    seen = {}

    class Recorder:
        def evaluate(self, infos, new_ids, abort):
            seen["infos"], seen["new"] = infos, new_ids

    registry = ArtistRegistry(tmp_path / "artists.json")
    api = FakeApi(found)
    ctx = sc._Context(
        config=config, soundcloud_dir=config.soundcloud_dir, staging_dir=tmp_path / "staging",
        dashboard=EventDashboard(io.StringIO()), lyrics=None, abort=threading.Event(), yt=["yt-dlp"],
        api=api, gate=sc._RateGate(), index=index,
        archive=SoundCloudArchive(config.soundcloud_dir / ".sc_archive.txt"), registry=registry,
        accounts=Recorder(),
    )
    sc._precheck(jobs, ctx)
    assert "100" not in api.calls[0]
    assert len(seen["infos"]) == len(AQUA_TITLES)
    assert {item["uploader_id"] for item in seen["infos"]} == {AQUA_ID}
    assert seen["new"] == {str(100 + n) for n in range(2, len(AQUA_TITLES))}
    assert jobs[5].info["uploader"] == AQUA and jobs[5].info["title"] == AQUA_TITLES[5][0]


# -- CLI and server ----------------------------------------------------------------------------------------
def seeded(tmp_path):
    registry = ArtistRegistry(tmp_path / ".music-loader-artists.json")
    registry.put_account(AQUA_ID, acc.Account(AQUA_ID, handle=AQUA, permalink="ak2angel",
                                              suggestion="AQUAKEY", checked_at="2026-10-15").to_dict())
    registry.flush()


def test_cli_lists_and_changes_accounts(tmp_path, capsys):
    seeded(tmp_path)
    console = Console(file=io.StringIO(), width=200)
    assert artists_main(["-o", str(tmp_path)], console) == 0
    assert "suggestion" in console.file.getvalue() and "AQUAKEY?" in console.file.getvalue()
    assert artists_main(["-o", str(tmp_path), "accept", "@aquagwap"], console) == 0
    assert artists_main(["-o", str(tmp_path), "--json"], console) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed[0]["alias"] == "AQUAKEY" and listed[0]["locked"] is True
    assert artists_main(["-o", str(tmp_path), "set", "nobody", "X"], console) == 1
    assert artists_main(["-o", str(tmp_path), "set", AQUA_ID, "AQUA", "KEY"], console) == 0
    assert ArtistRegistry(tmp_path / ".music-loader-artists.json").account(AQUA_ID)["alias"] == "AQUA KEY"


def test_server_lists_and_changes_accounts(tmp_path):
    import threading
    import urllib.error
    import urllib.request

    from music_loader import server

    seeded(tmp_path)
    token = "test-token-0123456789abcdef"
    manager = server.JobManager(tmp_path, {}, Console(file=io.StringIO()))
    httpd = server.make_server("127.0.0.1", 0, manager, token)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}/api/v1"

    def call(method, path, body=None):
        data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
        request = urllib.request.Request(base + path, data=data, method=method)
        request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"null")

    try:
        assert "artists" in call("GET", "/info")[1]["features"]
        status, listing = call("GET", "/artists")
        assert status == 200 and listing["accounts"][0]["status"] == "suggestion"
        assert call("POST", f"/artists/{AQUA_ID}/reject")[1]["account"]["suggestion"] is None
        assert call("POST", f"/artists/{AQUA_ID}/accept")[0] == 400
        assert call("POST", f"/artists/{AQUA_ID}/set", {"name": ""})[0] == 400
        status, changed = call("POST", f"/artists/{AQUA_ID}/set", {"name": "AQUAKEY"})
        assert status == 200 and changed["account"]["status"] == "confirmed" and changed["account"]["locked"]
        assert call("POST", "/artists/999/keep")[0] == 404
        assert call("POST", f"/artists/{AQUA_ID}/explode")[0] == 404
        assert call("POST", "/artists/../keep")[0] == 404
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_job_keeps_the_artist_notes_of_its_run():
    from music_loader import server
    from music_loader.events import EventDashboard

    job = server.Job(id="ab", links=[], options={}, rejected=[])
    stream = io.StringIO()
    EventDashboard(stream).record_artists("alias", "@aquagwap → AQUAKEY (Spotify)")
    for line in stream.getvalue().splitlines():
        server.JobManager._apply(job, json.loads(line))
    server.JobManager._apply(job, {"type": "artists", "kind": "nonsense", "text": "x"})
    assert job.detail()["artists"]["alias"] == ["@aquagwap → AQUAKEY (Spotify)"]
    assert job.state()["artists"]["suggestion"] == []


def test_an_account_with_many_title_artists_looks_up_its_untitled_tracks(tmp_path):
    infos = [info(900 + n, f"Artist{n} - song {n}", uploader="mixtapes", uploader_id="5") for n in range(4)]
    infos.append(info(950, "nameless", uploader="mixtapes", uploader_id="5"))
    services = FakeServices()
    runner, notes = run(tmp_path, infos, services=services)
    assert runner.book.get("5").kind == "unknown"                       # no global decision
    assert ("soundcloud", "nameless") in services.calls
    assert ("undetermined", "nameless") in notes.notes
    # An artist's own account with a single feature is not "mixed": nothing is looked up.
    own = [info(960 + n, f"song {n}", uploader="solo", uploader_id="6") for n in range(4)]
    own.append(info(970, "Friend & solo - collab", uploader="solo", uploader_id="6"))
    services = FakeServices()
    run(tmp_path, own, services=services)
    assert services.calls == []


# -- lookups ------------------------------------------------------------------------------------------------
def test_library_catalog_finds_songs_by_file_name_and_reads_their_tags(tmp_path):
    from music_loader.artist_lookup import LibraryCatalog
    from music_loader.tags import TrackTags, write_tags

    folder = tmp_path / "AQUAKEY - OLD NOKIA"
    folder.mkdir()
    path = folder / "01 - OLD NOKIA.mp3"
    path.write_bytes(b"")
    write_tags(path, TrackTags(title="OLD NOKIA", artists=["AQUAKEY"], album="OLD NOKIA", album_artist="AQUAKEY",
                               url="https://open.spotify.com/track/abc"))
    hidden = tmp_path / ".sc_downloads"
    hidden.mkdir()
    (hidden / "01 - OLD NOKIA.mp3").write_bytes(b"")
    hits = LibraryCatalog(tmp_path, tmp_path / "SoundCloud").find("OLD NOKIA (SHINE VERSION)")
    assert [(hit.title, hit.artists, hit.spotify) for hit in hits] == [("OLD NOKIA", ["AQUAKEY"], True)]


def test_deezer_answers_and_errors(monkeypatch):
    from music_loader import artist_lookup
    from music_loader.config import AppConfig
    from music_loader.net import Response

    answers = []

    def fake_get(url, params=None, **kwargs):
        answers.append(params["q"])
        if "quota" in params["q"]:
            return Response(200, json.dumps({"error": {"code": 4, "message": "Quota limit exceeded"}}).encode(), url)
        body = {"data": [{"title": "OLD NOKIA", "duration": 156, "artist": {"name": "AQUAKEY"}}]}
        return Response(200, json.dumps(body).encode(), url)

    monkeypatch.setattr(artist_lookup, "http_get", fake_get)
    services = artist_lookup.OnlineServices(AppConfig.from_output_dir(Path("/tmp/x")))
    hits = services.deezer("AQUAKEY", 'OLD "NOKIA"')
    assert answers == ['artist:"AQUAKEY" track:"OLD  NOKIA"']
    assert (hits[0].artists, hits[0].duration) == (["AQUAKEY"], 156.0)
    services.deezer("AQUAKEY", 'OLD "NOKIA"')
    assert len(answers) == 1                                               # cached
    with pytest.raises(ServiceUnavailable):
        services.deezer("quota", "x")
    with pytest.raises(ServiceUnavailable):
        services.deezer("AQUAKEY", "anything else")                         # down for the run


def test_spotify_search_is_never_read_as_a_list_or_a_file():
    from music_loader.artist_lookup import _safe_query

    assert _safe_query("album: x") == "album x"
    assert _safe_query("songs.spotdl") == "songs"
    assert ":" not in _safe_query("https://open.spotify.com/track/1")
    assert _safe_query("--help me") == "help me"
