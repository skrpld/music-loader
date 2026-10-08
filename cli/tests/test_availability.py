import copy

import pytest

from music_loader.availability import (
    FailureCategory,
    Reason,
    UnavailableTrack,
    classify_error,
    classify_track,
)

# Trimmed SoundCloud API v2 track JSON (api-v2.soundcloud.com/tracks/<id>).
_PLAIN_HLS = {
    "url": "https://api-v2.soundcloud.com/media/soundcloud:tracks:1/abc/stream/hls",
    "preset": "mp3_0_1",
    "duration": 200000,
    "snipped": False,
    "format": {"protocol": "hls", "mime_type": "audio/mpeg"},
}
_PLAIN_PROGRESSIVE = {
    "url": "https://api-v2.soundcloud.com/media/soundcloud:tracks:1/abc/stream/progressive",
    "preset": "mp3_0_1",
    "duration": 200000,
    "snipped": False,
    "format": {"protocol": "progressive", "mime_type": "audio/mpeg"},
}
_CTR = {
    "url": "https://api-v2.soundcloud.com/media/soundcloud:tracks:1/abc/stream/ctr-encrypted-hls",
    "preset": "aac_160k",
    "duration": 200000,
    "snipped": False,
    "format": {"protocol": "ctr-encrypted-hls", "mime_type": "audio/mp4; codecs=\"mp4a.40.2\""},
}
_CBC = {
    "url": "https://api-v2.soundcloud.com/media/soundcloud:tracks:1/abc/stream/cbc-encrypted-hls",
    "preset": "aac_160k",
    "duration": 200000,
    "snipped": False,
    "format": {"protocol": "cbc-encrypted-hls", "mime_type": "audio/mp4; codecs=\"mp4a.40.2\""},
}
_PREVIEW = {
    "url": "https://api-v2.soundcloud.com/media/soundcloud:tracks:1/abc/preview/hls",
    "preset": "mp3_0_1",
    "duration": 30000,
    "snipped": True,
    "format": {"protocol": "hls", "mime_type": "audio/mpeg"},
}


def _track(policy="ALLOW", transcodings=(), **extra):
    data = {
        "id": 675214841,
        "kind": "track",
        "title": "Some Song",
        "duration": 201000,
        "full_duration": 201000,
        "permalink_url": "https://soundcloud.com/label/some-song",
        "policy": policy,
        "monetization_model": "NOT_APPLICABLE",
        "downloadable": False,
        "has_downloads_left": False,
        "user": {"id": 1, "username": "Some Label"},
        "publisher_metadata": {"artist": "Some Artist", "urn": "soundcloud:tracks:675214841"},
        "media": {"transcodings": [copy.deepcopy(item) for item in transcodings]},
    }
    data.update(extra)
    return data


# -- classify_track -------------------------------------------------------------------------
def test_normal_track_is_available():
    assert classify_track(_track(transcodings=[_PLAIN_PROGRESSIVE, _PLAIN_HLS])) is None


def test_all_transcodings_encrypted_is_drm():
    assert classify_track(_track(transcodings=[_CTR, _CBC])) is Reason.DRM


def test_single_encrypted_transcoding_is_drm():
    assert classify_track(_track(transcodings=[_CBC])) is Reason.DRM


def test_mixed_encrypted_and_plain_is_available():
    # yt-dlp skips the encrypted streams and takes the plain one.
    assert classify_track(_track(transcodings=[_CTR, _CBC, _PLAIN_HLS])) is None


def test_encrypted_full_with_plain_preview_is_drm():
    assert classify_track(_track(transcodings=[_CTR, _PREVIEW])) is Reason.DRM


def test_policy_snip_is_a_preview():
    data = _track(policy="SNIP", transcodings=[_PREVIEW])
    assert classify_track(data) is Reason.PREVIEW


def test_policy_snip_without_transcodings_is_a_preview():
    assert classify_track(_track(policy="SNIP")) is Reason.PREVIEW


def test_policy_snip_with_only_encrypted_streams_is_drm():
    assert classify_track(_track(policy="SNIP", transcodings=[_CTR])) is Reason.DRM


def test_only_snipped_transcodings_is_a_preview():
    assert classify_track(_track(transcodings=[_PREVIEW])) is Reason.PREVIEW


def test_policy_block_is_blocked():
    assert classify_track(_track(policy="BLOCK", transcodings=[_PLAIN_HLS])) is Reason.BLOCKED
    assert classify_track(_track(policy="BLOCK")) is Reason.BLOCKED


