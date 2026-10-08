import pytest

from music_loader.fallback import (
    Candidate,
    Wanted,
    candidate_from_info,
    check_candidate,
    search_url,
)

WANTED = Wanted("Locked Song", ("Artist",), 201.0)


def candidate(title="Locked Song", artists=("Artist",), duration=201.0):
    return Candidate("dQw4w9WgXcQ", title, tuple(artists), duration)


def test_the_same_song_is_accepted():
    assert check_candidate(WANTED, candidate()) is None
    # A few seconds off and "feat." in the title are still the same song.
    assert check_candidate(WANTED, candidate("Locked Song (feat. Someone)", duration=203.5)) is None
    # Every main artist must be there, in any spelling and order.
    both = Wanted("Locked Song", ("Artist", "Other"), 201.0)
    assert check_candidate(both, candidate(artists=("other", "ARTIST", "Guest"))) is None
    assert check_candidate(both, candidate(artists=("Artist",))) == "different artist"


@pytest.mark.parametrize(
    "changes, why",
    [
        ({"title": "Another Song"}, "different title"),
        ({"title": "Locked Song (Sped Up)"}, "different version"),
        ({"title": "Locked Song (Live)"}, "different version"),
        ({"artists": ("Somebody Else",)}, "different artist"),
        ({"artists": ()}, "artist unknown"),
        ({"duration": 210.0}, "different length"),
        ({"duration": 30.0}, "different length"),
        ({"duration": None}, "length unknown"),
    ],
)
def test_anything_that_does_not_match_is_refused(changes, why):
    assert check_candidate(WANTED, candidate(**changes)) == why


def test_a_sped_up_original_does_not_match_the_plain_song():
    wanted = Wanted("Locked Song (Sped Up)", ("Artist",), 180.0)
    assert check_candidate(wanted, candidate(duration=180.0)) == "different version"
    assert check_candidate(wanted, candidate("Locked Song (Sped Up)", duration=180.0)) is None


def test_no_comparison_without_a_wanted_length():
    assert check_candidate(Wanted("Locked Song", ("Artist",), 0.0), candidate()) == "SoundCloud length unknown"


def test_candidate_from_a_youtube_music_page():
    info = {"id": "dQw4w9WgXcQ", "title": "Locked Song (Official Audio)", "track": "Locked Song",
            "artists": ["Artist", "Guest"], "duration": 201, "channel": "Artist - Topic"}
    found = candidate_from_info(info)
    assert (found.title, found.artists, found.duration) == ("Locked Song", ("Artist", "Guest"), 201.0)
    assert found.url == "https://music.youtube.com/watch?v=dQw4w9WgXcQ"


def test_artist_comes_from_a_topic_channel_only():
    topic = candidate_from_info({"id": "dQw4w9WgXcQ", "title": "T", "channel": "Artist - Topic", "duration": 1})
    assert topic.artists == ("Artist",)
    # A random uploader is not evidence of the artist.
    other = candidate_from_info({"id": "dQw4w9WgXcQ", "title": "T", "channel": "Some Reuploader", "duration": 1})
    assert other.artists == ()


@pytest.mark.parametrize("info", [None, [], {}, {"id": "../etc"}, {"id": "x"}])
def test_unusable_pages_are_no_candidates(info):
    assert candidate_from_info(info) is None


def test_search_url_is_the_songs_section_for_artist_and_title():
    url = search_url(Wanted("Locked Song", ("Artist", "Other"), 1.0))
    assert url == "https://music.youtube.com/search?q=Artist+Locked+Song#songs"