def test_offered_original_download_wins():
    data = _track(policy="SNIP", transcodings=[_PREVIEW], downloadable=True, has_downloads_left=True)
    assert classify_track(data) is None
    # Downloadable but no downloads left: nothing to take.
    data = _track(policy="SNIP", transcodings=[_PREVIEW], downloadable=True, has_downloads_left=False)
    assert classify_track(data) is Reason.PREVIEW


def test_monetize_policy_with_plain_stream_is_available():
    assert classify_track(_track(policy="MONETIZE", transcodings=[_PLAIN_HLS])) is None


def test_unknown_shapes_are_not_classified():
    # No evidence either way: yt-dlp and the error text decide.
    assert classify_track(_track()) is None
    assert classify_track(None) is None
    assert classify_track([]) is None
    assert classify_track({"media": None}) is None
    assert classify_track({"media": {"transcodings": ["junk", None]}}) is None
    assert classify_track({"media": {"transcodings": [{"format": None}]}}) is None


# -- UnavailableTrack -------------------------------------------------------------------------
def test_unavailable_track_from_api_and_message():
    track = UnavailableTrack.from_api(_track(transcodings=[_CTR]), Reason.DRM)
    assert track.track_id == "675214841"
    assert track.title == "Some Song"
    assert track.artist == "Some Artist"          # publisher metadata beats the uploader
    assert track.url == "https://soundcloud.com/label/some-song"
    assert track.duration == pytest.approx(201.0)
    assert track.message() == "'Some Song' by Some Artist: DRM-protected on SoundCloud, can't be downloaded, skipped"
    assert track.message("nothing found elsewhere").endswith(", skipped (nothing found elsewhere)")


def test_unavailable_track_from_api_falls_back_to_uploader():
    data = _track(transcodings=[_CTR])
    del data["publisher_metadata"]
    assert UnavailableTrack.from_api(data, Reason.DRM).artist == "Some Label"


def test_unavailable_track_from_info():
    info = {"id": "1575070915", "title": "Song", "uploader": "Uploader",
            "webpage_url": "https://soundcloud.com/u/song", "duration": 180}
    track = UnavailableTrack.from_info(info, Reason.PREVIEW)
    assert (track.title, track.artist, track.url, track.duration) == ("Song", "Uploader", "https://soundcloud.com/u/song", 180.0)
    assert "30-second preview" in track.message()


# -- classify_error ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "lines, category, reason",
    [
        (["ERROR: [soundcloud] 675214841: This video is DRM protected"], FailureCategory.UNAVAILABLE, Reason.DRM),
        (["ERROR: [soundcloud] 1: Requested format is not available. Use --list-formats"],
         FailureCategory.UNAVAILABLE, Reason.PREVIEW),
        (["ERROR: [soundcloud] 1: This video is not available in your country"],
         FailureCategory.UNAVAILABLE, Reason.BLOCKED),
        (["ERROR: [soundcloud] 1: Unable to download JSON metadata: HTTP Error 404: Not Found"],
         FailureCategory.UNAVAILABLE, Reason.REMOVED),
        (["ERROR: unable to download video data: HTTP Error 429: Too Many Requests"],
         FailureCategory.RATE_LIMITED, None),
        (["ERROR: [soundcloud] 1: Unable to download webpage: The read operation timed out"],
         FailureCategory.NETWORK, None),
        (["ERROR: [soundcloud] 1: something nobody has seen before"], FailureCategory.FAILED, None),
        ([], FailureCategory.FAILED, None),
    ],
)
def test_classify_error(lines, category, reason):
    verdict = classify_error(lines)
    assert (verdict.category, verdict.reason) == (category, reason)


def test_rate_limit_wins_over_unavailable():
    # A 429 may have cut the request short; a wrong "unavailable" would be final.
    verdict = classify_error([
        "ERROR: unable to download video data: HTTP Error 429: Too Many Requests",
        "ERROR: [soundcloud] 1: This video is DRM protected",
    ])
    assert verdict.category is FailureCategory.RATE_LIMITED


# -- categories -------------------------------------------------------------------------------
def test_only_transient_categories_are_retryable():
    assert {category for category in FailureCategory if category.retryable} == {
        FailureCategory.RATE_LIMITED, FailureCategory.NETWORK,
    }
    assert not FailureCategory.UNAVAILABLE.retryable
    assert [category.value for category in FailureCategory] == ["failed", "unavailable", "rate_limited", "network"]
